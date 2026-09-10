"""Canonical application service for the loopback Web UI."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
import threading
import uuid
from collections.abc import Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Protocol

from pydantic import TypeAdapter

from video_pipeline.golden import accept_project
from video_pipeline.project import (
    AudioMediaFacts,
    AudioProbe,
    ProjectInputValidationError,
    SilenceDetector,
    _project_package_hashes,
    _project_scene_order,
    confirm_project_timeline,
    initialize_project,
    inspect_project,
    load_project,
)
from video_pipeline.revisions import RevisionManifest, RevisionStore, WorkingDraft
from video_pipeline.timeline import parse_explicit_timeline, parse_heading_sections
from video_pipeline.video import ProjectPipelineEvent, VideoPipeline, VideoResult
from video_pipeline.web.limits import MAX_CORRECTION_CHARS, MAX_SCRIPT_CHARS
from video_pipeline.web_errors import (
    NotFoundError,
    QueueFullError,
    RequestValidationError,
    StateConflictError,
)

_PUBLIC_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_PROJECT_ID = re.compile(r"^[0-9]{4}_[a-z0-9]+(?:[_-][a-z0-9]+)*$")
_REVISION_ID = re.compile(r"^v[0-9]{3,}$")
_ABSOLUTE_PATH = re.compile(r"(?<![A-Za-z0-9_:])/(?:[^\s/]+/)*[^\s/]+")
_OBJECT_ADAPTER = TypeAdapter(dict[str, object])


@dataclass(frozen=True, slots=True)
class ServiceLimits:
    """Input and queue limits enforced before persistent work starts."""

    max_queue: int = 32
    max_script_chars: int = MAX_SCRIPT_CHARS
    max_scenes: int = 20
    max_correction_chars: int = MAX_CORRECTION_CHARS


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    """Path-free public state for one queued render job."""

    job_id: str
    project_id: str
    run_id: str
    state: str
    stage: str
    revision_id: str | None = None
    retry_of: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class _WorkItem:
    snapshot: JobSnapshot
    project_json: Path
    max_attempts: int
    scene_id: str | None
    base_run_id: str | None
    correction: str | None
    base_package_hashes: dict[str, str]


@dataclass(frozen=True, slots=True)
class _ProjectLocalAudioProbe(AudioProbe):
    delegate: AudioProbe
    project_root: Path

    def __call__(self, staged_audio: Path) -> AudioMediaFacts | Mapping[str, object]:
        root_created = False
        audio_created = False
        probe_identity: os.stat_result | None = None
        probe_path = self.project_root / "audio" / staged_audio.name
        try:
            self.project_root.mkdir()
            root_created = True
        except FileExistsError as exc:
            raise ValueError("project id is already being initialized") from exc
        audio_root = self.project_root / "audio"
        try:
            audio_root.mkdir()
            audio_created = True
            descriptor = os.open(
                probe_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            probe_identity = os.fstat(descriptor)
            with os.fdopen(descriptor, "wb") as probe_file:
                probe_file.write(staged_audio.read_bytes())
            return self.delegate(probe_path)
        finally:
            try:
                current_identity = probe_path.lstat()
            except OSError:
                current_identity = None
            if (
                current_identity is not None
                and probe_identity is not None
                and current_identity.st_ino == probe_identity.st_ino
                and current_identity.st_dev == probe_identity.st_dev
                and stat.S_ISREG(current_identity.st_mode)
            ):
                probe_path.unlink()
            owned_directories = (
                (audio_root, audio_created),
                (self.project_root, root_created),
            )
            for owned_directory, was_created in owned_directories:
                if not was_created:
                    continue
                try:
                    owned_directory.rmdir()
                except OSError:
                    pass


class _Pipeline(Protocol):
    def render(
        self,
        project_path: str | Path,
        *,
        max_attempts: int = 3,
        scene: str | None = None,
        base_run_id: str | None = None,
        correction: str | None = None,
        on_progress: Callable[[ProjectPipelineEvent], None] | None = None,
    ) -> VideoResult: ...


PipelineFactory = Callable[[str], _Pipeline]


class WebService:
    """Coordinate canonical projects, serialized renders, and UI revisions."""

    def __init__(
        self,
        projects_root: str | Path,
        audio_root: str | Path,
        *,
        audio_probe: AudioProbe | None = None,
        silence_detector: SilenceDetector | None = None,
        pipeline_factory: PipelineFactory | None = None,
        project_id_factory: Callable[[], str] | None = None,
        job_id_factory: Callable[[], str] | None = None,
        run_id_factory: Callable[[], str] | None = None,
        limits: ServiceLimits = ServiceLimits(),  # noqa: B008
    ) -> None:
        self.projects_root = Path(projects_root).resolve()
        self.audio_root = Path(audio_root).resolve()
        self.audio_probe = audio_probe
        self.silence_detector = silence_detector
        self.pipeline_factory = pipeline_factory
        self.project_id_factory = project_id_factory or self._new_project_id
        self.job_id_factory = job_id_factory or (lambda: f"job-{uuid.uuid4().hex}")
        self.run_id_factory = run_id_factory or (lambda: f"run-{uuid.uuid4().hex}")
        self.limits = limits
        self._lock = threading.RLock()
        self._jobs: dict[str, JobSnapshot] = {}
        self._futures: dict[str, Future[None]] = {}
        self._closed = False
        self._recover_interrupted_jobs()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="video-web")

    def __enter__(self) -> WebService:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        """Finish accepted work and release the single render worker."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=False)

    def list_audio(self) -> list[dict[str, object]]:
        """List catalog audio through opaque, stable public identifiers."""

        return [
            {"id": asset_id, "label": path.stem.replace("-", " ").title()}
            for asset_id, path in self._audio_catalog().items()
        ]

    def list_projects(self) -> list[dict[str, object]]:
        """List durable projects through a path-free public projection."""

        projects: list[dict[str, object]] = []
        for project_root in self._project_roots():
            project_json = project_root / "project.json"
            if not _is_regular_file(project_json):
                continue
            try:
                project = load_project(project_json)
                inspection = inspect_project(project_json)
                timeline = inspection.get("timeline")
                timeline_status = (
                    timeline.get("status")
                    if isinstance(timeline, dict)
                    else project.status.value
                )
                current_revision_id = self._current_revision_id(project_root)
            except (OSError, TypeError, ValueError):
                continue
            projects.append(
                {
                    "id": project.id,
                    "project_id": project.id,
                    "title": self._public_text(project.title, project_id=project.id),
                    "status": project.status.value,
                    "timeline_status": timeline_status,
                    "current_run": project.current_run,
                    "current_revision_id": current_revision_id,
                    "accepted_run": project.accepted_run,
                }
            )
        return projects

    def list_jobs(self) -> list[dict[str, object]]:
        """List live and durable jobs without exposing runtime filesystem paths."""

        with self._lock:
            live_jobs = dict(self._jobs)
        records: dict[tuple[str, str], dict[str, object]] = {}
        for project_root in self._project_roots():
            project_id = project_root.name
            store = self._read_only_store(project_root)
            if store is None:
                continue
            drafts = self._working_drafts(project_root)
            try:
                revisions = store.list_revisions()
            except (OSError, TypeError, ValueError):
                revisions = []
            revisions_by_job = {revision.job_id: revision for revision in revisions}
            for job_id, draft in drafts.items():
                revision = revisions_by_job.get(job_id)
                records[(project_id, job_id)] = self._job_projection(
                    project_id=project_id,
                    draft=draft,
                    revision=revision,
                    snapshot=live_jobs.get(job_id),
                )
            for job_id, revision in revisions_by_job.items():
                if (project_id, job_id) in records:
                    continue
                records[(project_id, job_id)] = self._job_projection(
                    project_id=project_id,
                    draft=None,
                    revision=revision,
                    snapshot=live_jobs.get(job_id),
                )
        for snapshot in live_jobs.values():
            key = (snapshot.project_id, snapshot.job_id)
            if key in records:
                continue
            records[key] = self._job_projection(
                project_id=snapshot.project_id,
                draft=None,
                revision=None,
                snapshot=snapshot,
            )
        return [
            records[key]
            for key in sorted(records, key=_job_sort_key)
        ]

    def create_project(
        self,
        *,
        title: str,
        script: str,
        audio_asset_id: str,
    ) -> dict[str, object]:
        """Create a canonical candidate project from validated Web input."""

        if not isinstance(title, str) or not title.strip():
            raise RequestValidationError("title must not be blank")
        if not isinstance(script, str) or not script.strip():
            raise RequestValidationError("script must not be blank")
        if len(script) > self.limits.max_script_chars:
            raise RequestValidationError("script exceeds the 50000 character limit")
        # Count the same sections that ``initialize_project`` materializes.
        # A heading-only count misses plain-text scripts, where blank-line
        # separated blocks become canonical scenes.
        try:
            parsed_scenes = parse_explicit_timeline(script)
            if parsed_scenes is None:
                parsed_scenes = parse_heading_sections(script)
        except ValueError as exc:
            raise RequestValidationError("invalid project script") from exc
        scene_count = len(parsed_scenes or ())
        if scene_count > self.limits.max_scenes:
            raise RequestValidationError(
                f"script may contain at most {self.limits.max_scenes} scenes"
            )
        self._validate_public_id(audio_asset_id, "audio asset")
        audio = self._audio_catalog().get(audio_asset_id)
        if audio is None:
            raise NotFoundError("unknown audio asset")
        project_id = self.project_id_factory()
        if not isinstance(project_id, str) or _PROJECT_ID.fullmatch(project_id) is None:
            raise RequestValidationError("project id must be a safe YYYY_slug identifier")
        project_root = self.projects_root / project_id
        self.projects_root.mkdir(parents=True, exist_ok=True)

        selected_probe: AudioProbe | None = self.audio_probe
        if self.audio_probe is not None:
            selected_probe = _ProjectLocalAudioProbe(self.audio_probe, project_root)

        with tempfile.TemporaryDirectory(prefix="video-web-script-") as directory:
            script_path = Path(directory) / "script.md"
            script_path.write_text(script, encoding="utf-8")
            try:
                initialize_project(
                    project_root,
                    title=title.strip(),
                    script=script_path,
                    audio=audio,
                    audio_probe=selected_probe,
                    silence_detector=self.silence_detector,
                )
            except ProjectInputValidationError as exc:
                raise RequestValidationError("invalid project script") from exc
        return inspect_project(project_root / "project.json")

    def inspect(self, project_id: str) -> dict[str, object]:
        """Inspect one canonical project without exposing a host path."""

        project_json = self._project_json(project_id)
        projection = inspect_project(project_json)
        if (project_json.parent / "ui" / "revisions").is_dir():
            ui = self._ui_projection(project_id, project_json.parent)
            media = ui["media"]
            media_scenes = media.get("scenes") if isinstance(media, dict) else None
            project_scenes = projection.get("scenes")
            revision_store = RevisionStore(project_json.parent, create=False)
            current_revision = revision_store.current_revision()
            current_is_failure = (
                current_revision is not None and current_revision.status != "success"
            )
            has_failure_revision = any(
                revision.status != "success"
                for revision in revision_store.list_revisions()
            )
            media_is_complete = (
                isinstance(media, dict)
                and media.get("final_asset_id") is not None
                and isinstance(media_scenes, list)
                and isinstance(project_scenes, list)
                and len(media_scenes) == len(project_scenes)
                and len(media_scenes) > 0
            )
            if current_is_failure or has_failure_revision or media_is_complete:
                projection["ui"] = ui
        return projection

    def confirm_timeline(self, project_id: str) -> dict[str, object]:
        """Confirm the canonical timeline and return the updated inspection."""

        project_json = self._project_json(project_id)
        confirm_project_timeline(project_json)
        return inspect_project(project_json)

    def enqueue_render(
        self,
        project_id: str,
        *,
        max_attempts: int = 3,
        retry_of: str | None = None,
    ) -> JobSnapshot:
        """Queue a full canonical render, optionally retrying an interruption."""

        if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts < 1:
            raise RequestValidationError("max_attempts must be a positive integer")
        return self._enqueue(
            project_id,
            max_attempts=max_attempts,
            scene_id=None,
            base_run_id=None,
            correction=None,
            retry_of=retry_of,
        )

    def enqueue_regeneration(
        self,
        project_id: str,
        *,
        base_run_id: str,
        scene_id: str,
        correction: str,
        retry_of: str | None = None,
    ) -> JobSnapshot:
        """Queue a selective regeneration from an immutable ready run."""

        if not isinstance(correction, str) or not correction.strip():
            raise RequestValidationError("correction must not be blank")
        if len(correction) > self.limits.max_correction_chars:
            raise RequestValidationError("correction exceeds the 5000 character limit")
        self._validate_public_id(base_run_id, "base run")
        self._validate_public_id(scene_id, "scene")
        return self._enqueue(
            project_id,
            max_attempts=3,
            scene_id=scene_id,
            base_run_id=base_run_id,
            correction=correction,
            retry_of=retry_of,
        )

    def get_job(self, job_id: str) -> JobSnapshot:
        """Return the latest in-memory or recovered state for a public job."""

        self._validate_public_id(job_id, "job")
        with self._lock:
            snapshot = self._jobs.get(job_id)
        if snapshot is None:
            raise NotFoundError("unknown job")
        return snapshot

    def retry_job(self, job_id: str) -> JobSnapshot:
        """Retry an interrupted or failed job from its durable working draft."""

        self._validate_public_id(job_id, "job")
        source_project_id, source = self._find_retry_source(job_id)
        if source.scene_id is None and source.base_run_id is None:
            return self.enqueue_render(source_project_id, retry_of=job_id)
        if (
            source.scene_id is None
            or source.base_run_id is None
            or source.correction is None
        ):
            raise RequestValidationError("selective retry draft is incomplete")
        return self.enqueue_regeneration(
            source_project_id,
            base_run_id=source.base_run_id,
            scene_id=source.scene_id,
            correction=source.correction,
            retry_of=job_id,
        )

    def wait_job(self, job_id: str, timeout: float | None = None) -> JobSnapshot:
        """Wait for a live job to finish and return its terminal snapshot."""

        self._validate_public_id(job_id, "job")
        with self._lock:
            future = self._futures.get(job_id)
        if future is not None:
            future.result(timeout=timeout)
        return self.get_job(job_id)

    def checkout_revision(self, project_id: str, revision_id: str) -> RevisionManifest:
        """Move only a project's UI revision pointer."""

        return RevisionStore(self._project_root(project_id)).checkout(revision_id)

    def accept_run(self, project_id: str, run_id: str) -> dict[str, object]:
        """Promote a ready canonical run without changing the UI checkout."""

        self._validate_public_id(run_id, "run")
        with self._lock:
            if any(
                job.project_id == project_id and job.state in {"queued", "running"}
                for job in self._jobs.values()
            ):
                raise StateConflictError(
                    "cannot accept while this project has an active job"
                )
            project = accept_project(self._project_json(project_id), run_id)
        public_project = _OBJECT_ADAPTER.validate_json(project.model_dump_json())
        return {"project": public_project, "run_id": run_id}

    def resolve_asset(self, asset_id: str) -> tuple[Path, Path]:
        """Resolve an opaque asset to an explicit trusted root and candidate."""

        self._validate_public_id(asset_id, "asset")
        audio = self._audio_catalog().get(asset_id)
        if audio is not None:
            return self.audio_root, audio
        for candidate_id, root, candidate in self._media_candidates():
            if candidate_id == asset_id:
                return root, candidate
        raise NotFoundError("unknown asset")

    def _ui_projection(self, project_id: str, project_root: Path) -> dict[str, object]:
        store = RevisionStore(project_root, create=False)
        revisions = store.list_revisions()
        current = store.current_revision()
        media: dict[str, object] = {"final_asset_id": None, "scenes": []}
        if current is not None and current.status == "success":
            project = load_project(project_root / "project.json")
            final = self._media_candidate(
                project_root,
                project_id=project_id,
                revision_id=current.revision_id,
                run_id=current.run_id,
                kind="final",
                scene_id=None,
                scene_path=None,
            )
            if final is not None:
                media["final_asset_id"] = final[0]
            scene_media: list[dict[str, object]] = []
            for scene in sorted(project.scenes, key=_project_scene_order):
                normalized = self._media_candidate(
                    project_root,
                    project_id=project_id,
                    revision_id=current.revision_id,
                    run_id=current.run_id,
                    kind="normalized",
                    scene_id=scene.id,
                    scene_path=scene.path,
                )
                if normalized is not None:
                    scene_media.append(
                        {
                            "scene_id": scene.id,
                            "normalized_asset_id": normalized[0],
                        }
                    )
            media["scenes"] = scene_media
        public_revisions: list[dict[str, object]] = []
        for revision in revisions:
            document = revision.to_document()
            document["correction"] = self._public_text(
                revision.correction,
                project_id=project_id,
            )
            document["messages"] = [
                self._public_text(message, project_id=project_id)
                for message in revision.messages
            ]
            public_revisions.append(document)
        return {
            "current_revision_id": (
                current.revision_id if current is not None else None
            ),
            "revisions": public_revisions,
            "media": media,
        }

    def _media_candidates(self) -> list[tuple[str, Path, Path]]:
        candidates: list[tuple[str, Path, Path]] = []
        if not _is_real_directory(self.projects_root):
            return candidates
        for project_root in sorted(self.projects_root.iterdir()):
            if (
                _PROJECT_ID.fullmatch(project_root.name) is None
                or not _is_real_directory(project_root)
            ):
                continue
            try:
                project_root.resolve(strict=True).relative_to(self.projects_root)
                project = load_project(project_root / "project.json")
                revisions = RevisionStore(project_root, create=False).list_revisions()
            except (OSError, TypeError, ValueError):
                continue
            for revision in revisions:
                if revision.project_id != project.id or revision.status != "success":
                    continue
                final = self._media_candidate(
                    project_root,
                    project_id=project.id,
                    revision_id=revision.revision_id,
                    run_id=revision.run_id,
                    kind="final",
                    scene_id=None,
                    scene_path=None,
                )
                if final is not None:
                    candidates.append(final)
                for scene in project.scenes:
                    normalized = self._media_candidate(
                        project_root,
                        project_id=project.id,
                        revision_id=revision.revision_id,
                        run_id=revision.run_id,
                        kind="normalized",
                        scene_id=scene.id,
                        scene_path=scene.path,
                    )
                    if normalized is not None:
                        candidates.append(normalized)
        return candidates

    @staticmethod
    def _media_candidate(
        project_root: Path,
        *,
        project_id: str,
        revision_id: str,
        run_id: str,
        kind: str,
        scene_id: str | None,
        scene_path: str | None,
    ) -> tuple[str, Path, Path] | None:
        if (
            _PROJECT_ID.fullmatch(project_id) is None
            or _REVISION_ID.fullmatch(revision_id) is None
            or _PUBLIC_ID.fullmatch(run_id) is None
            or (scene_id is not None and _PUBLIC_ID.fullmatch(scene_id) is None)
        ):
            return None
        artifacts_root = project_root / "artifacts"
        run_root = artifacts_root / run_id
        if not _is_real_directory(artifacts_root) or not _is_real_directory(run_root):
            return None
        if kind == "final" and scene_id is None:
            candidate = run_root / "final.mp4"
        elif kind == "normalized" and scene_id is not None and scene_path is not None:
            relative_scene = Path(scene_path)
            if (
                relative_scene.is_absolute()
                or ".." in relative_scene.parts
                or relative_scene.parts[:1] != ("scenes",)
            ):
                return None
            scene_root = run_root / relative_scene
            if not _is_real_directory(scene_root):
                return None
            candidate = scene_root / "normalized.mp4"
        else:
            return None
        if not _is_regular_file(candidate):
            return None
        try:
            resolved_project = project_root.resolve(strict=True)
            resolved_artifacts = artifacts_root.resolve(strict=True)
            resolved_run = run_root.resolve(strict=True)
            resolved_candidate = candidate.resolve(strict=True)
            resolved_artifacts.relative_to(resolved_project)
            resolved_run.relative_to(resolved_artifacts)
            resolved_candidate.relative_to(resolved_run)
        except (OSError, ValueError):
            return None
        asset_id = _media_asset_id(
            project_id,
            revision_id,
            run_id,
            kind,
            scene_id,
        )
        return asset_id, resolved_run, resolved_candidate

    def _enqueue(
        self,
        project_id: str,
        *,
        max_attempts: int,
        scene_id: str | None,
        base_run_id: str | None,
        correction: str | None,
        retry_of: str | None,
    ) -> JobSnapshot:
        project_json = self._project_json(project_id)
        required_state = "ready" if scene_id is not None else "timeline_confirmed"
        if retry_of is not None:
            self._validate_public_id(retry_of, "retry job")
        with self._lock:
            if self._closed:
                raise StateConflictError("service is closed")
            project = load_project(project_json)
            previous: JobSnapshot | None = None
            source: WorkingDraft | None = None
            reuse_run = False
            if retry_of is None:
                if scene_id is None and project.status.value != required_state:
                    raise StateConflictError(
                        "timeline must be confirmed before rendering"
                    )
                if scene_id is not None and project.status.value not in {
                    "ready",
                    "failed",
                    "accepted",
                }:
                    raise StateConflictError(
                        "regeneration requires a ready base run"
                    )
            else:
                previous = self._jobs.get(retry_of)
                source = self._load_retry_source(project_json.parent, retry_of)
                if (
                    source.project_id != project_id
                    or source.job_id != retry_of
                    or source.status not in {"interrupted", "failure"}
                ):
                    raise RequestValidationError("retry source identity is inconsistent")
                if previous is not None and (
                    previous.project_id != project_id
                    or previous.run_id != source.run_id
                    or previous.state not in {"interrupted", "failure"}
                ):
                    raise RequestValidationError("retry source identity is inconsistent")
                if scene_id is None:
                    if (
                        source.correction is not None
                        or source.scene_id is not None
                        or source.base_run_id is not None
                    ):
                        raise RequestValidationError(
                            "full render retry cannot replace regeneration"
                        )
                    # A process can stop after VideoPipeline has opened its
                    # canonical run and switched project.json to rendering.
                    # Continue that run so its durable evidence/current_scene
                    # can be validated and resumed instead of allocating a
                    # second run that the canonical pipeline cannot resume.
                    current_run_path = (
                        project_json.parent / "artifacts" / source.run_id
                    )
                    run_document = current_run_path / "run.json"
                    if project.status.value in {"rendering", "failed"} and (
                        project.current_run == source.run_id
                    ):
                        if not _is_real_directory(current_run_path) or not _is_regular_file(
                            run_document
                        ):
                            raise RequestValidationError("interrupted run evidence is missing")
                        reuse_run = True
                    elif project.status.value != "timeline_confirmed":
                        raise StateConflictError(
                            "timeline must be confirmed before rendering"
                        )
                else:
                    if (
                        source.correction != correction
                        or source.scene_id != scene_id
                        or source.base_run_id != base_run_id
                    ):
                        raise RequestValidationError(
                            "regeneration retry inputs are inconsistent"
                        )
                    if project.status.value not in {
                        "ready",
                        "rendering",
                        "failed",
                        "accepted",
                    }:
                        raise StateConflictError(
                            "regeneration requires a ready base run"
                        )
            if scene_id is not None and scene_id not in {
                project_scene.id for project_scene in project.scenes
            }:
                raise NotFoundError("unknown project scene")
            if any(
                job.project_id == project_id and job.state in {"queued", "running"}
                for job in self._jobs.values()
            ):
                raise StateConflictError("project already has an active job")
            outstanding = sum(
                job.state in {"queued", "running"} for job in self._jobs.values()
            )
            if outstanding >= self.limits.max_queue:
                raise QueueFullError("render queue is full at capacity 32")
            job_id = self.job_id_factory()
            self._validate_public_id(job_id, "job")
            if job_id in self._jobs:
                raise StateConflictError("duplicate job id")
            if retry_of is None:
                run_id = self.run_id_factory()
                self._validate_public_id(run_id, "run")
                self._validate_available_run_id(project_json.parent, run_id)
                base_hashes = _project_package_hashes(project_json.parent, project)
                store = RevisionStore(project_json.parent)
                store.start_working(
                    project_id=project_id,
                    job_id=job_id,
                    run_id=run_id,
                    status="queued",
                    base_package_hashes=base_hashes,
                    correction=correction,
                    asset_ids=["audio-narration"],
                    scene_id=scene_id,
                    base_run_id=base_run_id,
                )
            else:
                assert source is not None
                expected_run_id = source.run_id if reuse_run else source.retry_run_id(job_id)
                if not reuse_run and expected_run_id == source.run_id:
                    raise StateConflictError("retry run id collision")
                if not reuse_run:
                    self._validate_available_run_id(
                        project_json.parent,
                        expected_run_id,
                    )
                store = RevisionStore(project_json.parent)
                retry = store.retry(
                    retry_of,
                    new_job_id=job_id,
                    reuse_run=reuse_run,
                )
                if (
                    retry.project_id != project_id
                    or retry.job_id != job_id
                    or retry.run_id != expected_run_id
                    or retry.retry_of != retry_of
                    or retry.status != "queued"
                ):
                    raise RequestValidationError("retry draft identity is inconsistent")
                run_id = retry.run_id
                base_hashes = dict(retry.base_package_hashes)
                if scene_id is None:
                    scene_id, base_run_id, correction = None, None, retry.correction
            snapshot = JobSnapshot(
                job_id=job_id,
                project_id=project_id,
                run_id=run_id,
                state="queued",
                stage="queued",
                retry_of=retry_of,
            )
            item = _WorkItem(
                snapshot=snapshot,
                project_json=project_json,
                max_attempts=max_attempts,
                scene_id=scene_id,
                base_run_id=base_run_id,
                correction=correction,
                base_package_hashes=base_hashes,
            )
            self._jobs[job_id] = snapshot
            future = self._executor.submit(self._run_job, item)
            self._futures[job_id] = future
            return snapshot

    def _run_job(self, item: _WorkItem) -> None:
        job_id = item.snapshot.job_id
        self._set_job(job_id, state="running", stage="starting")

        def on_progress(event: ProjectPipelineEvent) -> None:
            self._set_job(job_id, stage=event.stage.value)

        terminal_status = "success"
        terminal_messages: list[str] = []
        terminal_assets = ["audio-narration", "final"]
        public_error: str | None = None
        try:
            pipeline: _Pipeline
            if self.pipeline_factory is None:
                pipeline = VideoPipeline(
                    output_root=item.project_json.parent / "artifacts",
                    id_factory=lambda: item.snapshot.run_id,
                )
            else:
                pipeline = self.pipeline_factory(item.snapshot.run_id)
            result = pipeline.render(
                item.project_json,
                max_attempts=item.max_attempts,
                scene=item.scene_id,
                base_run_id=item.base_run_id,
                correction=item.correction,
                on_progress=on_progress,
            )
            self._validate_video_result(item, result)
        except Exception:
            terminal_status = "failure"
            terminal_messages = ["Render failed"]
            terminal_assets = ["audio-narration"]
            public_error = "Render failed"

        revision_id: str | None = None
        try:
            store = RevisionStore(item.project_json.parent)
            previous = store.current_revision()
            manifest = store.publish_terminal(
                project_id=item.snapshot.project_id,
                job_id=job_id,
                run_id=item.snapshot.run_id,
                status=terminal_status,
                base_package_hashes=item.base_package_hashes,
                correction=item.correction,
                messages=terminal_messages,
                asset_ids=terminal_assets,
            )
            if terminal_status == "failure" and previous is not None:
                store.checkout(previous.revision_id)
            revision_id = manifest.revision_id
        except Exception:
            terminal_status = "failure"
            public_error = "Render failed"
        self._set_job(
            job_id,
            state=terminal_status,
            stage="terminal",
            revision_id=revision_id,
            error=public_error,
        )

    @staticmethod
    def _validate_video_result(item: _WorkItem, result: VideoResult) -> None:
        project_root = item.project_json.parent
        artifacts_root = project_root / "artifacts"
        expected_run = artifacts_root / item.snapshot.run_id
        expected_output = expected_run / "final.mp4"
        if result.state != "ready" or result.output_path is None:
            raise ValueError("render did not produce a ready final output")
        if not _is_real_directory(project_root) or not _is_real_directory(artifacts_root):
            raise ValueError("render artifacts root must be a real directory")
        resolved_project = project_root.resolve(strict=True)
        resolved_artifacts = artifacts_root.resolve(strict=True)
        resolved_artifacts.relative_to(resolved_project)
        if not _is_real_directory(expected_run):
            raise ValueError("render run path must be a real directory")
        resolved_run = expected_run.resolve(strict=True)
        resolved_run.relative_to(resolved_artifacts)
        if result.run_path.resolve(strict=True) != resolved_run:
            raise ValueError("render run path does not match the queued run")
        if result.output_path.resolve(strict=True) != expected_output.resolve(strict=True):
            raise ValueError("render output path is not canonical")
        if not _is_regular_file(expected_output):
            raise ValueError("render final output must be a regular file")
        expected_output.resolve(strict=True).relative_to(resolved_run)

    def _set_job(
        self,
        job_id: str,
        *,
        state: str | None = None,
        stage: str | None = None,
        revision_id: str | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            current = self._jobs[job_id]
            self._jobs[job_id] = replace(
                current,
                state=current.state if state is None else state,
                stage=current.stage if stage is None else stage,
                revision_id=(
                    current.revision_id if revision_id is None else revision_id
                ),
                error=current.error if error is None else error,
            )

    def _project_roots(self) -> list[Path]:
        """Return safe, existing project directories in stable order."""

        if not _is_real_directory(self.projects_root):
            return []
        try:
            entries = sorted(self.projects_root.iterdir(), key=_path_name)
        except OSError:
            return []
        roots: list[Path] = []
        for project_root in entries:
            if (
                _PROJECT_ID.fullmatch(project_root.name) is None
                or not _is_real_directory(project_root)
            ):
                continue
            try:
                project_root.resolve(strict=True).relative_to(self.projects_root)
            except (OSError, ValueError):
                continue
            roots.append(project_root)
        return roots

    @staticmethod
    def _read_only_store(project_root: Path) -> RevisionStore | None:
        """Open a revision store without creating any project-local state."""

        try:
            return RevisionStore(project_root, create=False)
        except (OSError, TypeError, ValueError):
            return None

    @staticmethod
    def _working_drafts(project_root: Path) -> dict[str, WorkingDraft]:
        """Read valid working drafts while ignoring unsafe filesystem nodes."""

        working_root = project_root / "ui" / "working"
        if not _is_real_directory(working_root):
            return {}
        drafts: dict[str, WorkingDraft] = {}
        try:
            paths = sorted(working_root.glob("*.json"), key=_path_name)
        except OSError:
            return drafts
        for path in paths:
            if not _is_regular_file(path) or _PUBLIC_ID.fullmatch(path.stem) is None:
                continue
            try:
                draft = WorkingDraft.from_document(
                    _OBJECT_ADAPTER.validate_json(path.read_text(encoding="utf-8"))
                )
            except (OSError, TypeError, ValueError):
                continue
            if draft.job_id != path.stem or draft.project_id != project_root.name:
                continue
            drafts[draft.job_id] = draft
        return drafts

    def _current_revision_id(self, project_root: Path) -> str | None:
        """Return the current UI revision ID without exposing its path."""

        store = self._read_only_store(project_root)
        if store is None:
            return None
        try:
            current = store.current_revision()
        except (OSError, TypeError, ValueError):
            return None
        return current.revision_id if current is not None else None

    def _job_projection(
        self,
        *,
        project_id: str,
        draft: WorkingDraft | None,
        revision: RevisionManifest | None,
        snapshot: JobSnapshot | None,
    ) -> dict[str, object]:
        """Compose a path-free job record from durable and live sources."""

        job_id = (
            snapshot.job_id
            if snapshot is not None
            else draft.job_id
            if draft is not None
            else revision.job_id
            if revision is not None
            else ""
        )
        run_id = (
            snapshot.run_id
            if snapshot is not None
            else draft.run_id
            if draft is not None
            else revision.run_id
            if revision is not None
            else ""
        )
        state = (
            snapshot.state
            if snapshot is not None
            else draft.status
            if draft is not None
            else revision.status
            if revision is not None
            else "unknown"
        )
        stage = (
            snapshot.stage
            if snapshot is not None
            else "terminal"
            if state in {"success", "failure", "interrupted"}
            else state
        )
        raw_diagnostics = (
            draft.messages
            if draft is not None and draft.messages
            else revision.messages
            if revision is not None
            else []
        )
        diagnostics = [
            self._public_text(message, project_id=project_id)
            for message in raw_diagnostics
        ]
        if not diagnostics and state == "interrupted":
            diagnostics = ["Render interrupted"]
        if not diagnostics and state == "failure":
            diagnostics = ["Render failed"]
        error = snapshot.error if snapshot is not None else None
        if error is None and state == "interrupted":
            error = "Render interrupted"
        if error is None and state == "failure":
            error = diagnostics[-1] if diagnostics else "Render failed"
        return {
            "job_id": job_id,
            "project_id": project_id,
            "run_id": run_id,
            "state": state,
            "stage": stage,
            "revision_id": (
                snapshot.revision_id
                if snapshot is not None and snapshot.revision_id is not None
                else revision.revision_id
                if revision is not None
                else None
            ),
            "retry_of": (
                snapshot.retry_of
                if snapshot is not None and snapshot.retry_of is not None
                else draft.retry_of
                if draft is not None
                else None
            ),
            "error": self._public_text(error, project_id=project_id)
            if error is not None
            else None,
            "diagnostics": diagnostics,
            "scene_id": draft.scene_id if draft is not None else None,
            "base_run_id": draft.base_run_id if draft is not None else None,
            "correction": (
                self._public_text(draft.correction, project_id=project_id)
                if draft is not None and draft.correction is not None
                else None
            ),
        }

    def _find_retry_source(self, job_id: str) -> tuple[str, WorkingDraft]:
        """Find one project-local durable source draft for a retry."""

        matches: list[tuple[str, WorkingDraft]] = []
        for project_root in self._project_roots():
            path = project_root / "ui" / "working" / f"{job_id}.json"
            if not _is_regular_file(path):
                continue
            try:
                draft = WorkingDraft.from_document(
                    _OBJECT_ADAPTER.validate_json(path.read_text(encoding="utf-8"))
                )
            except (OSError, TypeError, ValueError):
                continue
            if draft.job_id == job_id and draft.project_id == project_root.name:
                matches.append((project_root.name, draft))
        if len(matches) != 1:
            raise NotFoundError("unknown job")
        _, source = matches[0]
        if source.status not in {"interrupted", "failure"}:
            raise StateConflictError(
                "retry requires an interrupted or failed working job"
            )
        return matches[0]

    def _public_text(self, value: str | None, *, project_id: str) -> str | None:
        """Redact configured host roots before returning operator text."""

        if value is None:
            return None
        text = value
        project_root = self.projects_root / project_id
        roots = (
            project_root,
            self.projects_root,
            self.projects_root.parent,
            self.audio_root,
            self.audio_root.parent,
        )
        for root in sorted(
            (str(path) for path in roots),
            key=len,
            reverse=True,
        ):
            text = text.replace(root, "<local>")
        return _ABSOLUTE_PATH.sub("<local>", text)

    def _audio_catalog(self) -> dict[str, Path]:
        if not _is_real_directory(self.audio_root):
            return {}
        catalog: dict[str, Path] = {}
        for path in sorted(self.audio_root.iterdir()):
            if not _is_regular_file(path):
                continue
            asset_id = f"audio-{path.stem}"
            if _PUBLIC_ID.fullmatch(asset_id) is not None:
                resolved = path.resolve(strict=True)
                resolved.relative_to(self.audio_root)
                if asset_id in catalog:
                    raise StateConflictError("audio asset id collision")
                catalog[asset_id] = resolved
        return catalog

    def _project_root(self, project_id: str) -> Path:
        if not isinstance(project_id, str) or _PROJECT_ID.fullmatch(project_id) is None:
            raise RequestValidationError("project id must be a safe YYYY_slug identifier")
        root = self.projects_root / project_id
        if not root.is_dir() or root.is_symlink():
            raise NotFoundError("unknown project")
        return root

    def _project_json(self, project_id: str) -> Path:
        project_json = self._project_root(project_id) / "project.json"
        if not project_json.is_file() or project_json.is_symlink():
            raise NotFoundError("unknown project")
        return project_json

    def _validate_available_run_id(self, project_root: Path, run_id: str) -> None:
        if any(snapshot.run_id == run_id for snapshot in self._jobs.values()):
            raise StateConflictError("run id collision")
        run_path = project_root / "artifacts" / run_id
        if run_path.exists() or run_path.is_symlink():
            raise StateConflictError("run id collision")

    @staticmethod
    def _load_retry_source(project_root: Path, job_id: str) -> WorkingDraft:
        source_path = project_root / "ui" / "working" / f"{job_id}.json"
        if not _is_regular_file(source_path) or source_path.stem != job_id:
            raise NotFoundError("retry source must be a regular project-local draft")
        source = WorkingDraft.from_document(
            _OBJECT_ADAPTER.validate_json(source_path.read_text(encoding="utf-8"))
        )
        return source

    def _recover_interrupted_jobs(self) -> None:
        if not self.projects_root.is_dir():
            return
        for project_root in sorted(self.projects_root.iterdir()):
            if (
                _PROJECT_ID.fullmatch(project_root.name) is None
                or not project_root.is_dir()
                or project_root.is_symlink()
            ):
                continue
            try:
                project_root.resolve(strict=True).relative_to(self.projects_root)
            except (OSError, ValueError):
                continue
            store = RevisionStore(project_root)
            with store._mutation_lock():
                working = project_root / "ui" / "working"
                if not working.is_dir() or working.is_symlink():
                    continue
                preflight: dict[str, tuple[Path, WorkingDraft, bytes]] = {}
                for path in sorted(working.iterdir()):
                    if path.suffix != ".json":
                        continue
                    if not _is_regular_file(path):
                        raise ValueError("working job must be a regular file")
                    try:
                        original_bytes = path.read_bytes()
                        draft = WorkingDraft.from_document(
                            _OBJECT_ADAPTER.validate_json(original_bytes)
                        )
                    except (OSError, TypeError, ValueError) as exc:
                        raise ValueError("working job document is invalid") from exc
                    if (
                        draft.job_id != path.stem
                        or draft.project_id != project_root.name
                        or _PUBLIC_ID.fullmatch(draft.job_id) is None
                        or _PUBLIC_ID.fullmatch(draft.run_id) is None
                        or draft.job_id in preflight
                        or draft.job_id in self._jobs
                    ):
                        raise ValueError("working job identity is inconsistent")
                    preflight[draft.job_id] = (path, draft, original_bytes)
                store._repair_missing_index_locked()
                reconciled_ids = store._reconcile_terminal_drafts_locked()
                recovered_ids = {
                    draft.job_id for draft in store._recover_interrupted_locked()
                }
                for job_id, (path, original, original_bytes) in preflight.items():
                    try:
                        current_bytes = path.read_bytes()
                        document = _OBJECT_ADAPTER.validate_json(current_bytes)
                        draft = WorkingDraft.from_document(document)
                    except (OSError, TypeError, ValueError):
                        raise ValueError("working job changed during recovery") from None
                    if draft.job_id != job_id or draft.project_id != project_root.name:
                        raise ValueError("recovered job identity is inconsistent")
                    if job_id in reconciled_ids:
                        if draft.status not in {"success", "failure"}:
                            raise ValueError("reconciled job state is inconsistent")
                        continue
                    was_interrupted = original.status == "interrupted"
                    recovered_now = job_id in recovered_ids
                    if not was_interrupted and not recovered_now:
                        if current_bytes != original_bytes:
                            raise ValueError("terminal working job changed during recovery")
                        continue
                    if draft.status != "interrupted":
                        raise ValueError("recovered job state is inconsistent")
                    if was_interrupted and current_bytes != original_bytes:
                        raise ValueError("interrupted job changed during recovery")
                    if draft.job_id in self._jobs:
                        raise ValueError("recovered job id collision")
                    self._jobs[draft.job_id] = JobSnapshot(
                        job_id=draft.job_id,
                        project_id=draft.project_id,
                        run_id=draft.run_id,
                        state="interrupted",
                        stage="terminal",
                        retry_of=draft.retry_of,
                        error="Render interrupted",
                    )

    @staticmethod
    def _validate_public_id(value: str, label: str) -> None:
        if not isinstance(value, str) or _PUBLIC_ID.fullmatch(value) is None:
            raise RequestValidationError(f"{label} must be a safe public identifier")

    @staticmethod
    def _new_project_id() -> str:
        from datetime import UTC, datetime

        return f"{datetime.now(UTC).year}_{uuid.uuid4().hex[:12]}"


__all__ = ["JobSnapshot", "QueueFullError", "ServiceLimits", "WebService"]


def _media_asset_id(
    project_id: str,
    revision_id: str,
    run_id: str,
    kind: str,
    scene_id: str | None,
) -> str:
    identity = "\0".join(
        (project_id, revision_id, run_id, kind, scene_id or "")
    ).encode("utf-8")
    return f"media-{hashlib.sha256(identity).hexdigest()}"


def _is_regular_file(path: Path) -> bool:
    try:
        mode = path.lstat().st_mode
    except OSError:
        return False
    return stat.S_ISREG(mode) and not stat.S_ISLNK(mode)


def _is_real_directory(path: Path) -> bool:
    try:
        mode = path.lstat().st_mode
    except OSError:
        return False
    return stat.S_ISDIR(mode) and not stat.S_ISLNK(mode)


def _path_name(path: Path) -> str:
    return path.name


def _job_sort_key(key: tuple[str, str]) -> tuple[str, str]:
    return key
