"""Public contract for block-separated text timeline initialization."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from video_pipeline.cli import main
from video_pipeline.project import Project
from video_pipeline.scene_plan import ScenePlan
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
    """The text-block path falls back when no pauses are available."""

    def __init__(self) -> None:
        self.calls: list[Path] = []

    def __call__(self, path: Path) -> tuple[PauseInterval, ...]:
        self.calls.append(path)
        return ()


def test_init_text_blocks_creates_proportional_candidate(tmp_path: Path) -> None:
    script = tmp_path / "roteiro.txt"
    script_bytes = (
        "Vetor inicial.\n"
        "\n\n"
        "Observe cada componente agora.\n"
        "\n\n\n"
        "Compare os comprimentos finais agora juntos.\n"
    ).encode("utf-8")
    script.write_bytes(script_bytes)

    audio = tmp_path / "narracao.wav"
    audio_bytes = b"deterministic fake wav bytes\x00"
    audio.write_bytes(audio_bytes)
    facts = {
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
    probe = FakeAudioProbe(facts)
    silence_detector = EmptySilenceDetector()
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
        silence_detector=silence_detector,
    )

    assert exit_code == 0
    assert (project / "script.md").read_bytes() == script_bytes
    assert not (project / "roteiro.txt").exists()
    assert len(probe.calls) == 1
    assert probe.calls[0].name == "narration.wav"
    assert probe.calls[0].parent.name == "audio"
    assert len(silence_detector.calls) == 1
    assert silence_detector.calls[0].name == "narration.wav"
    assert silence_detector.calls[0].parent.name == "audio"

    project_document = json.loads((project / "project.json").read_text(encoding="utf-8"))
    assert project_document["script_path"] == "script.md"
    assert project_document["status"] == "timeline_candidate"
    assert project_document["planning_state"] == "review_required"
    assert project_document["timeline_path"] == "timeline.json"

    loaded_project = Project.model_validate_json(
        (project / "project.json").read_text(encoding="utf-8")
    )
    timeline = Timeline.model_validate_json(
        (project / "timeline.json").read_text(encoding="utf-8")
    )
    assert loaded_project.status == "timeline_candidate"
    assert timeline.status == "candidate"
    assert timeline.method == "proportional_fallback"
    assert [segment.id for segment in timeline.segments] == [
        "scene-01",
        "scene-02",
        "scene-03",
    ]
    assert [segment.narration_text for segment in timeline.segments] == [
        "Vetor inicial.",
        "Observe cada componente agora.",
        "Compare os comprimentos finais agora juntos.",
    ]
    assert [(segment.start_seconds, segment.end_seconds) for segment in timeline.segments] == [
        (0.0, 2.0),
        (2.0, 6.0),
        (6.0, 12.0),
    ]
    assert [segment.target_duration_seconds for segment in timeline.segments] == [2.0, 4.0, 6.0]
    assert all(
        segment.start_provenance == "proportional_fallback"
        and segment.end_provenance == "proportional_fallback"
        for segment in timeline.segments
    )

    for scene, segment in zip(project_document["scenes"], timeline.segments, strict=True):
        plan_path = Path(scene["plan_path"])
        assert not plan_path.is_absolute()
        assert ".." not in plan_path.parts
        plan = ScenePlan.model_validate_json((project / plan_path).read_text(encoding="utf-8"))
        assert plan.id == segment.id
        assert plan.scene_name == f"Scene{segment.order:02d}Scene"
        assert plan.objective == segment.narration_text.splitlines()[0]
        assert plan.narration_text == segment.narration_text
        assert plan.start_seconds == segment.start_seconds
        assert plan.end_seconds == segment.end_seconds
        assert plan.duration_seconds == segment.target_duration_seconds
        assert plan.theme == loaded_project.theme
