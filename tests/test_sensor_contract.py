"""Public contract for sensors that never masquerade failure as evidence."""

from __future__ import annotations

from pathlib import Path

import pytest

from video_pipeline.expectations import LatexExpectation
from video_pipeline.latex_validation import LatexMatch, LatexValidator
from video_pipeline.observation import (
    ObservationResult,
    SceneObserver,
    SensorFailure,
    SensorFailureCode,
)

try:
    from video_pipeline.latex_validation import check_latex_matches
    from video_pipeline.sensors import SensorResult
except (ImportError, ModuleNotFoundError):
    check_latex_matches = None  # type: ignore[assignment]
    SensorResult = None  # type: ignore[assignment,misc]


def _require_contract() -> type[object]:
    if SensorResult is None or check_latex_matches is None:
        pytest.fail("UNIFORM_SENSOR_CONTRACT_MISSING")
    return SensorResult


def _expectation() -> LatexExpectation:
    return LatexExpectation(
        tex=r"A\mathbf{x}",
        font_size=48,
        color="yellow",
        x=0.0,
        y=0.0,
    )


def test_sensor_result_contains_evidence_or_failure_never_both() -> None:
    result_type = _require_contract()
    failure = SensorFailure(SensorFailureCode.FRAME_DECODE_FAILED, "broken frame")

    success = result_type.success(["frame-001"])
    failed = result_type.failed(failure)

    assert success.evidence == ["frame-001"]
    assert success.failure is None
    assert failed.evidence is None
    assert failed.failure is failure
    frame_success = ObservationResult.success([])
    assert frame_success.frames == []
    assert frame_success.failure is None
    frame_failure = ObservationResult.failed(failure)
    assert frame_failure.failure is failure
    with pytest.raises(ValueError, match="exactly one"):
        result_type(evidence=None, failure=None)
    with pytest.raises(ValueError, match="exactly one"):
        result_type(evidence=["frame-001"], failure=failure)


def test_frame_and_latex_sensors_share_the_sensor_result_contract(tmp_path: Path) -> None:
    result_type = _require_contract()
    frame_result = SceneObserver().observe(tmp_path / "missing.mp4", tmp_path / "frames")
    candidate_frames = tmp_path / "candidate-frames"
    candidate_frames.mkdir()
    (candidate_frames / "frame-001.png").write_bytes(b"NOT_A_PNG")

    latex_result = LatexValidator(timeout=120).observe(
        [_expectation()], candidate_frames, tmp_path / "latex-evidence"
    )

    assert isinstance(frame_result, result_type)
    assert isinstance(latex_result, result_type)
    assert frame_result.failure is not None
    assert latex_result.failure is not None


def test_latex_judge_is_purely_derived_from_sensor_evidence() -> None:
    _require_contract()
    assert check_latex_matches is not None
    expectation = _expectation()
    rejected = LatexMatch(
        tex=expectation.tex,
        best_iou=0.74,
        color_similarity=1.0,
        min_iou=expectation.min_iou,
        matched_frame="frame-001.png",
    )
    accepted = LatexMatch(
        tex=expectation.tex,
        best_iou=0.99,
        color_similarity=1.0,
        min_iou=expectation.min_iou,
        matched_frame="frame-001.png",
    )

    assert any("fixed typography" in reason for reason in check_latex_matches([rejected]))
    assert check_latex_matches([accepted]) == []
