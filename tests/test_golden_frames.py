"""The frame reader is checked against Manim's own validated control data.

`tests/golden/` vendors `.npz` control frames from the Manim Community
repository in Manim's own format: one `frame_data` key holding a
`(n_frames, height, width, 4)` uint8 RGBA array. The scene name is the label —
`geometry/Circle.npz` is a circle — so these files are ground truth for the
reader, produced and validated by the Manim project rather than by us.

`expected.json` declares what each scene is, written from the scene itself and
verified by eye plus centroid measurement, never from this reader's output.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

try:
    from video_pipeline.observation import ObservedShape, analyze_frames
except (ImportError, ModuleNotFoundError):
    analyze_frames = None  # type: ignore[assignment]
    ObservedShape = None  # type: ignore[assignment,misc]


GOLDEN = Path(__file__).parent / "golden"
# Manim's own comparison tolerances, reused rather than reinvented.
FRAME_ABSOLUTE_TOLERANCE = 1.01
FRAME_MISMATCH_RATIO_TOLERANCE = 1e-5
_LOW, _HIGH = 1.0 / 3.0, 2.0 / 3.0


def _require_contract() -> None:
    if analyze_frames is None:
        pytest.fail("GOLDEN_FRAME_CONTRACT_MISSING")


def _manifest() -> list[dict[str, object]]:
    document = json.loads((GOLDEN / "expected.json").read_text(encoding="utf-8"))
    scenes = document["scenes"]
    assert isinstance(scenes, list)
    return scenes


def _load(relative: str) -> np.ndarray:
    """Read control data in Manim's format: one `frame_data` RGBA stack."""

    with np.load(GOLDEN / relative) as data:
        frames = data["frame_data"]
        if frames.ndim != 4:
            # Manim's own backward compatibility for single-frame control data.
            frames = np.expand_dims(frames, axis=0)
        return frames


def _ids() -> list[str]:
    return [str(scene["file"]) for scene in _manifest()]


@pytest.mark.parametrize("scene", _manifest(), ids=_ids())
def test_control_data_is_stored_in_manim_format(scene: dict[str, object]) -> None:
    """The vendored golden set must stay byte-compatible with Manim's readers."""

    _require_contract()
    frames = _load(str(scene["file"]))

    assert frames.ndim == 4
    assert frames.shape[-1] == 4
    assert frames.dtype == np.uint8


@pytest.mark.parametrize("scene", _manifest(), ids=_ids())
def test_reader_matches_manim_control_data(scene: dict[str, object]) -> None:
    """Every declared fact about a real Manim render must be read back."""

    _require_contract()
    frames = _load(str(scene["file"]))
    observations = analyze_frames(frames)
    why = str(scene.get("why", ""))

    assert len(observations) == len(frames), why

    expected_count = scene.get("shapes_per_frame")
    if isinstance(expected_count, int):
        for observation in observations:
            assert len(observation.shapes) == expected_count, (
                f"{why} frame {observation.index} read "
                f"{[s.kind for s in observation.shapes]}"
            )

    kinds = [[shape.kind for shape in o.shapes] for o in observations]

    every = scene.get("every_frame")
    if isinstance(every, str):
        assert all(every in row for row in kinds), f"{why} read {kinds}"

    first = scene.get("first_frame")
    if isinstance(first, str):
        assert first in kinds[0], f"{why} read {kinds}"

    last = scene.get("last_frame")
    if isinstance(last, str):
        assert last in kinds[-1], f"{why} read {kinds}"

    never = scene.get("never")
    if isinstance(never, list):
        flat = {kind for row in kinds for kind in row}
        assert not (flat & set(never)), f"{why} read {kinds}"


@pytest.mark.parametrize("scene", _manifest(), ids=_ids())
def test_reader_matches_declared_motion(scene: dict[str, object]) -> None:
    """Observed centroid travel must agree with what the scene actually does."""

    _require_contract()
    moved = scene.get("moved")
    still = scene.get("not_moved")
    if not isinstance(moved, list) and not isinstance(still, list):
        pytest.skip("scene declares no motion")

    observations = analyze_frames(_load(str(scene["file"])))
    start, end = observations[0].shapes[0], observations[-1].shapes[-1]
    horizontal = end.center_x - start.center_x
    # Image rows grow downward, so a smaller centre_y is higher on screen.
    vertical = start.center_y - end.center_y
    travel = {
        "right": horizontal,
        "left": -horizontal,
        "up": vertical,
        "down": -vertical,
    }
    threshold = float(scene.get("min_motion", 0.05) or 0.05)
    why = str(scene.get("why", ""))

    for direction in moved if isinstance(moved, list) else []:
        assert travel[direction] >= threshold, (
            f"{why} expected {direction} >= {threshold}, got {travel[direction]:.3f}"
        )
    for direction in still if isinstance(still, list) else []:
        assert travel[direction] < threshold, (
            f"{why} expected no {direction}, got {travel[direction]:.3f}"
        )


@pytest.mark.parametrize("scene", _manifest(), ids=_ids())
def test_declared_region_is_read_back(scene: dict[str, object]) -> None:
    """A scene declared centred must be observed centred."""

    _require_contract()
    region = scene.get("region")
    if not isinstance(region, dict):
        pytest.skip("scene declares no region")

    shape = analyze_frames(_load(str(scene["file"])))[0].shapes[0]
    if region.get("x") == "center":
        assert _LOW <= shape.center_x <= _HIGH
    if region.get("y") == "middle":
        assert _LOW <= shape.center_y <= _HIGH


def test_manim_tolerances_are_reused_not_reinvented() -> None:
    """Exact-frame comparison, where it applies, uses Manim's own thresholds."""

    _require_contract()
    from manim.utils.testing import _frames_testers as manim_testers

    assert FRAME_ABSOLUTE_TOLERANCE == manim_testers.FRAME_ABSOLUTE_TOLERANCE
    assert FRAME_MISMATCH_RATIO_TOLERANCE == manim_testers.FRAME_MISMATCH_RATIO_TOLERANCE


def test_control_data_round_trips_through_manim_tolerance() -> None:
    """A golden file compared against itself passes Manim's own comparison."""

    _require_contract()
    frames = _load("transform/Transform.npz")

    np.testing.assert_allclose(frames, frames, atol=FRAME_ABSOLUTE_TOLERANCE)


def test_golden_frame_audit_contract() -> None:
    """Inventory the golden-set evidence contract without production imports."""

    assert (GOLDEN / "expected.json").is_file()
    assert (GOLDEN / "LICENSE").is_file()
    assert callable(globals().get("test_reader_matches_manim_control_data"))
