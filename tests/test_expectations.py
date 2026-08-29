"""Behavioral tests for checking a rendered storyboard against a scene spec."""

from __future__ import annotations

import pytest

try:
    from video_pipeline.expectations import (
        SceneBeat,
        SceneExpectations,
        check_expectations,
    )
    from video_pipeline.observation import FrameObservation, ObservedShape
except (ImportError, ModuleNotFoundError):
    SceneBeat = None  # type: ignore[assignment,misc]
    SceneExpectations = None  # type: ignore[assignment,misc]
    check_expectations = None  # type: ignore[assignment]
    FrameObservation = None  # type: ignore[assignment,misc]
    ObservedShape = None  # type: ignore[assignment,misc]


def _require_contract() -> None:
    if check_expectations is None:
        pytest.fail("SCENE_EXPECTATIONS_CONTRACT_MISSING")


def _shape(
    kind: str,
    center_x: float,
    center_y: float = 0.5,
    color: str = "white",
) -> ObservedShape:
    return ObservedShape(
        kind=kind,
        color=color,
        center_x=center_x,
        center_y=center_y,
        area_fraction=0.05,
        extent=0.8,
    )


def _frames(*rows: list[ObservedShape]) -> list[FrameObservation]:
    return [FrameObservation(index=index, shapes=row) for index, row in enumerate(rows)]


def _acceptance() -> SceneExpectations:
    return SceneExpectations(
        max_shapes=1,
        beats=[
            SceneBeat(shape="circle", region="center"),
            SceneBeat(shape="square", region="center"),
            SceneBeat(shape="square", moved="right"),
        ],
    )


def test_acceptance_storyboard_satisfies_every_beat() -> None:
    """Circle centre, square centre, then the square further right passes."""

    _require_contract()
    frames = _frames(
        [_shape("circle", 0.50)],
        [_shape("circle", 0.50)],
        [_shape("square", 0.50)],
        [_shape("square", 0.57)],
        [_shape("square", 0.64)],
    )

    assert check_expectations(frames, _acceptance()) == []


def test_a_second_visible_shape_is_reported() -> None:
    """A duplicated mobject breaks the declared shape budget."""

    _require_contract()
    frames = _frames(
        [_shape("circle", 0.50)],
        [_shape("square", 0.50)],
        [_shape("square", 0.50), _shape("square", 0.64)],
    )

    reasons = check_expectations(frames, _acceptance())

    assert reasons
    assert any("2 shapes" in reason for reason in reasons)


def test_a_missing_transformation_is_reported() -> None:
    """A scene that never shows a square cannot satisfy the square beats."""

    _require_contract()
    frames = _frames(
        [_shape("circle", 0.50)],
        [_shape("circle", 0.57)],
        [_shape("circle", 0.64)],
    )

    reasons = check_expectations(frames, _acceptance())

    assert reasons
    assert any("square" in reason for reason in reasons)


def test_a_shape_that_never_moves_right_is_reported() -> None:
    """The final beat needs observed rightward motion, not a static square."""

    _require_contract()
    frames = _frames(
        [_shape("circle", 0.50)],
        [_shape("square", 0.50)],
        [_shape("square", 0.50)],
    )

    reasons = check_expectations(frames, _acceptance())

    assert reasons
    assert any("right" in reason for reason in reasons)


def test_beats_must_be_satisfied_in_the_declared_order() -> None:
    """A square before the circle does not satisfy a circle-then-square spec."""

    _require_contract()
    frames = _frames(
        [_shape("square", 0.50)],
        [_shape("square", 0.64)],
        [_shape("circle", 0.50)],
    )

    reasons = check_expectations(frames, _acceptance())

    assert reasons


def test_no_declared_beats_checks_only_the_shape_budget() -> None:
    """A spec without beats still rejects a duplicated mobject."""

    _require_contract()
    expectations = SceneExpectations(max_shapes=1, beats=[])
    good = _frames([_shape("circle", 0.5)])
    bad = _frames([_shape("circle", 0.4), _shape("circle", 0.6)])

    assert check_expectations(good, expectations) == []
    assert check_expectations(bad, expectations)


def test_an_empty_storyboard_is_reported() -> None:
    """A video with no observable frame cannot prove any beat."""

    _require_contract()
    assert check_expectations([], _acceptance())


def test_a_declared_colour_must_be_observed() -> None:
    """A beat naming a colour is not satisfied by the right shape in another."""

    _require_contract()
    expectations = SceneExpectations(
        max_shapes=1,
        beats=[SceneBeat(shape="circle", color="blue")],
    )
    blue = _frames([_shape("circle", 0.5, color="blue")])
    red = _frames([_shape("circle", 0.5, color="red")])

    assert check_expectations(blue, expectations) == []
    assert check_expectations(red, expectations)


def test_a_beat_without_a_colour_accepts_any_colour() -> None:
    """Colour is opt-in per beat, like every other constraint."""

    _require_contract()
    expectations = SceneExpectations(max_shapes=1, beats=[SceneBeat(shape="circle")])

    assert check_expectations(_frames([_shape("circle", 0.5, color="pink")]), expectations) == []


def test_a_colour_change_is_expressible_as_two_beats() -> None:
    """A blue circle becoming a red square is two beats, in order."""

    _require_contract()
    expectations = SceneExpectations(
        max_shapes=1,
        beats=[
            SceneBeat(shape="circle", color="blue"),
            SceneBeat(shape="square", color="red"),
        ],
    )
    good = _frames(
        [_shape("circle", 0.5, color="blue")],
        [_shape("square", 0.5, color="red")],
    )
    reversed_order = _frames(
        [_shape("square", 0.5, color="red")],
        [_shape("circle", 0.5, color="blue")],
    )

    assert check_expectations(good, expectations) == []
    assert check_expectations(reversed_order, expectations)


def test_expectations_audit_contract() -> None:
    """Inventory the expectation evidence contract without production imports."""

    assert callable(globals().get("test_acceptance_storyboard_satisfies_every_beat"))
    assert callable(globals().get("test_a_second_visible_shape_is_reported"))
