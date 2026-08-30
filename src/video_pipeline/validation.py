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

    @property
    def returncode(self) -> int:
        ...

    @property
    def stdout(self) -> str | None:
        ...

    @property
    def stderr(self) -> str | None:
        ...


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
class _ProbeSnapshot:
    returncode: int
    stdout: str | None
    stderr: str | None


def _run_ffprobe(
    args: Sequence[str],
    *,
    capture_output: bool,
    text: bool,
    check: bool,
) -> _ProbeResult:
    """Invoke ffprobe through the typed process boundary."""

    del check
    if not text:
        raise ValueError("ffprobe validation requires text process output")
    process: subprocess.Popen[str] = subprocess.Popen(
        list(args),
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
        text=True,
    )
    stdout, stderr = process.communicate()
    return _ProbeSnapshot(process.returncode, stdout, stderr)


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
    quality_report: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class MediaStreamFacts:
    """Normalized facts for one stream selected from an ffprobe document."""

    index: int | None
    codec_type: str | None
    codec_name: str | None
    width: int | None
    height: int | None
    pixel_format: str | None
    frame_rate: float | None
    time_base: str | None
    time_base_seconds: float | None
    duration_seconds: float | None
    sample_rate_hz: int | None
    channels: int | None
    raw: dict[str, object]

    @property
    def pix_fmt(self) -> str | None:
        """Return ffprobe's conventional pixel-format spelling."""

        return self.pixel_format


@dataclass(frozen=True, slots=True)
class AudioVisualValidationResult:
    """Strict final audiovisual facts and reasons from one ffprobe invocation."""

    path: Path
    valid: bool
    reasons: list[str]
    video_streams: tuple[MediaStreamFacts, ...] = ()
    audio_streams: tuple[MediaStreamFacts, ...] = ()
    video_stream: MediaStreamFacts | None = None
    video_duration_seconds: float | None = None
    audio_duration_seconds: float | None = None
    expected_duration_seconds: float | None = None
    video_drift_seconds: float | None = None
    audio_drift_seconds: float | None = None
    audio_video_drift_seconds: float | None = None
    size_bytes: int | None = None
    probe_size_bytes: int | None = None
    raw_probe: dict[str, object] | None = None
    probe_returncode: int | None = None
    probe_stderr: str = ""

    @property
    def selected_video_stream(self) -> MediaStreamFacts | None:
        """Return the sole video stream when the cardinality contract holds."""

        return self.video_stream

    @property
    def selected_audio_streams(self) -> tuple[MediaStreamFacts, ...]:
        """Return all audio stream facts preserved from the probe."""

        return self.audio_streams

    @property
    def audio_stream(self) -> MediaStreamFacts | None:
        """Return the first preserved audio stream, when present."""

        return self.audio_streams[0] if self.audio_streams else None

    @property
    def duration_seconds(self) -> float | None:
        """Return the selected video duration for compatibility with render facts."""

        return self.video_duration_seconds

    @property
    def probe(self) -> dict[str, object] | None:
        """Return the unmodified decoded ffprobe document."""

        return self.raw_probe

    def to_document(self) -> dict[str, object]:
        """Serialize facts without discarding the original probe payload."""

        return {
            "path": str(self.path),
            "valid": self.valid,
            "reasons": list(self.reasons),
            "video_streams": [dict(stream.raw) for stream in self.video_streams],
            "audio_streams": [dict(stream.raw) for stream in self.audio_streams],
            "video_duration_seconds": self.video_duration_seconds,
            "audio_duration_seconds": self.audio_duration_seconds,
            "expected_duration_seconds": self.expected_duration_seconds,
            "video_drift_seconds": self.video_drift_seconds,
            "audio_drift_seconds": self.audio_drift_seconds,
            "audio_video_drift_seconds": self.audio_video_drift_seconds,
            "size_bytes": self.size_bytes,
            "probe_size_bytes": self.probe_size_bytes,
            "raw_probe": self.raw_probe,
            "probe_returncode": self.probe_returncode,
            "probe_stderr": self.probe_stderr,
        }


class RenderValidator:
    """Require a non-empty, probeable MP4 with a positive video stream."""

    def __init__(self, *, ffprobe_run: _FfprobeRun | None = None) -> None:
        self._ffprobe_run = ffprobe_run if ffprobe_run is not None else _run_ffprobe

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
            loaded: object = json.loads(probe.stdout or "")
        except (TypeError, ValueError) as exc:
            return _invalid(candidate, f"ffprobe output is unparseable: {exc}")
        document = _probe_document(loaded)
        if document is None:
            return _invalid(candidate, "ffprobe output is not an object")

        streams = document.get("streams")
        video = _first_video_stream(streams)
        reasons: list[str] = []
        if video is None:
            reasons.append("ffprobe found no video stream")

        format_data = _probe_document(document.get("format")) or {}

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


class FinalAudioVisualValidator:
    """Validate the complete video/audio contract of a final MP4."""

    def __init__(
        self,
        *,
        ffprobe_run: _FfprobeRun | None = None,
        expected_duration_seconds: float | None = None,
        expected_resolution: tuple[int, int] = (854, 480),
        expected_fps: float = 15.0,
        expected_timebase: str | int = "1/90000",
        duration_tolerance_seconds: float = 0.05,
    ) -> None:
        _validate_audio_visual_configuration(
            expected_duration_seconds,
            expected_resolution,
            expected_fps,
            expected_timebase,
            duration_tolerance_seconds,
        )
        self._ffprobe_run = ffprobe_run if ffprobe_run is not None else _run_ffprobe
        self._expected_duration_seconds = expected_duration_seconds
        self._expected_resolution = expected_resolution
        self._expected_fps = expected_fps
        self._expected_timebase = expected_timebase
        self._duration_tolerance_seconds = duration_tolerance_seconds

    def validate(
        self,
        path: str | Path,
        *,
        expected_duration_seconds: float | None = None,
        expected_resolution: tuple[int, int] | None = None,
        expected_fps: float | None = None,
        expected_timebase: str | int | None = None,
        duration_tolerance_seconds: float | None = None,
    ) -> AudioVisualValidationResult:
        """Probe and validate one final audiovisual artifact."""

        candidate = Path(path)
        configured_duration = (
            self._expected_duration_seconds
            if expected_duration_seconds is None
            else expected_duration_seconds
        )
        configured_resolution = (
            self._expected_resolution
            if expected_resolution is None
            else expected_resolution
        )
        configured_fps = self._expected_fps if expected_fps is None else expected_fps
        configured_timebase = (
            self._expected_timebase if expected_timebase is None else expected_timebase
        )
        configured_tolerance = (
            self._duration_tolerance_seconds
            if duration_tolerance_seconds is None
            else duration_tolerance_seconds
        )
        _validate_audio_visual_configuration(
            configured_duration,
            configured_resolution,
            configured_fps,
            configured_timebase,
            configured_tolerance,
        )

        if not candidate.exists():
            return _invalid_audio_visual(candidate, "MP4 is missing")
        if not candidate.is_file():
            return _invalid_audio_visual(candidate, "MP4 path is not a file")

        actual_size = candidate.stat().st_size
        if actual_size <= 0:
            return _invalid_audio_visual(
                candidate,
                "MP4 is empty (size is zero)",
                size_bytes=actual_size,
            )

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
            return _invalid_audio_visual(candidate, f"ffprobe failed: {exc}")

        probe_stderr = probe.stderr or ""
        if probe.returncode != 0:
            detail = probe_stderr.strip()
            suffix = f": {detail}" if detail else ""
            return _invalid_audio_visual(
                candidate,
                f"ffprobe failed{suffix}",
                probe_returncode=probe.returncode,
                probe_stderr=probe_stderr,
            )

        try:
            loaded: object = json.loads(probe.stdout or "")
        except (TypeError, ValueError) as exc:
            return _invalid_audio_visual(
                candidate,
                f"ffprobe output is unparseable: {exc}",
                probe_returncode=probe.returncode,
                probe_stderr=probe_stderr,
            )
        document = _probe_document(loaded)
        if document is None:
            return _invalid_audio_visual(
                candidate,
                "ffprobe output is not an object",
                probe_returncode=probe.returncode,
                probe_stderr=probe_stderr,
            )
        format_data = _probe_document(document.get("format")) or {}
        format_duration = _number(format_data.get("duration"))
        effective_expected_duration = (
            configured_duration if configured_duration is not None else format_duration
        )
        stream_values = document.get("streams")
        all_streams = _stream_facts(stream_values)
        video_streams = tuple(
            stream for stream in all_streams if stream.codec_type == "video"
        )
        audio_streams = tuple(
            stream for stream in all_streams if stream.codec_type == "audio"
        )
        selected_video = video_streams[0] if len(video_streams) == 1 else None
        video_duration = selected_video.duration_seconds if selected_video is not None else None
        audio_duration = audio_streams[0].duration_seconds if audio_streams else None
        video_drift = _signed_drift(video_duration, effective_expected_duration)
        audio_drift = _signed_drift(audio_duration, effective_expected_duration)
        audio_video_drift = _signed_drift(audio_duration, video_duration)
        probe_size = _positive_int(format_data.get("size"))

        reasons: list[str] = []
        if len(video_streams) != 1:
            reasons.append(
                "exactly one video stream is required; "
                f"ffprobe found {len(video_streams)}"
            )
        elif selected_video is not None:
            _append_video_reasons(
                reasons,
                selected_video,
                configured_resolution,
                configured_fps,
                configured_timebase,
            )

        usable_audio = tuple(stream for stream in audio_streams if _usable_audio(stream))
        if not usable_audio:
            reasons.append("at least one usable audio stream is required")
            _append_audio_reasons(reasons, audio_streams)

        if probe_size is None:
            reasons.append("ffprobe size must be positive")
        if effective_expected_duration is not None:
            _append_drift_reason(
                reasons,
                "video",
                video_drift,
                configured_tolerance,
            )
            _append_drift_reason(
                reasons,
                "audio",
                audio_drift,
                configured_tolerance,
            )
        if audio_video_drift is not None and abs(audio_video_drift) > configured_tolerance:
            reasons.append(
                "audio/video duration drift: "
                f"{audio_video_drift:+.6f}s exceeds tolerance "
                f"{configured_tolerance:.6f}s"
            )

        return AudioVisualValidationResult(
            path=candidate,
            valid=not reasons,
            reasons=reasons,
            video_streams=video_streams,
            audio_streams=audio_streams,
            video_stream=selected_video,
            video_duration_seconds=video_duration,
            audio_duration_seconds=audio_duration,
            expected_duration_seconds=effective_expected_duration,
            video_drift_seconds=video_drift,
            audio_drift_seconds=audio_drift,
            audio_video_drift_seconds=audio_video_drift,
            size_bytes=actual_size,
            probe_size_bytes=probe_size,
            raw_probe=document,
            probe_returncode=probe.returncode,
            probe_stderr=probe_stderr,
        )


def _invalid_audio_visual(
    path: Path,
    reason: str,
    *,
    size_bytes: int | None = None,
    probe_returncode: int | None = None,
    probe_stderr: str = "",
) -> AudioVisualValidationResult:
    return AudioVisualValidationResult(
        path=path,
        valid=False,
        reasons=[reason],
        size_bytes=size_bytes,
        probe_returncode=probe_returncode,
        probe_stderr=probe_stderr,
    )


def _validate_audio_visual_configuration(
    expected_duration_seconds: float | None,
    expected_resolution: tuple[int, int],
    expected_fps: float,
    expected_timebase: str | int,
    duration_tolerance_seconds: float,
) -> None:
    if expected_duration_seconds is not None and (
        not math.isfinite(expected_duration_seconds) or expected_duration_seconds <= 0
    ):
        raise ValueError("expected duration must be finite and positive")
    width, height = expected_resolution
    if width <= 0 or height <= 0:
        raise ValueError("expected resolution must be positive")
    if not math.isfinite(expected_fps) or expected_fps <= 0:
        raise ValueError("expected FPS must be finite and positive")
    timebase_value = _rational(expected_timebase)
    if timebase_value is None or timebase_value <= 0:
        raise ValueError("expected timebase must be positive")
    if not math.isfinite(duration_tolerance_seconds) or duration_tolerance_seconds < 0:
        raise ValueError("duration tolerance must be finite and non-negative")


def _probe_document(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    document: dict[str, object] = {}
    for key in value:
        if isinstance(key, str):
            document[key] = value[key]
    return document


def _stream_facts(value: object) -> tuple[MediaStreamFacts, ...]:
    if not isinstance(value, list):
        return ()
    facts: list[MediaStreamFacts] = []
    for stream in value:
        if isinstance(stream, dict):
            normalized = _probe_document(stream)
            if normalized is not None:
                facts.append(_media_stream_facts(normalized))
    return tuple(facts)


def _media_stream_facts(stream: dict[str, object]) -> MediaStreamFacts:
    time_base = _string(stream.get("time_base"))
    frame_rate = _rational(stream.get("avg_frame_rate"))
    if frame_rate is None:
        frame_rate = _rational(stream.get("r_frame_rate"))
    return MediaStreamFacts(
        index=_nonnegative_int(stream.get("index")),
        codec_type=_string(stream.get("codec_type")),
        codec_name=_string(stream.get("codec_name")),
        width=_positive_int(stream.get("width")),
        height=_positive_int(stream.get("height")),
        pixel_format=_string(stream.get("pix_fmt")),
        frame_rate=frame_rate,
        time_base=time_base,
        time_base_seconds=_rational(time_base),
        duration_seconds=_number(stream.get("duration")),
        sample_rate_hz=_positive_int(stream.get("sample_rate")),
        channels=_positive_int(stream.get("channels")),
        raw=dict(stream),
    )


def _append_video_reasons(
    reasons: list[str],
    stream: MediaStreamFacts,
    expected_resolution: tuple[int, int],
    expected_fps: float,
    expected_timebase: str | int,
) -> None:
    if stream.codec_name is None or stream.codec_name.lower() != "h264":
        reasons.append("video codec must be H264")
    if stream.width is None:
        reasons.append("video width must be positive")
    if stream.height is None:
        reasons.append("video height must be positive")
    if stream.width is not None and stream.height is not None:
        if (stream.width, stream.height) != expected_resolution:
            reasons.append(
                "video resolution mismatch: "
                f"{stream.width}x{stream.height} != "
                f"{expected_resolution[0]}x{expected_resolution[1]}"
            )
    if stream.pixel_format != "yuv420p":
        reasons.append("video pixel format must be yuv420p")
    if stream.frame_rate is None:
        reasons.append("video FPS is missing")
    elif abs(stream.frame_rate - expected_fps) > 1e-6:
        reasons.append(
            f"video FPS mismatch: {stream.frame_rate:.6f} != {expected_fps:.6f}"
        )
    expected_timebase_value = _rational(expected_timebase)
    if stream.time_base_seconds is None:
        reasons.append("video timebase is missing")
    elif expected_timebase_value is not None and abs(
        stream.time_base_seconds - expected_timebase_value
    ) > 1e-12:
        reasons.append(
            f"video timebase mismatch: {stream.time_base} != {expected_timebase}"
        )
    if stream.duration_seconds is None or stream.duration_seconds <= 0:
        reasons.append("video duration must be positive")


def _append_audio_reasons(
    reasons: list[str],
    streams: tuple[MediaStreamFacts, ...],
) -> None:
    for stream in streams:
        if stream.codec_name is None or stream.codec_name.lower() != "aac":
            reasons.append("audio codec must be AAC")
        if stream.sample_rate_hz is None:
            reasons.append("audio sample rate must be positive")
        if stream.channels is None:
            reasons.append("audio channel count must be positive")
        if stream.duration_seconds is None or stream.duration_seconds <= 0:
            reasons.append("audio duration must be positive")


def _usable_audio(stream: MediaStreamFacts) -> bool:
    return (
        stream.codec_name is not None
        and stream.codec_name.lower() == "aac"
        and stream.sample_rate_hz is not None
        and stream.channels is not None
        and stream.duration_seconds is not None
        and stream.duration_seconds > 0
    )


def _append_drift_reason(
    reasons: list[str],
    label: str,
    drift: float | None,
    tolerance: float,
) -> None:
    if drift is not None and abs(drift) > tolerance:
        reasons.append(
            f"{label} duration drift; expected duration drift: "
            f"{drift:+.6f}s exceeds tolerance {tolerance:.6f}s"
        )


def _signed_drift(value: float | None, reference: float | None) -> float | None:
    if value is None or reference is None:
        return None
    return value - reference


def _string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _rational(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if "/" not in text:
        return _number(text)
    numerator_text, denominator_text = text.split("/", 1)
    numerator = _number(numerator_text)
    denominator = _number(denominator_text)
    if numerator is None or denominator is None or denominator == 0:
        return None
    ratio = numerator / denominator
    return ratio if math.isfinite(ratio) else None


def _nonnegative_int(value: object) -> int | None:
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
    return number if number >= 0 else None


def _invalid(path: Path, reason: str) -> ValidationResult:
    return ValidationResult(path=path, valid=False, reasons=[reason])


def _first_video_stream(value: object) -> dict[str, object] | None:
    if not isinstance(value, list):
        return None
    for stream in value:
        if not isinstance(stream, dict):
            continue
        normalized = _probe_document(stream)
        if normalized is not None and normalized.get("codec_type") == "video":
            return normalized
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


__all__ = [
    "AudioVisualValidationResult",
    "FinalAudioVisualValidator",
    "MediaStreamFacts",
    "RenderValidator",
    "ValidationResult",
]
