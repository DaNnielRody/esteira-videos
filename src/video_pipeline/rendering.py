"""Bounded subprocess execution for Manim Community renders."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class _CompletedProcess(Protocol):
    """The process facts consumed from ``subprocess.run``."""

    returncode: int
    stdout: str | None
    stderr: str | None


class _SubprocessRun(Protocol):
    """Injectable subprocess boundary used by the deterministic tests."""

    def __call__(
        self,
        args: Sequence[str],
        *,
        capture_output: bool,
        text: bool,
        timeout: float,
        start_new_session: bool,
        check: bool,
    ) -> _CompletedProcess:
        """Run one bounded child process."""


@dataclass(frozen=True, slots=True)
class RenderResult:
    """Complete observable facts from one attempted Manim render."""

    argv: list[str]
    exit_code: int | None
    timed_out: bool
    missing_executable: bool
    stdout: str
    stderr: str
    elapsed_seconds: float
    mp4_paths: list[Path]


class ManimRunner:
    """Invoke Manim Community with the fixed MVP render settings."""

    def __init__(
        self,
        *,
        subprocess_run: _SubprocessRun | None = None,
        timeout: float = 120.0,
        process_group_id: int | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._subprocess_run = subprocess_run or _run_with_process_group
        self.timeout = float(timeout)
        self.process_group_id = process_group_id

    def run(self, scene_path: str | Path, media_dir: str | Path) -> RenderResult:
        """Render ``scene_path`` into ``media_dir`` and retain all process facts."""

        scene = Path(scene_path)
        media = Path(media_dir)
        media.mkdir(parents=True, exist_ok=True)
        argv = [
            sys.executable,
            "-m",
            "manim",
            "render",
            "--renderer",
            "cairo",
            "--format",
            "mp4",
            "--media_dir",
            str(media),
            "--resolution",
            "854,480",
            "--fps",
            "15",
            str(scene),
        ]

        started = time.monotonic()
        try:
            completed = self._subprocess_run(
                argv,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                start_new_session=True,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            self._terminate_process_group()
            return RenderResult(
                argv=argv,
                exit_code=None,
                timed_out=True,
                missing_executable=False,
                stdout=_text_output(exc.stdout),
                stderr=_text_output(exc.stderr),
                elapsed_seconds=max(0.0, time.monotonic() - started),
                mp4_paths=[],
            )
        except FileNotFoundError:
            return RenderResult(
                argv=argv,
                exit_code=None,
                timed_out=False,
                missing_executable=True,
                stdout="",
                stderr="",
                elapsed_seconds=max(0.0, time.monotonic() - started),
                mp4_paths=[],
            )

        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        paths = _discover_mp4_paths(media) if completed.returncode == 0 else []
        return RenderResult(
            argv=argv,
            exit_code=completed.returncode,
            timed_out=False,
            missing_executable=False,
            stdout=stdout,
            stderr=stderr,
            elapsed_seconds=max(0.0, time.monotonic() - started),
            mp4_paths=paths,
        )

    def _terminate_process_group(self) -> None:
        """Terminate the child session when a bounded render times out."""

        if self.process_group_id is None:
            return
        try:
            os.killpg(self.process_group_id, signal.SIGTERM)
        except OSError:
            # The process can exit between timeout observation and cleanup.
            return


def _discover_mp4_paths(media_dir: Path) -> list[Path]:
    """Return deterministic MP4 candidates below the attempt-local media root."""

    return sorted(
        (candidate for candidate in media_dir.rglob("*.mp4") if candidate.is_file()),
        key=lambda path: str(path),
    )


def _run_with_process_group(
    args: Sequence[str],
    *,
    capture_output: bool,
    text: bool,
    timeout: float,
    start_new_session: bool,
    check: bool,
) -> subprocess.CompletedProcess[str]:
    """Run a child while retaining the PID needed to terminate its session."""

    process = subprocess.Popen(
        list(args),
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
        text=text,
        start_new_session=start_new_session,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as first_timeout:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=5.0)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
        raise subprocess.TimeoutExpired(
            cmd=list(args),
            timeout=timeout,
            output=first_timeout.output or stdout,
            stderr=first_timeout.stderr or stderr,
        ) from first_timeout

    completed = subprocess.CompletedProcess(list(args), process.returncode, stdout, stderr)
    if check:
        completed.check_returncode()
    return completed


def _text_output(value: object) -> str:
    """Normalize subprocess timeout output while preserving its content."""

    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
