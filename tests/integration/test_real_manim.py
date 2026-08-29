"""Real-Manim evidence for the deterministic acceptance scene.

This is the one test that crosses the repository-local Manim and ffprobe
boundaries.  It never contacts Ollama: candidate Python comes from the fixed
provider below.
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

from video_pipeline.provider import ProviderRequest, ProviderResponse, UnloadResult
from video_pipeline.rendering import ManimRunner
from video_pipeline.spec import SceneSpec
from video_pipeline.validation import RenderValidator

try:
    from video_pipeline.pipeline import RenderPipeline as _RenderPipeline
except (ImportError, ModuleNotFoundError):
    _RenderPipeline = None


ACCEPTANCE_DESCRIPTION = (
    "Mostre um círculo no centro. Depois transforme-o em um quadrado e mova-o para a direita."
)


def _require_pipeline() -> type[object]:
    if _RenderPipeline is None:
        pytest.fail("RITL_CLI_CONTRACT_MISSING")
    return _RenderPipeline  # type: ignore[return-value]


class DeterministicAcceptanceProvider:
    """Provider fixture for Manim Community 0.21.0 acceptance rendering."""

    code = """\
from manim import Circle, Create, ORIGIN, RIGHT, Scene, Square, Transform


class AcceptanceScene(Scene):
    def construct(self):
        circle = Circle().move_to(ORIGIN)
        self.play(Create(circle))
        square = Square().move_to(ORIGIN)
        self.play(Transform(circle, square))
        self.play(circle.animate.shift(RIGHT))
"""

    def __init__(self) -> None:
        self.generate_calls = 0
        self.unload_calls = 0

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.generate_calls += 1
        assert request.scene_name == "AcceptanceScene"
        assert request.description == ACCEPTANCE_DESCRIPTION
        return ProviderResponse(
            code=self.code,
            raw_response={"response": "DETERMINISTIC_ACCEPTANCE_PROVIDER"},
        )

    def unload(self) -> UnloadResult:
        self.unload_calls += 1
        return UnloadResult(
            ok=True,
            raw_response={"response": "DETERMINISTIC_ACCEPTANCE_UNLOAD"},
        )


@pytest.mark.integration
def test_real_manim_renders_and_independently_validates_acceptance_scene(
    tmp_path: Path,
) -> None:
    """Manim Community produces a positive, independently probed acceptance MP4."""

    if importlib.util.find_spec("manim") is None:
        pytest.skip("Manim Community is not installed in the repository environment")
    if shutil.which("ffprobe") is None:
        pytest.skip("ffprobe is not installed in the repository environment")

    pipeline_type = _require_pipeline()
    provider = DeterministicAcceptanceProvider()
    output_root = tmp_path / "runs"
    pipeline = pipeline_type(  # type: ignore[operator]
        provider=provider,
        runner=ManimRunner(timeout=120),
        validator=RenderValidator(),
        output_root=output_root,
        id_factory=lambda: "real-manim-run",
    )
    spec = SceneSpec(
        schema_version="1.0",
        scene_name="AcceptanceScene",
        description=ACCEPTANCE_DESCRIPTION,
    )

    result = pipeline.render(spec, max_attempts=1)  # type: ignore[attr-defined]

    assert str(getattr(getattr(result, "state"), "value", getattr(result, "state"))).upper() == (
        "SUCCESS"
    )
    assert provider.generate_calls == 1
    assert provider.unload_calls == 1
    mp4_paths = sorted(output_root.rglob("*.mp4"))
    assert mp4_paths
    independent = RenderValidator().validate(mp4_paths[0])
    assert independent.valid is True
    assert independent.width is not None and independent.width > 0
    assert independent.height is not None and independent.height > 0
    assert independent.duration_seconds is not None and independent.duration_seconds > 0
    assert independent.size_bytes is not None and independent.size_bytes > 0


def test_real_manim_audit_contract() -> None:
    """Inventory the real-Manim evidence contract without production imports."""

    assert callable(
        globals().get("test_real_manim_renders_and_independently_validates_acceptance_scene")
    )
