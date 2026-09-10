"""Public CLI behavior for syncing manually edited timelines into plans."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from video_pipeline.cli import main
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
    """Create a proportional candidate without invoking real media tools."""

    def __init__(self) -> None:
        self.calls: list[Path] = []

    def __call__(self, path: Path) -> tuple[PauseInterval, ...]:
        self.calls.append(path)
        return ()


def _write_json(path: Path, document: dict[str, object]) -> None:
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_manual_timeline_confirmation_syncs_existing_scene_plans(tmp_path: Path) -> None:
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
    project_document = json.loads(project_json.read_text(encoding="utf-8"))
    plan_paths = [Path(scene["plan_path"]) for scene in project_document["scenes"]]
    before_plans: dict[Path, dict[str, object]] = {}
    for relative_path in plan_paths:
        plan_path = project / relative_path
        plan_document = json.loads(plan_path.read_text(encoding="utf-8"))
        if relative_path == plan_paths[0]:
            plan_document["capabilities"] = ["vector_geometry"]
            plan_document["objects"] = [
                {
                    "id": "vector",
                    "kind": "Arrow",
                    "color_role": "accent",
                    "semantic_role": "vector",
                    "region": "center",
                }
            ]
            plan_document["beats"] = [
                {
                    "id": "draw-vector",
                    "action": "create",
                    "objects": ["vector"],
                    "start_seconds": 0.0,
                    "end_seconds": 1.0,
                    "duration_seconds": 1.0,
                }
            ]
            plan_document["layout"] = {"object_regions": {"vector": "center"}}
            enriched_plan = ScenePlan.model_validate(plan_document)
            plan_document = enriched_plan.to_document()
            _write_json(plan_path, plan_document)
        before_plans[relative_path] = plan_document

    timeline_document = json.loads(timeline_json.read_text(encoding="utf-8"))
    timeline_document["method"] = "manual"
    timeline_document["segments"][0]["end_seconds"] = 3.0
    timeline_document["segments"][0]["target_duration_seconds"] = 3.0
    timeline_document["segments"][0]["end_provenance"] = "manual"
    timeline_document["segments"][1]["start_seconds"] = 3.0
    timeline_document["segments"][1]["target_duration_seconds"] = 3.0
    timeline_document["segments"][1]["start_provenance"] = "manual"
    timeline_document["segments"][1]["end_provenance"] = "manual"
    timeline_document["segments"][2]["start_provenance"] = "manual"
    timeline_document["segments"][0]["objective"] = "Revisar o vetor inicial."
    _write_json(timeline_json, timeline_document)
    Timeline.model_validate_json(timeline_json.read_text(encoding="utf-8"))

    assert main(["timeline", "confirm", str(project_json)]) == 0

    confirmed_project = json.loads(project_json.read_text(encoding="utf-8"))
    confirmed_timeline = Timeline.model_validate_json(
        timeline_json.read_text(encoding="utf-8")
    )
    assert confirmed_project["status"] == "timeline_confirmed"
    assert confirmed_project["planning_state"] == "ready"
    assert confirmed_timeline.status == "confirmed"
    assert confirmed_timeline.method == "manual"
    assert [
        (segment.start_seconds, segment.end_seconds)
        for segment in confirmed_timeline.segments
    ] == [
        (0.0, 3.0),
        (3.0, 6.0),
        (6.0, 12.0),
    ]
    assert confirmed_timeline.segments[0].objective == "Revisar o vetor inicial."
    assert [Path(scene["plan_path"]) for scene in confirmed_project["scenes"]] == plan_paths

    synced_fields = {
        "narration_text",
        "objective",
        "start_seconds",
        "end_seconds",
        "duration_seconds",
    }
    visual_fields = {
        "theme",
        "capabilities",
        "objects",
        "beats",
        "layout",
        "expectations",
        "continuity_in",
        "continuity_out",
    }
    for scene, segment in zip(
        confirmed_project["scenes"], confirmed_timeline.segments, strict=True
    ):
        relative_path = Path(scene["plan_path"])
        plan_document = json.loads((project / relative_path).read_text(encoding="utf-8"))
        plan = ScenePlan.model_validate(plan_document)
        assert plan.narration_text == segment.narration_text
        assert plan.objective == segment.objective
        assert plan.start_seconds == segment.start_seconds
        assert plan.end_seconds == segment.end_seconds
        assert plan.duration_seconds == segment.target_duration_seconds
        for field in visual_fields:
            assert plan_document[field] == before_plans[relative_path][field]
        assert set(before_plans[relative_path]) >= synced_fields | visual_fields
    assert len(list(project.glob("scenes/*/plan.json"))) == len(plan_paths)
