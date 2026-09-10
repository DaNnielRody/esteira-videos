"""Real-render acceptance evidence for fixed Text and Tex content."""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

try:
    from video_pipeline.expectations import SceneExpectations, TextExpectation
except ImportError:
    TextExpectation = None  # type: ignore[assignment,misc]
    from video_pipeline.expectations import SceneExpectations
from video_pipeline.pipeline import RenderPipeline
from video_pipeline.provider import ProviderRequest, ProviderResponse, UnloadResult
from video_pipeline.rendering import ManimRunner
from video_pipeline.spec import SceneSpec
from video_pipeline.validation import RenderValidator


class RenderedTextProvider:
    """Return one deterministic Text or Tex scene without contacting Ollama."""

    def __init__(self, constructor: str, content: str, *, x: float = 0.0) -> None:
        self.constructor = constructor
        self.content = content
        self.x = x

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        if self.constructor == "Text":
            expression = f"Text({self.content!r}, font='DejaVu Sans', font_size=42, color=YELLOW)"
        else:
            expression = f"Tex({self.content!r}, font_size=42, color=YELLOW)"
        code = f"""from manim import *

class RenderedTextScene(Scene):
    def construct(self):
        label = {expression}
        label.move_to([{self.x!r}, 0.0, 0.0])
        self.add(label)
        self.wait(0.5)
"""
        return ProviderResponse(code=code, raw_response={"response": code})

    def unload(self) -> UnloadResult:
        return UnloadResult(ok=True, raw_response={"response": "UNLOADED"})


def _require_runtime() -> None:
    if TextExpectation is None:
        pytest.fail("DETERMINISTIC_TEXT_EXPECTATION_MISSING")
    if importlib.util.find_spec("manim") is None:
        pytest.skip("Manim Community is unavailable")
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("FFmpeg is unavailable")


def _spec(renderer: str, content: str) -> SceneSpec:
    assert TextExpectation is not None
    font = "DejaVu Sans" if renderer == "text" else None
    return SceneSpec(
        id="rendered-text",
        scene_name="RenderedTextScene",
        description="Exiba o texto fixado na especificação.",
        reference_examples=0,
        expect=SceneExpectations(
            max_shapes=100,
            text=[
                TextExpectation(
                    renderer=renderer,
                    content=content,
                    font=font,
                    font_size=42,
                    color="yellow",
                    x=0.0,
                    y=0.0,
                    min_iou=0.95,
                )
            ],
        ),
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("renderer", "constructor", "content"),
    [
        ("text", "Text", "Gradiente\ndescendente"),
        ("tex", "Tex", r"Transformação linear"),
    ],
)
def test_exact_fixed_text_is_accepted(
    tmp_path: Path,
    renderer: str,
    constructor: str,
    content: str,
) -> None:
    _require_runtime()
    pipeline = RenderPipeline(
        provider=RenderedTextProvider(constructor, content),
        runner=ManimRunner(timeout=120),
        validator=RenderValidator(),
        output_root=tmp_path / "runs",
        id_factory=lambda: f"exact-{renderer}-run",
    )

    result = pipeline.render(_spec(renderer, content), max_attempts=1)

    assert result.state.value == "success"


@pytest.mark.integration
def test_wrong_or_displaced_fixed_text_is_rejected(tmp_path: Path) -> None:
    _require_runtime()
    pipeline = RenderPipeline(
        provider=RenderedTextProvider("Text", "Texto incorreto", x=1.5),
        runner=ManimRunner(timeout=120),
        validator=RenderValidator(),
        output_root=tmp_path / "runs",
        id_factory=lambda: "wrong-text-run",
    )

    result = pipeline.render(_spec("text", "Texto correto"), max_attempts=1)

    assert result.state.value == "attempts_exhausted"
    validation = json.loads(
        (result.run_path / "attempt-01" / "validation.json").read_text(encoding="utf-8")
    )
    assert any("fixed typography" in reason for reason in validation["reasons"])
