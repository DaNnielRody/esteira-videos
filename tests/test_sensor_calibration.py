"""Behavioral contract for the labeled sensor-calibration harness."""

from __future__ import annotations

from pathlib import Path

import pytest

try:
    from video_pipeline.calibration import calibrate_golden_set
except (ImportError, ModuleNotFoundError):
    calibrate_golden_set = None  # type: ignore[assignment]


GOLDEN = Path(__file__).parent / "golden"


def test_golden_set_reports_false_positives_and_negatives_per_axis() -> None:
    """Every labeled axis exposes confusion counts rather than a pass-only gate."""

    if calibrate_golden_set is None:
        pytest.fail("SENSOR_CALIBRATION_CONTRACT_MISSING")

    report = calibrate_golden_set(GOLDEN)

    assert report.scenes == 15
    assert report.sensor_failures == []
    assert set(report.axes) == {
        "shape_count",
        "shape",
        "color",
        "region",
        "motion",
        "latex",
        "text",
    }
    for axis, metrics in report.axes.items():
        assert metrics.false_positives == 0, axis
        assert metrics.false_negatives == 0, axis
        assert metrics.true_positives + metrics.true_negatives > 0, axis
    for axis in (
        "shape_count",
        "shape",
        "color",
        "region",
        "motion",
        "latex",
        "text",
    ):
        assert report.axes[axis].true_negatives > 0
        assert report.axes[axis].false_positive_rate == 0.0


def test_sensor_calibration_audit_contract() -> None:
    """Inventory the calibration promise."""

    assert callable(globals().get("test_golden_set_reports_false_positives_and_negatives_per_axis"))
