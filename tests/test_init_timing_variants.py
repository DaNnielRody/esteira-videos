"""Public contracts for Markdown timing variants during project initialization."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from video_pipeline.cli import main
from video_pipeline.scene_plan import ScenePlan
from video_pipeline.timeline import PauseInterval, SceneBrief, Timeline


class FakeAudioProbe:
    """Return deterministic facts for the staged narration boundary."""

    def __init__(self, facts: dict[str, object]) -> None:
        self.facts = facts

    def __call__(self, path: Path) -> dict[str, object]:
        del path
        return dict(self.facts)


class EmptySilenceDetector:
    """Force the candidate path without invoking real media tooling."""

    def __call__(self, path: Path) -> tuple[PauseInterval, ...]:
        del path
        return ()


class FakeMixedSilenceDetector:
    """Offer pauses for only the first weighted internal boundary."""

    def __init__(self) -> None:
        self.calls: list[Path] = []

    def __call__(self, path: Path) -> tuple[PauseInterval, ...]:
        self.calls.append(path)
        return (
            PauseInterval(start_seconds=1.6, end_seconds=2.2),
            PauseInterval(start_seconds=1.9, end_seconds=2.3),
            PauseInterval(start_seconds=7.5, end_seconds=7.7),
        )


class FakeSilenceProcess:
    """Replace the local FFmpeg process boundary without running media tools."""

    def __init__(self, *, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.calls: list[list[str]] = []

    def __call__(
        self,
        argv: list[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert capture_output is True
        assert text is True
        assert check is False
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(
            argv,
            self.returncode,
            stdout=self.stdout,
            stderr=self.stderr,
        )


def test_init_markdown_objectives_without_timestamps_preserves_scene_content(
    tmp_path: Path,
) -> None:
    script = tmp_path / "roteiro.md"
    script_bytes = (
        "# Abertura\n"
        "@objective: Apresente o vetor inicial.\n"
        "O vetor parte da origem.\n"
        "\n"
        "## Componentes\n"
        "@objective: Explique as componentes.\n"
        "Observe as componentes x e y.\n"
    ).encode("utf-8")
    script.write_bytes(script_bytes)
    audio = tmp_path / "narracao.wav"
    audio_bytes = b"fake wav bytes"
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
            "duration": 10.0,
            "size": len(audio_bytes),
            "probe_result": {"format": {}, "streams": []},
        }
    )
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
            silence_detector=EmptySilenceDetector(),
        )
        == 0
    )

    timeline = Timeline.model_validate_json(
        (project / "timeline.json").read_text(encoding="utf-8")
    )
    assert timeline.status == "candidate"
    assert [segment.objective for segment in timeline.segments] == [
        "Apresente o vetor inicial.",
        "Explique as componentes.",
    ]
    assert [segment.narration_text for segment in timeline.segments] == [
        "O vetor parte da origem.",
        "Observe as componentes x e y.",
    ]
    project_document = json.loads(
        (project / "project.json").read_text(encoding="utf-8")
    )
    assert project_document["status"] == "timeline_candidate"


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr", "expected_status"),
    (
        (
            0,
            "",
            "silence_start: 1.5\n"
            "silence_end: 2.1 | silence_duration: 0.6\n"
            "silence_start: 3.8\n"
            "silence_end: 4.2 | silence_duration: 0.4\n",
            "pause_aligned",
        ),
        (0, "", "ffmpeg info line", "proportional_fallback"),
        (1, "", "ffmpeg exploded", "error"),
        (0, "silence_start: not-a-number", "", "error"),
    ),
)
def test_init_uses_local_ffmpeg_silence_detector_process_boundary(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    returncode: int,
    stdout: str,
    stderr: str,
    expected_status: str,
) -> None:
    script = tmp_path / "roteiro.md"
    script.write_text(
        "# Abertura\n"
        "Uma introducao curta.\n"
        "\n"
        "## Componentes\n"
        "Duas componentes vetoriais.\n"
        "\n"
        "## Resultado\n"
        "Um resultado final.\n",
        encoding="utf-8",
    )
    audio = tmp_path / "narracao.wav"
    audio_bytes = b"fake wav bytes"
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
            "duration": 6.0,
            "size": len(audio_bytes),
            "probe_result": {"format": {}, "streams": []},
        }
    )
    process = FakeSilenceProcess(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )
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
        silence_subprocess_run=process,
    )

    assert process.calls
    assert process.calls[0][0] == "ffmpeg"
    assert "-loglevel" in process.calls[0]
    assert process.calls[0][process.calls[0].index("-loglevel") + 1] == "info"
    if expected_status == "pause_aligned":
        assert exit_code == 0
        timeline = Timeline.model_validate_json(
            (project / "timeline.json").read_text(encoding="utf-8")
        )
        assert timeline.method == "pause_aligned"
        assert [
            (segment.start_seconds, segment.end_seconds)
            for segment in timeline.segments
        ] == [(0.0, 1.8), (1.8, 4.0), (4.0, 6.0)]
    elif expected_status == "proportional_fallback":
        assert exit_code == 0
        timeline = Timeline.model_validate_json(
            (project / "timeline.json").read_text(encoding="utf-8")
        )
        assert timeline.method == "proportional_fallback"
    else:
        assert exit_code == 1
        assert "ERROR:" in capsys.readouterr().out
        assert not project.exists()


def test_init_mixes_pause_and_proportional_boundaries_per_internal_target(
    tmp_path: Path,
) -> None:
    script = tmp_path / "roteiro.md"
    script.write_text(
        "# Abertura\n"
        "um dois\n"
        "\n"
        "## Componentes\n"
        "tres quatro cinco\n"
        "\n"
        "## Resultado\n"
        "seis sete oito nove\n",
        encoding="utf-8",
    )
    audio = tmp_path / "narracao.wav"
    audio_bytes = b"fake wav bytes"
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
            "duration": 9.0,
            "size": len(audio_bytes),
            "probe_result": {"format": {}, "streams": []},
        }
    )
    detector = FakeMixedSilenceDetector()
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

    timeline = Timeline.model_validate_json(
        (project / "timeline.json").read_text(encoding="utf-8")
    )
    assert timeline.method == "pause_aligned"
    assert [(segment.start_seconds, segment.end_seconds) for segment in timeline.segments] == [
        (0.0, 1.9),
        (1.9, 5.0),
        (5.0, 9.0),
    ]
    assert [
        (segment.start_provenance, segment.end_provenance)
        for segment in timeline.segments
    ] == [
        ("pause_aligned", "pause_aligned"),
        ("pause_aligned", "proportional_fallback"),
        ("proportional_fallback", "pause_aligned"),
    ]
    warnings = " ".join(timeline.warnings + timeline.manual_review_reasons).lower()
    assert "mixed" in warnings
    assert "pause-aligned" in warnings
    assert "proportional" in warnings
    assert "asr" in warnings
    assert "forced alignment" in warnings
    assert [segment.order for segment in timeline.segments] == [1, 2, 3]
    assert all(
        current.start_seconds == previous.end_seconds
        for previous, current in zip(timeline.segments, timeline.segments[1:], strict=False)
    )


def test_init_materializes_scene_packages_and_confirmation_syncs_them(
    tmp_path: Path,
) -> None:
    script = tmp_path / "roteiro.md"
    script.write_text(
        "# Abertura\n"
        "@objective: Apresente a origem.\n"
        "O vetor parte da origem.\n"
        "\n"
        "## Resultado\n"
        "@objective: Mostre o resultado.\n"
        "Compare os comprimentos finais.\n",
        encoding="utf-8",
    )
    audio = tmp_path / "narracao.wav"
    audio_bytes = b"package fake wav bytes"
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
            "duration": 10.0,
            "size": len(audio_bytes),
            "probe_result": {"format": {}, "streams": []},
        }
    )
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
            silence_detector=EmptySilenceDetector(),
        )
        == 0
    )

    project_json = project / "project.json"
    timeline_json = project / "timeline.json"
    project_document = json.loads(project_json.read_text(encoding="utf-8"))
    timeline = Timeline.model_validate_json(timeline_json.read_text(encoding="utf-8"))
    assert timeline.status == "candidate"
    assert project_document["status"] == "timeline_candidate"

    for scene_ref, segment in zip(
        project_document["scenes"], timeline.segments, strict=True
    ):
        for key in ("path", "plan_path", "brief_path", "expectations_path"):
            relative_path = Path(scene_ref[key])
            assert not relative_path.is_absolute()
            assert ".." not in relative_path.parts
        assert scene_ref["path"] == segment.plan_path.removesuffix("/plan.json")
        assert scene_ref["plan_path"] == segment.plan_path

        plan_path = project / scene_ref["plan_path"]
        brief_path = project / scene_ref["brief_path"]
        expectations_path = project / scene_ref["expectations_path"]
        plan = ScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
        brief = SceneBrief.model_validate_json(brief_path.read_text(encoding="utf-8"))
        expectations = json.loads(expectations_path.read_text(encoding="utf-8"))
        assert plan.narration_text == segment.narration_text
        assert plan.objective == segment.objective
        assert plan.start_seconds == segment.start_seconds
        assert plan.end_seconds == segment.end_seconds
        assert plan.duration_seconds == segment.target_duration_seconds
        assert brief.id == segment.id
        assert brief.order == segment.order
        assert brief.plan_path == segment.plan_path
        assert brief.narration_text == segment.narration_text
        assert brief.objective == segment.objective
        assert brief.start_seconds == segment.start_seconds
        assert brief.end_seconds == segment.end_seconds
        assert brief.duration_seconds == segment.target_duration_seconds
        assert brief.start_provenance == segment.start_provenance
        assert brief.end_provenance == segment.end_provenance
        assert expectations == {}

    timeline_document = json.loads(timeline_json.read_text(encoding="utf-8"))
    timeline_document["method"] = "manual"
    timeline_document["segments"][0].update(
        {
            "end_seconds": 4.0,
            "target_duration_seconds": 4.0,
            "end_provenance": "manual",
            "objective": "Revisar a origem.",
            "expectations": {"max_shapes": 3},
        }
    )
    timeline_document["segments"][1].update(
        {
            "start_seconds": 4.0,
            "target_duration_seconds": 6.0,
            "start_provenance": "manual",
        }
    )
    timeline_json.write_text(
        json.dumps(timeline_document, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    assert main(["timeline", "confirm", str(project_json)]) == 0

    confirmed_project = json.loads(project_json.read_text(encoding="utf-8"))
    confirmed_timeline = Timeline.model_validate_json(
        timeline_json.read_text(encoding="utf-8")
    )
    assert confirmed_project["status"] == "timeline_confirmed"
    assert confirmed_timeline.status == "confirmed"
    assert confirmed_timeline.method == "manual"
    assert [
        (segment.start_seconds, segment.end_seconds)
        for segment in confirmed_timeline.segments
    ] == [(0.0, 4.0), (4.0, 10.0)]
    for scene_ref, segment in zip(
        confirmed_project["scenes"], confirmed_timeline.segments, strict=True
    ):
        plan = ScenePlan.model_validate_json(
            (project / scene_ref["plan_path"]).read_text(encoding="utf-8")
        )
        brief = SceneBrief.model_validate_json(
            (project / scene_ref["brief_path"]).read_text(encoding="utf-8")
        )
        expectations = json.loads(
            (project / scene_ref["expectations_path"]).read_text(encoding="utf-8")
        )
        assert plan.narration_text == segment.narration_text
        assert plan.objective == segment.objective
        assert plan.start_seconds == segment.start_seconds
        assert plan.end_seconds == segment.end_seconds
        assert plan.duration_seconds == segment.target_duration_seconds
        assert brief.id == segment.id
        assert brief.order == segment.order
        assert brief.plan_path == segment.plan_path
        assert brief.narration_text == segment.narration_text
        assert brief.objective == segment.objective
        assert brief.start_seconds == segment.start_seconds
        assert brief.end_seconds == segment.end_seconds
        assert brief.duration_seconds == segment.target_duration_seconds
        assert brief.start_provenance == segment.start_provenance
        assert brief.end_provenance == segment.end_provenance
        if segment.order == 1:
            assert plan.expectations is not None
            assert plan.expectations.max_shapes == 3
            assert expectations == json.loads(plan.expectations.model_dump_json())
        else:
            assert plan.expectations is None
            assert expectations == {}
