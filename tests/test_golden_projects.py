"""Golden project discovery and provider-free regression checks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_pipeline.continuity import ContinuityState, compare_continuity
from video_pipeline.golden import (
    discover_golden_projects,
    hash_references,
    read_golden_project,
    validate_all_golden_projects,
    validate_golden_project,
)
from video_pipeline.runtime import (
    BoundingBox,
    ObservedObject,
    ObservedScene,
    SceneCheckpoint,
)
from video_pipeline.scene_plan import Beat, ScenePlan, VisualObject
from video_pipeline.theme import VideoTheme


def _project(root: Path, *, project_id: str, status: str = "accepted") -> Path:
    project = root / project_id
    for scene_id in ("geometry", "equation"):
        (project / "scenes" / scene_id).mkdir(parents=True)
    (project / "golden" / "frames").mkdir(parents=True)
    (project / "golden" / "evidence").mkdir(parents=True)
    (project / "project.json").write_text(
        json.dumps(
            {
                "id": project_id,
                "status": status,
                "title": "Visual fixture",
                "capabilities": ["basic_geometry", "equations", "typography"],
            }
        ),
        encoding="utf-8",
    )
    theme = VideoTheme.production()
    (project / "theme.json").write_text(json.dumps(theme.model_dump(mode="json")), encoding="utf-8")
    (project / "golden" / "evidence" / "README.md").write_text(
        "Deterministic accepted-scene evidence.\n", encoding="utf-8"
    )

    plans = {
        "geometry": ScenePlan(
            id="geometry",
            scene_name="GeometryScene",
            objective="Show a primary geometric transformation.",
            duration_seconds=2.0,
            capabilities=["basic_geometry"],
            objects=[
                VisualObject(id="shape", kind="circle", color_role="primary", region="center")
            ],
            beats=[Beat(id="show", action="introduce", objects=["shape"], duration_seconds=1.0)],
        ),
        "equation": ScenePlan(
            id="equation",
            scene_name="EquationScene",
            objective="Show a readable equation after the shape.",
            duration_seconds=2.0,
            capabilities=["equations", "typography"],
            objects=[
                VisualObject(
                    id="formula",
                    kind="mathtex",
                    formula=r"x^2",
                    color_role="accent",
                    region="center",
                )
            ],
            beats=[Beat(id="show", action="introduce", objects=["formula"], duration_seconds=1.0)],
        ),
    }
    scenes: list[dict[str, object]] = []
    for scene_id, plan in plans.items():
        scene_dir = project / "scenes" / scene_id
        plan_path = scene_dir / "plan.json"
        plan_path.write_text(json.dumps(plan.to_document()), encoding="utf-8")
        mobject_source = "Circle()" if scene_id == "geometry" else 'MathTex(r"x^2")'
        scene_source = (
            "from manim import Circle, Create, MathTex\n"
            "from video_pipeline.runtime import VisualScene\n\n"
            f"class {plan.scene_name}(VisualScene):\n"
            "    def construct(self):\n"
            "        mobject = self.register_visual(\n"
            f"            {mobject_source},\n"
            f'            "{plan.objects[0].id}",\n'
            f'            kind="{plan.objects[0].kind}",\n'
            f'            color_role="{plan.objects[0].color_role}",\n'
            "        )\n"
            "        self.add(mobject)\n"
            "        self.play(Create(mobject), run_time=1.0)\n"
            "        self.checkpoint('golden', beat_id='show')\n"
        )
        (scene_dir / "scene.py").write_text(scene_source, encoding="utf-8")
        (scene_dir / "expectations.json").write_text(
            json.dumps({"max_shapes": 1, "beats": [], "latex": [], "text": []}),
            encoding="utf-8",
        )
        frame = project / "golden" / "frames" / f"{scene_id}-keyframe.svg"
        frame.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="854" height="480">'
            f"<title>{scene_id}</title></svg>\n",
            encoding="utf-8",
        )
        scenes.append(
            {
                "id": scene_id,
                "plan": f"scenes/{scene_id}/plan.json",
                "code": f"scenes/{scene_id}/scene.py",
                "expectations": f"scenes/{scene_id}/expectations.json",
                "capabilities": list(plan.capabilities),
                "expected_facts": {
                    "initial_state": [],
                    "final_state": [plan.objects[0].id],
                    "checkpoints": ["golden"],
                    "animations": ["Create"],
                },
                "semantic_expectations": {
                    "required_objects": [plan.objects[0].id],
                    "required_regions": {plan.objects[0].id: "center"},
                    "required_color_roles": {plan.objects[0].id: plan.objects[0].color_role},
                },
                "expected_findings": [],
                "tolerances": {"bbox": 0.03, "duration_seconds": 0.1},
                "keyframes": [f"golden/frames/{scene_id}-keyframe.svg"],
                "dimensions": {"width": 854, "height": 480},
                "duration_seconds": 2.0,
            }
        )

    code_references = [scene["code"] for scene in scenes]
    plan_references = [scene["plan"] for scene in scenes]
    manifest = {
        "schema_version": "golden.manifest/1",
        "version": 1,
        "profile": "visual",
        "status": "accepted",
        "project_id": project_id,
        "title": "Visual fixture",
        "capabilities": ["basic_geometry", "equations", "typography"],
        "theme": "theme.json",
        "tolerances": {"bbox": 0.03, "duration_seconds": 0.1},
        "code_hash": hash_references(project, code_references),
        "plan_hash": hash_references(project, plan_references),
        "scenes": scenes,
        "continuity": {
            "boundaries": [
                {
                    "from": "geometry",
                    "to": "equation",
                    "recurring_objects": [],
                    "expected_transition": "none",
                    "expected_findings": [],
                }
            ]
        },
    }
    (project / "golden" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return project


def test_discovery_finds_multiple_accepted_projects_and_ignores_nonaccepted_without_provider(
    tmp_path: Path,
) -> None:
    accepted = _project(tmp_path, project_id="2026_visual_foundation")
    second = _project(tmp_path, project_id="2026_visual_followup")
    ignored = _project(tmp_path, project_id="2026_draft", status="draft")

    found = discover_golden_projects(tmp_path)

    assert found == sorted((accepted, second), key=str)
    results = validate_all_golden_projects(tmp_path)
    assert [item.path for item in results] == sorted((accepted, second), key=str)
    assert all(item.valid and item.inference_calls == 0 for item in results)
    assert ignored not in found


def test_golden_hash_mismatch_and_missing_evidence_are_rejected(tmp_path: Path) -> None:
    project = _project(tmp_path, project_id="2026_visual_foundation")
    scene_code = project / "scenes" / "geometry" / "scene.py"
    scene_code.write_text(
        scene_code.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8"
    )

    result = validate_golden_project(project)

    assert result.valid is False
    assert any("code_hash does not match" in reason for reason in result.reasons)


def test_golden_rejects_expected_facts_that_disagree_with_plan(tmp_path: Path) -> None:
    project = _project(tmp_path, project_id="2026_visual_foundation")
    manifest_path = project / "golden" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["scenes"][0]["expected_facts"]["final_state"] = ["not_declared"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = validate_golden_project(project)

    assert result.valid is False
    assert any("unknown IDs" in reason for reason in result.reasons)


@pytest.mark.parametrize("profile_mode", ["missing", "unknown"])
def test_golden_manifest_requires_an_explicit_known_profile(
    tmp_path: Path,
    profile_mode: str,
) -> None:
    project = _project(tmp_path, project_id="2026_visual_foundation")
    manifest_path = project / "golden" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if profile_mode == "missing":
        manifest.pop("profile")
    else:
        manifest["profile"] = "hybrid"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = validate_golden_project(project)

    assert result.valid is False
    assert any(
        "profile must be visual or audiovisual" in reason for reason in result.reasons
    )
    with pytest.raises(ValueError, match="profile must be visual or audiovisual"):
        read_golden_project(project)


def test_golden_manifest_title_must_match_project(tmp_path: Path) -> None:
    project = _project(tmp_path, project_id="2026_visual_foundation")
    manifest_path = project / "golden" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["title"] = "Different title"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = validate_golden_project(project)

    assert result.valid is False
    assert any("title disagrees with project" in reason for reason in result.reasons)
    with pytest.raises(ValueError, match="title must match the project"):
        read_golden_project(project)


def test_repository_golden_project_is_truthful_and_boundary_is_continuous() -> None:
    project = Path(__file__).parents[1] / "projects" / "2026_visual_foundation"
    result = validate_golden_project(project)

    assert result.valid is True, result.reasons
    manifest = json.loads((project / "golden" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "golden.manifest/1"
    assert manifest["profile"] == "visual"
    assert manifest["status"] == "accepted"
    assert "audio" not in manifest
    assert "timeline" not in manifest
    geometry_plan = ScenePlan.model_validate_json(
        (project / "scenes/01_geometry/plan.json")
        .read_text(encoding="utf-8")
        .replace('"schema_version": "visual.scene-plan/1",', "", 1)
    )
    equation_document = json.loads(
        (project / "scenes/02_equation/plan.json").read_text(encoding="utf-8")
    )
    equation_document.pop("schema_version")
    equation_plan = ScenePlan.model_validate(equation_document)
    vector = ObservedObject(
        id="vector",
        kind="arrow",
        bbox=BoundingBox(left=0.1, top=0.4, right=0.3, bottom=0.6),
        center_x=0.2,
        center_y=0.5,
        width=0.2,
        height=0.2,
        color_role="primary",
    )
    first = ObservedScene(
        scene_id="geometry",
        scene_name=geometry_plan.scene_name,
        final_state=[vector],
    )
    second = ObservedScene(
        scene_id="equation",
        scene_name=equation_plan.scene_name,
        initial_state=[],
        checkpoints=[
            SceneCheckpoint(id="initial", instant_seconds=0.0, objects=[]),
            SceneCheckpoint(id="persist", instant_seconds=1.2, objects=[vector]),
        ],
    )

    previous = ContinuityState.from_scene(geometry_plan, first, state="final")
    current = ContinuityState.from_scene(equation_plan, second, state="initial")
    report = compare_continuity(previous, current)

    assert report.findings == []
