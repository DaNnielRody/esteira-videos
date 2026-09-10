"""Public safe-area contract for intentional camera reveals and focus moves."""

from __future__ import annotations

import pytest

from video_pipeline.critics import check_safe_area
from video_pipeline.runtime import BoundingBox, ObservedObject, ObservedScene, SceneCheckpoint
from video_pipeline.scene_plan import Beat, ScenePlan, VisualObject


def _plan(
    *, framing: str = "contained", start_seconds: float = 0.0, end_seconds: float = 2.0
) -> ScenePlan:
    return ScenePlan(
        id="camera-framing",
        scene_name="CameraFramingScene",
        objective="Follow one diagram while the camera reveals its context.",
        duration_seconds=4.0,
        objects=[VisualObject(id="diagram", kind="diagram")],
        beats=[
            Beat(
                id="camera-move",
                action="camera reveal",
                objects=["diagram"],
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                duration_seconds=end_seconds - start_seconds,
                framing=framing,
            )
        ],
    )


def _object(
    *,
    object_id: str = "diagram",
    left: float,
    top: float = 0.25,
    right: float,
    bottom: float = 0.65,
    logical_time: float,
    kind: str = "diagram",
    text: str | None = None,
) -> ObservedObject:
    return ObservedObject(
        id=object_id,
        kind=kind,
        bbox=BoundingBox(left=left, top=top, right=right, bottom=bottom),
        center_x=(left + right) / 2,
        center_y=(top + bottom) / 2,
        width=right - left,
        height=bottom - top,
        logical_time=logical_time,
        text=text,
    )


def _observed(*objects: ObservedObject) -> ObservedScene:
    checkpoints = [
        SceneCheckpoint(
            id=f"t-{item.logical_time}",
            beat_id="camera-move" if item.logical_time <= 2.0 else None,
            instant_seconds=item.logical_time,
            objects=[item],
        )
        for item in objects
    ]
    return ObservedScene(
        scene_id="camera-framing",
        scene_name="CameraFramingScene",
        initial_state=[objects[0]],
        final_state=[objects[-1]],
        checkpoints=checkpoints,
    )


def test_camera_reveal_allows_temporary_graphic_clipping_after_full_reveal() -> None:
    clipped = _object(left=-0.12, right=0.42, logical_time=0.5)
    revealed = _object(left=0.18, right=0.72, logical_time=1.5)

    findings = check_safe_area(_plan(framing="camera_reveal"), _observed(clipped, revealed))

    assert findings == []


def test_camera_focus_has_the_same_explicit_temporal_permission() -> None:
    clipped = _object(left=-0.12, right=0.42, logical_time=0.5)
    focused = _object(left=0.18, right=0.72, logical_time=1.5)

    findings = check_safe_area(_plan(framing="camera_focus"), _observed(clipped, focused))

    assert findings == []


def test_camera_reveal_allows_temporary_total_exit_after_full_reveal() -> None:
    outside = _object(left=-0.85, right=-0.2, logical_time=0.5)
    revealed = _object(left=0.18, right=0.72, logical_time=1.5)

    findings = check_safe_area(_plan(framing="camera_reveal"), _observed(outside, revealed))

    assert findings == []


def test_camera_reveal_allows_temporary_oversize_after_full_reveal() -> None:
    oversize = _object(left=-0.1, right=1.1, logical_time=0.5)
    revealed = _object(left=0.18, right=0.72, logical_time=1.5)

    findings = check_safe_area(_plan(framing="camera_reveal"), _observed(oversize, revealed))

    assert findings == []


def test_default_framing_remains_strictly_contained() -> None:
    clipped = _object(left=-0.12, right=0.42, logical_time=0.5)
    contained = _object(left=0.18, right=0.72, logical_time=1.5)

    findings = check_safe_area(_plan(), _observed(clipped, contained))

    assert any(finding.code == "OBJECT_CLIPPED" for finding in findings)


def test_camera_reveal_does_not_excuse_clipping_outside_declared_interval() -> None:
    clipped_late = _object(left=-0.12, right=0.42, logical_time=2.5)
    revealed = _object(left=0.18, right=0.72, logical_time=3.0)

    findings = check_safe_area(_plan(framing="camera_reveal"), _observed(clipped_late, revealed))

    assert any(finding.code == "OBJECT_CLIPPED" for finding in findings)


def test_camera_reveal_requires_full_enclosure_in_the_same_declared_interval() -> None:
    clipped = _object(left=-0.12, right=0.42, logical_time=0.5)
    revealed_late = _object(left=0.18, right=0.72, logical_time=3.5)

    findings = check_safe_area(_plan(framing="camera_reveal"), _observed(clipped, revealed_late))

    assert any(finding.code == "OBJECT_CLIPPED" for finding in findings)


def test_camera_reveal_does_not_use_full_enclosure_before_the_declared_interval() -> None:
    revealed_early = _object(left=0.18, right=0.72, logical_time=0.5)
    clipped = _object(left=-0.12, right=0.42, logical_time=1.5)

    findings = check_safe_area(
        _plan(framing="camera_reveal", start_seconds=1.0, end_seconds=3.0),
        _observed(revealed_early, clipped),
    )

    assert any(finding.code == "OBJECT_CLIPPED" for finding in findings)


def test_camera_reveal_requires_full_enclosure_evidence() -> None:
    clipped = _object(left=-0.12, right=0.42, logical_time=0.5)
    still_clipped = _object(left=-0.04, right=0.72, logical_time=1.5)

    findings = check_safe_area(_plan(framing="camera_reveal"), _observed(clipped, still_clipped))

    assert any(finding.code == "OBJECT_CLIPPED" for finding in findings)


def test_camera_reveal_does_not_exempt_an_object_missing_from_the_beat() -> None:
    clipped = _object(object_id="other", left=-0.12, right=0.42, logical_time=0.5)
    revealed = _object(object_id="other", left=0.18, right=0.72, logical_time=1.5)

    findings = check_safe_area(_plan(framing="camera_reveal"), _observed(clipped, revealed))

    assert any(finding.code == "OBJECT_CLIPPED" for finding in findings)


def test_camera_reveal_keeps_text_inside_reading_safe_area() -> None:
    clipped = _object(
        left=-0.12,
        right=0.42,
        logical_time=0.5,
        kind="text",
        text="Essential label",
    )
    revealed = _object(
        left=0.18,
        right=0.72,
        logical_time=1.5,
        kind="text",
        text="Essential label",
    )

    findings = check_safe_area(_plan(framing="camera_reveal"), _observed(clipped, revealed))

    assert any(finding.code == "TEXT_OUTSIDE_SAFE_AREA" for finding in findings)


def test_framing_contract_is_typed_and_defaults_to_contained() -> None:
    beat = Beat(objects=["diagram"])

    assert beat.framing == "contained"
    with pytest.raises(ValueError, match="framing"):
        Beat(objects=["diagram"], framing="slide-away")

    with pytest.raises(ValueError, match="framing"):
        Beat(framing="camera_reveal")
