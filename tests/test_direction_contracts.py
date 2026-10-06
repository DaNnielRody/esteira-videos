"""Regression promises derived from the approved LLM video, through the quality gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from video_pipeline.critics import evaluate_visual_quality
from video_pipeline.runtime import BoundingBox, ObservedObject, ObservedScene, SceneCheckpoint
from video_pipeline.scene_plan import ScenePlan


def obj(name: str, bounds: tuple[float, float, float, float]) -> ObservedObject:
    left, top, right, bottom = bounds
    return ObservedObject(
        id=name,
        kind="diagram",
        bbox=BoundingBox(left=left, top=top, right=right, bottom=bottom),
        center_x=(left + right) / 2,
        center_y=(top + bottom) / 2,
        width=right - left,
        height=bottom - top,
    )


def checkpoint(time: float, *objects: ObservedObject) -> SceneCheckpoint:
    return SceneCheckpoint(id=f"t-{time}", instant_seconds=time, objects=list(objects))


def plan(direction: dict[str, object]) -> ScenePlan:
    return ScenePlan.model_validate(
        {
            "id": "regression",
            "scene_name": "RegressionScene",
            "objective": "Explain a change",
            "duration_seconds": 4.0,
            "objects": [
                {"id": name, "kind": "diagram", "required": False}
                for name in ("text", "box", "model", "output")
            ],
            "direction": {"schema_version": "visual.direction/1", **direction},
        }
    )


def codes(scene_plan: ScenePlan, *samples: SceneCheckpoint) -> set[str]:
    observed = ObservedScene(
        scene_id=scene_plan.id, scene_name=scene_plan.scene_name, checkpoints=list(samples)
    )
    return {
        f.code
        for f in evaluate_visual_quality(scene_plan, observed).findings
        if f.code.startswith("DIRECTION_")
    }


def token_plan() -> ScenePlan:
    return plan(
        {
            "tokens": [
                {
                    "text_id": "text",
                    "container_id": "box",
                    "start_seconds": 0.0,
                    "end_seconds": 2.0,
                    "padding": 0.02,
                }
            ]
        }
    )


TEXT = obj("text", (0.3, 0.4, 0.5, 0.5))
BOX = obj("box", (0.27, 0.37, 0.53, 0.53))


def test_token_fits_and_whole_visual_unit_exits() -> None:
    assert codes(token_plan(), checkpoint(0.0, TEXT, BOX), checkpoint(2.0)) == set()


@pytest.mark.parametrize(
    "broken_box",
    [
        (0.31, 0.37, 0.53, 0.53),  # text escapes its container
        (0.29, 0.37, 0.53, 0.53),  # inside, but without the promised padding
    ],
)
def test_token_overflow_or_missing_padding_blocks_quality(
    broken_box: tuple[float, float, float, float],
) -> None:
    assert "DIRECTION_TOKEN_PADDING" in codes(
        token_plan(), checkpoint(0.0, TEXT, obj("box", broken_box)), checkpoint(2.0)
    )


def test_orphan_container_after_exit_blocks_quality() -> None:
    assert "DIRECTION_TOKEN_ORPHAN" in codes(
        token_plan(), checkpoint(0.0, TEXT, BOX), checkpoint(2.0, BOX)
    )


def test_missing_token_evidence_never_passes() -> None:
    assert "DIRECTION_EVIDENCE_MISSING" in codes(token_plan())


def test_unpaired_text_during_token_lifetime_blocks_quality() -> None:
    assert "DIRECTION_TOKEN_ORPHAN" in codes(
        token_plan(), checkpoint(0.0, TEXT, BOX), checkpoint(1.0, TEXT), checkpoint(2.0)
    )


def aspect_plan() -> ScenePlan:
    return plan(
        {
            "aspects": [
                {
                    "object_id": "model",
                    "start_seconds": 0.0,
                    "end_seconds": 2.0,
                    "expected_ratio": 1.0,
                    "relative_tolerance": 0.02,
                }
            ]
        }
    )


def test_circle_keeps_ratio_in_wide_camera_even_when_zoomed() -> None:
    # Default camera is 14 by 8: width .2 and height .35 both mean 2.8 world units.
    circle = obj("model", (0.3, 0.3, 0.5, 0.65))
    close = obj("model", (0.2, 0.1, 0.6, 0.8))
    assert codes(aspect_plan(), checkpoint(0.0, circle), checkpoint(2.0, close)) == set()


def test_nonuniform_scale_in_middle_is_rejected_even_if_endpoints_are_correct() -> None:
    circle = obj("model", (0.3, 0.3, 0.5, 0.65))
    ellipse = obj("model", (0.2, 0.3, 0.6, 0.65))
    assert "DIRECTION_ASPECT_RATIO" in codes(
        aspect_plan(), checkpoint(0.0, circle), checkpoint(1.0, ellipse), checkpoint(2.0, circle)
    )


def test_missing_aspect_evidence_blocks_quality() -> None:
    assert "DIRECTION_EVIDENCE_MISSING" in codes(aspect_plan(), checkpoint(0.0))


def processing_plan() -> ScenePlan:
    return plan(
        {
            "processing": [
                {
                    "model_id": "model",
                    "output_id": "output",
                    "animation": "Indicate",
                    "start_seconds": 1.0,
                    "end_seconds": 2.0,
                    "output_by_seconds": 3.0,
                }
            ]
        }
    )


def processing_codes(
    *,
    early: bool = False,
    animated_id: str = "model",
    include_animation: bool = True,
    include_output: bool = True,
) -> set[str]:
    from video_pipeline.runtime import AnimationFact

    p = processing_plan()
    output = obj("output", (0.7, 0.4, 0.8, 0.5))
    model = obj("model", (0.3, 0.3, 0.5, 0.65))
    samples = [
        checkpoint(0.0, model),
        checkpoint(1.0, model),
        checkpoint(1.5, model, *([output] if early else [])),
        checkpoint(2.0, model),
        checkpoint(3.0, model, *([output] if include_output else [])),
    ]
    observed = ObservedScene(
        scene_id=p.id,
        scene_name=p.scene_name,
        checkpoints=samples,
        animations=[
            AnimationFact(
                name="Indicate",
                object_ids=[animated_id],
                start_seconds=1.0,
                end_seconds=2.0,
                run_time=1.0,
            )
        ]
        if include_animation
        else [],
    )
    return {
        f.code
        for f in evaluate_visual_quality(p, observed).findings
        if f.code.startswith("DIRECTION_")
    }


def test_output_after_observed_processing_passes() -> None:
    assert processing_codes() == set()


def test_output_visible_during_processing_is_rejected() -> None:
    assert "DIRECTION_OUTPUT_EARLY" in processing_codes(early=True)


@pytest.mark.parametrize(
    "options", [{"include_animation": False}, {"animated_id": "box"}, {"include_output": False}]
)
def test_processing_declaration_without_matching_evidence_never_passes(
    options: dict[str, str | bool],
) -> None:
    assert "DIRECTION_EVIDENCE_MISSING" in processing_codes(**options)


@pytest.mark.parametrize(
    "defect, expected",
    [
        (None, set()),
        ("orphan", {"DIRECTION_TOKEN_ORPHAN"}),
        ("stretch", {"DIRECTION_ASPECT_RATIO"}),
        ("early", {"DIRECTION_OUTPUT_EARLY"}),
    ],
)
def test_real_manim_runtime_feeds_direction_gate(
    tmp_path: Path,
    defect: str | None,
    expected: set[str],
) -> None:
    from manim import Circle, Indicate, Rectangle, Text, VGroup, tempconfig

    from video_pipeline.runtime import VisualScene

    p = plan(
        {
            "tokens": [
                {
                    "text_id": "text",
                    "container_id": "box",
                    "start_seconds": 0.0,
                    "end_seconds": 2.0,
                    "padding": 0.005,
                }
            ],
            "aspects": [
                {
                    "object_id": "model",
                    "start_seconds": 0.0,
                    "end_seconds": 2.0,
                    "expected_ratio": 1.0,
                }
            ],
            "processing": [
                {
                    "model_id": "model",
                    "output_id": "output",
                    "animation": "Indicate",
                    "start_seconds": 1.0,
                    "end_seconds": 2.0,
                    "output_by_seconds": 3.0,
                }
            ],
        }
    )

    class RegressionScene(VisualScene):
        def construct(self) -> None:
            label = Text("GTA 6", font_size=24).shift([-3, 0, 0])
            box = Rectangle(width=label.width + 0.4, height=label.height + 0.4).move_to(label)
            token = VGroup(label, box)
            model = Circle(radius=0.5)
            output = Text("4", font_size=24).shift([3, 0, 0])
            for item, name in [(label, "text"), (box, "box"), (model, "model"), (output, "output")]:
                self.register_visual(item, name)
            self.add(token, model)
            self.checkpoint("live")
            self.wait(1.0)
            if defect == "stretch":
                model.stretch(2, 0)
            if defect == "early":
                self.add(output)
            self.checkpoint("processing-start")
            self.play(Indicate(model), run_time=1.0)
            self.remove(token)
            if defect == "orphan":
                self.add(box)
            self.checkpoint("processing-end")
            self.wait(1.0)
            self.add(output)
            self.checkpoint("output")

    with tempconfig(
        {
            "dry_run": True,
            "skip_animations": True,
            "media_dir": str(tmp_path),
            "pixel_width": 320,
            "pixel_height": 180,
            "frame_rate": 10,
        }
    ):
        scene = RegressionScene(scene_plan=p)
        scene.construct()
        observed = scene.write_observation(tmp_path / "observed.json")
    actual = {
        f.code
        for f in evaluate_visual_quality(p, observed).findings
        if f.code.startswith("DIRECTION_")
    }
    assert actual == expected


@pytest.mark.parametrize(
    "contract",
    [
        {
            "tokens": [
                {
                    "text_id": "missing",
                    "container_id": "box",
                    "start_seconds": 0.0,
                    "end_seconds": 2.0,
                }
            ]
        },
        {
            "tokens": [
                {
                    "text_id": "text",
                    "container_id": "text",
                    "start_seconds": 0.0,
                    "end_seconds": 2.0,
                }
            ]
        },
        {
            "tokens": [
                {"text_id": "text", "container_id": "box", "start_seconds": 2.0, "end_seconds": 1.0}
            ]
        },
        {
            "tokens": [
                {"text_id": "text", "container_id": "box", "start_seconds": 0.0, "end_seconds": 5.0}
            ]
        },
        {
            "aspects": [
                {
                    "object_id": "model",
                    "start_seconds": 0.0,
                    "end_seconds": 2.0,
                    "expected_ratio": float("nan"),
                }
            ]
        },
        {
            "aspects": [
                {
                    "object_id": "model",
                    "start_seconds": 0.0,
                    "end_seconds": 2.0,
                    "expected_ratio": 1.0,
                    "relative_tolerance": 1.0,
                }
            ]
        },
        {
            "processing": [
                {
                    "model_id": "model",
                    "output_id": "output",
                    "animation": "Indicate",
                    "start_seconds": 1.0,
                    "end_seconds": 2.0,
                    "output_by_seconds": 1.5,
                }
            ]
        },
        {
            "processing": [
                {
                    "model_id": "model",
                    "output_id": "output",
                    "animation": "Indicate",
                    "start_seconds": 1.0,
                    "end_seconds": 2.0,
                    "output_by_seconds": 5.0,
                }
            ]
        },
    ],
)
def test_invalid_contracts_are_rejected_before_render(contract: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        plan(contract)


def test_legacy_plan_keeps_direction_optional() -> None:
    legacy = ScenePlan(
        id="legacy", scene_name="LegacyScene", objective="Existing plan", duration_seconds=1.0
    )
    assert legacy.direction is None
    assert codes(legacy) == set()


def test_example_plan_round_trips_through_persisted_json() -> None:
    example = Path(__file__).resolve().parents[1] / "examples/direction-contract/plan.json"
    original = ScenePlan.model_validate_json(example.read_text())
    assert ScenePlan.model_validate_json(original.model_dump_json()).direction == original.direction
