"""Canonical project contracts and atomic input initialization."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from video_pipeline.expectations import SceneExpectations
from video_pipeline.scene_plan import ScenePlan
from video_pipeline.theme import VideoTheme
from video_pipeline.timeline import (
    FFmpegSilenceDetector,
    PauseInterval,
    SceneBrief,
    SilenceDetector,
    SilenceSubprocessRun,
    Timeline,
    TimelineSegment,
    build_explicit_timeline,
    build_pause_aligned_timeline,
    confirm_timeline,
    load_timeline,
    parse_heading_sections,
)

_PROJECT_ID = re.compile(r"^[0-9]{4}_[a-z0-9]+(?:[_-][a-z0-9]+)*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ProjectState(str, Enum):
    """Lifecycle state for one canonical project."""

    draft = "draft"
    timeline_candidate = "timeline_candidate"
    timeline_confirmed = "timeline_confirmed"
    rendering = "rendering"
    ready = "ready"
    accepted = "accepted"
    failed = "failed"


class ProjectStageState(str, Enum):
    """State of planning, rendering, or composition work."""

    pending = "pending"
    review_required = "review_required"
    ready = "ready"
    failed = "failed"


class AudioMediaFacts(BaseModel):
    """Probe facts and immutable identity for the copied narration input."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    path: str
    hash: str = Field(pattern=_SHA256.pattern)
    container: str
    codec: str
    stream: int = Field(ge=0)
    sample_rate: int = Field(gt=0)
    channels: int = Field(gt=0)
    duration: float = Field(gt=0)
    size: int = Field(gt=0)
    probe_result: dict[str, object]

    @field_validator("path")
    @classmethod
    def _path_must_be_safe_relative(cls, value: str) -> str:
        if not _is_safe_relative_path(value):
            raise ValueError("audio path must be a safe relative path")
        return value

    @field_validator("container", "codec")
    @classmethod
    def _media_text_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("audio media facts must not contain blank text")
        return value


class ProjectSceneRef(BaseModel):
    """Ordered persistent references to one project's visual scene."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    order: int = Field(ge=1)
    path: str
    plan_path: str
    brief_path: str
    expectations_path: str

    @field_validator("path", "plan_path", "brief_path", "expectations_path")
    @classmethod
    def _references_must_be_safe_relative(cls, value: str) -> str:
        if not _is_safe_relative_path(value):
            raise ValueError("scene references must be safe relative paths")
        return value


class Project(BaseModel):
    """Persistent identity created before any planning or rendering."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["project/1"] = "project/1"
    id: str = Field(pattern=_PROJECT_ID.pattern)
    title: str
    status: ProjectState = ProjectState.draft
    script_path: str
    script_sha256: str = Field(pattern=_SHA256.pattern)
    audio_path: str
    audio: AudioMediaFacts
    timeline_path: str | None = None
    scenes: list[ProjectSceneRef] = Field(default_factory=list)
    theme: VideoTheme = Field(default_factory=VideoTheme.production)
    current_run: str | None = None
    current_scene: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9-]*$",
    )
    accepted_run: str | None = None
    planning_state: ProjectStageState = ProjectStageState.pending
    render_state: ProjectStageState = ProjectStageState.pending
    composition_state: ProjectStageState = ProjectStageState.pending

    @field_validator("title")
    @classmethod
    def _title_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must not be blank")
        return value

    @field_validator("script_path", "audio_path", "timeline_path")
    @classmethod
    def _references_must_be_safe_relative(cls, value: str | None) -> str | None:
        if value is not None and not _is_safe_relative_path(value):
            raise ValueError("project references must be safe relative paths")
        return value

    @model_validator(mode="after")
    def _current_scene_must_be_known(self) -> Project:
        if self.current_scene is not None and self.current_scene not in {
            scene.id for scene in self.scenes
        }:
            raise ValueError("current_scene must be a known project scene or null")
        return self


class AudioProbe(Protocol):
    """Replaceable boundary for independent narration probing."""

    def __call__(self, path: Path) -> AudioMediaFacts | Mapping[str, object]:
        """Return complete facts for one audio file."""


def initialize_project(
    destination: str | Path,
    *,
    title: str,
    script: str | Path,
    audio: str | Path,
    audio_probe: AudioProbe | None = None,
    silence_detector: SilenceDetector | None = None,
    silence_subprocess_run: SilenceSubprocessRun | None = None,
) -> Project:
    """Create one canonical project without invoking planning or rendering."""

    project_path = _canonical_project_path(destination)
    script_path = _require_file(script, label="script")
    audio_path = _require_file(audio, label="audio")
    script_bytes = _read_utf8(script_path)

    probe = audio_probe or probe_audio
    project_path.parent.mkdir(parents=True, exist_ok=True)
    staging_path = Path(
        tempfile.mkdtemp(prefix=f".{project_path.name}.", dir=str(project_path.parent))
    )

    script_relative = "script.md"
    audio_relative = f"audio/narration{audio_path.suffix}"
    try:
        script_hash, _ = _copy_atomic(script_path, staging_path / script_relative)
        audio_hash, audio_size = _copy_atomic(
            audio_path,
            staging_path / audio_relative,
        )
        raw_facts = probe(staging_path / audio_relative)
        facts = _facts_for_project(
            raw_facts,
            relative_path=audio_relative,
            digest=audio_hash,
            size=audio_size,
        )
        theme = VideoTheme.production()
        script_text = script_bytes.decode("utf-8")
        explicit = build_explicit_timeline(
            script_text,
            facts.duration,
            theme=theme,
        )
        timeline = explicit[0] if explicit is not None else None
        plans = explicit[1] if explicit is not None else ()
        if timeline is None:
            heading_sections = parse_heading_sections(script_text)
            if heading_sections is not None:
                detector = silence_detector or FFmpegSilenceDetector(
                    subprocess_run=silence_subprocess_run
                )
                pause_aligned = build_pause_aligned_timeline(
                    script_text,
                    facts.duration,
                    detector(staging_path / audio_relative),
                    theme=theme,
                )
                timeline = pause_aligned[0] if pause_aligned is not None else None
                plans = pause_aligned[1] if pause_aligned is not None else ()
        scene_references: list[ProjectSceneRef] = []
        if timeline is not None:
            for segment, plan in zip(timeline.segments, plans, strict=True):
                plan_path = staging_path / segment.plan_path
                plan_path.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write_json(plan_path, plan.to_document())
                brief_relative = _brief_path_for_segment(segment)
                expectations_relative = _expectations_path_for_segment(segment)
                _atomic_write_json(
                    staging_path / brief_relative,
                    _brief_for_segment(segment).to_document(),
                )
                _atomic_write_json(
                    staging_path / expectations_relative,
                    _expectations_document(plan),
                )
                scene_references.append(
                    ProjectSceneRef(
                        id=segment.id,
                        order=segment.order,
                        path=segment.plan_path.removesuffix("/plan.json"),
                        plan_path=segment.plan_path,
                        brief_path=brief_relative,
                        expectations_path=expectations_relative,
                    )
                )
            _atomic_write_json(staging_path / "timeline.json", timeline.to_document())
        timeline_confirmed = timeline is not None and timeline.method == "explicit_timestamp"
        timeline_candidate = timeline is not None and timeline.status == "candidate"
        project = Project(
            id=project_path.name,
            title=title,
            status=(
                ProjectState.timeline_confirmed
                if timeline_confirmed
                else ProjectState.timeline_candidate
                if timeline_candidate
                else ProjectState.draft
            ),
            script_path=script_relative,
            script_sha256=script_hash,
            audio_path=audio_relative,
            audio=facts,
            timeline_path="timeline.json" if timeline is not None else None,
            scenes=scene_references,
            theme=theme,
            planning_state=(
                ProjectStageState.ready
                if timeline_confirmed
                else ProjectStageState.review_required
                if timeline_candidate
                else ProjectStageState.pending
            ),
        )
        serialized_project: object = json.loads(project.model_dump_json())
        document = _object(serialized_project)
        if document is None:
            raise ValueError("project document must be a JSON object")
        _atomic_write_json(staging_path / "project.json", document)
        try:
            staging_path.rename(project_path)
        except FileExistsError as exc:
            raise ValueError(f"project already exists: {project_path}") from exc
        return project
    except BaseException:
        shutil.rmtree(staging_path, ignore_errors=True)
        raise


def load_project(path: str | Path) -> Project:
    """Load and strictly validate one persistent project document."""

    return Project.model_validate_json(Path(path).read_text(encoding="utf-8"))


def inspect_project(path: str | Path) -> dict[str, object]:
    """Return read-only lifecycle and artifact facts for one canonical project."""

    project_json = Path(path).resolve()
    project = load_project(project_json)
    _validate_project_identity(project_json, project)
    project_root = project_json.parent
    timeline_summary = _inspect_timeline(project_json, project)
    scenes = [
        {
            "id": scene.id,
            "order": scene.order,
            "path": scene.path,
            "plan_path": scene.plan_path,
            "brief_path": scene.brief_path,
            "expectations_path": scene.expectations_path,
        }
        for scene in sorted(project.scenes, key=_project_scene_order)
    ]
    serialized_audio: object = json.loads(project.audio.model_dump_json())
    audio = _object(serialized_audio)
    if audio is None:
        raise ValueError("project audio facts must be a JSON object")
    audio.pop("probe_result", None)
    latest_run = _inspect_latest_run(project_root, project)
    progress = (
        latest_run.get("progress")
        if isinstance(latest_run, dict)
        else _project_progress(project)
    )
    return {
        "project": {
            "id": project.id,
            "title": project.title,
            "status": project.status.value,
            "script_path": project.script_path,
            "script_sha256": project.script_sha256,
            "audio_path": project.audio_path,
            "timeline_path": project.timeline_path,
            "current_run": project.current_run,
            "current_scene": project.current_scene,
            "accepted_run": project.accepted_run,
            "planning_state": project.planning_state.value,
            "render_state": project.render_state.value,
            "composition_state": project.composition_state.value,
        },
        "audio": audio,
        "timeline": timeline_summary,
        "progress": progress,
        "scenes": scenes,
        "latest_run": latest_run,
    }


def validate_project_timeline(path: str | Path) -> tuple[Project, Timeline]:
    """Load a project and its referenced timeline without changing either document."""

    project_path = Path(path).resolve()
    project = load_project(project_path)
    _validate_project_identity(project_path, project)
    timeline_path = _timeline_path(project_path, project)
    timeline = load_timeline(timeline_path)
    _validate_project_timeline_contract(project, timeline)
    _load_project_plans(project_path, project, timeline)
    return project, timeline


def confirm_project_timeline(path: str | Path) -> tuple[Project, Timeline]:
    """Confirm a candidate after strict coverage validation and an atomic update."""

    project_path = Path(path).resolve()
    project = load_project(project_path)
    _validate_project_identity(project_path, project)
    timeline_path = _timeline_path(project_path, project)
    timeline = load_timeline(timeline_path)
    _validate_project_timeline_contract(project, timeline)
    if timeline.status != "candidate":
        raise ValueError("only a candidate timeline can be confirmed")
    confirmed_timeline = confirm_timeline(timeline)
    plans = _load_project_plans(
        project_path,
        project,
        timeline,
        validate_package=False,
    )
    serialized_project: object = json.loads(project.model_dump_json())
    project_document = _object(serialized_project)
    if project_document is None:
        raise ValueError("project document must be a JSON object")
    project_document["status"] = ProjectState.timeline_confirmed.value
    project_document["planning_state"] = ProjectStageState.ready.value
    confirmed_project = Project.model_validate_json(json.dumps(project_document))
    timeline_path = _timeline_path(project_path, project)
    plan_updates: list[tuple[Path, Mapping[str, object]]] = []
    package_updates: list[tuple[Path, Mapping[str, object]]] = []
    scenes_by_id = {scene.id: scene for scene in project.scenes}
    for plan_path, plan, segment in plans:
        synchronized_plan = _synchronized_plan(plan, segment)
        plan_updates.append((plan_path, synchronized_plan.to_document()))
        scene_ref = scenes_by_id[segment.id]
        brief_path = _safe_project_path(
            project_path.parent,
            scene_ref.brief_path,
            label="scene brief",
        )
        expectations_path = _safe_project_path(
            project_path.parent,
            scene_ref.expectations_path,
            label="scene expectations",
        )
        package_updates.extend(
            (
                (brief_path, _brief_for_segment(segment).to_document()),
                (expectations_path, _expectations_document(synchronized_plan)),
            )
        )
    _atomic_update_json_documents(
        (
            (project_path, project_document),
            (timeline_path, confirmed_timeline.to_document()),
            *plan_updates,
            *package_updates,
        )
    )
    return confirmed_project, confirmed_timeline


def _inspect_timeline(project_json: Path, project: Project) -> dict[str, object]:
    """Read the referenced timeline without probing or inferring any facts."""

    relative_path = project.timeline_path
    if relative_path is None:
        return {
            "path": None,
            "status": "missing",
            "method": None,
            "duration_seconds": None,
            "warnings": [],
            "manual_review_reasons": [],
            "timing_limitations": [],
            "segments": [],
            "error": "project has no timeline reference",
        }
    try:
        timeline_path = _safe_project_path(
            project_json.parent,
            relative_path,
            label="timeline",
        )
        timeline = load_timeline(timeline_path)
    except (OSError, ValueError) as exc:
        return {
            "path": relative_path,
            "status": "invalid",
            "method": None,
            "duration_seconds": None,
            "warnings": [],
            "manual_review_reasons": [],
            "timing_limitations": [],
            "segments": [],
            "error": _safe_error_text(exc, project_json.parent),
        }
    warnings = [
        _safe_operator_text(warning, project_json.parent)
        for warning in timeline.warnings
    ]
    manual_review_reasons = [
        _safe_operator_text(reason, project_json.parent)
        for reason in timeline.manual_review_reasons
    ]
    timing_limitations = [*warnings, *manual_review_reasons]
    if not any(
        "ASR" in limitation or "forced alignment" in limitation
        for limitation in timing_limitations
    ):
        timing_limitations.append(
            "Spoken-content correspondence remains unverified without ASR or "
            "forced alignment."
        )
    return {
        "path": relative_path,
        "status": timeline.status,
        "method": timeline.method,
        "duration_seconds": timeline.duration_seconds,
        "warnings": warnings,
        "manual_review_reasons": manual_review_reasons,
        "timing_limitations": timing_limitations,
        "segments": [
            {
                "id": segment.id,
                "order": segment.order,
                "narration_text": segment.narration_text,
                "objective": segment.objective,
                "start_seconds": segment.start_seconds,
                "end_seconds": segment.end_seconds,
                "target_duration_seconds": segment.target_duration_seconds,
                "start_provenance": segment.start_provenance,
                "end_provenance": segment.end_provenance,
                "plan_path": segment.plan_path,
            }
            for segment in timeline.segments
        ],
        "error": None,
    }


def _inspect_latest_run(project_root: Path, project: Project) -> dict[str, object] | None:
    """Read only the current run's project-relative evidence, if one exists."""

    run_id = project.current_run
    if run_id is None:
        return None
    if not _is_safe_relative_path(run_id):
        return {
            "run_id": _safe_operator_text(run_id, project_root),
            "status": "invalid",
            "path": None,
            "state": None,
            "current_scene": None,
            "action_next": None,
            "error": "current_run is not a safe relative run identifier",
            "scenes": [],
            "artifacts": {},
        }
    run_relative = f"artifacts/{run_id}/run.json"
    try:
        resolved_run = _safe_project_path(project_root, run_relative, label="run")
        loaded: object = json.loads(resolved_run.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError) as exc:
        missing = isinstance(exc, OSError) or "does not exist" in str(exc)
        return {
            "run_id": run_id,
            "status": "missing" if missing else "invalid",
            "path": run_relative,
            "state": None,
            "current_scene": None,
            "action_next": None,
            "error": (
                "run evidence is missing"
                if missing
                else "run evidence is invalid JSON"
            ),
            "scenes": [],
            "artifacts": {},
        }
    if not isinstance(loaded, dict):
        return {
            "run_id": run_id,
            "status": "invalid",
            "path": run_relative,
            "state": None,
            "current_scene": None,
            "action_next": None,
            "error": "run document must be a JSON object",
            "scenes": [],
            "artifacts": {},
        }
    loaded_document = _object(loaded)
    if loaded_document is None:
        raise ValueError("run document must be a JSON object")
    records = loaded_document.get("scenes")
    record_by_id = {
        record.get("id"): record
        for record in records
        if isinstance(record, dict) and isinstance(record.get("id"), str)
    } if isinstance(records, list) else {}
    scene_summaries: list[dict[str, object]] = []
    for scene in sorted(project.scenes, key=_project_scene_order):
        record = record_by_id.get(scene.id)
        scene_relative = f"artifacts/{run_id}/{scene.path}"
        scene_state = (
            record.get("state")
            if isinstance(record, dict) and isinstance(record.get("state"), str)
            else "missing"
        )
        attempts = (
            record.get("attempts", 0)
            if isinstance(record, dict) and isinstance(record.get("attempts", 0), int)
            else 0
        )
        scene_summary: dict[str, object] = {
            "id": scene.id,
            "order": scene.order,
            "state": scene_state,
            "attempts": attempts,
            "artifacts": {
                "raw": _inspect_artifact(project_root, f"{scene_relative}/raw.mp4"),
                "normalized": _inspect_artifact(
                    project_root,
                    f"{scene_relative}/normalized.mp4",
                ),
                "normalization": _inspect_artifact(
                    project_root,
                    f"{scene_relative}/normalization.json",
                ),
                "code": _inspect_artifact(
                    project_root,
                    f"{scene_relative}/scene.py",
                ),
                "provenance": _inspect_artifact(
                    project_root,
                    f"{scene_relative}/code-provenance.json",
                ),
            },
            "temporal_correction": _inspect_temporal_correction(
                project_root,
                f"{scene_relative}/normalization.json",
            ),
            "action_next": (
                _safe_optional_operator_text(record.get("action_next"), project_root)
                if isinstance(record, dict)
                else "inspect missing scene run evidence"
            ),
            "error": (
                _safe_error_text(record.get("error"), project_root)
                if isinstance(record, dict)
                else None
            ),
        }
        scene_summaries.append(scene_summary)
    final_artifact = _inspect_artifact(project_root, f"artifacts/{run_id}/final.mp4")
    return {
        "run_id": run_id,
        "status": "present",
        "path": run_relative,
        "state": _safe_optional_operator_text(
            loaded_document.get("state"), project_root
        ),
        "current_scene": _safe_optional_operator_text(
            loaded_document.get("current_scene"), project_root
        ),
        "action_next": _safe_optional_operator_text(
            loaded_document.get("action_next"), project_root
        ),
        "error": _safe_error_text(loaded_document.get("error"), project_root),
        "progress": _inspect_progress(records, len(project.scenes)),
        "scenes": scene_summaries,
        "composition": _inspect_composition(
            project_root,
            loaded_document,
            final_artifact,
            project.composition_state.value,
        ),
        "artifacts": {
            "final": final_artifact,
            "composition": _inspect_artifact(
                project_root,
                f"artifacts/{run_id}/composition.json",
            ),
        },
    }


def _project_progress(project: Project) -> dict[str, int]:
    """Describe queued scene progress before a render run exists."""

    return {
        "total": len(project.scenes),
        "queued": len(project.scenes),
        "rendering": 0,
        "ready": 0,
        "failed": 0,
    }


def _inspect_progress(records: object, total: int) -> dict[str, int]:
    """Count known scene lifecycle states without trusting persisted paths."""

    progress = {
        "total": total,
        "queued": 0,
        "rendering": 0,
        "ready": 0,
        "failed": 0,
    }
    if not isinstance(records, list):
        return progress
    for value in records:
        record = _object(value)
        if record is None:
            continue
        state = record.get("state")
        if isinstance(state, str) and state in {
            "queued",
            "rendering",
            "ready",
            "failed",
        }:
            progress[state] += 1
    return progress


def _inspect_temporal_correction(
    project_root: Path,
    relative_path: str,
) -> dict[str, object]:
    """Summarize normalization evidence without exposing its paths."""

    try:
        target = _safe_project_path(project_root, relative_path, label="normalization")
        loaded: object = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError) as exc:
        status = (
            "missing"
            if isinstance(exc, OSError) or "does not exist" in str(exc)
            else "invalid"
        )
        return _temporal_error_summary(
            status,
            "normalization evidence is missing"
            if status == "missing"
            else "normalization evidence is invalid JSON",
        )
    document = _object(loaded)
    if document is None:
        return _temporal_error_summary(
            "invalid",
            "normalization evidence must be a JSON object",
        )
    status_value = document.get("status")
    if status_value not in {
        "accepted",
        "normalized",
        "requires_regeneration",
        "failed",
    }:
        return _temporal_error_summary(
            "invalid",
            "normalization evidence has an unsupported status",
        )
    argv = document.get("argv")
    joined_argv = (
        " ".join(item for item in argv if isinstance(item, str))
        if isinstance(argv, list)
        else ""
    )
    if status_value == "accepted":
        operation = "none"
    elif status_value == "requires_regeneration":
        operation = "regenerate"
    elif status_value == "failed":
        operation = "failed"
    elif "trim=" in joined_argv:
        operation = "trim"
    elif "tpad=stop_mode=clone" in joined_argv:
        operation = "freeze_frame"
    else:
        operation = "normalize"
    raw_reasons = document.get("validation_reasons")
    reasons = (
        [_safe_operator_text(item, project_root) for item in raw_reasons]
        if isinstance(raw_reasons, list)
        else []
    )
    return {
        "status": status_value,
        "operation": operation,
        "observed_duration_seconds": document.get("observed_duration_seconds"),
        "target_duration_seconds": document.get("target_duration_seconds"),
        "delta_seconds": document.get("delta_seconds"),
        "validated_duration_seconds": document.get("validated_duration_seconds"),
        "reasons": reasons,
    }


def _temporal_error_summary(status: str, error: str) -> dict[str, object]:
    """Build a stable malformed/missing normalization summary."""

    return {
        "status": status,
        "operation": "failed",
        "observed_duration_seconds": None,
        "target_duration_seconds": None,
        "delta_seconds": None,
        "validated_duration_seconds": None,
        "reasons": [],
        "error": error,
    }


def _inspect_composition(
    project_root: Path,
    run_document: Mapping[str, object],
    final_artifact: Mapping[str, object],
    composition_state: str,
) -> dict[str, object]:
    """Summarize composition and validation without raw process documents."""

    composition = _object(run_document.get("composition"))
    run_state = run_document.get("state")
    state = composition_state
    if composition is None:
        composition_error = (
            "composition evidence is missing"
            if run_document.get("composition") is None
            else "composition evidence must be a JSON object"
        )
        exit_code: object = None
        elapsed_seconds: object = None
        error: object = (
            run_document.get("error") if run_state == "failed" else composition_error
        )
        validation_value = run_document.get("final_validation")
    else:
        exit_code = composition.get("exit_code")
        elapsed_seconds = composition.get("elapsed_seconds")
        error = composition.get("error")
        validation_value = composition.get("validation")
        if validation_value is None:
            validation_value = run_document.get("final_validation")
    return {
        "state": state,
        "exit_code": exit_code,
        "error": _safe_error_text(error, project_root),
        "elapsed_seconds": elapsed_seconds,
        "output": dict(final_artifact),
        "validation": _inspect_final_validation(validation_value, project_root),
    }


def _inspect_final_validation(
    value: object,
    project_root: Path,
) -> dict[str, object]:
    """Return compact final media facts without raw probe/path fields."""

    document = _object(value)
    if document is None:
        return {
            "status": "missing" if value is None else "invalid",
            "error": (
                "final validation evidence is missing"
                if value is None
                else "final validation evidence must be a JSON object"
            ),
        }
    reasons_value = document.get("reasons")
    reasons = (
        [_safe_operator_text(item, project_root) for item in reasons_value]
        if isinstance(reasons_value, list)
        else []
    )
    return {
        "valid": document.get("valid"),
        "reasons": reasons,
        "video_codecs": _inspect_codecs(document.get("video_streams")),
        "audio_codecs": _inspect_codecs(document.get("audio_streams")),
        "video_duration_seconds": document.get("video_duration_seconds"),
        "audio_duration_seconds": document.get("audio_duration_seconds"),
        "expected_duration_seconds": document.get("expected_duration_seconds"),
        "video_drift_seconds": document.get("video_drift_seconds"),
        "audio_drift_seconds": document.get("audio_drift_seconds"),
        "audio_video_drift_seconds": document.get("audio_video_drift_seconds"),
    }


def _inspect_codecs(value: object) -> list[str]:
    """Extract codec names from serialized stream projections."""

    if not isinstance(value, list):
        return []
    codecs: list[str] = []
    for item in value:
        stream = _object(item)
        codec = stream.get("codec_name") if stream is not None else None
        if isinstance(codec, str) and codec not in codecs:
            codecs.append(codec)
    return codecs


def _safe_error_text(value: object, project_root: Path) -> str | None:
    """Keep operator errors useful without leaking an absolute project path."""

    if value is None:
        return None
    if not isinstance(value, str):
        return "invalid persisted error"
    return _safe_operator_text(value, project_root)


def _safe_operator_text(value: object, project_root: Path) -> str:
    """Keep persisted operator text useful without exposing the project root."""

    if not isinstance(value, str):
        return "invalid persisted text"
    return value.replace(str(project_root.resolve()), "<project>")


def _safe_optional_operator_text(value: object, project_root: Path) -> str | None:
    """Sanitize optional persisted operator text while preserving null values."""

    if value is None:
        return None
    return _safe_operator_text(value, project_root)


def _inspect_artifact(project_root: Path, relative_path: str) -> dict[str, object]:
    """Describe one optional artifact while rejecting unsafe references."""

    if not _is_safe_relative_path(relative_path):
        return {
            "path": "<invalid artifact path>",
            "status": "invalid",
            "exists": False,
            "size_bytes": None,
        }
    try:
        candidate = _safe_project_path(project_root, relative_path, label="artifact")
        size_bytes = candidate.stat().st_size
    except (OSError, ValueError) as exc:
        missing = isinstance(exc, OSError) or "does not exist" in str(exc)
        return {
            "path": relative_path,
            "status": "missing" if missing else "invalid",
            "exists": False,
            "size_bytes": None,
            "error": "artifact is missing" if missing else "artifact path is invalid",
        }
    return {
        "path": relative_path,
        "status": "present",
        "exists": True,
        "size_bytes": size_bytes,
    }


def _project_scene_order(scene: ProjectSceneRef) -> int:
    return scene.order


def _project_scene_id(scene: ProjectSceneRef) -> str:
    return scene.id


def probe_audio(path: str | Path) -> AudioMediaFacts:
    """Probe a narration file through the local ffprobe process boundary."""

    candidate = _require_file(path, label="audio")
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
        result = subprocess.run(argv, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise ValueError(f"ffprobe failed: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip()
        suffix = f": {detail}" if detail else ""
        raise ValueError(f"ffprobe failed{suffix}")
    try:
        document: object = json.loads(result.stdout)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"ffprobe output is unparseable: {exc}") from exc
    root = _object(document)
    if root is None:
        raise ValueError("ffprobe output must be a JSON object")
    streams = root.get("streams")
    audio_stream = _first_audio_stream(streams)
    if audio_stream is None:
        raise ValueError("audio has no usable audio stream")
    format_data = _object(root.get("format")) or {}
    container = _text(format_data.get("format_name"))
    codec = _text(audio_stream.get("codec_name"))
    stream = _stream_index(audio_stream.get("index"))
    sample_rate = _positive_int(audio_stream.get("sample_rate"))
    channels = _positive_int(audio_stream.get("channels"))
    duration = _positive_float(audio_stream.get("duration"))
    if duration is None:
        duration = _positive_float(format_data.get("duration"))
    size = _positive_int(format_data.get("size"))
    if container is None or codec is None or stream is None:
        raise ValueError("ffprobe audio facts are incomplete or non-positive")
    if sample_rate is None or channels is None or duration is None or size is None:
        raise ValueError("ffprobe audio facts are incomplete or non-positive")
    digest, actual_size = _file_sha256(candidate)
    if size != actual_size:
        size = actual_size
    return AudioMediaFacts(
        path=candidate.name,
        hash=digest,
        container=container,
        codec=codec,
        stream=stream,
        sample_rate=sample_rate,
        channels=channels,
        duration=duration,
        size=size,
        probe_result=root,
    )


def _canonical_project_path(destination: str | Path) -> Path:
    raw_path = Path(destination)
    if ".." in raw_path.parts:
        raise ValueError("project destination must not contain path traversal")
    project_path = raw_path.resolve()
    if _PROJECT_ID.fullmatch(project_path.name) is None:
        raise ValueError("project directory must use YYYY_slug naming")
    if project_path.exists() or project_path.is_symlink():
        raise ValueError(f"project already exists: {project_path}")
    return project_path


def _require_file(path: str | Path, *, label: str) -> Path:
    candidate = Path(path)
    if not candidate.is_file():
        raise ValueError(f"{label} input must be an existing file: {candidate}")
    return candidate.resolve()


def _read_utf8(path: Path) -> bytes:
    data = path.read_bytes()
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"script must be valid UTF-8: {path}") from exc
    return data


def _facts_for_project(
    raw_facts: AudioMediaFacts | Mapping[str, object],
    *,
    relative_path: str,
    digest: str,
    size: int,
) -> AudioMediaFacts:
    if isinstance(raw_facts, AudioMediaFacts):
        serialized: object = json.loads(raw_facts.model_dump_json())
        data = _object(serialized)
        if data is None:
            raise ValueError("audio probe facts must be a JSON object")
    elif isinstance(raw_facts, Mapping):
        data = dict(raw_facts)
    else:
        raise ValueError("audio probe must return AudioMediaFacts facts")
    data["path"] = relative_path
    data["hash"] = digest
    data["size"] = size
    return AudioMediaFacts.model_validate(data)


def _timeline_path(project_json_path: Path, project: Project) -> Path:
    relative_path = project.timeline_path
    if relative_path is None:
        raise ValueError("project has no timeline reference")
    project_root = project_json_path.resolve().parent
    return _safe_project_path(project_root, relative_path, label="timeline")


def _safe_project_path(project_root: Path, relative_path: str, *, label: str) -> Path:
    if not _is_safe_relative_path(relative_path):
        raise ValueError(f"project {label} reference must be a safe relative path")
    candidate = (project_root / relative_path).resolve()
    try:
        candidate.relative_to(project_root)
    except ValueError as exc:
        raise ValueError(f"project {label} reference escapes its project") from exc
    if not candidate.is_file():
        raise ValueError(f"project {label} does not exist: {candidate}")
    return candidate


def _validate_project_identity(project_json_path: Path, project: Project) -> None:
    project_directory = project_json_path.resolve().parent
    if project.id != project_directory.name:
        raise ValueError(
            "project id must match its directory: "
            f"{project.id!r} != {project_directory.name!r}"
        )


def _validate_project_timeline_contract(project: Project, timeline: Timeline) -> None:
    if abs(timeline.duration_seconds - project.audio.duration) > 1e-6:
        raise ValueError(
            f"timeline audio duration {timeline.duration_seconds:.6f}s does not match "
            f"project audio duration {project.audio.duration:.6f}s"
        )
    if timeline.status == "candidate":
        allowed_project_statuses = {ProjectState.timeline_candidate}
    else:
        allowed_project_statuses = {
            ProjectState.timeline_confirmed,
            ProjectState.rendering,
            ProjectState.ready,
            ProjectState.accepted,
            ProjectState.failed,
        }
    if project.status not in allowed_project_statuses:
        raise ValueError(
            "project status is incompatible with timeline status: "
            f"{project.status.value} vs {timeline.status}"
        )
    if len(project.scenes) != len(timeline.segments):
        raise ValueError("project and timeline scene counts do not agree")
    plan_paths = [scene.plan_path for scene in project.scenes]
    if len(plan_paths) != len(set(plan_paths)):
        raise ValueError("project scene plan references must be unique")
    brief_paths = [scene.brief_path for scene in project.scenes]
    if len(brief_paths) != len(set(brief_paths)):
        raise ValueError("project scene brief references must be unique")
    expectations_paths = [scene.expectations_path for scene in project.scenes]
    if len(expectations_paths) != len(set(expectations_paths)):
        raise ValueError("project scene expectations references must be unique")
    for project_scene, segment in zip(project.scenes, timeline.segments, strict=True):
        if project_scene.order != segment.order or project_scene.id != segment.id:
            raise ValueError("project and timeline scene IDs or order do not agree")
        if project_scene.plan_path != segment.plan_path:
            raise ValueError("project and timeline scene plan references do not agree")
        expected_scene_path = segment.plan_path.removesuffix("/plan.json")
        if project_scene.path != expected_scene_path:
            raise ValueError("project and timeline scene paths do not agree")
        if project_scene.brief_path != f"{expected_scene_path}/brief.json":
            raise ValueError("project scene brief reference does not agree")
        if project_scene.expectations_path != f"{expected_scene_path}/expectations.json":
            raise ValueError("project scene expectations reference does not agree")


def _load_project_plans(
    project_json_path: Path,
    project: Project,
    timeline: Timeline,
    *,
    validate_package: bool = True,
) -> tuple[tuple[Path, ScenePlan, TimelineSegment], ...]:
    project_root = project_json_path.resolve().parent
    plans: list[tuple[Path, ScenePlan, TimelineSegment]] = []
    for project_scene, segment in zip(project.scenes, timeline.segments, strict=True):
        plan_path = _safe_project_path(project_root, project_scene.plan_path, label="plan")
        plan = ScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
        if plan.id != segment.id:
            raise ValueError("scene plan identity does not agree with timeline")
        _validate_scene_package(
            project_root,
            project_scene,
            segment,
            plan,
            validate_agreement=validate_package,
        )
        plans.append((plan_path, plan, segment))
    return tuple(plans)


def _validate_scene_package(
    project_root: Path,
    project_scene: ProjectSceneRef,
    segment: TimelineSegment,
    plan: ScenePlan,
    *,
    validate_agreement: bool,
) -> None:
    brief_path = _safe_project_path(
        project_root,
        project_scene.brief_path,
        label="scene brief",
    )
    brief = SceneBrief.model_validate_json(brief_path.read_text(encoding="utf-8"))
    if validate_agreement and brief != _brief_for_segment(segment):
        raise ValueError("scene brief does not agree with timeline")
    if validate_agreement:
        _validate_plan_temporal_fields(plan, segment)
        expected_expectations = (
            SceneExpectations.model_validate(segment.expectations)
            if segment.expectations
            else None
        )
        if plan.expectations != expected_expectations:
            raise ValueError("scene plan expectations do not agree with timeline")
    expectations_path = _safe_project_path(
        project_root,
        project_scene.expectations_path,
        label="scene expectations",
    )
    loaded: object = json.loads(expectations_path.read_text(encoding="utf-8"))
    if validate_agreement and loaded != _expectations_document(plan):
        raise ValueError("scene expectations do not agree with scene plan")


def _project_package_hashes(project_root: Path, project: Project) -> dict[str, str]:
    """Hash each scene's ordered persisted plan package by scene ID."""

    package_hashes: dict[str, str] = {}
    for scene in sorted(project.scenes, key=_project_scene_id):
        digest = hashlib.sha256()
        for logical_name, relative_path in (
            ("plan.json", scene.plan_path),
            ("brief.json", scene.brief_path),
            ("expectations.json", scene.expectations_path),
        ):
            target = _safe_project_path(project_root, relative_path, label="scene package")
            digest.update(logical_name.encode("utf-8"))
            digest.update(b"\0")
            with target.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            digest.update(b"\0")
        package_hashes[scene.id] = digest.hexdigest()
    return package_hashes


def _synchronized_plan(plan: ScenePlan, segment: TimelineSegment) -> ScenePlan:
    document = plan.to_document()
    document.update(
        {
            "narration_text": segment.narration_text,
            "objective": segment.objective,
            "start_seconds": segment.start_seconds,
            "end_seconds": segment.end_seconds,
            "duration_seconds": segment.target_duration_seconds,
        }
    )
    if segment.expectations:
        expectations = SceneExpectations.model_validate(segment.expectations)
        serialized: object = json.loads(expectations.model_dump_json())
        expectations_document = _object(serialized)
        if expectations_document is None:
            raise ValueError("scene expectations document must be a JSON object")
        document["expectations"] = expectations_document
    else:
        document["expectations"] = None
    return ScenePlan.model_validate_json(json.dumps(document))


def _validate_plan_temporal_fields(plan: ScenePlan, segment: TimelineSegment) -> None:
    if (
        plan.narration_text != segment.narration_text
        or plan.objective != segment.objective
        or plan.start_seconds != segment.start_seconds
        or plan.end_seconds != segment.end_seconds
        or plan.duration_seconds != segment.target_duration_seconds
    ):
        raise ValueError("scene plan temporal fields do not agree with timeline")


def _brief_path_for_segment(segment: TimelineSegment) -> str:
    package_path = segment.plan_path.removesuffix("/plan.json")
    return f"{package_path}/brief.json"


def _expectations_path_for_segment(segment: TimelineSegment) -> str:
    package_path = segment.plan_path.removesuffix("/plan.json")
    return f"{package_path}/expectations.json"


def _brief_for_segment(segment: TimelineSegment) -> SceneBrief:
    return SceneBrief(
        id=segment.id,
        order=segment.order,
        narration_text=segment.narration_text,
        objective=segment.objective,
        start_seconds=segment.start_seconds,
        end_seconds=segment.end_seconds,
        duration_seconds=segment.target_duration_seconds,
        start_provenance=segment.start_provenance,
        end_provenance=segment.end_provenance,
        plan_path=segment.plan_path,
    )


def _expectations_document(plan: ScenePlan) -> dict[str, object]:
    if plan.expectations is None:
        return {}
    serialized: object = json.loads(plan.expectations.model_dump_json())
    document = _object(serialized)
    if document is None:
        raise ValueError("scene expectations document must be a JSON object")
    return document


def _copy_atomic(source: Path, destination: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    temporary: Path | None = None
    destination.parent.mkdir(parents=True, exist_ok=True)
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
                digest.update(chunk)
                size += len(chunk)
            target_handle.flush()
            os.fsync(target_handle.fileno())
        temporary.rename(destination)
    except BaseException:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise
    return digest.hexdigest(), size


def _atomic_write_json(path: Path, document: Mapping[str, object]) -> None:
    temporary = _write_json_temp(path, document)
    try:
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _atomic_update_json_documents(
    documents: tuple[tuple[Path, Mapping[str, object]], ...],
) -> None:
    """Serialize JSON documents and publish them through one payload transaction."""

    payloads = tuple(
        (path, _serialize_json_payload(document)) for path, document in documents
    )
    _atomic_update_payloads(payloads)


def _atomic_update_payloads(
    payloads: tuple[tuple[Path, bytes], ...],
    *,
    validate: Callable[[], None] | None = None,
) -> None:
    """Publish unique byte payloads with rollback on any replacement failure."""

    destinations = tuple(path.resolve() for path, _ in payloads)
    if len(destinations) != len(set(destinations)):
        raise ValueError("payload transaction destinations must be unique")
    normalized_payloads = tuple(
        (destination, payload)
        for destination, (_, payload) in zip(destinations, payloads, strict=True)
    )
    originals: dict[Path, bytes | None] = {
        path: path.read_bytes() if path.exists() else None
        for path, _ in normalized_payloads
    }
    new_temporaries: list[Path] = []
    backup_temporaries: list[Path] = []
    replaced: list[tuple[Path, Path | None]] = []
    try:
        for path, payload in normalized_payloads:
            path.parent.mkdir(parents=True, exist_ok=True)
            new_temporaries.append(_write_temp_bytes(path, payload))
        backups: dict[Path, Path | None] = {}
        for path, _ in normalized_payloads:
            original = originals[path]
            backup = _write_temp_bytes(path, original) if original is not None else None
            backups[path] = backup
            if backup is not None:
                backup_temporaries.append(backup)
        for (path, _), temporary in zip(normalized_payloads, new_temporaries, strict=True):
            temporary.replace(path)
            replaced.append((path, backups[path]))
        if validate is not None:
            validate()
    except BaseException as exc:
        rollback_failures: list[tuple[Path, BaseException]] = []
        for path, backup in reversed(replaced):
            try:
                if backup is None:
                    path.unlink(missing_ok=True)
                else:
                    backup.replace(path)
            except BaseException as rollback_error:
                rollback_failures.append((path, rollback_error))
        if rollback_failures:
            details = "; ".join(
                f"{path}: {error}" for path, error in rollback_failures
            )
            raise RuntimeError(
                "payload transaction failed and rollback could not restore targets: "
                f"{details}"
            ) from exc
        raise
    finally:
        for temporary in (*new_temporaries, *backup_temporaries):
            temporary.unlink(missing_ok=True)


def _serialize_json_payload(document: Mapping[str, object]) -> bytes:
    return (
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _write_json_temp(path: Path, document: Mapping[str, object]) -> Path:
    payload = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
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
    except BaseException:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise
    if temporary is None:
        raise RuntimeError("temporary JSON path was not created")
    return temporary


def _write_temp_bytes(path: Path, payload: bytes) -> Path:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise
    if temporary is None:
        raise RuntimeError("temporary backup path was not created")
    return temporary


def _is_safe_relative_path(value: str) -> bool:
    if not value or value.startswith("/") or "\\" in value:
        return False
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return False
    return PurePosixPath(value).is_absolute() is False


def _file_sha256(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _object(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _first_audio_stream(value: object) -> dict[str, object] | None:
    if not isinstance(value, list):
        return None
    for item in value:
        stream = _object(item)
        if stream is not None and stream.get("codec_type") == "audio":
            return stream
    return None


def _text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        result = value
    elif isinstance(value, str):
        try:
            result = int(value)
        except ValueError:
            return None
    else:
        return None
    return result if result > 0 else None


def _stream_index(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        result = value
    elif isinstance(value, str):
        try:
            result = int(value)
        except ValueError:
            return None
    else:
        return None
    return result if result >= 0 else None


def _positive_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, str):
        try:
            result = float(value)
        except ValueError:
            return None
    else:
        return None
    return result if result > 0 else None


__all__ = [
    "AudioMediaFacts",
    "AudioProbe",
    "Project",
    "ProjectSceneRef",
    "ProjectStageState",
    "ProjectState",
    "PauseInterval",
    "SilenceDetector",
    "Timeline",
    "confirm_project_timeline",
    "inspect_project",
    "initialize_project",
    "load_project",
    "probe_audio",
    "validate_project_timeline",
]
