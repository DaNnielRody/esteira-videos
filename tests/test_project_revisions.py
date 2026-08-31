"""Behavioral RED for immutable UI revision manifests and checkout branches."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
from dataclasses import asdict, is_dataclass
from pathlib import Path

import pytest
from test_project_lifecycle import _initialize_confirmed_project

PROJECT_ID = "2026_lifecycle"
BASE_PACKAGE_HASHES = {
    "abertura": hashlib.sha256(b"package-abertura").hexdigest(),
    "explicacao": hashlib.sha256(b"package-explicacao").hexdigest(),
}


def _require_revision_contract() -> tuple[
    type[object], type[object], type[object], type[object]
]:
    """Keep a missing public revisions seam as a behavioral RED."""

    try:
        module = importlib.import_module("video_pipeline.revisions")
        public_module = importlib.import_module("video_pipeline")
    except (ImportError, ModuleNotFoundError):
        pytest.fail("SELECTIVE_REVISION_CONTRACT_MISSING")

    store_type = getattr(module, "RevisionStore", None)
    revision_type = getattr(module, "RevisionManifest", None)
    index_type = getattr(module, "RevisionIndex", None)
    draft_type = getattr(module, "WorkingDraft", None)
    if not all(
        isinstance(candidate, type)
        for candidate in (store_type, revision_type, index_type, draft_type)
    ) or any(
        getattr(public_module, name, None) is not candidate
        for name, candidate in (
            ("RevisionStore", store_type),
            ("RevisionManifest", revision_type),
            ("RevisionIndex", index_type),
            ("WorkingDraft", draft_type),
        )
    ):
        pytest.fail("SELECTIVE_REVISION_CONTRACT_MISSING")
    required_methods = (
        "publish_terminal",
        "checkout",
        "load_index",
        "start_working",
        "recover_interrupted",
        "retry",
    )
    if any(not callable(getattr(store_type, name, None)) for name in required_methods):
        pytest.fail("SELECTIVE_REVISION_CONTRACT_MISSING")
    return store_type, revision_type, index_type, draft_type


def _project(tmp_path: Path) -> Path:
    project, _ = _initialize_confirmed_project(tmp_path)
    return project


def _store(store_type: type[object], project_root: Path) -> object:
    return store_type(project_root=project_root)  # type: ignore[call-arg]


def _publish(
    store: object,
    *,
    job_id: str,
    run_id: str,
    status: str,
    correction: str | None = None,
    messages: list[str] | None = None,
    asset_ids: list[str] | None = None,
) -> object:
    return store.publish_terminal(  # type: ignore[attr-defined]
        project_id=PROJECT_ID,
        job_id=job_id,
        run_id=run_id,
        status=status,
        base_package_hashes=dict(BASE_PACKAGE_HASHES),
        correction=correction,
        messages=list(messages or []),
        asset_ids=list(asset_ids or []),
    )


def _document(value: object) -> dict[str, object]:
    if hasattr(value, "to_document"):
        document = value.to_document()  # type: ignore[attr-defined]
    elif hasattr(value, "model_dump"):
        document = value.model_dump(mode="json")  # type: ignore[attr-defined]
    elif is_dataclass(value):
        document = asdict(value)
    else:
        document = vars(value)
    assert isinstance(document, dict)
    return document


def _json_document(path: Path) -> dict[str, object]:
    document = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _snapshot(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _revision_snapshot(project_root: Path) -> dict[str, bytes]:
    return _snapshot(project_root / "ui" / "revisions")


def _assert_no_host_paths(document: object, project_root: Path) -> None:
    serialized = json.dumps(document, ensure_ascii=False, default=str)
    assert str(project_root.resolve()) not in serialized


def _assert_index_only_pointer(project_root: Path, expected: str) -> dict[str, object]:
    index_path = project_root / "ui" / "index.json"
    document = _json_document(index_path)
    assert set(document) == {"schema_version", "current_revision_id"}
    assert document["schema_version"] == "project.ui-revision-index/1"
    assert document["current_revision_id"] == expected
    return document


def test_terminal_jobs_create_typed_complete_revisions_and_minimal_index(
    tmp_path: Path,
) -> None:
    store_type, revision_type, index_type, _ = _require_revision_contract()
    project_root = _project(tmp_path)
    project_json = project_root / "project.json"
    timeline_json = project_root / "timeline.json"
    project_bytes = project_json.read_bytes()
    timeline_bytes = timeline_json.read_bytes()
    golden_root = project_root / "golden"
    golden_root.mkdir(parents=True)
    (golden_root / "manifest.json").write_bytes(b"golden-before-revisions")
    golden_snapshot = _snapshot(golden_root)

    store = _store(store_type, project_root)
    success = _publish(
        store,
        job_id="job-001",
        run_id="run-001",
        status="success",
        messages=["render completed"],
        asset_ids=["asset-final-001"],
    )
    failure = _publish(
        store,
        job_id="job-002",
        run_id="run-002",
        status="failure",
        correction="rebuild the opening scene",
        messages=["validation failed", "retry is available"],
        asset_ids=["asset-diagnostic-002"],
    )

    assert isinstance(success, revision_type)
    assert isinstance(failure, revision_type)
    index = store.load_index()
    assert isinstance(index, index_type)
    assert _document(index)["current_revision_id"] == "v002"
    for revision, expected in (
        (
            success,
            {
                "revision_id": "v001",
                "project_id": PROJECT_ID,
                "job_id": "job-001",
                "run_id": "run-001",
                "status": "success",
                "parent_revision_id": None,
                "base_package_hashes": BASE_PACKAGE_HASHES,
                "correction": None,
                "messages": ["render completed"],
                "asset_ids": ["asset-final-001"],
            },
        ),
        (
            failure,
            {
                "revision_id": "v002",
                "project_id": PROJECT_ID,
                "job_id": "job-002",
                "run_id": "run-002",
                "status": "failure",
                "parent_revision_id": "v001",
                "base_package_hashes": BASE_PACKAGE_HASHES,
                "correction": "rebuild the opening scene",
                "messages": ["validation failed", "retry is available"],
                "asset_ids": ["asset-diagnostic-002"],
            },
        ),
    ):
        document = _document(revision)
        assert document["schema_version"] == "project.ui-revision/1"
        for field, value in expected.items():
            assert document[field] == value
        persisted = _json_document(
            project_root / "ui" / "revisions" / f"{expected['revision_id']}.json"
        )
        assert persisted["schema_version"] == "project.ui-revision/1"
        for field, value in expected.items():
            assert persisted[field] == value
        _assert_no_host_paths(document, project_root)
        _assert_no_host_paths(persisted, project_root)

    _assert_index_only_pointer(project_root, "v002")
    assert project_json.read_bytes() == project_bytes
    assert timeline_json.read_bytes() == timeline_bytes
    assert _snapshot(golden_root) == golden_snapshot

    revision_snapshot = _revision_snapshot(project_root)
    index_bytes = (project_root / "ui" / "index.json").read_bytes()
    try:
        duplicate = _publish(
            store,
            job_id="job-002",
            run_id="run-002",
            status="failure",
            correction="rebuild the opening scene",
            messages=["validation failed", "retry is available"],
            asset_ids=["asset-diagnostic-002"],
        )
    except ValueError as exc:
        assert "conflict" in str(exc).lower() or "duplicate" in str(exc).lower()
    else:
        assert isinstance(duplicate, revision_type)
        assert _document(duplicate)["revision_id"] == "v002"
    assert _revision_snapshot(project_root) == revision_snapshot
    assert (project_root / "ui" / "index.json").read_bytes() == index_bytes

    with pytest.raises(ValueError, match=r"conflict|duplicate|terminal"):
        _publish(
            store,
            job_id="job-002",
            run_id="run-other",
            status="success",
            correction="different terminal payload",
            messages=["different terminal payload"],
            asset_ids=["asset-other"],
        )
    assert _revision_snapshot(project_root) == revision_snapshot
    assert (project_root / "ui" / "index.json").read_bytes() == index_bytes
    _assert_index_only_pointer(project_root, "v002")


def test_revision_documents_reject_missing_or_unknown_schema_versions(
    tmp_path: Path,
) -> None:
    store_type, revision_type, index_type, draft_type = _require_revision_contract()
    project_root = _project(tmp_path)
    store = _store(store_type, project_root)
    revision = _publish(store, job_id="job-versioned", run_id="run-versioned", status="success")
    draft = store.start_working(  # type: ignore[attr-defined]
        project_id=PROJECT_ID,
        job_id="job-working",
        run_id="run-working",
        status="queued",
        base_package_hashes=dict(BASE_PACKAGE_HASHES),
    )
    documents = (
        (revision_type, _document(revision)),
        (index_type, _document(store.load_index())),  # type: ignore[attr-defined]
        (draft_type, _document(draft)),
    )
    for document_type, document in documents:
        missing = dict(document)
        missing.pop("schema_version")
        with pytest.raises(ValueError, match="schema_version"):
            document_type.from_document(missing)  # type: ignore[attr-defined]
        unknown = dict(document)
        unknown["schema_version"] = "unsupported/999"
        with pytest.raises(ValueError, match="schema_version"):
            document_type.from_document(unknown)  # type: ignore[attr-defined]

def test_checkout_preserves_later_revision_and_branch_uses_selected_parent(
    tmp_path: Path,
) -> None:
    store_type, revision_type, _, _ = _require_revision_contract()
    project_root = _project(tmp_path)
    store = _store(store_type, project_root)
    golden_root = project_root / "golden"
    golden_root.mkdir(parents=True)
    (golden_root / "manifest.json").write_bytes(b"golden-must-remain")
    project_snapshot = (project_root / "project.json").read_bytes()
    timeline_snapshot = (project_root / "timeline.json").read_bytes()
    golden_snapshot = _snapshot(golden_root)

    first = _publish(
        store,
        job_id="job-branch-001",
        run_id="run-branch-001",
        status="success",
    )
    second = _publish(
        store,
        job_id="job-branch-002",
        run_id="run-branch-002",
        status="success",
    )
    assert isinstance(first, revision_type)
    assert isinstance(second, revision_type)
    assert _document(first)["revision_id"] == "v001"
    assert _document(second)["revision_id"] == "v002"
    revisions_before_checkout = _revision_snapshot(project_root)

    checked_out = store.checkout("v001")
    assert isinstance(checked_out, revision_type)
    assert _document(checked_out)["revision_id"] == "v001"
    _assert_index_only_pointer(project_root, "v001")
    assert _revision_snapshot(project_root) == revisions_before_checkout
    assert (project_root / "project.json").read_bytes() == project_snapshot
    assert (project_root / "timeline.json").read_bytes() == timeline_snapshot
    assert _snapshot(golden_root) == golden_snapshot

    branched = _publish(
        store,
        job_id="job-branch-003",
        run_id="run-branch-003",
        status="success",
        correction="branch from the checked out opening",
    )
    assert isinstance(branched, revision_type)
    assert _document(branched)["revision_id"] == "v003"
    assert _document(branched)["parent_revision_id"] == "v001"
    for relative_path, content in revisions_before_checkout.items():
        assert (project_root / "ui" / "revisions" / relative_path).read_bytes() == content
    assert sorted(
        path.name for path in (project_root / "ui" / "revisions").glob("*.json")
    ) == ["v001.json", "v002.json", "v003.json"]
    _assert_index_only_pointer(project_root, "v003")
    assert (project_root / "project.json").read_bytes() == project_snapshot
    assert (project_root / "timeline.json").read_bytes() == timeline_snapshot
    assert _snapshot(golden_root) == golden_snapshot
    _assert_no_host_paths(
        _json_document(project_root / "ui" / "revisions" / "v003.json"),
        project_root,
    )


def test_restart_recovers_working_job_and_requires_explicit_retry_for_revision(
    tmp_path: Path,
) -> None:
    store_type, revision_type, _, draft_type = _require_revision_contract()
    project_root = _project(tmp_path)
    store = _store(store_type, project_root)
    golden_root = project_root / "golden"
    golden_root.mkdir(parents=True)
    (golden_root / "manifest.json").write_bytes(b"golden-before-recovery")
    project_snapshot = (project_root / "project.json").read_bytes()
    timeline_snapshot = (project_root / "timeline.json").read_bytes()
    golden_snapshot = _snapshot(golden_root)

    first = _publish(
        store,
        job_id="job-selected",
        run_id="run-selected",
        status="success",
        messages=["selected revision"],
        asset_ids=["asset-selected"],
    )
    assert isinstance(first, revision_type)
    assert _document(first)["revision_id"] == "v001"
    working = store.start_working(  # type: ignore[attr-defined]
        project_id=PROJECT_ID,
        job_id="job-a",
        run_id="run-a",
        status="running",
        base_package_hashes=dict(BASE_PACKAGE_HASHES),
        correction="retry this scene",
        messages=["draft started"],
        asset_ids=["asset-selected"],
        scene_id="abertura",
        base_run_id="run-base",
    )
    assert isinstance(working, draft_type)
    working_path = project_root / "ui" / "working" / "job-a.json"
    assert working_path.is_file()
    revisions_before_recovery = _revision_snapshot(project_root)
    index_before_recovery = (project_root / "ui" / "index.json").read_bytes()
    _assert_no_host_paths(_json_document(working_path), project_root)

    restarted_store = _store(store_type, project_root)
    recovered = restarted_store.recover_interrupted()  # type: ignore[attr-defined]
    assert recovered
    assert all(isinstance(item, draft_type) for item in recovered)
    interrupted = _json_document(working_path)
    assert interrupted["status"] == "interrupted"
    assert interrupted["job_id"] == "job-a"
    interrupted_bytes_before_retry = working_path.read_bytes()
    assert _revision_snapshot(project_root) == revisions_before_recovery
    assert (project_root / "ui" / "index.json").read_bytes() == index_before_recovery
    assert not (project_root / "ui" / "working" / "job-a-retry.json").exists()
    _assert_no_host_paths(interrupted, project_root)

    retry = restarted_store.retry(  # type: ignore[attr-defined]
        "job-a",
        new_job_id="job-a-retry",
    )
    assert isinstance(retry, draft_type)
    retry_document = _document(retry)
    retry_path = project_root / "ui" / "working" / "job-a-retry.json"
    assert retry_path.is_file()
    assert _json_document(retry_path) == retry_document
    assert retry_document["job_id"] == "job-a-retry"
    assert retry_document["status"] == "queued"
    assert retry_document["retry_of"] == "job-a"
    for field in (
        "project_id",
        "base_package_hashes",
        "correction",
        "messages",
        "asset_ids",
        "scene_id",
        "base_run_id",
    ):
        assert retry_document[field] == interrupted[field]
    assert working_path.read_bytes() == interrupted_bytes_before_retry
    assert _revision_snapshot(project_root) == revisions_before_recovery
    assert (project_root / "ui" / "index.json").read_bytes() == index_before_recovery
    assert (project_root / "project.json").read_bytes() == project_snapshot
    assert (project_root / "timeline.json").read_bytes() == timeline_snapshot
    assert _snapshot(golden_root) == golden_snapshot
    _assert_no_host_paths(retry_document, project_root)

    terminal_retry = _publish(
        restarted_store,
        job_id="job-a-retry",
        run_id="run-a-retry",
        status="success",
        correction="retry this scene",
        messages=["retry completed"],
        asset_ids=["asset-retry"],
    )
    assert isinstance(terminal_retry, revision_type)
    terminal_document = _document(terminal_retry)
    assert terminal_document["revision_id"] == "v002"
    assert terminal_document["parent_revision_id"] == "v001"
    assert terminal_document["job_id"] == "job-a-retry"
    assert sorted(
        path.name for path in (project_root / "ui" / "revisions").glob("*.json")
    ) == ["v001.json", "v002.json"]
    assert (
        project_root / "ui" / "revisions" / "v001.json"
    ).read_bytes() == revisions_before_recovery["v001.json"]
    assert working_path.read_bytes() == interrupted_bytes_before_retry
    _assert_index_only_pointer(project_root, "v002")
    assert (project_root / "project.json").read_bytes() == project_snapshot
    assert (project_root / "timeline.json").read_bytes() == timeline_snapshot
    assert _snapshot(golden_root) == golden_snapshot
    _assert_no_host_paths(terminal_document, project_root)


@pytest.mark.parametrize(
    "relative_path",
    ("ui", "ui/revisions", "ui/working", "ui/index.json"),
)
@pytest.mark.parametrize("node_kind", ("symlink", "fifo"))
def test_revision_store_rejects_symlink_and_special_ui_nodes_before_external_write(
    tmp_path: Path,
    relative_path: str,
    node_kind: str,
) -> None:
    store_type, _, _, _ = _require_revision_contract()
    project_root = _project(tmp_path)
    external_root = tmp_path / "external"
    external_root.mkdir()
    sentinel = external_root / "sentinel.bin"
    sentinel.write_bytes(b"must remain byte-identical")
    target = project_root / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    if node_kind == "symlink":
        external_target = external_root / "redirect"
        if target.suffix:
            external_target.write_bytes(b"external index target")
            target.symlink_to(external_target)
        else:
            external_target.mkdir()
            target.symlink_to(external_target, target_is_directory=True)
    else:
        os.mkfifo(target)
    external_before = _snapshot(external_root)
    external_entries_before = sorted(
        path.relative_to(external_root).as_posix()
        for path in external_root.rglob("*")
    )

    with pytest.raises((OSError, ValueError)):
        _store(store_type, project_root)

    assert _snapshot(external_root) == external_before
    assert sorted(
        path.relative_to(external_root).as_posix()
        for path in external_root.rglob("*")
    ) == external_entries_before
    assert sentinel.read_bytes() == b"must remain byte-identical"


def test_terminal_publication_closes_drafts_and_recovery_ignores_terminal_jobs(
    tmp_path: Path,
) -> None:
    store_type, revision_type, _, draft_type = _require_revision_contract()
    project_root = _project(tmp_path)
    store = _store(store_type, project_root)
    jobs = (
        ("job-terminal-success", "run-terminal-success", "success"),
        ("job-terminal-failure", "run-terminal-failure", "failure"),
    )
    for job_id, run_id, _ in jobs:
        draft = store.start_working(  # type: ignore[attr-defined]
            project_id=PROJECT_ID,
            job_id=job_id,
            run_id=run_id,
            status="running",
            base_package_hashes=dict(BASE_PACKAGE_HASHES),
            correction="preserve this correction",
            messages=["working draft"],
            asset_ids=["asset-working"],
        )
        assert isinstance(draft, draft_type)
    draft_documents = {
        job_id: _json_document(project_root / "ui" / "working" / f"{job_id}.json")
        for job_id, _, _ in jobs
    }

    terminal_manifests: list[object] = []
    for job_id, run_id, status in jobs:
        terminal = _publish(
            store,
            job_id=job_id,
            run_id=run_id,
            status=status,
            correction="preserve this correction",
            messages=["terminal result"],
            asset_ids=["asset-terminal"],
        )
        assert isinstance(terminal, revision_type)
        terminal_manifests.append(terminal)
        persisted = _json_document(project_root / "ui" / "working" / f"{job_id}.json")
        original = draft_documents[job_id]
        for field in (
            "project_id",
            "job_id",
            "run_id",
            "parent_revision_id",
            "base_package_hashes",
            "correction",
            "retry_of",
        ):
            assert persisted[field] == original[field]
        assert persisted["status"] == status
        assert persisted["messages"] == ["terminal result"]
        assert persisted["asset_ids"] == ["asset-terminal"]

    revisions_before_recovery = _revision_snapshot(project_root)
    index_before_recovery = (project_root / "ui" / "index.json").read_bytes()
    working_before_recovery = _snapshot(project_root / "ui" / "working")
    recovered = _store(store_type, project_root).recover_interrupted()  # type: ignore[attr-defined]
    assert recovered == []
    assert _revision_snapshot(project_root) == revisions_before_recovery
    assert (project_root / "ui" / "index.json").read_bytes() == index_before_recovery
    assert _snapshot(project_root / "ui" / "working") == working_before_recovery
    assert len(terminal_manifests) == 2


@pytest.mark.parametrize("operation", ("manifest", "publish", "checkout"))
def test_malformed_revision_identity_is_rejected_by_public_load_publish_and_checkout(
    tmp_path: Path,
    operation: str,
) -> None:
    store_type, revision_type, _, _ = _require_revision_contract()
    project_root = _project(tmp_path)
    store = _store(store_type, project_root)
    malformed = {
        "revision_id": "foo",
        "project_id": PROJECT_ID,
        "job_id": "job-malformed",
        "run_id": "run-malformed",
        "status": "success",
        "parent_revision_id": None,
        "base_package_hashes": dict(BASE_PACKAGE_HASHES),
        "correction": None,
        "messages": [],
        "asset_ids": [],
    }
    malformed_path = project_root / "ui" / "revisions" / "v001.json"
    malformed_path.write_text(json.dumps(malformed), encoding="utf-8")

    with pytest.raises(ValueError, match=r"revision|identity|mismatch"):
        if operation == "manifest":
            revision_type.from_document(malformed)  # type: ignore[attr-defined]
        elif operation == "publish":
            _publish(
                store,
                job_id="job-malformed",
                run_id="run-malformed",
                status="success",
            )
        else:
            store.checkout("v001")


def test_terminal_publication_rolls_back_revision_when_index_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_type, revision_type, _, _ = _require_revision_contract()
    project_root = _project(tmp_path)
    store = _store(store_type, project_root)
    first = _publish(
        store,
        job_id="job-before-fault",
        run_id="run-before-fault",
        status="success",
    )
    assert isinstance(first, revision_type)
    revisions_before = _revision_snapshot(project_root)
    index_path = project_root / "ui" / "index.json"
    index_before = index_path.read_bytes()
    original_replace = os.replace
    injected = False

    def fail_index_replace(
        source: str | os.PathLike[str],
        destination: str | os.PathLike[str],
    ) -> None:
        nonlocal injected
        if Path(destination) == index_path:
            injected = True
            raise OSError("injected revision index replace failure")
        original_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_index_replace)
    try:
        with pytest.raises(OSError, match="index replace"):
            _publish(
                store,
                job_id="job-after-fault",
                run_id="run-after-fault",
                status="success",
            )
    finally:
        monkeypatch.undo()

    assert injected
    assert _revision_snapshot(project_root) == revisions_before
    assert index_path.read_bytes() == index_before
    retried = _publish(
        store,
        job_id="job-after-fault",
        run_id="run-after-fault",
        status="success",
    )
    assert isinstance(retried, revision_type)
    assert _document(retried)["revision_id"] == "v002"
    assert _revision_snapshot(project_root)["v001.json"] == revisions_before["v001.json"]
    assert _assert_index_only_pointer(project_root, "v002")["current_revision_id"] == "v002"
