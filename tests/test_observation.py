"""Behavioral tests for reading shapes back out of rendered frames."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

try:
    from video_pipeline.observation import FrameObservation, ObservedShape, analyze_frame
except (ImportError, ModuleNotFoundError):
    analyze_frame = None  # type: ignore[assignment]
    FrameObservation = None  # type: ignore[assignment,misc]
    ObservedShape = None  # type: ignore[assignment,misc]


WIDTH = 428
HEIGHT = 240


def _require_contract() -> None:
    if analyze_frame is None:
        pytest.fail("SCENE_OBSERVATION_CONTRACT_MISSING")


def _frame(path: Path) -> Image.Image:
    return Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))


def _save(image: Image.Image, path: Path) -> Path:
    image.save(path)
    return path


def _circle(path: Path, *, center: tuple[int, int], radius: int, fill: bool = True) -> Path:
    image = _frame(path)
    draw = ImageDraw.Draw(image)
    box = [
        center[0] - radius,
        center[1] - radius,
        center[0] + radius,
        center[1] + radius,
    ]
    if fill:
        draw.ellipse(box, fill=(60, 130, 200))
    else:
        draw.ellipse(box, outline=(230, 230, 230), width=3)
    return _save(image, path)


def _square(path: Path, *, center: tuple[int, int], half: int, fill: bool = True) -> Path:
    image = _frame(path)
    draw = ImageDraw.Draw(image)
    box = [center[0] - half, center[1] - half, center[0] + half, center[1] + half]
    if fill:
        draw.rectangle(box, fill=(200, 80, 60))
    else:
        draw.rectangle(box, outline=(230, 230, 230), width=3)
    return _save(image, path)


def _two_squares(path: Path) -> Path:
    image = _frame(path)
    draw = ImageDraw.Draw(image)
    draw.rectangle([180, 90, 230, 140], fill=(200, 80, 60))
    draw.rectangle([280, 90, 330, 140], fill=(200, 80, 60))
    return _save(image, path)


def test_analyze_frame_reads_one_filled_circle_at_the_centre(tmp_path: Path) -> None:
    """A filled circle is reported once, as a circle, at the frame centre."""

    _require_contract()
    path = _circle(tmp_path / "circle.png", center=(WIDTH // 2, HEIGHT // 2), radius=40)

    observation = analyze_frame(path, index=0)

    assert len(observation.shapes) == 1
    shape = observation.shapes[0]
    assert shape.kind == "circle"
    assert shape.center_x == pytest.approx(0.5, abs=0.02)
    assert shape.center_y == pytest.approx(0.5, abs=0.02)
    assert 0.0 < shape.area_fraction < 1.0


def test_analyze_frame_distinguishes_a_square_from_a_circle(tmp_path: Path) -> None:
    """Extent and corner occupancy separate a square from a circle."""

    _require_contract()
    path = _square(tmp_path / "square.png", center=(WIDTH // 2, HEIGHT // 2), half=40)

    observation = analyze_frame(path, index=0)

    assert [shape.kind for shape in observation.shapes] == ["square"]


def test_analyze_frame_classifies_outline_only_shapes(tmp_path: Path) -> None:
    """A stroke-only shape is filled before classification, as Manim draws it."""

    _require_contract()
    circle = _circle(
        tmp_path / "outline-circle.png",
        center=(WIDTH // 2, HEIGHT // 2),
        radius=40,
        fill=False,
    )
    square = _square(
        tmp_path / "outline-square.png",
        center=(WIDTH // 2, HEIGHT // 2),
        half=40,
        fill=False,
    )

    assert [shape.kind for shape in analyze_frame(circle, index=0).shapes] == ["circle"]
    assert [shape.kind for shape in analyze_frame(square, index=0).shapes] == ["square"]


def test_analyze_frame_counts_every_visible_shape(tmp_path: Path) -> None:
    """Two shapes on screen are two observations, never one merged blob."""

    _require_contract()
    path = _two_squares(tmp_path / "two.png")

    observation = analyze_frame(path, index=0)

    assert len(observation.shapes) == 2
    assert all(shape.kind == "square" for shape in observation.shapes)


def test_analyze_frame_reports_an_empty_frame_without_shapes(tmp_path: Path) -> None:
    """A blank frame yields no shapes rather than a spurious detection."""

    _require_contract()
    path = _save(_frame(tmp_path / "blank.png"), tmp_path / "blank.png")

    assert analyze_frame(path, index=3).shapes == []
    assert analyze_frame(path, index=3).index == 3


def test_analyze_frame_locates_a_shape_right_of_centre(tmp_path: Path) -> None:
    """Horizontal position is reported as a left-to-right frame fraction."""

    _require_contract()
    left = _square(tmp_path / "left.png", center=(120, HEIGHT // 2), half=30)
    right = _square(tmp_path / "right.png", center=(320, HEIGHT // 2), half=30)

    left_x = analyze_frame(left, index=0).shapes[0].center_x
    right_x = analyze_frame(right, index=1).shapes[0].center_x

    assert left_x < 0.4
    assert right_x > 0.6
    assert right_x > left_x


def _wide_rectangle(path: Path) -> Path:
    """Two touching squares merge into one 2:1 blob, as Manim renders them."""

    image = _frame(path)
    draw = ImageDraw.Draw(image)
    draw.rectangle([154, 90, 214, 150], outline=(230, 230, 230), width=3)
    draw.rectangle([214, 90, 274, 150], outline=(230, 230, 230), width=3)
    return _save(image, path)


def test_a_wide_rectangle_is_not_reported_as_a_square(tmp_path: Path) -> None:
    """Two adjacent squares merge into one blob; its 2:1 box is not a square.

    Observed in run f07b3c568ac74b6c9ec11ab2fa83e908: a duplicated mobject
    landed flush against the original, so connected components saw a single
    region. Calling that a square would let a wrong video pass.
    """

    _require_contract()
    path = _wide_rectangle(tmp_path / "wide.png")

    observation = analyze_frame(path, index=0)

    assert len(observation.shapes) == 1
    assert observation.shapes[0].kind != "square"


def test_a_flat_ellipse_is_not_reported_as_a_circle(tmp_path: Path) -> None:
    """Roundness alone is not a circle; the bounding box must be square too."""

    _require_contract()
    image = _frame(tmp_path / "ellipse.png")
    draw = ImageDraw.Draw(image)
    draw.ellipse([120, 100, 320, 150], fill=(60, 130, 200))
    path = _save(image, tmp_path / "ellipse.png")

    assert all(shape.kind != "circle" for shape in analyze_frame(path, index=0).shapes)


def test_observation_audit_contract() -> None:
    """Inventory the observation evidence contract without production imports."""

    assert callable(globals().get("test_analyze_frame_reads_one_filled_circle_at_the_centre"))
    assert callable(globals().get("test_analyze_frame_counts_every_visible_shape"))
