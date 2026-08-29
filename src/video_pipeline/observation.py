"""Read back what a rendered MP4 actually shows, frame by frame.

Manim exits zero and ffprobe accepts the container for a scene that draws the
wrong thing, so neither can decide semantic fidelity.  This module observes the
pixels instead: it samples frames from the finished video and reports the
shapes visible in each one, which is what the specification can be checked
against.

The classifier is deliberately narrow.  It separates a circle from an
axis-aligned square and counts how many shapes are on screen; it does not
recognise text, colour, or arbitrary geometry.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
import numpy.typing as npt
from PIL import Image
from scipy import ndimage

# Manim renders on a black background; anything brighter is drawn content.
_FOREGROUND_LUMINANCE = 40
# Antialiasing leaves single-pixel specks that are not shapes.
_MIN_AREA_FRACTION = 0.0004
# A filled axis-aligned square occupies its bounding box; a circle covers pi/4.
_SQUARE_MIN_EXTENT = 0.85
_CIRCLE_EXTENT_RANGE = (0.62, _SQUARE_MIN_EXTENT)
# Corners sampled just inside the bounding box: inside a square, outside a circle.
_CORNER_INSET = 0.12
# A circle and an axis-aligned square both sit in a near-square bounding box.
# Two touching shapes merge into one wide region, which must not pass as either.
_MAX_ASPECT = 1.2


class _FfmpegRun(Protocol):
    """Injectable ffmpeg boundary used by the deterministic tests."""

    def __call__(
        self,
        args: Sequence[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
        timeout: float,
    ) -> object:
        """Extract sampled frames from one media file."""


@dataclass(frozen=True, slots=True)
class ObservedShape:
    """One connected shape read out of one frame."""

    kind: str
    center_x: float
    center_y: float
    area_fraction: float
    extent: float


@dataclass(frozen=True, slots=True)
class FrameObservation:
    """Every shape visible in one sampled frame."""

    index: int
    shapes: list[ObservedShape]


class SceneObserver:
    """Sample frames from a rendered MP4 and describe what each one shows."""

    def __init__(
        self,
        *,
        samples: int = 12,
        timeout: float = 60.0,
        ffmpeg_run: _FfmpegRun | None = None,
    ) -> None:
        if samples <= 0:
            raise ValueError("samples must be positive")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.samples = samples
        self.timeout = float(timeout)
        self._ffmpeg_run = ffmpeg_run or _run_ffmpeg

    def observe(self, mp4_path: str | Path, frames_dir: str | Path) -> list[FrameObservation]:
        """Extract sampled frames into ``frames_dir`` and analyze each one.

        The frames stay on disk: they are the evidence for the verdict, and a
        reader can look at exactly what the checker looked at.
        """

        source = Path(mp4_path)
        target = Path(frames_dir)
        target.mkdir(parents=True, exist_ok=True)
        duration = _duration_seconds(source)
        if duration is None or duration <= 0:
            return []

        # Sample on a fixed grid so the storyboard is reproducible for one video.
        rate = max(self.samples / duration, 1.0 / duration)
        argv = [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(source),
            "-vf",
            f"fps={rate:.6f}",
            "-frames:v",
            str(self.samples),
            str(target / "frame-%03d.png"),
        ]
        try:
            self._ffmpeg_run(
                argv,
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout,
            )
        except (OSError, subprocess.SubprocessError):
            return []

        return [
            analyze_frame(path, index=index)
            for index, path in enumerate(sorted(target.glob("frame-*.png")))
        ]


def analyze_frame(path: str | Path, *, index: int) -> FrameObservation:
    """Describe every shape visible in one frame image."""

    with Image.open(path) as handle:
        luminance = np.asarray(handle.convert("L"))

    mask = luminance > _FOREGROUND_LUMINANCE
    height, width = mask.shape
    frame_area = float(height * width)
    labels, count = ndimage.label(mask)

    shapes: list[ObservedShape] = []
    for label in range(1, count + 1):
        component = labels == label
        # Manim strokes outlines; fill them so a hollow shape measures like the
        # solid region a viewer perceives.
        filled = ndimage.binary_fill_holes(component)
        if filled is None:
            filled = component
        area = int(filled.sum())
        if area / frame_area < _MIN_AREA_FRACTION:
            continue
        shapes.append(_describe(filled, area=area, width=width, height=height))

    shapes.sort(key=_horizontal_order)
    return FrameObservation(index=index, shapes=shapes)


def _describe(
    filled: npt.NDArray[np.bool_],
    *,
    area: int,
    width: int,
    height: int,
) -> ObservedShape:
    rows, columns = np.nonzero(filled)
    top, bottom = int(rows.min()), int(rows.max())
    left, right = int(columns.min()), int(columns.max())
    box_height = bottom - top + 1
    box_width = right - left + 1
    extent = area / float(box_height * box_width)
    corners = _corner_hits(filled, top=top, left=left, height=box_height, width=box_width)
    aspect = max(box_width, box_height) / float(min(box_width, box_height))
    return ObservedShape(
        kind=_classify(extent, corners, aspect),
        center_x=float(columns.mean()) / width,
        center_y=float(rows.mean()) / height,
        area_fraction=area / float(width * height),
        extent=extent,
    )


def _corner_hits(
    filled: npt.NDArray[np.bool_],
    *,
    top: int,
    left: int,
    height: int,
    width: int,
) -> int:
    """Count bounding-box corners that fall inside the shape."""

    inset_y = max(int(round(height * _CORNER_INSET)), 1)
    inset_x = max(int(round(width * _CORNER_INSET)), 1)
    rows = (top + inset_y, top + height - 1 - inset_y)
    columns = (left + inset_x, left + width - 1 - inset_x)
    hits = 0
    for row in rows:
        for column in columns:
            if 0 <= row < filled.shape[0] and 0 <= column < filled.shape[1]:
                hits += int(bool(filled[row, column]))
    return hits


def _classify(extent: float, corners: int, aspect: float) -> str:
    """Name one shape from how it fills its bounding box."""

    if aspect > _MAX_ASPECT:
        # An elongated region is neither shape: most often it is two shapes
        # touching, which connected components cannot separate.
        return "other"
    if extent >= _SQUARE_MIN_EXTENT and corners >= 3:
        return "square"
    low, high = _CIRCLE_EXTENT_RANGE
    if low <= extent < high and corners <= 1:
        return "circle"
    if extent < low:
        return "polygon"
    return "other"


def _horizontal_order(shape: ObservedShape) -> tuple[float, float]:
    return (shape.center_x, shape.center_y)


def _duration_seconds(path: Path) -> float | None:
    """Read the media duration ffmpeg will be sampling across."""

    try:
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if probe.returncode != 0:
        return None
    try:
        return float((probe.stdout or "").strip())
    except ValueError:
        return None


def _run_ffmpeg(
    args: Sequence[str],
    *,
    capture_output: bool,
    text: bool,
    check: bool,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        capture_output=capture_output,
        text=text,
        check=check,
        timeout=timeout,
    )


__all__ = ["FrameObservation", "ObservedShape", "SceneObserver", "analyze_frame"]
