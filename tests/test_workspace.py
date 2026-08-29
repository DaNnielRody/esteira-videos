"""Behavioral tests for isolated run and attempt workspaces."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterator

import pytest


try:
    from video_pipeline.workspace import RunWorkspace
except (ImportError, ModuleNotFoundError):  # pragma: no cover - RED shim
    _CONTRACT_IMPORT_ERROR = True
    RunWorkspace = None  # type: ignore[assignment,misc]
else:
    _CONTRACT_IMPORT_ERROR = False


def _require_contract() -> None:
    if _CONTRACT_IMPORT_ERROR:
        pytest.fail("RENDER_VALIDATOR_CONTRACT_MISSING")


def _ids(values: list[str]) -> Callable[[], str]:
    iterator: Iterator[str] = iter(values)
    return lambda: next(iterator)


def test_run_workspace_uses_collision_resistant_isolated_run_directories(
    tmp_path: Path,
) -> None:
    """Two runs never share or overwrite a directory, even at one root."""

    _require_contract()
    workspace = RunWorkspace(
        root=tmp_path,
        id_factory=_ids(["collision-candidate", "collision-candidate", "fresh-run"]),
    )

    first = workspace.create_run()
    first_marker = first.path / "run-marker.txt"
    first_marker.write_text("FIRST_RUN_SENTINEL", encoding="utf-8")
    second = workspace.create_run()

    assert first.path.is_dir()
    assert second.path.is_dir()
    assert first.path != second.path
    assert first_marker.read_text(encoding="utf-8") == "FIRST_RUN_SENTINEL"
    assert first.path.parent == tmp_path
    assert second.path.parent == tmp_path


def test_run_workspace_allocates_sequential_attempt_01_and_02_without_overwrite(
    tmp_path: Path,
) -> None:
    """Attempts are isolated, ordered, and preserve all prior artifacts."""

    _require_contract()
    workspace = RunWorkspace(root=tmp_path, id_factory=_ids(["run-fixed"]))
    run = workspace.create_run()

    attempt_01 = run.create_attempt()
    sentinel = attempt_01.path / "stdout.txt"
    sentinel.write_text("ATTEMPT_01_SENTINEL", encoding="utf-8")
    attempt_02 = run.create_attempt()

    assert attempt_01.path.name == "attempt-01"
    assert attempt_02.path.name == "attempt-02"
    assert attempt_01.path != attempt_02.path
    assert attempt_01.path.is_dir()
    assert attempt_02.path.is_dir()
    assert sentinel.read_text(encoding="utf-8") == "ATTEMPT_01_SENTINEL"
    assert not any(attempt_02.path.iterdir())


def test_workspace_audit_contract() -> None:
    """Inventory the workspace contract tests without product calls."""

    behavioral_tests = (
        "test_run_workspace_uses_collision_resistant_isolated_run_directories",
        "test_run_workspace_allocates_sequential_attempt_01_and_02_without_overwrite",
    )

    assert all(callable(globals().get(name)) for name in behavioral_tests)
