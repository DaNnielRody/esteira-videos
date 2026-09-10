"""Public CLI behavior for validating and confirming an editorial timeline."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from video_pipeline.cli import main
from video_pipeline.project import Project, ProjectState
from video_pipeline.timeline import PauseInterval, Timeline


class FakeAudioProbe:
    """Operation-specific fake for the staged narration probe boundary."""

    def __init__(self, facts: dict[str, object]) -> None:
        self.facts = facts
        self.calls: list[Path] = []

    def __call__(self, path: Path) -> dict[str, object]:
        self.calls.append(path)
        return dict(self.facts)


class EmptySilenceDetector:
    """Create a proportional candidate without invoking real media tools."""

    def __init__(self) -> None:
        self.calls: list[Path] = []

    def __call__(self, path: Path) -> tuple[PauseInterval, ...]:
        self.calls.append(path)
        return ()


def test_timeline_validate_then_confirm_promotes_candidate(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = tmp_path / "roteiro.md"
    script_bytes = (
        "# Origem\n"
        "Vetor inicial.\n"
        "\n"
        "## Componentes\n"
        "Observe cada componente agora.\n"
        "\n"
        "## Resultado\n"
        "Compare os comprimentos finais agora juntos.\n"
    ).encode("utf-8")
    script.write_bytes(script_bytes)

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
    timeline_json = project / "timeline.json"
    before_project = json.loads(project_json.read_text(encoding="utf-8"))
    before_timeline = json.loads(timeline_json.read_text(encoding="utf-8"))

    validate_code = main(["timeline", "validate", str(project_json)])
    validate_output = capsys.readouterr().out
    assert validate_code == 0
    assert "candidate" in validate_output.lower()
    assert "manual" in validate_output.lower()
    assert json.loads(project_json.read_text(encoding="utf-8")) == before_project
    assert json.loads(timeline_json.read_text(encoding="utf-8")) == before_timeline

    confirm_code = main(["timeline", "confirm", str(project_json)])
    confirm_output = capsys.readouterr().out
    assert confirm_code == 0
    assert "confirmed" in confirm_output.lower()
    assert len(probe.calls) == 1
    assert len(detector.calls) == 1

    confirmed_project_document = json.loads(project_json.read_text(encoding="utf-8"))
    confirmed_timeline_document = json.loads(timeline_json.read_text(encoding="utf-8"))
    loaded_project = Project.model_validate_json(project_json.read_text(encoding="utf-8"))
    loaded_timeline = Timeline.model_validate_json(timeline_json.read_text(encoding="utf-8"))

    assert loaded_project.status == ProjectState.timeline_confirmed
    assert confirmed_project_document["status"] == "timeline_confirmed"
    assert confirmed_project_document["planning_state"] == "ready"
    assert loaded_timeline.status == "confirmed"
    assert confirmed_timeline_document["status"] == "confirmed"
    for field in (
        "script_path",
        "script_sha256",
        "audio_path",
        "audio",
        "timeline_path",
        "theme",
        "scenes",
    ):
        assert confirmed_project_document[field] == before_project[field]
    assert confirmed_timeline_document["method"] == before_timeline["method"]
    assert confirmed_timeline_document["segments"] == before_timeline["segments"]
    assert confirmed_timeline_document["warnings"] == before_timeline["warnings"]
    assert confirmed_timeline_document["manual_review_reasons"] == before_timeline[
        "manual_review_reasons"
    ]
