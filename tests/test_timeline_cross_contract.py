"""Public CLI behavior for project/timeline cross-contract validation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from video_pipeline.cli import main
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


def test_timeline_commands_reject_audio_duration_mismatch_without_mutation(
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
    timeline_document = json.loads(timeline_json.read_text(encoding="utf-8"))
    timeline_document["duration_seconds"] = 13.0
    timeline_document["segments"][-1]["end_seconds"] = 13.0
    timeline_document["segments"][-1]["target_duration_seconds"] = 7.0
    timeline_json.write_text(
        json.dumps(timeline_document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    Timeline.model_validate_json(timeline_json.read_text(encoding="utf-8"))
    before_project = project_json.read_bytes()
    before_timeline = timeline_json.read_bytes()

    validate_code = main(["timeline", "validate", str(project_json)])
    validate_output = capsys.readouterr().out
    assert validate_code == 1
    assert "audio duration" in validate_output.lower()
    assert project_json.read_bytes() == before_project
    assert timeline_json.read_bytes() == before_timeline

    confirm_code = main(["timeline", "confirm", str(project_json)])
    confirm_output = capsys.readouterr().out
    assert confirm_code == 1
    assert "audio duration" in confirm_output.lower()
    assert project_json.read_bytes() == before_project
    assert timeline_json.read_bytes() == before_timeline
    assert len(probe.calls) == 1
    assert len(detector.calls) == 1


def _initialize_candidate_project(tmp_path: Path) -> tuple[Path, Path]:
    script = tmp_path / "roteiro.md"
    script.write_text(
        "# Origem\nVetor inicial.\n\n"
        "## Resultado\nCompare os comprimentos finais.\n",
        encoding="utf-8",
    )
    audio = tmp_path / "narracao.wav"
    audio_bytes = b"deterministic fake wav bytes\x00"
    audio.write_bytes(audio_bytes)
    project = tmp_path / "projects" / "2026_candidato"
    facts = {
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
    assert (
        main(
            [
                "init",
                str(project),
                "--title",
                "Candidato",
                "--script",
                str(script),
                "--audio",
                str(audio),
            ],
            audio_probe=FakeAudioProbe(facts),
            silence_detector=EmptySilenceDetector(),
        )
        == 0
    )
    return project, project / "project.json"


def _synchronize_changed_segment_package(project: Path, segment: dict[str, object]) -> None:
    plan_path = project / str(segment["plan_path"])
    package_root = plan_path.parent
    interval = {
        "start_seconds": segment["start_seconds"],
        "end_seconds": segment["end_seconds"],
        "duration_seconds": segment["target_duration_seconds"],
    }
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan.update(interval)
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    brief_path = package_root / "brief.json"
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    brief.update(interval)
    brief_path.write_text(json.dumps(brief), encoding="utf-8")


@pytest.mark.parametrize("case", ["gap", "overlap", "order"])
def test_candidate_timeline_rejects_structure_without_mutation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    case: str,
) -> None:
    project, project_json = _initialize_candidate_project(tmp_path)
    timeline_json = project / "timeline.json"
    timeline = json.loads(timeline_json.read_text(encoding="utf-8"))
    segments = timeline["segments"]
    if case == "gap":
        segments[1]["start_seconds"] = segments[0]["end_seconds"] + 0.5
        segments[1]["target_duration_seconds"] = (
            segments[1]["end_seconds"] - segments[1]["start_seconds"]
        )
        _synchronize_changed_segment_package(project, segments[1])
    elif case == "overlap":
        segments[1]["start_seconds"] = segments[0]["end_seconds"] - 0.5
        segments[1]["target_duration_seconds"] = (
            segments[1]["end_seconds"] - segments[1]["start_seconds"]
        )
        _synchronize_changed_segment_package(project, segments[1])
    else:
        segments[0]["order"], segments[1]["order"] = (
            segments[1]["order"],
            segments[0]["order"],
        )
    timeline_json.write_text(json.dumps(timeline), encoding="utf-8")

    watched_paths = [project_json, timeline_json]
    for segment in segments:
        package_root = (project / str(segment["plan_path"])).parent
        watched_paths.extend(package_root / filename for filename in ("plan.json", "brief.json"))
    snapshots = {path: path.read_bytes() for path in watched_paths}

    for command in ("validate", "confirm"):
        exit_code = main(["timeline", command, str(project_json)])
        output = capsys.readouterr().out
        assert exit_code == 1, f"{case}/{command}: {output}"
        for path, snapshot in snapshots.items():
            assert path.read_bytes() == snapshot, f"{case}/{command}: {path}"


def test_project_id_must_match_directory_for_timeline_validation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project, project_json = _initialize_candidate_project(tmp_path)
    document = json.loads(project_json.read_text(encoding="utf-8"))
    document["id"] = "2026_outro_projeto"
    project_json.write_text(json.dumps(document), encoding="utf-8")
    snapshot = project_json.read_bytes()

    for command in ("validate", "confirm"):
        exit_code = main(["timeline", command, str(project_json)])
        output = capsys.readouterr().out
        assert exit_code == 1, f"{command}: {output}"
        assert "project id" in output.lower()
        assert project_json.read_bytes() == snapshot
    assert project.name == "2026_candidato"
