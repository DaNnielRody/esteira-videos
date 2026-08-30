"""Public end-to-end contract for rendering a confirmed canonical project."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from video_pipeline.cli import main
from video_pipeline.observation import ObservationResult
from video_pipeline.project import Project, _project_package_hashes
from video_pipeline.provider import ProviderRequest, ProviderResponse, UnloadResult
from video_pipeline.rendering import RenderResult
from video_pipeline.scene_plan import ScenePlan
from video_pipeline.temporal import TemporalNormalizationResult
from video_pipeline.validation import (
    AudioVisualValidationResult,
    MediaStreamFacts,
    ValidationResult,
)
from video_pipeline.video import CompositionProfile, CompositionResult


class FakeAudioProbe:
    """Return deterministic facts for the staged narration boundary."""

    def __init__(self, facts: dict[str, object]) -> None:
        self.facts = facts

    def __call__(self, path: Path) -> dict[str, object]:
        assert path.name == "narration.wav"
        assert path.parent.name == "audio"
        return dict(self.facts)


class FakeProvider:
    """Generate one accepted source candidate per timeline scene."""

    def __init__(self, project_json: Path) -> None:
        self.project_json = project_json
        self.requests: list[ProviderRequest] = []
        self.states_seen: list[str] = []
        self.codes = iter(
            (
                (
                    "from manim import Scene\n\nclass AberturaScene(Scene):\n"
                    "    def construct(self):\n        pass\n"
                ),
                (
                    "from manim import Scene\n\nclass ExplicacaoScene(Scene):\n"
                    "    def construct(self):\n        pass\n"
                ),
            )
        )

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        document = json.loads(self.project_json.read_text(encoding="utf-8"))
        self.states_seen.append(document["status"])
        return ProviderResponse(code=next(self.codes), raw_response={"fake": True})

    def unload(self) -> UnloadResult:
        return UnloadResult(ok=True, raw_response={"fake": True})


class FakeManimRunner:
    """Write deterministic raw scene media without invoking Manim."""

    def __init__(self) -> None:
        self.scene_paths: list[Path] = []

    def run(self, scene_path: str | Path, media_dir: str | Path) -> RenderResult:
        scene = Path(scene_path)
        self.scene_paths.append(scene)
        output = Path(media_dir) / "raw.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(f"raw:{scene.stem}".encode("utf-8"))
        return RenderResult(
            argv=["fake-manim", str(scene)],
            exit_code=0,
            timed_out=False,
            missing_executable=False,
            stdout="fake render stdout",
            stderr="",
            elapsed_seconds=0.01,
            mp4_paths=[output],
        )


class FakeRawValidator:
    """Accept each raw candidate while preserving measured duration facts."""

    def __init__(self) -> None:
        self.calls: list[Path] = []

    def validate(self, path: str | Path) -> ValidationResult:
        candidate = Path(path)
        self.calls.append(candidate)
        duration = 4.0 if len(self.calls) == 1 else 6.0
        return ValidationResult(
            path=candidate,
            valid=True,
            reasons=[],
            width=854,
            height=480,
            duration_seconds=duration,
            size_bytes=candidate.stat().st_size,
        )


class FakeObserver:
    """Return empty semantic evidence for plans with no required objects."""

    def observe(self, mp4_path: str | Path, frames_dir: str | Path) -> ObservationResult:
        del mp4_path
        Path(frames_dir).mkdir(parents=True, exist_ok=True)
        return ObservationResult.success([])


class FakeNormalizedValidator:
    """Revalidate each staged normalized scene through an injected boundary."""

    def __init__(self) -> None:
        self.calls: list[Path] = []

    def validate(self, path: str | Path) -> ValidationResult:
        candidate = Path(path)
        self.calls.append(candidate)
        duration = 4.0 if len(self.calls) == 1 else 6.0
        return ValidationResult(
            path=candidate,
            valid=True,
            reasons=[],
            width=854,
            height=480,
            duration_seconds=duration,
            size_bytes=candidate.stat().st_size,
        )


class FakeTemporalNormalizer:
    """Copy raw media to a distinct accepted normalized artifact."""

    def __init__(self, validator: FakeNormalizedValidator) -> None:
        self.validator = validator
        self.calls: list[tuple[Path, Path]] = []

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
        del target_resolution, target_fps, target_timebase, target_pixel_format
        assert validator is self.validator
        raw = Path(raw_path)
        normalized = Path(normalized_path)
        self.calls.append((raw, normalized))
        normalized.parent.mkdir(parents=True, exist_ok=True)
        normalized.write_bytes(b"normalized:" + raw.read_bytes())
        validated = self.validator.validate(normalized)
        assert validated.duration_seconds is not None
        log_path = normalized.with_name("normalization.json")
        return TemporalNormalizationResult(
            status="normalized",
            raw_path=raw,
            normalized_path=normalized,
            log_path=log_path,
            observed_duration_seconds=observed_duration_seconds,
            target_duration_seconds=target_duration_seconds,
            delta_seconds=observed_duration_seconds - target_duration_seconds,
            argv=["fake-normalizer", str(raw), str(normalized)],
            stdout="fake normalization stdout",
            stderr="",
            exit_code=0,
            elapsed_seconds=0.02,
            validated_duration_seconds=validated.duration_seconds,
            validation_reasons=validated.reasons,
        )


class FakeFinalValidator:
    """Accept the composed audiovisual output and retain strict facts."""

    def __init__(self) -> None:
        self.calls: list[Path] = []

    def validate(self, path: str | Path) -> AudioVisualValidationResult:
        candidate = Path(path)
        self.calls.append(candidate)
        size_bytes = candidate.stat().st_size
        video_stream = MediaStreamFacts(
            index=0,
            codec_type="video",
            codec_name="h264",
            width=854,
            height=480,
            pixel_format="yuv420p",
            frame_rate=15.0,
            time_base="1/90000",
            time_base_seconds=1 / 90_000,
            duration_seconds=10.0,
            sample_rate_hz=None,
            channels=None,
            raw={
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "width": 854,
                "height": 480,
                "pix_fmt": "yuv420p",
                "avg_frame_rate": "15/1",
                "time_base": "1/90000",
                "duration": "10.0",
            },
        )
        audio_stream = MediaStreamFacts(
            index=1,
            codec_type="audio",
            codec_name="aac",
            width=None,
            height=None,
            pixel_format=None,
            frame_rate=None,
            time_base="1/48000",
            time_base_seconds=1 / 48_000,
            duration_seconds=10.0,
            sample_rate_hz=48_000,
            channels=2,
            raw={
                "index": 1,
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "48000",
                "channels": 2,
                "time_base": "1/48000",
                "duration": "10.0",
            },
        )
        return AudioVisualValidationResult(
            path=candidate,
            valid=True,
            reasons=[],
            video_streams=(video_stream,),
            audio_streams=(audio_stream,),
            video_stream=video_stream,
            video_duration_seconds=10.0,
            audio_duration_seconds=10.0,
            expected_duration_seconds=10.0,
            video_drift_seconds=0.0,
            audio_drift_seconds=0.0,
            audio_video_drift_seconds=0.0,
            size_bytes=size_bytes,
            probe_size_bytes=size_bytes,
            raw_probe={
                "format": {"size": str(size_bytes), "duration": "10.0"},
                "streams": [video_stream.raw, audio_stream.raw],
            },
            probe_returncode=0,
        )


class FakeComposer:
    """Publish a fake final MP4 only from normalized scenes and narration."""

    def __init__(self) -> None:
        self.scene_paths: list[Path] = []
        self.narration_path: Path | None = None
        self.validator_calls = 0

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
        assert output_path is not None
        assert expected_duration_seconds == 10.0
        assert profile is not None
        assert validator is not None
        self.scene_paths = list(scene_paths)
        self.narration_path = narration_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake-final-mp4")
        final_validation = validator.validate(output_path)
        self.validator_calls += 1
        return CompositionResult(
            argv=["fake-ffmpeg", *(str(path) for path in self.scene_paths), str(narration_path)],
            exit_code=0,
            stdout="fake composition stdout",
            stderr="",
            output_path=output_path,
            elapsed_seconds=0.03,
            validation=final_validation,
        )


class MissingValidationComposer(FakeComposer):
    """Publish a valid final while leaving validation to the pipeline fallback."""

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
        result = super().compose(
            scene_paths,
            narration_path,
            output_path,
            expected_duration_seconds=expected_duration_seconds,
            profile=profile,
            validator=validator,
        )
        return replace(result, validation=None)


class DivergentValidationComposer(FakeComposer):
    """Return a published output with validation facts for another path."""

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
        result = super().compose(
            scene_paths,
            narration_path,
            output_path,
            expected_duration_seconds=expected_duration_seconds,
            profile=profile,
            validator=validator,
        )
        assert result.validation is not None
        assert output_path is not None
        return replace(
            result,
            validation=replace(
                result.validation,
                path=output_path.with_name("divergent-final.mp4"),
            ),
        )


def test_render_confirmed_project_runs_canonical_pipeline_with_fakes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = tmp_path / "roteiro.md"
    script.write_text(
        "# Abertura\n"
        "@start: 0\n"
        "@end: 4\n"
        "@objective: Introduza vetores.\n"
        "Esta e a abertura exata.\n\n"
        "## Explicacao\n"
        "@start: 4\n"
        "@end: 10\n"
        "@objective: Explique a soma.\n"
        "Esta e a explicacao exata.\n",
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
        "duration": 10.0,
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
    for scene_ref in json.loads(project_json.read_text(encoding="utf-8"))["scenes"]:
        plan_path = project / scene_ref["plan_path"]
        plan = ScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
        plan_path.write_text(
            json.dumps(
                plan.model_copy(update={"capabilities": ["basic_geometry"]}).to_document(),
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    provider = FakeProvider(project_json)
    runner = FakeManimRunner()
    raw_validator = FakeRawValidator()
    observer = FakeObserver()
    normalized_validator = FakeNormalizedValidator()
    normalizer = FakeTemporalNormalizer(normalized_validator)
    final_validator = FakeFinalValidator()
    composer = FakeComposer()

    exit_code = main(
        ["render", str(project_json), "--max-attempts", "1"],
        provider=provider,
        runner=runner,
        validator=raw_validator,
        observer=observer,
        temporal_normalizer=normalizer,
        normalized_validator=normalized_validator,
        final_validator=final_validator,
        composer=composer,
        id_factory=lambda: "run-001",
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "READY" in output
    assert "final.mp4" in output

    project_document = json.loads(project_json.read_text(encoding="utf-8"))
    assert project_document["status"] == "ready"
    assert project_document["current_run"] == "run-001"
    assert project_document["accepted_run"] is None
    assert project_document["render_state"] == "ready"
    assert project_document["composition_state"] == "ready"
    assert provider.states_seen == ["rendering", "rendering"]

    assert [request.narration_text for request in provider.requests] == [
        "Esta e a abertura exata.",
        "Esta e a explicacao exata.",
    ]
    assert [
        (request.start_seconds, request.end_seconds, request.target_duration_seconds)
        for request in provider.requests
    ] == [(0.0, 4.0, 4.0), (4.0, 10.0, 6.0)]
    assert [request.objective for request in provider.requests] == [
        "Introduza vetores.",
        "Explique a soma.",
    ]
    project_model = Project.model_validate_json(project_json.read_text(encoding="utf-8"))
    assert [request.theme for request in provider.requests] == [
        project_model.theme.to_document(),
        project_model.theme.to_document(),
    ]
    assert [request.capabilities for request in provider.requests] == [
        ("basic_geometry",),
        ("basic_geometry",),
    ]
    assert provider.requests[0].previous_scene is None
    assert provider.requests[0].next_scene == {
        "id": "explicacao",
        "start_seconds": 4.0,
        "end_seconds": 10.0,
    }
    assert provider.requests[1].previous_scene == {
        "id": "abertura",
        "start_seconds": 0.0,
        "end_seconds": 4.0,
    }
    assert provider.requests[1].next_scene is None
    assert [request.resolution for request in provider.requests] == [(854, 480)] * 2
    assert [request.fps for request in provider.requests] == [15, 15]

    run_path = project / "artifacts" / "run-001"
    run_document = json.loads((run_path / "run.json").read_text(encoding="utf-8"))
    assert run_document["state"] == "ready"
    assert run_document["final_sha256"] == hashlib.sha256(
        (run_path / "final.mp4").read_bytes()
    ).hexdigest()
    assert run_document["final_size_bytes"] == (run_path / "final.mp4").stat().st_size
    assert "accept" in run_document["action_next"].lower()
    assert list(run_document["package_hashes"]) == sorted(run_document["package_hashes"])
    assert run_document["package_hashes"] == _project_package_hashes(
        project,
        Project.model_validate_json(project_json.read_text(encoding="utf-8")),
    )
    assert [scene["id"] for scene in run_document["scenes"]] == [
        "abertura",
        "explicacao",
    ]
    assert all(scene["attempts"] for scene in run_document["scenes"])
    assert all(scene["diagnostics"] for scene in run_document["scenes"])
    assert all(scene["code_path"].endswith("/scene.py") for scene in run_document["scenes"])
    for scene_record, scene_ref in zip(
        run_document["scenes"],
        project_document["scenes"],
        strict=True,
    ):
        run_scene = run_path / scene_ref["path"]
        assert Path(scene_record["code_path"]) == run_scene / "scene.py"
        assert Path(scene_record["provenance_path"]) == (
            run_scene / "code-provenance.json"
        )
        assert scene_record["code_sha256"] == hashlib.sha256(
            (run_scene / "scene.py").read_bytes()
        ).hexdigest()

    assert len(normalizer.calls) == 2
    assert len(raw_validator.calls) == 2
    assert len(normalized_validator.calls) == 2
    assert len(final_validator.calls) == 1
    assert composer.validator_calls == 1
    assert composer.narration_path == project / "audio" / "narration.wav"
    assert [path.read_bytes() for path in composer.scene_paths] == [
        b"normalized:raw:scene",
        b"normalized:raw:scene",
    ]
    assert composer.scene_paths == [
        run_path / "scenes" / "01_abertura" / "normalized.mp4",
        run_path / "scenes" / "02_explicacao" / "normalized.mp4",
    ]
    assert (run_path / "final.mp4").read_bytes() == b"fake-final-mp4"

    for scene_ref, code in zip(
        project_document["scenes"],
        ("AberturaScene", "ExplicacaoScene"),
        strict=True,
    ):
        scene_root = project / scene_ref["path"]
        assert not (scene_root / "code.py").exists()
        assert not (scene_root / "scene.py").exists()
        assert not (scene_root / "code-provenance.json").exists()
        run_scene = run_path / scene_ref["path"]
        assert (run_scene / "scene.py").read_text(encoding="utf-8").startswith(
            "from manim import Scene\n"
        )
        provenance = json.loads(
            (run_scene / "code-provenance.json").read_text(encoding="utf-8")
        )
        assert provenance["scene_name"] == code
        assert Path(provenance["run_path"]).name == "run-001"
        assert (run_scene / "raw.mp4").is_file()
        assert (run_scene / "normalized.mp4").is_file()
        assert (run_scene / "normalization.json").is_file()
        assert run_scene / "raw.mp4" != run_scene / "normalized.mp4"


def test_render_rejects_composer_with_divergent_validation_path(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = tmp_path / "roteiro.md"
    script.write_text(
        "# Abertura\n"
        "@start: 0\n"
        "@end: 4\n"
        "@objective: Introduza vetores.\n"
        "Esta e a abertura exata.\n\n"
        "## Explicacao\n"
        "@start: 4\n"
        "@end: 10\n"
        "@objective: Explique a soma.\n"
        "Esta e a explicacao exata.\n",
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
        "duration": 10.0,
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
    for scene_ref in json.loads(project_json.read_text(encoding="utf-8"))["scenes"]:
        plan_path = project / scene_ref["plan_path"]
        plan = ScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
        plan_path.write_text(
            json.dumps(
                plan.model_copy(update={"capabilities": ["basic_geometry"]}).to_document(),
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    normalized_validator = FakeNormalizedValidator()
    assert (
        main(
            ["render", str(project_json), "--max-attempts", "1"],
            provider=FakeProvider(project_json),
            runner=FakeManimRunner(),
            validator=FakeRawValidator(),
            observer=FakeObserver(),
            temporal_normalizer=FakeTemporalNormalizer(normalized_validator),
            normalized_validator=normalized_validator,
            final_validator=FakeFinalValidator(),
            composer=DivergentValidationComposer(),
            id_factory=lambda: "run-001",
        )
        == 1
    )
    assert "ERROR" in capsys.readouterr().out
    project_document = json.loads(project_json.read_text(encoding="utf-8"))
    assert project_document["status"] != "ready"
    run_document = json.loads(
        (project / "artifacts" / "run-001" / "run.json").read_text(encoding="utf-8")
    )
    assert run_document["state"] != "ready"
