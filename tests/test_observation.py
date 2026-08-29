"""Behavioral tests for reading shapes back out of rendered frames.

Frames are handled in Manim's control-data shape throughout: a
`(n_frames, height, width, 4)` uint8 RGBA array. Synthetic frames here cover
the geometry the vendored golden set does not contain — chiefly rotation, which
`tests/test_golden_frames.py` has no Manim control data for.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from PIL import Image, ImageDraw

try:
    from video_pipeline.observation import (
        FrameObservation,
        ObservedShape,
        analyze_frames,
    )
except (ImportError, ModuleNotFoundError):
    analyze_frames = None  # type: ignore[assignment]
    FrameObservation = None  # type: ignore[assignment,misc]
    ObservedShape = None  # type: ignore[assignment,misc]


WIDTH = 428
HEIGHT = 240
STROKE = 4  # close to Manim's default stroke width


def _require_contract() -> None:
    if analyze_frames is None:
        pytest.fail("SCENE_OBSERVATION_CONTRACT_MISSING")


def _blank() -> Image.Image:
    return Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 255))


def _stack(*images: Image.Image) -> np.ndarray:
    """Pack frames the way Manim stores control data."""

    return np.stack([np.asarray(image.convert("RGBA")) for image in images])


def _one(image: Image.Image) -> list[ObservedShape]:
    return analyze_frames(_stack(image))[0].shapes


def _square_points(
    degrees: float,
    *,
    center: tuple[int, int] = (WIDTH // 2, HEIGHT // 2),
    radius: int = 55,
) -> list[tuple[float, float]]:
    """Corners of a square rotated by ``degrees`` about its centre."""

    angle = math.radians(degrees) + math.pi / 4
    points = [
        (
            center[0] + radius * math.cos(angle + math.pi / 2 * k),
            center[1] + radius * math.sin(angle + math.pi / 2 * k),
        )
        for k in range(4)
    ]
    return [*points, points[0]]


def _outline(points: list[tuple[float, float]], *, width: int = STROKE) -> Image.Image:
    image = _blank()
    ImageDraw.Draw(image).line(points, fill=(230, 230, 230, 255), width=width)
    return image


def _circle(
    *,
    center: tuple[int, int] = (WIDTH // 2, HEIGHT // 2),
    radius: int = 45,
    fill: bool = True,
) -> Image.Image:
    image = _blank()
    draw = ImageDraw.Draw(image)
    box = [
        center[0] - radius,
        center[1] - radius,
        center[0] + radius,
        center[1] + radius,
    ]
    if fill:
        draw.ellipse(box, fill=(60, 130, 200, 255))
    else:
        draw.ellipse(box, outline=(230, 230, 230, 255), width=STROKE)
    return image


def test_a_filled_circle_is_read_as_one_centred_circle() -> None:
    """A filled circle is reported once, as a circle, at the frame centre."""

    _require_contract()
    shapes = _one(_circle())

    assert len(shapes) == 1
    assert shapes[0].kind == "circle"
    assert shapes[0].center_x == pytest.approx(0.5, abs=0.02)
    assert shapes[0].center_y == pytest.approx(0.5, abs=0.02)
    assert 0.0 < shapes[0].area_fraction < 1.0


def test_an_outline_only_shape_is_measured_as_the_region_it_encloses() -> None:
    """Manim strokes outlines, so a hollow shape must read as the solid one."""

    _require_contract()

    assert [s.kind for s in _one(_circle(fill=False))] == ["circle"]
    assert [s.kind for s in _one(_outline(_square_points(0)))] == ["square"]


@pytest.mark.parametrize("degrees", [0, 10, 20, 30, 45, 60])
def test_a_square_is_a_square_at_any_rotation(degrees: float) -> None:
    """Rotation must not change what a shape is.

    The previous hand-rolled classifier measured an axis-aligned bounding box,
    so a square rotated by 10 degrees read as `polygon` and a spec asking for a
    square rejected a perfectly good render.
    """

    _require_contract()
    shapes = _one(_outline(_square_points(degrees)))

    assert [shape.kind for shape in shapes] == ["square"], f"at {degrees} degrees"


@pytest.mark.parametrize("degrees", [0, 20, 45])
def test_a_hairline_diagonal_outline_does_not_vanish(degrees: float) -> None:
    """A one-pixel diagonal stroke must still be one shape.

    Four-connected labelling shattered a 1px diagonal outline into hundreds of
    fragments, each below the noise floor, so the shape disappeared from the
    storyboard entirely rather than being misread.
    """

    _require_contract()
    shapes = _one(_outline(_square_points(degrees), width=1))

    assert len(shapes) == 1, f"at {degrees} degrees"


def test_two_separate_shapes_are_counted_separately() -> None:
    """Two shapes on screen are two observations, never one merged blob."""

    _require_contract()
    image = _blank()
    draw = ImageDraw.Draw(image)
    draw.rectangle([120, 90, 180, 150], fill=(200, 80, 60, 255))
    draw.rectangle([260, 90, 320, 150], fill=(200, 80, 60, 255))

    shapes = _one(image)

    assert len(shapes) == 2
    assert all(shape.kind == "square" for shape in shapes)


def test_two_touching_squares_are_not_read_as_one_square() -> None:
    """A duplicate landing flush against the original must not pass as a square.

    Observed in run f07b3c568ac74b6c9ec11ab2fa83e908: the merged region is a
    2:1 rectangle, and calling it a square let a wrong video through.
    """

    _require_contract()
    image = _blank()
    draw = ImageDraw.Draw(image)
    draw.rectangle([154, 90, 214, 150], outline=(230, 230, 230, 255), width=STROKE)
    draw.rectangle([214, 90, 274, 150], outline=(230, 230, 230, 255), width=STROKE)

    kinds = [shape.kind for shape in _one(image)]

    assert "square" not in kinds


def test_an_elongated_rectangle_is_not_a_square() -> None:
    """Proportion decides a square, at any rotation."""

    _require_contract()
    image = _blank()
    ImageDraw.Draw(image).rectangle(
        [100, 100, 330, 150], outline=(230, 230, 230, 255), width=STROKE
    )

    kinds = [shape.kind for shape in _one(image)]

    assert "square" not in kinds
    assert "circle" not in kinds


def test_a_flat_ellipse_is_not_a_circle() -> None:
    """Roundness alone is not a circle; the proportion must be square too."""

    _require_contract()
    image = _blank()
    ImageDraw.Draw(image).ellipse([120, 100, 320, 150], fill=(60, 130, 200, 255))

    assert all(shape.kind != "circle" for shape in _one(image))


def test_an_empty_frame_reports_no_shapes() -> None:
    """A blank frame yields no shapes rather than a spurious detection."""

    _require_contract()
    observations = analyze_frames(_stack(_blank(), _blank()))

    assert [o.shapes for o in observations] == [[], []]
    assert [o.index for o in observations] == [0, 1]


def test_horizontal_position_is_a_frame_fraction() -> None:
    """Position is reported left-to-right as a fraction of frame width."""

    _require_contract()
    left = _one(_outline(_square_points(0, center=(110, HEIGHT // 2)), width=STROKE))
    right = _one(_outline(_square_points(0, center=(320, HEIGHT // 2)), width=STROKE))

    assert left[0].center_x < 0.4
    assert right[0].center_x > 0.6


def test_a_frame_stack_is_read_in_order() -> None:
    """Every frame of a Manim-shaped stack is observed, keeping its index."""

    _require_contract()
    stack = _stack(_circle(), _blank(), _outline(_square_points(30)))

    observations = analyze_frames(stack)

    assert [o.index for o in observations] == [0, 1, 2]
    assert [shape.kind for shape in observations[0].shapes] == ["circle"]
    assert observations[1].shapes == []
    assert [shape.kind for shape in observations[2].shapes] == ["square"]


PALETTE = {
    "RED": "red",
    "MAROON": "red",
    "ORANGE": "orange",
    "GOLD": "orange",
    "YELLOW": "yellow",
    "GREEN": "green",
    "TEAL": "teal",
    "BLUE": "blue",
    "DARK_BLUE": "blue",
    "PURPLE": "purple",
    "PINK": "pink",
    "WHITE": "white",
    "GREY": "grey",
}


def _rgb(constant: str) -> tuple[int, int, int]:
    """Resolve one Manim palette constant to 8-bit RGB."""

    import manim

    channels = getattr(manim, constant).to_rgb()
    return tuple(int(round(float(c) * 255)) for c in channels)  # type: ignore[return-value]


@pytest.mark.parametrize("constant", sorted(PALETTE))
def test_every_palette_colour_is_read_back_from_an_outline(constant: str) -> None:
    """A stroked shape carries its colour in the stroke alone."""

    _require_contract()
    image = _blank()
    red, green, blue = _rgb(constant)
    ImageDraw.Draw(image).line(
        _square_points(0), fill=(red, green, blue, 255), width=STROKE
    )

    assert [shape.color for shape in _one(image)] == [PALETTE[constant]]


@pytest.mark.parametrize("constant", ["RED", "BLUE", "GREEN", "YELLOW", "PURPLE"])
def test_a_fill_decides_the_colour_over_a_white_stroke(constant: str) -> None:
    """Manim strokes a filled shape in white; the fill is what a viewer names."""

    _require_contract()
    image = _blank()
    draw = ImageDraw.Draw(image)
    box = [WIDTH // 2 - 55, HEIGHT // 2 - 55, WIDTH // 2 + 55, HEIGHT // 2 + 55]
    draw.rectangle(box, fill=(*_rgb(constant), 255), outline=(255, 255, 255, 255), width=STROKE)

    assert [shape.color for shape in _one(image)] == [PALETTE[constant]]


def test_an_antialiased_white_stroke_is_white_not_grey() -> None:
    """Antialiasing blends a stroke toward the background and drags the median.

    Measured on `tests/golden/movements/Shift.npz`: a white square reads
    median value 0.60, which names grey, while its 90th percentile is 1.00.
    The brightest drawn pixels are the stroke; the dim ones are the blend.
    """

    _require_contract()
    small = Image.new("RGBA", (214, 120), (0, 0, 0, 255))
    ImageDraw.Draw(small).line(
        [(70, 30), (150, 30), (150, 90), (70, 90), (70, 30)],
        fill=(255, 255, 255, 255),
        width=1,
    )

    assert [shape.color for shape in _one(small)] == ["white"]


def test_colour_is_read_per_shape() -> None:
    """Two shapes in one frame keep their own colours."""

    _require_contract()
    image = _blank()
    draw = ImageDraw.Draw(image)
    draw.rectangle([100, 90, 160, 150], fill=(*_rgb("BLUE"), 255))
    draw.rectangle([280, 90, 340, 150], fill=(*_rgb("RED"), 255))

    assert [shape.color for shape in _one(image)] == ["blue", "red"]


def test_observation_audit_contract() -> None:
    """Inventory the observation evidence contract without production imports."""

    assert callable(globals().get("test_a_square_is_a_square_at_any_rotation"))
    assert callable(globals().get("test_two_touching_squares_are_not_read_as_one_square"))
