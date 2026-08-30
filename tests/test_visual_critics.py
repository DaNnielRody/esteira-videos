"""Deterministic visual quality checks over explicit plans and observed facts."""

from __future__ import annotations

import pytest

from video_pipeline.critics import (
    check_contrast,
    check_legibility,
    check_overlaps,
    check_plan_coherence,
    check_rhythm,
    check_safe_area,
    evaluate_visual_quality,
)
from video_pipeline.observation import FrameObservation, ObservedShape
from video_pipeline.runtime import (
    AnimationFact,
    BoundingBox,
    ObservedObject,
    ObservedScene,
    SceneCheckpoint,
)
from video_pipeline.scene_plan import Beat, LayoutExpectation, ScenePlan, VisualObject


def _plan(*, overlap_policy: str = "forbidden") -> ScenePlan:
    return ScenePlan(
        id="safe-area",
        scene_name="SafeAreaScene",
        objective="Show one definition clearly.",
        duration_seconds=4.0,
        objects=[
            VisualObject(
                id="definition",
                kind="text",
                text="A definition",
                region="center",
                color_role="text",
                overlap_policy=overlap_policy,  # type: ignore[arg-type]
            )
        ],
        beats=[Beat(id="show", action="introduce", objects=["definition"], duration_seconds=2.0)],
    )


def _observed(*objects: ObservedObject, scene_id: str = "safe-area") -> ObservedScene:
    return ObservedScene(
        scene_id=scene_id,
        scene_name="SafeAreaScene",
        initial_state=list(objects),
        final_state=list(objects),
    )


def _object(
    *,
    object_id: str = "definition",
    left: float = 0.2,
    top: float = 0.2,
    right: float = 0.8,
    bottom: float = 0.35,
    color: str = "#F8FAFC",
    kind: str = "text",
    width: float | None = None,
    height: float | None = None,
    logical_time: float = 0.0,
    visible: bool = True,
    orientation: float = 0.0,
    text: str | None = None,
    formula: str | None = None,
) -> ObservedObject:
    box = BoundingBox(left=left, top=top, right=right, bottom=bottom)
    return ObservedObject(
        id=object_id,
        kind=kind,
        bbox=box,
        center_x=(left + right) / 2,
        center_y=(top + bottom) / 2,
        width=right - left if width is None else width,
        height=bottom - top if height is None else height,
        observed_color=color,
        logical_time=logical_time,
        visible=visible,
        orientation=orientation,
        text=text,
        formula=formula,
    )


def test_safe_area_critic_reports_text_outside_the_safe_area() -> None:
    object_outside = _object(left=0.01, right=0.70)

    report = evaluate_visual_quality(_plan(), _observed(object_outside))

    finding = next(item for item in report.findings if item.code == "TEXT_OUTSIDE_SAFE_AREA")
    assert finding.severity == "failure"
    assert finding.observed["left_edge"] == pytest.approx(0.01)
    assert "safe" in finding.suggestion.lower()


def test_safe_area_critic_accepts_content_inside_the_safe_area() -> None:
    report = evaluate_visual_quality(_plan(), _observed(_object()))

    assert not any(item.code == "TEXT_OUTSIDE_SAFE_AREA" for item in report.findings)


def test_overlap_policy_distinguishes_prohibited_and_declared_intentional_overlap() -> None:
    plan = ScenePlan(
        id="overlap",
        scene_name="OverlapScene",
        objective="Show two separate labels.",
        duration_seconds=4.0,
        objects=[
            VisualObject(id="left", kind="circle", overlap_policy="forbidden"),
            VisualObject(id="right", kind="label", overlap_policy="forbidden"),
        ],
    )
    left = _object(object_id="left", left=0.2, right=0.7, top=0.2, bottom=0.7, kind="circle")
    right = _object(object_id="right", left=0.5, right=0.8, top=0.4, bottom=0.8)

    report = evaluate_visual_quality(plan, _observed(left, right, scene_id="overlap"))

    assert any(item.code == "PROHIBITED_OVERLAP" for item in report.findings)
    intentional = plan.model_copy(
        update={
            "objects": [
                plan.objects[0],
                plan.objects[1].model_copy(update={"overlap_policy": "intentional"}),
            ]
        }
    )
    assert not any(
        item.code == "PROHIBITED_OVERLAP"
        for item in evaluate_visual_quality(
            intentional, _observed(left, right, scene_id="overlap")
        ).findings
    )


def test_contrast_critic_reports_low_contrast_and_semantic_color_mismatch() -> None:
    plan = _plan()
    grey = _object(color="#555555")

    findings = [
        item
        for item in evaluate_visual_quality(plan, _observed(grey)).findings
        if item.code in {"LOW_CONTRAST", "SEMANTIC_COLOR_MISMATCH"}
    ]

    assert {item.code for item in findings} == {"LOW_CONTRAST", "SEMANTIC_COLOR_MISMATCH"}
    assert findings[0].observed["ratio"] < findings[0].expected["minimum_ratio"]


def test_legibility_critic_reports_small_text_and_short_reading_time() -> None:
    plan = _plan()
    small = _object(height=0.01)
    short_plan = plan.model_copy(
        update={
            "beats": [
                Beat(id="show", action="introduce", objects=["definition"], duration_seconds=0.2)
            ]
        }
    )

    findings = evaluate_visual_quality(short_plan, _observed(small)).findings

    assert any(item.code == "TEXT_TOO_SMALL" for item in findings)
    assert any(item.code == "READ_DURATION_TOO_SHORT" for item in findings)


def test_rhythm_critic_reports_long_static_interval_and_observed_duration_mismatch() -> None:
    plan = _plan()
    object_now = _object()
    observed = ObservedScene(
        scene_id="safe-area",
        scene_name="SafeAreaScene",
        initial_state=[object_now],
        final_state=[object_now],
        checkpoints=[
            {"id": "start", "instant_seconds": 0.0, "objects": [object_now], "visual_change": True},
            {"id": "hold", "instant_seconds": 4.0, "objects": [object_now], "visual_change": False},
        ],
    )

    short_plan = plan.model_copy(update={"duration_seconds": 2.0})
    findings = evaluate_visual_quality(short_plan, observed).findings

    assert any(item.code == "LONG_STATIC_INTERVAL" for item in findings)
    assert any(item.code == "OBSERVED_DURATION_MISMATCH" for item in findings)


def test_plan_coherence_reports_missing_required_object_and_wrong_region() -> None:
    plan = _plan()
    wrong_region = _object(left=0.8, right=0.95)

    findings = evaluate_visual_quality(plan, _observed(wrong_region)).findings

    assert any(item.code == "OBJECT_REGION_MISMATCH" for item in findings)
    assert not any(item.code == "REQUIRED_OBJECT_MISSING" for item in findings)
    missing = evaluate_visual_quality(plan, _observed()).findings
    assert any(item.code == "REQUIRED_OBJECT_MISSING" for item in missing)


@pytest.mark.parametrize(
    ("changes", "visible", "expected_code"),
    [
        ({"left": -0.1, "right": 0.4}, True, "OBJECT_CLIPPED"),
        ({"left": -0.8, "right": -0.2}, True, "OBJECT_INVISIBLE"),
        ({}, False, "OBJECT_INVISIBLE"),
    ],
)
def test_safe_area_distinguishes_partial_clipping_total_clipping_and_invisible(
    changes: dict[str, float],
    visible: bool,
    expected_code: str,
) -> None:
    item = _object(visible=visible, **changes)

    findings = check_safe_area(_plan(), _observed(item))

    assert any(finding.code == expected_code for finding in findings)


def test_legibility_reports_a_formula_that_exceeds_the_safe_width() -> None:
    plan = ScenePlan(
        id="formula",
        scene_name="FormulaScene",
        objective="Keep a formula readable.",
        duration_seconds=3.0,
        objects=[
            VisualObject(
                id="formula",
                kind="mathtex",
                formula="x^2 + y^2 = r^2",
                color_role="accent",
            )
        ],
    )
    formula = _object(
        object_id="formula",
        kind="mathtex",
        left=0.02,
        right=0.98,
        top=0.4,
        bottom=0.55,
        formula="x^2 + y^2 = r^2",
    )

    findings = check_legibility(plan, _observed(formula, scene_id="formula"))

    assert any(finding.code == "FORMULA_TOO_WIDE" for finding in findings)


@pytest.mark.parametrize(
    ("left_policy", "right_policy", "with_animation", "allowed"),
    [
        ("transient", "forbidden", False, False),
        ("transient", "forbidden", True, True),
        ("group", "forbidden", False, True),
        ("background", "forbidden", False, True),
    ],
)
def test_overlap_critic_handles_transient_group_and_background_exceptions(
    left_policy: str,
    right_policy: str,
    with_animation: bool,
    allowed: bool,
) -> None:
    plan = ScenePlan(
        id="overlap-policy",
        scene_name="OverlapPolicyScene",
        objective="Check overlap policy.",
        duration_seconds=2.0,
        objects=[
            VisualObject(
                id="left",
                kind="background" if left_policy == "background" else "circle",
                overlap_policy=left_policy,  # type: ignore[arg-type]
                group_id="cluster" if left_policy == "group" else None,
            ),
            VisualObject(
                id="right",
                kind="circle",
                overlap_policy=right_policy,  # type: ignore[arg-type]
                group_id="cluster" if left_policy == "group" else None,
            ),
        ],
    )
    left = _object(
        object_id="left",
        kind="background" if left_policy == "background" else "circle",
        left=0.2,
        top=0.2,
        right=0.7,
        bottom=0.7,
        logical_time=0.5,
    )
    right = _object(
        object_id="right",
        kind="circle",
        left=0.5,
        top=0.4,
        right=0.8,
        bottom=0.8,
        logical_time=0.5,
    )
    animations = (
        [
            AnimationFact(
                name="Transform",
                object_ids=["left", "right"],
                start_seconds=0.0,
                end_seconds=1.0,
                run_time=1.0,
            )
        ]
        if with_animation
        else []
    )
    observed = _observed(left, right, scene_id="overlap-policy").model_copy(
        update={"animations": animations}
    )

    findings = check_overlaps(plan, observed)

    assert any(finding.code == "PROHIBITED_OVERLAP" for finding in findings) is not allowed


def test_transient_overlap_is_not_allowed_by_an_unrelated_animation() -> None:
    plan = ScenePlan(
        id="transient-unrelated",
        scene_name="TransientUnrelatedScene",
        objective="Check transient evidence.",
        duration_seconds=2.0,
        objects=[
            VisualObject(id="left", kind="circle", overlap_policy="transient"),
            VisualObject(id="right", kind="circle"),
        ],
    )
    left = _object(object_id="left", kind="circle", left=0.2, right=0.7, logical_time=0.5)
    right = _object(object_id="right", kind="circle", left=0.5, right=0.8, logical_time=0.5)
    unrelated = AnimationFact(
        name="Transform",
        object_ids=["other"],
        start_seconds=0.0,
        end_seconds=1.0,
        run_time=1.0,
    )
    observed = _observed(left, right, scene_id="transient-unrelated").model_copy(
        update={"animations": [unrelated]}
    )

    findings = check_overlaps(plan, observed)

    assert any(finding.code == "PROHIBITED_OVERLAP" for finding in findings)


def test_contrast_accepts_sufficient_colour_and_uses_post_render_pixel_colour() -> None:
    sufficient_plan = ScenePlan(
        id="contrast",
        scene_name="ContrastScene",
        objective="Check sufficient contrast.",
        duration_seconds=2.0,
        objects=[VisualObject(id="shape", kind="circle", color_role="primary")],
    )
    sufficient = _object(
        object_id="shape",
        kind="circle",
        color="#4CC9F0",
    )
    sufficient_findings = check_contrast(
        sufficient_plan,
        _observed(sufficient, scene_id="contrast"),
    )
    assert not any(finding.code == "LOW_CONTRAST" for finding in sufficient_findings)

    text_plan = ScenePlan(
        id="pixel-colour",
        scene_name="PixelColourScene",
        objective="Use pixel evidence for semantic colour.",
        duration_seconds=2.0,
        objects=[VisualObject(id="label", kind="text", text="Label", color_role="text")],
    )
    runtime_colour = _object(
        object_id="label",
        color="#F8FAFC",
        text="Label",
    )
    red_glyphs = FrameObservation(
        index=0,
        shapes=[
            ObservedShape(
                kind="polygon",
                color="red",
                center_x=0.48,
                center_y=0.50,
                area_fraction=0.01,
                extent=0.7,
                observed_rgb=(200, 0, 0),
            ),
            ObservedShape(
                kind="polygon",
                color="red",
                center_x=0.52,
                center_y=0.50,
                area_fraction=0.01,
                extent=0.7,
                observed_rgb=(200, 0, 0),
            ),
        ],
    )
    pixel_observed = _observed(runtime_colour, scene_id="pixel-colour").model_copy(
        update={"frames": [red_glyphs]}
    )

    pixel_findings = check_contrast(text_plan, pixel_observed)

    mismatch = next(
        finding for finding in pixel_findings if finding.code == "SEMANTIC_COLOR_MISMATCH"
    )
    assert mismatch.observed["color"] == "#C80000"
    assert not any(finding.code == "PIXEL_COLOR_AMBIGUOUS" for finding in pixel_findings)


def test_legibility_reports_density_excess_lines_and_observed_short_presence() -> None:
    plan = _plan().model_copy(
        update={
            "layout": LayoutExpectation(max_text_lines=1, max_content_density=0.65),
        }
    )
    item = _object(
        text="one\ntwo\nthree",
        width=0.6,
        height=0.4,
        logical_time=0.2,
    )
    observed = ObservedScene(
        scene_id="safe-area",
        scene_name="SafeAreaScene",
        initial_state=[],
        final_state=[],
        checkpoints=[
            SceneCheckpoint(id="empty", instant_seconds=0.0, objects=[]),
            SceneCheckpoint(id="shown", instant_seconds=0.2, objects=[item]),
            SceneCheckpoint(id="removed", instant_seconds=0.4, objects=[]),
        ],
    )

    findings = check_legibility(plan, observed)

    codes = {finding.code for finding in findings}
    assert {"TEXT_LINES_EXCESSIVE", "CONTENT_DENSITY_HIGH", "READ_DURATION_TOO_SHORT"} <= codes
    reading = next(finding for finding in findings if finding.code == "READ_DURATION_TOO_SHORT")
    assert reading.observed["duration_seconds"] == pytest.approx(0.2)


def test_rhythm_reports_fast_beats_simultaneous_actions_and_new_object_burst() -> None:
    objects = [VisualObject(id=object_id, kind="circle") for object_id in ("one", "two", "three")]
    plan = ScenePlan(
        id="rhythm",
        scene_name="RhythmScene",
        objective="Measure visual rhythm.",
        duration_seconds=2.0,
        objects=objects,
        beats=[Beat(id="show", action="introduce", objects=["one"], duration_seconds=0.1)],
    )
    shown = [
        _object(
            object_id=object_id,
            kind="circle",
            left=0.1 + index * 0.2,
            right=0.25 + index * 0.2,
            top=0.4,
            bottom=0.55,
            logical_time=0.1,
        )
        for index, object_id in enumerate(("one", "two", "three"))
    ]
    observed = ObservedScene(
        scene_id="rhythm",
        scene_name="RhythmScene",
        initial_state=[],
        final_state=shown,
        checkpoints=[
            SceneCheckpoint(id="empty", instant_seconds=0.0, objects=[]),
            SceneCheckpoint(id="shown", instant_seconds=0.1, objects=shown),
        ],
        animations=[
            AnimationFact(
                name="Create+Create+Create",
                object_ids=["one", "two", "three"],
                start_seconds=0.1,
                end_seconds=1.1,
                run_time=1.0,
            )
        ],
    )

    findings = check_rhythm(plan, observed)

    codes = {finding.code for finding in findings}
    assert {"BEAT_TOO_FAST", "SIMULTANEOUS_IMPORTANT_ACTIONS", "OBJECT_BURST"} <= codes


def test_rhythm_reports_missing_visual_closure() -> None:
    plan = _plan()
    object_now = _object()
    observed = ObservedScene(
        scene_id="safe-area",
        scene_name="SafeAreaScene",
        initial_state=[],
        final_state=[],
        checkpoints=[
            SceneCheckpoint(id="shown", instant_seconds=0.0, objects=[object_now]),
            SceneCheckpoint(id="end", instant_seconds=2.0, objects=[], visual_change=True),
        ],
    )

    findings = check_rhythm(plan, observed)

    assert any(finding.code == "MISSING_VISUAL_CLOSURE" for finding in findings)


def test_coherence_reports_extra_object_text_formula_and_absent_beat() -> None:
    plan = ScenePlan(
        id="coherence",
        scene_name="CoherenceScene",
        objective="Check authored content.",
        duration_seconds=4.0,
        objects=[
            VisualObject(id="label", kind="text", text="Expected"),
            VisualObject(id="formula", kind="mathtex", formula="x=1"),
        ],
        beats=[
            Beat(id="label-beat", action="introduce", objects=["label"], text="Expected"),
            Beat(id="formula-beat", action="introduce", objects=["formula"], formula="x=1"),
        ],
    )
    wrong_label = _object(
        object_id="label",
        text="Wrong",
        logical_time=0.0,
    )
    wrong_formula = _object(
        object_id="formula",
        kind="mathtex",
        formula="y=2",
        logical_time=1.0,
    )
    extra = _object(object_id="extra", kind="circle", logical_time=1.0)
    observed = ObservedScene(
        scene_id="coherence",
        scene_name="CoherenceScene",
        initial_state=[],
        final_state=[wrong_label, wrong_formula, extra],
        checkpoints=[
            SceneCheckpoint(
                id="label",
                beat_id="label-beat",
                instant_seconds=0.0,
                objects=[wrong_label],
            ),
            SceneCheckpoint(
                id="formula",
                beat_id="formula-beat",
                instant_seconds=1.0,
                objects=[wrong_label, wrong_formula, extra],
            ),
        ],
    )

    findings = check_plan_coherence(plan, observed)
    codes = {finding.code for finding in findings}

    assert {"UNPLANNED_OBJECT", "BEAT_TEXT_MISMATCH", "BEAT_FORMULA_MISMATCH"} <= codes

    absent = check_plan_coherence(
        plan,
        ObservedScene(scene_id="coherence", scene_name="CoherenceScene"),
    )
    assert any(finding.code == "BEAT_NOT_OBSERVED" for finding in absent)


def test_coherence_reports_wrong_sequence_and_missing_movement() -> None:
    sequence_plan = ScenePlan(
        id="sequence",
        scene_name="SequenceScene",
        objective="Check authored beat order.",
        duration_seconds=3.0,
        objects=[VisualObject(id="first", kind="circle"), VisualObject(id="second", kind="circle")],
        beats=[
            Beat(id="first-beat", action="introduce", objects=["first"]),
            Beat(id="second-beat", action="introduce", objects=["second"]),
        ],
    )
    first = _object(object_id="first", kind="circle", left=0.1, right=0.2)
    second = _object(object_id="second", kind="circle", left=0.3, right=0.4)
    reversed_observed = ObservedScene(
        scene_id="sequence",
        scene_name="SequenceScene",
        checkpoints=[
            SceneCheckpoint(
                id="second-first",
                beat_id="first-beat",
                instant_seconds=0.0,
                objects=[second],
            ),
            SceneCheckpoint(
                id="first-second",
                beat_id="second-beat",
                instant_seconds=1.0,
                objects=[first],
            ),
        ],
    )
    sequence_findings = check_plan_coherence(sequence_plan, reversed_observed)
    assert any(finding.code == "BEAT_SEQUENCE_WRONG" for finding in sequence_findings)

    movement_plan = ScenePlan(
        id="movement",
        scene_name="MovementScene",
        objective="Check observed movement.",
        duration_seconds=3.0,
        objects=[VisualObject(id="vector", kind="arrow")],
        beats=[
            Beat(id="start", action="introduce", objects=["vector"]),
            Beat(id="move", action="transform", objects=["vector"], movement="right"),
        ],
    )
    stationary = _object(object_id="vector", kind="arrow", left=0.1, right=0.3)
    stationary_later = _object(
        object_id="vector",
        kind="arrow",
        left=0.1,
        right=0.3,
        logical_time=1.0,
    )
    stationary_observed = ObservedScene(
        scene_id="movement",
        scene_name="MovementScene",
        checkpoints=[
            SceneCheckpoint(id="start", beat_id="start", instant_seconds=0.0, objects=[stationary]),
            SceneCheckpoint(
                id="move",
                beat_id="move",
                instant_seconds=1.0,
                objects=[stationary_later],
            ),
        ],
    )
    movement_findings = check_plan_coherence(movement_plan, stationary_observed)
    assert any(finding.code == "BEAT_MOVEMENT_MISSING" for finding in movement_findings)
