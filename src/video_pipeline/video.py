"""Whole-video orchestration, progress, composition, and review artifacts."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from video_pipeline.capabilities import CapabilityRegistry
from video_pipeline.observation import SceneObserver
from video_pipeline.pipeline import PipelineEvent, PipelineState, RenderPipeline
from video_pipeline.project import (
    Project,
    ProjectSceneRef,
    ProjectStageState,
    ProjectState,
    _atomic_update_json_documents,
    _project_package_hashes,
    validate_project_timeline,
)
from video_pipeline.provider import LLMProvider
from video_pipeline.rendering import ManimRunner
from video_pipeline.scene_plan import ScenePlan
from video_pipeline.spec import SceneSpec
from video_pipeline.temporal import (
    TemporalNormalizationResult,
    TemporalNormalizer,
    TemporalTolerances,
    TemporalValidator,
    normalize_scene,
)
from video_pipeline.timeline import TimelineSegment
from video_pipeline.validation import (
    AudioVisualValidationResult,
    FinalAudioVisualValidator,
    RenderValidator,
)


@dataclass(frozen=True, slots=True)
class CompositionProfile:
    """Output media contract shared by audiovisual composition and validation."""

    resolution: tuple[int, int] = (854, 480)
    fps: int = 15
    timebase: int = 90_000
    pixel_format: str = "yuv420p"
    video_codec: str = "libx264"
    audio_codec: str = "aac"
    faststart: bool = True

    def __post_init__(self) -> None:
        width, height = self.resolution
        if width <= 0 or height <= 0:
            raise ValueError("composition resolution must be positive")
        if self.fps <= 0:
            raise ValueError("composition FPS must be positive")
        if self.timebase <= 0:
            raise ValueError("composition timebase must be positive")
        if not self.pixel_format.strip():
            raise ValueError("composition pixel format must not be blank")
        if not self.video_codec.strip() or not self.audio_codec.strip():
            raise ValueError("composition codecs must not be blank")


@dataclass(frozen=True, slots=True)
class CompositionResult:
    """Observable result from composing accepted scene MP4s."""

    argv: list[str]
    exit_code: int | None
    stdout: str
    stderr: str
    output_path: Path | None
    elapsed_seconds: float = 0.0
    validation: AudioVisualValidationResult | None = None
    log_path: Path | None = None
    error: str | None = None

    def to_document(self) -> dict[str, object]:
        """Return the auditable composition and final-validation facts."""

        return {
            "argv": list(self.argv),
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "elapsed_seconds": self.elapsed_seconds,
            "output_path": str(self.output_path) if self.output_path is not None else None,
            "validation": self.validation.to_document() if self.validation is not None else None,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class VideoResult:
    """Terminal result for one video render."""

    state: str
    run_path: Path
    output_path: Path | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectPipelineEvent(PipelineEvent):
    """A scene progress event annotated with its containing project run."""

    project_run_id: str
    scene_id: str


class VideoComposer(Protocol):
    """External composition boundary."""

    def compose(
        self,
        scene_paths: Sequence[Path],
        narration_path: Path,
        output_path: Path | None = None,
        *,
        expected_duration_seconds: float | None = None,
        profile: CompositionProfile | None = None,
        validator: FinalCompositionValidator | None = None,
    ) -> CompositionResult:
        """Compose accepted normalized scenes with the immutable narration."""


class FinalCompositionValidator(Protocol):
    """Validator boundary for the temporary final MP4."""

    def validate(self, path: str | Path) -> AudioVisualValidationResult:
        """Validate one unpublished audiovisual candidate."""


class _DefaultTemporalNormalizer:
    """Adapt the canonical temporal function to the injected object seam."""

    def __init__(self, tolerances: TemporalTolerances) -> None:
        self.tolerances = tolerances

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
    ) -> TemporalNormalizationResult:
        return normalize_scene(
            raw_path,
            normalized_path=normalized_path,
            observed_duration_seconds=observed_duration_seconds,
            target_duration_seconds=target_duration_seconds,
            target_resolution=target_resolution,
            target_fps=target_fps,
            target_timebase=target_timebase,
            target_pixel_format=target_pixel_format,
            tolerances=self.tolerances,
            validator=validator,
            log_path=Path(normalized_path).with_name("normalization.json"),
        )


class _CompletedProcess(Protocol):
    returncode: int
    stdout: str | None
    stderr: str | None


class _SubprocessRun(Protocol):
    def __call__(
        self,
        args: Sequence[str],
        *,
        capture_output: bool,
        text: bool,
        timeout: float,
        check: bool,
    ) -> _CompletedProcess:
        """Run one bounded ffmpeg command."""


class FFmpegComposer:
    """Compose normalized scene video with the immutable narration track."""

    def __init__(
        self,
        *,
        subprocess_run: _SubprocessRun | None = None,
        timeout: float = 120.0,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._subprocess_run = subprocess_run if subprocess_run is not None else _run_ffmpeg
        self.timeout = float(timeout)

    def compose(
        self,
        scene_paths: Sequence[Path],
        narration_path: Path,
        output_path: Path | None = None,
        *,
        expected_duration_seconds: float | None = None,
        profile: CompositionProfile | None = None,
        validator: FinalCompositionValidator | None = None,
    ) -> CompositionResult:
        """Compose, validate, and atomically publish one final audiovisual MP4."""

        scenes = list(scene_paths)
        if not scenes:
            raise ValueError("composition requires at least one scene")
        if output_path is None:
            raise ValueError(
                "audiovisual composition requires narration, output, duration, "
                "profile, and validator"
            )
        if expected_duration_seconds is None or profile is None or validator is None:
            raise ValueError(
                "audiovisual composition requires narration, output, duration, "
                "profile, and validator"
            )
        if not math.isfinite(expected_duration_seconds) or expected_duration_seconds <= 0:
            raise ValueError("expected composition duration must be finite and positive")
        for scene_path in scenes:
            if not scene_path.is_file():
                raise ValueError(f"scene path is not a file: {scene_path}")
        if not narration_path.is_file():
            raise ValueError(f"narration path is not a file: {narration_path}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        list_path = output_path.parent / "scenes.txt"
        _write_text(
            list_path,
            "".join(f"file '{_concat_path(path)}'\n" for path in scenes),
        )
        temporary_path = _temporary_output_path(output_path)
        argv = _composition_argv(
            list_path,
            narration_path,
            temporary_path,
            expected_duration_seconds,
            profile,
        )
        log_path = output_path.parent / "composition.json"
        started = time.monotonic()
        try:
            completed = self._subprocess_run(
                argv,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            _remove_temporary(temporary_path)
            result = CompositionResult(
                argv=argv,
                exit_code=None,
                stdout="",
                stderr=str(exc),
                output_path=None,
                elapsed_seconds=_elapsed_since(started),
                log_path=log_path,
                error=str(exc),
            )
            _write_json(log_path, result.to_document())
            return result

        elapsed = _elapsed_since(started)
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        if completed.returncode != 0:
            _remove_temporary(temporary_path)
            result = CompositionResult(
                argv=argv,
                exit_code=completed.returncode,
                stdout=stdout,
                stderr=stderr,
                output_path=None,
                elapsed_seconds=elapsed,
                log_path=log_path,
                error=stderr or "ffmpeg composition failed",
            )
            _write_json(log_path, result.to_document())
            return result
        if not temporary_path.is_file():
            _remove_temporary(temporary_path)
            result = CompositionResult(
                argv=argv,
                exit_code=completed.returncode,
                stdout=stdout,
                stderr=stderr,
                output_path=None,
                elapsed_seconds=elapsed,
                log_path=log_path,
                error="ffmpeg did not produce a temporary final MP4",
            )
            _write_json(log_path, result.to_document())
            return result

        try:
            validation = validator.validate(temporary_path)
        except (OSError, ValueError) as exc:
            _remove_temporary(temporary_path)
            result = CompositionResult(
                argv=argv,
                exit_code=completed.returncode,
                stdout=stdout,
                stderr=stderr,
                output_path=None,
                elapsed_seconds=elapsed,
                log_path=log_path,
                error=f"final validation failed: {exc}",
            )
            _write_json(log_path, result.to_document())
            return result
        if not validation.valid:
            _remove_temporary(temporary_path)
            result = CompositionResult(
                argv=argv,
                exit_code=completed.returncode,
                stdout=stdout,
                stderr=stderr,
                output_path=None,
                elapsed_seconds=elapsed,
                validation=validation,
                log_path=log_path,
                error=(
                    "; ".join(validation.reasons)
                    or "final audiovisual validation failed"
                ),
            )
            _write_json(log_path, result.to_document())
            return result

        try:
            temporary_path.replace(output_path)
        except OSError as exc:
            _remove_temporary(temporary_path)
            result = CompositionResult(
                argv=argv,
                exit_code=completed.returncode,
                stdout=stdout,
                stderr=stderr,
                output_path=None,
                elapsed_seconds=elapsed,
                validation=validation,
                log_path=log_path,
                error=f"could not publish final MP4: {exc}",
            )
            _write_json(log_path, result.to_document())
            return result

        published_validation = replace(validation, path=output_path)
        result = CompositionResult(
            argv=argv,
            exit_code=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            output_path=output_path,
            elapsed_seconds=elapsed,
            validation=published_validation,
            log_path=log_path,
        )
        _write_json(log_path, result.to_document())
        return result


class VideoPipeline:
    """Render one confirmed project through the canonical audiovisual path."""

    def __init__(
        self,
        *,
        provider: LLMProvider | None = None,
        runner: ManimRunner | None = None,
        validator: RenderValidator | None = None,
        observer: SceneObserver | None = None,
        temporal_normalizer: TemporalNormalizer | None = None,
        normalized_validator: TemporalValidator | None = None,
        final_validator: FinalCompositionValidator | None = None,
        composer: VideoComposer | None = None,
        output_root: str | Path | None = None,
        id_factory: Callable[[], str] | None = None,
        temperature: float = 0.0,
        seed: int = 42,
        capability_registry: CapabilityRegistry | None = None,
        temporal_tolerances: TemporalTolerances | None = None,
    ) -> None:
        self.provider = provider
        self.runner = runner
        self.validator = validator
        self.observer = observer
        self.temporal_tolerances = temporal_tolerances or TemporalTolerances()
        self.temporal_normalizer = temporal_normalizer or _DefaultTemporalNormalizer(
            self.temporal_tolerances
        )
        self.normalized_validator = normalized_validator or RenderValidator()
        self.final_validator = final_validator
        self.composer = composer or FFmpegComposer()
        self.output_root = Path(output_root).resolve() if output_root is not None else None
        self.id_factory = id_factory or (lambda: uuid.uuid4().hex)
        self.temperature = temperature
        self.seed = seed
        self.capability_registry = capability_registry

    def render(
        self,
        project_path: str | Path,
        *,
        max_attempts: int = 3,
        scene: str | None = None,
        base_run_id: str | None = None,
        correction: str | None = None,
        on_progress: Callable[[ProjectPipelineEvent], None] | None = None,
    ) -> VideoResult:
        """Render all or one confirmed timeline scene and publish final media."""

        project_json = Path(project_path).resolve()
        project, timeline = validate_project_timeline(project_json)
        if timeline.status != "confirmed":
            raise ValueError("timeline must be confirmed before rendering")
        project_root = project_json.parent
        selected_scene_id = _resolve_scene_selector(
            project_root,
            project,
            timeline.segments,
            scene,
        )
        selective_requested = base_run_id is not None or correction is not None
        if selective_requested:
            if selected_scene_id is None:
                raise ValueError("base run and correction require an explicit scene")
            if (base_run_id is None) != (correction is None):
                raise ValueError("base run and correction must be provided together")
            assert base_run_id is not None
            assert correction is not None
            base_run_id = base_run_id.strip()
            correction = correction.strip()
            if not base_run_id or not correction:
                raise ValueError("base run and correction must not be blank")
        input_hashes = _project_input_hashes(project_root, project)
        package_hashes = _project_package_hashes(project_root, project)
        existing_run_id = project.current_run
        is_resume = (
            not selective_requested
            and existing_run_id is not None
            and project.status in {ProjectState.failed, ProjectState.rendering}
        )
        run_id: str
        run_path: Path
        if is_resume:
            assert existing_run_id is not None
            run_id = existing_run_id
            run_path, run_document = self._load_failed_run(project_root, run_id)
            self._validate_resume_run(
                project,
                timeline.segments,
                project_root,
                run_path,
                run_document,
                input_hashes,
                package_hashes,
            )
            scene_records = _resume_scene_records(
                run_document,
                timeline.segments,
                project.scenes,
            )
            run_document["scenes"] = scene_records
            run_document.setdefault("current_scene", None)
            for segment, scene_ref, record in zip(
                timeline.segments,
                project.scenes,
                scene_records,
                strict=True,
            ):
                if record["state"] == "ready":
                    self._validate_reusable_scene(
                        project_root,
                        run_path,
                        segment,
                        scene_ref,
                        record,
                    )
            for record in scene_records:
                if record.get("state") == "rendering":
                    record.update(
                        {
                            "state": "failed",
                            "action_next": "inspect diagnostics and retry this scene",
                            "error": record.get("error")
                            or "interrupted scene requires retry",
                        }
                    )
            run_document.update(
                {
                    "state": "rendering",
                    "state_history": _append_state(
                        run_document.get("state_history"), "rendering"
                    ),
                    "max_attempts": max_attempts,
                    "current_scene": None,
                    "action_next": "resume the failed run and complete remaining scenes",
                    "error": None,
                }
            )
        else:
            base_run_path: Path | None = None
            if selective_requested:
                assert base_run_id is not None
                base_run_path, _, base_scene_records = self._load_ready_base_run(
                    project,
                    timeline.segments,
                    project_root,
                    base_run_id,
                    input_hashes,
                    package_hashes,
                )
            run_id, run_path = self._create_run(project_root)
            try:
                if selective_requested:
                    assert base_run_path is not None
                    scene_records = []
                    for segment, scene_ref, base_record in zip(
                        timeline.segments,
                        project.scenes,
                        base_scene_records,
                        strict=True,
                    ):
                        if segment.id == selected_scene_id:
                            scene_records.append(_new_scene_record(segment, scene_ref))
                        else:
                            scene_records.append(
                                self._clone_ready_scene(
                                    project_root,
                                    base_run_path,
                                    run_path,
                                    segment,
                                    scene_ref,
                                    base_record,
                                )
                            )
                else:
                    scene_records = [
                        _new_scene_record(segment, scene_ref)
                        for segment, scene_ref in zip(
                            timeline.segments,
                            project.scenes,
                            strict=True,
                        )
                    ]
            except BaseException:
                shutil.rmtree(run_path, ignore_errors=True)
                raise
            run_document = {
                "schema_version": "project.render-run/1",
                "run_id": run_id,
                "project_id": project.id,
                "project_path": str(project_json),
                "run_path": str(run_path),
                "state": "rendering",
                "state_history": ["rendering"],
                "current_scene": None,
                "max_attempts": max_attempts,
                "input_hashes": input_hashes,
                "package_hashes": package_hashes,
                "timeline_path": str(project_root / (project.timeline_path or "timeline.json")),
                "scenes": scene_records,
                "composition": None,
                "final_validation": None,
                "output_path": None,
                "action_next": "complete the render before accepting this run",
                "error": None,
            }
            if selective_requested:
                assert base_run_id is not None
                assert correction is not None
                run_document.update(
                    {
                        "base_run_id": base_run_id,
                        "selected_scene_id": selected_scene_id,
                        "correction": correction,
                    }
                )
        _validate_run_current_scene(
            run_document,
            [segment.id for segment in timeline.segments],
        )
        rendering_document = _project_document(
            project,
            status=ProjectState.rendering.value,
            current_run=run_id,
            current_scene=None,
            render_state=ProjectStageState.pending.value,
            composition_state=ProjectStageState.pending.value,
        )
        _validate_project_document(rendering_document)
        if is_resume:
            _atomic_update_json_documents(
                (
                    (project_json, rendering_document),
                    (run_path / "run.json", run_document),
                )
            )
        else:
            try:
                _atomic_update_json_documents(
                    (
                        (project_json, rendering_document),
                        (run_path / "run.json", run_document),
                    )
                )
            except BaseException:
                if selective_requested:
                    shutil.rmtree(run_path, ignore_errors=True)
                else:
                    try:
                        run_path.rmdir()
                    except OSError:
                        pass
                raise

        profile = CompositionProfile(
            resolution=project.theme.resolution,
            fps=project.theme.fps,
        )
        normalized_paths: list[Path] = []
        try:
            for index, (segment, scene_ref) in enumerate(
                zip(timeline.segments, project.scenes, strict=True)
            ):
                record = scene_records[index]
                if selected_scene_id is not None and segment.id != selected_scene_id:
                    if record["state"] == "ready":
                        normalized_paths.append(
                            self._validate_reusable_scene(
                                project_root,
                                run_path,
                                segment,
                                scene_ref,
                                record,
                            )
                        )
                    continue
                if record["state"] == "ready":
                    normalized_path = self._validate_reusable_scene(
                        project_root,
                        run_path,
                        segment,
                        scene_ref,
                        record,
                    )
                    normalized_paths.append(normalized_path)
                    continue
                record["state"] = "rendering"
                record["action_next"] = "generate, observe, and normalize scene"
                run_document["current_scene"] = segment.id
                scene_rendering_document = _project_document(
                    project,
                    status=ProjectState.rendering.value,
                    current_run=run_id,
                    current_scene=segment.id,
                    render_state=ProjectStageState.pending.value,
                    composition_state=ProjectStageState.pending.value,
                )
                _validate_project_document(scene_rendering_document)
                _validate_run_current_scene(
                    run_document,
                    [item.id for item in timeline.segments],
                )
                _atomic_update_json_documents(
                    (
                        (project_json, scene_rendering_document),
                        (run_path / "run.json", run_document),
                    )
                )
                normalized_path = self._render_scene(
                    project_root,
                    timeline.segments,
                    segment,
                    scene_ref,
                    run_id=run_id,
                    run_path=run_path,
                    max_attempts=max_attempts,
                    profile=profile,
                    record=record,
                    correction=(
                        correction
                        if selective_requested and segment.id == selected_scene_id
                        else None
                    ),
                    on_progress=on_progress,
                )
                normalized_paths.append(normalized_path)
                record["error"] = None
                record["state"] = "ready"
                record["action_next"] = "include normalized scene in composition"
                run_document["current_scene"] = None
                render_state = (
                    ProjectStageState.ready.value
                    if all(item.get("state") == "ready" for item in scene_records)
                    else ProjectStageState.pending.value
                )
                scene_ready_document = _project_document(
                    project,
                    status=ProjectState.rendering.value,
                    current_run=run_id,
                    current_scene=None,
                    render_state=render_state,
                    composition_state=ProjectStageState.pending.value,
                )
                _validate_project_document(scene_ready_document)
                _validate_run_current_scene(
                    run_document,
                    [item.id for item in timeline.segments],
                )
                _atomic_update_json_documents(
                    (
                        (project_json, scene_ready_document),
                        (run_path / "run.json", run_document),
                    )
                )

            if not all(record.get("state") == "ready" for record in scene_records):
                raise ValueError(
                    "cannot compose until every timeline scene is ready; "
                    "select another failed scene"
                )
            run_document.update(
                {
                    "state": "composing",
                    "state_history": _append_state(
                        run_document.get("state_history"), "composing"
                    ),
                    "current_scene": None,
                    "action_next": "compose final audiovisual output",
                }
            )
            _validate_run_current_scene(
                run_document,
                [segment.id for segment in timeline.segments],
            )
            composing_document = _project_document(
                project,
                status=ProjectState.rendering.value,
                current_run=run_id,
                current_scene=None,
                render_state=ProjectStageState.ready.value,
                composition_state=ProjectStageState.pending.value,
            )
            _validate_project_document(composing_document)
            _atomic_update_json_documents(
                (
                    (project_json, composing_document),
                    (run_path / "run.json", run_document),
                )
            )
            audio_path = _resolve_project_file(
                project_root,
                project.audio_path,
                label="audio",
            )
            final_path = run_path / "final.mp4"
            active_final_validator = self.final_validator or FinalAudioVisualValidator(
                expected_duration_seconds=timeline.duration_seconds,
                expected_resolution=profile.resolution,
                expected_fps=float(profile.fps),
                expected_timebase=f"1/{profile.timebase}",
            )
            composition = self.composer.compose(
                normalized_paths,
                audio_path,
                final_path,
                expected_duration_seconds=timeline.duration_seconds,
                profile=profile,
                validator=active_final_validator,
            )
            _atomic_write_json_file(
                run_path / "composition.json",
                composition.to_document(),
            )
            if composition.output_path is None or not composition.output_path.is_file():
                raise ValueError(
                    composition.error or "composer did not publish the final MP4"
                )
            expected_final_path = final_path.resolve()
            if composition.output_path.resolve() != expected_final_path:
                raise ValueError(
                    "composition output path does not match the published run final MP4"
                )
            final_validation = composition.validation
            if final_validation is None:
                final_validation = active_final_validator.validate(composition.output_path)
            if final_validation.path.resolve() != expected_final_path:
                raise ValueError(
                    "final validation path does not match the published run final MP4"
                )
            if not final_validation.valid:
                raise ValueError(
                    "; ".join(final_validation.reasons)
                    or "final audiovisual validation failed"
                )
            final_validation = replace(final_validation, path=final_path)
            ready_composition = replace(
                composition,
                output_path=final_path,
                validation=final_validation,
            )
            _atomic_write_json_file(
                run_path / "composition.json",
                ready_composition.to_document(),
            )
            final_size_bytes = final_path.stat().st_size
            if final_size_bytes <= 0:
                raise ValueError("published final MP4 must be non-empty")
            final_sha256 = _sha256_file(final_path)
            run_document.update(
                {
                    "state": "ready",
                    "state_history": _append_states(
                        run_document.get("state_history"), "composing", "ready"
                    ),
                    "composition": ready_composition.to_document(),
                    "final_validation": final_validation.to_document(),
                    "output_path": str(final_path),
                    "final_sha256": final_sha256,
                    "final_size_bytes": final_size_bytes,
                    "action_next": "accept this run explicitly when editorial review is complete",
                    "error": None,
                }
            )
            ready_document = _project_document(
                project,
                status=ProjectState.ready.value,
                current_run=run_id,
                current_scene=None,
                render_state=ProjectStageState.ready.value,
                composition_state=ProjectStageState.ready.value,
            )
            _validate_project_document(ready_document)
            _validate_run_current_scene(
                run_document,
                [segment.id for segment in timeline.segments],
            )
            _atomic_update_json_documents(
                (
                    (project_json, ready_document),
                    (run_path / "run.json", run_document),
                )
            )
            return VideoResult(
                state=ProjectState.ready.value,
                run_path=run_path,
                output_path=ready_composition.output_path,
            )
        except Exception as exc:
            error_text = str(exc)
            for record in scene_records:
                if record.get("state") == "rendering":
                    record.update(
                        {
                            "state": "failed",
                            "action_next": "inspect diagnostics and retry this scene",
                            "error": error_text,
                        }
                    )
            run_document.update(
                {
                    "state": "failed",
                    "state_history": _append_state(
                        run_document.get("state_history"), "failed"
                    ),
                    "action_next": "inspect the failed run and correct the scene",
                    "error": error_text,
                }
            )
            failed_document = _project_document(
                project,
                status=ProjectState.failed.value,
                current_run=run_id,
                current_scene=run_document.get("current_scene"),
                render_state=ProjectStageState.failed.value,
                composition_state=ProjectStageState.failed.value,
            )
            _validate_project_document(failed_document)
            _validate_run_current_scene(
                run_document,
                [segment.id for segment in timeline.segments],
            )
            _atomic_update_json_documents(
                (
                    (project_json, failed_document),
                    (run_path / "run.json", run_document),
                )
            )
            raise ValueError(error_text) from exc

    def _load_failed_run(
        self,
        project_root: Path,
        run_id: str,
    ) -> tuple[Path, dict[str, object]]:
        """Load the failed or interrupted run selected for a safe resume."""

        if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
            raise ValueError("failed run ID must be a safe non-empty name")
        root = (self.output_root or project_root / "artifacts").resolve()
        run_path = (root / run_id).resolve()
        try:
            run_path.relative_to(root)
        except ValueError as exc:
            raise ValueError("failed run path escapes the artifact root") from exc
        run_document_path = run_path / "run.json"
        if not run_document_path.is_file():
            raise ValueError(
                "failed run evidence is missing; start a new run explicitly"
            )
        run_document = _load_json_document(run_document_path)
        if run_document.get("run_id") != run_id:
            raise ValueError("failed run document ID does not match project current_run")
        if run_document.get("state") not in {"failed", "rendering", "composing"}:
            raise ValueError("current run is not a resumable run")
        return run_path, run_document

    def _load_ready_base_run(
        self,
        project: Project,
        segments: Sequence[TimelineSegment],
        project_root: Path,
        base_run_id: str,
        input_hashes: Mapping[str, str],
        package_hashes: Mapping[str, str],
    ) -> tuple[Path, dict[str, object], list[dict[str, object]]]:
        """Load and fully validate a ready run before selective regeneration."""

        if (
            not base_run_id
            or Path(base_run_id).name != base_run_id
            or base_run_id in {".", ".."}
            or "\\" in base_run_id
        ):
            raise ValueError("base run ID must be a safe non-empty name")
        artifacts_root = (self.output_root or project_root / "artifacts").resolve()
        base_run_path = _safe_child_path(
            artifacts_root,
            base_run_id,
            label="base run",
        )
        run_json = base_run_path / "run.json"
        if not run_json.is_file():
            raise ValueError(f"base run evidence is missing: {run_json}")
        run_document = _load_json_document(run_json)
        if not run_document:
            raise ValueError(f"base run evidence is invalid: {run_json}")
        if run_document.get("run_id") != base_run_id:
            raise ValueError("base run document ID does not match requested base run")
        if run_document.get("state") != "ready":
            raise ValueError("base run must be ready before selective regeneration")
        if "current_scene" not in run_document or run_document.get("current_scene") is not None:
            raise ValueError("base run current_scene must be null")
        if run_document.get("project_id") != project.id:
            raise ValueError("base run project ID does not match project")
        if run_document.get("input_hashes") != dict(input_hashes):
            raise ValueError("base run input hashes do not match current project")
        if run_document.get("package_hashes") != dict(package_hashes):
            raise ValueError("base run package hashes do not match current project")
        try:
            _validate_reusable_final_attestation(
                base_run_path,
                run_document,
                required=True,
            )
        except (OSError, ValueError) as exc:
            raise ValueError(f"base run final attestation is invalid: {exc}") from exc
        try:
            records = _resume_scene_records(run_document, segments, project.scenes)
        except (OSError, ValueError) as exc:
            raise ValueError(f"base run scene records are invalid: {exc}") from exc
        if any(record.get("state") != "ready" for record in records):
            raise ValueError("base run must have every scene ready")
        for segment, scene_ref, record in zip(
            segments,
            project.scenes,
            records,
            strict=True,
        ):
            base_scene_path = _safe_child_path(
                base_run_path,
                scene_ref.path,
                label="base scene",
            )
            base_pipeline_path = _safe_child_path(
                base_run_path,
                f"pipeline/{segment.id}",
                label="base scene pipeline",
            )
            _validate_reusable_source_tree(
                base_scene_path,
                label=f"base scene {segment.id}",
                expected_root_files={
                    "raw.mp4",
                    "normalized.mp4",
                    "normalization.json",
                    "scene.py",
                    "code-provenance.json",
                },
            )
            _validate_reusable_source_tree(
                base_pipeline_path,
                label=f"base scene pipeline {segment.id}",
            )
            try:
                self._validate_reusable_scene(
                    project_root,
                    base_run_path,
                    segment,
                    scene_ref,
                    record,
                )
            except (OSError, ValueError) as exc:
                raise ValueError(
                    f"base run scene {segment.id} evidence is invalid: {exc}"
                ) from exc
        return base_run_path, run_document, records

    def _validate_resume_run(
        self,
        project: Project,
        segments: Sequence[TimelineSegment],
        project_root: Path,
        run_path: Path,
        run_document: Mapping[str, object],
        input_hashes: Mapping[str, str],
        package_hashes: Mapping[str, str],
    ) -> None:
        """Validate all persisted identity and reusable-scene evidence first."""

        if run_document.get("project_id") != project.id:
            raise ValueError("failed run project ID does not match project")
        _validate_run_current_scene(run_document, [segment.id for segment in segments])
        run_state = run_document.get("state")
        if project.status is ProjectState.rendering:
            if run_state not in {"rendering", "composing"}:
                raise ValueError("rendering project does not reference a resumable run")
            if run_state == "composing" and run_document.get("current_scene") is not None:
                raise ValueError("composing run must not have a current scene")
        elif project.status is ProjectState.failed and run_state != "failed":
            raise ValueError("failed project does not reference a failed run")
        if project.current_scene != run_document.get("current_scene"):
            raise ValueError("project and run current_scene values do not match")
        if run_document.get("input_hashes") != dict(input_hashes):
            raise ValueError(
                "stored run input hashes changed; start a new run explicitly"
            )
        if run_document.get("package_hashes") != dict(package_hashes):
            raise ValueError(
                "stored run package hashes changed; start a new run explicitly"
            )
        _validate_reusable_final_attestation(run_path, run_document)
        records = _resume_scene_records(run_document, segments, project.scenes)
        rendering_records = [
            record for record in records if record.get("state") == "rendering"
        ]
        current_scene = run_document.get("current_scene")
        if run_state == "rendering":
            if current_scene is None:
                if rendering_records:
                    raise ValueError(
                        "idle rendering run must not have a current rendering scene"
                    )
            elif len(rendering_records) != 1 or rendering_records[0].get("id") != current_scene:
                raise ValueError(
                    "interrupted rendering run must have exactly one current rendering scene"
                )
        elif run_state == "composing":
            if rendering_records or any(record.get("state") != "ready" for record in records):
                raise ValueError("composing run must have only ready scene records")
        elif run_state == "failed" and rendering_records:
            raise ValueError("failed run must not have rendering scene records")
        for segment, scene_ref, record in zip(
            segments,
            project.scenes,
            records,
            strict=True,
        ):
            if record["state"] == "ready":
                self._validate_reusable_scene(
                    project_root,
                    run_path,
                    segment,
                    scene_ref,
                    record,
                )

    def _validate_reusable_scene(
        self,
        project_root: Path,
        run_path: Path,
        segment: TimelineSegment,
        scene_ref: ProjectSceneRef,
        record: Mapping[str, object],
    ) -> Path:
        """Verify a ready scene's immutable code and media evidence."""

        if record.get("state") != "ready":
            raise ValueError(f"scene is not ready for reuse: {segment.id}")
        attempts = record.get("attempts")
        if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts <= 0:
            raise ValueError(f"scene has no successful attempt evidence: {segment.id}")
        scene_root = _safe_child_path(run_path, scene_ref.path, label="run scene")
        raw_path = _safe_child_path(scene_root, "raw.mp4", label="raw scene")
        normalized_path = _safe_child_path(
            scene_root,
            "normalized.mp4",
            label="normalized scene",
        )
        normalization_path = _safe_child_path(
            scene_root,
            "normalization.json",
            label="normalization evidence",
        )
        code_path = _safe_child_path(scene_root, "scene.py", label="scene code")
        provenance_path = _safe_child_path(
            scene_root,
            "code-provenance.json",
            label="scene provenance",
        )
        for path in (
            raw_path,
            normalized_path,
            normalization_path,
            code_path,
            provenance_path,
        ):
            if not path.is_file():
                raise ValueError(
                    f"ready scene evidence is missing; start a new run explicitly: {path}"
                )
        for key, expected in (
            ("raw_path", raw_path),
            ("normalized_path", normalized_path),
            ("normalization_path", normalization_path),
            ("code_path", code_path),
            ("provenance_path", provenance_path),
        ):
            _require_record_path(record, key, expected, scene_id=segment.id)
        pipeline_run = _record_path(record, "run_path", scene_id=segment.id)
        pipeline_root = _safe_child_path(
            run_path,
            f"pipeline/{segment.id}",
            label="scene pipeline",
        )
        _require_path_inside(pipeline_run, pipeline_root, label="scene pipeline")
        latest_attempt = _record_path(
            record,
            "latest_attempt_path",
            scene_id=segment.id,
        )
        _require_path_inside(
            latest_attempt,
            pipeline_run,
            label="latest scene attempt",
        )
        diagnostics_path = latest_attempt / "diagnostics.json"
        observation_path = latest_attempt / "observation.json"
        quality_path = latest_attempt / "quality-report.json"
        for key, expected in (
            ("diagnostics_path", diagnostics_path),
            ("observation_path", observation_path),
            ("quality_path", quality_path),
        ):
            _require_record_path(record, key, expected, scene_id=segment.id)
        for evidence_name in (
            "scene.py",
            "diagnostics.json",
            "observation.json",
            "quality-report.json",
            "validation.json",
        ):
            evidence_path = latest_attempt / evidence_name
            if not evidence_path.is_file():
                raise ValueError(
                    "ready scene evidence is missing; start a new run explicitly: "
                    f"{evidence_path}"
                )
        normalization = _load_json_document(normalization_path)
        if normalization.get("status") not in {"accepted", "normalized"}:
            raise ValueError(
                f"ready scene normalization evidence is not accepted: {segment.id}"
            )
        for key, path in (
            ("raw_sha256", raw_path),
            ("normalized_sha256", normalized_path),
            ("normalization_sha256", normalization_path),
            ("code_sha256", code_path),
            ("provenance_sha256", provenance_path),
            ("diagnostics_sha256", diagnostics_path),
            ("observation_sha256", observation_path),
            ("quality_sha256", quality_path),
        ):
            digest = record.get(key)
            if not isinstance(digest, str) or digest != _sha256_file(path):
                raise ValueError(
                    f"stored {key} does not match ready scene evidence; "
                    "start a new run explicitly"
                )
        return normalized_path

    def _clone_ready_scene(
        self,
        project_root: Path,
        base_run_path: Path,
        new_run_path: Path,
        segment: TimelineSegment,
        scene_ref: ProjectSceneRef,
        record: Mapping[str, object],
    ) -> dict[str, object]:
        """Atomically clone one ready scene and its inner pipeline evidence."""

        if not new_run_path.is_dir():
            raise ValueError(f"new run path does not exist: {new_run_path}")
        base_scene_path = _safe_child_path(
            base_run_path,
            scene_ref.path,
            label="base scene",
        )
        base_pipeline_path = _safe_child_path(
            base_run_path,
            f"pipeline/{segment.id}",
            label="base scene pipeline",
        )
        new_scene_path = _safe_child_path(
            new_run_path,
            scene_ref.path,
            label="new scene",
        )
        new_pipeline_path = _safe_child_path(
            new_run_path,
            f"pipeline/{segment.id}",
            label="new scene pipeline",
        )
        if not base_scene_path.is_dir() or not base_pipeline_path.is_dir():
            raise ValueError(f"base run scene evidence is missing: {segment.id}")
        if os.path.lexists(new_scene_path) or os.path.lexists(new_pipeline_path):
            raise ValueError(f"new run scene destinations already exist: {segment.id}")

        cloned_value = _rebase_absolute_paths(record, base_run_path, new_run_path)
        if not isinstance(cloned_value, dict):
            raise ValueError(f"base run scene record is not an object: {segment.id}")
        cloned_record = cloned_value
        cloned_record["error"] = None
        staging_path: Path | None = None
        published_paths: list[Path] = []
        try:
            staging_path = Path(
                tempfile.mkdtemp(
                    prefix=f".scene-{segment.id}-",
                    dir=new_run_path,
                )
            )
            staged_scene_path = staging_path / scene_ref.path
            staged_pipeline_path = staging_path / "pipeline" / segment.id
            shutil.copytree(base_scene_path, staged_scene_path)
            shutil.copytree(base_pipeline_path, staged_pipeline_path)
            staged_scene_path.parent.mkdir(parents=True, exist_ok=True)
            staged_pipeline_path.parent.mkdir(parents=True, exist_ok=True)
            new_scene_path.parent.mkdir(parents=True, exist_ok=True)
            new_pipeline_path.parent.mkdir(parents=True, exist_ok=True)
            staged_scene_path.replace(new_scene_path)
            published_paths.append(new_scene_path)
            staged_pipeline_path.replace(new_pipeline_path)
            published_paths.append(new_pipeline_path)
            self._validate_reusable_scene(
                project_root,
                new_run_path,
                segment,
                scene_ref,
                cloned_record,
            )
            return cloned_record
        except BaseException:
            for published_path in reversed(published_paths):
                if published_path.is_dir() or published_path.is_symlink():
                    shutil.rmtree(published_path, ignore_errors=True)
            raise
        finally:
            if staging_path is not None:
                shutil.rmtree(staging_path, ignore_errors=True)

    def _create_run(self, project_root: Path) -> tuple[str, Path]:
        root = self.output_root or project_root / "artifacts"
        root.mkdir(parents=True, exist_ok=True)
        while True:
            run_id = self.id_factory()
            if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
                raise ValueError("run id must be a safe non-empty name")
            run_path = root / run_id
            try:
                run_path.mkdir()
            except FileExistsError:
                continue
            return run_id, run_path

    def _render_scene(
        self,
        project_root: Path,
        segments: Sequence[TimelineSegment],
        segment: TimelineSegment,
        scene_ref: ProjectSceneRef,
        *,
        run_id: str,
        run_path: Path,
        max_attempts: int,
        profile: CompositionProfile,
        record: dict[str, object],
        correction: str | None = None,
        on_progress: Callable[[ProjectPipelineEvent], None] | None = None,
    ) -> Path:
        # The public project contract has already validated these values; the
        # concrete models are reloaded here so generated requests use the
        # exact persisted plan rather than an inferred duplicate.
        plan_path = _resolve_project_file(project_root, scene_ref.plan_path, label="plan")
        plan = ScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
        if plan.id != segment.id:
            raise ValueError("scene plan identity does not agree with timeline")
        spec = SceneSpec(
            id=plan.id,
            scene_name=plan.scene_name,
            description=(
                plan.objective
                if correction is None
                else f"{plan.objective}\n\nEditorial correction: {correction}"
            ),
            plan=plan,
        )
        previous_attempts = _record_attempts(record)
        scene_attempt = previous_attempts + 1
        scene_index = segment.order - 1
        previous_scene = (
            _scene_context(segments[scene_index - 1]) if scene_index > 0 else None
        )
        next_scene = (
            _scene_context(segments[scene_index + 1])
            if scene_index + 1 < len(segments)
            else None
        )
        inner_run_root = run_path / "pipeline" / segment.id
        scene_pipeline = RenderPipeline(
            provider=self.provider,
            runner=self.runner,
            validator=self.validator,
            observer=self.observer,
            output_root=run_path / "pipeline" / segment.id,
            id_factory=lambda: _next_scene_pipeline_run_id(
                inner_run_root,
                run_id,
                segment.order,
                scene_attempt,
            ),
            temperature=self.temperature,
            seed=self.seed,
            capability_registry=self.capability_registry,
            temporal_normalizer=self.temporal_normalizer,
            temporal_validator=self.normalized_validator,
            temporal_tolerances=self.temporal_tolerances,
        )
        def emit_project_event(event: PipelineEvent) -> None:
            if on_progress is None:
                return
            project_event = ProjectPipelineEvent(
                run_id=event.run_id,
                attempt=event.attempt,
                stage=event.stage,
                state=event.state,
                observation=event.observation,
                project_run_id=run_id,
                scene_id=segment.id,
            )
            try:
                on_progress(project_event)
            except Exception:
                return

        result = scene_pipeline.render(
            spec,
            max_attempts=max_attempts,
            previous_scene=previous_scene,
            next_scene=next_scene,
            on_progress=emit_project_event if on_progress is not None else None,
        )
        project_attempt_base = _project_attempt_base(record)
        pipeline_history = _pipeline_attempt_history(
            record,
            result.run_path,
            result.state,
            result.attempts,
            project_attempt_base,
        )
        record["attempts"] = project_attempt_base + len(pipeline_history)
        record["pipeline_attempts"] = result.attempts
        record["run_path"] = str(result.run_path)
        diagnostics_path = (
            result.run_path / f"attempt-{result.attempts:02d}" / "diagnostics.json"
        )
        latest_attempt_path = result.run_path / f"attempt-{result.attempts:02d}"
        record["latest_attempt_path"] = str(latest_attempt_path)
        diagnostics_path = latest_attempt_path / "diagnostics.json"
        observation_path = latest_attempt_path / "observation.json"
        quality_path = latest_attempt_path / "quality-report.json"
        record["diagnostics_path"] = str(diagnostics_path)
        record["observation_path"] = str(observation_path)
        record["quality_path"] = str(quality_path)
        record["diagnostics"] = _load_json_document(diagnostics_path)
        if not record["diagnostics"]:
            record["diagnostics"] = {
                "provider_error": _load_json_document(
                    latest_attempt_path / "provider_error.json"
                ),
                "response": _load_json_document(latest_attempt_path / "response.json"),
                "unload": _load_json_document(latest_attempt_path / "unload.json"),
            }
        if result.state is not PipelineState.SUCCESS or result.mp4_path is None:
            record["state"] = "failed"
            record["error"] = result.error or result.state.value
            record["action_next"] = "inspect diagnostics and retry this scene"
            _extend_attempt_history(record, pipeline_history)
            raise ValueError(
                f"scene {segment.id} render failed: {result.error or result.state.value}"
            )

        run_scene_root = run_path / scene_ref.path
        run_scene_root.mkdir(parents=True, exist_ok=True)
        raw_path = run_scene_root / "raw.mp4"
        _copy_file_atomic(result.mp4_path, raw_path)
        record["raw_path"] = str(raw_path)

        attempt_path = result.run_path / f"attempt-{result.attempts:02d}"
        code_source = attempt_path / "scene.py"
        if not code_source.is_file():
            raise ValueError(f"accepted scene source is missing: {code_source}")
        code_path = run_scene_root / "scene.py"
        _copy_file_atomic(code_source, code_path)
        provenance_path = run_scene_root / "code-provenance.json"
        _atomic_write_json_file(
            provenance_path,
            {
                "scene_id": segment.id,
                "scene_name": plan.scene_name,
                "run_id": run_id,
                "run_path": _relative_project_path(project_root, run_path),
                "source_path": _relative_project_path(project_root, code_source),
                "attempt": result.attempts,
            },
        )
        record["code_path"] = str(code_path)
        record["provenance_path"] = str(provenance_path)

        normalized_path = run_scene_root / "normalized.mp4"
        normalization = result.temporal_normalization
        if normalization is None:
            observed_duration = _validation_duration(attempt_path / "validation.json")
            normalization = self.temporal_normalizer.normalize(
                raw_path,
                normalized_path=normalized_path,
                observed_duration_seconds=observed_duration,
                target_duration_seconds=segment.target_duration_seconds,
                target_resolution=profile.resolution,
                target_fps=profile.fps,
                target_timebase=profile.timebase,
                target_pixel_format=profile.pixel_format,
                validator=self.normalized_validator,
            )
        normalization_path = run_scene_root / "normalization.json"
        _atomic_write_json_file(normalization_path, normalization.to_document())
        if normalization.status not in {"accepted", "normalized"}:
            detail = "; ".join(normalization.validation_reasons or [])
            raise ValueError(
                f"scene {segment.id} normalization {normalization.status}: "
                f"{detail or normalization.stderr or 'no normalized artifact'}"
            )
        source = normalization.normalized_path
        if source is None:
            source = raw_path
        if not source.is_file():
            raise ValueError(f"normalized scene is missing: {source}")
        if source.resolve() == raw_path.resolve():
            _copy_file_atomic(raw_path, normalized_path)
        elif source.resolve() != normalized_path.resolve():
            _copy_file_atomic(source, normalized_path)
        if not normalized_path.is_file():
            raise ValueError("normalized scene was not published")
        record["normalized_path"] = str(normalized_path)
        record["normalization_path"] = str(normalization_path)
        record["diagnostics"] = {
            **_load_json_document(diagnostics_path),
            "normalization": normalization.to_document(),
        }
        record["provenance_sha256"] = _sha256_file(provenance_path)
        record["diagnostics_sha256"] = _sha256_file(diagnostics_path)
        record["observation_sha256"] = _sha256_file(observation_path)
        record["quality_sha256"] = _sha256_file(quality_path)
        record["code_sha256"] = _sha256_file(code_path)
        record["raw_sha256"] = _sha256_file(raw_path)
        record["normalized_sha256"] = _sha256_file(normalized_path)
        record["normalization_sha256"] = _sha256_file(normalization_path)
        _extend_attempt_history(
            record,
            pipeline_history,
            final_artifacts={
                "raw_path": str(raw_path),
                "normalized_path": str(normalized_path),
                "normalization_path": str(normalization_path),
            },
        )
        return normalized_path


def _rebase_absolute_paths(
    value: object,
    base_run_path: Path,
    new_run_path: Path,
) -> object:
    """Deep-copy JSON-shaped values, rebasing absolute paths under ``base``."""
    base_root = base_run_path.resolve()
    new_root = new_run_path.resolve()
    json_object = _json_object(value)
    if json_object is not None:
        return {
            key: _rebase_absolute_paths(item, base_root, new_root)
            for key, item in json_object.items()
        }
    if isinstance(value, list):
        return [_rebase_absolute_paths(item, base_root, new_root) for item in value]
    if isinstance(value, str):
        candidate = Path(value)
        if candidate.is_absolute():
            try:
                relative = candidate.resolve().relative_to(base_root)
            except (OSError, ValueError):
                pass
            else:
                return str(new_root / relative)
        return value.replace(str(base_root), str(new_root))
    return value


def _new_scene_record(
    segment: TimelineSegment,
    scene_ref: ProjectSceneRef,
) -> dict[str, object]:
    """Create the initial queued evidence record for one project scene."""

    return {
        "id": segment.id,
        "order": segment.order,
        "plan_path": scene_ref.plan_path,
        "state": "queued",
        "attempts": 0,
        "attempt_history": [],
        "diagnostics": None,
        "diagnostics_path": None,
        "diagnostics_sha256": None,
        "observation_path": None,
        "observation_sha256": None,
        "quality_path": None,
        "quality_sha256": None,
        "run_path": None,
        "code_path": None,
        "provenance_path": None,
        "provenance_sha256": None,
        "raw_path": None,
        "normalized_path": None,
        "normalization_path": None,
        "action_next": "render scene",
        "error": None,
    }


def _project_input_hashes(project_root: Path, project: Project) -> dict[str, str]:
    """Hash the immutable project inputs used by a render run."""

    if project.timeline_path is None:
        raise ValueError("project has no timeline reference")
    script_path = _resolve_project_file(project_root, project.script_path, label="script")
    audio_path = _resolve_project_file(project_root, project.audio_path, label="audio")
    timeline_path = _resolve_project_file(
        project_root,
        project.timeline_path,
        label="timeline",
    )
    script_hash = _sha256_file(script_path)
    audio_hash = _sha256_file(audio_path)
    if script_hash != project.script_sha256:
        raise ValueError("project script hash does not match its copied script")
    if audio_hash != project.audio.hash or audio_path.stat().st_size != project.audio.size:
        raise ValueError("project audio hash or size does not match its copied audio")
    return {
        "script_sha256": script_hash,
        "audio_sha256": audio_hash,
        "timeline_sha256": _sha256_file(timeline_path),
    }


def _resume_scene_records(
    run_document: Mapping[str, object],
    segments: Sequence[TimelineSegment],
    scene_refs: Sequence[ProjectSceneRef],
) -> list[dict[str, object]]:
    """Validate and return the existing ordered scene records for a resume."""

    raw_records = run_document.get("scenes")
    if not isinstance(raw_records, list) or len(raw_records) != len(segments):
        raise ValueError("failed run scene evidence does not match the timeline")
    records: list[dict[str, object]] = []
    for segment, scene_ref, record_index in zip(
        segments,
        scene_refs,
        range(len(segments)),
        strict=True,
    ):
        raw_record_value: object = raw_records[record_index]
        record = _json_object(raw_record_value)
        if record is None:
            raise ValueError("failed run scene evidence must be JSON objects")
        if record.get("id") != segment.id or record.get("order") != segment.order:
            raise ValueError("failed run scene IDs or order do not match timeline")
        stored_plan_path = record.get("plan_path")
        if stored_plan_path is None:
            record["plan_path"] = scene_ref.plan_path
        elif stored_plan_path != scene_ref.plan_path:
            raise ValueError(f"failed run plan reference does not match: {segment.id}")
        record_state = record.get("state")
        rendering_scene = (
            record_state == "rendering"
            and run_document.get("state") == "rendering"
            and run_document.get("current_scene") == segment.id
        )
        if record_state not in {"ready", "failed", "queued"} and not rendering_scene:
            raise ValueError(
                f"run scene has unsupported lifecycle state: {segment.id}"
            )
        action_next = record.get("action_next")
        if rendering_scene and (
            not isinstance(action_next, str) or not action_next.strip()
        ):
            raise ValueError(
                f"interrupted scene is missing its next action: {segment.id}"
            )
        attempts = record.get("attempts")
        if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
            raise ValueError(f"failed run scene attempts are invalid: {segment.id}")
        history = record.get("attempt_history")
        if not isinstance(history, list):
            record["attempt_history"] = []
        records.append(record)
    return records


def _json_object(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _append_state(raw_history: object, state: str) -> list[str]:
    """Append one lifecycle state while preserving prior run history."""

    if isinstance(raw_history, list):
        history = [item for item in raw_history if isinstance(item, str)]
    else:
        history = []
    if not history or history[-1] != state:
        history.append(state)
    return history


def _append_states(raw_history: object, *states: str) -> list[str]:
    if isinstance(raw_history, list):
        history = [item for item in raw_history if isinstance(item, str)]
    else:
        history = []
    for state in states:
        history = _append_state(history, state)
    return history


def _validate_run_current_scene(
    run_document: Mapping[str, object],
    scene_ids: Sequence[str],
) -> None:
    """Validate the resumable run's current scene as a known scene ID."""

    current_scene = run_document.get("current_scene")
    if current_scene is None:
        return
    if not isinstance(current_scene, str) or current_scene not in set(scene_ids):
        raise ValueError("run current_scene must be a known scene ID or null")


def _safe_child_path(base: Path, relative_path: str, *, label: str) -> Path:
    """Resolve a path beneath a known artifact root."""

    relative = Path(relative_path)
    if (
        relative.is_absolute()
        or "\\" in relative_path
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError(f"{label} path must be safe and relative")
    root = base.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} path escapes its root") from exc
    return candidate


def _validate_reusable_source_tree(
    root: Path,
    *,
    label: str,
    expected_root_files: set[str] | None = None,
) -> None:
    """Reject unsafe base evidence before any selective clone is created."""

    if root.is_symlink():
        raise ValueError(f"{label} contains symlink: {root}")
    if not root.is_dir():
        raise ValueError(f"{label} contains an unmanifested non-regular root: {root}")

    observed_root_entries: set[str] = set()
    pending = [root]
    while pending:
        current = pending.pop()
        try:
            entries = list(os.scandir(current))
        except OSError as exc:
            raise ValueError(f"{label} cannot be inspected: {exc}") from exc
        for entry in entries:
            entry_path = Path(entry.path)
            if current == root:
                observed_root_entries.add(entry.name)
            if entry.is_symlink():
                raise ValueError(f"{label} contains symlink: {entry_path}")
            if entry.is_dir(follow_symlinks=False):
                pending.append(entry_path)
            elif not entry.is_file(follow_symlinks=False):
                raise ValueError(
                    f"{label} contains an unmanifested non-regular entry: {entry_path}"
                )

    if expected_root_files is None:
        return
    missing = expected_root_files - observed_root_entries
    if missing:
        names = ", ".join(sorted(missing))
        raise ValueError(f"{label} is missing manifest entries: {names}")
    extras = observed_root_entries - expected_root_files
    if extras:
        names = ", ".join(sorted(extras))
        raise ValueError(f"{label} contains unmanifested entries: {names}")


def _record_path(
    record: Mapping[str, object],
    key: str,
    *,
    scene_id: str,
) -> Path:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"scene {scene_id} is missing {key} evidence")
    return Path(value).resolve()


def _require_record_path(
    record: Mapping[str, object],
    key: str,
    expected: Path,
    *,
    scene_id: str,
) -> None:
    actual = _record_path(record, key, scene_id=scene_id)
    if actual != expected.resolve():
        raise ValueError(f"scene {scene_id} {key} does not match its canonical path")


def _require_path_inside(path: Path, root: Path, *, label: str) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} path is outside its canonical root") from exc


def _record_attempts(record: Mapping[str, object]) -> int:
    value = record.get("attempts", 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("scene attempts must be a non-negative integer")
    return value


def _project_attempt_base(record: Mapping[str, object]) -> int:
    """Return the greatest project-level attempt number already recorded."""

    stored = record.get("attempts", 0)
    base = stored if isinstance(stored, int) and not isinstance(stored, bool) else 0
    history = record.get("attempt_history")
    if not isinstance(history, list):
        return max(0, base)
    for item in history:
        if not isinstance(item, dict):
            continue
        value = item.get("attempt")
        if isinstance(value, int) and not isinstance(value, bool):
            base = max(base, value)
    return max(0, base)


def _pipeline_attempt_history(
    record: Mapping[str, object],
    run_path: Path,
    pipeline_state: PipelineState,
    attempt_count: int,
    project_attempt_base: int,
) -> list[dict[str, object]]:
    """Translate every inner RITL attempt into project-level evidence."""

    del record
    if attempt_count <= 0:
        raise ValueError("render pipeline returned no attempt evidence")
    run_document = _load_json_document(run_path / "run.json")
    raw_attempts_value = run_document.get("attempts")
    if (
        not isinstance(raw_attempts_value, list)
        or len(raw_attempts_value) < attempt_count
    ):
        raise ValueError("render pipeline attempt evidence is incomplete")
    raw_attempts: list[object] = [item for item in raw_attempts_value]

    entries: list[dict[str, object]] = []
    for offset, raw_attempt in enumerate(raw_attempts[:attempt_count]):
        attempt = _json_object(raw_attempt)
        if attempt is None:
            raise ValueError("render pipeline attempt evidence must be an object")
        attempt_path_value = attempt.get("path")
        if not isinstance(attempt_path_value, str) or not attempt_path_value:
            raise ValueError("render pipeline attempt evidence has no path")
        attempt_path = Path(attempt_path_value).resolve()
        _require_path_inside(
            attempt_path,
            run_path,
            label="render pipeline attempt",
        )
        inner_attempt = attempt.get("attempt")
        if isinstance(inner_attempt, bool) or not isinstance(inner_attempt, int):
            inner_attempt = offset + 1
        terminal_state = attempt.get("terminal_state")
        if not isinstance(terminal_state, str) or not terminal_state:
            terminal_state = str(attempt.get("state", pipeline_state.value))
        diagnostics = _load_json_document(attempt_path / "diagnostics.json")
        if not diagnostics:
            attempt_diagnostics = attempt.get("diagnostics")
            if isinstance(attempt_diagnostics, dict):
                diagnostics = {
                    key: value
                    for key, value in attempt_diagnostics.items()
                    if isinstance(key, str)
                }
        temporal = _load_json_document(
            attempt_path / "temporal-normalization.json"
        )
        if not temporal:
            attempt_temporal = attempt.get("temporal_normalization")
            if isinstance(attempt_temporal, dict):
                temporal = {
                    key: value
                    for key, value in attempt_temporal.items()
                    if isinstance(key, str)
                }
        entry: dict[str, object] = {
            "attempt": project_attempt_base + offset + 1,
            "pipeline_attempt": inner_attempt,
            "state": "ready" if terminal_state == PipelineState.SUCCESS.value else "failed",
            "pipeline_state": terminal_state,
            "run_path": str(run_path),
            "attempt_path": str(attempt_path),
            "state_history": attempt.get("state_history", []),
            "diagnostics": diagnostics,
            "error": attempt.get("error"),
        }
        mp4_path = attempt.get("mp4_path")
        if isinstance(mp4_path, str) and mp4_path:
            entry["mp4_path"] = mp4_path
        if temporal:
            entry["temporal_normalization"] = temporal
        entries.append(entry)
    return entries


def _extend_attempt_history(
    record: dict[str, object],
    entries: Sequence[dict[str, object]],
    *,
    final_artifacts: Mapping[str, str] | None = None,
) -> None:
    """Append new inner attempts once, optionally decorating the final one."""

    history = record.get("attempt_history")
    if not isinstance(history, list):
        history = []
        record["attempt_history"] = history
    existing_paths = {
        item.get("attempt_path")
        for item in history
        if isinstance(item, dict) and isinstance(item.get("attempt_path"), str)
    }
    for entry in entries:
        attempt_path = entry.get("attempt_path")
        if attempt_path in existing_paths:
            continue
        history.append(entry)
        if isinstance(attempt_path, str):
            existing_paths.add(attempt_path)
    if final_artifacts is not None and entries:
        entries[-1].update(final_artifacts)


def _scene_pipeline_run_id(run_id: str, order: int, attempt: int) -> str:
    if attempt <= 1:
        return f"{run_id}-{order:02d}"
    return f"{run_id}-{order:02d}-retry-{attempt:02d}"


def _next_scene_pipeline_run_id(
    root: Path,
    run_id: str,
    order: int,
    attempt: int,
) -> str:
    """Choose the first unused inner-run identifier while preserving old runs."""

    base_id = _scene_pipeline_run_id(run_id, order, attempt)
    candidate = base_id
    suffix = 2
    while (root / candidate).exists():
        candidate = f"{base_id}-retry-{suffix:02d}"
        suffix += 1
    return candidate


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_reusable_final_attestation(
    run_path: Path,
    run_document: Mapping[str, object],
    *,
    required: bool = False,
) -> None:
    """Reject a failed run carrying a stale ready-final attestation."""

    sha256 = run_document.get("final_sha256")
    size_bytes = run_document.get("final_size_bytes")
    if sha256 is None and size_bytes is None:
        if required:
            raise ValueError("stored final attestation is required; start a new run explicitly")
        return
    if not isinstance(sha256, str) or len(sha256) != 64:
        raise ValueError("stored final_sha256 is invalid; start a new run explicitly")
    if any(character not in "0123456789abcdef" for character in sha256):
        raise ValueError("stored final_sha256 is invalid; start a new run explicitly")
    if (
        isinstance(size_bytes, bool)
        or not isinstance(size_bytes, int)
        or size_bytes <= 0
    ):
        raise ValueError("stored final_size_bytes is invalid; start a new run explicitly")
    final_path = run_path / "final.mp4"
    if not final_path.is_file():
        raise ValueError("stored final attestation has no final MP4; start a new run explicitly")
    actual_size = final_path.stat().st_size
    if size_bytes != actual_size or sha256 != _sha256_file(final_path):
        raise ValueError(
            "stored final attestation does not match final.mp4; "
            "start a new run explicitly"
        )


def _project_document(project: Project, **updates: object) -> dict[str, object]:
    """Serialize one project with a small validated lifecycle update."""

    document: dict[str, object] = project.model_dump(mode="json")
    document.update(updates)
    return document


def _validate_project_document(document: Mapping[str, object]) -> None:
    """Validate lifecycle updates before an atomic project replacement."""

    Project.model_validate_json(json.dumps(document, ensure_ascii=False))


def _resolve_project_file(project_root: Path, relative_path: str, *, label: str) -> Path:
    """Resolve one persisted project reference without allowing path escape."""

    relative = Path(relative_path)
    if (
        relative.is_absolute()
        or "\\" in relative_path
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError(f"project {label} reference must be a safe relative path")
    candidate = (project_root / relative).resolve()
    try:
        candidate.relative_to(project_root)
    except ValueError as exc:
        raise ValueError(f"project {label} reference escapes its project") from exc
    if not candidate.is_file():
        raise ValueError(f"project {label} does not exist: {candidate}")
    return candidate


def _relative_project_path(project_root: Path, path: Path) -> str:
    """Serialize an artifact path relative to the canonical project root."""

    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"artifact path escapes the project: {path}") from exc


def _scene_context(segment: TimelineSegment) -> dict[str, object]:
    """Return the bounded neighboring-scene context sent to the provider."""

    return {
        "id": segment.id,
        "start_seconds": segment.start_seconds,
        "end_seconds": segment.end_seconds,
    }


def _resolve_scene_selector(
    project_root: Path,
    project: Project,
    segments: Sequence[TimelineSegment],
    selector: str | None,
) -> str | None:
    """Resolve one explicit scene ID or generated class name before mutation."""

    if selector is None:
        return None
    value = selector.strip()
    if not value:
        raise ValueError("scene selector must not be blank")
    matches: list[str] = []
    for segment, scene_ref in zip(segments, project.scenes, strict=True):
        plan_path = _resolve_project_file(project_root, scene_ref.plan_path, label="plan")
        plan = ScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
        if value == segment.id or value == plan.scene_name:
            matches.append(segment.id)
    if not matches:
        raise ValueError(f"unknown scene selector: {selector}")
    if len(matches) > 1:
        raise ValueError(f"ambiguous scene selector: {selector}")
    return matches[0]


def _load_json_document(path: Path) -> dict[str, object]:
    """Read an artifact document while keeping malformed evidence explicit."""

    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {key: value for key, value in loaded.items() if isinstance(key, str)}


def _validation_duration(path: Path) -> float:
    """Read the positive raw duration retained by :class:`RenderPipeline`."""

    document = _load_json_document(path)
    value = document.get("duration_seconds")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"raw validation duration is unavailable: {path}")
    duration = float(value)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError(f"raw validation duration is not positive: {path}")
    return duration


def _copy_file_atomic(source: Path, destination: Path) -> None:
    """Copy one accepted artifact through a same-directory atomic replace."""

    if not source.is_file():
        raise ValueError(f"artifact source is not a file: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with source.open("rb") as source_handle, tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            delete=False,
        ) as target_handle:
            temporary = Path(target_handle.name)
            for chunk in iter(lambda: source_handle.read(1024 * 1024), b""):
                target_handle.write(chunk)
            target_handle.flush()
            os.fsync(target_handle.fileno())
        temporary.replace(destination)
    except BaseException:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def _atomic_write_json_file(path: Path, document: object) -> None:
    """Persist one JSON evidence document with flush, fsync, and replace."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        document,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        default=str,
    ) + "\n"
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
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except BaseException:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def _concat_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "'\\''")


def _composition_argv(
    list_path: Path,
    narration_path: Path,
    temporary_path: Path,
    expected_duration_seconds: float,
    profile: CompositionProfile,
) -> list[str]:
    width, height = profile.resolution
    argv = [
        "ffmpeg",
        "-v",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-i",
        str(narration_path.resolve()),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-vf",
        f"scale={width}:{height}",
        "-s",
        f"{width}x{height}",
        "-r",
        str(profile.fps),
        "-video_track_timescale",
        str(profile.timebase),
        "-t",
        f"{expected_duration_seconds:.6f}",
        "-c:v",
        profile.video_codec,
        "-c:a",
        profile.audio_codec,
        "-pix_fmt",
        profile.pixel_format,
    ]
    if profile.faststart:
        argv.extend(["-movflags", "+faststart"])
    argv.extend(["-y", str(temporary_path)])
    return argv


def _temporary_output_path(output_path: Path) -> Path:
    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=output_path.parent,
        prefix=f".{output_path.name}.",
        suffix=".tmp.mp4",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
    temporary.unlink()
    return temporary


def _remove_temporary(path: Path) -> None:
    path.unlink(missing_ok=True)


def _elapsed_since(started: float) -> float:
    return max(0.0, time.monotonic() - started)


def _run_ffmpeg(
    args: Sequence[str],
    *,
    capture_output: bool,
    text: bool,
    timeout: float,
    check: bool,
) -> subprocess.CompletedProcess[str]:
    if not text:
        raise ValueError("ffmpeg output must be captured as text")
    return subprocess.run(
        list(args),
        capture_output=capture_output,
        text=True,
        timeout=timeout,
        check=check,
    )


def _composition_document(result: CompositionResult) -> dict[str, object]:
    return result.to_document()


def _write_json(path: Path, document: object) -> None:
    _write_text(
        path,
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _write_text(path: Path, value: str) -> None:
    """Write one persistent text artifact through a durable same-dir replace."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


__all__ = [
    "CompositionProfile",
    "CompositionResult",
    "FinalCompositionValidator",
    "FFmpegComposer",
    "ProjectPipelineEvent",
    "TemporalNormalizer",
    "VideoComposer",
    "VideoPipeline",
    "VideoResult",
]
