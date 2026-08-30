"""Public contract for canonical project initialization."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from video_pipeline import project as project_module
from video_pipeline.cli import main
from video_pipeline.project import probe_audio


class FakeAudioProbe:
    """Operation-specific fake for the injectable audio probe boundary."""

    def __init__(self, facts: dict[str, object]) -> None:
        self.facts = facts
        self.calls: list[Path] = []

    def __call__(self, path: Path) -> dict[str, object]:
        self.calls.append(path)
        return dict(self.facts)


class EmptySilenceDetector:
    """Keep this probe-only test away from real FFmpeg media execution."""

    def __call__(self, path: Path) -> tuple[object, ...]:
        del path
        return ()


class FailingAudioProbe:
    """Operation-specific fake for ffprobe failures at the public boundary."""

    def __init__(self, message: str) -> None:
        self.message = message
        self.calls: list[Path] = []

    def __call__(self, path: Path) -> dict[str, object]:
        self.calls.append(path)
        raise ValueError(self.message)


def test_init_copies_inputs_and_persists_audio_probe_facts(tmp_path: Path) -> None:
    script = tmp_path / "roteiro.md"
    script_bytes = "# Abertura\nExplique vetores.\n".encode("utf-8")
    script.write_bytes(script_bytes)

    audio = tmp_path / "narracao.wav"
    audio_bytes = b"deterministic fake wav bytes\x00"
    audio.write_bytes(audio_bytes)

    audio_hash = hashlib.sha256(audio_bytes).hexdigest()
    facts = {
        "path": "audio/narration.wav",
        "hash": audio_hash,
        "container": "wav",
        "codec": "pcm_s16le",
        "stream": 0,
        "sample_rate": 48_000,
        "channels": 2,
        "duration": 12.5,
        "size": len(audio_bytes),
        "probe_result": {
            "format": {"format_name": "wav", "duration": "12.5"},
            "streams": [
                {
                    "index": 0,
                    "codec_type": "audio",
                    "codec_name": "pcm_s16le",
                    "sample_rate": "48000",
                    "channels": 2,
                }
            ],
        },
    }
    probe = FakeAudioProbe(facts)
    project = tmp_path / "projects" / "2026_vetores"

    exit_code = main(
        [
            "init",
            str(project),
            "--title",
            "Vetores",
            "--script",
            str(script),
            "--audio",
            str(audio),
        ],
        audio_probe=probe,
        silence_detector=EmptySilenceDetector(),
    )

    assert exit_code == 0
    assert len(probe.calls) == 1
    assert probe.calls[0].name == "narration.wav"
    assert probe.calls[0].parent.name == "audio"
    assert probe.calls[0] != audio
    assert (project / "script.md").read_bytes() == script_bytes
    assert (project / "audio" / "narration.wav").read_bytes() == audio_bytes

    document = json.loads((project / "project.json").read_text(encoding="utf-8"))
    assert document["id"] == "2026_vetores"
    assert document["script_path"] == "script.md"
    assert document["audio_path"] == "audio/narration.wav"
    assert not Path(document["script_path"]).is_absolute()
    assert ".." not in Path(document["script_path"]).parts
    assert not Path(document["audio_path"]).is_absolute()
    assert ".." not in Path(document["audio_path"]).parts
    assert document["script_sha256"] == hashlib.sha256(script_bytes).hexdigest()
    assert document["audio"] == facts


@pytest.mark.parametrize(
    "case",
    [
        "missing_script",
        "invalid_script_utf8",
        "missing_audio",
        "invalid_audio_facts",
        "audio_no_stream",
        "audio_ffprobe_failure",
        "invalid_project_id",
        "path_traversal",
    ],
)
def test_init_rejects_invalid_inputs_without_publishing_project(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    case: str,
) -> None:
    projects = tmp_path / "projects"
    project = projects / "2026_invalid_input"
    script = tmp_path / "roteiro.md"
    audio = tmp_path / "narracao.wav"
    script.write_bytes(
        b"# Abertura\n"
        b"@start: 0\n"
        b"@end: 10\n"
        b"Uma cena deterministica.\n"
    )
    audio_bytes = b"deterministic fake wav bytes\x00"
    audio.write_bytes(audio_bytes)
    facts: dict[str, object] = {
        "path": "audio/narration.wav",
        "hash": hashlib.sha256(audio_bytes).hexdigest(),
        "container": "wav",
        "codec": "pcm_s16le",
        "stream": 0,
        "sample_rate": 48_000,
        "channels": 2,
        "duration": 10.0,
        "size": len(audio_bytes),
        "probe_result": {
            "format": {"format_name": "wav", "duration": "10.0"},
            "streams": [
                {
                    "index": 0,
                    "codec_type": "audio",
                    "codec_name": "pcm_s16le",
                    "sample_rate": "48000",
                    "channels": 2,
                }
            ],
        },
    }

    if case == "missing_script":
        script.unlink()
    elif case == "invalid_script_utf8":
        script.write_bytes(b"# Invalido\n\xff")
    elif case == "missing_audio":
        audio.unlink()
    elif case == "invalid_audio_facts":
        facts["duration"] = 0.0

    audio_probe: FakeAudioProbe | FailingAudioProbe = FakeAudioProbe(facts)
    if case == "audio_no_stream":
        audio_probe = FailingAudioProbe("audio has no usable audio stream")
    elif case == "audio_ffprobe_failure":
        audio_probe = FailingAudioProbe("ffprobe failed")

    if case == "invalid_project_id":
        project = projects / "not-a-project-id"
    elif case == "path_traversal":
        project = projects / ".." / "2026_escape"

    exit_code = main(
        [
            "init",
            str(project),
            "--title",
            "Invalido",
            "--script",
            str(script),
            "--audio",
            str(audio),
        ],
        audio_probe=audio_probe,
        silence_detector=EmptySilenceDetector(),
    )

    output = capsys.readouterr().out
    assert exit_code == 1, output
    assert "ERROR" in output
    assert not project.exists()
    if projects.exists():
        assert not any(
            child.name.startswith(f".{project.name}.")
            for child in projects.iterdir()
        )


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr", "expected_error"),
    [
        (1, "", "FFPROBE_STDERR", "ffprobe failed"),
        (
            0,
            json.dumps(
                {
                    "format": {"format_name": "wav", "duration": "10.0"},
                    "streams": [{"index": 0, "codec_type": "video"}],
                }
            ),
            "",
            "audio has no usable audio stream",
        ),
    ],
)
def test_probe_audio_rejects_fake_ffprobe_failures_without_real_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    stdout: str,
    stderr: str,
    expected_error: str,
) -> None:
    audio = tmp_path / "narration.wav"
    audio.write_bytes(b"fake audio bytes")
    calls: list[list[str]] = []

    def fake_run(
        args: list[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert capture_output is True
        assert text is True
        assert check is False
        calls.append(args)
        return subprocess.CompletedProcess(
            args,
            returncode,
            stdout=stdout,
            stderr=stderr,
        )

    monkeypatch.setattr(project_module.subprocess, "run", fake_run)

    with pytest.raises(ValueError, match=expected_error):
        probe_audio(audio)

    assert len(calls) == 1
    assert calls[0][0] == "ffprobe"
    assert calls[0][-1] == str(audio.resolve())
