"""Public CLI behavior for the canonical render timeline gate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Never

import pytest

from video_pipeline.cli import main
from video_pipeline.timeline import PauseInterval


class FakeAudioProbe:
    """Return deterministic facts for the copied narration boundary."""

    def __init__(self, facts: dict[str, object]) -> None:
        self.facts = facts
        self.calls: list[Path] = []

    def __call__(self, path: Path) -> dict[str, object]:
        self.calls.append(path)
        return dict(self.facts)


class EmptySilenceDetector:
    """Produce a candidate timeline without invoking a media tool."""

    def __init__(self) -> None:
        self.calls: list[Path] = []

    def __call__(self, path: Path) -> tuple[PauseInterval, ...]:
        self.calls.append(path)
        return ()


class ExplodingProvider:
    """Fail if canonical render reaches the provider boundary."""

    def generate(self, *_args: object, **_kwargs: object) -> Never:
        raise AssertionError("provider must not be called for a candidate timeline")

    def unload(self) -> Never:
        raise AssertionError("provider must not be called for a candidate timeline")


class ExplodingRunner:
    """Fail if canonical render reaches the Manim boundary."""

    def run(self, *_args: object, **_kwargs: object) -> Never:
        raise AssertionError("runner must not be called for a candidate timeline")


class ExplodingValidator:
    """Fail if canonical render reaches the validation boundary."""

    def validate(self, *_args: object, **_kwargs: object) -> Never:
        raise AssertionError("validator must not be called for a candidate timeline")


class ExplodingComposer:
    """Fail if canonical render reaches the composition boundary."""

    def compose(self, *_args: object, **_kwargs: object) -> Never:
        raise AssertionError("composer must not be called for a candidate timeline")


def _snapshot_files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_render_rejects_candidate_before_render_boundaries_or_mutation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = tmp_path / "roteiro.md"
    script.write_text(
        "# Origem\nVetor inicial.\n\n"
        "## Componentes\nObserve cada componente agora.\n\n"
        "## Resultado\nCompare os comprimentos finais agora juntos.\n",
        encoding="utf-8",
    )
    audio = tmp_path / "narracao.wav"
    audio_bytes = b"deterministic fake wav bytes\x00"
    audio.write_bytes(audio_bytes)
    probe = FakeAudioProbe(
        {
            "path": "audio/narration.wav",
            "hash": hashlib.sha256(audio_bytes).hexdigest(),
            "container": "wav",
            "codec": "pcm_s16le",
            "stream": 0,
            "sample_rate": 48_000,
            "channels": 2,
            "duration": 12.0,
            "size": len(audio_bytes),
            "probe_result": {"format": {}, "streams": []},
        }
    )
    detector = EmptySilenceDetector()
    project = tmp_path / "projects" / "2026_vetores"

    assert (
        main(
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
            silence_detector=detector,
        )
        == 0
    )
    project_json = project / "project.json"
    before_files = _snapshot_files(project)
    before_probe_calls = len(probe.calls)
    before_detector_calls = len(detector.calls)

    exit_code = main(
        ["render", str(project_json)],
        provider=ExplodingProvider(),
        runner=ExplodingRunner(),
        validator=ExplodingValidator(),
        composer=ExplodingComposer(),
    )

    output = capsys.readouterr().out.lower()
    assert exit_code == 1
    assert "timeline must be confirmed" in output
    assert _snapshot_files(project) == before_files
    assert len(probe.calls) == before_probe_calls
    assert len(detector.calls) == before_detector_calls
    assert json.loads(project_json.read_text(encoding="utf-8"))["status"] == (
        "timeline_candidate"
    )
