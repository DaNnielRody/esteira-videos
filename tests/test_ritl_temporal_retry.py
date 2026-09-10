"""Public contract for temporal failures re-entering the RITL correction loop."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from test_project_render import FakeAudioProbe, FakeObserver

from video_pipeline.cli import main
from video_pipeline.provider import ProviderRequest, ProviderResponse, UnloadResult
from video_pipeline.rendering import RenderResult
from video_pipeline.temporal import TemporalNormalizationResult, TemporalTolerances, normalize_scene
from video_pipeline.validation import AudioVisualValidationResult, ValidationResult
from video_pipeline.video import CompositionProfile, CompositionResult


class RitlProvider:
    """Return two code candidates so temporal evidence can drive correction."""

    def __init__(self, project_json: Path) -> None:
        self.project_json = project_json
        self.requests: list[ProviderRequest] = []
        self.codes = (
            "from manim import Scene\n\nclass AberturaScene(Scene):\n"
            "    def construct(self):\n        pass\n",
            "from manim import Scene\n\nclass AberturaScene(Scene):\n"
            "    def construct(self):\n        self.wait(1)\n",
        )

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        document = json.loads(self.project_json.read_text(encoding="utf-8"))
        assert document["status"] == "rendering"
        return ProviderResponse(
            code=self.codes[len(self.requests) - 1],
            raw_response={"attempt": len(self.requests)},
        )

    def unload(self) -> UnloadResult:
        return UnloadResult(ok=True, raw_response={"fake": True})


class RitlRunner:
    """Write one distinct raw candidate for each provider attempt."""

    def __init__(self) -> None:
        self.calls: list[Path] = []

    def run(self, scene_path: str | Path, media_dir: str | Path) -> RenderResult:
        scene = Path(scene_path)
        self.calls.append(scene)
        output = Path(media_dir) / f"raw-{len(self.calls)}.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(f"RAW_{len(self.calls)}".encode("utf-8"))
        return RenderResult(
            argv=["fake-manim", str(scene)],
            exit_code=0,
            timed_out=False,
            missing_executable=False,
            stdout=f"render-{len(self.calls)}",
            stderr="",
            elapsed_seconds=0.01,
            mp4_paths=[output],
        )


class RitlRawValidator:
    """Report valid visual media with a large then small temporal delta."""

    def __init__(self) -> None:
        self.calls: list[Path] = []
        self.durations = iter((5.0, 3.92))

    def validate(self, path: str | Path) -> ValidationResult:
        candidate = Path(path)
        self.calls.append(candidate)
        return ValidationResult(
            path=candidate,
            valid=True,
            reasons=[],
            width=854,
            height=480,
            duration_seconds=next(self.durations),
            size_bytes=candidate.stat().st_size,
        )


class RitlNormalizedValidator:
    """Accept the deterministic normalized candidate at the target duration."""

    def __init__(self) -> None:
        self.calls: list[Path] = []

    def validate(self, path: str | Path) -> ValidationResult:
        candidate = Path(path)
        self.calls.append(candidate)
        return ValidationResult(
            path=candidate,
            valid=True,
            reasons=[],
            width=854,
            height=480,
            duration_seconds=4.0,
            size_bytes=candidate.stat().st_size,
        )


class RitlFFmpeg:
    """Copy the normalized candidate through the temporal FFmpeg boundary."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(
        self,
        argv: Sequence[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> object:
        assert capture_output is True
        assert text is True
        assert check is False
        command = list(argv)
        self.calls.append(command)
        Path(command[-1]).write_bytes(b"NORMALIZED_SCENE")
        return type(
            "CompletedTemporalProcess",
            (),
            {
                "returncode": 0,
                "stdout": "temporal stdout",
                "stderr": "",
            },
        )()


class RitlTemporalNormalizer:
    """Use the canonical temporal function with one injected FFmpeg fake."""

    def __init__(self, validator: RitlNormalizedValidator) -> None:
        self.validator = validator
        self.tolerances = TemporalTolerances(
            acceptance_seconds=0.05,
            correction_limit_seconds=0.5,
        )
        self.ffmpeg = RitlFFmpeg()
        self.results: list[TemporalNormalizationResult] = []

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
        validator: object | None = None,
    ) -> TemporalNormalizationResult:
        assert validator is self.validator
        result = normalize_scene(
            raw_path,
            normalized_path=normalized_path,
            observed_duration_seconds=observed_duration_seconds,
            target_duration_seconds=target_duration_seconds,
            target_resolution=target_resolution,
            target_fps=target_fps,
            target_timebase=target_timebase,
            target_pixel_format=target_pixel_format,
            tolerances=self.tolerances,
            ffmpeg_run=self.ffmpeg,
            validator=self.validator,
            log_path=Path(normalized_path).with_name("temporal-normalization.json"),
        )
        self.results.append(result)
        return result


class RitlFinalValidator:
    """Accept one composed four-second audiovisual artifact."""

    def validate(self, path: str | Path) -> AudioVisualValidationResult:
        candidate = Path(path)
        return AudioVisualValidationResult(
            path=candidate,
            valid=True,
            reasons=[],
            video_duration_seconds=4.0,
            audio_duration_seconds=4.0,
            expected_duration_seconds=4.0,
            video_drift_seconds=0.0,
            audio_drift_seconds=0.0,
            audio_video_drift_seconds=0.0,
            size_bytes=candidate.stat().st_size,
        )


class RitlComposer:
    """Publish a fake final output from the accepted normalized scene."""

    def __init__(self) -> None:
        self.calls: list[list[Path]] = []

    def compose(
        self,
        scene_paths: Sequence[Path],
        narration_path: Path,
        output_path: Path | None = None,
        *,
        expected_duration_seconds: float | None = None,
        profile: CompositionProfile | None = None,
        validator: object | None = None,
    ) -> CompositionResult:
        assert expected_duration_seconds == 4.0
        assert profile is not None
        assert validator is not None
        assert output_path is not None
        assert narration_path.is_file()
        self.calls.append(list(scene_paths))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"FINAL_SCENE")
        validation = validator.validate(output_path)
        return CompositionResult(
            argv=["fake-composer", *(str(path) for path in scene_paths)],
            exit_code=0,
            stdout="composer stdout",
            stderr="",
            output_path=output_path,
            elapsed_seconds=0.01,
            validation=validation,
        )


def test_temporal_delta_reenters_qwen_correction_loop_with_bounded_attempts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = tmp_path / "roteiro.md"
    script.write_text(
        "# Abertura\n"
        "@start: 0\n"
        "@end: 4\n"
        "@objective: Introduza vetores.\n"
        "Esta e a abertura exata.\n",
        encoding="utf-8",
    )
    audio = tmp_path / "narracao.wav"
    audio_bytes = b"immutable narration bytes\x00"
    audio.write_bytes(audio_bytes)
    facts = {
        "path": "audio/narration.wav",
        "hash": hashlib.sha256(audio_bytes).hexdigest(),
        "container": "wav",
        "codec": "pcm_s16le",
        "stream": 0,
        "sample_rate": 48_000,
        "channels": 2,
        "duration": 4.0,
        "size": len(audio_bytes),
        "probe_result": {"format": {}, "streams": []},
    }
    project = tmp_path / "projects" / "2026_vetores"
    assert (
        main(
            [
                "init",
                str(project),
                "--title",
                "Vetores",
                "--script",
                str(script),
                "--audio",
                str(audio),
            ],
            audio_probe=FakeAudioProbe(facts),
        )
        == 0
    )
    project_json = project / "project.json"
    provider = RitlProvider(project_json)
    runner = RitlRunner()
    raw_validator = RitlRawValidator()
    normalized_validator = RitlNormalizedValidator()
    temporal_normalizer = RitlTemporalNormalizer(normalized_validator)
    composer = RitlComposer()

    assert (
        main(
            ["render", str(project_json), "--max-attempts", "2"],
            provider=provider,
            runner=runner,
            validator=raw_validator,
            observer=FakeObserver(),
            temporal_normalizer=temporal_normalizer,
            normalized_validator=normalized_validator,
            final_validator=RitlFinalValidator(),
            composer=composer,
            id_factory=lambda: "run-001",
        )
        == 0
    )
    assert "READY" in capsys.readouterr().out

    assert len(provider.requests) == 2
    first_code = provider.codes[0]
    assert provider.requests[1].previous_code == first_code
    temporal_diagnostics = dict(provider.requests[1].diagnostics or {})["temporal"]
    assert temporal_diagnostics == {
        "observed_duration_seconds": 5.0,
        "target_duration_seconds": 4.0,
        "delta_seconds": 1.0,
        "acceptance_tolerance_seconds": 0.05,
        "correction_limit_seconds": 0.5,
        "requested_action": "regenerate the scene with the target duration",
    }
    assert [result.status for result in temporal_normalizer.results] == [
        "requires_regeneration",
        "normalized",
    ]
    assert temporal_normalizer.ffmpeg.calls and len(temporal_normalizer.ffmpeg.calls) == 1
    normalized_argv = temporal_normalizer.ffmpeg.calls[0]
    assert "tpad=stop_mode=clone" in " ".join(normalized_argv)
    assert "setpts=PTS-STARTPTS" in " ".join(normalized_argv)
    assert "yuv420p" in normalized_argv

    run_path = project / "artifacts" / "run-001"
    pipeline_path = run_path / "pipeline" / "abertura" / "run-001-01"
    attempts = sorted(path for path in pipeline_path.glob("attempt-*") if path.is_dir())
    assert [path.name for path in attempts] == ["attempt-01", "attempt-02"]
    first_attempt = attempts[0]
    first_raw = first_attempt / "media" / "raw-1.mp4"
    assert first_raw.read_bytes() == b"RAW_1"
    first_validation = json.loads(
        (first_attempt / "validation.json").read_text(encoding="utf-8")
    )
    assert first_validation["valid"] is True
    first_temporal = json.loads(
        (first_attempt / "temporal-normalization.json").read_text(encoding="utf-8")
    )
    assert first_temporal["status"] == "requires_regeneration"
    assert first_temporal["argv"] == []
    assert not (first_attempt / "media" / "normalized.mp4").exists()

    run_document = json.loads((run_path / "run.json").read_text(encoding="utf-8"))
    assert run_document["state"] == "ready"
    assert run_document["scenes"][0]["attempts"] == 2
    attempt_history = run_document["scenes"][0]["attempt_history"]
    assert [entry["attempt"] for entry in attempt_history] == [1, 2]
    assert [Path(entry["attempt_path"]).name for entry in attempt_history] == [
        "attempt-01",
        "attempt-02",
    ]
    assert [entry["state"] for entry in attempt_history] == ["failed", "ready"]
    assert attempt_history[0]["pipeline_state"] == "correcting"
    assert attempt_history[1]["pipeline_state"] == "success"
    assert attempt_history[0]["diagnostics"]["temporal"]["delta_seconds"] == 1.0
    assert (
        attempt_history[0]["diagnostics"]["temporal_normalization"]["status"]
        == "requires_regeneration"
    )
    assert (
        attempt_history[1]["diagnostics"]["temporal_normalization"]["status"]
        == "normalized"
    )
    assert (run_path / "scenes" / "01_abertura" / "raw.mp4").is_file()
    assert (run_path / "scenes" / "01_abertura" / "normalized.mp4").is_file()
    assert composer.calls == [[run_path / "scenes" / "01_abertura" / "normalized.mp4"]]
