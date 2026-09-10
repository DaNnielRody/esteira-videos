"""Schema contract for deterministic Text, Tex, and MathTex expectations."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

try:
    from video_pipeline.expectations import TextExpectation
except ImportError:
    TextExpectation = None  # type: ignore[assignment,misc]


def _require_contract() -> type:
    if TextExpectation is None:
        pytest.fail("DETERMINISTIC_TEXT_EXPECTATION_MISSING")
    return TextExpectation


def test_plain_text_requires_an_explicit_font() -> None:
    expectation_type = _require_contract()

    with pytest.raises(ValidationError, match="font"):
        expectation_type(
            renderer="text",
            content="Gradiente descendente",
            font_size=42,
            color="white",
            x=0.0,
            y=-2.5,
        )


def test_tex_rejects_a_pango_font_and_keeps_fixed_typography() -> None:
    expectation_type = _require_contract()

    with pytest.raises(ValidationError, match="font"):
        expectation_type(
            renderer="tex",
            content=r"Transformação linear",
            font="DejaVu Sans",
            font_size=42,
            color="yellow",
            x=0.0,
            y=0.0,
        )

    expectation = expectation_type(
        renderer="tex",
        content=r"Transformação linear",
        font_size=42,
        color="yellow",
        x=0.0,
        y=0.0,
    )
    assert expectation.renderer == "tex"
    assert expectation.content == r"Transformação linear"


def test_multiline_plain_text_is_a_fixed_supported_value() -> None:
    expectation_type = _require_contract()

    expectation = expectation_type(
        renderer="text",
        content="Camada de entrada\nCamada de saída",
        font="DejaVu Sans",
        font_size=36,
        color="white",
        x=0.0,
        y=0.0,
    )

    assert expectation.content.splitlines() == ["Camada de entrada", "Camada de saída"]
