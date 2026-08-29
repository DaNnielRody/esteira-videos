"""Measure observer false positives and negatives on the labeled golden set."""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from video_pipeline.expectations import LatexExpectation, TextExpectation
from video_pipeline.latex_validation import LatexValidator, check_latex_matches
from video_pipeline.observation import FrameObservation, SceneObserver, analyze_frames
from video_pipeline.rendering import ManimRunner

_AXES = ("shape_count", "shape", "color", "region", "motion", "latex", "text")
_SHAPES = frozenset({"circle", "square", "polygon"})
_COLORS = frozenset(
    {"red", "orange", "yellow", "green", "teal", "blue", "purple", "pink", "white", "grey", "black"}
)
_LOW = 1.0 / 3.0
_HIGH = 2.0 / 3.0


@dataclass(slots=True)
class AxisMetrics:
    """Confusion counts for one observable axis."""

    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    true_negatives: int = 0

    @property
    def false_positive_rate(self) -> float | None:
        denominator = self.false_positives + self.true_negatives
        return self.false_positives / denominator if denominator else None

    @property
    def false_negative_rate(self) -> float | None:
        denominator = self.false_negatives + self.true_positives
        return self.false_negatives / denominator if denominator else None


@dataclass(frozen=True, slots=True)
class CalibrationFailure:
    """One golden case the sensor could not evaluate."""

    file: str
    detail: str


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    """Aggregate labeled evidence for all current observer axes."""

    scenes: int
    axes: dict[str, AxisMetrics]
    sensor_failures: list[CalibrationFailure]

    def to_document(self) -> dict[str, object]:
        """Return a stable JSON-ready report."""

        return {
            "scenes": self.scenes,
            "sensor_failures": [
                {"file": failure.file, "detail": failure.detail}
                for failure in self.sensor_failures
            ],
            "axes": {
                name: {
                    "true_positives": metrics.true_positives,
                    "false_positives": metrics.false_positives,
                    "false_negatives": metrics.false_negatives,
                    "true_negatives": metrics.true_negatives,
                    "false_positive_rate": metrics.false_positive_rate,
                    "false_negative_rate": metrics.false_negative_rate,
                }
                for name, metrics in self.axes.items()
            },
        }


def calibrate_golden_set(root: str | Path) -> CalibrationReport:
    """Evaluate the observer against independently declared golden labels."""

    golden = Path(root)
    manifest = _manifest(golden / "expected.json")
    latex_manifest = _latex_manifest(golden / "latex" / "expected.json")
    text_manifest = _latex_manifest(golden / "text" / "expected.json")
    metrics = {axis: AxisMetrics() for axis in _AXES}
    failures: list[CalibrationFailure] = []

    for scene in manifest:
        relative = scene.get("file")
        if not isinstance(relative, str):
            failures.append(CalibrationFailure(file="<unknown>", detail="missing file label"))
            continue
        try:
            with np.load(golden / relative) as data:
                frames = data["frame_data"]
                if frames.ndim != 4:
                    frames = np.expand_dims(frames, axis=0)
                observations = analyze_frames(frames)
            _measure_scene(scene, observations, metrics)
        except (OSError, KeyError, ValueError) as exc:
            failures.append(CalibrationFailure(file=relative, detail=str(exc)))

    _measure_latex_cases(latex_manifest, metrics["latex"], failures)
    _measure_text_cases(text_manifest, metrics["text"], failures)

    return CalibrationReport(
        scenes=len(manifest) + len(latex_manifest) + len(text_manifest),
        axes=metrics,
        sensor_failures=failures,
    )


def _manifest(path: Path) -> list[dict[str, object]]:
    document: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("scenes"), list):
        raise ValueError("golden manifest must contain a scenes list")
    scenes: list[dict[str, object]] = []
    for item in document["scenes"]:
        if not isinstance(item, dict):
            raise ValueError("every golden scene must be an object")
        scenes.append({str(key): value for key, value in item.items()})
    return scenes


def _latex_manifest(path: Path) -> list[dict[str, object]]:
    document: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("cases"), list):
        raise ValueError("latex golden manifest must contain a cases list")
    cases: list[dict[str, object]] = []
    for item in document["cases"]:
        if not isinstance(item, dict):
            raise ValueError("every latex golden case must be an object")
        cases.append({str(key): value for key, value in item.items()})
    return cases


def _measure_latex_cases(
    cases: list[dict[str, object]],
    metrics: AxisMetrics,
    failures: list[CalibrationFailure],
) -> None:
    for case in cases:
        name = case.get("name")
        expected_tex = case.get("expected_tex")
        candidate_tex = case.get("candidate_tex")
        should_match = case.get("should_match")
        if (
            not isinstance(name, str)
            or not isinstance(expected_tex, str)
            or not isinstance(candidate_tex, str)
            or not isinstance(should_match, bool)
        ):
            failures.append(CalibrationFailure(file="latex/<invalid>", detail="invalid label"))
            continue
        with tempfile.TemporaryDirectory(prefix="video-pipeline-latex-calibration-") as temp:
            root = Path(temp)
            source = root / "candidate.py"
            source.write_text(
                "from manim import *\n\n"
                "class LatexCalibrationScene(Scene):\n"
                "    def construct(self):\n"
                f"        formula = MathTex({candidate_tex!r}, font_size=48, color=YELLOW)\n"
                "        formula.move_to([0.0, 0.0, 0.0])\n"
                "        self.add(formula)\n"
                "        self.wait(0.5)\n",
                encoding="utf-8",
            )
            rendered = ManimRunner(timeout=120).run(source, root / "media")
            if rendered.exit_code != 0 or not rendered.mp4_paths:
                failures.append(
                    CalibrationFailure(file=f"latex/{name}", detail="candidate render failed")
                )
                continue
            observed = SceneObserver(samples=1, timeout=120).observe(
                rendered.mp4_paths[0], root / "frames"
            )
            if observed.failure is not None:
                failures.append(
                    CalibrationFailure(file=f"latex/{name}", detail=observed.failure.detail)
                )
                continue
            expectation = LatexExpectation(
                tex=expected_tex,
                font_size=48,
                color="yellow",
                x=0.0,
                y=0.0,
                min_iou=0.95,
            )
            result = LatexValidator(timeout=120).validate(
                [expectation], root / "frames", root / "evidence"
            )
            if result.failure is not None:
                failures.append(
                    CalibrationFailure(file=f"latex/{name}", detail=result.failure.detail)
                )
                continue
            predicted_match = not result.reasons
            if should_match and predicted_match:
                metrics.true_positives += 1
            elif should_match:
                metrics.false_negatives += 1
            elif predicted_match:
                metrics.false_positives += 1
            else:
                metrics.true_negatives += 1


def _measure_text_cases(
    cases: list[dict[str, object]],
    metrics: AxisMetrics,
    failures: list[CalibrationFailure],
) -> None:
    for case in cases:
        name = case.get("name")
        renderer = case.get("renderer")
        expected_content = case.get("expected_content")
        candidate_content = case.get("candidate_content")
        font = case.get("font")
        expected_font_size = case.get("expected_font_size")
        candidate_font_size = case.get("candidate_font_size")
        expected_color = case.get("expected_color")
        candidate_color = case.get("candidate_color")
        expected_x = case.get("expected_x")
        candidate_x = case.get("candidate_x")
        should_match = case.get("should_match")
        if (
            not isinstance(name, str)
            or renderer not in {"text", "tex"}
            or not isinstance(expected_content, str)
            or not isinstance(candidate_content, str)
            or (font is not None and not isinstance(font, str))
            or not isinstance(expected_font_size, int)
            or not isinstance(candidate_font_size, int)
            or not isinstance(expected_color, str)
            or not isinstance(candidate_color, str)
            or not isinstance(expected_x, (int, float))
            or not isinstance(candidate_x, (int, float))
            or not isinstance(should_match, bool)
        ):
            failures.append(CalibrationFailure(file="text/<invalid>", detail="invalid label"))
            continue
        try:
            expectation = TextExpectation(
                renderer=renderer,
                content=expected_content,
                font=font,
                font_size=expected_font_size,
                color=expected_color,
                x=float(expected_x),
                y=0.0,
                min_iou=0.95,
            )
            candidate = TextExpectation(
                renderer=renderer,
                content=candidate_content,
                font=font,
                font_size=candidate_font_size,
                color=candidate_color,
                x=float(candidate_x),
                y=0.0,
                min_iou=0.95,
            )
        except ValueError as exc:
            failures.append(CalibrationFailure(file=f"text/{name}", detail=str(exc)))
            continue
        with tempfile.TemporaryDirectory(prefix="video-pipeline-text-calibration-") as temp:
            root = Path(temp)
            source = root / "candidate.py"
            if candidate.renderer == "text":
                constructor = (
                    f"Text({candidate.content!r}, font={candidate.font!r}, "
                    f"font_size={candidate.font_size}, color={candidate.color.upper()})"
                )
            else:
                constructor = (
                    f"Tex({candidate.content!r}, font_size={candidate.font_size}, "
                    f"color={candidate.color.upper()})"
                )
            source.write_text(
                "from manim import *\n\n"
                "class TextCalibrationScene(Scene):\n"
                "    def construct(self):\n"
                f"        label = {constructor}\n"
                f"        label.move_to([{candidate.x!r}, 0.0, 0.0])\n"
                "        self.add(label)\n"
                "        self.wait(0.5)\n",
                encoding="utf-8",
            )
            rendered = ManimRunner(timeout=120).run(source, root / "media")
            if rendered.exit_code != 0 or not rendered.mp4_paths:
                failures.append(
                    CalibrationFailure(file=f"text/{name}", detail="candidate render failed")
                )
                continue
            observed_frames = SceneObserver(samples=1, timeout=120).observe(
                rendered.mp4_paths[0], root / "frames"
            )
            if observed_frames.failure is not None:
                failures.append(
                    CalibrationFailure(
                        file=f"text/{name}", detail=observed_frames.failure.detail
                    )
                )
                continue
            observed_text = LatexValidator(timeout=120).observe(
                [expectation], root / "frames", root / "evidence"
            )
            if observed_text.failure is not None:
                failures.append(
                    CalibrationFailure(file=f"text/{name}", detail=observed_text.failure.detail)
                )
                continue
            matches = observed_text.evidence if observed_text.evidence is not None else []
            predicted_match = not check_latex_matches(matches)
            _record_binary_prediction(should_match, predicted_match, metrics)


def _record_binary_prediction(
    expected_positive: bool,
    predicted_positive: bool,
    metrics: AxisMetrics,
) -> None:
    if expected_positive and predicted_positive:
        metrics.true_positives += 1
    elif expected_positive:
        metrics.false_negatives += 1
    elif predicted_positive:
        metrics.false_positives += 1
    else:
        metrics.true_negatives += 1


def _measure_scene(
    scene: dict[str, object],
    observations: list[FrameObservation],
    metrics: dict[str, AxisMetrics],
) -> None:
    _measure_counts(scene, observations, metrics["shape_count"])
    _measure_frame_label(scene, observations, metrics["shape"], label="frame")
    _measure_frame_label(scene, observations, metrics["color"], label="frame_color")
    _measure_region(scene, observations, metrics["region"])
    _measure_motion(scene, observations, metrics["motion"])


def _measure_counts(
    scene: dict[str, object],
    observations: list[FrameObservation],
    metrics: AxisMetrics,
) -> None:
    expected = scene.get("shapes_per_frame")
    if not isinstance(expected, int):
        return
    for observation in observations:
        actual = len(observation.shapes)
        # Count is a multiclass sensor. Score it one-vs-rest over the bounded
        # labels visible to this sample: 0 through one above the larger count.
        # This records real negative classes (the frame is not count 0, 2, ...)
        # instead of reporting an undefined FPR when the exact count is right.
        class_count = max(expected, actual) + 2
        if actual == expected:
            metrics.true_positives += 1
            metrics.true_negatives += class_count - 1
        else:
            metrics.false_positives += 1
            metrics.false_negatives += 1
            metrics.true_negatives += max(class_count - 2, 0)


def _measure_frame_label(
    scene: dict[str, object],
    observations: list[FrameObservation],
    metrics: AxisMetrics,
    *,
    label: str,
) -> None:
    if not observations:
        return
    declared: list[tuple[int, str]] = []
    every = scene.get(f"every_{label}")
    if isinstance(every, str):
        declared.extend((index, every) for index in range(len(observations)))
    else:
        first = scene.get(f"first_{label}")
        last = scene.get(f"last_{label}")
        if isinstance(first, str):
            declared.append((0, first))
        if isinstance(last, str):
            declared.append((len(observations) - 1, last))

    for index, expected in declared:
        shapes = observations[index].shapes
        actual = [shape.kind if label == "frame" else shape.color for shape in shapes]
        vocabulary = _SHAPES if label == "frame" else _COLORS
        actual_labels = set(actual)
        if expected in actual:
            metrics.true_positives += 1
        else:
            metrics.false_negatives += 1
        unexpected = actual_labels - {expected}
        metrics.false_positives += len(unexpected)
        metrics.true_negatives += len(vocabulary - {expected} - unexpected)


def _measure_region(
    scene: dict[str, object],
    observations: list[FrameObservation],
    metrics: AxisMetrics,
) -> None:
    region = scene.get("region")
    if not isinstance(region, dict):
        return
    for observation in observations:
        if not observation.shapes:
            for expected in region.values():
                if isinstance(expected, str):
                    metrics.false_negatives += 1
            continue
        shape = observation.shapes[0]
        expected_x = region.get("x")
        if isinstance(expected_x, str):
            _measure_binary(expected_x == _x_region(shape.center_x), metrics)
        expected_y = region.get("y")
        if isinstance(expected_y, str):
            _measure_binary(expected_y == _y_region(shape.center_y), metrics)


def _measure_motion(
    scene: dict[str, object],
    observations: list[FrameObservation],
    metrics: AxisMetrics,
) -> None:
    if not observations or not observations[0].shapes or not observations[-1].shapes:
        return
    start = observations[0].shapes[0]
    end = observations[-1].shapes[-1]
    horizontal = end.center_x - start.center_x
    vertical = start.center_y - end.center_y
    travel = {
        "right": horizontal,
        "left": -horizontal,
        "up": vertical,
        "down": -vertical,
    }
    threshold_value = scene.get("min_motion", 0.05)
    threshold = float(threshold_value) if isinstance(threshold_value, (int, float)) else 0.05
    moved = scene.get("moved")
    for direction in moved if isinstance(moved, list) else []:
        if isinstance(direction, str):
            if travel.get(direction, 0.0) >= threshold:
                metrics.true_positives += 1
            else:
                metrics.false_negatives += 1
    still = scene.get("not_moved")
    for direction in still if isinstance(still, list) else []:
        if isinstance(direction, str):
            if travel.get(direction, 0.0) >= threshold:
                metrics.false_positives += 1
            else:
                metrics.true_negatives += 1


def _measure_binary(matches: bool, metrics: AxisMetrics) -> None:
    if matches:
        metrics.true_positives += 1
        metrics.true_negatives += 2
    else:
        metrics.false_positives += 1
        metrics.false_negatives += 1
        metrics.true_negatives += 1


def _x_region(value: float) -> str:
    if value < _LOW:
        return "left"
    if value > _HIGH:
        return "right"
    return "center"


def _y_region(value: float) -> str:
    if value < _LOW:
        return "top"
    if value > _HIGH:
        return "bottom"
    return "middle"


__all__ = ["AxisMetrics", "CalibrationFailure", "CalibrationReport", "calibrate_golden_set"]
