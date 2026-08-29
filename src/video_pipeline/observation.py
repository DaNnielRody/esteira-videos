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

The vocabulary is deliberately narrow: circle, square, polygon, a fixed colour
palette, coarse regions and centroid motion. It does not recognise text or
arbitrary geometry; deterministic ``MathTex`` checks live in the separate
LaTeX validator.
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

from video_pipeline.sensors import SensorFailure, SensorFailureCode, SensorResult

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

# Colour naming. Hue bins sit at the midpoints between Manim's own palette
# constants, measured rather than guessed:
#   RED 4.7  MAROON 348.2  ORANGE 25.1  GOLD 31.9  YELLOW 46.8  GREEN 101.3
#   TEAL 165.0  BLUE 191.3  DARK_BLUE 199.6  PURPLE 281.4  PINK 308.7
_HUE_BINS: tuple[tuple[float, float, str], ...] = (
    (0.0, 15.0, "red"),
    (15.0, 39.0, "orange"),
    (39.0, 70.0, "yellow"),
    (70.0, 140.0, "green"),
    (140.0, 178.0, "teal"),
    (178.0, 240.0, "blue"),
    (240.0, 295.0, "purple"),
    (295.0, 335.0, "pink"),
    (335.0, 360.0, "red"),
)
# Antialiasing and h264 chroma subsampling dilute saturation, so a shape counts
# as coloured on its strongest pixels, not its median ones.
_SATURATION_PERCENTILE = 90
_MIN_SATURATION = 0.20
# The same blend drags value down: a white stroke reads median 0.60 and 90th
# percentile 1.00. The brightest drawn pixels are the stroke.
_VALUE_PERCENTILE = 90
_WHITE_MIN_VALUE = 0.75
_GREY_MIN_VALUE = 0.25


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
    color: str
    center_x: float
    center_y: float
    area_fraction: float
    extent: float


@dataclass(frozen=True, slots=True)
class FrameObservation:
    """Every shape visible in one sampled frame."""

    index: int
    shapes: list[ObservedShape]


@dataclass(frozen=True, slots=True, init=False)
class ObservationResult(SensorResult[list[FrameObservation]]):
    """Uniform frame-sensor result containing evidence or an explicit failure."""

    def __init__(
        self,
        *,
        frames: list[FrameObservation] | None = None,
        failure: SensorFailure | None = None,
    ) -> None:
        SensorResult.__init__(self, evidence=frames, failure=failure)

    @property
    def frames(self) -> list[FrameObservation]:
        """Compatibility name for successful frame evidence."""

        return self.evidence if self.evidence is not None else []

    @classmethod
    def success(cls, evidence: list[FrameObservation]) -> ObservationResult:
        """Build successful frame evidence through the shared sensor contract."""

        return cls(frames=evidence)

    @classmethod
    def failed(
        cls,
        failure: SensorFailure | SensorFailureCode,
        detail: str | None = None,
    ) -> ObservationResult:
        """Build a failed result without pretending an empty storyboard was seen."""

        if isinstance(failure, SensorFailure):
            return cls(failure=failure)
        if detail is None:
            raise ValueError("sensor failure detail is required")
        return cls(failure=SensorFailure(code=failure, detail=detail))


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

    def observe(self, mp4_path: str | Path, frames_dir: str | Path) -> ObservationResult:
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
            return ObservationResult.failed(
                SensorFailureCode.DURATION_UNAVAILABLE,
                f"could not read a positive duration from {source}",
            )

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
            extraction = self._ffmpeg_run(
                argv,
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            return ObservationResult.failed(
                SensorFailureCode.FRAME_EXTRACTION_TIMEOUT,
                f"ffmpeg frame extraction timed out after {exc.timeout} seconds",
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return ObservationResult.failed(
                SensorFailureCode.FRAME_EXTRACTION_FAILED,
                f"ffmpeg frame extraction failed: {exc}",
            )
        returncode = getattr(extraction, "returncode", 0)
        if returncode != 0:
            detail = str(getattr(extraction, "stderr", "") or "").strip()
            return ObservationResult.failed(
                SensorFailureCode.FRAME_EXTRACTION_FAILED,
                f"ffmpeg frame extraction exited {returncode}: {detail}",
            )

        frames = _read_png_stack(sorted(target.glob("frame-*.png")))
        if frames.size == 0:
            return ObservationResult.failed(
                SensorFailureCode.NO_FRAMES_EXTRACTED,
                f"ffmpeg produced no readable frames from {source}",
            )
        np.savez_compressed(target / "frames.npz", frame_data=frames)
        return ObservationResult(frames=analyze_frames(frames))


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
    colour = _rgb_channels(frame)

    shapes: list[ObservedShape] = []
    for contour in contours:
        area = float(cv2.contourArea(contour))
        if area / frame_area < _MIN_AREA_FRACTION:
            continue
        described = _describe(
            contour, area=area, width=width, height=height, mask=mask, colour=colour
        )
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
    mask: npt.NDArray[np.uint8],
    colour: npt.NDArray[np.uint8],
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
        color=_name_colour(_drawn_pixels(contour, mask=mask, colour=colour)),
        center_x=float(center_x) / width,
        center_y=float(center_y) / height,
        area_fraction=area / float(width * height),
        extent=extent,
    )


def _rgb_channels(frame: npt.NDArray[np.uint8]) -> npt.NDArray[np.uint8]:
    """Return the frame's colour channels, dropping any alpha."""

    array = np.asarray(frame)
    if array.ndim == 2:
        return np.ascontiguousarray(cv2.cvtColor(array, cv2.COLOR_GRAY2RGB), dtype=np.uint8)
    return np.ascontiguousarray(array[..., :3], dtype=np.uint8)


def _drawn_pixels(
    contour: npt.NDArray[np.int32],
    *,
    mask: npt.NDArray[np.uint8],
    colour: npt.NDArray[np.uint8],
) -> npt.NDArray[np.uint8]:
    """Return only the pixels this shape actually paints.

    Filling the contour would include the background a hollow shape encloses,
    so the filled region is intersected with the foreground mask: what remains
    is stroke plus fill, which is what a viewer sees.
    """

    region = np.zeros(mask.shape, dtype=np.uint8)
    cv2.drawContours(region, [contour], -1, 255, thickness=cv2.FILLED)
    drawn = (region > 0) & (mask > 0)
    return np.ascontiguousarray(colour[drawn], dtype=np.uint8)


def _name_colour(pixels: npt.NDArray[np.uint8]) -> str:
    """Name the colour a viewer would give this shape."""

    if pixels.size == 0:
        return "unknown"
    hsv = cv2.cvtColor(pixels.reshape(-1, 1, 3), cv2.COLOR_RGB2HSV).reshape(-1, 3)
    # OpenCV packs hue into 0..179 to fit a byte; degrees are twice that.
    hue = hsv[:, 0].astype(np.float64) * 2.0
    saturation = hsv[:, 1].astype(np.float64) / 255.0
    value = hsv[:, 2].astype(np.float64) / 255.0

    if float(np.percentile(saturation, _SATURATION_PERCENTILE)) >= _MIN_SATURATION:
        coloured = saturation >= _MIN_SATURATION
        return _hue_name(float(np.median(hue[coloured])))

    brightest = float(np.percentile(value, _VALUE_PERCENTILE))
    if brightest >= _WHITE_MIN_VALUE:
        return "white"
    if brightest >= _GREY_MIN_VALUE:
        return "grey"
    return "black"


def _hue_name(degrees: float) -> str:
    """Map one hue angle to its Manim-palette colour name."""

    angle = degrees % 360.0
    for low, high, name in _HUE_BINS:
        if low <= angle < high:
            return name
    return "red"


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
    "ObservationResult",
    "ObservedShape",
    "SceneObserver",
    "SensorFailure",
    "SensorFailureCode",
    "analyze_frame",
    "analyze_frames",
]
