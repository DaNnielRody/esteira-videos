"""Public contract for the final audiovisual ffprobe validation gate."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import pytest

import video_pipeline.validation as validation_module
from video_pipeline.validation import AudioVisualValidationResult, FinalAudioVisualValidator


@dataclass(frozen=True, slots=True)
class ProbeFacts:
    returncode: int
    stdout: str
    stderr: str = ""


class FakeFfprobe:
    """Injected ffprobe process boundary for final-media facts."""

    def __init__(self, result: ProbeFacts) -> None:
        self.result = result
        self.calls: list[tuple[list[str], bool, bool, bool]] = []

    def __call__(
        self,
        argv: Sequence[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> ProbeFacts:
        self.calls.append((list(argv), capture_output, text, check))
        return self.result


def _video(**overrides: object) -> dict[str, object]:
    stream: dict[str, object] = {
        "index": 0,
        "codec_type": "video",
        "codec_name": "h264",
        "width": 854,
        "height": 480,
        "pix_fmt": "yuv420p",
        "r_frame_rate": "15/1",
        "avg_frame_rate": "15/1",
        "time_base": "1/90000",
        "duration": "10.0",
    }
    stream.update(overrides)
    return stream


def _audio(**overrides: object) -> dict[str, object]:
    stream: dict[str, object] = {
        "index": 1,
        "codec_type": "audio",
        "codec_name": "aac",
        "sample_rate": "48000",
        "channels": 2,
        "time_base": "1/48000",
        "duration": "10.0",
    }
    stream.update(overrides)
    return stream


def _document(
    *,
    streams: list[dict[str, object]] | None = None,
    format_duration: str = "10.0",
) -> dict[str, object]:
    return {
        "streams": streams if streams is not None else [_video(), _audio()],
        "format": {
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            "duration": format_duration,
            "size": "42",
        },
    }


@dataclass(frozen=True, slots=True)
class ValidationCase:
    document: dict[str, object]
    expected_valid: bool
    reason_fragment: str
    expected_duration_seconds: float = 10.0
    returncode: int = 0
    stderr: str = ""
    present: bool = True
    contents: bytes = b"FINAL_MP4_BYTES"
    expected_video_drift_seconds: float | None = None
    expected_audio_drift_seconds: float | None = None


_VALID = _document()


def test_validation_exposes_only_canonical_audiovisual_names() -> None:
    """The validator API has one result and one validator name."""

    assert "AudioVisualValidationResult" in validation_module.__all__
    assert "FinalAudioVisualValidator" in validation_module.__all__
    for legacy_name in (
        "AudioVisualValidator",
        "AudiovisualValidator",
        "AudiovisualValidationResult",
    ):
        assert not hasattr(validation_module, legacy_name)
        assert legacy_name not in validation_module.__all__


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            ValidationCase(
                _VALID,
                True,
                "",
                expected_video_drift_seconds=0.0,
                expected_audio_drift_seconds=0.0,
            ),
            id="valid-final-mp4",
        ),
        pytest.param(
            ValidationCase(_document(streams=[_audio()]), False, "exactly one video"),
            id="zero-video-streams",
        ),
        pytest.param(
            ValidationCase(
                _document(streams=[_video(), _video(index=2), _audio()]),
                False,
                "exactly one video",
            ),
            id="two-video-streams",
        ),
        pytest.param(
            ValidationCase(_document(streams=[_video()]), False, "audio"),
            id="no-audio-stream",
        ),
        pytest.param(
            ValidationCase(_document(streams=[_video(width=0), _audio()]), False, "width"),
            id="nonpositive-dimensions",
        ),
        pytest.param(
            ValidationCase(
                _document(
                    streams=[_video(duration="0"), _audio(duration="0")],
                    format_duration="0",
                ),
                False,
                "duration",
            ),
            id="nonpositive-duration",
        ),
        pytest.param(
            ValidationCase(_document(streams=[_video(codec_name="vp9"), _audio()]), False, "h264"),
            id="wrong-video-codec",
        ),
        pytest.param(
            ValidationCase(_document(streams=[_video(), _audio(codec_name="opus")]), False, "aac"),
            id="wrong-audio-codec",
        ),
        pytest.param(
            ValidationCase(
                _document(streams=[_video(pix_fmt="yuv444p"), _audio()]),
                False,
                "yuv420p",
            ),
            id="wrong-pixel-format",
        ),
        pytest.param(
            ValidationCase(_document(streams=[_video(width=1280), _audio()]), False, "resolution"),
            id="resolution-mismatch",
        ),
        pytest.param(
            ValidationCase(
                _document(
                    streams=[
                        _video(r_frame_rate="30/1", avg_frame_rate="30/1"),
                        _audio(),
                    ]
                ),
                False,
                "FPS",
            ),
            id="fps-mismatch",
        ),
        pytest.param(
            ValidationCase(
                _document(streams=[_video(time_base="1/1000"), _audio()]),
                False,
                "timebase",
            ),
            id="timebase-mismatch",
        ),
        pytest.param(
            ValidationCase(
                _document(streams=[_video(), _audio(duration="10.3")]),
                False,
                "audio duration drift",
                expected_audio_drift_seconds=0.3,
            ),
            id="excessive-audio-drift",
        ),
        pytest.param(
            ValidationCase(
                _document(streams=[_video(duration="10.3"), _audio()]),
                False,
                "video duration drift",
                expected_video_drift_seconds=0.3,
            ),
            id="excessive-video-drift",
        ),
        pytest.param(
            ValidationCase(
                _document(),
                False,
                "expected duration drift",
                expected_duration_seconds=10.3,
                expected_video_drift_seconds=-0.3,
                expected_audio_drift_seconds=-0.3,
            ),
            id="excessive-expected-duration-drift",
        ),
        pytest.param(
            ValidationCase(_document(), False, "empty", contents=b""),
            id="empty-file",
        ),
        pytest.param(
            ValidationCase(_document(), False, "missing", present=False),
            id="missing-file",
        ),
        pytest.param(
            ValidationCase(
                _document(),
                False,
                "ffprobe failed",
                returncode=1,
                stderr="invalid atom",
            ),
            id="nonzero-probe",
        ),
    ],
)
def test_final_audiovisual_validator_enforces_strict_contract(
    tmp_path: Path,
    case: ValidationCase,
) -> None:
    """Final validation keeps media facts while rejecting unsafe audiovisual output."""

    path = tmp_path / "final.mp4"
    if case.present:
        path.write_bytes(case.contents)

    fake = FakeFfprobe(
        ProbeFacts(
            returncode=case.returncode,
            stdout=json.dumps(case.document),
            stderr=case.stderr,
        )
    )
    validator = FinalAudioVisualValidator(
        ffprobe_run=fake,
        expected_duration_seconds=case.expected_duration_seconds,
        expected_resolution=(854, 480),
        expected_fps=15,
        expected_timebase="1/90000",
        duration_tolerance_seconds=0.05,
    )

    result = validator.validate(path)

    assert isinstance(result, AudioVisualValidationResult)
    assert result.valid is case.expected_valid
    if case.reason_fragment:
        assert any(case.reason_fragment.lower() in reason.lower() for reason in result.reasons)

    if case.expected_video_drift_seconds is not None:
        assert result.video_drift_seconds == pytest.approx(case.expected_video_drift_seconds)
    if case.expected_audio_drift_seconds is not None:
        assert result.audio_drift_seconds == pytest.approx(case.expected_audio_drift_seconds)

    if case.expected_valid:
        assert result.video_stream is not None
        assert result.video_stream.codec_name == "h264"
        assert result.video_stream.width == 854
        assert result.video_stream.height == 480
        assert len(result.audio_streams) == 1
        assert result.audio_streams[0].codec_name == "aac"
        assert result.video_duration_seconds == pytest.approx(10.0)
        assert result.audio_duration_seconds == pytest.approx(10.0)
        assert result.audio_video_drift_seconds == pytest.approx(0.0)
        assert result.size_bytes == len(case.contents)
        assert result.probe_size_bytes == 42
        assert result.raw_probe == case.document

    if case.present and case.contents:
        assert len(fake.calls) == 1
        argv, capture_output, text, check = fake.calls[0]
        assert str(path) in argv
        assert capture_output is True
        assert text is True
        assert check is False
    else:
        assert fake.calls == []
