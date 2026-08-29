"""Command-line entry point for the render-in-the-loop pipeline."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Sequence
from pathlib import Path

from video_pipeline.calibration import calibrate_golden_set
from video_pipeline.pipeline import PipelineState, RenderPipeline
from video_pipeline.provider import LLMProvider, OllamaProvider
from video_pipeline.rendering import ManimRunner
from video_pipeline.spec import load_scene_spec
from video_pipeline.study import prepare_reference_study
from video_pipeline.validation import RenderValidator


def main(
    argv: Sequence[str] | None = None,
    *,
    provider: LLMProvider | None = None,
    runner: ManimRunner | None = None,
    validator: RenderValidator | None = None,
    output_root: str | Path = Path("artifacts/runs"),
    id_factory: Callable[[], str] | None = None,
    model: str = "qwen2.5-coder:7b",
    base_url: str = "http://localhost:11434",
    provider_timeout: float = 120.0,
    render_timeout: float = 120.0,
    max_attempts: int = 3,
    temperature: float = 0.0,
    seed: int = 42,
) -> int:
    """Run ``video-pipeline render scene.json`` and return its process status."""

    parser = _build_parser(
        model=model,
        base_url=base_url,
        provider_timeout=provider_timeout,
        render_timeout=render_timeout,
        max_attempts=max_attempts,
        output_root=Path(output_root),
        temperature=temperature,
        seed=seed,
    )
    options = parser.parse_args(list(argv) if argv is not None else None)
    if options.command == "calibrate":
        try:
            report = calibrate_golden_set(options.golden_root)
            document = report.to_document()
            destination = Path(options.output).resolve()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except (OSError, ValueError) as exc:
            print(f"ERROR: {exc}")
            return 1
        has_errors = bool(report.sensor_failures) or any(
            metrics.false_positives or metrics.false_negatives
            for metrics in report.axes.values()
        )
        print(f"STATE: {'CALIBRATION_FAILED' if has_errors else 'CALIBRATION_PASSED'}")
        print(f"REPORT: {destination}")
        return 1 if has_errors else 0
    if options.command == "prepare-study":
        try:
            prepared = prepare_reference_study(options.manifest, options.output_root)
        except (OSError, ValueError) as exc:
            print(f"ERROR: {exc}")
            return 1
        print("STATE: STUDY_PREPARED")
        print(f"STUDY: {prepared.root}")
        print(f"SAMPLES_PER_CONDITION: {len(prepared.control_specs)}")
        return 0
    if options.command != "render":
        parser.error("a command is required")

    try:
        spec = load_scene_spec(options.scene)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1

    configured_provider = provider if provider is not None else OllamaProvider(
        model=options.model,
        base_url=options.base_url,
        timeout=options.provider_timeout,
    )
    configured_runner = runner if runner is not None else ManimRunner(
        timeout=options.render_timeout
    )
    configured_validator = validator if validator is not None else RenderValidator()
    pipeline = RenderPipeline(
        provider=configured_provider,
        runner=configured_runner,
        validator=configured_validator,
        output_root=options.output_root,
        id_factory=id_factory,
        temperature=options.temperature,
        seed=options.seed,
    )

    try:
        result = pipeline.render(spec, max_attempts=options.max_attempts)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1

    print(f"STATE: {result.state.value.upper()}")
    print(f"RUN: {result.run_path}")
    if result.mp4_path is not None:
        print(f"MP4: {result.mp4_path}")
    if result.error:
        print(f"ERROR: {result.error}")
    return 0 if result.state is PipelineState.SUCCESS else 1


def _build_parser(
    *,
    model: str,
    base_url: str,
    provider_timeout: float,
    render_timeout: float,
    max_attempts: int,
    output_root: Path,
    temperature: float,
    seed: int,
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="video-pipeline")
    subparsers = parser.add_subparsers(dest="command")
    render = subparsers.add_parser("render", help="generate and render one scene")
    render.add_argument("scene", type=Path, help="path to a Scene Spec JSON file")
    render.add_argument(
        "--model",
        default=model,
        help="Ollama model name",
    )
    render.add_argument(
        "--base-url",
        default=base_url,
        help="Ollama base URL",
    )
    render.add_argument(
        "--provider-timeout",
        type=float,
        default=provider_timeout,
        help="Ollama request timeout in seconds",
    )
    render.add_argument(
        "--render-timeout",
        type=float,
        default=render_timeout,
        help="Manim render timeout in seconds",
    )
    render.add_argument(
        "--max-attempts",
        type=int,
        default=max_attempts,
        help="maximum generation/render attempts",
    )
    render.add_argument(
        "--temperature",
        type=_temperature,
        default=temperature,
        help="Ollama sampling temperature from 0.0 to 2.0",
    )
    render.add_argument(
        "--seed",
        type=_seed,
        default=seed,
        help="non-negative Ollama sampling seed",
    )
    render.add_argument(
        "--output-root",
        type=Path,
        default=output_root,
        help="directory under which isolated run directories are created",
    )
    calibrate = subparsers.add_parser(
        "calibrate", help="measure sensor FP/FN on the labeled golden set"
    )
    calibrate.add_argument(
        "--golden-root",
        type=Path,
        default=Path("tests/golden"),
        help="directory containing expected.json and Manim control data",
    )
    calibrate.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/sensor-calibration.json"),
        help="JSON report destination",
    )
    study = subparsers.add_parser(
        "prepare-study", help="materialize paired no-reference/reference scene specs"
    )
    study.add_argument("manifest", type=Path, help="reference-study JSON manifest")
    study.add_argument(
        "--output-root",
        type=Path,
        default=Path("artifacts/reference-study"),
        help="destination for paired control and treatment specs",
    )
    return parser


def _temperature(value: str) -> float:
    parsed = float(value)
    if not 0.0 <= parsed <= 2.0:
        raise argparse.ArgumentTypeError("temperature must be between 0.0 and 2.0")
    return parsed


def _seed(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("seed must be non-negative")
    return parsed


__all__ = ["main"]
