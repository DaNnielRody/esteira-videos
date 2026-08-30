"""Behavioral tests for independent MP4 validation through ffprobe."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

try:
    from video_pipeline.validation import RenderValidator
except (ImportError, ModuleNotFoundError):  # pragma: no cover - RED shim
    _CONTRACT_IMPORT_ERROR = True
    RenderValidator = None  # type: ignore[assignment,misc]
else:
    _CONTRACT_IMPORT_ERROR = False


class CompletedProbe:
    """Small ffprobe result with only the process facts the validator consumes."""

    def __init__(self, *, returncode: int, stdout: str, stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class RecordingFfprobe:
    """Operation-specific fake for the external ffprobe boundary."""

    def __init__(self, result: CompletedProbe) -> None:
        self.result = result
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(
        self,
        argv: Sequence[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> CompletedProbe:
        self.calls.append(
            (
                list(argv),
                {
                    "capture_output": capture_output,
                    "text": text,
                    "check": check,
                },
            )
        )
        return self.result


def _require_contract() -> None:
    if _CONTRACT_IMPORT_ERROR:
        pytest.fail("RENDER_VALIDATOR_CONTRACT_MISSING")


def _mp4(tmp_path: Path, name: str = "candidate.mp4", contents: bytes = b"not empty") -> Path:
    path = tmp_path / name
    path.write_bytes(contents)
    return path


def _validator(probe: RecordingFfprobe) -> RenderValidator:
    _require_contract()
    return RenderValidator(ffprobe_run=probe)


def test_render_validator_rejects_missing_mp4_without_claiming_success(
    tmp_path: Path,
) -> None:
    """A missing candidate is invalid even when no process could be probed."""

    _require_contract()
    probe = RecordingFfprobe(CompletedProbe(returncode=0, stdout="{}"))
    result = _validator(probe).validate(tmp_path / "missing.mp4")

    assert result.valid is False
    assert any("missing" in reason.lower() for reason in result.reasons)
    assert probe.calls == []


def test_render_validator_rejects_empty_mp4_before_metadata_can_pass(
    tmp_path: Path,
) -> None:
    """A zero-byte artifact cannot satisfy the independent media gate."""

    path = _mp4(tmp_path, contents=b"")
    probe = RecordingFfprobe(CompletedProbe(returncode=0, stdout="{}"))
    result = _validator(probe).validate(path)

    assert result.valid is False
    assert any("empty" in reason.lower() or "size" in reason.lower() for reason in result.reasons)
    assert probe.calls == []


def test_render_validator_rejects_corrupt_mp4_when_ffprobe_fails(
    tmp_path: Path,
) -> None:
    """Non-empty bytes are still invalid when ffprobe cannot parse them."""

    path = _mp4(tmp_path, contents=b"CORRUPT_MP4_SENTINEL")
    probe = RecordingFfprobe(
        CompletedProbe(
            returncode=1,
            stdout="",
            stderr="moov atom not found",
        )
    )
    result = _validator(probe).validate(path)

    assert result.valid is False
    assert any(
        "probe" in reason.lower() or "corrupt" in reason.lower()
        for reason in result.reasons
    )


def test_render_validator_rejects_probe_results_without_a_video_stream(
    tmp_path: Path,
) -> None:
    """An audio-only or otherwise streamless artifact is not a video."""

    path = _mp4(tmp_path)
    probe = RecordingFfprobe(
        CompletedProbe(
            returncode=0,
            stdout=json.dumps(
                {
                    "streams": [
                        {"codec_type": "audio", "duration": "1.0"},
                    ],
                    "format": {"duration": "1.0", "size": str(path.stat().st_size)},
                }
            ),
        )
    )
    result = _validator(probe).validate(path)

    assert result.valid is False
    assert any("video" in reason.lower() or "stream" in reason.lower() for reason in result.reasons)


def test_render_validator_rejects_zero_duration_even_with_video_dimensions(
    tmp_path: Path,
) -> None:
    """Duration zero is invalid, explicitly covering the non-positive boundary."""

    path = _mp4(tmp_path)
    probe = RecordingFfprobe(
        CompletedProbe(
            returncode=0,
            stdout=json.dumps(
                {
                    "streams": [
                        {"codec_type": "video", "width": 854, "height": 480},
                    ],
                    "format": {"duration": "0", "size": str(path.stat().st_size)},
                }
            ),
        )
    )
    result = _validator(probe).validate(path)

    assert result.valid is False
    assert any("duration" in reason.lower() for reason in result.reasons)


def test_render_validator_accepts_positive_video_stream_metadata(
    tmp_path: Path,
) -> None:
    """A newly probed video needs positive stream dimensions, duration, and size."""

    path = _mp4(tmp_path, contents=b"VALID_MP4_BYTES")
    probe = RecordingFfprobe(
        CompletedProbe(
            returncode=0,
            stdout=json.dumps(
                {
                    "streams": [
                        {
                            "codec_type": "video",
                            "width": 854,
                            "height": 480,
                            "duration": "1.25",
                        }
                    ],
                    "format": {"duration": "1.25", "size": "42"},
                }
            ),
        )
    )
    result = _validator(probe).validate(path)

    assert result.valid is True
    assert result.width == 854
    assert result.height == 480
    assert result.duration_seconds == pytest.approx(1.25)
    assert result.size_bytes == 42
    assert probe.calls
    argv, kwargs = probe.calls[0]
    assert str(path) in argv
    assert kwargs.get("capture_output") is True
    assert kwargs.get("text") is True


def test_validation_audit_contract() -> None:
    """Inventory the validation contract tests without product calls."""

    behavioral_tests = (
        "test_render_validator_rejects_missing_mp4_without_claiming_success",
        "test_render_validator_rejects_empty_mp4_before_metadata_can_pass",
        "test_render_validator_rejects_corrupt_mp4_when_ffprobe_fails",
        "test_render_validator_rejects_probe_results_without_a_video_stream",
        "test_render_validator_rejects_zero_duration_even_with_video_dimensions",
        "test_render_validator_accepts_positive_video_stream_metadata",
    )

    assert all(callable(globals().get(name)) for name in behavioral_tests)
