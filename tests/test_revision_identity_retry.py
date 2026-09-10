"""Public identity and retry-run collision contracts for durable revisions."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_web_service import (
    _BlockingPipelineFactory,
    _create_confirmed_projects,
    _make_service,
)

from video_pipeline.revisions import RevisionStore
from video_pipeline.web_errors import StateConflictError


def test_publish_terminal_rejects_a_foreign_project_before_persisting(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "2026_local_project"
    project_root.mkdir()
    store = RevisionStore(project_root)

    with pytest.raises(ValueError, match="project"):
        store.publish_terminal(
            project_id="2026_other_project",
            job_id="job-alien",
            run_id="run-alien",
            status="success",
            base_package_hashes={},
        )

    assert not list((project_root / "ui" / "revisions").glob("*.json"))
    assert not (project_root / "ui" / "index.json").exists()


def test_start_working_rejects_a_foreign_project_before_persisting(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "2026_local_project"
    project_root.mkdir()
    store = RevisionStore(project_root)

    with pytest.raises(ValueError, match="project"):
        store.start_working(
            project_id="2026_other_project",
            job_id="job-alien",
            run_id="run-alien",
            status="queued",
            base_package_hashes={},
        )

    assert not list((project_root / "ui" / "working").glob("*.json"))


def test_revision_retry_rejects_a_run_id_already_owned_by_another_draft(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "2026_retry_project"
    project_root.mkdir()
    store = RevisionStore(project_root)
    store.start_working(
        project_id=project_root.name,
        job_id="job-source",
        run_id="run-source",
        status="queued",
        base_package_hashes={},
    )
    store.start_working(
        project_id=project_root.name,
        job_id="job-existing",
        run_id="run-job-retry",
        status="queued",
        base_package_hashes={},
    )
    store.recover_interrupted()

    with pytest.raises(StateConflictError, match="run"):
        store.retry("job-source", new_job_id="job-retry")

    assert not (project_root / "ui" / "working" / "job-retry.json").exists()


def test_web_retry_rejects_reusing_source_run_without_artifact(
    tmp_path: Path,
) -> None:
    project_id = "2026_retry_source_collision"
    failing_factory = _BlockingPipelineFactory(
        expected_terminal=1,
        failure_message="controlled failure",
    )
    service, projects_root, _, _ = _make_service(
        tmp_path,
        project_id=project_id,
        pipeline_factory=failing_factory,
        job_id_factory=lambda: "job-source",
        run_id_factory=lambda: "run-job-retry",
    )
    try:
        _create_confirmed_projects(service, [project_id])
        source = service.enqueue_render(project_id)  # type: ignore[attr-defined]
        assert service.wait_job(source.job_id, timeout=5).state == "failure"  # type: ignore[attr-defined]
    finally:
        service.close()  # type: ignore[attr-defined]

    project_root = projects_root / project_id
    assert not (project_root / "artifacts" / source.run_id).exists()

    retry_factory = _BlockingPipelineFactory(expected_terminal=1)
    retry_factory.release.set()
    restarted, _, _, _ = _make_service(
        tmp_path,
        project_id=project_id,
        pipeline_factory=retry_factory,
        job_id_factory=lambda: "job-retry",
    )
    try:
        with pytest.raises(StateConflictError, match="run"):
            restarted.retry_job(source.job_id)  # type: ignore[attr-defined]
        assert retry_factory.created_run_ids == []
        assert not (project_root / "ui" / "working" / "job-retry.json").exists()
    finally:
        restarted.close()  # type: ignore[attr-defined]
