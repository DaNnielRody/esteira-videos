"""Real-render contract for every prompt-ready reference example."""

from __future__ import annotations

import importlib.util
import json
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
    facts = json.loads((tmp_path / "media" / "visual-facts.json").read_text())
    assert facts["checkpoints"][-1]["instant_seconds"] == pytest.approx(
        validation.duration_seconds,
        abs=1 / 15,
    )


def test_reference_corpus_audit_contract() -> None:
    """Inventory the real corpus-render evidence."""

    assert len(REFERENCE_EXAMPLES) >= 8
    assert callable(globals().get("test_every_qwen_reference_renders_with_manim_community"))


@pytest.mark.integration
def test_runtime_clock_tracks_fractional_frame_animation_durations(tmp_path: Path) -> None:
    source = tmp_path / "fractional.py"
    source.write_text(
        "from manim import Dot, RIGHT\n"
        "from video_pipeline.runtime import VisualScene\n"
        "class FractionalScene(VisualScene):\n"
        "    def construct(self):\n"
        "        dot=Dot()\n        self.add(dot)\n"
        "        for _ in range(10):\n"
        "            self.play(dot.animate.shift(RIGHT*.1),run_time=.11)\n"
        "        self.checkpoint('final')\n"
    )
    result = ManimRunner().run(source, tmp_path / "media")
    assert result.exit_code == 0, result.stderr
    video = next(p for p in result.mp4_paths if "partial_movie_files" not in p.parts)
    duration = RenderValidator().validate(video).duration_seconds
    facts = json.loads((tmp_path / "media/visual-facts.json").read_text())
    assert facts["checkpoints"][-1]["instant_seconds"] == pytest.approx(duration, abs=1 / 15)
