"""Real-render evidence for deterministic LaTeX semantic validation."""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from video_pipeline.expectations import LatexExpectation, SceneExpectations
from video_pipeline.pipeline import RenderPipeline
from video_pipeline.provider import ProviderRequest, ProviderResponse, UnloadResult
from video_pipeline.rendering import ManimRunner
from video_pipeline.spec import SceneSpec
from video_pipeline.validation import RenderValidator


class LatexProvider:
    """Return one deterministic MathTex scene without contacting Ollama."""

    def __init__(
        self,
        tex: str,
        *,
        color: str = "YELLOW",
        with_background: bool = False,
    ) -> None:
        self.tex = tex
        self.color = color
        self.with_background = with_background

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        background = (
            "self.add(NumberPlane().set_color(BLUE).set_opacity(0.45))"
            if self.with_background
            else ""
        )
        code = f'''from manim import *

class LatexValidationScene(Scene):
    def construct(self):
        {background}
        formula = MathTex(r"{self.tex}", font_size=48, color={self.color})
        formula.move_to([0.0, 0.0, 0.0])
        self.play(Write(formula))
        self.wait(0.5)
'''
        return ProviderResponse(code=code, raw_response={"response": code})

    def unload(self) -> UnloadResult:
        return UnloadResult(ok=True, raw_response={"response": "UNLOADED"})


def _spec() -> SceneSpec:
    return SceneSpec(
        schema_version="1.0",
        scene_name="LatexValidationScene",
        description="Mostre a transformação linear indicada.",
        topics=["linear_algebra"],
        reference_examples=0,
        expect=SceneExpectations(
            latex=[
                LatexExpectation(
                    tex=r"\mathbf{x}\mapsto A\mathbf{x}",
                    font_size=48,
                    color="yellow",
                    x=0.0,
                    y=0.0,
                    min_iou=0.95,
                )
            ],
        ),
    )


def _require_runtime() -> None:
    if importlib.util.find_spec("manim") is None:
        pytest.skip("Manim Community is not installed in the repository environment")
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("FFmpeg is not installed in the repository environment")


@pytest.mark.integration
def test_wrong_latex_is_rejected_from_the_rendered_frames(tmp_path: Path) -> None:
    """Matching layout cannot make a different formula semantically valid."""

    _require_runtime()
    pipeline = RenderPipeline(
        provider=LatexProvider(r"\mathbf{x}\mapsto B\mathbf{x}"),
        runner=ManimRunner(timeout=120),
        validator=RenderValidator(),
        output_root=tmp_path / "runs",
        id_factory=lambda: "wrong-latex-run",
    )

    result = pipeline.render(_spec(), max_attempts=1)

    assert result.state.value == "attempts_exhausted"
    validation = json.loads(
        (result.run_path / "attempt-01" / "validation.json").read_text(encoding="utf-8")
    )
    assert any("latex" in reason.lower() for reason in validation["reasons"])


@pytest.mark.integration
def test_exact_latex_is_accepted_and_records_mask_iou(tmp_path: Path) -> None:
    """The fixed expression, typography, and position pass the same visual gate."""

    _require_runtime()
    pipeline = RenderPipeline(
        provider=LatexProvider(r"\mathbf{x}\mapsto A\mathbf{x}"),
        runner=ManimRunner(timeout=120),
        validator=RenderValidator(),
        output_root=tmp_path / "runs",
        id_factory=lambda: "exact-latex-run",
    )

    result = pipeline.render(_spec(), max_attempts=1)

    assert result.state.value == "success"
    evidence = json.loads(
        (result.run_path / "attempt-01" / "latex-validation.json").read_text(
            encoding="utf-8"
        )
    )
    assert evidence["failure"] is None
    assert evidence["matches"][0]["best_iou"] >= 0.95


@pytest.mark.integration
def test_latex_with_wrong_fixed_color_is_rejected(tmp_path: Path) -> None:
    """Matching glyph masks do not override the color fixed by the spec."""

    _require_runtime()
    pipeline = RenderPipeline(
        provider=LatexProvider(r"\mathbf{x}\mapsto A\mathbf{x}", color="BLUE"),
        runner=ManimRunner(timeout=120),
        validator=RenderValidator(),
        output_root=tmp_path / "runs",
        id_factory=lambda: "wrong-latex-color-run",
    )

    result = pipeline.render(_spec(), max_attempts=1)

    assert result.state.value == "attempts_exhausted"
    validation = json.loads(
        (result.run_path / "attempt-01" / "validation.json").read_text(encoding="utf-8")
    )
    assert any("color" in reason.lower() for reason in validation["reasons"])


@pytest.mark.integration
def test_exact_latex_is_accepted_over_a_different_colored_visual(tmp_path: Path) -> None:
    """Unrelated visual pixels must not create a false negative for fixed LaTeX."""

    _require_runtime()
    pipeline = RenderPipeline(
        provider=LatexProvider(
            r"\mathbf{x}\mapsto A\mathbf{x}",
            with_background=True,
        ),
        runner=ManimRunner(timeout=120),
        validator=RenderValidator(),
        output_root=tmp_path / "runs",
        id_factory=lambda: "composite-latex-run",
    )

    result = pipeline.render(_spec(), max_attempts=1)

    assert result.state.value == "success"
