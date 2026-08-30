"""Deterministic duration normalization for rendered scene video."""

from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from video_pipeline.validation import RenderValidator, ValidationResult

TemporalNormalizationStatus = Literal[
    "accepted",
    "normalized",
    "requires_regeneration",
    "failed",
]


@dataclass(frozen=True, slots=True)
class TemporalTolerances:
    """Central acceptance and deterministic-correction limits in seconds."""

    acceptance_seconds: float = 0.05
    correction_limit_seconds: float = 0.5

    def __post_init__(self) -> None:
        if not math.isfinite(self.acceptance_seconds) or self.acceptance_seconds < 0:
            raise ValueError("acceptance_seconds must be finite and non-negative")
        if not math.isfinite(self.correction_limit_seconds):
            raise ValueError("correction_limit_seconds must be finite")
        if self.correction_limit_seconds < self.acceptance_seconds:
            raise ValueError(
                "correction_limit_seconds must be at least acceptance_seconds"
            )


class _ProcessFacts(Protocol):
    @property
    def returncode(self) -> int:
        ...

    @property
    def stdout(self) -> str | bytes | None:
        ...

    @property
    def stderr(self) -> str | bytes | None:
        ...


@dataclass(frozen=True, slots=True)
class _ProcessSnapshot:
    returncode: int
    stdout: str | bytes | None
    stderr: str | bytes | None


class TemporalFFmpegRun(Protocol):
    """Replaceable FFmpeg process boundary."""

    def __call__(
        self,
        args: Sequence[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> _ProcessFacts:
        """Run one normalization command and return process facts."""


class TemporalValidator(Protocol):
    """Validator boundary used after a normalized file is published."""

    def validate(self, path: str | Path) -> ValidationResult:
        """Re-probe one normalized scene."""


class TemporalNormalizer(Protocol):
    """Deterministic decision boundary for one rendered scene."""

    def normalize(
        self,
        raw_path: str | Path,
        *,
        normalized_path: str | Path,
        observed_duration_seconds: float,
        target_duration_seconds: float,
        target_resolution: tuple[int, int],
        target_fps: int,
        target_timebase: int,
        target_pixel_format: str,
        validator: TemporalValidator,
    ) -> "TemporalNormalizationResult":
        """Accept, deterministically correct, or escalate a scene."""


@dataclass(frozen=True, slots=True)
class TemporalNormalizationResult:
    """Decision and complete process/validation evidence for one scene."""

    status: TemporalNormalizationStatus
    raw_path: Path
    normalized_path: Path | None
    log_path: Path
    observed_duration_seconds: float
    target_duration_seconds: float
    delta_seconds: float
    argv: list[str]
    stdout: str
    stderr: str
    exit_code: int | None
    elapsed_seconds: float
    validated_duration_seconds: float | None = None
    validation_reasons: list[str] | None = None

    def to_document(self) -> dict[str, object]:
        """Return the JSON evidence persisted by :func:`normalize_scene`."""

        return {
            "status": self.status,
            "raw_path": str(self.raw_path),
            "normalized_path": (
                str(self.normalized_path) if self.normalized_path is not None else None
            ),
            "log_path": str(self.log_path),
            "observed_duration_seconds": self.observed_duration_seconds,
            "target_duration_seconds": self.target_duration_seconds,
            "delta_seconds": self.delta_seconds,
            "argv": list(self.argv),
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "elapsed_seconds": self.elapsed_seconds,
            "validated_duration_seconds": self.validated_duration_seconds,
            "validation_reasons": list(self.validation_reasons or []),
        }


def normalize_scene(
    raw_path: str | Path,
    *,
    normalized_path: str | Path,
    observed_duration_seconds: float,
    target_duration_seconds: float,
    target_resolution: tuple[int, int],
    target_fps: int,
    target_timebase: int,
    target_pixel_format: str,
    tolerances: TemporalTolerances | None = None,
    ffmpeg_run: TemporalFFmpegRun | None = None,
    validator: TemporalValidator | None = None,
    log_path: str | Path | None = None,
) -> TemporalNormalizationResult:
    """Accept, normalize, or escalate one rendered scene deterministically.

    The signed delta is ``observed - target``.  Small deltas are accepted as-is;
    deltas within the correction limit use an explicit trim or cloned-frame pad;
    larger deltas require a new render and never invoke FFmpeg.
    """

    raw = Path(raw_path)
    normalized = Path(normalized_path)
    log = Path(log_path) if log_path is not None else _default_log_path(normalized)
    _validate_inputs(
        raw,
        normalized,
        observed_duration_seconds,
        target_duration_seconds,
        target_resolution,
        target_fps,
        target_timebase,
        target_pixel_format,
    )
    active_tolerances = tolerances or TemporalTolerances()
    delta = observed_duration_seconds - target_duration_seconds

    if abs(delta) <= active_tolerances.acceptance_seconds:
        result = TemporalNormalizationResult(
            status="accepted",
            raw_path=raw,
            normalized_path=None,
            log_path=log,
            observed_duration_seconds=observed_duration_seconds,
            target_duration_seconds=target_duration_seconds,
            delta_seconds=delta,
            argv=[],
            stdout="",
            stderr="",
            exit_code=None,
            elapsed_seconds=0.0,
        )
        _write_log(log, result)
        return result

    if abs(delta) > active_tolerances.correction_limit_seconds:
        result = TemporalNormalizationResult(
            status="requires_regeneration",
            raw_path=raw,
            normalized_path=None,
            log_path=log,
            observed_duration_seconds=observed_duration_seconds,
            target_duration_seconds=target_duration_seconds,
            delta_seconds=delta,
            argv=[],
            stdout="",
            stderr=(
                "duration delta exceeds deterministic correction limit: "
                f"{delta:+.6f}s"
            ),
            exit_code=None,
            elapsed_seconds=0.0,
        )
        _write_log(log, result)
        return result

    normalized.parent.mkdir(parents=True, exist_ok=True)
    argv = _normalization_argv(
        raw,
        normalized,
        delta=delta,
        target_duration_seconds=target_duration_seconds,
        target_resolution=target_resolution,
        target_fps=target_fps,
        target_timebase=target_timebase,
        target_pixel_format=target_pixel_format,
    )
    started = time.monotonic()
    runner: TemporalFFmpegRun
    if ffmpeg_run is None:
        runner = _run_ffmpeg
    else:
        runner = ffmpeg_run
    try:
        completed = runner(
            argv,
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        result = TemporalNormalizationResult(
            status="failed",
            raw_path=raw,
            normalized_path=None,
            log_path=log,
            observed_duration_seconds=observed_duration_seconds,
            target_duration_seconds=target_duration_seconds,
            delta_seconds=delta,
            argv=list(argv),
            stdout="",
            stderr=str(exc),
            exit_code=None,
            elapsed_seconds=max(0.0, time.monotonic() - started),
        )
        _write_log(log, result)
        return result

    elapsed = max(0.0, time.monotonic() - started)
    process = _process_facts(completed)
    if process[0] != 0:
        result = TemporalNormalizationResult(
            status="failed",
            raw_path=raw,
            normalized_path=None,
            log_path=log,
            observed_duration_seconds=observed_duration_seconds,
            target_duration_seconds=target_duration_seconds,
            delta_seconds=delta,
            argv=list(argv),
            stdout=process[1],
            stderr=process[2],
            exit_code=process[0],
            elapsed_seconds=elapsed,
        )
        _write_log(log, result)
        return result

    if not normalized.is_file():
        result = TemporalNormalizationResult(
            status="failed",
            raw_path=raw,
            normalized_path=None,
            log_path=log,
            observed_duration_seconds=observed_duration_seconds,
            target_duration_seconds=target_duration_seconds,
            delta_seconds=delta,
            argv=list(argv),
            stdout=process[1],
            stderr="FFmpeg did not produce the normalized scene",
            exit_code=process[0],
            elapsed_seconds=elapsed,
        )
        _write_log(log, result)
        return result

    active_validator = validator or RenderValidator()
    validation = active_validator.validate(normalized)
    result = _result_after_validation(
        raw,
        normalized,
        log,
        observed_duration_seconds,
        target_duration_seconds,
        delta,
        list(argv),
        process[1],
        process[2],
        process[0],
        elapsed,
        validation,
    )
    _write_log(log, result)
    return result


def _result_after_validation(
    raw_path: Path,
    normalized_path: Path,
    log_path: Path,
    observed_duration_seconds: float,
    target_duration_seconds: float,
    delta_seconds: float,
    argv: list[str],
    stdout: str,
    stderr: str,
    exit_code: int,
    elapsed_seconds: float,
    validation: ValidationResult,
) -> TemporalNormalizationResult:
    reasons = list(validation.reasons)
    validated_duration = validation.duration_seconds
    if not validation.valid:
        reasons.append("normalized scene failed independent validation")
    if validated_duration is None:
        reasons.append("normalized scene duration is unavailable")
    elif abs(validated_duration - target_duration_seconds) > 1e-6:
        reasons.append(
            "normalized scene duration does not match target: "
            f"{validated_duration:.6f}s vs {target_duration_seconds:.6f}s"
        )
    return TemporalNormalizationResult(
        status="normalized" if not reasons else "failed",
        raw_path=raw_path,
        normalized_path=normalized_path if not reasons else None,
        log_path=log_path,
        observed_duration_seconds=observed_duration_seconds,
        target_duration_seconds=target_duration_seconds,
        delta_seconds=delta_seconds,
        argv=argv,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        elapsed_seconds=elapsed_seconds,
        validated_duration_seconds=validated_duration,
        validation_reasons=reasons,
    )


def _normalization_argv(
    raw_path: Path,
    normalized_path: Path,
    *,
    delta: float,
    target_duration_seconds: float,
    target_resolution: tuple[int, int],
    target_fps: int,
    target_timebase: int,
    target_pixel_format: str,
) -> list[str]:
    width, height = target_resolution
    if delta > 0:
        duration_filter = f"trim=duration={target_duration_seconds:.6f}"
    else:
        duration_filter = f"tpad=stop_mode=clone:stop_duration={-delta:.6f}"
    filter_graph = (
        f"{duration_filter},setpts=PTS-STARTPTS,settb=1/{target_timebase}"
    )
    return [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(raw_path),
        "-vf",
        filter_graph,
        "-s",
        f"{width}x{height}",
        "-r",
        str(target_fps),
        "-video_track_timescale",
        str(target_timebase),
        "-pix_fmt",
        target_pixel_format,
        "-c:v",
        "libx264",
        "-an",
        "-y",
        str(normalized_path),
    ]


def _validate_inputs(
    raw_path: Path,
    normalized_path: Path,
    observed_duration_seconds: float,
    target_duration_seconds: float,
    target_resolution: tuple[int, int],
    target_fps: int,
    target_timebase: int,
    target_pixel_format: str,
) -> None:
    if not raw_path.is_file():
        raise ValueError(f"raw scene does not exist: {raw_path}")
    if raw_path.resolve() == normalized_path.resolve():
        raise ValueError("normalized scene path must be distinct from raw scene path")
    if not math.isfinite(observed_duration_seconds) or observed_duration_seconds <= 0:
        raise ValueError("observed duration must be finite and positive")
    if not math.isfinite(target_duration_seconds) or target_duration_seconds <= 0:
        raise ValueError("target duration must be finite and positive")
    width, height = target_resolution
    if width <= 0 or height <= 0:
        raise ValueError("target resolution must be positive")
    if target_fps <= 0:
        raise ValueError("target FPS must be positive")
    if target_timebase <= 0:
        raise ValueError("target timebase must be positive")
    if not target_pixel_format.strip():
        raise ValueError("target pixel format must not be blank")


def _result_document_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".temporal.json")


def _default_log_path(normalized_path: Path) -> Path:
    return _result_document_path(normalized_path)


def _write_log(path: Path, result: TemporalNormalizationResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(result.to_document(), ensure_ascii=False, indent=2, sort_keys=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except BaseException:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def _process_facts(value: _ProcessFacts) -> tuple[int, str, str]:
    return value.returncode, _text_output(value.stdout), _text_output(value.stderr)


def _run_ffmpeg(
    args: Sequence[str],
    *,
    capture_output: bool,
    text: bool,
    check: bool,
    ) -> _ProcessFacts:
    del check
    if not text:
        raise ValueError("FFmpeg normalization requires text process output")
    process: subprocess.Popen[str] = subprocess.Popen(
        list(args),
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
        text=True,
    )
    stdout, stderr = process.communicate()
    return _ProcessSnapshot(process.returncode, stdout, stderr)


def _text_output(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


__all__ = [
    "TemporalFFmpegRun",
    "TemporalNormalizationResult",
    "TemporalNormalizationStatus",
    "TemporalNormalizer",
    "TemporalTolerances",
    "TemporalValidator",
    "normalize_scene",
]
