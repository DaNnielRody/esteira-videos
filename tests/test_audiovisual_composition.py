"""Public contract for canonical audiovisual FFmpeg composition."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

from video_pipeline.validation import AudioVisualValidationResult
from video_pipeline.video import CompositionProfile, FFmpegComposer
from video_pipeline.video import _write_json as _write_video_json


@dataclass(frozen=True, slots=True)
class ProcessFacts:
    returncode: int
    stdout: str
    stderr: str


class FakeFFmpeg:
    """Write only the injected temporary output; never invoke real FFmpeg."""

    def __init__(self, *, returncode: int) -> None:
        self.returncode = returncode
        self.calls: list[tuple[list[str], float, bool, bool]] = []

    def __call__(
        self,
        argv: Sequence[str],
        *,
        capture_output: bool,
        text: bool,
        timeout: float,
        check: bool,
    ) -> ProcessFacts:
        arguments = list(argv)
        self.calls.append((arguments, timeout, capture_output, check))
        Path(arguments[-1]).write_bytes(b"COMPOSED_FINAL_BYTES")
        return ProcessFacts(
            returncode=self.returncode,
            stdout="fake ffmpeg stdout",
            stderr="fake ffmpeg stderr" if self.returncode else "",
        )


class FakeFinalValidator:
    """Return a strict final-validation result for the temporary candidate."""

    def __init__(self, *, valid: bool) -> None:
        self.valid = valid
        self.calls: list[Path] = []
        self.results: list[AudioVisualValidationResult] = []

    def validate(self, path: str | Path) -> AudioVisualValidationResult:
        candidate = Path(path)
        self.calls.append(candidate)
        result = AudioVisualValidationResult(
            path=candidate,
            valid=self.valid,
            reasons=[] if self.valid else ["final audiovisual validation failed"],
            video_duration_seconds=10.0,
            audio_duration_seconds=10.0,
            expected_duration_seconds=10.0,
            video_drift_seconds=0.0,
            audio_drift_seconds=0.0,
            audio_video_drift_seconds=0.0,
            size_bytes=candidate.stat().st_size,
            probe_size_bytes=candidate.stat().st_size,
            raw_probe={"fake": True},
            probe_returncode=0,
        )
        self.results.append(result)
        return result


def test_composition_scenes_list_preserves_previous_file_on_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed scenes-list publication leaves its previous contents intact."""

    destination = tmp_path / "persistent.json"
    destination.write_bytes(b"PREVIOUS_JSON_BYTES")

    def fail_replace(self: Path, target: str | Path) -> Path:
        del self, target
        raise OSError("injected replace failure")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="injected replace failure"):
        _write_video_json(destination, {"state": "new"})

    assert destination.read_bytes() == b"PREVIOUS_JSON_BYTES"
    assert not list(tmp_path.glob(f".{destination.name}.*.tmp"))


def test_composer_scenes_list_uses_atomic_replace_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The composer must not partially rewrite an existing scenes.txt file."""

    first_scene = tmp_path / "scene-01.mp4"
    narration = tmp_path / "narration.wav"
    first_scene.write_bytes(b"SCENE_BYTES")
    narration.write_bytes(b"NARRATION_BYTES")
    list_path = tmp_path / "scenes.txt"
    list_path.write_bytes(b"PREVIOUS_SCENES_BYTES")
    original_replace = Path.replace

    def fail_scenes_replace(self: Path, target: str | Path) -> Path:
        if Path(target) == list_path:
            raise OSError("injected scenes replace failure")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_scenes_replace)
    profile = CompositionProfile(
        resolution=(854, 480),
        fps=15,
        timebase=90_000,
        pixel_format="yuv420p",
    )
    with pytest.raises(OSError, match="injected scenes replace failure"):
        FFmpegComposer(subprocess_run=FakeFFmpeg(returncode=0)).compose(
            [first_scene],
            narration,
            tmp_path / "final.mp4",
            expected_duration_seconds=10.0,
            profile=profile,
            validator=FakeFinalValidator(valid=True),
        )

    assert list_path.read_bytes() == b"PREVIOUS_SCENES_BYTES"
    assert not list(tmp_path.glob(".scenes.txt.*.tmp"))


@pytest.mark.parametrize(
    ("returncode", "validator_valid", "expect_success"),
    [
        pytest.param(0, True, True, id="validated-publish"),
        pytest.param(1, True, False, id="ffmpeg-failure"),
        pytest.param(0, False, False, id="validator-failure"),
    ],
)
def test_ffmpeg_composer_maps_original_narration_and_publishes_atomically(
    tmp_path: Path,
    returncode: int,
    validator_valid: bool,
    expect_success: bool,
) -> None:
    """Compose ordered normalized scenes with narration only after validation."""

    first_scene = tmp_path / "scene-01.mp4"
    second_scene = tmp_path / "scene-02.mp4"
    narration = tmp_path / "narration.wav"
    first_scene.write_bytes(b"SCENE_01_BYTES")
    second_scene.write_bytes(b"SCENE_02_BYTES")
    narration.write_bytes(b"IMMUTABLE_NARRATION_BYTES")
    original_bytes = {
        first_scene: first_scene.read_bytes(),
        second_scene: second_scene.read_bytes(),
        narration: narration.read_bytes(),
    }
    output = tmp_path / "final.mp4"
    ffmpeg = FakeFFmpeg(returncode=returncode)
    validator = FakeFinalValidator(valid=validator_valid)
    profile = CompositionProfile(
        resolution=(854, 480),
        fps=15,
        timebase=90_000,
        pixel_format="yuv420p",
    )

    result = FFmpegComposer(subprocess_run=ffmpeg).compose(
        [first_scene, second_scene],
        narration,
        output,
        expected_duration_seconds=10.0,
        profile=profile,
        validator=validator,
    )

    assert result.exit_code == returncode
    assert result.stdout == "fake ffmpeg stdout"
    assert result.stderr == ("fake ffmpeg stderr" if returncode else "")
    assert result.elapsed_seconds >= 0.0
    assert result.log_path == tmp_path / "composition.json"
    assert result.log_path.is_file()
    logged = json.loads(result.log_path.read_text(encoding="utf-8"))
    assert logged["argv"] == result.argv
    assert logged["stdout"] == "fake ffmpeg stdout"
    assert logged["stderr"] == result.stderr
    assert logged["exit_code"] == returncode
    assert logged["elapsed_seconds"] == result.elapsed_seconds
    if returncode != 0:
        assert logged["validation"] is None
    else:
        assert logged["validation"]["valid"] is validator_valid

    assert first_scene.read_bytes() == original_bytes[first_scene]
    assert second_scene.read_bytes() == original_bytes[second_scene]
    assert narration.read_bytes() == original_bytes[narration]
    assert (tmp_path / "scenes.txt").read_text(encoding="utf-8") == (
        f"file '{first_scene.resolve()}'\nfile '{second_scene.resolve()}'\n"
    )

    if expect_success:
        assert result.output_path == output
        assert output.read_bytes() == b"COMPOSED_FINAL_BYTES"
        assert len(validator.calls) == 1
        assert validator.calls[0] != output
        assert not list(tmp_path.glob(".final.mp4.*.tmp"))
        assert result.validation is not validator.results[0]
        assert result.validation is not None
        assert result.validation.path == output
        assert validator.results[0].path != output
        assert logged["validation"]["path"] == str(output)
        argv = result.argv
        assert argv.count(str(narration.resolve())) == 1
        assert argv[argv.index("-map") + 1] == "0:v:0"
        second_map = argv.index("-map", argv.index("-map") + 1)
        assert argv[second_map + 1] == "1:a:0"
        assert argv[argv.index("-c:v") + 1] == "libx264"
        assert argv[argv.index("-c:a") + 1] == "aac"
        assert argv[argv.index("-pix_fmt") + 1] == "yuv420p"
        assert argv[argv.index("-s") + 1] == "854x480"
        assert argv[argv.index("-r") + 1] == "15"
        assert argv[argv.index("-video_track_timescale") + 1] == "90000"
        assert argv[argv.index("-t") + 1] == "10.000000"
        assert "+faststart" in argv
        assert "-shortest" not in argv
        assert "-c" not in argv
    else:
        assert result.output_path is None
        assert not output.exists()
        assert not list(tmp_path.glob(".final.mp4.*.tmp"))
        if returncode != 0:
            assert validator.calls == []
        else:
            assert len(validator.calls) == 1
            assert result.error == "final audiovisual validation failed"
