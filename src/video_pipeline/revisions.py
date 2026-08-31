"""Durable, project-local manifests for immutable UI revisions.

The revision store deliberately owns only the ``ui`` directory.  A terminal
render is represented by one immutable manifest, while the small index file is
the checkout pointer and working drafts are kept separate from both.  This
keeps revision history independent from the project and timeline documents.
"""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_REVISION_PATTERN = re.compile(r"^v([0-9]{3,})$")
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_REVISION_SCHEMA_VERSION = "project.ui-revision/1"
_INDEX_SCHEMA_VERSION = "project.ui-revision-index/1"
_WORKING_SCHEMA_VERSION = "project.ui-working/1"

_TERMINAL_DRAFT_STATES = frozenset(
    {
        "success",
        "failure",
        "provider_error",
        "sensor_error",
        "attempts_exhausted",
        "terminal",
        "interrupted",
    }
)


@dataclass(frozen=True, slots=True)
class RevisionManifest:
    """The complete public record of one terminal render revision."""

    revision_id: str
    project_id: str
    job_id: str
    run_id: str
    status: str
    parent_revision_id: str | None
    base_package_hashes: dict[str, str]
    correction: str | None
    messages: list[str]
    asset_ids: list[str]

    def to_document(self) -> dict[str, object]:
        """Return the stable JSON representation persisted by the store."""

        return {
            "schema_version": _REVISION_SCHEMA_VERSION,
            "revision_id": self.revision_id,
            "project_id": self.project_id,
            "job_id": self.job_id,
            "run_id": self.run_id,
            "status": self.status,
            "parent_revision_id": self.parent_revision_id,
            "base_package_hashes": dict(self.base_package_hashes),
            "correction": self.correction,
            "messages": list(self.messages),
            "asset_ids": list(self.asset_ids),
        }

    @classmethod
    def from_document(cls, document: Mapping[str, object]) -> RevisionManifest:
        """Build a typed manifest after validating persisted JSON fields."""

        _require_schema_version(document, _REVISION_SCHEMA_VERSION, "revision manifest")
        return cls(
            revision_id=_required_revision_id(document, "revision_id"),
            project_id=_required_id(document, "project_id"),
            job_id=_required_id(document, "job_id"),
            run_id=_required_id(document, "run_id"),
            status=_required_string(document, "status"),
            parent_revision_id=_optional_revision_id(document, "parent_revision_id"),
            base_package_hashes=_package_hashes(document.get("base_package_hashes")),
            correction=_optional_string(document, "correction"),
            messages=_string_list(document.get("messages"), "messages"),
            asset_ids=_safe_id_list(document.get("asset_ids"), "asset_ids"),
        )


@dataclass(frozen=True, slots=True)
class RevisionIndex:
    """The checkout pointer for a project."""

    current_revision_id: str | None

    def to_document(self) -> dict[str, object]:
        """Return the intentionally minimal index document."""

        return {
            "schema_version": _INDEX_SCHEMA_VERSION,
            "current_revision_id": self.current_revision_id,
        }

    @classmethod
    def from_document(cls, document: Mapping[str, object]) -> RevisionIndex:
        """Build an index from its JSON representation."""

        _require_schema_version(document, _INDEX_SCHEMA_VERSION, "revision index")
        if set(document) != {"schema_version", "current_revision_id"}:
            raise ValueError("revision index fields are invalid")
        value = document["current_revision_id"]
        if value is not None and not isinstance(value, str):
            raise ValueError("current_revision_id must be a revision id or null")
        if value is not None:
            _validate_revision_id(value)
        return cls(current_revision_id=value)


@dataclass(frozen=True, slots=True)
class WorkingDraft:
    """A persisted non-terminal job, recoverable without creating a revision."""

    project_id: str
    job_id: str
    run_id: str
    status: str
    parent_revision_id: str | None
    base_package_hashes: dict[str, str]
    correction: str | None
    messages: list[str]
    asset_ids: list[str]
    scene_id: str | None = None
    base_run_id: str | None = None
    retry_of: str | None = None

    def to_document(self) -> dict[str, object]:
        """Return the stable JSON representation of this working draft."""

        return {
            "schema_version": _WORKING_SCHEMA_VERSION,
            "project_id": self.project_id,
            "job_id": self.job_id,
            "run_id": self.run_id,
            "status": self.status,
            "parent_revision_id": self.parent_revision_id,
            "base_package_hashes": dict(self.base_package_hashes),
            "correction": self.correction,
            "messages": list(self.messages),
            "asset_ids": list(self.asset_ids),
            "scene_id": self.scene_id,
            "base_run_id": self.base_run_id,
            "retry_of": self.retry_of,
        }

    @classmethod
    def from_document(cls, document: Mapping[str, object]) -> WorkingDraft:
        """Build a typed working draft after validating persisted JSON."""

        _require_schema_version(document, _WORKING_SCHEMA_VERSION, "working draft")
        return cls(
            project_id=_required_id(document, "project_id"),
            job_id=_required_id(document, "job_id"),
            run_id=_required_id(document, "run_id"),
            status=_required_string(document, "status"),
            parent_revision_id=_optional_revision_id(document, "parent_revision_id"),
            base_package_hashes=_package_hashes(document.get("base_package_hashes")),
            correction=_optional_string(document, "correction"),
            messages=_string_list(document.get("messages"), "messages"),
            asset_ids=_safe_id_list(document.get("asset_ids"), "asset_ids"),
            scene_id=_optional_id(document, "scene_id"),
            base_run_id=_optional_id(document, "base_run_id"),
            retry_of=_optional_id(document, "retry_of"),
        )

    def retry_run_id(self, new_job_id: str) -> str:
        """Derive the canonical run ID for a retry job."""

        _validate_id(new_job_id, "new_job_id")
        job_prefix = f"{self.job_id}-"
        if new_job_id.startswith(job_prefix):
            return f"{self.run_id}-{new_job_id[len(job_prefix):]}"
        return f"{self.run_id}-retry"


class RevisionStore:
    """Persist immutable terminal revisions and recoverable working drafts."""

    def __init__(self, project_root: str | Path, *, create: bool = True) -> None:
        self.project_root = Path(project_root).resolve()
        self.ui_root = self.project_root / "ui"
        self.revisions_root = self.ui_root / "revisions"
        self.working_root = self.ui_root / "working"
        self._preflight_paths()
        if create:
            self.revisions_root.mkdir(parents=True, exist_ok=True)
            self.working_root.mkdir(parents=True, exist_ok=True)

    def publish_terminal(
        self,
        *,
        project_id: str,
        job_id: str,
        run_id: str,
        status: str,
        base_package_hashes: Mapping[str, str],
        correction: str | None = None,
        messages: Sequence[str] = (),
        asset_ids: Sequence[str] = (),
    ) -> RevisionManifest:
        """Create or idempotently return the terminal revision for ``job_id``."""

        _validate_id(project_id, "project_id")
        _validate_id(job_id, "job_id")
        _validate_id(run_id, "run_id")
        _validate_terminal_status(status)
        hashes = _package_hashes(base_package_hashes)
        message_list = _string_list(messages, "messages")
        asset_list = _safe_id_list(asset_ids, "asset_ids")
        _validate_optional_text(correction, "correction", self.project_root)

        existing = self._revision_for_job(job_id)
        if existing is not None:
            if _same_terminal_payload(
                existing,
                project_id=project_id,
                job_id=job_id,
                run_id=run_id,
                status=status,
                base_package_hashes=hashes,
                correction=correction,
                messages=message_list,
                asset_ids=asset_list,
            ):
                self._terminalize_working_draft(existing)
                return existing
            raise ValueError(f"terminal job conflict for duplicate job {job_id}")

        base = self._current_revision()
        revision_id = self._next_revision_id()
        manifest = RevisionManifest(
            revision_id=revision_id,
            project_id=project_id,
            job_id=job_id,
            run_id=run_id,
            status=status,
            parent_revision_id=base.revision_id if base is not None else None,
            base_package_hashes=hashes,
            correction=correction,
            messages=message_list,
            asset_ids=asset_list,
        )
        _write_json_create_only(
            self.revisions_root / f"{manifest.revision_id}.json",
            manifest.to_document(),
        )
        revision_path = self.revisions_root / f"{manifest.revision_id}.json"
        try:
            self._write_index(RevisionIndex(current_revision_id=manifest.revision_id))
        except Exception:
            try:
                revision_path.unlink()
            except OSError:
                pass
            raise
        self._terminalize_working_draft(manifest)
        return manifest

    def checkout(self, revision_id: str) -> RevisionManifest:
        """Move only the checkout pointer to an existing revision."""

        _validate_revision_id(revision_id)
        manifest = self._load_revision(revision_id)
        self._write_index(RevisionIndex(current_revision_id=manifest.revision_id))
        return manifest

    def load_index(self) -> RevisionIndex:
        """Load the checkout pointer, returning an empty pointer if absent."""

        path = self.ui_root / "index.json"
        if not path.exists():
            return RevisionIndex(current_revision_id=None)
        return RevisionIndex.from_document(_load_document(path))

    def list_revisions(self) -> list[RevisionManifest]:
        """Return immutable revision manifests in monotonic revision order."""

        return [self._load_revision(path.stem) for path in self._revision_paths()]

    def current_revision(self) -> RevisionManifest | None:
        """Return the checked-out revision without changing the pointer."""

        return self._current_revision()

    def start_working(
        self,
        *,
        project_id: str,
        job_id: str,
        run_id: str,
        status: str,
        base_package_hashes: Mapping[str, str],
        correction: str | None = None,
        messages: Sequence[str] = (),
        asset_ids: Sequence[str] = (),
        scene_id: str | None = None,
        base_run_id: str | None = None,
    ) -> WorkingDraft:
        """Persist a new working job without changing revision history."""

        _validate_id(project_id, "project_id")
        _validate_id(job_id, "job_id")
        _validate_id(run_id, "run_id")
        if not status or not isinstance(status, str):
            raise ValueError("working status must be a non-empty string")
        if status in _TERMINAL_DRAFT_STATES:
            raise ValueError("start_working requires a non-terminal status")
        hashes = _package_hashes(base_package_hashes)
        message_list = _string_list(messages, "messages")
        asset_list = _safe_id_list(asset_ids, "asset_ids")
        _validate_optional_text(correction, "correction", self.project_root)
        if scene_id is not None:
            _validate_id(scene_id, "scene_id")
        if base_run_id is not None:
            _validate_id(base_run_id, "base_run_id")
        if (scene_id is None) != (base_run_id is None):
            raise ValueError("scene_id and base_run_id must be supplied together")
        base = self._current_revision()
        draft_parent_revision_id = base.revision_id if base is not None else None
        draft = WorkingDraft(
            project_id=project_id,
            job_id=job_id,
            run_id=run_id,
            status=status,
            parent_revision_id=draft_parent_revision_id,
            base_package_hashes=hashes,
            correction=correction,
            messages=message_list,
            asset_ids=asset_list,
            scene_id=scene_id,
            base_run_id=base_run_id,
        )
        path = self.working_root / f"{job_id}.json"
        if path.exists():
            existing = WorkingDraft.from_document(_load_document(path))
            if existing.to_document() == draft.to_document():
                return existing
            raise ValueError(f"working job conflict for duplicate job {job_id}")
        _write_json_create_only(path, draft.to_document())
        return draft

    def recover_interrupted(self) -> list[WorkingDraft]:
        """Mark all non-terminal persisted drafts interrupted, atomically."""

        recovered: list[WorkingDraft] = []
        for path in self._working_paths():
            draft = WorkingDraft.from_document(_load_document(path))
            if draft.status in _TERMINAL_DRAFT_STATES:
                continue
            interrupted = WorkingDraft(
                project_id=draft.project_id,
                job_id=draft.job_id,
                run_id=draft.run_id,
                status="interrupted",
                parent_revision_id=draft.parent_revision_id,
                base_package_hashes=dict(draft.base_package_hashes),
                correction=draft.correction,
                messages=list(draft.messages),
                asset_ids=list(draft.asset_ids),
                scene_id=draft.scene_id,
                base_run_id=draft.base_run_id,
                retry_of=draft.retry_of,
            )
            _write_json_replace(path, interrupted.to_document())
            recovered.append(interrupted)
        return recovered

    def retry(self, job_id: str, *, new_job_id: str) -> WorkingDraft:
        """Create a queued retry draft while preserving its interrupted source."""

        _validate_id(job_id, "job_id")
        _validate_id(new_job_id, "new_job_id")
        source_path = self.working_root / f"{job_id}.json"
        if not source_path.is_file() or source_path.is_symlink():
            raise ValueError(f"unknown working job {job_id}")
        source = WorkingDraft.from_document(_load_document(source_path))
        if source.status != "interrupted":
            raise ValueError("retry requires an interrupted working job")
        target_path = self.working_root / f"{new_job_id}.json"
        if target_path.exists():
            raise ValueError(f"working job conflict for duplicate job {new_job_id}")
        retry_draft = WorkingDraft(
            project_id=source.project_id,
            job_id=new_job_id,
            run_id=source.retry_run_id(new_job_id),
            status="queued",
            parent_revision_id=source.parent_revision_id,
            base_package_hashes=dict(source.base_package_hashes),
            correction=source.correction,
            messages=list(source.messages),
            asset_ids=list(source.asset_ids),
            scene_id=source.scene_id,
            base_run_id=source.base_run_id,
            retry_of=source.job_id,
        )
        _write_json_create_only(target_path, retry_draft.to_document())
        return retry_draft

    def _write_index(self, index: RevisionIndex) -> None:
        _write_json_replace(self.ui_root / "index.json", index.to_document())

    def _preflight_paths(self) -> None:
        """Validate every store path before creating any directory or file."""

        if not self.project_root.is_dir():
            raise ValueError("project_root must be an existing directory")
        self._preflight_directory(self.ui_root, "ui")
        self._preflight_directory(self.revisions_root, "ui/revisions")
        self._preflight_directory(self.working_root, "ui/working")
        self._preflight_index(self.ui_root / "index.json")

    def _preflight_directory(self, path: Path, label: str) -> None:
        self._ensure_inside_project(path)
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            return
        except OSError as exc:
            raise ValueError(f"cannot inspect {label}") from exc
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise ValueError(f"{label} must be a real directory inside project")
        self._ensure_inside_project(path)

    def _preflight_index(self, path: Path) -> None:
        self._ensure_inside_project(path)
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            return
        except OSError as exc:
            raise ValueError("cannot inspect ui/index.json") from exc
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise ValueError("ui/index.json must be a real regular file")
        self._ensure_inside_project(path)

    def _ensure_inside_project(self, path: Path) -> None:
        try:
            path.resolve(strict=False).relative_to(self.project_root)
        except ValueError as exc:
            raise ValueError("revision store path escapes project root") from exc

    def _terminalize_working_draft(self, manifest: RevisionManifest) -> None:
        """Persist terminal facts while retaining the draft's immutable identity."""

        path = self.working_root / f"{manifest.job_id}.json"
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            return
        except OSError as exc:
            raise ValueError(f"cannot inspect working draft {manifest.job_id}") from exc
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise ValueError(f"working draft {manifest.job_id} must be a real file")
        draft = WorkingDraft.from_document(_load_document(path))
        if (
            draft.project_id != manifest.project_id
            or draft.job_id != manifest.job_id
            or draft.run_id != manifest.run_id
            or draft.base_package_hashes != manifest.base_package_hashes
            or draft.correction != manifest.correction
        ):
            raise ValueError(f"working draft identity conflict for {manifest.job_id}")
        terminal = WorkingDraft(
            project_id=draft.project_id,
            job_id=draft.job_id,
            run_id=draft.run_id,
            status=manifest.status,
            parent_revision_id=draft.parent_revision_id,
            base_package_hashes=dict(draft.base_package_hashes),
            correction=draft.correction,
            messages=list(manifest.messages),
            asset_ids=list(manifest.asset_ids),
            retry_of=draft.retry_of,
        )
        if terminal.to_document() != draft.to_document():
            _write_json_replace(path, terminal.to_document())

    def _current_revision(self) -> RevisionManifest | None:
        current_id = self.load_index().current_revision_id
        if current_id is None:
            return None
        return self._load_revision(current_id)

    def _load_revision(self, revision_id: str) -> RevisionManifest:
        path = self.revisions_root / f"{revision_id}.json"
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"unknown revision {revision_id}")
        manifest = RevisionManifest.from_document(_load_document(path))
        if manifest.revision_id != revision_id:
            raise ValueError(f"revision document identity mismatch for {revision_id}")
        return manifest

    def _revision_for_job(self, job_id: str) -> RevisionManifest | None:
        for path in self._revision_paths():
            manifest = RevisionManifest.from_document(_load_document(path))
            if manifest.revision_id != path.stem:
                raise ValueError(f"revision document identity mismatch for {path.stem}")
            if manifest.job_id == job_id:
                return manifest
        return None

    def _revision_paths(self) -> list[Path]:
        paths: list[Path] = []
        for path in sorted(
            self.revisions_root.glob("v*.json"),
            key=_revision_path_number,
        ):
            if path.is_symlink() or not path.is_file():
                continue
            if _REVISION_PATTERN.fullmatch(path.stem) is not None:
                paths.append(path)
        return paths

    def _working_paths(self) -> list[Path]:
        paths: list[Path] = []
        for path in sorted(self.working_root.glob("*.json")):
            if path.is_symlink() or not path.is_file():
                continue
            if _ID_PATTERN.fullmatch(path.stem) is not None:
                paths.append(path)
        return paths

    def _next_revision_id(self) -> str:
        largest = 0
        for path in self._revision_paths():
            match = _REVISION_PATTERN.fullmatch(path.stem)
            if match is not None:
                largest = max(largest, int(match.group(1)))
        return f"v{largest + 1:03d}"


def _same_terminal_payload(
    manifest: RevisionManifest,
    *,
    project_id: str,
    job_id: str,
    run_id: str,
    status: str,
    base_package_hashes: dict[str, str],
    correction: str | None,
    messages: list[str],
    asset_ids: list[str],
) -> bool:
    """Compare immutable terminal inputs while ignoring generated revision id."""

    return (
        manifest.project_id == project_id
        and manifest.job_id == job_id
        and manifest.run_id == run_id
        and manifest.status == status
        and manifest.base_package_hashes == base_package_hashes
        and manifest.correction == correction
        and manifest.messages == messages
        and manifest.asset_ids == asset_ids
    )


def _validate_id(value: str, label: str) -> None:
    if not isinstance(value, str) or _ID_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} must be a safe public identifier")


def _require_schema_version(
    document: Mapping[str, object],
    expected: str,
    label: str,
) -> None:
    if document.get("schema_version") != expected:
        raise ValueError(f"{label} schema_version is unsupported")


def _validate_revision_id(value: str) -> None:
    if not isinstance(value, str) or _REVISION_PATTERN.fullmatch(value) is None:
        raise ValueError("revision_id must match vNNN")


def _revision_path_number(path: Path) -> int:
    match = _REVISION_PATTERN.fullmatch(path.stem)
    return int(match.group(1)) if match is not None else -1


def _required_id(document: Mapping[str, object], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string identifier")
    _validate_id(value, field)
    return value


def _required_revision_id(document: Mapping[str, object], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a revision identifier")
    _validate_revision_id(value)
    return value


def _required_string(document: Mapping[str, object], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _optional_string(document: Mapping[str, object], field: str) -> str | None:
    value = document.get(field)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{field} must be a string or null")
    return value


def _optional_id(document: Mapping[str, object], field: str) -> str | None:
    value = _optional_string(document, field)
    if value is not None:
        _validate_id(value, field)
    return value


def _optional_revision_id(document: Mapping[str, object], field: str) -> str | None:
    value = _optional_string(document, field)
    if value is not None:
        _validate_revision_id(value)
    return value


def _package_hashes(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError("base_package_hashes must be an object")
    hashes: dict[str, str] = {}
    for key, digest in value.items():
        if not isinstance(key, str):
            raise ValueError("base package hash keys must be strings")
        _validate_id(key, "base package hash key")
        if not isinstance(digest, str) or _HASH_PATTERN.fullmatch(digest) is None:
            raise ValueError("base package hashes must be SHA-256 hex digests")
        hashes[key] = digest
    return hashes


def _string_list(value: object, field: str) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{field} must be a list of strings")
    values = list(value)  # type: ignore[misc]
    if any(not isinstance(item, str) for item in values):  # type: ignore[misc]
        raise ValueError(f"{field} must be a list of strings")
    return values  # type: ignore[misc]


def _safe_id_list(value: object, field: str) -> list[str]:
    values = _string_list(value, field)
    for item in values:
        _validate_id(item, field)
    return values


def _validate_terminal_status(status: str) -> None:
    if status not in {"success", "failure"}:
        raise ValueError("terminal status must be success or failure")


def _validate_optional_text(value: str | None, field: str, project_root: Path) -> None:
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{field} must be a string or null")
    if value is not None and str(project_root) in value:
        raise ValueError(f"{field} must not contain a host path")


def _load_document(path: Path) -> dict[str, object]:
    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid JSON document: {path}") from exc
    if not isinstance(loaded, dict):
        raise ValueError(f"JSON document must be an object: {path}")
    return {key: value for key, value in loaded.items() if isinstance(key, str)}


def _write_json_create_only(path: Path, document: Mapping[str, object]) -> None:
    """Write a document once, using a same-directory link as the commit."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _json_bytes(document)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as temporary:
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.link(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _write_json_replace(path: Path, document: Mapping[str, object]) -> None:
    """Atomically replace a mutable document in the same directory."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _json_bytes(document)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as temporary:
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _json_bytes(document: Mapping[str, object]) -> bytes:
    return (
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
