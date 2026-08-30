"""Behavioral tests for the public Manim rendering seam.

The subprocess boundary is replaced with operation-specific fakes.  These
tests deliberately do not require Manim itself to be installed: a missing
production seam is reported as the contract-specific RED signature from the
test body rather than as a collection/import error.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

try:
    from video_pipeline.rendering import ManimRunner
except (ImportError, ModuleNotFoundError):  # pragma: no cover - RED shim
    _CONTRACT_IMPORT_ERROR = True
    ManimRunner = None  # type: ignore[assignment,misc]
else:
    _CONTRACT_IMPORT_ERROR = False


class CompletedProcessFake:
    """Small subprocess result with only the public boundary facts."""

    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class RecordingSubprocess:
    """Operation-specific fake for a successful or failed render process."""

    def __init__(self, result: CompletedProcessFake) -> None:
        self.result = result
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(
        self,
        argv: Sequence[str],
        *args: object,
        **kwargs: object,
    ) -> CompletedProcessFake:
        del args
        self.calls.append((list(argv), kwargs))
        return self.result


class TimeoutSubprocess:
    """Boundary fake exposing a subprocess timeout and partial output."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(
        self,
        argv: Sequence[str],
        *args: object,
        **kwargs: object,
    ) -> CompletedProcessFake:
        del args
        command = list(argv)
        self.calls.append((command, kwargs))
        timeout = kwargs.get("timeout", 1)
        timeout_value = timeout if isinstance(timeout, (int, float)) else 1
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=timeout_value,
            output="PARTIAL_STDOUT_SENTINEL",
            stderr="PARTIAL_STDERR_SENTINEL",
        )


class MissingExecutableSubprocess:
    """Boundary fake distinguishing executable absence from a timeout."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(
        self,
        argv: Sequence[str],
        *args: object,
        **kwargs: object,
    ) -> CompletedProcessFake:
        del args, kwargs
        command = list(argv)
        self.calls.append(command)
        raise FileNotFoundError(2, "No such file or directory", command[0])


class ObservationWritingSubprocess:
    """Fake child that proves the runner exports its runtime observation seam."""

    def __init__(self, media_dir: Path) -> None:
        self.media_dir = media_dir
        self.observation_path: str | None = None

    def __call__(
        self,
        argv: Sequence[str],
        *args: object,
        **kwargs: object,
    ) -> CompletedProcessFake:
        del argv, args, kwargs
        self.observation_path = os.environ.get("VIDEO_PIPELINE_OBSERVATION_PATH")
        assert self.observation_path is not None
        assert os.environ.get("VIDEO_PIPELINE_MEDIA_DIR") == str(self.media_dir)
        observation = {
            "schema_version": "visual.observed-scene/1",
            "scene_id": "subprocess",
            "scene_name": "SubprocessScene",
            "initial_state": [],
            "final_state": [],
            "checkpoints": [],
            "animations": [],
            "camera_initial": {},
            "camera_final": {},
            "frames": [],
        }
        Path(self.observation_path).write_text(json.dumps(observation), encoding="utf-8")
        candidate = self.media_dir / "subprocess.mp4"
        candidate.write_bytes(b"fake mp4")
        return CompletedProcessFake(returncode=0, stdout="", stderr="")


def _require_contract() -> None:
    if _CONTRACT_IMPORT_ERROR:
        pytest.fail("RENDER_VALIDATOR_CONTRACT_MISSING")


def _scene_and_media(tmp_path: Path) -> tuple[Path, Path, Path]:
    scene_path = tmp_path / "AcceptanceScene.py"
    scene_path.write_text("# controlled scene boundary\n", encoding="utf-8")
    media_dir = tmp_path / "attempt-01" / "media"
    final_mp4 = media_dir / "videos" / scene_path.stem / "480p15" / f"{scene_path.stem}.mp4"
    final_mp4.parent.mkdir(parents=True)
    final_mp4.write_bytes(b"attempt-local mp4 bytes")
    return scene_path, media_dir, final_mp4


def test_manim_runner_uses_exact_cairo_mp4_media_and_854x480_15fps_argv(
    tmp_path: Path,
) -> None:
    """The renderer receives the declared Community-Manim command line."""

    _require_contract()
    scene_path, media_dir, final_mp4 = _scene_and_media(tmp_path)
    process = RecordingSubprocess(
        CompletedProcessFake(
            returncode=0,
            stdout="RENDER_STDOUT_SENTINEL",
            stderr="RENDER_STDERR_SENTINEL",
        )
    )
    runner = ManimRunner(subprocess_run=process, timeout=7.5)

    result = runner.run(scene_path, media_dir=media_dir)

    assert process.calls
    argv, kwargs = process.calls[0]
    assert argv == [
        sys.executable,
        "-m",
        "manim",
        "render",
        "--renderer",
        "cairo",
        "--format",
        "mp4",
        "--media_dir",
        str(media_dir),
        "--resolution",
        "854,480",
        "--fps",
        "15",
        str(scene_path),
    ]
    assert kwargs["timeout"] == 7.5
    assert result.argv == argv
    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.missing_executable is False
    assert result.stdout == "RENDER_STDOUT_SENTINEL"
    assert result.stderr == "RENDER_STDERR_SENTINEL"
    assert isinstance(result.elapsed_seconds, float)
    assert result.elapsed_seconds >= 0
    assert list(result.mp4_paths) == [final_mp4]


def test_manim_runner_preserves_traceback_and_all_nonzero_process_facts(
    tmp_path: Path,
) -> None:
    """A deliberate Manim exception is a captured render failure, not a timeout."""

    _require_contract()
    scene_path, media_dir, _final_mp4 = _scene_and_media(tmp_path)
    # A failed process must not make the pre-existing candidate look newly valid.
    for candidate in media_dir.rglob("*.mp4"):
        candidate.unlink()
    traceback = "Traceback (most recent call last):\nMANIM_TRACEBACK_SENTINEL\n"
    process = RecordingSubprocess(
        CompletedProcessFake(
            returncode=17,
            stdout="FAILED_STDOUT_SENTINEL",
            stderr=traceback,
        )
    )
    runner = ManimRunner(subprocess_run=process, timeout=3)

    result = runner.run(scene_path, media_dir=media_dir)

    assert result.exit_code == 17
    assert result.timed_out is False
    assert result.missing_executable is False
    assert result.stdout == "FAILED_STDOUT_SENTINEL"
    assert result.stderr == traceback
    assert "Traceback" in result.stderr
    assert result.elapsed_seconds >= 0
    assert list(result.mp4_paths) == []


def test_manim_runner_timeout_starts_and_terminates_a_complete_process_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Timeout is observable and the child is launched in its own process group."""

    _require_contract()
    scene_path, media_dir, _final_mp4 = _scene_and_media(tmp_path)
    for candidate in media_dir.rglob("*.mp4"):
        candidate.unlink()
    process = TimeoutSubprocess()
    killpg_calls: list[tuple[int, int]] = []

    def fake_killpg(pgid: int, sig: int) -> None:
        killpg_calls.append((pgid, sig))

    # The runner's process-group boundary is injectable; this fake keeps the
    # test independent of the host process table while requiring group setup.
    monkeypatch.setattr("video_pipeline.rendering.os.killpg", fake_killpg)
    runner = ManimRunner(subprocess_run=process, timeout=0.25, process_group_id=4242)

    result = runner.run(scene_path, media_dir=media_dir)

    assert result.timed_out is True
    assert result.missing_executable is False
    assert result.exit_code is None
    assert "PARTIAL_STDOUT_SENTINEL" in result.stdout
    assert "PARTIAL_STDERR_SENTINEL" in result.stderr
    assert result.mp4_paths == []
    assert process.calls[0][1].get("start_new_session") is True
    assert killpg_calls == [(4242, signal.SIGTERM)]


def test_manim_runner_distinguishes_missing_executable_from_timeout(
    tmp_path: Path,
) -> None:
    """An unavailable Python executable is not misreported as a timed-out render."""

    _require_contract()
    scene_path, media_dir, _final_mp4 = _scene_and_media(tmp_path)
    for candidate in media_dir.rglob("*.mp4"):
        candidate.unlink()
    process = MissingExecutableSubprocess()
    runner = ManimRunner(subprocess_run=process, timeout=1)

    result = runner.run(scene_path, media_dir=media_dir)

    assert result.missing_executable is True
    assert result.timed_out is False
    assert result.exit_code is None
    assert result.elapsed_seconds >= 0
    assert result.mp4_paths == []


def test_manim_runner_exports_observation_path_to_the_child_and_restores_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A subprocess scene can write facts without receiving a Python argument."""

    _require_contract()
    scene_path = tmp_path / "SubprocessScene.py"
    scene_path.write_text("# controlled scene boundary\n", encoding="utf-8")
    media_dir = tmp_path / "media"
    process = ObservationWritingSubprocess(media_dir)
    monkeypatch.setenv("VIDEO_PIPELINE_OBSERVATION_PATH", "caller-owned.json")
    monkeypatch.setenv("VIDEO_PIPELINE_MEDIA_DIR", "caller-owned-media")

    result = ManimRunner(subprocess_run=process).run(scene_path, media_dir)

    assert result.exit_code == 0
    assert process.observation_path == str(media_dir / "visual-facts.json")
    assert (
        json.loads(Path(process.observation_path).read_text(encoding="utf-8"))["scene_id"]
        == "subprocess"
    )
    assert os.environ["VIDEO_PIPELINE_OBSERVATION_PATH"] == "caller-owned.json"
    assert os.environ["VIDEO_PIPELINE_MEDIA_DIR"] == "caller-owned-media"


def test_rendering_audit_contract() -> None:
    """Inventory the rendering contract tests without product calls."""

    behavioral_tests = (
        "test_manim_runner_uses_exact_cairo_mp4_media_and_854x480_15fps_argv",
        "test_manim_runner_preserves_traceback_and_all_nonzero_process_facts",
        "test_manim_runner_timeout_starts_and_terminates_a_complete_process_group",
        "test_manim_runner_distinguishes_missing_executable_from_timeout",
    )

    assert all(callable(globals().get(name)) for name in behavioral_tests)
