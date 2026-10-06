"""Deterministic contracts for the visual operating system."""

from __future__ import annotations

import pytest

from video_pipeline.prompts import build_prompt
from video_pipeline.provider import ProviderRequest
from video_pipeline.quality import QualityFinding, QualityReport
from video_pipeline.runtime import (
    BoundingBox,
    ObservedObject,
    ObservedScene,
    SceneCheckpoint,
)
from video_pipeline.scene_plan import Beat, ScenePlan, VisualObject
from video_pipeline.theme import VideoTheme


def test_production_theme_exposes_stable_visual_contract() -> None:
    theme = VideoTheme.production()

    assert theme.palette["primary"] == "#4CC9F0"
    assert theme.palette["background"] == "#0B1020"
    assert theme.font_sizes["title"] > theme.font_sizes["body"]
    assert theme.resolution == (854, 480)
    assert theme.fps == 15
    assert theme.safe_area.left < theme.safe_area.right


def test_theme_rejects_invalid_color_and_impossible_safe_area() -> None:
    with pytest.raises(ValueError, match="hex"):
        VideoTheme(palette={"primary": "not-a-color"})

    with pytest.raises(ValueError, match="safe area"):
        VideoTheme(safe_area={"left": 0.8, "right": 0.2, "top": 0.1, "bottom": 0.9})


def test_scene_plan_has_ordered_beats_and_semantic_object_roles() -> None:
    plan = ScenePlan(
        id="vector-components",
        scene_name="VectorComponentsScene",
        objective="Explain how a vector decomposes into components.",
        duration_seconds=8.0,
        capabilities=["equations", "geometry"],
        objects=[
            VisualObject(
                id="vector",
                kind="arrow",
                color_role="primary",
                region="center",
            ),
            VisualObject(
                id="magnitude",
                kind="mathtex",
                formula=r"\|v\|",
                color_role="accent",
                region="bottom",
            ),
        ],
        beats=[
            Beat(id="introduce", action="introduce", objects=["vector"], duration_seconds=1.5),
            Beat(id="emphasize", action="emphasize", objects=["magnitude"], duration_seconds=3.0),
        ],
    )

    assert plan.total_beat_duration == pytest.approx(4.5)
    assert plan.objects[0].color_role == "primary"
    assert plan.beats[1].objects == ["magnitude"]


def test_scene_plan_rejects_unknown_beat_objects_and_overlong_beats() -> None:
    with pytest.raises(ValueError, match="unknown object"):
        ScenePlan(
            id="bad-plan",
            scene_name="BadPlanScene",
            objective="Show one object.",
            duration_seconds=2.0,
            objects=[VisualObject(id="shape", kind="circle")],
            beats=[Beat(action="introduce", objects=["missing"], duration_seconds=1.0)],
        )

    with pytest.raises(ValueError, match="duration"):
        ScenePlan(
            id="bad-duration",
            scene_name="BadDurationScene",
            objective="Show one object.",
            duration_seconds=1.0,
            objects=[VisualObject(id="shape", kind="circle")],
            beats=[Beat(action="introduce", objects=["shape"], duration_seconds=1.1)],
        )


def test_quality_report_serializes_actionable_structured_findings() -> None:
    finding = QualityFinding(
        code="TEXT_OUTSIDE_SAFE_AREA",
        severity="failure",
        scene_id="vector-components",
        beat_id="definition",
        object_ids=["definition"],
        observed={"right_edge": 7.41},
        expected={"max_right_edge": 6.61},
        explanation="The definition extends beyond the right safe-area boundary.",
        suggestion="Reduce the text width or split it into two lines.",
    )
    report = QualityReport(
        scene_id="vector-components",
        attempt=1,
        findings=[finding],
    )

    document = report.to_document()

    assert report.has_failures is True
    assert document["schema_version"] == "visual.quality-report/1"
    assert document["findings"][0]["code"] == "TEXT_OUTSIDE_SAFE_AREA"
    assert document["findings"][0]["suggestion"].startswith("Reduce")


def test_observed_scene_is_versioned_and_carries_logical_facts() -> None:
    observed = ObservedScene(
        scene_id="vector-components",
        scene_name="VectorComponentsScene",
        initial_state=[
            ObservedObject(
                id="vector",
                kind="arrow",
                bbox=BoundingBox(left=0.4, top=0.4, right=0.6, bottom=0.6),
                center_x=0.5,
                center_y=0.5,
                width=0.2,
                height=0.2,
            )
        ],
        final_state=[],
        checkpoints=[SceneCheckpoint(id="intro", instant_seconds=0.8, objects=[])],
    )

    document = observed.to_document()

    assert document["schema_version"] == "visual.observed-scene/1"
    assert document["initial_state"][0]["id"] == "vector"
    assert document["checkpoints"][0]["instant_seconds"] == pytest.approx(0.8)


def test_visual_plan_and_findings_are_sent_to_the_coder_prompt() -> None:
    plan = ScenePlan(
        id="prompt-scene",
        scene_name="PromptScene",
        objective="Explain one visual concept.",
        duration_seconds=3.0,
        capabilities=["typography"],
        objects=[VisualObject(id="title", kind="text", text="Concept", color_role="text")],
    )
    request = ProviderRequest(
        scene_name=plan.scene_name,
        description=plan.objective,
        theme=plan.theme.model_dump(mode="json"),
        scene_plan=plan.to_document(),
        capabilities=tuple(plan.capabilities),
        previous_code="BROKEN_CODE",
        diagnostics={
            "exit_code": 0,
            "stderr": "",
            "validator_reasons": [],
            "quality_findings": [
                {
                    "code": "TEXT_TOO_SMALL",
                    "severity": "failure",
                    "observed": {"pixel_height": 8},
                    "expected": {"minimum_pixel_height": 18},
                    "suggestion": "Increase the font size.",
                }
            ],
        },
    )

    prompt = build_prompt(request)

    assert "Visual identity contract" in prompt
    assert "ScenePlan" in prompt
    assert "Capabilities authorized for this scene only: typography" in prompt
    assert "TEXT_TOO_SMALL" in prompt
    assert "Increase the font size" in prompt.splitlines()[-1]
