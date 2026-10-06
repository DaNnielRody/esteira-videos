"""Boundary tests for explicit LaTeX sensor failures."""

from __future__ import annotations

from pathlib import Path

from video_pipeline.expectations import LatexExpectation
from video_pipeline.latex_validation import LatexValidator


def test_corrupt_candidate_png_is_sensor_failure_not_semantic_rejection(
    tmp_path: Path,
) -> None:
    """Unreadable evidence must never produce an IoU-based verdict."""

    frames = tmp_path / "frames"
    frames.mkdir()
    (frames / "frame-001.png").write_bytes(b"NOT_A_PNG")
    expectation = LatexExpectation(
        tex="x^2",
        font_size=48,
        color="yellow",
        x=0.0,
        y=0.0,
    )

    result = LatexValidator(timeout=120).observe([expectation], frames, tmp_path / "evidence")

    assert result.evidence is None
    assert result.failure is not None
    assert result.failure.code == "frame_decode_failed"
    assert "frame-001.png" in result.failure.detail
