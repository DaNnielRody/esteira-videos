"""Deterministic continuity checks between adjacent visual scenes."""

from __future__ import annotations

from video_pipeline.continuity import ContinuityObject, ContinuityState, compare_continuity
from video_pipeline.runtime import (
    AnimationFact,
    BoundingBox,
    CameraState,
    ObservedObject,
    ObservedScene,
)
from video_pipeline.scene_plan import ContinuityPlan, ScenePlan, VisualObject


def _state(*, background: str = "#0B1020", transition: str = "fade") -> ContinuityState:
    return ContinuityState(
        scene_id="scene-b",
        theme_id="production",
        background=background,
        camera=CameraState(),
        visual_scale=1.0,
        recurring_objects=[
            ContinuityObject(
                id="axis",
                identity="axis-primary",
                kind="line",
                color_role="primary",
                scale=1.0,
            )
        ],
        color_roles={"primary": "#4CC9F0"},
        typography={"body": "DejaVu Sans"},
        persistent_elements=["axis"],
        transition=transition,
    )


def test_adjacent_matching_states_have_no_continuity_findings() -> None:
    report = compare_continuity(_state(), _state())

    assert report.findings == []


def test_continuity_reports_background_identity_camera_and_transition_breaks() -> None:
    previous = _state()
    current = _state(background="#FFFFFF", transition="slide").model_copy(
        update={
            "recurring_objects": [
                ContinuityObject(
                    id="axis",
                    identity="axis-secondary",
                    kind="line",
                    color_role="accent",
                    scale=1.5,
                )
            ],
            "camera": CameraState(center_x=2.0),
        }
    )

    codes = {finding.code for finding in compare_continuity(previous, current).findings}

    assert {
        "CONTINUITY_BACKGROUND_CHANGED",
        "CONTINUITY_OBJECT_IDENTITY_CHANGED",
        "CONTINUITY_OBJECT_SCALE_CHANGED",
        "CONTINUITY_CAMERA_JUMP",
        "CONTINUITY_TRANSITION_CHANGED",
    } <= codes


def test_declared_continuity_changes_are_not_failures() -> None:
    previous = _state()
    current = _state(background="#FFFFFF", transition="slide")

    report = compare_continuity(
        previous,
        current,
        declared_changes=["background", "transition"],
    )

    assert not any(
        finding.code in {"CONTINUITY_BACKGROUND_CHANGED", "CONTINUITY_TRANSITION_CHANGED"}
        and finding.severity == "failure"
        for finding in report.findings
    )


def test_continuity_reports_typography_colour_scale_and_missing_object_changes() -> None:
    previous = _state()
    current = _state().model_copy(
        update={
            "typography": {"body": "Liberation Sans"},
            "color_roles": {"primary": "#FF0000"},
            "recurring_objects": [],
        }
    )

    codes = {finding.code for finding in compare_continuity(previous, current).findings}

    assert {
        "CONTINUITY_TYPOGRAPHY_CHANGED",
        "CONTINUITY_COLOR_PALETTE_CHANGED",
        "CONTINUITY_OBJECT_MISSING",
    } <= codes


def test_continuity_allows_intentional_typography_colour_scale_and_object_changes() -> None:
    previous = _state()
    current = _state().model_copy(
        update={
            "typography": {"body": "Liberation Sans"},
            "color_roles": {"primary": "#FF0000"},
            "visual_scale": 2.0,
            "recurring_objects": [],
        }
    )

    report = compare_continuity(
        previous,
        current,
        declared_changes=["typography", "colors", "scale", "objects"],
    )

    assert report.findings == []


def test_from_scene_only_carries_declared_recurring_objects() -> None:
    plan = ScenePlan(
        id="boundary",
        scene_name="BoundaryScene",
        objective="Keep one object and discard a temporary decoration.",
        duration_seconds=2.0,
        objects=[
            VisualObject(id="persistent", kind="arrow"),
            VisualObject(id="temporary", kind="text"),
        ],
        continuity_out=ContinuityPlan(
            required=True,
            expected_transition="none",
            recurring_object_ids=["persistent"],
        ),
    )
    persistent = ObservedObject(
        id="persistent",
        kind="arrow",
        bbox=BoundingBox(left=0.1, top=0.4, right=0.3, bottom=0.6),
        center_x=0.2,
        center_y=0.5,
        width=0.2,
        height=0.2,
    )
    temporary = ObservedObject(
        id="temporary",
        kind="text",
        bbox=BoundingBox(left=0.5, top=0.4, right=0.7, bottom=0.6),
        center_x=0.6,
        center_y=0.5,
        width=0.2,
        height=0.2,
    )
    observed = ObservedScene(
        scene_id="boundary",
        scene_name="BoundaryScene",
        final_state=[persistent, temporary],
    )

    state = ContinuityState.from_scene(plan, observed)

    assert [item.id for item in state.recurring_objects] == ["persistent"]


def test_from_scene_uses_measured_scale_and_boundary_specific_transition() -> None:
    plan = ScenePlan(
        id="measured",
        scene_name="MeasuredScene",
        objective="Preserve a measured object.",
        duration_seconds=2.0,
        objects=[VisualObject(id="shape", kind="square")],
        continuity_out=ContinuityPlan(
            recurring_object_ids=["shape"],
            expected_transition="none",
        ),
    )
    observed = ObservedScene(
        scene_id="measured",
        scene_name="MeasuredScene",
        final_state=[
            ObservedObject(
                id="shape",
                kind="square",
                bbox=BoundingBox(left=0.1, top=0.1, right=0.7, bottom=0.5),
                center_x=0.4,
                center_y=0.3,
                width=0.6,
                height=0.4,
            )
        ],
    )

    state = ContinuityState.from_scene(plan, observed)

    assert state.visual_scale == 0.6
    assert state.recurring_objects[0].scale == 0.6


def _transition_fact(name: str) -> AnimationFact:
    return AnimationFact(
        name=name,
        start_seconds=0.0,
        end_seconds=0.5,
        run_time=0.5,
    )


def _transition_plan(
    scene_id: str,
    *,
    incoming: bool = False,
    allow_transition_change: bool = False,
) -> ScenePlan:
    boundary = ContinuityPlan(
        required=True,
        expected_transition="fade",
        allow_changes=["transition"] if allow_transition_change else [],
    )
    return ScenePlan(
        id=scene_id,
        scene_name=f"{scene_id.title()}Scene",
        objective="Measure a scene transition.",
        duration_seconds=2.0,
        continuity_in=boundary if incoming else None,
        continuity_out=None if incoming else boundary,
    )


def test_required_transition_reports_absence_and_accepts_explicit_fade() -> None:
    previous_plan = _transition_plan("previous")
    current_plan = _transition_plan("current", incoming=True)
    previous_without_fade = ObservedScene(
        scene_id="previous",
        scene_name="PreviousScene",
        animations=[_transition_fact("Create")],
    )
    current_without_fade = ObservedScene(
        scene_id="current",
        scene_name="CurrentScene",
        animations=[_transition_fact("Create")],
    )
    missing = compare_continuity(
        ContinuityState.from_scene(previous_plan, previous_without_fade),
        ContinuityState.from_scene(current_plan, current_without_fade, state="initial"),
    )
    assert any(finding.code == "CONTINUITY_TRANSITION_MISSING" for finding in missing.findings)

    previous_with_fade = previous_without_fade.model_copy(
        update={"animations": [_transition_fact("FadeOut")]}
    )
    current_with_fade = current_without_fade.model_copy(
        update={"animations": [_transition_fact("FadeIn")]}
    )
    present = compare_continuity(
        ContinuityState.from_scene(previous_plan, previous_with_fade),
        ContinuityState.from_scene(current_plan, current_with_fade, state="initial"),
    )
    assert not any(finding.code == "CONTINUITY_TRANSITION_MISSING" for finding in present.findings)


def test_required_transition_change_is_allowed_when_declared() -> None:
    previous_plan = _transition_plan("previous")
    current_plan = _transition_plan("current", incoming=True, allow_transition_change=True)
    previous = ContinuityState.from_scene(
        previous_plan,
        ObservedScene(
            scene_id="previous",
            scene_name="PreviousScene",
            animations=[_transition_fact("FadeOut")],
        ),
    )
    current = ContinuityState.from_scene(
        current_plan,
        ObservedScene(
            scene_id="current",
            scene_name="CurrentScene",
            animations=[_transition_fact("Create")],
        ),
        state="initial",
    )

    report = compare_continuity(previous, current, declared_changes=["transition"])

    assert not any(
        finding.code in {"CONTINUITY_TRANSITION_CHANGED", "CONTINUITY_TRANSITION_MISSING"}
        for finding in report.findings
    )
