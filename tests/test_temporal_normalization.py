"""Public contract for deterministic scene-duration normalization."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

import pytest

from video_pipeline.temporal import TemporalTolerances, normalize_scene
from video_pipeline.validation import ValidationResult


class CompletedProcessFake:
    """The process facts returned by the fake FFmpeg boundary."""

    def __init__(self) -> None:
        self.returncode = 0
        self.stdout = "FFMPEG_STDOUT_SENTINEL"
        self.stderr = "FFMPEG_STDERR_SENTINEL"


class FakeFFmpeg:
    """Copy raw bytes to the requested output and retain the command facts."""

    def __init__(self, raw_path: Path) -> None:
        self.raw_path = raw_path
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(
        self,
        argv: Sequence[str],
        *args: object,
        **kwargs: object,
    ) -> CompletedProcessFake:
        del args
        self.calls.append((list(argv), kwargs))
        output_path = Path(argv[-1])
        output_path.write_bytes(b"NORMALIZED_SCENE_BYTES")
        return CompletedProcessFake()


class FakeValidator:
    """Revalidate the normalized output with a declared target duration."""

    def __init__(self, duration_seconds: float) -> None:
        self.duration_seconds = duration_seconds
        self.calls: list[Path] = []

    def validate(self, path: str | Path) -> ValidationResult:
        candidate = Path(path)
        self.calls.append(candidate)
        return ValidationResult(
            path=candidate,
            valid=True,
            reasons=[],
            duration_seconds=self.duration_seconds,
            size_bytes=candidate.stat().st_size,
        )


@pytest.mark.parametrize(
    "case,observed,target,expected_status,filter_marker",
    [
        ("long", 5.08, 5.0, "normalized", "trim="),
        ("short", 4.93, 5.0, "normalized", "tpad=stop_mode=clone"),
        ("too_long", 5.5, 5.0, "requires_regeneration", None),
    ],
)
def test_scene_temporal_normalization_is_deterministic_and_auditable(
    tmp_path: Path,
    case: str,
    observed: float,
    target: float,
    expected_status: str,
    filter_marker: str | None,
) -> None:
    raw_path = tmp_path / f"{case}-raw.mp4"
    raw_bytes = f"RAW_{case}_BYTES".encode("utf-8")
    raw_path.write_bytes(raw_bytes)
    normalized_path = tmp_path / f"{case}-normalized.mp4"
    log_path = tmp_path / f"{case}-normalization.json"
    ffmpeg = FakeFFmpeg(raw_path)
    validator = FakeValidator(target)
    tolerances = TemporalTolerances(
        acceptance_seconds=0.02,
        correction_limit_seconds=0.2,
    )

    result = normalize_scene(
        raw_path,
        normalized_path=normalized_path,
        log_path=log_path,
        observed_duration_seconds=observed,
        target_duration_seconds=target,
        target_resolution=(854, 480),
        target_fps=15,
        target_timebase=90_000,
        target_pixel_format="yuv420p",
        tolerances=tolerances,
        ffmpeg_run=ffmpeg,
        validator=validator,
    )

    assert result.status == expected_status
    assert result.delta_seconds == pytest.approx(observed - target)
    assert result.raw_path == raw_path
    assert raw_path.read_bytes() == raw_bytes
    assert result.log_path == log_path
    log = json.loads(log_path.read_text(encoding="utf-8"))
    assert log["delta_seconds"] == pytest.approx(observed - target)
    assert log["status"] == expected_status

    if expected_status == "requires_regeneration":
        assert result.normalized_path is None
        assert ffmpeg.calls == []
        assert validator.calls == []
        assert log["exit_code"] is None
        return

    assert result.normalized_path == normalized_path
    assert normalized_path != raw_path
    assert normalized_path.read_bytes() == b"NORMALIZED_SCENE_BYTES"
    assert validator.calls == [normalized_path]
    assert result.validated_duration_seconds == pytest.approx(target)
    assert len(ffmpeg.calls) == 1
    argv, kwargs = ffmpeg.calls[0]
    joined_argv = " ".join(argv)
    assert filter_marker in joined_argv
    assert "setpts=PTS-STARTPTS" in joined_argv
    assert "854x480" in joined_argv
    assert "15" in argv
    assert "90000" in argv
    assert "yuv420p" in argv
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert kwargs["check"] is False
    assert result.argv == argv
    assert result.stdout == "FFMPEG_STDOUT_SENTINEL"
    assert result.stderr == "FFMPEG_STDERR_SENTINEL"
    assert result.exit_code == 0
    assert isinstance(result.elapsed_seconds, float)
    assert result.elapsed_seconds >= 0.0
    assert log["argv"] == argv
    assert log["stdout"] == "FFMPEG_STDOUT_SENTINEL"
    assert log["stderr"] == "FFMPEG_STDERR_SENTINEL"
    assert log["exit_code"] == 0
    assert log["elapsed_seconds"] == result.elapsed_seconds
