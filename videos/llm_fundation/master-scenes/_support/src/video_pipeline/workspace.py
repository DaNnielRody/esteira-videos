"""Collision-safe run and attempt directories for preserved pipeline facts."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


def _new_run_id() -> str:
    """Return a collision-resistant default run identifier."""

    return uuid.uuid4().hex


@dataclass(slots=True)
class AttemptWorkspace:
    """One isolated, never-overwritten attempt directory."""

    path: Path

    @property
    def media_dir(self) -> Path:
        """The attempt-local Manim media root."""

        return self.path / "media"


@dataclass(slots=True)
class RunHandle:
    """A run directory that allocates sequential preserved attempts."""

    path: Path
    _next_attempt_number: int = 1

    def create_attempt(self) -> AttemptWorkspace:
        """Create the next available ``attempt-NN`` directory."""

        while True:
            number = self._next_attempt_number
            attempt_path = self.path / f"attempt-{number:02d}"
            self._next_attempt_number += 1
            try:
                attempt_path.mkdir()
            except FileExistsError:
                continue
            return AttemptWorkspace(path=attempt_path)


class RunWorkspace:
    """Allocate isolated run directories below a configurable root."""

    def __init__(
        self,
        *,
        root: str | Path = Path("artifacts/runs"),
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.root = Path(root)
        self._id_factory = id_factory or _new_run_id
        self.root.mkdir(parents=True, exist_ok=True)

    def create_run(self) -> RunHandle:
        """Create a collision-free run directory without overwriting an old run."""

        while True:
            run_id = self._id_factory()
            if not run_id:
                raise ValueError("run id must not be blank")
            run_path = self.root / run_id
            try:
                run_path.mkdir()
            except FileExistsError:
                continue
            return RunHandle(path=run_path)

