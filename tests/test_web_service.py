"""RED contracts for the canonical Web UI service boundary."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from pathlib import Path
from threading import Event, Lock

import pytest

from video_pipeline.pipeline import PipelineStage, PipelineState
from video_pipeline.project import (
    Project,
    _project_package_hashes,
    inspect_project,
)
from video_pipeline.revisions import RevisionStore
from video_pipeline.timeline import Timeline
from video_pipeline.video import ProjectPipelineEvent, VideoResult

CONTRACT_MISSING = "WEB_CANONICAL_SERVICE_CONTRACT_MISSING"


def _load_contract() -> tuple[type[object], ...]:
    """Import the Web service only when a test runs, preserving a behavioral RED."""

    try:
        from video_pipeline.web import (
            JobSnapshot as PublicJobSnapshot,
        )
        from video_pipeline.web import (
            ServiceLimits as PublicServiceLimits,
        )
        from video_pipeline.web import (
            WebService as PublicWebService,
        )
        from video_pipeline.web.service import (
            JobSnapshot,
            ServiceLimits,
            WebService,
        )
    except (ImportError, ModuleNotFoundError) as exc:  # pragma: no cover - RED seam
        pytest.fail(f"{CONTRACT_MISSING}: {exc}", pytrace=False)
    required_methods = (
        "list_audio",
        "create_project",
        "inspect",
        "confirm_timeline",
        "enqueue_render",
        "enqueue_regeneration",
        "get_job",
        "wait_job",
        "checkout_revision",
        "accept_run",
        "close",
    )
    if any(not callable(getattr(WebService, name, None)) for name in required_methods):
        pytest.fail(CONTRACT_MISSING, pytrace=False)
    if not all(
        isinstance(candidate, type)
        for candidate in (
            PublicWebService,
            PublicServiceLimits,
            PublicJobSnapshot,
            WebService,
            ServiceLimits,
            JobSnapshot,
        )
    ) or any(
        public is not private
        for public, private in (
            (PublicWebService, WebService),
            (PublicServiceLimits, ServiceLimits),
            (PublicJobSnapshot, JobSnapshot),
        )
    ):
        pytest.fail(CONTRACT_MISSING, pytrace=False)
    return (
        PublicWebService,
        PublicServiceLimits,
        PublicJobSnapshot,
        WebService,
        ServiceLimits,
        JobSnapshot,
    )


class FakeAudioProbe:
    """Deterministic probe fake at the canonical copied-audio boundary."""

    def __init__(self, duration: float = 10.0) -> None:
        self.duration = duration
        self.calls: list[Path] = []

    def __call__(self, path: Path) -> dict[str, object]:
        self.calls.append(path)
        audio_bytes = path.read_bytes()
        return {
            "path": "audio/narration.wav",
            "hash": hashlib.sha256(audio_bytes).hexdigest(),
            "container": "wav",
            "codec": "pcm_s16le",
            "stream": 0,
            "sample_rate": 48_000,
            "channels": 1,
            "duration": self.duration,
            "size": len(audio_bytes),
            "probe_result": {
                "format": {"format_name": "wav", "duration": str(self.duration)},
                "streams": [
                    {
                        "index": 0,
                        "codec_type": "audio",
                        "codec_name": "pcm_s16le",
                        "sample_rate": "48000",
                        "channels": 1,
                    }
                ],
            },
        }


class EmptySilenceDetector:
    """Candidate setup must not invoke FFmpeg or other real media tooling."""

    def __call__(self, path: Path) -> tuple[object, ...]:
        del path
        return ()


def _make_audio_root(tmp_path: Path) -> tuple[Path, Path]:
    audio_root = tmp_path / "audio-catalog"
    audio_root.mkdir(parents=True, exist_ok=True)
    audio = audio_root / "narration.wav"
    audio.write_bytes(b"deterministic fake narration")
    return audio_root, audio


def _make_service(
    tmp_path: Path,
    *,
    project_id: str = "2026_web",
    project_ids: Iterable[str] | None = None,
    pipeline_factory: Callable[[str], object] | None = None,
    job_id_factory: Callable[[], str] | None = None,
    run_id_factory: Callable[[], str] | None = None,
) -> tuple[object, Path, Path, FakeAudioProbe]:
    _, _, _, WebService, ServiceLimits, _ = _load_contract()
    audio_root, audio = _make_audio_root(tmp_path)
    probe = FakeAudioProbe()
    projects_root = tmp_path / "projects"
    id_source = iter(project_ids or (project_id,))
    service_options: dict[str, object] = {
        "projects_root": projects_root,
        "audio_root": audio_root,
        "audio_probe": probe,
        "project_id_factory": lambda: next(id_source),
        "silence_detector": EmptySilenceDetector(),
        "limits": ServiceLimits(),
    }
    if pipeline_factory is not None:
        service_options["pipeline_factory"] = pipeline_factory
    if job_id_factory is not None:
        service_options["job_id_factory"] = job_id_factory
    if run_id_factory is not None:
        service_options["run_id_factory"] = run_id_factory
    service = WebService(
        **service_options,
    )
    return service, projects_root, audio, probe


def _assert_no_project_residue(projects_root: Path, project_id: str) -> None:
    assert not (projects_root / project_id).exists()
    if projects_root.exists():
        assert not any(projects_root.rglob("*"))


def _snapshot_files(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _mark_project_ready(project_root: Path, run_id: str = "base-ready") -> None:
    project_json = project_root / "project.json"
    document = json.loads(project_json.read_text(encoding="utf-8"))
    document.update(
        {
            "status": "ready",
            "current_run": run_id,
            "current_scene": None,
            "render_state": "ready",
            "composition_state": "ready",
        }
    )
    project_json.write_text(json.dumps(document), encoding="utf-8")


class _BlockingPipelineFactory:
    """A real-signature VideoPipeline seam for serialized queue assertions."""

    def __init__(
        self,
        *,
        expected_terminal: int,
        block_all: bool = False,
        failure_message: str | None = None,
    ) -> None:
        self.expected_terminal = expected_terminal
        self.block_all = block_all
        self.failure_message = failure_message
        self.release = Event()
        self.first_started = Event()
        self.all_terminal = Event()
        self.started_projects: list[str] = []
        self.created_run_ids: list[str] = []
        self.render_calls: list[dict[str, object]] = []
        self.events: list[ProjectPipelineEvent] = []
        self.callback_seen: list[bool] = []
        self.generating_emitted = Event()
        self.max_active = 0
        self._active = 0
        self._completed = 0
        self._created = 0
        self._lock = Lock()

    def __call__(self, run_id: str) -> "_BlockingPipeline":
        with self._lock:
            self._created += 1
            ordinal = self._created
            self.created_run_ids.append(run_id)
        return _BlockingPipeline(self, run_id, ordinal)

    def _started(self, project_id: str, callback_present: bool) -> None:
        with self._lock:
            self.started_projects.append(project_id)
            self.callback_seen.append(callback_present)
            self._active += 1
            self.max_active = max(self.max_active, self._active)
            first = len(self.started_projects) == 1
        if first:
            self.first_started.set()

    def _record_call(self, values: dict[str, object]) -> None:
        with self._lock:
            self.render_calls.append(values)

    def _record_event(self, event: ProjectPipelineEvent) -> None:
        with self._lock:
            self.events.append(event)

    def _completed_one(self) -> None:
        with self._lock:
            self._active -= 1
            self._completed += 1
            terminal = self._completed >= self.expected_terminal
        if terminal:
            self.all_terminal.set()


class _BlockingPipeline:
    def __init__(
        self,
        owner: _BlockingPipelineFactory,
        run_id: str,
        ordinal: int,
    ) -> None:
        self.owner = owner
        self.run_id = run_id
        self.ordinal = ordinal

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
        project_file = Path(project_path)
        project_id = (
            project_file.parent.name
            if project_file.name == "project.json"
            else project_file.name
        )
        self.owner._record_call(
            {
                "project_path": project_file,
                "max_attempts": max_attempts,
                "scene": scene,
                "base_run_id": base_run_id,
                "correction": correction,
                "on_progress": on_progress,
            }
        )
        self.owner._started(project_id, on_progress is not None)
        try:
            if on_progress is not None:
                inner_run_id = f"{self.run_id}-scene-abertura"
                started_event = ProjectPipelineEvent(
                    run_id=inner_run_id,
                    attempt=1,
                    stage=PipelineStage.GENERATING,
                    state=PipelineState.ATTEMPTING,
                    observation="not_applicable",
                    project_run_id=self.run_id,
                    scene_id="abertura",
                )
                self.owner._record_event(started_event)
                on_progress(started_event)
                self.owner.generating_emitted.set()
            if self.owner.failure_message is not None:
                raise RuntimeError(self.owner.failure_message)
            if self.owner.block_all or self.ordinal == 1:
                if not self.owner.release.wait(timeout=5):
                    raise AssertionError("queue test release was not signaled")
            if on_progress is not None:
                terminal_event = ProjectPipelineEvent(
                    run_id=f"{self.run_id}-scene-abertura",
                    attempt=1,
                    stage=PipelineStage.TERMINAL,
                    state=PipelineState.SUCCESS,
                    observation="not_applicable",
                    project_run_id=self.run_id,
                    scene_id="abertura",
                )
                self.owner._record_event(terminal_event)
                on_progress(terminal_event)
            run_path = project_file.parent / "artifacts" / self.run_id
            run_path.mkdir(parents=True, exist_ok=True)
            final_path = run_path / "final.mp4"
            final_path.write_bytes(b"fake non-empty final mp4")
            return VideoResult(
                state="ready",
                run_path=run_path,
                output_path=final_path,
            )
        finally:
            self.owner._completed_one()


def _create_confirmed_projects(service: object, project_ids: Iterable[str]) -> None:
    for project_id in project_ids:
        service.create_project(  # type: ignore[attr-defined]
            title=f"Projeto {project_id}",
            script="# Abertura\nTexto.\n\n## Fecho\nConclusão.\n",
            audio_asset_id="audio-narration",
        )
        service.confirm_timeline(project_id)  # type: ignore[attr-defined]


def _job_id(snapshot: object) -> str:
    if isinstance(snapshot, dict):
        value = snapshot["job_id"]
    else:
        value = snapshot.job_id  # type: ignore[attr-defined]
    assert isinstance(value, str)
    return value


def _job_state(service: object, snapshot: object) -> str:
    current = service.get_job(_job_id(snapshot))  # type: ignore[attr-defined]
    if isinstance(current, dict):
        value = current["state"]
    else:
        value = current.state  # type: ignore[attr-defined]
    assert isinstance(value, str)
    return value


def _job_snapshot(service: object, snapshot: object) -> object:
    return service.get_job(_job_id(snapshot))  # type: ignore[attr-defined]


def test_guard_catalog_create_and_confirm_use_canonical_contract(
    tmp_path: Path,
) -> None:
    (
        PublicWebService,
        PublicServiceLimits,
        PublicJobSnapshot,
        WebService,
        ServiceLimits,
        JobSnapshot,
    ) = _load_contract()
    assert PublicWebService is WebService
    assert PublicServiceLimits is ServiceLimits
    assert PublicJobSnapshot is JobSnapshot

    audio_root, audio = _make_audio_root(tmp_path)
    probe = FakeAudioProbe()
    project_ids = iter(("2026_canonical",))
    service = WebService(
        projects_root=tmp_path / "projects",
        audio_root=audio_root,
        audio_probe=probe,
        project_id_factory=lambda: next(project_ids),
        silence_detector=EmptySilenceDetector(),
        limits=ServiceLimits(),
    )
    script = (
        "# Abertura\nUma introdução.\n\n"
        "## Fecho\nUma conclusão.\n"
    )

    with service:
        assets = service.list_audio()
        assert [asset["id"] for asset in assets] == ["audio-narration"]
        serialized_assets = json.dumps(assets, ensure_ascii=False)
        assert str(audio_root.resolve()) not in serialized_assets
        assert all("path" not in asset for asset in assets)
        assert not Path(assets[0]["id"]).is_absolute()
        assert ".." not in Path(assets[0]["id"]).parts

        created = service.create_project(
            title="Projeto canônico",
            script=script,
            audio_asset_id="audio-narration",
        )
        project_root = tmp_path / "projects" / "2026_canonical"
        project_json = project_root / "project.json"
        timeline_json = project_root / "timeline.json"
        canonical_project = Project.model_validate_json(
            project_json.read_text(encoding="utf-8")
        )
        canonical_timeline = Timeline.model_validate_json(
            timeline_json.read_text(encoding="utf-8")
        )
        assert canonical_project.id == "2026_canonical"
        assert canonical_project.status.value == "timeline_candidate"
        assert canonical_timeline.status == "candidate"
        assert created["project"]["id"] == canonical_project.id
        assert created["timeline"]["status"] == canonical_timeline.status
        assert probe.calls
        assert probe.calls[0] != audio
        assert probe.calls[0].parent.name == "audio"
        assert probe.calls[0].is_relative_to(project_root)

        before_confirmation = service.inspect("2026_canonical")
        assert before_confirmation["project"]["status"] == "timeline_candidate"
        assert before_confirmation["timeline"]["status"] == "candidate"
        assert before_confirmation == inspect_project(project_json)

        confirmed = service.confirm_timeline("2026_canonical")
        confirmed_project = Project.model_validate_json(
            project_json.read_text(encoding="utf-8")
        )
        confirmed_timeline = Timeline.model_validate_json(
            timeline_json.read_text(encoding="utf-8")
        )
        assert confirmed_project.status.value == "timeline_confirmed"
        assert confirmed_timeline.status == "confirmed"
        assert confirmed["project"]["id"] == "2026_canonical"
        assert confirmed["timeline"]["status"] == "confirmed"
        after_confirmation = service.inspect("2026_canonical")
        assert after_confirmation == inspect_project(project_json)
        assert after_confirmation["project"]["status"] == "timeline_confirmed"
        assert after_confirmation["timeline"]["status"] == "confirmed"


def test_service_rejects_unknown_audio_id_without_writes(tmp_path: Path) -> None:
    service, projects_root, _, _ = _make_service(tmp_path)

    with service:
        with pytest.raises(ValueError, match="audio"):
            service.create_project(
                title="Áudio ausente",
                script="# Abertura\nTexto.\n",
                audio_asset_id="missing.wav",
            )
    _assert_no_project_residue(projects_root, "2026_web")


def test_service_rejects_unsafe_project_id_without_writes(tmp_path: Path) -> None:
    unsafe_service, unsafe_projects_root, _, _ = _make_service(
        tmp_path,
        project_id="../escape",
    )
    with unsafe_service:
        with pytest.raises(ValueError, match="project|id"):
            unsafe_service.create_project(
                title="ID inseguro",
                script="# Abertura\nTexto.\n",
                audio_asset_id="audio-narration",
            )
    _assert_no_project_residue(unsafe_projects_root, "../escape")
    assert not (unsafe_projects_root.parent / "escape").exists()


def test_service_rejects_overlong_script_before_creating_project_or_residue(
    tmp_path: Path,
) -> None:
    service, projects_root, _, _ = _make_service(tmp_path)
    overlong_script = "x" * 50_001

    with service:
        with pytest.raises(ValueError, match="script"):
            service.create_project(
                title="Roteiro grande",
                script=overlong_script,
                audio_asset_id="audio-narration",
            )

    _assert_no_project_residue(projects_root, "2026_web")


def test_service_rejects_more_than_twenty_headings_before_project_registration(
    tmp_path: Path,
) -> None:
    service, projects_root, _, _ = _make_service(tmp_path)
    overfull_script = "\n\n".join(
        f"# Cena {index:02d}\nConteúdo da cena {index}."
        for index in range(1, 22)
    )

    with service:
        with pytest.raises(ValueError, match="20"):
            service.create_project(
                title="Vinte e uma cenas",
                script=overfull_script,
                audio_asset_id="audio-narration",
            )

    _assert_no_project_residue(projects_root, "2026_web")


def test_render_requires_confirmation_but_confirmation_opens_real_enqueue_boundary(
    tmp_path: Path,
) -> None:
    project_id = "2026_web"
    pipeline_factory = _BlockingPipelineFactory(expected_terminal=1)
    service, projects_root, _, _ = _make_service(
        tmp_path,
        pipeline_factory=pipeline_factory,
    )
    project_root = projects_root / project_id
    project_json = project_root / "project.json"

    try:
        service.create_project(
            title="Fronteira da timeline",
            script="# Abertura\nTexto.\n\n## Fecho\nConclusão.\n",
            audio_asset_id="audio-narration",
        )
        with pytest.raises(ValueError, match="confirmed"):
            service.enqueue_render(project_id)
        assert pipeline_factory.created_run_ids == []
        assert pipeline_factory.render_calls == []

        confirmed = service.confirm_timeline(project_id)
        assert confirmed["timeline"]["status"] == "confirmed"
        queued = service.enqueue_render(project_id)
        assert queued
        assert pipeline_factory.first_started.wait(timeout=5)
        pipeline_factory.release.set()
        assert pipeline_factory.all_terminal.wait(timeout=5)
    finally:
        pipeline_factory.release.set()
        service.close()  # type: ignore[attr-defined]

    assert project_json.exists()


def test_enqueue_regeneration_rejects_oversized_correction_before_job_or_draft(
    tmp_path: Path,
) -> None:
    service, projects_root, _, _ = _make_service(tmp_path)
    project_id = "2026_web"
    project_root = projects_root / project_id
    oversized_correction = "c" * 5_001

    with service:
        service.create_project(
            title="Correção limitada",
            script="# Abertura\nTexto.\n\n## Fecho\nConclusão.\n",
                audio_asset_id="audio-narration",
        )
        service.confirm_timeline(project_id)
        with pytest.raises(ValueError, match="correction|correção"):
            service.enqueue_regeneration(
                project_id,
                base_run_id="2026_base",
                scene_id="abertura",
                correction=oversized_correction,
            )

    ui_root = project_root / "ui"
    assert not ui_root.exists() or not any(ui_root.rglob("*"))


def test_service_limits_expose_the_literal_maximum_queue_size() -> None:
    _, _, _, _, ServiceLimits, _ = _load_contract()
    assert ServiceLimits().max_queue == 32


def test_render_jobs_are_fifo_single_worker_and_reach_terminal_state(
    tmp_path: Path,
) -> None:
    project_ids = [
        "2026_fifo_one",
        "2026_fifo_two",
        "2026_fifo_three",
    ]
    pipeline_factory = _BlockingPipelineFactory(expected_terminal=len(project_ids))
    service, _, _, _ = _make_service(
        tmp_path,
        project_ids=project_ids,
        pipeline_factory=pipeline_factory,
    )

    try:
        _create_confirmed_projects(service, project_ids)
        first = service.enqueue_render(project_ids[0])  # type: ignore[attr-defined]
        assert first.state == "queued"  # type: ignore[attr-defined]
        assert pipeline_factory.first_started.wait(timeout=5)
        second = service.enqueue_render(project_ids[1])  # type: ignore[attr-defined]
        third = service.enqueue_render(project_ids[2])  # type: ignore[attr-defined]
        assert second.state == "queued"  # type: ignore[attr-defined]
        assert third.state == "queued"  # type: ignore[attr-defined]
        assert _job_state(service, first) == "running"
        assert _job_state(service, second) == "queued"
        assert _job_state(service, third) == "queued"

        pipeline_factory.release.set()
        assert pipeline_factory.all_terminal.wait(timeout=5)
        assert pipeline_factory.started_projects == project_ids
        assert pipeline_factory.max_active == 1
        assert pipeline_factory.callback_seen == [True, True, True]
        assert [
            service.wait_job(_job_id(job), timeout=5).state  # type: ignore[attr-defined]
            for job in (first, second, third)
        ] == [
            "success",
            "success",
            "success",
        ]
    finally:
        pipeline_factory.release.set()
        service.close()  # type: ignore[attr-defined]

def test_render_queue_rejects_thirty_third_outstanding_job_without_draft_or_job(
    tmp_path: Path,
) -> None:
    project_ids = [f"2026_capacity_{index:02d}" for index in range(1, 34)]
    job_ids = [f"job-capacity-{index:02d}" for index in range(1, 34)]
    run_ids = [f"run-capacity-{index:02d}" for index in range(1, 34)]
    job_source = iter(job_ids)
    run_source = iter(run_ids)
    pipeline_factory = _BlockingPipelineFactory(expected_terminal=32, block_all=True)
    service, projects_root, _, _ = _make_service(
        tmp_path,
        project_ids=project_ids,
        pipeline_factory=pipeline_factory,
        job_id_factory=lambda: next(job_source),
        run_id_factory=lambda: next(run_source),
    )
    rejected_project_root = projects_root / project_ids[-1]

    try:
        _create_confirmed_projects(service, project_ids)
        accepted = [
            service.enqueue_render(project_id)  # type: ignore[attr-defined]
            for project_id in project_ids[:-1]
        ]
        assert len(accepted) == 32
        assert pipeline_factory.first_started.wait(timeout=5)
        before_rejected = _snapshot_files(rejected_project_root / "ui")
        with pytest.raises(ValueError, match="queue|capacity|full|32"):
            service.enqueue_render(project_ids[-1])  # type: ignore[attr-defined]
        with pytest.raises(ValueError, match="job"):
            service.get_job(job_ids[-1])  # type: ignore[attr-defined]
        assert _snapshot_files(rejected_project_root / "ui") == before_rejected
        assert not (rejected_project_root / "ui" / "working").exists()
    finally:
        pipeline_factory.release.set()
        service.close()  # type: ignore[attr-defined]

    assert pipeline_factory.created_run_ids == run_ids[:32]


def test_enqueue_render_delegates_canonical_project_and_progress_without_replacing_evidence(
    tmp_path: Path,
) -> None:
    project_id = "2026_render_contract"
    pipeline_factory = _BlockingPipelineFactory(expected_terminal=1)
    service, projects_root, _, _ = _make_service(
        tmp_path,
        project_id=project_id,
        pipeline_factory=pipeline_factory,
    )
    project_root = projects_root / project_id
    project_json = project_root / "project.json"

    try:
        _create_confirmed_projects(service, [project_id])
        before_inspection = inspect_project(project_json)
        job = service.enqueue_render(  # type: ignore[attr-defined]
            project_id,
            max_attempts=4,
        )
        assert pipeline_factory.generating_emitted.wait(timeout=5)
        running = _job_snapshot(service, job)
        assert running.state == "running"  # type: ignore[attr-defined]
        assert running.stage == "generating"  # type: ignore[attr-defined]
        pipeline_factory.release.set()
        assert pipeline_factory.all_terminal.wait(timeout=5)
        terminal = service.wait_job(_job_id(job))  # type: ignore[attr-defined]
        assert terminal.state == "success"  # type: ignore[attr-defined]
        assert terminal.stage == "terminal"  # type: ignore[attr-defined]
        assert terminal.revision_id == "v001"  # type: ignore[attr-defined]
        after_inspection = service.inspect(project_id)
        assert after_inspection == inspect_project(project_json)
        assert after_inspection == before_inspection
    finally:
        pipeline_factory.release.set()
        service.close()  # type: ignore[attr-defined]

    assert len(pipeline_factory.render_calls) == 1
    render_call = pipeline_factory.render_calls[0]
    assert render_call["project_path"] == project_json
    assert render_call["max_attempts"] == 4
    assert render_call["scene"] is None
    assert render_call["base_run_id"] is None
    assert render_call["correction"] is None
    assert render_call["on_progress"] is not None
    assert pipeline_factory.events
    assert all(isinstance(event, ProjectPipelineEvent) for event in pipeline_factory.events)
    assert all(event.scene_id == "abertura" for event in pipeline_factory.events)
    outer_run_id = pipeline_factory.created_run_ids[0]
    assert all(event.project_run_id == outer_run_id for event in pipeline_factory.events)
    assert all(
        event.run_id == f"{outer_run_id}-scene-abertura"
        for event in pipeline_factory.events
    )
    assert all(event.run_id != event.project_run_id for event in pipeline_factory.events)
    assert [event.observation for event in pipeline_factory.events] == [
        "not_applicable",
        "not_applicable",
    ]
    assert [event.stage.value for event in pipeline_factory.events] == [
        "generating",
        "terminal",
    ]
    final_path = project_root / "artifacts" / pipeline_factory.created_run_ids[0] / "final.mp4"
    assert final_path.is_file()
    assert final_path.stat().st_size > 0


def test_enqueue_regeneration_delegates_scene_base_and_correction_without_new_canonical_documents(
    tmp_path: Path,
) -> None:
    project_id = "2026_regeneration_contract"
    pipeline_factory = _BlockingPipelineFactory(expected_terminal=1)
    pipeline_factory.release.set()
    service, projects_root, _, _ = _make_service(
        tmp_path,
        project_id=project_id,
        pipeline_factory=pipeline_factory,
    )
    project_root = projects_root / project_id
    project_json = project_root / "project.json"
    timeline_json = project_root / "timeline.json"
    scene_snapshot: dict[str, bytes] = {}
    timeline_before = b""
    correction = "Aumente a espessura da seta azul"

    try:
        _create_confirmed_projects(service, [project_id])
        _mark_project_ready(project_root)
        scene_snapshot = _snapshot_files(project_root / "scenes")
        timeline_before = timeline_json.read_bytes()
        job = service.enqueue_regeneration(  # type: ignore[attr-defined]
            project_id,
            base_run_id="base-ready",
            scene_id="abertura",
            correction=correction,
        )
        assert pipeline_factory.all_terminal.wait(timeout=5)
    finally:
        pipeline_factory.release.set()
        service.close()  # type: ignore[attr-defined]

    call = pipeline_factory.render_calls[0]
    assert call["project_path"] == project_json
    assert call["scene"] == "abertura"
    assert call["base_run_id"] == "base-ready"
    assert call["correction"] == correction
    assert pipeline_factory.events
    assert timeline_json.read_bytes() == timeline_before
    assert _snapshot_files(project_root / "scenes") == scene_snapshot
    assert job is not None


def test_success_publishes_one_revision_with_canonical_packages_assets_and_snapshot_id(
    tmp_path: Path,
) -> None:
    project_id = "2026_revision_success"
    pipeline_factory = _BlockingPipelineFactory(expected_terminal=1)
    pipeline_factory.release.set()
    service, projects_root, _, _ = _make_service(
        tmp_path,
        project_id=project_id,
        pipeline_factory=pipeline_factory,
    )
    project_root = projects_root / project_id
    project_json = project_root / "project.json"
    correction = "Reforce a leitura do resultado"

    try:
        _create_confirmed_projects(service, [project_id])
        _mark_project_ready(project_root)
        job = service.enqueue_regeneration(  # type: ignore[attr-defined]
            project_id,
            base_run_id="base-ready",
            scene_id="abertura",
            correction=correction,
        )
        assert pipeline_factory.all_terminal.wait(timeout=5)
    finally:
        pipeline_factory.release.set()
        service.close()  # type: ignore[attr-defined]

    revision_paths = sorted((project_root / "ui" / "revisions").glob("v*.json"))
    assert len(revision_paths) == 1
    revision = json.loads(revision_paths[0].read_text(encoding="utf-8"))
    project_model = Project.model_validate_json(project_json.read_text(encoding="utf-8"))
    job_id = _job_id(job)
    assert revision["revision_id"] == "v001"
    assert revision["project_id"] == project_id
    assert revision["job_id"] == job_id
    assert revision["run_id"] == pipeline_factory.created_run_ids[0]
    assert revision["status"] == "success"
    assert revision["correction"] == correction
    assert revision["base_package_hashes"] == _project_package_hashes(
        project_root,
        project_model,
    )
    assert revision["asset_ids"]
    assert "audio-narration" in revision["asset_ids"]
    assert "final" in revision["asset_ids"]
    snapshot = _job_snapshot(service, job)
    assert snapshot.revision_id == revision["revision_id"]  # type: ignore[attr-defined]


def test_pipeline_exception_is_redacted_in_failure_job_revision_and_never_creates_golden(
    tmp_path: Path,
) -> None:
    project_id = "2026_revision_failure"
    failure = f"render crashed at {tmp_path}/token=token secret=secret"
    pipeline_factory = _BlockingPipelineFactory(
        expected_terminal=1,
        failure_message=failure,
    )
    service, projects_root, _, _ = _make_service(
        tmp_path,
        project_id=project_id,
        pipeline_factory=pipeline_factory,
    )
    project_root = projects_root / project_id
    project_json = project_root / "project.json"

    try:
        _create_confirmed_projects(service, [project_id])
        job = service.enqueue_render(project_id)  # type: ignore[attr-defined]
        assert pipeline_factory.all_terminal.wait(timeout=5)
    finally:
        pipeline_factory.release.set()
        service.close()  # type: ignore[attr-defined]

    snapshot = _job_snapshot(service, job)
    public_error = str(snapshot.error)  # type: ignore[attr-defined]
    revision_paths = sorted((project_root / "ui" / "revisions").glob("v*.json"))
    assert len(revision_paths) == 1
    revision_text = revision_paths[0].read_text(encoding="utf-8")
    assert snapshot.state == "failure"  # type: ignore[attr-defined]
    assert snapshot.revision_id == "v001"  # type: ignore[attr-defined]
    assert snapshot.error  # type: ignore[attr-defined]
    for sensitive in (str(tmp_path), "token", "secret"):
        assert sensitive not in public_error
        assert sensitive not in revision_text
    assert json.loads(revision_text)["status"] == "failure"
    assert not (project_root / "golden").exists()
    assert project_json.exists()
    inspection = service.inspect(project_id)  # type: ignore[attr-defined]
    assert inspection["ui"]["current_revision_id"] == "v001"  # type: ignore[index]
    assert inspection["ui"]["revisions"][0]["status"] == "failure"  # type: ignore[index]
    assert inspection["ui"]["media"] == {"final_asset_id": None, "scenes": []}  # type: ignore[index]


def test_terminal_persistence_failure_finishes_job_without_claiming_a_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = "2026_terminal_persistence_failure"
    pipeline_factory = _BlockingPipelineFactory(expected_terminal=1)
    pipeline_factory.release.set()
    service, _, _, _ = _make_service(
        tmp_path,
        project_id=project_id,
        pipeline_factory=pipeline_factory,
    )

    def fail_publish(*_args: object, **_kwargs: object) -> object:
        raise OSError(f"cannot persist {tmp_path}/token=secret")

    try:
        _create_confirmed_projects(service, [project_id])
        monkeypatch.setattr(RevisionStore, "publish_terminal", fail_publish)
        job = service.enqueue_render(project_id)  # type: ignore[attr-defined]
        terminal = service.wait_job(_job_id(job), timeout=5)  # type: ignore[attr-defined]
    finally:
        pipeline_factory.release.set()
        service.close()  # type: ignore[attr-defined]

    assert terminal.state == "failure"  # type: ignore[attr-defined]
    assert terminal.stage == "terminal"  # type: ignore[attr-defined]
    assert terminal.revision_id is None  # type: ignore[attr-defined]
    assert terminal.error == "Render failed"  # type: ignore[attr-defined]
    assert str(tmp_path) not in terminal.error  # type: ignore[attr-defined]
    assert "token" not in terminal.error  # type: ignore[attr-defined]
    assert "secret" not in terminal.error  # type: ignore[attr-defined]


def test_restart_recovers_draft_without_auto_resume_then_explicit_retry_is_fifo_and_revised(
    tmp_path: Path,
) -> None:
    project_id = "2026_restart_primary"
    sibling_id = "2026_restart_sibling"
    old_job_id = "job-interrupted"
    old_run_id = "run-interrupted"
    project_ids = [project_id, sibling_id]
    bootstrap, projects_root, _, _ = _make_service(
        tmp_path,
        project_ids=project_ids,
    )
    project_root = projects_root / project_id

    try:
        _create_confirmed_projects(bootstrap, project_ids)
    finally:
        bootstrap.close()  # type: ignore[attr-defined]

    project_model = Project.model_validate_json(
        (project_root / "project.json").read_text(encoding="utf-8")
    )
    store = RevisionStore(project_root)
    old_draft = store.start_working(
        project_id=project_id,
        job_id=old_job_id,
        run_id=old_run_id,
        status="running",
        base_package_hashes=_project_package_hashes(project_root, project_model),
        correction=None,
        asset_ids=["audio-narration"],
    )
    assert old_draft.status == "running"
    old_draft_path = project_root / "ui" / "working" / f"{old_job_id}.json"
    assert old_draft_path.is_relative_to(project_root / "ui")

    pipeline_factory = _BlockingPipelineFactory(expected_terminal=2)
    service, _, _, _ = _make_service(
        tmp_path,
        project_ids=project_ids,
        pipeline_factory=pipeline_factory,
    )
    retry_job: object
    try:
        interrupted = service.get_job(old_job_id)  # type: ignore[attr-defined]
        assert interrupted.state == "interrupted"  # type: ignore[attr-defined]
        interrupted_bytes = old_draft_path.read_bytes()
        assert pipeline_factory.created_run_ids == []
        assert not list((project_root / "ui" / "revisions").glob("v*.json"))

        retry_job = service.enqueue_render(  # type: ignore[attr-defined]
            project_id,
            retry_of=old_job_id,
        )
        assert retry_job.retry_of == old_job_id  # type: ignore[attr-defined]
        retry_job_id = _job_id(retry_job)
        retry_draft_path = project_root / "ui" / "working" / f"{retry_job_id}.json"
        assert retry_draft_path.is_relative_to(project_root / "ui")
        retry_draft = json.loads(retry_draft_path.read_text(encoding="utf-8"))
        assert retry_draft["retry_of"] == old_job_id
        assert retry_draft["project_id"] == project_id

        assert pipeline_factory.first_started.wait(timeout=5)
        sibling_job = service.enqueue_render(sibling_id)  # type: ignore[attr-defined]
        assert sibling_job.retry_of is None  # type: ignore[attr-defined]
        pipeline_factory.release.set()
        assert pipeline_factory.all_terminal.wait(timeout=5)
    finally:
        pipeline_factory.release.set()
        service.close()  # type: ignore[attr-defined]

    assert pipeline_factory.started_projects == [project_id, sibling_id]
    assert old_draft_path.read_bytes() == interrupted_bytes
    preserved_old_draft = json.loads(old_draft_path.read_text(encoding="utf-8"))
    assert preserved_old_draft["job_id"] == old_job_id
    assert preserved_old_draft["run_id"] == old_run_id
    assert preserved_old_draft["status"] == "interrupted"
    revision_paths = sorted((project_root / "ui" / "revisions").glob("v*.json"))
    assert len(revision_paths) == 1
    revision = json.loads(revision_paths[0].read_text(encoding="utf-8"))
    assert revision["project_id"] == project_id
    assert revision["job_id"] == _job_id(retry_job)
    assert revision["run_id"] == pipeline_factory.created_run_ids[0]
    assert revision["status"] == "success"
    assert revision["revision_id"] == "v001"
    assert revision_paths[0].is_relative_to(project_root / "ui")


def test_regeneration_retry_preserves_the_interrupted_scene_and_base_run(
    tmp_path: Path,
) -> None:
    project_id = "2026_retry_inputs"
    old_job_id = "job-regeneration-interrupted"
    bootstrap, projects_root, _, _ = _make_service(tmp_path, project_id=project_id)
    project_root = projects_root / project_id
    try:
        _create_confirmed_projects(bootstrap, [project_id])
        _mark_project_ready(project_root)
    finally:
        bootstrap.close()  # type: ignore[attr-defined]

    project = Project.model_validate_json(
        (project_root / "project.json").read_text(encoding="utf-8")
    )
    RevisionStore(project_root).start_working(
        project_id=project_id,
        job_id=old_job_id,
        run_id="run-interrupted",
        status="running",
        base_package_hashes=_project_package_hashes(project_root, project),
        correction="Preserve esta correção",
        scene_id="abertura",
        base_run_id="base-ready",
    )

    service, _, _, _ = _make_service(tmp_path, project_id=project_id)
    try:
        assert service.get_job(old_job_id).state == "interrupted"  # type: ignore[attr-defined]
        with pytest.raises(ValueError, match="retry inputs"):
            service.enqueue_regeneration(  # type: ignore[attr-defined]
                project_id,
                base_run_id="base-ready",
                scene_id="fecho",
                correction="Preserve esta correção",
                retry_of=old_job_id,
            )
    finally:
        service.close()  # type: ignore[attr-defined]
    assert (project_root / "ui" / "index.json").is_relative_to(project_root / "ui")


def test_checkout_moves_only_ui_index_and_preserves_revisions_and_golden(
    tmp_path: Path,
) -> None:
    project_id = "2026_checkout_contract"
    service, projects_root, _, _ = _make_service(tmp_path, project_id=project_id)
    project_root = projects_root / project_id

    try:
        _create_confirmed_projects(service, [project_id])
    finally:
        service.close()  # type: ignore[attr-defined]

    project_model = Project.model_validate_json(
        (project_root / "project.json").read_text(encoding="utf-8")
    )
    package_hashes = _project_package_hashes(project_root, project_model)
    store = RevisionStore(project_root)
    first = store.publish_terminal(
        project_id=project_id,
        job_id="job-checkout-one",
        run_id="run-checkout-one",
        status="success",
        base_package_hashes=package_hashes,
        messages=["first"],
        asset_ids=["audio-narration"],
    )
    second = store.publish_terminal(
        project_id=project_id,
        job_id="job-checkout-two",
        run_id="run-checkout-two",
        status="success",
        base_package_hashes=package_hashes,
        messages=["second"],
        asset_ids=["audio-narration"],
    )
    golden_root = project_root / "golden"
    golden_root.mkdir()
    golden_manifest = golden_root / "manifest.json"
    golden_manifest.write_bytes(b"golden must remain untouched")
    revision_before = _snapshot_files(project_root / "ui" / "revisions")
    golden_before = _snapshot_files(golden_root)

    checkout_service, _, _, _ = _make_service(tmp_path, project_id=project_id)
    try:
        checked_out = checkout_service.checkout_revision(  # type: ignore[attr-defined]
            project_id,
            first.revision_id,
        )
    finally:
        checkout_service.close()  # type: ignore[attr-defined]

    assert checked_out.revision_id == first.revision_id  # type: ignore[attr-defined]
    assert (project_root / "ui" / "index.json").is_relative_to(project_root / "ui")
    assert _snapshot_files(project_root / "ui" / "revisions") == revision_before
    assert _snapshot_files(golden_root) == golden_before
    assert second.revision_id != checked_out.revision_id  # type: ignore[attr-defined]
    assert json.loads((project_root / "ui" / "index.json").read_text(encoding="utf-8"))[
        "current_revision_id"
    ] == first.revision_id


def test_accept_run_rejects_non_ready_project_without_promoting_or_mutating_files(
    tmp_path: Path,
) -> None:
    project_id = "2026_accept_adapter"
    service, projects_root, _, _ = _make_service(tmp_path, project_id=project_id)
    project_root = projects_root / project_id
    try:
        _create_confirmed_projects(service, [project_id])
        golden_root = project_root / "golden"
        golden_root.mkdir()
        golden_manifest = golden_root / "manifest.json"
        golden_manifest.write_bytes(b"golden sentinel")
        project_json = project_root / "project.json"
        timeline_json = project_root / "timeline.json"
        project_before = project_json.read_bytes()
        timeline_before = timeline_json.read_bytes()
        golden_before = _snapshot_files(golden_root)
        ui_before = _snapshot_files(project_root / "ui")
        with pytest.raises(ValueError):
            service.accept_run(project_id, "run-accepted")  # type: ignore[attr-defined]
    finally:
        service.close()  # type: ignore[attr-defined]

    assert project_json.read_bytes() == project_before
    assert timeline_json.read_bytes() == timeline_before
    assert _snapshot_files(golden_root) == golden_before
    assert _snapshot_files(project_root / "ui") == ui_before
    assert golden_manifest.read_bytes() == b"golden sentinel"


def test_accept_run_rejects_active_project_job_before_mutating_any_state(
    tmp_path: Path,
) -> None:
    project_id = "2026_accept_active_job"
    pipeline_factory = _BlockingPipelineFactory(expected_terminal=1)
    service, projects_root, _, _ = _make_service(
        tmp_path,
        project_id=project_id,
        pipeline_factory=pipeline_factory,
    )
    project_root = projects_root / project_id
    job: object | None = None

    try:
        _create_confirmed_projects(service, [project_id])
        _mark_project_ready(project_root)
        job = service.enqueue_regeneration(  # type: ignore[attr-defined]
            project_id,
            base_run_id="base-ready",
            scene_id="abertura",
            correction="Mantenha o job ativo durante a tentativa de aceite",
        )
        assert pipeline_factory.first_started.wait(timeout=5)
        project_before = (project_root / "project.json").read_bytes()
        timeline_before = (project_root / "timeline.json").read_bytes()
        ui_before = _snapshot_files(project_root / "ui")

        with pytest.raises(ValueError, match="active job"):
            service.accept_run(project_id, "base-ready")  # type: ignore[attr-defined]

        assert (project_root / "project.json").read_bytes() == project_before
        assert (project_root / "timeline.json").read_bytes() == timeline_before
        assert _snapshot_files(project_root / "ui") == ui_before
        assert not (project_root / "golden").exists()
    finally:
        pipeline_factory.release.set()
        if job is not None:
            service.wait_job(_job_id(job), timeout=5)  # type: ignore[attr-defined]
        service.close()  # type: ignore[attr-defined]


def test_project_probe_cleanup_never_removes_a_concurrently_published_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import video_pipeline.web.service as service_module

    project_id = "2026_probe_race"
    projects_root = tmp_path / "projects"
    project_root = projects_root / project_id
    audio_root, _ = _make_audio_root(tmp_path)

    class PublishingProbe(FakeAudioProbe):
        def __call__(self, path: Path) -> dict[str, object]:
            facts = super().__call__(path)
            (project_root / "published.txt").write_text("keep", encoding="utf-8")
            return facts

    def fake_initialize(
        destination: Path,
        **options: object,
    ) -> object:
        del destination
        staged = tmp_path / "staged.wav"
        staged.write_bytes(b"audio")
        probe = options["audio_probe"]
        assert callable(probe)
        probe(staged)
        return object()

    monkeypatch.setattr(service_module, "initialize_project", fake_initialize)
    monkeypatch.setattr(
        service_module,
        "inspect_project",
        lambda _: {"project": {"id": project_id}},
    )
    service = service_module.WebService(
        projects_root,
        audio_root,
        audio_probe=PublishingProbe(),
        project_id_factory=lambda: project_id,
    )
    try:
        service.create_project(
            title="Probe race",
            script="# Abertura\nTexto.\n",
            audio_asset_id="audio-narration",
        )
    finally:
        service.close()

    assert (project_root / "published.txt").read_text(encoding="utf-8") == "keep"


def test_unscoped_final_asset_id_is_rejected_for_valid_or_hostile_manifests(
    tmp_path: Path,
) -> None:
    service, projects_root, _, _ = _make_service(tmp_path)
    project_root = projects_root / "2026_web"
    try:
        _create_confirmed_projects(service, ["2026_web"])
        revision_root = project_root / "ui" / "revisions"
        revision_root.mkdir(parents=True)
        (project_root / "ui" / "index.json").write_text(
            json.dumps(
                {
                    "schema_version": "project.ui-revision-index/1",
                    "current_revision_id": "v001",
                }
            ),
            encoding="utf-8",
        )
        revision_path = revision_root / "v001.json"
        revision_path.write_text(
            json.dumps(
                {
                    "schema_version": "project.ui-revision/1",
                    "revision_id": "v001",
                    "project_id": "2026_web",
                    "job_id": "job-one",
                    "run_id": "run-one",
                    "status": "success",
                    "parent_revision_id": None,
                    "base_package_hashes": {},
                    "correction": None,
                    "messages": [],
                    "asset_ids": ["final"],
                }
            ),
            encoding="utf-8",
        )
        artifacts_root = project_root / "artifacts"
        final = artifacts_root / "run-one" / "final.mp4"
        final.parent.mkdir(parents=True)
        final.write_bytes(b"preview")

        with pytest.raises(ValueError, match="asset"):
            service.resolve_asset("final")  # type: ignore[attr-defined]

        outside = project_root / "outside" / "final.mp4"
        outside.parent.mkdir()
        outside.write_bytes(b"outside")
        revision = json.loads(revision_path.read_text(encoding="utf-8"))
        revision["run_id"] = "../outside"
        revision_path.write_text(json.dumps(revision), encoding="utf-8")
        with pytest.raises(ValueError, match="asset"):
            service.resolve_asset("final")  # type: ignore[attr-defined]
    finally:
        service.close()  # type: ignore[attr-defined]


def test_noncanonical_video_result_is_one_failure_revision(tmp_path: Path) -> None:
    class EscapingPipeline:
        def __init__(self, project_root: Path) -> None:
            self.project_root = project_root

        def render(self, project_path: Path, **_: object) -> VideoResult:
            del project_path
            outside = self.project_root / "outside"
            outside.mkdir()
            output = outside / "final.mp4"
            output.write_bytes(b"not canonical")
            return VideoResult(state="ready", run_path=outside, output_path=output)

    project_id = "2026_result_guard"
    projects_root = tmp_path / "projects"
    project_root = projects_root / project_id
    service, _, _, _ = _make_service(
        tmp_path,
        project_id=project_id,
        pipeline_factory=lambda _: EscapingPipeline(project_root),
    )
    try:
        _create_confirmed_projects(service, [project_id])
        job = service.enqueue_render(project_id)  # type: ignore[attr-defined]
        terminal = service.wait_job(_job_id(job), timeout=5)  # type: ignore[attr-defined]
    finally:
        service.close()  # type: ignore[attr-defined]

    assert terminal.state == "failure"  # type: ignore[attr-defined]
    revisions = sorted((project_root / "ui" / "revisions").glob("v*.json"))
    assert len(revisions) == 1
    assert json.loads(revisions[0].read_text(encoding="utf-8"))["status"] == "failure"


def test_recovery_rejects_duplicate_job_identity_across_projects(tmp_path: Path) -> None:
    project_ids = ["2026_collision_one", "2026_collision_two"]
    bootstrap, projects_root, _, _ = _make_service(tmp_path, project_ids=project_ids)
    try:
        _create_confirmed_projects(bootstrap, project_ids)
    finally:
        bootstrap.close()  # type: ignore[attr-defined]

    for project_id in project_ids:
        project_root = projects_root / project_id
        project = Project.model_validate_json(
            (project_root / "project.json").read_text(encoding="utf-8")
        )
        RevisionStore(project_root).start_working(
            project_id=project_id,
            job_id="job-collision",
            run_id=f"run-{project_id}",
            status="running",
            base_package_hashes=_project_package_hashes(project_root, project),
            asset_ids=["audio-narration"],
        )

    with pytest.raises(ValueError, match="job|collision"):
        _make_service(tmp_path, project_ids=project_ids)


def test_run_id_collision_is_rejected_before_draft_or_pipeline(tmp_path: Path) -> None:
    project_id = "2026_run_collision"
    pipeline_factory = _BlockingPipelineFactory(expected_terminal=1)
    service, projects_root, _, _ = _make_service(
        tmp_path,
        project_id=project_id,
        pipeline_factory=pipeline_factory,
        run_id_factory=lambda: "run-collision",
    )
    project_root = projects_root / project_id
    try:
        _create_confirmed_projects(service, [project_id])
        (project_root / "artifacts" / "run-collision").mkdir(parents=True)
        with pytest.raises(ValueError, match="run|collision"):
            service.enqueue_render(project_id)  # type: ignore[attr-defined]
    finally:
        pipeline_factory.release.set()
        service.close()  # type: ignore[attr-defined]

    assert pipeline_factory.created_run_ids == []
    assert not (project_root / "ui" / "working").exists()


def test_recovery_rejects_stem_mismatch_before_mutating_draft(tmp_path: Path) -> None:
    project_id = "2026_recovery_stem"
    bootstrap, projects_root, _, _ = _make_service(tmp_path, project_id=project_id)
    project_root = projects_root / project_id
    try:
        _create_confirmed_projects(bootstrap, [project_id])
    finally:
        bootstrap.close()  # type: ignore[attr-defined]
    project = Project.model_validate_json(
        (project_root / "project.json").read_text(encoding="utf-8")
    )
    store = RevisionStore(project_root)
    store.start_working(
        project_id=project_id,
        job_id="job-document",
        run_id="run-document",
        status="running",
        base_package_hashes=_project_package_hashes(project_root, project),
        asset_ids=["audio-narration"],
    )
    original = project_root / "ui" / "working" / "job-document.json"
    mismatched = project_root / "ui" / "working" / "job-filename.json"
    original.rename(mismatched)
    before = mismatched.read_bytes()

    with pytest.raises(ValueError, match="identity|stem|job"):
        _make_service(tmp_path, project_id=project_id)

    assert mismatched.read_bytes() == before


def test_video_result_rejects_symlinked_artifacts_ancestor(tmp_path: Path) -> None:
    class SymlinkPipeline:
        def __init__(self, project_root: Path, outside: Path, run_id: str) -> None:
            self.project_root = project_root
            self.outside = outside
            self.run_id = run_id

        def render(self, project_path: Path, **_: object) -> VideoResult:
            del project_path
            self.outside.mkdir()
            (self.project_root / "artifacts").symlink_to(self.outside, target_is_directory=True)
            run_path = self.project_root / "artifacts" / self.run_id
            run_path.mkdir()
            output = run_path / "final.mp4"
            output.write_bytes(b"external")
            return VideoResult(state="ready", run_path=run_path, output_path=output)

    project_id = "2026_result_symlink"
    run_id = "run-symlink"
    project_root = tmp_path / "projects" / project_id
    outside = tmp_path / "outside-artifacts"
    service, _, _, _ = _make_service(
        tmp_path,
        project_id=project_id,
        pipeline_factory=lambda _: SymlinkPipeline(project_root, outside, run_id),
        run_id_factory=lambda: run_id,
    )
    try:
        _create_confirmed_projects(service, [project_id])
        job = service.enqueue_render(project_id)  # type: ignore[attr-defined]
        terminal = service.wait_job(_job_id(job), timeout=5)  # type: ignore[attr-defined]
    finally:
        service.close()  # type: ignore[attr-defined]

    assert terminal.state == "failure"  # type: ignore[attr-defined]
    revision = json.loads(
        (project_root / "ui" / "revisions" / "v001.json").read_text(encoding="utf-8")
    )
    assert revision["status"] == "failure"


def test_project_probe_setup_failure_removes_only_owned_residue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import video_pipeline.web.service as service_module

    project_id = "2026_probe_setup"
    projects_root = tmp_path / "projects"
    project_root = projects_root / project_id
    audio_root, _ = _make_audio_root(tmp_path)
    original_read_bytes = Path.read_bytes

    def failing_read(path: Path) -> bytes:
        if path == tmp_path / "staged.wav":
            raise OSError("injected probe write failure")
        return original_read_bytes(path)

    def fake_initialize(destination: Path, **options: object) -> object:
        del destination
        staged = tmp_path / "staged.wav"
        staged.write_bytes(b"audio")
        probe = options["audio_probe"]
        assert callable(probe)
        probe(staged)
        return object()

    monkeypatch.setattr(service_module, "initialize_project", fake_initialize)
    monkeypatch.setattr(Path, "read_bytes", failing_read)
    service = service_module.WebService(
        projects_root,
        audio_root,
        audio_probe=FakeAudioProbe(),
        project_id_factory=lambda: project_id,
    )
    try:
        with pytest.raises(OSError, match="injected"):
            service.create_project(
                title="Probe setup",
                script="# Abertura\nTexto.\n",
                audio_asset_id="audio-narration",
            )
    finally:
        service.close()

    assert not project_root.exists()


def test_inspection_rejects_symlinked_metadata_ancestors(tmp_path: Path) -> None:
    service, projects_root, _, _ = _make_service(tmp_path)
    project_root = projects_root / "2026_web"
    external_ui = tmp_path / "external-ui"
    revisions = external_ui / "revisions"
    revisions.mkdir(parents=True)
    (external_ui / "index.json").write_text(
        json.dumps(
            {
                "schema_version": "project.ui-revision-index/1",
                "current_revision_id": "v001",
            }
        ),
        encoding="utf-8",
    )
    (revisions / "v001.json").write_text(
        json.dumps(
            {
                "schema_version": "project.ui-revision/1",
                "revision_id": "v001",
                "project_id": "2026_web",
                "job_id": "job-one",
                "run_id": "run-one",
                "status": "success",
                "parent_revision_id": None,
                "base_package_hashes": {},
                "correction": None,
                "messages": [],
                "asset_ids": ["final"],
            }
        ),
        encoding="utf-8",
    )
    try:
        _create_confirmed_projects(service, ["2026_web"])
        (project_root / "ui").symlink_to(external_ui, target_is_directory=True)
        final = project_root / "artifacts" / "run-one" / "final.mp4"
        final.parent.mkdir(parents=True)
        final.write_bytes(b"preview")
        with pytest.raises(ValueError, match="ui|directory|project"):
            service.inspect("2026_web")  # type: ignore[attr-defined]
    finally:
        service.close()  # type: ignore[attr-defined]


def test_restart_ignores_terminal_draft_and_preserves_all_ui_bytes(
    tmp_path: Path,
) -> None:
    project_id = "2026_terminal_restart"
    pipeline_factory = _BlockingPipelineFactory(expected_terminal=1)
    pipeline_factory.release.set()
    service, projects_root, _, _ = _make_service(
        tmp_path,
        project_id=project_id,
        pipeline_factory=pipeline_factory,
        job_id_factory=lambda: "job-terminal",
        run_id_factory=lambda: "run-terminal",
    )
    project_root = projects_root / project_id
    try:
        _create_confirmed_projects(service, [project_id])
        job = service.enqueue_render(project_id)  # type: ignore[attr-defined]
        terminal = service.wait_job(_job_id(job), timeout=5)  # type: ignore[attr-defined]
        assert terminal.state == "success"  # type: ignore[attr-defined]
    finally:
        service.close()  # type: ignore[attr-defined]
    ui_before = _snapshot_files(project_root / "ui")

    restarted, _, _, _ = _make_service(tmp_path, project_id=project_id)
    try:
        with pytest.raises(ValueError, match="unknown job"):
            restarted.get_job("job-terminal")  # type: ignore[attr-defined]
    finally:
        restarted.close()  # type: ignore[attr-defined]

    assert _snapshot_files(project_root / "ui") == ui_before


def test_operation_state_gates_reject_without_job_draft_or_pipeline(
    tmp_path: Path,
) -> None:
    project_ids = ["2026_gate_regenerate", "2026_gate_render"]
    pipeline_factory = _BlockingPipelineFactory(expected_terminal=1)
    service, projects_root, _, _ = _make_service(
        tmp_path,
        project_ids=project_ids,
        pipeline_factory=pipeline_factory,
    )
    try:
        _create_confirmed_projects(service, project_ids)
        with pytest.raises(ValueError, match="ready"):
            service.enqueue_regeneration(  # type: ignore[attr-defined]
                project_ids[0],
                base_run_id="run-base",
                scene_id="abertura",
                correction="Aumente o contraste",
            )
        ready_root = projects_root / project_ids[1]
        _mark_project_ready(ready_root)
        with pytest.raises(ValueError, match="confirmed"):
            service.enqueue_render(project_ids[1])  # type: ignore[attr-defined]
    finally:
        pipeline_factory.release.set()
        service.close()  # type: ignore[attr-defined]

    assert pipeline_factory.created_run_ids == []
    for project_id in project_ids:
        assert not (projects_root / project_id / "ui").exists()
