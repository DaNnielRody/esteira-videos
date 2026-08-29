"""Real-render contract for every prompt-ready reference example."""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

from video_pipeline.reference_catalog import REFERENCE_EXAMPLES, ReferenceExample
from video_pipeline.rendering import ManimRunner
from video_pipeline.validation import RenderValidator


@pytest.mark.integration
@pytest.mark.parametrize(
    "example",
    REFERENCE_EXAMPLES,
    ids=[example.identifier for example in REFERENCE_EXAMPLES],
)
def test_every_qwen_reference_renders_with_manim_community(
    example: ReferenceExample,
    tmp_path: Path,
) -> None:
    """A reference is usable only when Community renders a probeable MP4."""

    if importlib.util.find_spec("manim") is None:
        pytest.skip("Manim Community is not installed in the repository environment")
    if shutil.which("ffprobe") is None:
        pytest.skip("ffprobe is not installed in the repository environment")

    source = tmp_path / f"{example.identifier}.py"
    source.write_text(example.code, encoding="utf-8")
    result = ManimRunner(timeout=120).run(source, tmp_path / "media")

    assert result.exit_code == 0, result.stderr
    assert result.mp4_paths, result.stdout
    validation = RenderValidator().validate(result.mp4_paths[0])
    assert validation.valid, validation.reasons


def test_reference_corpus_audit_contract() -> None:
    """Inventory the real corpus-render evidence."""

    assert len(REFERENCE_EXAMPLES) >= 8
    assert callable(globals().get("test_every_qwen_reference_renders_with_manim_community"))
