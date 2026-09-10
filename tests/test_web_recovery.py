"""Recovery tests that preserve the canonical project state after a crash."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from threading import Event

from test_web_service import (
    _create_confirmed_projects,
    _make_service,
    _mark_project_ready,
)

from video_pipeline.project import Project, _project_package_hashes
from video_pipeline.revisions import RevisionStore
from video_pipeline.video import VideoResult


class _CrashFactory:
    def __init__(self) -> None:
        self.started = Event()
        self.run_ids: list[str] = []

    def __call__(self, run_id: str) -> _CrashPipeline:
        self.run_ids.append(run_id)
        return _CrashPipeline(run_id, self.started)


class _CrashPipeline:
    def __init__(self, run_id: str, started: Event) -> None:
        self.run_id = run_id
        self.started = started

    def render(
        self,
        project_path: str | Path,
        *,
        max_attempts: int = 3,
        scene: str | None = None,
        base_run_id: str | None = None,
        correction: str | None = None,
        on_progress: Callable[[object], None] | None = None,
    ) -> VideoResult:
        del max_attempts, scene, base_run_id, correction, on_progress
        project_file = Path(project_path)
        document = json.loads(project_file.read_text(encoding="utf-8"))
        document.update(
            {
                "status": "rendering",
                "current_run": self.run_id,
                "current_scene": None,
                "render_state": "pending",
                "composition_state": "pending",
            }
        )
        project_file.write_text(json.dumps(document), encoding="utf-8")
        run_path = project_file.parent / "artifacts" / self.run_id
        run_path.mkdir(parents=True, exist_ok=True)
        (run_path / "run.json").write_text(
            json.dumps(
                {
                    "schema_version": "project.render-run/1",
                    "run_id": self.run_id,
                    "project_id": project_file.parent.name,
                    "state": "rendering",
                    "current_scene": None,
                }
            ),
            encoding="utf-8",
        )
        self.started.set()
        raise KeyboardInterrupt("simulated worker interruption")


class _ResumeFactory:
    def __init__(self) -> None:
        self.run_ids: list[str] = []
        self.calls: list[dict[str, object]] = []

    def __call__(self, run_id: str) -> _ResumePipeline:
        self.run_ids.append(run_id)
        return _ResumePipeline(run_id, self.calls)


class _ResumePipeline:
    def __init__(self, run_id: str, calls: list[dict[str, object]]) -> None:
        self.run_id = run_id
        self.calls = calls

    def render(
        self,
        project_path: str | Path,
        *,
        max_attempts: int = 3,
        scene: str | None = None,
        base_run_id: str | None = None,
        correction: str | None = None,
        on_progress: Callable[[object], None] | None = None,
    ) -> VideoResult:
        del max_attempts, on_progress
        project_file = Path(project_path)
        self.calls.append(
            {
                "scene": scene,
                "base_run_id": base_run_id,
                "correction": correction,
            }
        )
        document = json.loads(project_file.read_text(encoding="utf-8"))
        document.update(
            {
                "status": "ready",
                "current_run": self.run_id,
                "current_scene": None,
                "render_state": "ready",
                "composition_state": "ready",
            }
        )
        project_file.write_text(json.dumps(document), encoding="utf-8")
        run_path = project_file.parent / "artifacts" / self.run_id
        run_path.mkdir(parents=True, exist_ok=True)
        (run_path / "run.json").write_text(
            json.dumps(
                {
                    "schema_version": "project.render-run/1",
                    "run_id": self.run_id,
                    "project_id": project_file.parent.name,
                    "state": "ready",
                    "current_scene": None,
                }
            ),
            encoding="utf-8",
        )
        output_path = run_path / "final.mp4"
        output_path.write_bytes(b"resumed fake final")
        return VideoResult(state="ready", run_path=run_path, output_path=output_path)


def _no_run_factory() -> str:
    raise AssertionError("a full retry should continue the interrupted run")


def test_full_retry_resumes_a_run_after_project_switched_to_rendering(tmp_path: Path) -> None:
    project_id = "2026_real_recovery"
    crash_factory = _CrashFactory()
    service, projects_root, _, _ = _make_service(
        tmp_path,
        project_id=project_id,
        pipeline_factory=crash_factory,
        run_id_factory=lambda: "run-crashed",
    )
    _create_confirmed_projects(service, [project_id])
    first = service.enqueue_render(project_id)
    assert crash_factory.started.wait(timeout=5)
    service.close()

    project_root = projects_root / project_id
    project_document = json.loads(
        (project_root / "project.json").read_text(encoding="utf-8")
    )
    assert project_document["status"] == "rendering"
    assert project_document["current_run"] == first.run_id

    resume_factory = _ResumeFactory()
    resumed, _, _, _ = _make_service(
        tmp_path,
        project_id=project_id,
        pipeline_factory=resume_factory,
        run_id_factory=_no_run_factory,
    )
    try:
        assert resumed.get_job(first.job_id).state == "interrupted"
        retry = resumed.enqueue_render(project_id, retry_of=first.job_id)
        assert retry.run_id == first.run_id
        terminal = resumed.wait_job(retry.job_id, timeout=5)
        assert terminal.state == "success"
        assert resume_factory.run_ids == [first.run_id]
        assert resume_factory.calls == [
            {"scene": None, "base_run_id": None, "correction": None}
        ]
    finally:
        resumed.close()


def test_regeneration_retry_allows_rendering_project_and_allocates_fresh_run(
    tmp_path: Path,
) -> None:
    project_id = "2026_selective_recovery"
    bootstrap, projects_root, _, _ = _make_service(tmp_path, project_id=project_id)
    _create_confirmed_projects(bootstrap, [project_id])
    project_root = projects_root / project_id
    _mark_project_ready(project_root, run_id="base-ready")
    project = Project.model_validate_json(
        (project_root / "project.json").read_text(encoding="utf-8")
    )
    old_run_id = "run-regeneration-crashed"
    old_job_id = "job-regeneration-crashed"
    RevisionStore(project_root).start_working(
        project_id=project_id,
        job_id=old_job_id,
        run_id=old_run_id,
        status="running",
        base_package_hashes=_project_package_hashes(project_root, project),
        correction="Ajuste seletivo",
        scene_id="abertura",
        base_run_id="base-ready",
    )
    old_run_path = project_root / "artifacts" / old_run_id
    old_run_path.mkdir(parents=True)
    (old_run_path / "run.json").write_text(
        json.dumps(
            {
                "schema_version": "project.render-run/1",
                "run_id": old_run_id,
                "project_id": project_id,
                "state": "rendering",
                "current_scene": "abertura",
            }
        ),
        encoding="utf-8",
    )
    project_document = json.loads(
        (project_root / "project.json").read_text(encoding="utf-8")
    )
    project_document.update(
        {
            "status": "rendering",
            "current_run": old_run_id,
            "current_scene": "abertura",
            "render_state": "pending",
            "composition_state": "pending",
        }
    )
    (project_root / "project.json").write_text(
        json.dumps(project_document),
        encoding="utf-8",
    )
    bootstrap.close()

    resume_factory = _ResumeFactory()
    service, _, _, _ = _make_service(
        tmp_path,
        project_id=project_id,
        pipeline_factory=resume_factory,
    )
    try:
        assert service.get_job(old_job_id).state == "interrupted"
        retry = service.enqueue_regeneration(
            project_id,
            base_run_id="base-ready",
            scene_id="abertura",
            correction="Ajuste seletivo",
            retry_of=old_job_id,
        )
        assert retry.run_id != old_run_id
        terminal = service.wait_job(retry.job_id, timeout=5)
        assert terminal.state == "success"
        assert resume_factory.run_ids == [retry.run_id]
        assert resume_factory.calls == [
            {
                "scene": "abertura",
                "base_run_id": "base-ready",
                "correction": "Ajuste seletivo",
            }
        ]
    finally:
        service.close()
