"""Discovery and model-free validation of accepted golden projects.

Golden validation is deliberately independent from the provider and renderer.
It validates the authored contract, referenced evidence, and content hashes;
it never executes a scene or asks a model to regenerate one.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import platform
import re
import shutil
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from video_pipeline.capabilities import default_capability_registry
from video_pipeline.expectations import SceneExpectations
from video_pipeline.project import (
    Project,
    ProjectSceneRef,
    ProjectState,
    _atomic_update_payloads,
    _project_package_hashes,
    _serialize_json_payload,
    load_project,
    validate_project_timeline,
)
from video_pipeline.scene_plan import ScenePlan
from video_pipeline.temporal import TemporalTolerances
from video_pipeline.theme import VideoTheme
from video_pipeline.timeline import Timeline, load_timeline

_PROJECT_ID = re.compile(r"^[0-9]{4}_[a-z0-9]+(?:[_-][a-z0-9]+)*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GOLDEN_SCHEMA_VERSION = "golden.manifest/1"
_GOLDEN_PROFILES = frozenset({"visual", "audiovisual"})
_SELECTIVE_LINEAGE_KEYS = ("base_run_id", "selected_scene_id", "correction")
_AUDIOVISUAL_GOLDEN_STATUSES = frozenset(
    {
        ProjectState.accepted.value,
        ProjectState.rendering.value,
        ProjectState.ready.value,
        ProjectState.failed.value,
    }
)
_REQUIRED_FACT_LISTS = ("initial_state", "final_state", "checkpoints", "animations")


@dataclass(frozen=True, slots=True)
class GoldenProject:
    """A discovered accepted project directory."""

    path: Path
    project_id: str
    capabilities: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GoldenValidation:
    """Model-free validation result for one golden project."""

    path: Path
    valid: bool
    reasons: list[str]
    inference_calls: int = 0
    code_hash: str | None = None
    plan_hash: str | None = None


@dataclass(frozen=True, slots=True)
class _CodeEvidence:
    """Static facts recoverable from reusable scene source without executing it."""

    registrations: tuple[str, ...]
    checkpoints: tuple[str, ...]
    beat_ids: tuple[str, ...]
    animations: tuple[str, ...]


def discover_golden_projects(root: str | Path = Path("projects")) -> list[Path]:
    """Find accepted projects using the canonical year/slug convention."""

    base = Path(root)
    if not base.is_dir():
        return []
    found: list[Path] = []
    for project_json in sorted(base.glob("*/project.json"), key=lambda item: str(item)):
        project_dir = project_json.parent
        if _PROJECT_ID.fullmatch(project_dir.name) is None:
            continue
        try:
            document = _read_object(project_json)
            manifest = _read_object(project_dir / "golden" / "manifest.json")
        except (OSError, ValueError):
            continue
        profile = _common_manifest_profile(document, manifest)
        if profile is not None and _golden_lifecycle_allowed(document, manifest, profile):
            found.append(project_dir)
    return found


def read_golden_project(path: str | Path) -> GoldenProject:
    """Read one accepted project's identity and declared capabilities."""

    root = Path(path)
    document = _read_object(root / "project.json")
    project_id = document.get("id")
    if not isinstance(project_id, str) or not project_id.strip():
        raise ValueError("golden project ID must be non-blank")
    if _PROJECT_ID.fullmatch(project_id) is None:
        raise ValueError("golden project ID must use YYYY_slug naming")
    if project_id != root.name:
        raise ValueError("golden project ID must match its directory name")
    manifest = _read_object(root / "golden" / "manifest.json")
    if manifest.get("schema_version") != _GOLDEN_SCHEMA_VERSION:
        raise ValueError("golden manifest schema_version is unsupported")
    if manifest.get("version") != 1:
        raise ValueError("golden manifest version must be 1")
    profile = manifest.get("profile")
    if not isinstance(profile, str) or profile not in _GOLDEN_PROFILES:
        raise ValueError("golden manifest profile must be visual or audiovisual")
    if manifest.get("status") != "accepted":
        raise ValueError("golden manifest status must be accepted")
    if manifest.get("project_id") != project_id:
        raise ValueError("golden manifest project_id must match the project")
    title = manifest.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("golden manifest title must be non-blank")
    project_title = document.get("title")
    if not isinstance(project_title, str) or not project_title.strip():
        raise ValueError("project title must be non-blank")
    if title != project_title:
        raise ValueError("golden manifest title must match the project")
    if not _golden_lifecycle_allowed(document, manifest, profile):
        raise ValueError("golden project is not a valid lifecycle snapshot")
    capabilities = manifest.get("capabilities")
    if not isinstance(capabilities, list) or not all(
        isinstance(item, str) and item.strip() for item in capabilities
    ):
        raise ValueError("golden project capabilities must be a string list")
    if len(capabilities) != len(set(capabilities)):
        raise ValueError("golden project capabilities must be unique")
    default_capability_registry().require(capabilities)
    return GoldenProject(root, project_id, tuple(capabilities))


def validate_golden_project(path: str | Path) -> GoldenValidation:
    """Validate all golden evidence without provider, model, or render calls."""

    root = Path(path)
    reasons: list[str] = []
    project_document: dict[str, object] = {}
    manifest: dict[str, object] = {}
    root_theme: VideoTheme | None = None
    try:
        project_document = _read_object(root / "project.json")
    except (OSError, ValueError) as exc:
        reasons.append(f"project.json: {exc}")
    _validate_project_identity(root, project_document, reasons, require_accepted=False)

    try:
        manifest = _read_object(root / "golden" / "manifest.json")
    except (OSError, ValueError) as exc:
        reasons.append(f"golden/manifest.json: {exc}")

    profile = manifest.get("profile")
    if not isinstance(profile, str) or profile not in _GOLDEN_PROFILES:
        reasons.append("golden manifest profile must be visual or audiovisual")
        return GoldenValidation(
            path=root,
            valid=False,
            reasons=reasons,
            inference_calls=0,
        )
    _validate_project_lifecycle(project_document, manifest, profile, reasons)
    manifest_capabilities = _validate_common_manifest(
        manifest,
        project_document,
        reasons,
    )

    if profile == "audiovisual":
        return _validate_audiovisual_project(
            root,
            project_document,
            manifest,
            reasons,
        )

    declared_capabilities = _string_list(
        project_document.get("capabilities"), "project capabilities", reasons
    )
    if not declared_capabilities:
        reasons.append("project capabilities must contain at least one proven capability")
    _validate_capabilities(declared_capabilities, reasons)

    theme_document: dict[str, object] = {}
    try:
        theme_document = _read_object(root / "theme.json")
        root_theme = VideoTheme.model_validate(theme_document)
    except (OSError, ValueError, ValidationError) as exc:
        reasons.append(f"theme.json: {exc}")

    if manifest_capabilities is None:
        manifest_capabilities = []
    if declared_capabilities != manifest_capabilities:
        reasons.append("golden manifest capabilities disagree with project")

    _validate_manifest_theme(root, manifest, theme_document, root_theme, reasons)
    _validate_manifest_metadata(manifest, reasons)

    scenes = manifest.get("scenes")
    scene_documents: list[dict[str, object]] = []
    if not isinstance(scenes, list):
        reasons.append("golden manifest scenes must be a list")
    elif len(scenes) < 2:
        reasons.append("golden manifest must contain at least two real scenes")
    else:
        scene_documents = _validate_scenes(
            root,
            scenes,
            declared_capabilities,
            root_theme,
            reasons,
        )
    scene_ids = (
        {item.get("id") for item in scenes if isinstance(item, dict)}
        if isinstance(scenes, list)
        else set()
    )
    _validate_manifest_continuity(manifest, scene_ids, reasons)

    code_hash = manifest.get("code_hash")
    plan_hash = manifest.get("plan_hash")
    if scene_documents:
        code_paths = [Path(item["code"]) for item in scene_documents]
        plan_paths = [Path(item["plan"]) for item in scene_documents]
        actual_code_hash = hash_references(root, code_paths)
        actual_plan_hash = hash_references(root, plan_paths)
        if code_hash != actual_code_hash:
            reasons.append(
                "golden manifest code_hash does not match referenced scene code: "
                f"expected {actual_code_hash}"
            )
        if plan_hash != actual_plan_hash:
            reasons.append(
                "golden manifest plan_hash does not match referenced scene plans: "
                f"expected {actual_plan_hash}"
            )

    frames_dir = root / "golden" / "frames"
    evidence_dir = root / "golden" / "evidence"
    if not frames_dir.is_dir():
        reasons.append("golden/frames directory is required")
    if not evidence_dir.is_dir():
        reasons.append("golden/evidence directory is required")

    return GoldenValidation(
        path=root,
        valid=not reasons,
        reasons=reasons,
        inference_calls=0,
        code_hash=code_hash if isinstance(code_hash, str) else None,
        plan_hash=plan_hash if isinstance(plan_hash, str) else None,
    )


def validate_all_golden_projects(root: str | Path = Path("projects")) -> list[GoldenValidation]:
    """Run every accepted-project regression without invoking Qwen."""

    return [validate_golden_project(path) for path in discover_golden_projects(root)]


def hash_file(path: str | Path) -> str:
    """Hash one artifact in bounded chunks."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_references(root: str | Path, references: Sequence[str | Path]) -> str:
    """Hash ordered referenced files, including names to prevent substitution."""

    base = Path(root).resolve()
    digest = hashlib.sha256()
    for reference in references:
        relative = Path(reference)
        target = _safe_reference(base, relative)
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        with target.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def accept_project(path: str | Path, run_id: str) -> Project:
    """Promote one ready audiovisual run and persist its golden manifest."""

    project_json = Path(path).resolve()
    project = load_project(project_json)
    if project.status != ProjectState.ready:
        raise ValueError("only a ready project can be accepted")
    if project.current_scene is not None:
        raise ValueError("ready project current_scene must be null")
    if project.current_run != run_id:
        raise ValueError("run is not the project's current run")
    if not _safe_run_id(run_id):
        raise ValueError("run ID must be a safe relative name")

    project_root = project_json.parent
    project, timeline = validate_project_timeline(project_json)
    if timeline.status != "confirmed":
        raise ValueError("accepted project must have a confirmed timeline")
    plans = _validate_acceptance_timeline(project, timeline, project_root)
    script_path = _required_reference(project_root, project.script_path, label="script")
    audio_path = _required_reference(project_root, project.audio_path, label="audio")
    if project.timeline_path is None:
        raise ValueError("accepted project must reference a timeline")
    timeline_path = _required_reference(project_root, project.timeline_path, label="timeline")
    script_hash = hash_file(script_path)
    audio_hash = hash_file(audio_path)
    if script_hash != project.script_sha256:
        raise ValueError("script hash does not match project.json")
    if audio_hash != project.audio.hash:
        raise ValueError("audio hash does not match project.json")
    if audio_path.stat().st_size != project.audio.size:
        raise ValueError("audio size does not match project.json")
    input_hashes = {
        "script_sha256": script_hash,
        "audio_sha256": audio_hash,
        "timeline_sha256": hash_file(timeline_path),
    }
    package_hashes = _project_package_hashes(project_root, project)

    run_relative = f"artifacts/{run_id}/run.json"
    run_path = _required_reference(project_root, run_relative, label="run")
    run_document = _read_object(run_path)
    _validate_ready_run(
        run_document,
        project,
        timeline,
        project_root,
        run_id,
    )
    selective_lineage = _selective_lineage_from_document(
        run_document,
        project,
        run_id,
        label="ready run",
    )
    if run_document.get("input_hashes") != input_hashes:
        raise ValueError("ready run input hashes do not match current project inputs")
    if run_document.get("package_hashes") != package_hashes:
        raise ValueError("ready run package hashes do not match current scene packages")
    composition_path = _required_reference(
        project_root,
        f"artifacts/{run_id}/composition.json",
        label="composition",
    )
    composition = _read_object(composition_path)
    run_composition = _mapping_value(run_document.get("composition"), "run composition")
    if run_composition != composition:
        raise ValueError("ready run composition does not match composition.json")
    final_validation = _mapping_value(run_document.get("final_validation"), "final validation")
    if final_validation.get("valid") is not True:
        raise ValueError("ready run must contain valid final audiovisual validation")
    composition_validation = composition.get("validation")
    if (
        not isinstance(composition_validation, dict)
        or composition_validation.get("valid") is not True
    ):
        raise ValueError("composition validation is not valid")
    if final_validation != composition_validation:
        raise ValueError("ready run final_validation does not match composition.validation")
    final_relative = f"artifacts/{run_id}/final.mp4"
    composition_errors = _composition_fact_errors(
        run_composition,
        project_root=project_root,
        final_relative=final_relative,
        expected_validation=final_validation,
    )
    composition_errors.extend(
        _composition_fact_errors(
            composition,
            project_root=project_root,
            final_relative=final_relative,
            expected_validation=final_validation,
        )
    )
    if composition_errors:
        raise ValueError("composition facts are invalid: " + "; ".join(composition_errors))
    final_path = _required_reference(project_root, final_relative, label="final artifact")
    final_attestation_errors = _final_artifact_attestation_errors(run_document, final_path)
    if final_attestation_errors:
        raise ValueError(
            "ready run final artifact attestation is invalid: "
            + "; ".join(final_attestation_errors)
        )
    output_path = run_document.get("output_path")
    if output_path is None:
        raise ValueError("ready run must reference its final artifact")
    output_relative = _project_relative_value(project_root, output_path, label="run output")
    if output_relative != final_relative:
        raise ValueError("run output does not reference its final artifact")
    composition_output = composition.get("output_path")
    if composition_output is None:
        raise ValueError("composition must reference its final artifact")
    composition_relative = _project_relative_value(
        project_root,
        composition_output,
        label="composition output",
    )
    if composition_relative != final_relative:
        raise ValueError("composition output does not reference its final artifact")
    final_media_contract = _expected_final_media_contract(project, timeline.duration_seconds)
    media_fact_errors = _final_media_fact_errors(
        final_validation,
        project_root=project_root,
        final_media_contract=final_media_contract,
        expected_duration_seconds=timeline.duration_seconds,
        duration_tolerance_seconds=0.05,
        final_path=final_path,
    )
    if media_fact_errors:
        raise ValueError(
            "final audiovisual validation facts are invalid: "
            + "; ".join(media_fact_errors)
        )
    scene_documents, scene_payloads = _acceptance_scene_documents(
        project_root,
        project,
        timeline,
        plans,
        run_document,
        run_id,
    )
    composition_manifest = _relative_document_paths(
        project_root,
        composition,
        ("output_path", "log_path"),
    )
    composition_validation = composition_manifest.get("validation")
    if isinstance(composition_validation, dict):
        composition_manifest["validation"] = _relative_document_paths(
            project_root,
            composition_validation,
            ("path",),
        )
    final_validation_manifest = _relative_document_paths(
        project_root,
        final_validation,
        ("path",),
    )

    accepted_package_root = f"golden/accepted/{run_id}"
    timeline_snapshot_relative = f"{accepted_package_root}/timeline.json"
    snapshot_payloads: list[tuple[Path, bytes]] = [
        (
            project_root / timeline_snapshot_relative,
            timeline_path.read_bytes(),
        )
    ]
    for scene_document, project_scene in zip(
        scene_documents,
        project.scenes,
        strict=True,
    ):
        snapshot_scene_root = f"{accepted_package_root}/{project_scene.path}"
        for key, source_relative in (
            ("plan_path", project_scene.plan_path),
            ("brief_path", project_scene.brief_path),
            ("expectations_path", project_scene.expectations_path),
        ):
            source_path = _required_reference(
                project_root,
                source_relative,
                label=f"scene {key.removesuffix('_path')}",
            )
            snapshot_relative = f"{snapshot_scene_root}/{Path(source_relative).name}"
            scene_document[key] = snapshot_relative
            snapshot_payloads.append(
                (project_root / snapshot_relative, source_path.read_bytes())
            )

    capabilities: list[str] = []
    for plan in plans:
        for capability in plan.capabilities:
            if capability not in capabilities:
                capabilities.append(capability)
    tolerances = {
        "timeline_seconds": timeline.tolerance_seconds,
        "temporal_normalization": {
            "acceptance_seconds": TemporalTolerances().acceptance_seconds,
            "correction_limit_seconds": TemporalTolerances().correction_limit_seconds,
        },
        "final_duration_seconds": 0.05,
    }
    manifest: dict[str, object] = {
        "schema_version": _GOLDEN_SCHEMA_VERSION,
        "profile": "audiovisual",
        "version": 1,
        "status": "accepted",
        "project_id": project.id,
        "title": project.title,
        "run_id": run_id,
        "inputs": {
            "script": {
                "path": project.script_path,
                "sha256": script_hash,
                "size_bytes": script_path.stat().st_size,
            },
            "audio": {
                "path": project.audio_path,
                "sha256": audio_hash,
                "size_bytes": audio_path.stat().st_size,
                "facts": json.loads(project.audio.model_dump_json()),
            },
        },
        "timeline": {
            "path": timeline_snapshot_relative,
            "schema_version": timeline.schema_version,
            "sha256": hash_file(timeline_path),
            "status": timeline.status,
            "method": timeline.method,
            "duration_seconds": timeline.duration_seconds,
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
        },
        "theme": project.theme.model_dump(mode="json"),
        "capabilities": capabilities,
        "scenes": scene_documents,
        "composition": composition_manifest,
        "final_validation": final_validation_manifest,
        "final_media_contract": final_media_contract,
        "artifacts": {
            "final": {
                "path": final_relative,
                "sha256": run_document["final_sha256"],
                "size_bytes": run_document["final_size_bytes"],
            },
        },
        "tolerances": tolerances,
        "runtime_versions": {
            "python": sys.version.split()[0],
            "python_implementation": platform.python_implementation(),
            "platform": platform.platform(),
        },
        "provenance": {
            "project": "project.json",
            "run": run_relative,
            "timeline": timeline_snapshot_relative,
            "manifest": "golden/manifest.json",
            "source_run": run_id,
        },
        "reproducibility": {
            "script_sha256": script_hash,
            "audio_sha256": audio_hash,
            "run_id": run_id,
            "final_artifact": final_relative,
        },
    }
    if selective_lineage is not None:
        manifest.update(selective_lineage)
    project_document = json.loads(project.model_dump_json())
    if not isinstance(project_document, dict):
        raise ValueError("project document must be a JSON object")
    project_document["status"] = ProjectState.accepted.value
    project_document["accepted_run"] = run_id
    project_document["current_scene"] = None
    accepted_project = Project.model_validate_json(json.dumps(project_document))
    golden_root = project_root / "golden"
    golden_root_existed = golden_root.exists()
    manifest_path = golden_root / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    (manifest_path.parent / "frames").mkdir(parents=True, exist_ok=True)
    (manifest_path.parent / "evidence").mkdir(parents=True, exist_ok=True)
    def validate_published_golden() -> None:
        validation = validate_golden_project(project_root)
        if not validation.valid:
            raise ValueError(
                "golden validation failed: " + "; ".join(validation.reasons)
            )

    try:
        _atomic_update_payloads(
            (
                *snapshot_payloads,
                *scene_payloads,
                (project_json, _serialize_json_payload(project_document)),
                (manifest_path, _serialize_json_payload(manifest)),
            ),
            validate=validate_published_golden,
        )
    except BaseException:
        if not golden_root_existed:
            shutil.rmtree(golden_root, ignore_errors=True)
        raise
    return accepted_project


def _load_acceptance_timeline(project_root: Path, project: Project) -> Timeline:
    if project.timeline_path is None:
        raise ValueError("accepted project must reference a timeline")
    timeline_path = _required_reference(project_root, project.timeline_path, label="timeline")
    timeline = load_timeline(timeline_path)
    if timeline.status != "confirmed":
        raise ValueError("accepted project must have a confirmed timeline")
    if abs(timeline.duration_seconds - project.audio.duration) > timeline.tolerance_seconds:
        raise ValueError("timeline duration does not match project audio duration")
    return timeline


def _validate_acceptance_timeline(
    project: Project,
    timeline: Timeline,
    project_root: Path,
) -> tuple[ScenePlan, ...]:
    if len(project.scenes) != len(timeline.segments):
        raise ValueError("project and timeline scene counts do not agree")
    plans: list[ScenePlan] = []
    for project_scene, segment in zip(project.scenes, timeline.segments, strict=True):
        if (
            project_scene.id != segment.id
            or project_scene.order != segment.order
            or project_scene.plan_path != segment.plan_path
            or project_scene.path != segment.plan_path.removesuffix("/plan.json")
        ):
            raise ValueError("project and timeline scene references do not agree")
        plan_path = _required_reference(project_root, project_scene.plan_path, label="plan")
        plan = ScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
        if plan.id != segment.id:
            raise ValueError("scene plan identity does not agree with timeline")
        if (
            plan.narration_text != segment.narration_text
            or plan.objective != segment.objective
            or plan.start_seconds != segment.start_seconds
            or plan.end_seconds != segment.end_seconds
            or abs(plan.duration_seconds - segment.target_duration_seconds) > 1e-6
        ):
            raise ValueError("scene plan timing or narration does not agree with timeline")
        plans.append(plan)
    return tuple(plans)


def _validate_ready_run(
    document: Mapping[str, object],
    project: Project,
    timeline: Timeline,
    project_root: Path,
    run_id: str,
) -> None:
    if document.get("run_id") != run_id:
        raise ValueError("run document ID does not match requested run")
    if document.get("project_id") != project.id:
        raise ValueError("run document project ID does not match project")
    if document.get("state") != "ready":
        raise ValueError("run must be ready before acceptance")
    if "current_scene" not in document:
        raise ValueError("ready run current_scene is required")
    if document.get("current_scene") is not None:
        raise ValueError("ready run current_scene must be null")
    if project.timeline_path is None:
        raise ValueError("accepted project must reference a timeline")
    timeline_reference = document.get("timeline_path")
    if not isinstance(timeline_reference, str):
        raise ValueError("ready run must reference its timeline")
    if _project_relative_value(project_root, timeline_reference, label="run timeline") != (
        project.timeline_path
    ):
        raise ValueError("run timeline does not reference the project timeline")
    run_reference = document.get("run_path")
    if _project_relative_value(project_root, run_reference, label="run path") != (
        f"artifacts/{run_id}"
    ):
        raise ValueError("run path does not reference the requested run")
    scenes = document.get("scenes")
    if not isinstance(scenes, list):
        raise ValueError("ready run must contain scene evidence")
    if len(scenes) != len(project.scenes) or len(scenes) != len(timeline.segments):
        raise ValueError("ready run scene count does not match the timeline")
    expected = tuple((scene.id, scene.order) for scene in project.scenes)
    observed: list[tuple[object, object]] = []
    for raw_scene in scenes:
        if not isinstance(raw_scene, dict):
            raise ValueError("ready run scene evidence must be objects")
        observed.append((raw_scene.get("id"), raw_scene.get("order")))
    if tuple(observed) != expected:
        raise ValueError("ready run scene IDs or order do not match the timeline")


def _acceptance_scene_documents(
    project_root: Path,
    project: Project,
    timeline: Timeline,
    plans: tuple[ScenePlan, ...],
    run_document: Mapping[str, object],
    run_id: str,
) -> tuple[list[dict[str, object]], list[tuple[Path, bytes]]]:
    records = run_document.get("scenes")
    if not isinstance(records, list):
        raise ValueError("ready run scenes must be a list")
    record_by_id: dict[str, Mapping[str, object]] = {}
    for raw_record in records:
        if not isinstance(raw_record, dict) or not isinstance(raw_record.get("id"), str):
            raise ValueError("run scene records require IDs")
        record_id = raw_record["id"]
        if record_id in record_by_id:
            raise ValueError("run scene IDs must be unique")
        record_by_id[record_id] = raw_record

    documents: list[dict[str, object]] = []
    scene_payloads: list[tuple[Path, bytes]] = []
    for project_scene, segment, plan in zip(
        project.scenes,
        timeline.segments,
        plans,
        strict=True,
    ):
        record = record_by_id.get(segment.id)
        if record is None:
            raise ValueError(f"run is missing scene evidence: {segment.id}")
        if record.get("state") != "ready":
            raise ValueError(f"scene run is not ready: {segment.id}")
        if record.get("order") != segment.order:
            raise ValueError(f"scene run order does not match timeline: {segment.id}")
        attempts = record.get("attempts")
        if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts <= 0:
            raise ValueError(f"scene run has no successful attempts: {segment.id}")

        canonical_paths = _canonical_run_scene_paths(
            project_root,
            run_id,
            project_scene.path,
            segment.id,
            project_scene.plan_path,
            record,
        )
        raw_relative = canonical_paths["raw_path"]
        normalized_relative = canonical_paths["normalized_path"]
        normalization_relative = canonical_paths["normalization_path"]
        raw_path = _required_reference(project_root, raw_relative, label="raw scene")
        normalized_path = _required_reference(
            project_root,
            normalized_relative,
            label="normalized scene",
        )
        normalization_path = _required_reference(
            project_root,
            normalization_relative,
            label="normalization evidence",
        )
        candidate_code_relative = canonical_paths["code_path"]
        candidate_provenance_relative = canonical_paths["provenance_path"]
        candidate_code_path = _required_reference(
            project_root,
            candidate_code_relative,
            label="run scene code",
        )
        candidate_provenance_path = _required_reference(
            project_root,
            candidate_provenance_relative,
            label="run code provenance",
        )
        code_relative = f"{project_scene.path}/scene.py"
        provenance_relative = f"{project_scene.path}/code-provenance.json"
        code_path = _safe_reference(project_root.resolve(), Path(code_relative))
        provenance_path = _safe_reference(
            project_root.resolve(),
            Path(provenance_relative),
        )
        history = record.get("attempt_history")
        if not isinstance(history, list):
            raise ValueError(f"scene {segment.id} attempt history is required")
        for entry in history:
            if not isinstance(entry, dict):
                raise ValueError(f"scene {segment.id} attempt history must contain objects")
            for key in ("run_path", "attempt_path", "mp4_path"):
                value = entry.get(key)
                if value is None:
                    continue
                relative = _project_relative_value(
                    project_root,
                    value,
                    label=f"scene {segment.id} attempt {key}",
                )
                if not relative.startswith(f"artifacts/{run_id}/"):
                    raise ValueError(
                        f"scene {segment.id} attempt {key} is outside the run"
                    )
        normalization = _read_object(normalization_path)
        if normalization.get("status") not in {"accepted", "normalized"}:
            raise ValueError(f"scene normalization is not accepted: {segment.id}")
        diagnostics = _mapping_value(record.get("diagnostics"), "scene diagnostics")
        diagnostics_relative = canonical_paths["diagnostics_path"]
        observation_relative = canonical_paths["observation_path"]
        quality_relative = canonical_paths["quality_path"]
        diagnostics_path = _required_reference(
            project_root,
            diagnostics_relative,
            label="scene diagnostics evidence",
        )
        observation_path = _required_reference(
            project_root,
            observation_relative,
            label="scene semantic evidence",
        )
        quality_path = _required_reference(
            project_root,
            quality_relative,
            label="scene quality evidence",
        )
        code_payload = candidate_code_path.read_bytes()
        provenance_payload = candidate_provenance_path.read_bytes()
        provenance = _read_object(candidate_provenance_path)
        provenance_facts = _relative_provenance(project_root, provenance)
        for key, path in (
            ("code_sha256", candidate_code_path),
            ("raw_sha256", raw_path),
            ("normalized_sha256", normalized_path),
            ("normalization_sha256", normalization_path),
            ("provenance_sha256", candidate_provenance_path),
            ("diagnostics_sha256", diagnostics_path),
            ("observation_sha256", observation_path),
            ("quality_sha256", quality_path),
        ):
            digest = record.get(key)
            if not isinstance(digest, str) or digest != hash_file(path):
                raise ValueError(
                    f"scene {segment.id} stored {key} does not match run evidence"
                )
        raw_facts = diagnostics.get("validation")
        if not isinstance(raw_facts, dict):
            raw_facts = {}
        raw_facts = _relative_document_paths(project_root, raw_facts, ("path",))
        normalized_facts = {
            "status": normalization.get("status"),
            "observed_duration_seconds": normalization.get("observed_duration_seconds"),
            "target_duration_seconds": normalization.get("target_duration_seconds"),
            "delta_seconds": normalization.get("delta_seconds"),
            "validated_duration_seconds": normalization.get("validated_duration_seconds"),
            "validation_reasons": normalization.get("validation_reasons", []),
        }
        documents.append(
            {
                "id": segment.id,
                "order": segment.order,
                "plan_path": project_scene.plan_path,
                "plan_sha256": hash_file(project_root / project_scene.plan_path),
                "brief_path": project_scene.brief_path,
                "brief_sha256": hash_file(
                    _required_reference(
                        project_root,
                        project_scene.brief_path,
                        label="scene brief",
                    )
                ),
                "expectations_path": project_scene.expectations_path,
                "expectations_sha256": hash_file(
                    _required_reference(
                        project_root,
                        project_scene.expectations_path,
                        label="scene expectations",
                    )
                ),
                "code_path": code_relative,
                "code_sha256": hashlib.sha256(code_payload).hexdigest(),
                "provenance_path": provenance_relative,
                "provenance_sha256": hashlib.sha256(provenance_payload).hexdigest(),
                "provenance": provenance_facts,
                "capabilities": list(plan.capabilities),
                "narration_text": segment.narration_text,
                "start_seconds": segment.start_seconds,
                "end_seconds": segment.end_seconds,
                "target_duration_seconds": segment.target_duration_seconds,
                "attempts": attempts,
                "evidence": {
                    "diagnostics": _media_document(
                        project_root,
                        diagnostics_relative,
                        diagnostics_path,
                    ),
                    "semantic": _media_document(
                        project_root,
                        observation_relative,
                        observation_path,
                    ),
                    "quality": _media_document(
                        project_root,
                        quality_relative,
                        quality_path,
                    ),
                    "normalization": _media_document(
                        project_root,
                        normalization_relative,
                        normalization_path,
                    ),
                    "raw": _media_document(project_root, raw_relative, raw_path),
                    "normalized": _media_document(
                        project_root,
                        normalized_relative,
                        normalized_path,
                    ),
                },
                "raw_media": {
                    **raw_facts,
                    "facts": raw_facts,
                    "path": raw_relative,
                    "sha256": hash_file(raw_path),
                    "size_bytes": raw_path.stat().st_size,
                    "media_contract": _scene_media_contract(plan),
                },
                "normalized_media": {
                    **normalized_facts,
                    "facts": normalized_facts,
                    "path": normalized_relative,
                    "sha256": hash_file(normalized_path),
                    "size_bytes": normalized_path.stat().st_size,
                    "media_contract": _scene_media_contract(plan),
                },
                "semantic": _media_document(
                    project_root,
                    observation_relative,
                    observation_path,
                ),
                "quality": _media_document(
                    project_root,
                    quality_relative,
                    quality_path,
                ),
            }
        )
        scene_payloads.extend(
            (
                (code_path, code_payload),
                (provenance_path, provenance_payload),
            )
        )
    if len(record_by_id) != len(project.scenes):
        raise ValueError("run contains scene evidence outside the project timeline")
    return documents, scene_payloads


def _canonical_run_scene_paths(
    project_root: Path,
    run_id: str,
    scene_path: str,
    scene_id: str,
    plan_path: str,
    record: Mapping[str, object],
) -> dict[str, str]:
    """Return and verify the canonical run and attempt evidence paths."""

    scene_root = f"artifacts/{run_id}/{scene_path}"
    pipeline_root = f"artifacts/{run_id}/pipeline/{scene_id}"
    pipeline_relative = _project_relative_value(
        project_root,
        record.get("run_path"),
        label=f"scene {scene_id} pipeline run",
    )
    pipeline_path = Path(pipeline_relative)
    if pipeline_path.parent.as_posix() != pipeline_root or not pipeline_path.name:
        raise ValueError(f"scene {scene_id} pipeline path is not canonical")
    pipeline_attempts = record.get("pipeline_attempts")
    if (
        isinstance(pipeline_attempts, bool)
        or not isinstance(pipeline_attempts, int)
        or pipeline_attempts <= 0
    ):
        raise ValueError(f"scene {scene_id} has invalid pipeline attempt count")
    attempt_relative = f"{pipeline_relative}/attempt-{pipeline_attempts:02d}"
    expected = {
        "run_path": pipeline_relative,
        "latest_attempt_path": attempt_relative,
        "plan_path": plan_path,
        "raw_path": f"{scene_root}/raw.mp4",
        "normalized_path": f"{scene_root}/normalized.mp4",
        "normalization_path": f"{scene_root}/normalization.json",
        "code_path": f"{scene_root}/scene.py",
        "provenance_path": f"{scene_root}/code-provenance.json",
        "diagnostics_path": f"{attempt_relative}/diagnostics.json",
        "observation_path": f"{attempt_relative}/observation.json",
        "quality_path": f"{attempt_relative}/quality-report.json",
    }
    for key, expected_relative in expected.items():
        actual = _project_relative_value(
            project_root,
            record.get(key),
            label=f"scene {scene_id} {key}",
        )
        if actual != expected_relative:
            raise ValueError(f"scene {scene_id} {key} is not canonical")
    return expected


def _media_document(project_root: Path, relative_path: str, path: Path) -> dict[str, object]:
    del project_root
    return {
        "path": relative_path,
        "sha256": hash_file(path),
        "size_bytes": path.stat().st_size,
    }


def _scene_media_contract(plan: ScenePlan) -> dict[str, object]:
    return {
        "resolution": {
            "width": plan.theme.resolution[0],
            "height": plan.theme.resolution[1],
        },
        "fps": plan.theme.fps,
        "timebase": "1/90000",
        "pixel_format": "yuv420p",
    }


def _relative_provenance(
    project_root: Path,
    document: Mapping[str, object],
) -> dict[str, object]:
    relative = dict(document)
    for key in ("run_path", "source_path"):
        value = relative.get(key)
        if value is not None:
            relative[key] = _project_relative_value(
                project_root,
                value,
                label=f"provenance {key}",
            )
    return relative


def _relative_document_paths(
    project_root: Path,
    document: Mapping[str, object],
    keys: Sequence[str],
) -> dict[str, object]:
    relative = dict(document)
    for key in keys:
        value = relative.get(key)
        if value is not None:
            relative[key] = _project_relative_value(
                project_root,
                value,
                label=f"manifest {key}",
            )
    return relative


def _required_reference(project_root: Path, relative_path: str, *, label: str) -> Path:
    if not _safe_relative_text(relative_path):
        raise ValueError(f"project {label} reference must be safe and relative")
    target = _safe_reference(project_root.resolve(), Path(relative_path))
    if not target.is_file():
        raise ValueError(f"project {label} does not exist: {relative_path}")
    return target


def _project_relative_value(project_root: Path, value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a project-relative path")
    candidate = Path(value)
    if candidate.is_absolute():
        try:
            relative = candidate.resolve().relative_to(project_root.resolve())
        except ValueError as exc:
            raise ValueError(f"{label} escapes the project") from exc
        relative_text = relative.as_posix()
    else:
        relative_text = value
    if not _safe_relative_text(relative_text):
        raise ValueError(f"{label} must be a project-relative path")
    return relative_text


def _safe_relative_text(value: str) -> bool:
    if not value or value.startswith("/") or "\\" in value:
        return False
    parts = value.split("/")
    return all(part not in {"", ".", ".."} for part in parts)


def _safe_run_id(value: str) -> bool:
    return _safe_relative_text(value) and Path(value).name == value


def _selective_lineage_from_document(
    document: Mapping[str, object],
    project: Project,
    run_id: str,
    *,
    label: str,
) -> dict[str, str] | None:
    """Validate and return the optional selective-render lineage fields."""

    present = tuple(key for key in _SELECTIVE_LINEAGE_KEYS if key in document)
    if not present:
        return None
    if len(present) != len(_SELECTIVE_LINEAGE_KEYS):
        raise ValueError(
            f"{label} selective lineage must include all of "
            "base_run_id, selected_scene_id, and correction"
        )
    base_run_id = document.get("base_run_id")
    selected_scene_id = document.get("selected_scene_id")
    correction = document.get("correction")
    if not isinstance(base_run_id, str) or not _safe_run_id(base_run_id):
        raise ValueError(f"{label} base_run_id must be a safe name")
    if not isinstance(selected_scene_id, str) or not _safe_run_id(selected_scene_id):
        raise ValueError(f"{label} selected_scene_id must be a safe name")
    if selected_scene_id not in {scene.id for scene in project.scenes}:
        raise ValueError(f"{label} selected_scene_id is not a project scene")
    if base_run_id == run_id:
        raise ValueError(f"{label} base_run_id must differ from the accepted run")
    if not isinstance(correction, str) or not correction.strip():
        raise ValueError(f"{label} correction must be non-empty")
    return {
        "base_run_id": base_run_id,
        "selected_scene_id": selected_scene_id,
        "correction": correction,
    }


def _expected_provenance_run_id(
    accepted_run_id: str,
    scene_id: str,
    selective_lineage: Mapping[str, str] | None,
    base_provenance: Mapping[str, object] | None = None,
) -> str:
    """Return the run whose provenance a golden scene is expected to carry."""

    if selective_lineage is not None and scene_id != selective_lineage["selected_scene_id"]:
        origin_run_id = base_provenance.get("run_id") if base_provenance else None
        if isinstance(origin_run_id, str) and _safe_run_id(origin_run_id):
            return origin_run_id
        return selective_lineage["base_run_id"]
    return accepted_run_id


def _mapping_value(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _composition_fact_errors(
    composition: Mapping[str, object],
    *,
    project_root: Path,
    final_relative: str,
    expected_validation: Mapping[str, object] | None = None,
) -> list[str]:
    """Validate persisted composition structure shared by accept and deep checks."""

    errors: list[str] = []
    argv = composition.get("argv")
    if not isinstance(argv, list) or not argv or not all(
        isinstance(item, str) and item.strip() for item in argv
    ):
        errors.append("composition argv must be a non-empty string list")

    exit_code = composition.get("exit_code")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int) or exit_code != 0:
        errors.append("composition exit_code must be zero")
    if composition.get("error") is not None:
        errors.append("composition error must be null")
    if "elapsed_seconds" in composition:
        elapsed = _finite_number(composition.get("elapsed_seconds"))
        if elapsed is None or elapsed < 0:
            errors.append("composition elapsed_seconds must be finite and non-negative")

    output_path = composition.get("output_path")
    try:
        output_relative = _project_relative_value(
            project_root,
            output_path,
            label="composition output",
        )
    except ValueError as exc:
        errors.append(str(exc))
    else:
        if output_relative != final_relative:
            errors.append(f"composition output must reference {final_relative}")

    validation = composition.get("validation")
    if not isinstance(validation, dict):
        errors.append("composition validation must be an object")
    elif expected_validation is not None and validation != expected_validation:
        errors.append("composition validation must exactly match final_validation")
    return errors


def _final_artifact_attestation_errors(
    run_document: Mapping[str, object],
    final_path: Path,
) -> list[str]:
    """Recompute the ready run's final bytes against its persisted attestation."""

    errors: list[str] = []
    digest = run_document.get("final_sha256")
    if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
        errors.append("final_sha256 must be a lowercase SHA-256")
    size = run_document.get("final_size_bytes")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        errors.append("final_size_bytes must be a positive integer")
    if not final_path.is_file():
        errors.append("final artifact is missing")
        return errors
    try:
        actual_size = final_path.stat().st_size
        actual_digest = hash_file(final_path)
    except OSError as exc:
        errors.append(f"final artifact cannot be attested: {exc}")
        return errors
    if isinstance(size, int) and not isinstance(size, bool) and size != actual_size:
        errors.append("final_size_bytes does not match final.mp4")
    if isinstance(digest, str) and _SHA256.fullmatch(digest) is not None:
        if digest != actual_digest:
            errors.append("final_sha256 does not match final.mp4")
    return errors


def _validate_audiovisual_project(
    root: Path,
    project_document: dict[str, object],
    manifest: dict[str, object],
    initial_reasons: list[str] | None = None,
) -> GoldenValidation:
    """Validate an audiovisual manifest using only persisted JSON and files."""

    reasons = list(initial_reasons or [])
    project_id = project_document.get("id")
    if manifest.get("project_id") != project_id:
        reasons.append("audiovisual manifest project_id disagrees with project")
    if manifest.get("title") != project_document.get("title"):
        reasons.append("audiovisual manifest title disagrees with project")
    run_id = manifest.get("run_id")
    if not isinstance(run_id, str) or not _safe_run_id(run_id):
        reasons.append("audiovisual manifest run_id must be a safe name")

    inputs = _mapping_or_error(manifest.get("inputs"), "inputs", reasons)
    script_input = _mapping_or_error(inputs.get("script"), "script input", reasons)
    audio_input = _mapping_or_error(inputs.get("audio"), "audio input", reasons)
    _validate_hashed_reference(
        root,
        script_input,
        label="script input",
        expected_project_hash=project_document.get("script_sha256"),
        reasons=reasons,
    )
    _validate_hashed_reference(
        root,
        audio_input,
        label="audio input",
        expected_project_hash=_nested_value(project_document.get("audio"), "hash"),
        reasons=reasons,
    )
    audio_facts = _mapping_or_error(audio_input.get("facts"), "audio facts", reasons)
    project_audio = _mapping_or_error(project_document.get("audio"), "project audio", reasons)
    if audio_facts != project_audio:
        reasons.append("audiovisual manifest audio facts disagree with project")
    if audio_input.get("path") != project_document.get("audio_path"):
        reasons.append("audiovisual manifest audio path disagrees with project")

    timeline = _mapping_or_error(manifest.get("timeline"), "timeline", reasons)
    _validate_audiovisual_timeline(root, project_document, timeline, reasons)

    theme = manifest.get("theme")
    if not isinstance(theme, dict) or not theme:
        reasons.append("audiovisual manifest theme must be an object")
    scenes = manifest.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        reasons.append("audiovisual manifest scenes must be a non-empty list")
    else:
        _validate_audiovisual_scenes(root, scenes, timeline, reasons)
    _validate_audiovisual_deep_snapshot(
        root,
        project_document,
        manifest,
        timeline,
        scenes if isinstance(scenes, list) else [],
        run_id if isinstance(run_id, str) else None,
        reasons,
    )

    composition = _mapping_or_error(manifest.get("composition"), "composition", reasons)
    if not composition.get("argv"):
        reasons.append("audiovisual manifest composition argv is required")
    composition_output = composition.get("output_path")
    if isinstance(composition_output, str):
        _validate_file_reference(root, composition_output, "composition output", reasons)
    else:
        reasons.append("audiovisual manifest composition output is required")
    final_validation = _mapping_or_error(
        manifest.get("final_validation"), "final validation", reasons
    )
    if final_validation.get("valid") is not True:
        reasons.append("audiovisual manifest final validation must be valid")
    for key in (
        "video_duration_seconds",
        "audio_duration_seconds",
        "expected_duration_seconds",
        "video_drift_seconds",
        "audio_drift_seconds",
        "audio_video_drift_seconds",
    ):
        if key not in final_validation:
            reasons.append(f"audiovisual final validation lacks {key}")
    final_validation_path = final_validation.get("path")
    if isinstance(final_validation_path, str):
        _validate_file_reference(root, final_validation_path, "final validation", reasons)
    final_contract = _mapping_or_error(
        manifest.get("final_media_contract"),
        "final media contract",
        reasons,
    )
    for key in ("video_codec", "audio_codec", "pixel_format", "resolution", "fps", "timebase"):
        if key not in final_contract:
            reasons.append(f"audiovisual manifest final media contract lacks {key}")
    if final_contract.get("video_codec") != "libx264":
        reasons.append("audiovisual final media contract must use libx264")
    if final_contract.get("audio_codec") != "aac":
        reasons.append("audiovisual final media contract must use AAC")
    if final_contract.get("pixel_format") != "yuv420p":
        reasons.append("audiovisual final media contract must use yuv420p")
    artifacts = _mapping_or_error(manifest.get("artifacts"), "artifacts", reasons)
    final_artifact = _mapping_or_error(artifacts.get("final"), "final artifact", reasons)
    final_path = _validate_artifact_reference(root, final_artifact, "final artifact", reasons)
    if final_path is not None and isinstance(run_id, str):
        final_reference = final_artifact.get("path")
        if not isinstance(final_reference, str) or not final_reference.startswith(
            f"artifacts/{run_id}/"
        ):
            reasons.append("final artifact must reference the accepted run")

    _require_nonempty_mapping(manifest.get("tolerances"), "tolerances", reasons)
    _require_nonempty_mapping(manifest.get("runtime_versions"), "runtime versions", reasons)
    provenance = _mapping_or_error(manifest.get("provenance"), "provenance", reasons)
    run_reference = provenance.get("run")
    if isinstance(run_reference, str):
        _validate_file_reference(root, run_reference, "provenance run", reasons)
    else:
        reasons.append("provenance run reference is required")
    _require_nonempty_mapping(manifest.get("reproducibility"), "reproducibility", reasons)
    return GoldenValidation(
        path=root,
        valid=not reasons,
        reasons=reasons,
        inference_calls=0,
    )


def _validate_audiovisual_deep_snapshot(
    root: Path,
    project_document: Mapping[str, object],
    manifest: Mapping[str, object],
    timeline_document: Mapping[str, object],
    manifest_scenes: list[object],
    run_id: str | None,
    reasons: list[str],
) -> None:
    """Cross-check an audiovisual manifest against its persisted snapshot."""

    try:
        project = Project.model_validate_json(json.dumps(project_document))
    except (ValidationError, ValueError) as exc:
        reasons.append(f"audiovisual project snapshot is invalid: {exc}")
        return
    project_current_scene = project_document.get("current_scene")
    if project_current_scene is not None and (
        project_document.get("status") == ProjectState.accepted.value
        or project_document.get("current_run") == run_id
    ):
        reasons.append("accepted project current_scene must be null")

    timeline_reference = timeline_document.get("path")
    timeline_path = (
        _validate_file_reference(root, timeline_reference, "timeline", reasons)
        if isinstance(timeline_reference, str)
        else None
    )
    timeline: Timeline | None = None
    if timeline_path is not None:
        try:
            timeline = load_timeline(timeline_path)
        except (OSError, ValueError, ValidationError) as exc:
            reasons.append(f"audiovisual timeline cannot be loaded: {exc}")
    if timeline is not None:
        _validate_audiovisual_timeline_projection(
            timeline,
            timeline_document,
            project,
            manifest_scenes,
            reasons,
        )
        if run_id is not None and timeline_reference != (
            f"golden/accepted/{run_id}/timeline.json"
        ):
            reasons.append("audiovisual timeline path must identify the accepted snapshot")
    canonical_duration = timeline.duration_seconds if timeline is not None else 0.0
    expected_final_contract = _expected_final_media_contract(project, canonical_duration)
    manifest_final_contract = _mapping_or_error(
        manifest.get("final_media_contract"),
        "final media contract",
        reasons,
    )
    if manifest_final_contract != expected_final_contract:
        reasons.append("golden final media contract disagrees with project and timeline")
    manifest_tolerances = _mapping_or_error(
        manifest.get("tolerances"),
        "tolerances",
        reasons,
    )
    if manifest_tolerances.get("final_duration_seconds") != 0.05:
        reasons.append("golden final duration tolerance must be 0.05 seconds")
    selective_lineage: dict[str, str] | None = None
    if run_id is not None and _safe_run_id(run_id):
        _validate_audiovisual_package_snapshot_paths(
            project,
            manifest_scenes,
            run_id,
            reasons,
        )

    if run_id is None or not _safe_run_id(run_id):
        return
    run_relative = f"artifacts/{run_id}/run.json"
    run_path = _validate_file_reference(root, run_relative, "accepted run", reasons)
    if run_path is None:
        return
    try:
        run_document = _read_object(run_path)
    except (OSError, ValueError) as exc:
        reasons.append(f"accepted run cannot be loaded: {exc}")
        return
    selective_lineage, base_run_path, base_run_document = _validate_selective_lineage_snapshot(
        root,
        project,
        manifest,
        run_document,
        run_id,
        reasons,
    )
    _validate_audiovisual_provenance_snapshots(
        root,
        project,
        manifest_scenes,
        run_id,
        selective_lineage,
        base_run_path,
        base_run_document,
        reasons,
    )
    _validate_selective_sibling_trees(
        root,
        project,
        run_id,
        selective_lineage,
        base_run_path,
        reasons,
    )
    _validate_audiovisual_run_snapshot(
        root,
        project,
        project_document,
        manifest,
        timeline_document,
        manifest_scenes,
        timeline,
        run_document,
        run_id,
        reasons,
    )


def _validate_audiovisual_package_snapshot_paths(
    project: Project,
    manifest_scenes: list[object],
    run_id: str,
    reasons: list[str],
) -> None:
    """Require manifest package references to point at this acceptance snapshot."""

    for index, project_scene in enumerate(project.scenes):
        if index >= len(manifest_scenes):
            break
        scene = _mapping_or_error(
            manifest_scenes[index],
            f"audiovisual manifest scene {index + 1}",
            reasons,
        )
        snapshot_root = f"golden/accepted/{run_id}/{project_scene.path}"
        for key, filename in (
            ("plan_path", "plan.json"),
            ("brief_path", "brief.json"),
            ("expectations_path", "expectations.json"),
        ):
            expected = f"{snapshot_root}/{filename}"
            if scene.get(key) != expected:
                reasons.append(
                    f"audiovisual scene {project_scene.id} {key} must reference "
                    "its accepted snapshot"
                )


def _validate_audiovisual_provenance_snapshots(
    root: Path,
    project: Project,
    manifest_scenes: list[object],
    run_id: str,
    selective_lineage: Mapping[str, str] | None,
    base_run_path: Path | None,
    base_run_document: Mapping[str, object] | None,
    reasons: list[str],
) -> None:
    """Check permanent provenance bytes and embedded facts against the manifest."""

    for index, project_scene in enumerate(project.scenes):
        if index >= len(manifest_scenes):
            break
        scene = _mapping_or_error(
            manifest_scenes[index],
            f"audiovisual manifest scene {index + 1}",
            reasons,
        )
        provenance_value = scene.get("provenance_path")
        provenance_path = (
            _validate_file_reference(
                root,
                provenance_value,
                f"scene {project_scene.id} provenance",
                reasons,
            )
            if isinstance(provenance_value, str)
            else None
        )
        if provenance_path is None:
            continue
        try:
            provenance_document = _read_object(provenance_path)
        except (OSError, ValueError) as exc:
            reasons.append(f"scene {project_scene.id} provenance cannot be loaded: {exc}")
            continue
        try:
            normalized = _relative_document_paths(
                root,
                provenance_document,
                ("run_path", "source_path"),
            )
        except ValueError as exc:
            reasons.append(f"scene {project_scene.id} provenance paths are invalid: {exc}")
            continue
        if scene.get("provenance") != normalized:
            reasons.append(
                f"scene {project_scene.id} provenance facts disagree with its file"
            )
        if normalized.get("scene_id") != project_scene.id:
            reasons.append(f"scene {project_scene.id} provenance scene_id is incorrect")
        base_provenance = None
        if (
            selective_lineage is not None
            and project_scene.id != selective_lineage["selected_scene_id"]
        ):
            base_provenance = _base_scene_provenance(
                root,
                project_scene,
                base_run_path,
                base_run_document,
                reasons,
            )
        expected_run_id = _expected_provenance_run_id(
            run_id,
            project_scene.id,
            selective_lineage,
            base_provenance,
        )
        if base_provenance is not None and normalized != base_provenance:
            reasons.append(
                f"scene {project_scene.id} provenance differs from immediate base run"
            )
        if normalized.get("run_id") != expected_run_id:
            reasons.append(f"scene {project_scene.id} provenance run_id is incorrect")
        if normalized.get("run_path") != f"artifacts/{expected_run_id}":
            reasons.append(f"scene {project_scene.id} provenance run_path is not canonical")
        source_path = normalized.get("source_path")
        if not isinstance(source_path, str) or not source_path.startswith(
            f"artifacts/{expected_run_id}/pipeline/{project_scene.id}/"
        ) or not source_path.endswith("/scene.py"):
            reasons.append(f"scene {project_scene.id} provenance source_path is not canonical")


def _base_scene_provenance(
    root: Path,
    project_scene: ProjectSceneRef,
    base_run_path: Path | None,
    base_run_document: Mapping[str, object] | None,
    reasons: list[str],
) -> dict[str, object] | None:
    """Load the corresponding sibling provenance from the immediate base run."""

    if base_run_path is None or base_run_document is None:
        return None
    records = base_run_document.get("scenes")
    if not isinstance(records, list):
        reasons.append("selective base run scenes are required for sibling lineage")
        return None
    record = next(
        (
            item
            for item in records
            if isinstance(item, dict) and item.get("id") == project_scene.id
        ),
        None,
    )
    if not isinstance(record, dict):
        reasons.append(
            f"selective base run is missing sibling scene {project_scene.id}"
        )
        return None
    base_run_id = base_run_path.name
    relative = f"artifacts/{base_run_id}/{project_scene.path}/code-provenance.json"
    provenance_path = _validate_file_reference(
        root,
        relative,
        f"base scene {project_scene.id} provenance",
        reasons,
    )
    if provenance_path is None:
        return None
    if _relative_or_error(
        root,
        record.get("provenance_path"),
        f"base scene {project_scene.id} provenance path",
        reasons,
    ) != relative:
        reasons.append(
            f"base scene {project_scene.id} provenance path is not canonical"
        )
    try:
        document = _read_object(provenance_path)
        return _relative_document_paths(root, document, ("run_path", "source_path"))
    except (OSError, ValueError) as exc:
        reasons.append(
            f"base scene {project_scene.id} provenance cannot be loaded: {exc}"
        )
        return None


def _validate_selective_sibling_trees(
    root: Path,
    project: Project,
    run_id: str,
    selective_lineage: Mapping[str, str] | None,
    base_run_path: Path | None,
    reasons: list[str],
) -> None:
    """Require every reused sibling tree to match its immediate base byte-for-byte."""

    if selective_lineage is None or base_run_path is None:
        return
    base_run_id = selective_lineage["base_run_id"]
    for project_scene in project.scenes:
        if project_scene.id == selective_lineage["selected_scene_id"]:
            continue
        current_scene_path = _artifact_tree_path(
            root,
            f"artifacts/{run_id}/{project_scene.path}",
        )
        base_scene_path = _artifact_tree_path(
            root,
            f"artifacts/{base_run_id}/{project_scene.path}",
        )
        current_pipeline_path = _artifact_tree_path(
            root,
            f"artifacts/{run_id}/pipeline/{project_scene.id}",
        )
        base_pipeline_path = _artifact_tree_path(
            root,
            f"artifacts/{base_run_id}/pipeline/{project_scene.id}",
        )
        current_scene_tree = _evidence_tree_snapshot(
            current_scene_path,
            label=f"scene {project_scene.id} current tree",
            reasons=reasons,
        )
        base_scene_tree = _evidence_tree_snapshot(
            base_scene_path,
            label=f"scene {project_scene.id} immediate base tree",
            reasons=reasons,
        )
        if current_scene_tree != base_scene_tree:
            reasons.append(
                f"scene {project_scene.id} scene tree differs from immediate base run"
            )
        current_pipeline_tree = _evidence_tree_snapshot(
            current_pipeline_path,
            label=f"scene {project_scene.id} current pipeline tree",
            reasons=reasons,
        )
        base_pipeline_tree = _evidence_tree_snapshot(
            base_pipeline_path,
            label=f"scene {project_scene.id} immediate base pipeline tree",
            reasons=reasons,
        )
        if current_pipeline_tree != base_pipeline_tree:
            reasons.append(
                f"scene {project_scene.id} pipeline tree differs from immediate base run"
            )


def _artifact_tree_path(root: Path, relative: str) -> Path:
    """Build a project-relative tree path without resolving nested symlinks."""

    base = root.resolve()
    candidate = base / relative
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise ValueError("artifact tree path escapes the project") from exc
    return candidate


def _evidence_tree_snapshot(
    root: Path,
    *,
    label: str,
    reasons: list[str],
) -> dict[str, tuple[str, str]]:
    """Snapshot regular evidence tree shape and file digests without following links."""

    if root.is_symlink():
        reasons.append(f"{label} contains symlink: {root}")
        return {}
    if not root.is_dir():
        reasons.append(f"{label} is missing or not a directory: {root}")
        return {}
    snapshot: dict[str, tuple[str, str]] = {}
    pending = [root]
    while pending:
        current = pending.pop()
        try:
            entries = list(os.scandir(current))
        except OSError as exc:
            reasons.append(f"{label} cannot be inspected: {exc}")
            continue
        for entry in entries:
            entry_path = Path(entry.path)
            relative = entry_path.relative_to(root).as_posix()
            if entry.is_symlink():
                reasons.append(f"{label} contains symlink: {entry_path}")
                continue
            if entry.is_dir(follow_symlinks=False):
                snapshot[relative] = ("directory", "")
                pending.append(entry_path)
            elif entry.is_file(follow_symlinks=False):
                try:
                    digest = hash_file(entry_path)
                except OSError as exc:
                    reasons.append(f"{label} file cannot be hashed: {exc}")
                    continue
                snapshot[relative] = ("file", digest)
            else:
                reasons.append(
                    f"{label} contains non-regular entry: {entry_path}"
                )
    return snapshot


def _validate_selective_lineage_snapshot(
    root: Path,
    project: Project,
    manifest: Mapping[str, object],
    run_document: Mapping[str, object],
    run_id: str,
    reasons: list[str],
) -> tuple[dict[str, str] | None, Path | None, dict[str, object] | None]:
    """Cross-check selective lineage and its immutable base run evidence."""

    try:
        manifest_lineage = _selective_lineage_from_document(
            manifest,
            project,
            run_id,
            label="golden manifest",
        )
    except ValueError as exc:
        reasons.append(str(exc))
        manifest_lineage = None
    try:
        run_lineage = _selective_lineage_from_document(
            run_document,
            project,
            run_id,
            label="accepted run",
        )
    except ValueError as exc:
        reasons.append(str(exc))
        run_lineage = None

    manifest_keys = tuple(key for key in _SELECTIVE_LINEAGE_KEYS if key in manifest)
    run_keys = tuple(key for key in _SELECTIVE_LINEAGE_KEYS if key in run_document)
    if manifest_keys != run_keys:
        reasons.append("golden manifest selective lineage presence disagrees with accepted run")
    elif manifest_lineage != run_lineage:
        reasons.append("golden manifest selective lineage disagrees with accepted run")

    lineage = run_lineage or manifest_lineage
    if lineage is None:
        return None, None, None
    base_run_id = lineage["base_run_id"]
    base_relative = f"artifacts/{base_run_id}/run.json"
    try:
        base_path = _safe_reference(root.resolve(), Path(base_relative))
    except ValueError as exc:
        reasons.append(f"selective base run path is invalid: {exc}")
        return lineage, None, None
    if not base_path.is_file():
        reasons.append(f"selective base run is missing: {base_relative}")
        return lineage, None, None
    try:
        base_document = _read_object(base_path)
    except (OSError, ValueError) as exc:
        reasons.append(f"selective base run cannot be loaded: {exc}")
        return lineage, base_path.parent, None
    if base_document.get("run_id") != base_run_id:
        reasons.append("selective base run run_id disagrees with lineage")
    if base_document.get("project_id") != project.id:
        reasons.append("selective base run project_id disagrees with project")
    if base_document.get("state") != "ready":
        reasons.append("selective base run state must be ready")
    if "current_scene" not in base_document or base_document.get("current_scene") is not None:
        reasons.append("selective base run current_scene must be null")
    return lineage, base_path.parent, base_document


def _validate_audiovisual_timeline_projection(
    timeline: Timeline,
    timeline_document: Mapping[str, object],
    project: Project,
    manifest_scenes: list[object],
    reasons: list[str],
) -> None:
    """Require manifest timeline fields and scene cardinality to be authored facts."""

    for key, expected in (
        ("schema_version", timeline.schema_version),
        ("status", timeline.status),
        ("method", timeline.method),
        ("duration_seconds", timeline.duration_seconds),
    ):
        if timeline_document.get(key) != expected:
            reasons.append(f"audiovisual timeline {key} disagrees with timeline.json")

    raw_segments = timeline_document.get("segments")
    if not isinstance(raw_segments, list):
        return
    if len(raw_segments) != len(timeline.segments):
        reasons.append("audiovisual timeline segment count disagrees with timeline.json")
    if len(project.scenes) != len(timeline.segments):
        reasons.append("project scenes count disagrees with timeline.json")
    if len(manifest_scenes) != len(timeline.segments):
        reasons.append("golden scenes count disagrees with timeline.json")

    for index, segment in enumerate(timeline.segments):
        if index >= len(raw_segments):
            break
        document = _mapping_or_error(
            raw_segments[index],
            f"audiovisual timeline segment {index + 1}",
            reasons,
        )
        for key, expected in (
            ("id", segment.id),
            ("order", segment.order),
            ("narration_text", segment.narration_text),
            ("objective", segment.objective),
            ("start_seconds", segment.start_seconds),
            ("end_seconds", segment.end_seconds),
            ("target_duration_seconds", segment.target_duration_seconds),
            ("start_provenance", segment.start_provenance),
            ("end_provenance", segment.end_provenance),
            ("plan_path", segment.plan_path),
        ):
            if document.get(key) != expected:
                reasons.append(
                    f"audiovisual timeline segment {segment.id} {key} "
                    "disagrees with timeline.json"
                )
        if index < len(project.scenes):
            project_scene = project.scenes[index]
            if project_scene.id != segment.id or project_scene.order != segment.order:
                reasons.append("project scene IDs or order disagree with timeline.json")
            if project_scene.plan_path != segment.plan_path:
                reasons.append("project scene plan paths disagree with timeline.json")
        if index < len(manifest_scenes):
            scene = _mapping_or_error(
                manifest_scenes[index],
                f"audiovisual manifest scene {index + 1}",
                reasons,
            )
            for key, expected in (
                ("id", segment.id),
                ("order", segment.order),
                ("narration_text", segment.narration_text),
                ("start_seconds", segment.start_seconds),
                ("end_seconds", segment.end_seconds),
                ("target_duration_seconds", segment.target_duration_seconds),
            ):
                if scene.get(key) != expected:
                    reasons.append(
                        f"audiovisual manifest scene {segment.id} {key} "
                        "disagrees with timeline.json"
                    )


def _validate_audiovisual_run_snapshot(
    root: Path,
    project: Project,
    project_document: Mapping[str, object],
    manifest: Mapping[str, object],
    timeline_document: Mapping[str, object],
    manifest_scenes: list[object],
    timeline: Timeline | None,
    run_document: Mapping[str, object],
    run_id: str,
    reasons: list[str],
) -> None:
    """Validate the accepted run and its composition as immutable evidence."""

    if run_document.get("schema_version") != "project.render-run/1":
        reasons.append("accepted run schema_version is unsupported")
    if run_document.get("run_id") != run_id:
        reasons.append("accepted run run_id disagrees with manifest")
    if run_document.get("project_id") != project.id:
        reasons.append("accepted run project_id disagrees with project")
    if run_document.get("state") != "ready":
        reasons.append("accepted run state must be ready")
    if "current_scene" not in run_document:
        reasons.append("accepted run current_scene is required")
    elif run_document.get("current_scene") is not None:
        reasons.append("accepted run current_scene must be null")
    run_path_value = run_document.get("run_path")
    if _relative_or_error(root, run_path_value, "accepted run path", reasons) != (
        f"artifacts/{run_id}"
    ):
        reasons.append("accepted run path must reference the accepted run")
    timeline_path = project.timeline_path
    run_timeline = run_document.get("timeline_path")
    if timeline_path is None or _relative_or_error(
        root,
        run_timeline,
        "accepted run timeline path",
        reasons,
    ) != timeline_path:
        reasons.append("accepted run timeline_path disagrees with project")

    records = run_document.get("scenes")
    if not isinstance(records, list):
        reasons.append("accepted run scenes must be a list")
    elif timeline is not None:
        if len(records) != len(timeline.segments):
            reasons.append("accepted run scene count disagrees with timeline.json")
        for index, segment in enumerate(timeline.segments):
            if index >= len(records):
                break
            record = _mapping_or_error(
                records[index],
                f"accepted run scene {index + 1}",
                reasons,
            )
            if record.get("id") != segment.id or record.get("order") != segment.order:
                reasons.append(
                    f"accepted run scene {segment.id} IDs or order disagree with timeline.json"
                )
            if index < len(project.scenes):
                project_scene = project.scenes[index]
                _validate_run_scene_record(
                    root,
                    run_id,
                    segment.id,
                    project_scene.path,
                    project_scene.plan_path,
                    record,
                    reasons,
                )

    expected_input_hashes = _audiovisual_input_hashes(
        root,
        project,
        timeline_document,
        reasons,
    )
    if (
        expected_input_hashes is not None
        and run_document.get("input_hashes") != expected_input_hashes
    ):
        reasons.append("accepted run input_hashes disagree with project files")
    expected_package_hashes = _manifest_package_hashes(root, manifest_scenes, reasons)
    if (
        expected_package_hashes is not None
        and run_document.get("package_hashes") != expected_package_hashes
    ):
        reasons.append("accepted run package_hashes disagree with project packages")

    composition_relative = f"artifacts/{run_id}/composition.json"
    composition_path = _validate_file_reference(
        root,
        composition_relative,
        "accepted composition",
        reasons,
    )
    if composition_path is None:
        return
    try:
        composition = _read_object(composition_path)
    except (OSError, ValueError) as exc:
        reasons.append(f"accepted composition cannot be loaded: {exc}")
        return
    run_composition = _mapping_or_error(
        run_document.get("composition"),
        "accepted run composition",
        reasons,
    )
    if run_composition != composition:
        reasons.append("accepted run composition disagrees with composition.json")
    run_final_validation = _mapping_or_error(
        run_document.get("final_validation"),
        "accepted run final validation",
        reasons,
    )
    composition_validation = _mapping_or_error(
        composition.get("validation"),
        "accepted composition validation",
        reasons,
    )
    if run_final_validation != composition_validation:
        reasons.append(
            "accepted run final_validation disagrees with composition.validation"
        )
    final_relative = f"artifacts/{run_id}/final.mp4"
    reasons.extend(
        _composition_fact_errors(
            run_composition,
            project_root=root,
            final_relative=final_relative,
            expected_validation=run_final_validation,
        )
    )
    reasons.extend(
        _composition_fact_errors(
            composition,
            project_root=root,
            final_relative=final_relative,
            expected_validation=run_final_validation,
        )
    )

    composition_manifest = _relative_document_paths(
        root,
        composition,
        ("output_path", "log_path"),
    )
    if isinstance(composition_manifest.get("validation"), dict):
        composition_manifest["validation"] = _relative_document_paths(
            root,
            composition_manifest["validation"],
            ("path",),
        )
    if composition_manifest != manifest.get("composition"):
        reasons.append("golden composition disagrees with composition.json")
    final_manifest = _relative_document_paths(root, run_final_validation, ("path",))
    if final_manifest != manifest.get("final_validation"):
        reasons.append("golden final_validation disagrees with accepted run")

    exact_paths = (
        ("accepted run output", run_document.get("output_path")),
        ("composition output", composition.get("output_path")),
        ("composition validation", composition_validation.get("path")),
        ("accepted final validation", run_final_validation.get("path")),
        ("manifest composition output", _nested_value(manifest.get("composition"), "output_path")),
        (
            "manifest composition validation",
            _nested_path(manifest.get("composition"), "validation", "path"),
        ),
        ("manifest final validation", _nested_value(manifest.get("final_validation"), "path")),
        ("manifest final artifact", _nested_path(manifest.get("artifacts"), "final", "path")),
    )
    for label, value in exact_paths:
        if _relative_or_error(root, value, label, reasons) != final_relative:
            reasons.append(f"{label} must reference {final_relative}")
    canonical_duration = timeline.duration_seconds if timeline is not None else 0.0
    expected_final_contract = _expected_final_media_contract(project, canonical_duration)
    final_path = _safe_reference(root.resolve(), Path(final_relative))
    reasons.extend(_final_artifact_attestation_errors(run_document, final_path))
    reasons.extend(
        _final_media_fact_errors(
            run_final_validation,
            project_root=root,
            final_media_contract=expected_final_contract,
            expected_duration_seconds=canonical_duration,
            duration_tolerance_seconds=0.05,
            final_path=final_path,
        )
    )
    final_artifact = _mapping_or_error(manifest.get("artifacts"), "artifacts", reasons)
    final_artifact = _mapping_or_error(final_artifact.get("final"), "final artifact", reasons)
    if final_path.is_file():
        if final_artifact.get("sha256") != hash_file(final_path):
            reasons.append("manifest final artifact hash disagrees with final.mp4")
        if final_artifact.get("size_bytes") != final_path.stat().st_size:
            reasons.append("manifest final artifact size disagrees with final.mp4")
        if final_artifact.get("sha256") != run_document.get("final_sha256"):
            reasons.append("manifest final artifact hash disagrees with ready run attestation")
        if final_artifact.get("size_bytes") != run_document.get("final_size_bytes"):
            reasons.append("manifest final artifact size disagrees with ready run attestation")

    provenance = _mapping_or_error(manifest.get("provenance"), "provenance", reasons)
    expected_provenance = {
        "project": "project.json",
        "run": f"artifacts/{run_id}/run.json",
        "timeline": timeline_document.get("path"),
        "manifest": "golden/manifest.json",
        "source_run": run_id,
    }
    if provenance != expected_provenance:
        reasons.append("golden provenance is not the exact accepted-run provenance")
    if project_document.get("accepted_run") != run_id:
        reasons.append("project accepted_run disagrees with manifest run_id")


def _validate_run_scene_record(
    root: Path,
    run_id: str,
    scene_id: str,
    scene_path: str,
    plan_path: str,
    record: Mapping[str, object],
    reasons: list[str],
) -> None:
    """Check persisted run scene paths and content hashes without executing code."""

    if record.get("state") != "ready":
        reasons.append(f"accepted run scene {scene_id} must be ready")
    attempts = record.get("attempts")
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts <= 0:
        reasons.append(f"accepted run scene {scene_id} attempts must be positive")
        return
    try:
        expected_paths = _canonical_run_scene_paths(
            root,
            run_id,
            scene_path,
            scene_id,
            plan_path,
            record,
        )
    except ValueError as exc:
        reasons.append(str(exc))
        return
    for path_key, digest_key in (
        ("raw_path", "raw_sha256"),
        ("normalized_path", "normalized_sha256"),
        ("normalization_path", "normalization_sha256"),
        ("code_path", "code_sha256"),
        ("provenance_path", "provenance_sha256"),
        ("diagnostics_path", "diagnostics_sha256"),
        ("observation_path", "observation_sha256"),
        ("quality_path", "quality_sha256"),
    ):
        path = _safe_reference(root.resolve(), Path(expected_paths[path_key]))
        if not path.is_file():
            reasons.append(f"run scene {scene_id} {path_key} is missing")
            continue
        digest = record.get(digest_key)
        if not isinstance(digest, str) or digest != hash_file(path):
            reasons.append(f"run scene {scene_id} {digest_key} disagrees with evidence")


def _audiovisual_input_hashes(
    root: Path,
    project: Project,
    timeline_document: Mapping[str, object],
    reasons: list[str],
) -> dict[str, str] | None:
    """Recompute immutable input hashes used by an audiovisual run."""

    timeline_reference = timeline_document.get("path")
    if not isinstance(timeline_reference, str):
        reasons.append("accepted golden has no timeline snapshot path")
        return None
    paths: tuple[tuple[str, str], ...] = (
        ("script_sha256", project.script_path),
        ("audio_sha256", project.audio_path),
        ("timeline_sha256", timeline_reference),
    )
    expected: dict[str, str] = {}
    for key, relative in paths:
        try:
            expected[key] = hash_file(_safe_reference(root.resolve(), Path(relative)))
        except (OSError, ValueError) as exc:
            reasons.append(f"accepted input {relative} cannot be hashed: {exc}")
            return None
    timeline_hash = timeline_document.get("sha256")
    if timeline_hash != expected["timeline_sha256"]:
        reasons.append("golden timeline hash disagrees with the accepted snapshot")
    return expected


def _manifest_package_hashes(
    root: Path,
    manifest_scenes: Sequence[object],
    reasons: list[str],
) -> dict[str, str] | None:
    """Recompute ordered scene package hashes from immutable manifest files."""

    scene_documents: list[dict[str, object]] = []
    for raw_scene in manifest_scenes:
        if not isinstance(raw_scene, dict):
            reasons.append("accepted run package scene must be an object")
            continue
        scene_documents.append(
            {key: value for key, value in raw_scene.items() if isinstance(key, str)}
        )
    if len(scene_documents) != len(manifest_scenes):
        return None
    scene_documents.sort(key=lambda scene: str(scene.get("id", "")))
    package_hashes: dict[str, str] = {}
    for scene in scene_documents:
        scene_id = scene.get("id")
        if not isinstance(scene_id, str) or not scene_id:
            reasons.append("accepted run package scene ID is required")
            continue
        digest = hashlib.sha256()
        for logical_name, key in (
            ("plan.json", "plan_path"),
            ("brief.json", "brief_path"),
            ("expectations.json", "expectations_path"),
        ):
            relative = scene.get(key)
            if not isinstance(relative, str):
                reasons.append(f"accepted run package {key} is required")
                continue
            try:
                target = _safe_reference(root.resolve(), Path(relative))
            except ValueError as exc:
                reasons.append(f"accepted run package {key}: {exc}")
                continue
            if not target.is_file():
                reasons.append(f"accepted run package {key} is missing: {relative}")
                continue
            digest.update(logical_name.encode("utf-8"))
            digest.update(b"\0")
            try:
                with target.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
            except OSError as exc:
                reasons.append(f"accepted run package {key} cannot be read: {exc}")
                continue
            digest.update(b"\0")
        package_hashes[scene_id] = digest.hexdigest()
    return package_hashes


def _relative_or_error(
    root: Path,
    value: object,
    label: str,
    reasons: list[str],
) -> str | None:
    try:
        return _project_relative_value(root, value, label=label)
    except ValueError as exc:
        reasons.append(str(exc))
        return None


def _nested_path(value: object, key: str, nested_key: str) -> object | None:
    nested = value.get(key) if isinstance(value, dict) else None
    return nested.get(nested_key) if isinstance(nested, dict) else None


def _validate_audiovisual_timeline(
    root: Path,
    project_document: Mapping[str, object],
    timeline: Mapping[str, object],
    reasons: list[str],
) -> None:
    if timeline.get("status") != "confirmed":
        reasons.append("audiovisual timeline must be confirmed")
    if not isinstance(timeline.get("method"), str):
        reasons.append("audiovisual timeline method is required")
    timeline_path = timeline.get("path")
    if isinstance(timeline_path, str):
        if not timeline_path.startswith("golden/accepted/"):
            reasons.append("audiovisual timeline must reference an accepted snapshot")
    _validate_hashed_reference(
        root,
        timeline,
        label="audiovisual timeline",
        reasons=reasons,
    )
    duration = timeline.get("duration_seconds")
    duration_value = _finite_number(duration)
    if duration_value is None or duration_value <= 0:
        reasons.append("audiovisual timeline duration must be positive")
    segments = timeline.get("segments")
    if not isinstance(segments, list) or not segments:
        reasons.append("audiovisual timeline segments must be non-empty")
        return
    previous_end: float | None = None
    for expected_order, raw_segment in enumerate(segments, start=1):
        segment = _mapping_or_error(raw_segment, "timeline segment", reasons)
        if segment.get("order") != expected_order:
            reasons.append("audiovisual timeline segment order is not contiguous")
        start = _finite_number(segment.get("start_seconds"))
        end = _finite_number(segment.get("end_seconds"))
        target = _finite_number(segment.get("target_duration_seconds"))
        if start is None or end is None or target is None or end <= start or target <= 0:
            reasons.append("audiovisual timeline segment timing is invalid")
        elif abs((end - start) - target) > 1e-6:
            reasons.append("audiovisual timeline segment duration is inconsistent")
        if previous_end is not None and start is not None and abs(start - previous_end) > 1e-6:
            reasons.append("audiovisual timeline contains a gap or overlap")
        if end is not None:
            previous_end = end
    first = _mapping_or_error(segments[0], "first timeline segment", reasons)
    last = _mapping_or_error(segments[-1], "last timeline segment", reasons)
    first_start = _finite_number(first.get("start_seconds"))
    last_end = _finite_number(last.get("end_seconds"))
    if first_start is None or abs(first_start) > 1e-6:
        reasons.append("audiovisual timeline must start at zero")
    if duration_value is not None and (
        last_end is None or abs(last_end - duration_value) > 1e-6
    ):
        reasons.append("audiovisual timeline must end at its duration")


def _validate_audiovisual_scenes(
    root: Path,
    scenes: list[object],
    timeline: Mapping[str, object],
    reasons: list[str],
) -> None:
    timeline_segments = timeline.get("segments")
    if not isinstance(timeline_segments, list):
        return
    seen: set[str] = set()
    for expected_order, raw_scene in enumerate(scenes, start=1):
        scene = _mapping_or_error(raw_scene, "audiovisual scene", reasons)
        scene_id = scene.get("id")
        if not isinstance(scene_id, str) or not scene_id.strip():
            reasons.append("audiovisual scene ID is required")
        elif scene_id in seen:
            reasons.append(f"audiovisual scene ID is duplicated: {scene_id}")
        else:
            seen.add(scene_id)
        if scene.get("order") != expected_order:
            reasons.append("audiovisual scene order is not contiguous")
        if expected_order <= len(timeline_segments):
            segment = _mapping_or_error(
                timeline_segments[expected_order - 1],
                "timeline scene",
                reasons,
            )
            if scene_id != segment.get("id"):
                reasons.append("audiovisual scene ID disagrees with timeline")
            scene_plan_path = scene.get("plan_path")
            if isinstance(scene_plan_path, str) and not scene_plan_path.startswith(
                "golden/accepted/"
            ):
                reasons.append("audiovisual scene plan must reference an accepted snapshot")
        for key in (
            "plan_path",
            "brief_path",
            "expectations_path",
            "code_path",
            "provenance_path",
        ):
            value = scene.get(key)
            if isinstance(value, str):
                _validate_file_reference(root, value, f"scene {key}", reasons)
            else:
                reasons.append(f"audiovisual scene {key} is required")
        _validate_hashed_reference(
            root,
            scene,
            label=f"scene {scene_id} plan",
            path_key="plan_path",
            hash_key="plan_sha256",
            reasons=reasons,
        )
        _validate_hashed_reference(
            root,
            scene,
            label=f"scene {scene_id} brief",
            path_key="brief_path",
            hash_key="brief_sha256",
            reasons=reasons,
        )
        _validate_hashed_reference(
            root,
            scene,
            label=f"scene {scene_id} expectations",
            path_key="expectations_path",
            hash_key="expectations_sha256",
            reasons=reasons,
        )
        _validate_hashed_reference(
            root,
            scene,
            label=f"scene {scene_id} code",
            path_key="code_path",
            hash_key="code_sha256",
            reasons=reasons,
        )
        _validate_hashed_reference(
            root,
            scene,
            label=f"scene {scene_id} provenance",
            path_key="provenance_path",
            hash_key="provenance_sha256",
            reasons=reasons,
        )
        evidence_document = _mapping_or_none(scene.get("evidence"))
        for key in ("raw", "normalized", "normalization", "diagnostics", "semantic", "quality"):
            evidence = _mapping_or_error(
                evidence_document.get(key),
                f"scene {scene_id} {key} evidence",
                reasons,
            )
            _validate_artifact_reference(
                root,
                evidence,
                f"scene {scene_id} {key} evidence",
                reasons,
            )
        for key in ("raw_media", "normalized_media", "semantic", "quality"):
            value = _mapping_or_error(scene.get(key), f"scene {scene_id} {key}", reasons)
            _validate_artifact_reference(root, value, f"scene {scene_id} {key}", reasons)
        attempts = scene.get("attempts")
        if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts <= 0:
            reasons.append(f"scene {scene_id} attempts must be positive")


def _validate_hashed_reference(
    root: Path,
    document: Mapping[str, object],
    *,
    label: str,
    reasons: list[str],
    expected_project_hash: object | None = None,
    path_key: str = "path",
    hash_key: str = "sha256",
) -> None:
    value = document.get(path_key)
    if not isinstance(value, str):
        reasons.append(f"{label} path is required")
        return
    target = _validate_file_reference(root, value, label, reasons)
    digest = document.get(hash_key)
    if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
        reasons.append(f"{label} hash must be a lowercase SHA-256")
    elif target is not None:
        try:
            actual = hash_file(target)
        except OSError as exc:
            reasons.append(f"{label} cannot be hashed: {exc}")
        else:
            if digest != actual:
                reasons.append(f"{label} hash does not match referenced file")
    if expected_project_hash is not None and digest != expected_project_hash:
        reasons.append(f"{label} hash disagrees with project")


def _validate_artifact_reference(
    root: Path,
    document: Mapping[str, object],
    label: str,
    reasons: list[str],
) -> Path | None:
    value = document.get("path")
    target = (
        _validate_file_reference(root, value, label, reasons)
        if isinstance(value, str)
        else None
    )
    digest = document.get("sha256")
    if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
        reasons.append(f"{label} hash must be a lowercase SHA-256")
    elif target is not None:
        try:
            if digest != hash_file(target):
                reasons.append(f"{label} hash does not match referenced file")
        except OSError as exc:
            reasons.append(f"{label} cannot be hashed: {exc}")
    size = document.get("size_bytes")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        reasons.append(f"{label} size_bytes must be non-negative")
    elif target is not None:
        try:
            actual_size = target.stat().st_size
        except OSError as exc:
            reasons.append(f"{label} cannot be sized: {exc}")
        else:
            if actual_size != size:
                reasons.append(f"{label} size does not match referenced file")
    return target


def _validate_file_reference(
    root: Path,
    value: str,
    label: str,
    reasons: list[str],
) -> Path | None:
    if not _safe_relative_text(value):
        reasons.append(f"{label} must be a safe relative reference")
        return None
    try:
        target = _safe_reference(root.resolve(), Path(value))
    except ValueError as exc:
        reasons.append(f"{label}: {exc}")
        return None
    if not target.is_file():
        reasons.append(f"{label} is missing: {value}")
        return None
    return target


def _mapping_or_error(value: object, label: str, reasons: list[str]) -> dict[str, object]:
    if not isinstance(value, dict):
        reasons.append(f"{label} must be an object")
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _mapping_or_none(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _nested_value(value: object, key: str) -> object | None:
    return value.get(key) if isinstance(value, dict) else None


def _expected_final_media_contract(
    project: Project,
    duration_seconds: float,
) -> dict[str, object]:
    """Build the model-free final media contract expected from the renderer."""

    return {
        "video_codec": "libx264",
        "audio_codec": "aac",
        "pixel_format": "yuv420p",
        "resolution": {
            "width": project.theme.resolution[0],
            "height": project.theme.resolution[1],
        },
        "fps": project.theme.fps,
        "timebase": "1/90000",
        "duration_seconds": duration_seconds,
    }


def _final_media_fact_errors(
    validation: Mapping[str, object],
    *,
    project_root: Path,
    final_media_contract: Mapping[str, object],
    expected_duration_seconds: float,
    duration_tolerance_seconds: float,
    final_path: Path,
) -> list[str]:
    """Validate persisted final-media facts without probing or executing media."""

    errors: list[str] = []
    expected_final_relative: str | None = None
    try:
        expected_final_relative = final_path.resolve().relative_to(
            project_root.resolve()
        ).as_posix()
    except ValueError:
        errors.append("final artifact is outside the project")

    validation_path = validation.get("path")
    if not isinstance(validation_path, str):
        errors.append("final validation path is required")
    else:
        try:
            validation_relative = _project_relative_value(
                project_root,
                validation_path,
                label="final validation",
            )
        except ValueError as exc:
            errors.append(str(exc))
        else:
            if (
                expected_final_relative is not None
                and validation_relative != expected_final_relative
            ):
                errors.append("final validation path must reference final.mp4")

    if validation.get("valid") is not True:
        errors.append("final validation must be valid")
    validation_reasons = validation.get("reasons")
    if not isinstance(validation_reasons, list):
        errors.append("final validation reasons must be a list")
    elif validation_reasons:
        errors.append("final validation reasons must be empty")

    probe_returncode = validation.get("probe_returncode")
    if (
        isinstance(probe_returncode, bool)
        or not isinstance(probe_returncode, int)
        or probe_returncode != 0
    ):
        errors.append("final validation probe_returncode must be zero")

    actual_size: int | None = None
    try:
        if not final_path.is_file():
            errors.append("final artifact is missing")
        else:
            actual_size = final_path.stat().st_size
            if actual_size <= 0:
                errors.append("final artifact size must be positive")
    except OSError as exc:
        errors.append(f"final artifact cannot be sized: {exc}")

    size_bytes = validation.get("size_bytes")
    if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes <= 0:
        errors.append("final validation size_bytes must be positive")
        size_value: int | None = None
    else:
        size_value = size_bytes
    probe_size_bytes = validation.get("probe_size_bytes")
    if (
        isinstance(probe_size_bytes, bool)
        or not isinstance(probe_size_bytes, int)
        or probe_size_bytes <= 0
    ):
        errors.append("final validation probe_size_bytes must be positive")
        probe_size_value: int | None = None
    else:
        probe_size_value = probe_size_bytes
    if size_value is not None and probe_size_value is not None and size_value != probe_size_value:
        errors.append("final validation size_bytes and probe_size_bytes disagree")
    if actual_size is not None:
        if size_value is not None and size_value != actual_size:
            errors.append("final validation size_bytes disagrees with final.mp4")
        if probe_size_value is not None and probe_size_value != actual_size:
            errors.append("final validation probe_size_bytes disagrees with final.mp4")

    raw_probe_value = validation.get("raw_probe")
    if not isinstance(raw_probe_value, dict):
        errors.append("final validation raw_probe must be an object")
        raw_probe: dict[str, object] = {}
    else:
        raw_probe = {
            key: value for key, value in raw_probe_value.items() if isinstance(key, str)
        }
    raw_format_value = raw_probe.get("format")
    if not isinstance(raw_format_value, dict):
        errors.append("final validation raw_probe.format must be an object")
        raw_format: dict[str, object] = {}
    else:
        raw_format = {
            key: value for key, value in raw_format_value.items() if isinstance(key, str)
        }
    raw_format_size = _positive_probe_integer(raw_format.get("size"))
    if raw_format_size is None:
        errors.append("final validation raw_probe.format.size must be positive")
    else:
        if probe_size_value is not None and raw_format_size != probe_size_value:
            errors.append("raw_probe.format.size disagrees with probe_size_bytes")
        if actual_size is not None and raw_format_size != actual_size:
            errors.append("raw_probe.format.size disagrees with final.mp4")
    raw_format_duration = _rational_number(raw_format.get("duration"))
    if raw_format_duration is None or raw_format_duration <= 0:
        errors.append("final validation raw_probe.format.duration must be positive and finite")

    raw_streams_value = raw_probe.get("streams")
    if not isinstance(raw_streams_value, list):
        errors.append("final validation raw_probe.streams must be a list")
        raw_streams: list[dict[str, object]] = []
    else:
        raw_streams = []
        for index, value in enumerate(raw_streams_value):
            if not isinstance(value, dict):
                errors.append(f"raw_probe stream {index + 1} must be an object")
                continue
            raw_streams.append(
                {key: item for key, item in value.items() if isinstance(key, str)}
            )
    raw_video_streams = [
        stream for stream in raw_streams if stream.get("codec_type") == "video"
    ]
    raw_audio_streams = [
        stream for stream in raw_streams if stream.get("codec_type") == "audio"
    ]

    video_streams_value = validation.get("video_streams")
    if not isinstance(video_streams_value, list):
        errors.append("final validation video_streams must be a list")
        video_streams: list[dict[str, object]] = []
    else:
        video_streams = []
        for index, value in enumerate(video_streams_value):
            if not isinstance(value, dict):
                errors.append(f"video stream {index + 1} must be an object")
                continue
            video_streams.append(
                {key: item for key, item in value.items() if isinstance(key, str)}
            )
    audio_streams_value = validation.get("audio_streams")
    if not isinstance(audio_streams_value, list):
        errors.append("final validation audio_streams must be a list")
        audio_streams: list[dict[str, object]] = []
    else:
        audio_streams = []
        for index, value in enumerate(audio_streams_value):
            if not isinstance(value, dict):
                errors.append(f"audio stream {index + 1} must be an object")
                continue
            audio_streams.append(
                {key: item for key, item in value.items() if isinstance(key, str)}
            )
    if video_streams != raw_video_streams:
        errors.append("video_streams must be the raw_probe video projection")
    if audio_streams != raw_audio_streams:
        errors.append("audio_streams must be the raw_probe audio projection")
    if len(raw_video_streams) != 1:
        errors.append("final validation must contain exactly one video stream")

    if final_media_contract.get("video_codec") != "libx264":
        errors.append("final media contract video codec must be libx264")
    if final_media_contract.get("audio_codec") != "aac":
        errors.append("final media contract audio codec must be AAC")
    if final_media_contract.get("pixel_format") != "yuv420p":
        errors.append("final media contract pixel format must be yuv420p")
    resolution_value = final_media_contract.get("resolution")
    if not isinstance(resolution_value, dict):
        errors.append("final media contract resolution must be an object")
        resolution: dict[str, object] = {}
    else:
        resolution = {
            key: value for key, value in resolution_value.items() if isinstance(key, str)
        }
    expected_width = _positive_integer(resolution.get("width"))
    expected_height = _positive_integer(resolution.get("height"))
    if expected_width is None or expected_height is None:
        errors.append("final media contract resolution must be positive")
    expected_fps = _finite_number(final_media_contract.get("fps"))
    if expected_fps is None or expected_fps <= 0:
        errors.append("final media contract fps must be positive")
    expected_timebase = _rational_number(final_media_contract.get("timebase"))
    if expected_timebase is None or expected_timebase <= 0:
        errors.append("final media contract timebase must be positive")
    contract_duration = _finite_number(final_media_contract.get("duration_seconds"))
    if contract_duration is None or contract_duration <= 0:
        errors.append("final media contract duration must be positive")
    elif abs(contract_duration - expected_duration_seconds) > 1e-6:
        errors.append("final media contract duration disagrees with timeline")
    if not math.isfinite(expected_duration_seconds) or expected_duration_seconds <= 0:
        errors.append("expected final duration must be positive and finite")
    if not math.isfinite(duration_tolerance_seconds) or duration_tolerance_seconds < 0:
        errors.append("final duration tolerance must be finite and non-negative")

    video_stream = raw_video_streams[0] if len(raw_video_streams) == 1 else {}
    video_codec = video_stream.get("codec_name")
    if not isinstance(video_codec, str) or video_codec.lower() != "h264":
        errors.append("final video stream codec must be h264")
    video_width = _positive_probe_integer(video_stream.get("width"))
    video_height = _positive_probe_integer(video_stream.get("height"))
    if expected_width is not None and video_width != expected_width:
        errors.append("final video stream width disagrees with contract")
    if expected_height is not None and video_height != expected_height:
        errors.append("final video stream height disagrees with contract")
    if video_stream.get("pix_fmt") != "yuv420p":
        errors.append("final video stream pixel format must be yuv420p")
    frame_rate_value = video_stream.get("avg_frame_rate")
    if frame_rate_value is None or (
        isinstance(frame_rate_value, str) and not frame_rate_value.strip()
    ):
        frame_rate_value = video_stream.get("r_frame_rate")
    video_fps = _rational_number(frame_rate_value)
    if video_fps is None or video_fps <= 0:
        errors.append("final video stream frame rate must be positive and finite")
    elif expected_fps is not None and abs(video_fps - expected_fps) > 1e-6:
        errors.append("final video stream frame rate disagrees with contract")
    video_timebase = _rational_number(video_stream.get("time_base"))
    if video_timebase is None or video_timebase <= 0:
        errors.append("final video stream timebase must be positive and finite")
    elif expected_timebase is not None and abs(video_timebase - expected_timebase) > 1e-12:
        errors.append("final video stream timebase disagrees with contract")
    video_stream_duration = _rational_number(video_stream.get("duration"))
    if video_stream_duration is None or video_stream_duration <= 0:
        errors.append("final video stream duration must be positive and finite")

    usable_audio_streams: list[dict[str, object]] = []
    for stream in raw_audio_streams:
        codec = stream.get("codec_name")
        sample_rate = _positive_probe_integer(stream.get("sample_rate"))
        channels = _positive_probe_integer(stream.get("channels"))
        duration = _rational_number(stream.get("duration"))
        if (
            isinstance(codec, str)
            and codec.lower() == "aac"
            and sample_rate is not None
            and channels is not None
            and duration is not None
            and duration > 0
        ):
            usable_audio_streams.append(stream)
    if not usable_audio_streams:
        errors.append("final validation must contain a usable AAC audio stream")
    audio_stream = usable_audio_streams[0] if usable_audio_streams else {}
    audio_stream_duration = _rational_number(audio_stream.get("duration"))

    if raw_format_duration is not None and raw_format_duration > 0:
        if abs(raw_format_duration - expected_duration_seconds) > duration_tolerance_seconds:
            errors.append("raw_probe.format.duration disagrees with timeline")
        if (
            video_stream_duration is not None
            and abs(raw_format_duration - video_stream_duration)
            > duration_tolerance_seconds
        ):
            errors.append("raw_probe.format.duration disagrees with video stream")
        if (
            audio_stream_duration is not None
            and abs(raw_format_duration - audio_stream_duration)
            > duration_tolerance_seconds
        ):
            errors.append("raw_probe.format.duration disagrees with audio stream")

    expected_reported_duration = _finite_number(validation.get("expected_duration_seconds"))
    if expected_reported_duration is None:
        errors.append("final validation expected duration must be finite")
    elif abs(expected_reported_duration - expected_duration_seconds) > 1e-6:
        errors.append("final validation expected duration disagrees with timeline")
    video_duration = _finite_number(validation.get("video_duration_seconds"))
    if video_duration is None:
        errors.append("final validation video duration must be finite")
    elif video_stream_duration is not None and abs(video_duration - video_stream_duration) > 1e-6:
        errors.append("final validation video duration disagrees with video stream")
    audio_duration = _finite_number(validation.get("audio_duration_seconds"))
    if audio_duration is None:
        errors.append("final validation audio duration must be finite")
    elif audio_stream_duration is not None and abs(audio_duration - audio_stream_duration) > 1e-6:
        errors.append("final validation audio duration disagrees with audio stream")

    video_drift = _finite_number(validation.get("video_drift_seconds"))
    if video_duration is not None:
        calculated_video_drift = video_duration - expected_duration_seconds
        if video_drift is None:
            errors.append("final validation video drift must be finite")
        elif abs(video_drift - calculated_video_drift) > 1e-6:
            errors.append("final validation video drift is algebraically inconsistent")
        if abs(calculated_video_drift) > duration_tolerance_seconds:
            errors.append("final validation video drift exceeds tolerance")
    audio_drift = _finite_number(validation.get("audio_drift_seconds"))
    if audio_duration is not None:
        calculated_audio_drift = audio_duration - expected_duration_seconds
        if audio_drift is None:
            errors.append("final validation audio drift must be finite")
        elif abs(audio_drift - calculated_audio_drift) > 1e-6:
            errors.append("final validation audio drift is algebraically inconsistent")
        if abs(calculated_audio_drift) > duration_tolerance_seconds:
            errors.append("final validation audio drift exceeds tolerance")
    audio_video_drift = _finite_number(validation.get("audio_video_drift_seconds"))
    if audio_duration is not None and video_duration is not None:
        calculated_audio_video_drift = audio_duration - video_duration
        if audio_video_drift is None:
            errors.append("final validation audio/video drift must be finite")
        elif abs(audio_video_drift - calculated_audio_video_drift) > 1e-6:
            errors.append("final validation audio/video drift is algebraically inconsistent")
        if abs(calculated_audio_video_drift) > duration_tolerance_seconds:
            errors.append("final validation audio/video drift exceeds tolerance")
    return errors


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _rational_number(value: object) -> float | None:
    """Parse a finite ffprobe-style rational or scalar number."""

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if "/" in text:
        numerator_text, denominator_text = text.split("/", 1)
        try:
            numerator = float(numerator_text)
            denominator = float(denominator_text)
        except (ValueError, OverflowError):
            return None
        if not math.isfinite(numerator) or not math.isfinite(denominator) or denominator == 0:
            return None
        number = numerator / denominator
    else:
        try:
            number = float(text)
        except (ValueError, OverflowError):
            return None
    return number if math.isfinite(number) else None


def _positive_integer(value: object) -> int | None:
    """Return a strictly positive JSON integer."""

    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _positive_probe_integer(value: object) -> int | None:
    """Return a strictly positive integer from an ffprobe scalar."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer() or value <= 0:
            return None
        return int(value)
    if not isinstance(value, str):
        return None
    try:
        number = float(value.strip())
    except (ValueError, OverflowError):
        return None
    if not math.isfinite(number) or not number.is_integer() or number <= 0:
        return None
    return int(number)


def _require_nonempty_mapping(value: object, label: str, reasons: list[str]) -> None:
    if not isinstance(value, dict) or not value:
        reasons.append(f"audiovisual manifest {label} must be a non-empty object")


def _common_manifest_profile(
    project_document: Mapping[str, object],
    manifest: Mapping[str, object],
) -> str | None:
    """Return a profile only for a minimally valid common golden envelope."""

    profile = manifest.get("profile")
    if (
        manifest.get("schema_version") != _GOLDEN_SCHEMA_VERSION
        or manifest.get("version") != 1
        or manifest.get("status") != "accepted"
        or not isinstance(profile, str)
        or profile not in _GOLDEN_PROFILES
        or manifest.get("project_id") != project_document.get("id")
        or manifest.get("title") != project_document.get("title")
    ):
        return None
    return profile


def _golden_lifecycle_allowed(
    project_document: Mapping[str, object],
    manifest: Mapping[str, object],
    profile: str,
) -> bool:
    """Check whether the project status may expose this immutable snapshot."""

    if profile == "visual":
        return project_document.get("status") == ProjectState.accepted.value
    if profile == "audiovisual":
        run_id = manifest.get("run_id")
        return (
            project_document.get("status") in _AUDIOVISUAL_GOLDEN_STATUSES
            and isinstance(run_id, str)
            and _safe_run_id(run_id)
            and project_document.get("accepted_run") == run_id
        )
    return False


def _validate_project_lifecycle(
    project_document: Mapping[str, object],
    manifest: Mapping[str, object],
    profile: str,
    reasons: list[str],
) -> None:
    """Record one profile-specific lifecycle failure without duplicate status errors."""

    status = project_document.get("status")
    if profile == "visual":
        if status != ProjectState.accepted.value:
            reasons.append("project.json status must be accepted")
        return
    if profile == "audiovisual":
        if status not in _AUDIOVISUAL_GOLDEN_STATUSES:
            reasons.append("project.json status is not allowed for an audiovisual golden snapshot")
            return
        run_id = manifest.get("run_id")
        if isinstance(run_id, str) and _safe_run_id(run_id):
            if project_document.get("accepted_run") != run_id:
                reasons.append("audiovisual manifest run_id disagrees with accepted run")


def _validate_project_identity(
    root: Path,
    document: dict[str, object],
    reasons: list[str],
    *,
    require_accepted: bool = True,
) -> None:
    if require_accepted and document.get("status") != "accepted":
        reasons.append("project.json status must be accepted")
    project_id = document.get("id")
    if not isinstance(project_id, str) or _PROJECT_ID.fullmatch(project_id) is None:
        reasons.append("project.json id must use YYYY_slug naming")
    elif project_id != root.name:
        reasons.append("project.json id must match its directory name")


def _validate_manifest_metadata(manifest: dict[str, object], reasons: list[str]) -> None:
    for key in ("code_hash", "plan_hash"):
        value = manifest.get(key)
        if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
            reasons.append(f"golden manifest {key} must be a lowercase SHA-256 hash")
    tolerances = manifest.get("tolerances")
    if not isinstance(tolerances, dict) or not tolerances:
        reasons.append("golden manifest tolerances must be a non-empty object")
    elif any(
        isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0
        for value in tolerances.values()
    ):
        reasons.append("golden manifest tolerances must contain non-negative numbers")


def _validate_common_manifest(
    manifest: dict[str, object],
    project_document: Mapping[str, object],
    reasons: list[str],
) -> list[str] | None:
    """Validate fields shared by the visual and audiovisual golden profiles."""

    if manifest.get("schema_version") != _GOLDEN_SCHEMA_VERSION:
        reasons.append("golden manifest schema_version is unsupported")
    if manifest.get("version") != 1:
        reasons.append("golden manifest version must be 1")
    profile = manifest.get("profile")
    if not isinstance(profile, str) or profile not in _GOLDEN_PROFILES:
        reasons.append("golden manifest profile must be visual or audiovisual")
    if manifest.get("status") != "accepted":
        reasons.append("golden manifest status must be accepted")
    project_id = project_document.get("id")
    if manifest.get("project_id") != project_id:
        reasons.append("golden manifest project_id disagrees with project")
    title = manifest.get("title")
    if not isinstance(title, str) or not title.strip():
        reasons.append("golden manifest title must be non-blank")
    project_title = project_document.get("title")
    if not isinstance(project_title, str) or not project_title.strip():
        reasons.append("project title must be non-blank")
    elif title != project_title:
        reasons.append("golden manifest title disagrees with project")
    capabilities = _string_list(
        manifest.get("capabilities"),
        "golden manifest capabilities",
        reasons,
    )
    if not capabilities:
        reasons.append("golden manifest capabilities must contain at least one proven capability")
    _validate_capabilities(capabilities, reasons, label="golden manifest")
    return capabilities


def _validate_manifest_continuity(
    manifest: dict[str, object],
    scene_ids: set[object],
    reasons: list[str],
) -> None:
    continuity = manifest.get("continuity")
    if not isinstance(continuity, dict):
        reasons.append("golden manifest continuity must be an object")
        return
    boundaries = continuity.get("boundaries")
    if not isinstance(boundaries, list) or not boundaries:
        reasons.append("golden manifest continuity.boundaries must be non-empty")
        return
    for index, boundary in enumerate(boundaries, start=1):
        label = f"continuity boundary {index}"
        if not isinstance(boundary, dict):
            reasons.append(f"{label} must be an object")
            continue
        for key in ("from", "to"):
            if not isinstance(boundary.get(key), str) or not boundary[key].strip():
                reasons.append(f"{label} {key} is required")
            elif boundary[key] not in scene_ids:
                reasons.append(f"{label} {key} must reference a manifest scene")
        recurring = boundary.get("recurring_objects")
        if not isinstance(recurring, list) or not all(
            isinstance(item, str) and item.strip() for item in recurring
        ):
            reasons.append(f"{label} recurring_objects must be a string list")
        elif len(recurring) != len(set(recurring)):
            reasons.append(f"{label} recurring_objects must be unique")
        transition = boundary.get("expected_transition")
        if not isinstance(transition, str) or not transition.strip():
            reasons.append(f"{label} expected_transition is required")
        expected_findings = boundary.get("expected_findings")
        if not isinstance(expected_findings, list):
            reasons.append(f"{label} expected_findings must be a list")
        else:
            for finding in expected_findings:
                if (
                    not isinstance(finding, dict)
                    or not isinstance(finding.get("code"), str)
                    or re.fullmatch(r"[A-Z][A-Z0-9_]*", str(finding.get("code"))) is None
                ):
                    reasons.append(f"{label} expected_findings entries need a code")


def _validate_manifest_theme(
    root: Path,
    manifest: dict[str, object],
    root_document: dict[str, object],
    root_theme: VideoTheme | None,
    reasons: list[str],
) -> None:
    reference = manifest.get("theme")
    if isinstance(reference, str):
        try:
            referenced_document = _read_object(_safe_reference(root.resolve(), Path(reference)))
            referenced_theme = VideoTheme.model_validate(referenced_document)
        except (OSError, ValueError, ValidationError) as exc:
            reasons.append(f"golden manifest theme: {exc}")
            return
        if root_theme is not None and referenced_theme != root_theme:
            reasons.append("golden manifest theme differs from theme.json")
        return
    if isinstance(reference, dict):
        try:
            referenced_theme = VideoTheme.model_validate(reference)
        except (ValueError, ValidationError) as exc:
            reasons.append(f"golden manifest theme: {exc}")
            return
        if root_theme is not None and referenced_theme != root_theme:
            reasons.append("golden manifest theme differs from theme.json")
        return
    del root_document
    reasons.append("golden manifest theme is required")


def _validate_scenes(
    root: Path,
    scenes: list[object],
    declared_capabilities: list[str],
    root_theme: VideoTheme | None,
    reasons: list[str],
) -> list[dict[str, object]]:
    valid_documents: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    for index, raw_scene in enumerate(scenes, start=1):
        label = f"golden scene {index}"
        if not isinstance(raw_scene, dict):
            reasons.append(f"{label} must be an object")
            continue
        scene = {str(key): value for key, value in raw_scene.items()}
        scene_id = scene.get("id")
        if not isinstance(scene_id, str) or not scene_id.strip():
            reasons.append(f"{label} id is required")
        elif scene_id in seen_ids:
            reasons.append(f"{label} id must be unique: {scene_id}")
        else:
            seen_ids.add(scene_id)

        references: dict[str, Path] = {}
        for key in ("plan", "code", "expectations"):
            value = scene.get(key)
            if not isinstance(value, str) or not value.strip():
                reasons.append(f"{label} {key} reference is required")
                continue
            try:
                target = _safe_reference(root.resolve(), Path(value))
            except ValueError as exc:
                reasons.append(f"{label} {key}: {exc}")
                continue
            if not target.is_file():
                reasons.append(f"{label} missing {key}: {value}")
                continue
            references[key] = Path(value)

        plan: ScenePlan | None = None
        if "plan" in references:
            try:
                plan = _read_plan(root / references["plan"])
            except (OSError, ValueError, ValidationError) as exc:
                reasons.append(f"{label} plan: {exc}")
        if plan is not None:
            if isinstance(scene_id, str) and plan.id != scene_id:
                reasons.append(f"{label} id does not match plan id")
            if root_theme is not None and plan.theme != root_theme:
                reasons.append(f"{label} plan theme differs from theme.json")

        code_evidence: _CodeEvidence | None = None
        if "code" in references:
            code_evidence = _validate_code(root / references["code"], label, reasons)
        if "expectations" in references:
            try:
                SceneExpectations.model_validate(_read_object(root / references["expectations"]))
            except (OSError, ValueError, ValidationError) as exc:
                reasons.append(f"{label} expectations: {exc}")

        scene_capabilities = _string_list(
            scene.get("capabilities"), f"{label} capabilities", reasons
        )
        if not scene_capabilities:
            reasons.append(f"{label} must prove at least one capability")
        unknown = set(scene_capabilities) - set(declared_capabilities)
        if unknown:
            reasons.append(
                f"{label} capabilities are not declared by project: {', '.join(sorted(unknown))}"
            )
        _validate_capabilities(scene_capabilities, reasons, label=label)

        _validate_scene_evidence(
            root,
            scene,
            label,
            reasons,
            plan=plan,
            root_theme=root_theme,
            scene_capabilities=scene_capabilities,
            code_evidence=code_evidence,
        )
        if references.keys() >= {"plan", "code", "expectations"}:
            valid_documents.append(
                {
                    **scene,
                    "plan": references["plan"],
                    "code": references["code"],
                    "expectations": references["expectations"],
                }
            )
    return valid_documents


def _validate_scene_evidence(
    root: Path,
    scene: dict[str, object],
    label: str,
    reasons: list[str],
    *,
    plan: ScenePlan | None,
    root_theme: VideoTheme | None,
    scene_capabilities: list[str],
    code_evidence: _CodeEvidence | None,
) -> None:
    expected_facts = scene.get("expected_facts")
    if not isinstance(expected_facts, dict):
        reasons.append(f"{label} expected_facts must be an object")
    else:
        for key in _REQUIRED_FACT_LISTS:
            if not isinstance(expected_facts.get(key), list):
                reasons.append(f"{label} expected_facts.{key} must be a list")
            elif any(
                not isinstance(item, (str, dict)) or (isinstance(item, str) and not item.strip())
                for item in expected_facts[key]
            ):
                reasons.append(f"{label} expected_facts.{key} must contain named facts or objects")
        _validate_expected_facts(
            expected_facts,
            label,
            reasons,
            plan=plan,
            code_evidence=code_evidence,
        )

    semantic = scene.get("semantic_expectations")
    if not isinstance(semantic, dict) or not semantic:
        reasons.append(f"{label} semantic_expectations must be a non-empty object")
    elif plan is not None:
        _validate_semantic_expectations(semantic, plan, label, reasons)

    if plan is not None:
        missing_capabilities = set(plan.capabilities) - set(scene_capabilities)
        if missing_capabilities:
            reasons.append(
                f"{label} capabilities omit plan capabilities: "
                f"{', '.join(sorted(missing_capabilities))}"
            )

    findings = scene.get("expected_findings")
    if not isinstance(findings, list):
        reasons.append(f"{label} expected_findings must be a list")
    else:
        for finding in findings:
            if (
                not isinstance(finding, dict)
                or not isinstance(finding.get("code"), str)
                or re.fullmatch(r"[A-Z][A-Z0-9_]*", str(finding.get("code"))) is None
            ):
                reasons.append(f"{label} expected_findings entries need a code")

    dimensions = scene.get("dimensions")
    if not isinstance(dimensions, dict):
        reasons.append(f"{label} dimensions must be an object")
    elif any(
        isinstance(dimensions.get(key), bool)
        or not isinstance(dimensions.get(key), int)
        or dimensions.get(key, 0) <= 0
        for key in ("width", "height")
    ):
        reasons.append(f"{label} dimensions need positive integer width and height")
    elif root_theme is not None and (
        dimensions["width"] != root_theme.resolution[0]
        or dimensions["height"] != root_theme.resolution[1]
    ):
        reasons.append(f"{label} dimensions must match theme resolution")

    duration = scene.get("duration_seconds")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration <= 0:
        reasons.append(f"{label} duration_seconds must be positive")
    elif plan is not None and abs(float(duration) - plan.duration_seconds) > 1e-6:
        reasons.append(f"{label} duration_seconds must match its ScenePlan")

    keyframes = scene.get("keyframes")
    if not isinstance(keyframes, list) or not keyframes:
        reasons.append(f"{label} keyframes must contain at least one small frame")
        return
    for frame_index, reference in enumerate(keyframes, start=1):
        if not isinstance(reference, str) or not reference.strip():
            reasons.append(f"{label} keyframe {frame_index} reference is required")
            continue
        try:
            frame_path = _safe_reference(root.resolve(), Path(reference))
        except ValueError as exc:
            reasons.append(f"{label} keyframe {frame_index}: {exc}")
            continue
        if not frame_path.is_file():
            reasons.append(f"{label} missing keyframe {frame_index}: {reference}")
        elif frame_path.stat().st_size == 0 or frame_path.stat().st_size > 5 * 1024 * 1024:
            reasons.append(f"{label} keyframe {frame_index} must be non-empty and <= 5 MiB")
        else:
            _validate_keyframe_payload(frame_path, label, frame_index, reasons)


def _validate_keyframe_payload(
    path: Path,
    label: str,
    frame_index: int,
    reasons: list[str],
) -> None:
    """Reject evidence files that only have a plausible image extension."""

    try:
        prefix = path.read_bytes()[:4096]
    except OSError as exc:
        reasons.append(f"{label} keyframe {frame_index} cannot be read: {exc}")
        return
    suffix = path.suffix.lower()
    valid = (
        prefix.lstrip().lower().startswith(b"<svg")
        if suffix == ".svg"
        else prefix.startswith(b"\x89PNG\r\n\x1a\n")
        if suffix == ".png"
        else prefix.startswith(b"\xff\xd8")
        if suffix in {".jpg", ".jpeg"}
        else prefix.startswith(b"RIFF") and b"WEBP" in prefix[:16]
        if suffix == ".webp"
        else False
    )
    if not valid:
        reasons.append(f"{label} keyframe {frame_index} is not a supported image payload")


def _validate_expected_facts(
    expected_facts: dict[str, object],
    label: str,
    reasons: list[str],
    *,
    plan: ScenePlan | None,
    code_evidence: _CodeEvidence | None,
) -> None:
    """Cross-check manifest facts against plan IDs and source literals."""

    if plan is None:
        return
    plan_ids = {item.id for item in plan.objects}
    required_ids = {item.id for item in plan.objects if item.required}
    registered = set(code_evidence.registrations) if code_evidence is not None else set()
    final_ids = _fact_ids(expected_facts.get("final_state"))
    initial_ids = _fact_ids(expected_facts.get("initial_state"))
    for state_name, ids in (("initial_state", initial_ids), ("final_state", final_ids)):
        unknown = ids - plan_ids
        if unknown:
            reasons.append(
                f"{label} expected_facts.{state_name} has unknown IDs: {', '.join(sorted(unknown))}"
            )
        unregistered = ids - registered
        if unregistered and code_evidence is not None:
            reasons.append(
                f"{label} expected_facts.{state_name} is not registered in scene code: "
                f"{', '.join(sorted(unregistered))}"
            )
    missing_required = required_ids - final_ids
    if missing_required:
        reasons.append(
            f"{label} expected_facts.final_state omits required IDs: "
            f"{', '.join(sorted(missing_required))}"
        )
    checkpoint_ids = _fact_names(expected_facts.get("checkpoints"))
    animation_names = _fact_names(expected_facts.get("animations"))
    if code_evidence is not None:
        missing_checkpoints = set(checkpoint_ids) - set(code_evidence.checkpoints)
        if missing_checkpoints:
            reasons.append(
                f"{label} expected checkpoints are absent from scene code: "
                f"{', '.join(sorted(missing_checkpoints))}"
            )
        missing_animations = set(animation_names) - set(code_evidence.animations)
        if missing_animations:
            reasons.append(
                f"{label} expected animations are absent from scene code: "
                f"{', '.join(sorted(missing_animations))}"
            )
        if list(code_evidence.checkpoints) != checkpoint_ids:
            reasons.append(f"{label} expected checkpoints do not match scene code literals")
        if list(code_evidence.animations) != animation_names:
            reasons.append(f"{label} expected animations do not match scene code calls")
        plan_beat_ids = {beat.id for beat in plan.beats if beat.id is not None}
        missing_beat_ids = plan_beat_ids - set(code_evidence.beat_ids)
        if missing_beat_ids:
            reasons.append(
                f"{label} plan beats are not attached to code checkpoints: "
                f"{', '.join(sorted(missing_beat_ids))}"
            )


def _validate_semantic_expectations(
    semantic: dict[str, object],
    plan: ScenePlan,
    label: str,
    reasons: list[str],
) -> None:
    """Ensure semantic expectations name the same authored objects and roles."""

    plan_objects = {item.id: item for item in plan.objects}
    required_value = semantic.get("required_objects")
    required = (
        {item for item in required_value if isinstance(item, str)}
        if isinstance(required_value, list)
        else set()
    )
    if not isinstance(required_value, list) or not required:
        reasons.append(f"{label} semantic_expectations.required_objects must be non-empty")
    unknown_required = required - set(plan_objects)
    if unknown_required:
        reasons.append(
            f"{label} semantic expectations have unknown IDs: {', '.join(sorted(unknown_required))}"
        )
    missing_required = {item.id for item in plan.objects if item.required} - required
    if missing_required:
        reasons.append(
            f"{label} semantic expectations omit required IDs: "
            f"{', '.join(sorted(missing_required))}"
        )

    regions = semantic.get("required_regions")
    if regions is not None and not isinstance(regions, dict):
        reasons.append(f"{label} semantic required_regions must be an object")
    elif isinstance(regions, dict):
        for object_id, region in regions.items():
            declared = plan_objects.get(str(object_id))
            if declared is None:
                reasons.append(f"{label} semantic region names unknown object: {object_id}")
            elif not isinstance(region, str) or region not in plan.theme.regions:
                reasons.append(f"{label} semantic region is invalid: {region}")
            elif declared.region is not None and region != declared.region:
                reasons.append(f"{label} semantic region disagrees with plan for {object_id}")

    colours = semantic.get("required_color_roles")
    if colours is not None and not isinstance(colours, dict):
        reasons.append(f"{label} semantic required_color_roles must be an object")
    elif isinstance(colours, dict):
        for object_id, role in colours.items():
            declared = plan_objects.get(str(object_id))
            if declared is None:
                reasons.append(f"{label} semantic colour names unknown object: {object_id}")
            elif not isinstance(role, str) or role not in plan.theme.palette:
                reasons.append(f"{label} semantic colour role is invalid: {role}")
            elif declared.color_role is not None and role != declared.color_role:
                reasons.append(f"{label} semantic colour disagrees with plan for {object_id}")


def _fact_names(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        item if isinstance(item, str) else str(item["name"])
        for item in value
        if isinstance(item, str) or isinstance(item, dict) and isinstance(item.get("name"), str)
    ]


def _fact_ids(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    ids: set[str] = set()
    for item in value:
        if isinstance(item, str) and item.strip():
            ids.add(item)
        elif isinstance(item, dict) and isinstance(item.get("id"), str):
            ids.add(str(item["id"]))
    return ids


def _validate_code(path: Path, label: str, reasons: list[str]) -> _CodeEvidence | None:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, SyntaxError, UnicodeError) as exc:
        reasons.append(f"{label} code is not valid reusable Python: {exc}")
        return None
    if not source.strip():
        reasons.append(f"{label} code must not be empty")
    if "class " not in source or "Scene" not in source:
        reasons.append(f"{label} code must define a Manim scene class")
    registrations: list[str] = []
    checkpoints: list[str] = []
    beat_ids: list[str] = []
    animations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        method = node.func.attr
        if method == "register_visual":
            value = _literal_string(node.args[1]) if len(node.args) > 1 else None
            if value is None:
                value = _keyword_string(node, "object_id")
            if value is not None:
                registrations.append(value)
        elif method == "checkpoint":
            checkpoint = _literal_string(node.args[0]) if node.args else None
            if checkpoint is not None:
                checkpoints.append(checkpoint)
            beat_id = _keyword_string(node, "beat_id")
            if beat_id is not None:
                beat_ids.append(beat_id)
        elif method == "play":
            animations.extend(_animation_names(node.args))
    return _CodeEvidence(
        registrations=tuple(dict.fromkeys(registrations)),
        checkpoints=tuple(dict.fromkeys(checkpoints)),
        beat_ids=tuple(dict.fromkeys(beat_ids)),
        animations=tuple(animations),
    )


def _literal_string(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _keyword_string(node: ast.Call, name: str) -> str | None:
    for keyword in node.keywords:
        if keyword.arg == name:
            return _literal_string(keyword.value)
    return None


def _animation_names(nodes: list[ast.AST]) -> list[str]:
    names: list[str] = []
    for root in nodes:
        for node in ast.walk(root):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                names.append(node.func.id)
            elif isinstance(node.func, ast.Attribute) and node.func.attr in {
                "shift",
                "move_to",
                "rotate",
                "scale",
            }:
                names.append(node.func.attr)
    return names


def _validate_capabilities(
    capability_ids: list[str],
    reasons: list[str],
    *,
    label: str = "project",
) -> None:
    try:
        default_capability_registry().require(capability_ids)
    except ValueError as exc:
        reasons.append(f"{label} capabilities: {exc}")


def _string_list(value: object, label: str, reasons: list[str]) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        reasons.append(f"{label} must be a list of non-blank strings")
        return []
    values = list(value)
    if len(values) != len(set(values)):
        reasons.append(f"{label} must not contain duplicates")
    return values


def _read_plan(path: Path) -> ScenePlan:
    document = _read_object(path)
    version = document.pop("schema_version", None)
    if version is not None and version != "visual.scene-plan/1":
        raise ValueError("unsupported scene-plan schema version")
    return ScenePlan.model_validate(document)


def _safe_reference(root: Path, relative: Path) -> Path:
    if relative.is_absolute():
        raise ValueError("references must be relative to the project")
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("reference escapes the project") from exc
    return target


def _read_object(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise OSError(f"missing file: {path}")
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON document must be an object")
    return {str(key): item for key, item in value.items()}


__all__ = [
    "accept_project",
    "GoldenProject",
    "GoldenValidation",
    "discover_golden_projects",
    "hash_file",
    "hash_references",
    "read_golden_project",
    "validate_all_golden_projects",
    "validate_golden_project",
]
