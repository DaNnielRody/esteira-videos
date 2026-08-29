"""Read back what a rendered MP4 actually shows, frame by frame.

Manim exits zero and ffprobe accepts the container for a scene that draws the
wrong thing, so neither can decide semantic fidelity. This module observes the
pixels instead: it samples frames from the finished video and reports the
shapes visible in each one, which is what the specification can be checked
against.

Frames are handled in Manim's own control-data shape -- a
``(n_frames, height, width, 4)`` uint8 RGBA array stored under the key
``frame_data`` -- so the evidence written here can be read by Manim's testing
utilities, and Manim's vendored control data can be read by this module. See
``tests/golden/README.md``.

Shape measurement uses OpenCV contours, and every descriptor comes from the
*rotated* minimum-area rectangle, so a square is a square at any angle. An
axis-aligned bounding box is not rotation invariant and previously reported a
square rotated by ten degrees as a polygon.

The vocabulary is deliberately narrow: circle, square, polygon. It does not
recognise text, colour, or arbitrary geometry.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np
import numpy.typing as npt

# Manim renders on a black background; anything brighter is drawn content.
_FOREGROUND_LUMINANCE = 40
# Antialiasing leaves specks that are not shapes.
_MIN_AREA_FRACTION = 0.0004
# Douglas-Peucker tolerance, as a fraction of the contour perimeter.
_POLYGON_EPSILON = 0.02
# A circle and a square both sit in a near-square rotated box. An elongated
# region is neither: most often it is two shapes touching, which contour
# extraction cannot separate.
_MAX_ASPECT = 1.25
# Fraction of its rotated box a shape fills: a square ~1.0, a circle ~pi/4.
_SQUARE_MIN_EXTENT = 0.85
_CIRCLE_EXTENT_RANGE = (0.62, _SQUARE_MIN_EXTENT)
# Douglas-Peucker keeps four corners for a quadrilateral, many for a circle.
_CIRCLE_MIN_VERTICES = 6


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

        The sampled frames stay on disk twice: as PNGs a reader can open, and
        as ``frames.npz`` in Manim's control-data format. They are the evidence
        the verdict was taken from.
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

        frames = _read_png_stack(sorted(target.glob("frame-*.png")))
        if frames.size == 0:
            return []
        np.savez_compressed(target / "frames.npz", frame_data=frames)
        return analyze_frames(frames)


def analyze_frames(frames: npt.NDArray[np.uint8]) -> list[FrameObservation]:
    """Describe every shape in a Manim-shaped ``(n, h, w, 4)`` RGBA stack."""

    stack = np.asarray(frames)
    if stack.ndim != 4:
        # Manim's own backward compatibility for single-frame control data.
        stack = np.expand_dims(stack, axis=0)
    return [analyze_frame(frame, index=index) for index, frame in enumerate(stack)]


def analyze_frame(frame: npt.NDArray[np.uint8], *, index: int) -> FrameObservation:
    """Describe every shape visible in one RGBA frame."""

    mask = _foreground_mask(frame)
    height, width = mask.shape
    frame_area = float(height * width)

    # External contours only: a stroked outline yields one boundary, so a
    # hollow shape measures as the solid region a viewer perceives.
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    shapes: list[ObservedShape] = []
    for contour in contours:
        area = float(cv2.contourArea(contour))
        if area / frame_area < _MIN_AREA_FRACTION:
            continue
        described = _describe(contour, area=area, width=width, height=height)
        if described is not None:
            shapes.append(described)

    shapes.sort(key=_horizontal_order)
    return FrameObservation(index=index, shapes=shapes)


def _foreground_mask(frame: npt.NDArray[np.uint8]) -> npt.NDArray[np.uint8]:
    """Return drawn content as an 8-bit mask, ignoring any alpha channel."""

    array = np.asarray(frame)
    if array.ndim == 3 and array.shape[-1] >= 3:
        colour = np.ascontiguousarray(array[..., :3], dtype=np.uint8)
        luminance = cv2.cvtColor(colour, cv2.COLOR_RGB2GRAY)
    else:
        luminance = np.ascontiguousarray(array, dtype=np.uint8)
    _, mask = cv2.threshold(luminance, _FOREGROUND_LUMINANCE, 255, cv2.THRESH_BINARY)
    return mask


def _describe(
    contour: npt.NDArray[np.int32],
    *,
    area: float,
    width: int,
    height: int,
) -> ObservedShape | None:
    (center_x, center_y), (box_width, box_height), _ = cv2.minAreaRect(contour)
    if box_width <= 0 or box_height <= 0:
        return None

    # Every descriptor below comes from the rotated box, so it holds at any angle.
    aspect = max(box_width, box_height) / min(box_width, box_height)
    extent = area / (box_width * box_height)
    perimeter = float(cv2.arcLength(contour, True))
    vertices = len(cv2.approxPolyDP(contour, _POLYGON_EPSILON * perimeter, True))

    return ObservedShape(
        kind=_classify(aspect=aspect, extent=extent, vertices=vertices),
        center_x=float(center_x) / width,
        center_y=float(center_y) / height,
        area_fraction=area / float(width * height),
        extent=extent,
    )


def _classify(*, aspect: float, extent: float, vertices: int) -> str:
    """Name one shape from rotation-invariant descriptors."""

    if aspect > _MAX_ASPECT:
        return "polygon"
    if vertices == 4 and extent >= _SQUARE_MIN_EXTENT:
        return "square"
    low, high = _CIRCLE_EXTENT_RANGE
    if vertices >= _CIRCLE_MIN_VERTICES and low <= extent < high:
        return "circle"
    return "polygon"


def _horizontal_order(shape: ObservedShape) -> tuple[float, float]:
    return (shape.center_x, shape.center_y)


def _read_png_stack(paths: list[Path]) -> npt.NDArray[np.uint8]:
    """Load sampled PNGs into Manim's ``(n, h, w, 4)`` RGBA layout."""

    frames: list[npt.NDArray[np.uint8]] = []
    for path in paths:
        # cv2 reads BGR(A); Manim's control data is RGBA.
        image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if image is None:
            continue
        if image.ndim == 2:
            image = cv2.cvtColor(image, cv2.COLOR_GRAY2RGBA)
        elif image.shape[-1] == 3:
            image = cv2.cvtColor(image, cv2.COLOR_BGR2RGBA)
        else:
            image = cv2.cvtColor(image, cv2.COLOR_BGRA2RGBA)
        frames.append(np.ascontiguousarray(image, dtype=np.uint8))
    if not frames:
        return np.zeros((0, 0, 0, 4), dtype=np.uint8)
    return np.stack(frames)


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


__all__ = [
    "FrameObservation",
    "ObservedShape",
    "SceneObserver",
    "analyze_frame",
    "analyze_frames",
]
