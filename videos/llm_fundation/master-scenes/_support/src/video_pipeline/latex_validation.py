"""Deterministic LaTeX verification against a fixed Manim reference render."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt

from video_pipeline.expectations import LatexExpectation, TextExpectation
from video_pipeline.observation import SceneObserver
from video_pipeline.rendering import ManimRunner
from video_pipeline.sensors import SensorFailure, SensorFailureCode, SensorResult

_FOREGROUND_LUMINANCE = 40
_BBOX_PADDING = 3
_MIN_COLOR_SIMILARITY = 0.90

RenderedTextExpectation = LatexExpectation | TextExpectation


@dataclass(frozen=True, slots=True)
class LatexMatch:
    """Best observed mask overlap for one expected expression."""

    tex: str
    best_iou: float
    color_similarity: float
    min_iou: float
    matched_frame: str | None
    renderer: str = "mathtex"


@dataclass(frozen=True, slots=True)
class LatexValidationResult:
    """Semantic mismatches or an explicit sensor failure, never both."""

    matches: list[LatexMatch]
    reasons: list[str]
    failure: SensorFailure | None = None

    def to_document(self) -> dict[str, object]:
        """Return the canonical artifact schema for this sensor result."""

        return {
            "failure": (
                {"code": self.failure.code.value, "detail": self.failure.detail}
                if self.failure is not None
                else None
            ),
            "matches": [
                {
                    "tex": match.tex,
                    "best_iou": match.best_iou,
                    "color_similarity": match.color_similarity,
                    "min_iou": match.min_iou,
                    "matched_frame": match.matched_frame,
                    "renderer": match.renderer,
                }
                for match in self.matches
            ],
            "reasons": list(self.reasons),
        }


class LatexValidator:
    """Render fixed MathTex references and compare them to sampled video frames."""

    def __init__(self, *, timeout: float = 60.0) -> None:
        self.timeout = timeout

    def observe(
        self,
        expectations: list[RenderedTextExpectation],
        candidate_frames_dir: str | Path,
        evidence_dir: str | Path,
    ) -> SensorResult[list[LatexMatch]]:
        """Produce visual match evidence without deciding scene validity."""

        if not expectations:
            return SensorResult.success([])
        candidate_paths = sorted(Path(candidate_frames_dir).glob("frame-*.png"))
        if not candidate_paths:
            return SensorResult.failed(
                SensorFailure(
                    SensorFailureCode.NO_FRAMES_EXTRACTED,
                    "latex validation received no sampled candidate frames",
                )
            )
        unreadable = next((path for path in candidate_paths if not _readable(path)), None)
        if unreadable is not None:
            return SensorResult.failed(
                SensorFailure(
                    SensorFailureCode.FRAME_DECODE_FAILED,
                    f"could not decode sampled frame {unreadable}",
                )
            )

        root = Path(evidence_dir)
        root.mkdir(parents=True, exist_ok=True)
        matches: list[LatexMatch] = []
        for index, expectation in enumerate(expectations, start=1):
            item_dir = root / f"latex-{index:02d}"
            reference, failure = self._render_reference(expectation, item_dir)
            if failure is not None:
                return SensorResult.failed(failure)
            if reference is None:
                return SensorResult.failed(
                    SensorFailure(
                        SensorFailureCode.LATEX_REFERENCE_RENDER_FAILED,
                        f"reference render for latex item {index} produced no frame",
                    )
                )
            if not _readable(reference):
                return SensorResult.failed(
                    SensorFailure(
                        SensorFailureCode.FRAME_DECODE_FAILED,
                        f"could not decode latex reference frame {reference}",
                    )
                )
            match = _best_match(expectation, reference, candidate_paths)
            matches.append(match)
        return SensorResult.success(matches)

    def _render_reference(
        self,
        expectation: RenderedTextExpectation,
        target: Path,
    ) -> tuple[Path | None, SensorFailure | None]:
        target.mkdir(parents=True, exist_ok=True)
        source = target / "reference.py"
        color = expectation.color.upper()
        if isinstance(expectation, TextExpectation):
            content = expectation.content
            if expectation.renderer == "text":
                constructor = (
                    f"Text({content!r}, font={expectation.font!r}, "
                    f"font_size={expectation.font_size}, color={color})"
                )
            else:
                constructor = (
                    f"Tex({content!r}, font_size={expectation.font_size}, color={color})"
                )
        else:
            constructor = (
                f"MathTex({expectation.tex!r}, "
                f"font_size={expectation.font_size}, color={color})"
            )
        source.write_text(
            "from manim import *\n\n"
            "class RenderedTextReferenceScene(Scene):\n"
            "    def construct(self):\n"
            f"        formula = {constructor}\n"
            f"        formula.move_to([{expectation.x!r}, {expectation.y!r}, 0.0])\n"
            "        self.add(formula)\n"
            "        self.wait(0.5)\n",
            encoding="utf-8",
        )
        render = ManimRunner(timeout=self.timeout).run(source, target / "media")
        if render.exit_code != 0 or not render.mp4_paths:
            detail = render.stderr.strip() or render.stdout.strip() or "no reference MP4"
            return None, SensorFailure(
                SensorFailureCode.LATEX_REFERENCE_RENDER_FAILED,
                f"latex reference render failed: {detail}",
            )
        observed = SceneObserver(samples=1, timeout=self.timeout).observe(
            render.mp4_paths[0], target / "frames"
        )
        if observed.failure is not None:
            return None, observed.failure
        paths = sorted((target / "frames").glob("frame-*.png"))
        return (paths[0] if paths else None), None


def _best_match(
    expectation: RenderedTextExpectation,
    reference_path: Path,
    candidate_paths: list[Path],
) -> LatexMatch:
    reference = _mask(reference_path)
    bounds = _bounds(reference)
    if bounds is None:
        return LatexMatch(
            tex=_content(expectation),
            best_iou=0.0,
            color_similarity=0.0,
            min_iou=expectation.min_iou,
            matched_frame=None,
            renderer=_renderer(expectation),
        )
    top, bottom, left, right = bounds
    reference_crop = reference[top:bottom, left:right]
    target_color = _target_color(reference_path, reference)
    best_score = 0.0
    best_color = 0.0
    best_path: Path | None = None
    for path in candidate_paths:
        candidate = _mask_matching_color(path, target_color)
        if candidate.shape != reference.shape:
            continue
        candidate_crop = candidate[top:bottom, left:right]
        score = _iou(reference_crop, candidate_crop)
        if best_path is None or score > best_score:
            best_score = score
            best_color = _color_similarity(reference_path, path, reference)
            best_path = path
    return LatexMatch(
        tex=_content(expectation),
        best_iou=best_score,
        color_similarity=best_color,
        min_iou=expectation.min_iou,
        matched_frame=str(best_path) if best_path is not None else None,
        renderer=_renderer(expectation),
    )


def _content(expectation: RenderedTextExpectation) -> str:
    return expectation.content if isinstance(expectation, TextExpectation) else expectation.tex


def _renderer(expectation: RenderedTextExpectation) -> str:
    return expectation.renderer if isinstance(expectation, TextExpectation) else "mathtex"


def check_latex_matches(matches: list[LatexMatch]) -> list[str]:
    """Return semantic reasons derived only from successful sensor evidence."""

    reasons: list[str] = []
    for index, match in enumerate(matches, start=1):
        if match.color_similarity < _MIN_COLOR_SIMILARITY:
            reasons.append(
                f"latex item {index} has the wrong fixed color: similarity "
                f"{match.color_similarity:.4f} is below {_MIN_COLOR_SIMILARITY:.4f}"
            )
        elif match.best_iou < match.min_iou:
            reasons.append(
                f"latex item {index} was never observed with the fixed typography: "
                f"expected {match.tex!r}, best mask IoU {match.best_iou:.4f} "
                f"is below {match.min_iou:.4f}"
            )
    return reasons


def _mask(path: Path) -> npt.NDArray[np.bool_]:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        return np.zeros((0, 0), dtype=np.bool_)
    grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return np.asarray(grey > _FOREGROUND_LUMINANCE, dtype=np.bool_)


def _readable(path: Path) -> bool:
    return cv2.imread(str(path), cv2.IMREAD_COLOR) is not None


def _target_color(
    path: Path,
    mask: npt.NDArray[np.bool_],
) -> npt.NDArray[np.float64]:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None or not np.any(mask):
        return np.zeros(3, dtype=np.float64)
    return np.asarray(np.median(image[mask].astype(np.float64), axis=0), dtype=np.float64)


def _mask_matching_color(
    path: Path,
    target: npt.NDArray[np.float64],
) -> npt.NDArray[np.bool_]:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        return np.zeros((0, 0), dtype=np.bool_)
    target_pixel = np.clip(target, 0, 255).astype(np.uint8).reshape(1, 1, 3)
    target_hsv = cv2.cvtColor(target_pixel, cv2.COLOR_BGR2HSV)[0, 0]
    candidate_hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    visible = grey > _FOREGROUND_LUMINANCE
    target_saturation = int(target_hsv[1])
    if int(target_hsv[2]) <= _FOREGROUND_LUMINANCE:
        return np.zeros(image.shape[:2], dtype=np.bool_)
    if target_saturation < 50:
        return np.asarray(visible & (candidate_hsv[..., 1] < 65), dtype=np.bool_)
    hue_delta = np.abs(candidate_hsv[..., 0].astype(np.int16) - int(target_hsv[0]))
    circular_delta = np.minimum(hue_delta, 180 - hue_delta)
    return np.asarray(
        visible & (candidate_hsv[..., 1] >= 35) & (circular_delta <= 12),
        dtype=np.bool_,
    )


def _color_similarity(
    reference_path: Path,
    candidate_path: Path,
    reference_mask: npt.NDArray[np.bool_],
) -> float:
    reference = cv2.imread(str(reference_path), cv2.IMREAD_COLOR)
    candidate = cv2.imread(str(candidate_path), cv2.IMREAD_COLOR)
    if reference is None or candidate is None or reference.shape != candidate.shape:
        return 0.0
    candidate_mask = _mask(candidate_path)
    shared = reference_mask & candidate_mask
    if not np.any(shared):
        return 0.0
    expected = np.median(reference[shared].astype(np.float64), axis=0)
    actual = np.median(candidate[shared].astype(np.float64), axis=0)
    maximum_distance = float(np.sqrt(3 * (255**2)))
    distance = float(np.linalg.norm(expected - actual))
    return max(0.0, 1.0 - distance / maximum_distance)


def _bounds(mask: npt.NDArray[np.bool_]) -> tuple[int, int, int, int] | None:
    rows, columns = np.nonzero(mask)
    if rows.size == 0 or columns.size == 0:
        return None
    top = max(int(rows.min()) - _BBOX_PADDING, 0)
    bottom = min(int(rows.max()) + _BBOX_PADDING + 1, mask.shape[0])
    left = max(int(columns.min()) - _BBOX_PADDING, 0)
    right = min(int(columns.max()) + _BBOX_PADDING + 1, mask.shape[1])
    return top, bottom, left, right


def _iou(left: npt.NDArray[np.bool_], right: npt.NDArray[np.bool_]) -> float:
    left_count = int(np.count_nonzero(left))
    right_count = int(np.count_nonzero(right))
    if left_count == 0 or right_count == 0:
        return 0.0
    kernel = np.ones((3, 3), dtype=np.uint8)
    left_neighborhood = cv2.dilate(left.astype(np.uint8), kernel) > 0
    right_neighborhood = cv2.dilate(right.astype(np.uint8), kernel) > 0
    recall = int(np.count_nonzero(left & right_neighborhood)) / left_count
    precision = int(np.count_nonzero(right & left_neighborhood)) / right_count
    return 2.0 * precision * recall / (precision + recall) if precision + recall else 0.0


__all__ = [
    "LatexMatch",
    "LatexValidationResult",
    "LatexValidator",
    "check_latex_matches",
]
