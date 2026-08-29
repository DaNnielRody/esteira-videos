"""Independent MP4 validation through ffprobe."""

from __future__ import annotations

import json
import math
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class _ProbeResult(Protocol):
    """The ffprobe process facts consumed by the validator."""

    returncode: int
    stdout: str | None
    stderr: str | None


class _FfprobeRun(Protocol):
    """Injectable ffprobe boundary used by the deterministic tests."""

    def __call__(
        self,
        args: Sequence[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> _ProbeResult:
        """Probe one media file."""


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Facts and reasons produced by one independent media validation."""

    path: Path
    valid: bool
    reasons: list[str]
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    size_bytes: int | None = None
    sensor_failure_code: str | None = None
    sensor_failure_detail: str | None = None


class RenderValidator:
    """Require a non-empty, probeable MP4 with a positive video stream."""

    def __init__(self, *, ffprobe_run: _FfprobeRun | None = None) -> None:
        self._ffprobe_run = ffprobe_run or subprocess.run

    def validate(self, path: str | Path) -> ValidationResult:
        """Probe ``path`` and reject every missing or unusable media boundary."""

        candidate = Path(path)
        if not candidate.exists():
            return _invalid(candidate, "MP4 is missing")
        if not candidate.is_file():
            return _invalid(candidate, "MP4 path is not a file")

        actual_size = candidate.stat().st_size
        if actual_size <= 0:
            return _invalid(candidate, "MP4 is empty (size is zero)")

        argv = [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(candidate),
        ]
        try:
            probe = self._ffprobe_run(
                argv,
                capture_output=True,
                text=True,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return _invalid(candidate, f"ffprobe failed: {exc}")

        if probe.returncode != 0:
            detail = (probe.stderr or "").strip()
            suffix = f": {detail}" if detail else ""
            return _invalid(candidate, f"ffprobe failed{suffix}")

        try:
            document = json.loads(probe.stdout or "")
        except (TypeError, ValueError) as exc:
            return _invalid(candidate, f"ffprobe output is unparseable: {exc}")
        if not isinstance(document, dict):
            return _invalid(candidate, "ffprobe output is not an object")

        streams = document.get("streams")
        video = _first_video_stream(streams)
        reasons: list[str] = []
        if video is None:
            reasons.append("ffprobe found no video stream")

        format_data = document.get("format")
        if not isinstance(format_data, dict):
            format_data = {}

        width = _positive_int(video.get("width")) if video is not None else None
        height = _positive_int(video.get("height")) if video is not None else None
        if width is None:
            reasons.append("video width must be positive")
        if height is None:
            reasons.append("video height must be positive")

        stream_duration = _number(video.get("duration")) if video is not None else None
        format_duration = _number(format_data.get("duration"))
        duration_seconds = (
            stream_duration if stream_duration is not None else format_duration
        )
        if duration_seconds is None:
            reasons.append("duration is missing or unparseable")
        else:
            if duration_seconds <= 0:
                reasons.append("duration must be positive")

        size_bytes = _positive_int(format_data.get("size"))
        if size_bytes is None:
            reasons.append("ffprobe size must be positive")

        return ValidationResult(
            path=candidate,
            valid=not reasons,
            reasons=reasons,
            width=width,
            height=height,
            duration_seconds=duration_seconds,
            size_bytes=size_bytes,
        )


def _invalid(path: Path, reason: str) -> ValidationResult:
    return ValidationResult(path=path, valid=False, reasons=[reason])


def _first_video_stream(value: object) -> dict[str, object] | None:
    if not isinstance(value, list):
        return None
    for stream in value:
        if not isinstance(stream, dict):
            continue
        if stream.get("codec_type") == "video":
            return stream
    return None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = value
    elif isinstance(value, str):
        try:
            number = int(value)
        except ValueError:
            return None
    else:
        return None
    return number if number > 0 else None
