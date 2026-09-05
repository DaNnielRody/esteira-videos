"""Durable revision recovery and identity contracts."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from test_web_service import (
    _BlockingPipelineFactory,
    _create_confirmed_projects,
    _make_service,
)

from video_pipeline.revisions import RevisionManifest, RevisionStore, WorkingDraft
from video_pipeline.web import WebService


def _draft_status(project_root: Path, job_id: str) -> str:
    document = json.loads(
        (project_root / "ui" / "working" / f"{job_id}.json").read_text(encoding="utf-8")
    )
    return document["status"]


def test_recovery_reconciles_terminal_revision_before_marking_draft_interrupted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crash after revision/index commit must not turn a completed job into retry."""

    project_root = tmp_path / "2026_recovery_integrity"
    project_root.mkdir()
    store = RevisionStore(project_root)
    store.start_working(
        project_id=project_root.name,
        job_id="job-terminal-race",
        run_id="run-terminal-race",
        status="queued",
        base_package_hashes={},
    )

    draft_path = project_root / "ui" / "working" / "job-terminal-race.json"
    original_replace = os.replace

    def crash_after_terminal_commit(
        source: str | os.PathLike[str],
        destination: str | os.PathLike[str],
    ) -> None:
        if Path(destination) == draft_path:
            raise OSError("crash after revision and index commit")
        original_replace(source, destination)

    monkeypatch.setattr(os, "replace", crash_after_terminal_commit)
    with pytest.raises(OSError, match="revision and index"):
        store.publish_terminal(
            project_id=project_root.name,
            job_id="job-terminal-race",
            run_id="run-terminal-race",
            status="success",
            base_package_hashes={},
        )
    monkeypatch.undo()

    service = WebService(project_root.parent, tmp_path / "audio")
    try:
        listed = next(item for item in service.list_jobs() if item["job_id"] == "job-terminal-race")
        assert listed["state"] == "success"
        assert listed["revision_id"] == "v001"
    finally:
        service.close()

    restarted = RevisionStore(project_root)
    assert restarted.recover_interrupted() == []
    assert _draft_status(project_root, "job-terminal-race") == "success"
    assert restarted.current_revision() is not None
    assert restarted.current_revision().revision_id == "v001"  # type: ignore[union-attr]


def test_idempotent_terminal_publish_repairs_only_a_missing_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retry after an index-write crash repairs the unambiguous first pointer."""

    project_root = tmp_path / "2026_missing_index"
    project_root.mkdir()
    store = RevisionStore(project_root)
    store.start_working(
        project_id=project_root.name,
        job_id="job-index-race",
        run_id="run-index-race",
        status="queued",
        base_package_hashes={},
    )

    index_path = project_root / "ui" / "index.json"
    original_replace = os.replace

    def crash_before_index(
        source: str | os.PathLike[str],
        destination: str | os.PathLike[str],
    ) -> None:
        if Path(destination) == index_path:
            raise KeyboardInterrupt("crash before index commit")
        original_replace(source, destination)

    monkeypatch.setattr(os, "replace", crash_before_index)
    with pytest.raises(KeyboardInterrupt, match="index commit"):
        store.publish_terminal(
            project_id=project_root.name,
            job_id="job-index-race",
            run_id="run-index-race",
            status="success",
            base_package_hashes={},
        )
    monkeypatch.undo()

    repaired = store.publish_terminal(
        project_id=project_root.name,
        job_id="job-index-race",
        run_id="run-index-race",
        status="success",
        base_package_hashes={},
    )
    assert repaired.revision_id == "v001"
    assert store.load_index().current_revision_id == "v001"
    assert _draft_status(project_root, "job-index-race") == "success"


def test_idempotent_terminal_publish_preserves_an_existing_checkout(
    tmp_path: Path,
) -> None:
    """Repair logic must not undo a valid revision selected by the operator."""

    project_root = tmp_path / "2026_existing_index"
    project_root.mkdir()
    store = RevisionStore(project_root)
    store.start_working(
        project_id=project_root.name,
        job_id="job-selected",
        run_id="run-selected",
        status="queued",
        base_package_hashes={},
    )
    first = store.publish_terminal(
        project_id=project_root.name,
        job_id="job-selected",
        run_id="run-selected",
        status="success",
        base_package_hashes={},
    )
    store.publish_terminal(
        project_id=project_root.name,
        job_id="job-later",
        run_id="run-later",
        status="success",
        base_package_hashes={},
    )
    store.checkout(first.revision_id)
    store.publish_terminal(
        project_id=project_root.name,
        job_id="job-selected",
        run_id="run-selected",
        status="success",
        base_package_hashes={},
    )

    assert store.load_index().current_revision_id == first.revision_id


def test_missing_index_does_not_guess_from_an_orphaned_later_revision(
    tmp_path: Path,
) -> None:
    """Recovery repairs only the unambiguous first revision, never a later orphan."""

    project_root = tmp_path / "2026_orphaned_revision"
    project_root.mkdir()
    store = RevisionStore(project_root)
    store.publish_terminal(
        project_id=project_root.name,
        job_id="job-first",
        run_id="run-first",
        status="success",
        base_package_hashes={},
    )
    store.publish_terminal(
        project_id=project_root.name,
        job_id="job-later",
        run_id="run-later",
        status="success",
        base_package_hashes={},
    )
    (project_root / "ui" / "revisions" / "v001.json").unlink()
    (project_root / "ui" / "index.json").unlink()

    store.recover_interrupted()

    assert store.load_index().current_revision_id is None


def test_retrying_the_same_failed_job_allocates_a_distinct_run_each_time(
    tmp_path: Path,
) -> None:
    """The retry action on an old failure remains usable after another retry."""

    failing_factory = _BlockingPipelineFactory(
        expected_terminal=1,
        failure_message="controlled failure",
    )
    first_service, _, _, _ = _make_service(
        tmp_path,
        project_id="2026_retry_identity",
        pipeline_factory=failing_factory,
        job_id_factory=lambda: "job-source",
        run_id_factory=lambda: "run-source",
    )
    try:
        _create_confirmed_projects(first_service, ["2026_retry_identity"])
        source = first_service.enqueue_render("2026_retry_identity")
        assert first_service.wait_job(source.job_id, timeout=5).state == "failure"
    finally:
        first_service.close()

    first_retry_factory = _BlockingPipelineFactory(expected_terminal=1)
    first_retry_factory.release.set()
    first_retry_service, _, _, _ = _make_service(
        tmp_path,
        project_id="2026_retry_identity",
        pipeline_factory=first_retry_factory,
        job_id_factory=lambda: "job-source-abc",
    )
    try:
        first_retry = first_retry_service.retry_job("job-source")
        assert first_retry_service.wait_job(first_retry.job_id, timeout=5).state == "success"
        assert first_retry.run_id == f"run-{first_retry.job_id}"
        first_retry_run = first_retry.run_id
    finally:
        first_retry_service.close()

    second_retry_factory = _BlockingPipelineFactory(expected_terminal=1)
    second_retry_factory.release.set()
    second_retry_service, _, _, _ = _make_service(
        tmp_path,
        project_id="2026_retry_identity",
        pipeline_factory=second_retry_factory,
        job_id_factory=lambda: "abc",
    )
    try:
        second_retry = second_retry_service.retry_job("job-source")
        assert second_retry.run_id == f"run-{second_retry.job_id}"
        assert second_retry.run_id != first_retry_run
        assert second_retry_service.wait_job(second_retry.job_id, timeout=5).state == "success"
    finally:
        second_retry_service.close()


def test_retry_run_ids_do_not_accumulate_source_lineage() -> None:
    """A long retry chain keeps each run name bounded by its new job ID."""

    source = WorkingDraft(
        project_id="2026_retry_identity",
        job_id="job-source",
        run_id="run-source",
        status="failure",
        parent_revision_id=None,
        base_package_hashes={},
        correction=None,
        messages=[],
        asset_ids=[],
    )
    for attempt in range(12):
        new_job_id = f"job-retry-{attempt:02d}"
        retry_run_id = source.retry_run_id(new_job_id)
        assert retry_run_id == f"run-{new_job_id}"
        source = replace(source, job_id=new_job_id, run_id=retry_run_id)

    assert len(source.run_id) < 64


def test_checkout_rejects_a_revision_from_another_project_without_moving_index(
    tmp_path: Path,
) -> None:
    """A project-local checkout cannot select a manifest with alien identity."""

    project_root = tmp_path / "2026_checkout_identity"
    project_root.mkdir()
    store = RevisionStore(project_root)
    first = store.publish_terminal(
        project_id=project_root.name,
        job_id="job-local",
        run_id="run-local",
        status="success",
        base_package_hashes={},
    )
    alien = RevisionManifest(
        revision_id="v002",
        project_id="2026_other_project",
        job_id="job-alien",
        run_id="run-alien",
        status="success",
        parent_revision_id=first.revision_id,
        base_package_hashes={},
        correction=None,
        messages=[],
        asset_ids=[],
    )
    (project_root / "ui" / "revisions" / "v002.json").write_text(
        json.dumps(alien.to_document()),
        encoding="utf-8",
    )
    store.checkout(first.revision_id)

    with pytest.raises(ValueError, match="project"):
        store.checkout("v002")

    assert store.load_index().current_revision_id == first.revision_id
