"""Sequential render-in-the-loop pipeline with preserved run evidence."""

from __future__ import annotations

import ast
import json
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from video_pipeline.expectations import SceneExpectations, check_expectations
from video_pipeline.observation import FrameObservation, SceneObserver
from video_pipeline.prompts import build_prompt
from video_pipeline.provider import (
    LLMProvider,
    OllamaProvider,
    ProviderRequest,
    ProviderResponse,
    UnloadResult,
)
from video_pipeline.rendering import ManimRunner, RenderResult
from video_pipeline.spec import SceneSpec
from video_pipeline.validation import RenderValidator, ValidationResult
from video_pipeline.workspace import RunHandle, RunWorkspace

_PARTIAL_MOVIE_DIR = "partial_movie_files"


class PipelineState(StrEnum):
    """Observable states of one render run."""

    ATTEMPTING = "attempting"
    CORRECTING = "correcting"
    SUCCESS = "success"
    PROVIDER_ERROR = "provider_error"
    ATTEMPTS_EXHAUSTED = "attempts_exhausted"


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Terminal result returned by :class:`RenderPipeline`."""

    state: PipelineState
    run_path: Path
    mp4_path: Path | None = None
    error: str | None = None
    attempts: int = 0

    @property
    def terminal_state(self) -> PipelineState:
        """Alias useful to callers that name the state explicitly."""

        return self.state

    @property
    def output_path(self) -> Path | None:
        """Alias for the accepted MP4 path, when the run succeeded."""

        return self.mp4_path


# The longer name is retained as a discoverable public result type.
RenderPipelineResult = PipelineResult
TerminalState = PipelineState


class RenderPipeline:
    """Run provider generation, unload, Manim, and validation serially."""

    def __init__(
        self,
        *,
        provider: LLMProvider | None = None,
        runner: ManimRunner | None = None,
        validator: RenderValidator | None = None,
        observer: SceneObserver | None = None,
        output_root: str | Path = Path("artifacts/runs"),
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.provider = provider if provider is not None else OllamaProvider()
        self.runner = runner if runner is not None else ManimRunner()
        self.validator = validator if validator is not None else RenderValidator()
        self.observer = observer if observer is not None else SceneObserver()
        # Resolve once so CLI output is an absolute, copy/pasteable run path.
        self.output_root = Path(output_root).resolve()
        self.id_factory = id_factory

    def render(self, spec: SceneSpec, max_attempts: int = 3) -> PipelineResult:
        """Generate, render, validate, and correct up to ``max_attempts`` times."""

        _validate_max_attempts(max_attempts)
        workspace = self._new_workspace()
        run = workspace.create_run()
        run_document = _new_run_document(run, spec, max_attempts)
        _write_json(run.path / "run.json", run_document)

        previous_code: str | None = None
        diagnostics: dict[str, object] | None = None
        state = PipelineState.ATTEMPTING

        for attempt_number in range(1, max_attempts + 1):
            attempt = run.create_attempt()
            request = ProviderRequest(
                schema_version=spec.schema_version,
                scene_name=spec.scene_name,
                description=spec.description,
                previous_code=previous_code,
                diagnostics=diagnostics,
            )
            attempt_record: dict[str, object] = {
                "attempt": attempt_number,
                "path": str(attempt.path),
                "initial_state": state.value,
                "state": state.value,
                "state_history": [state.value],
            }
            attempts = run_document["attempts"]
            if not isinstance(attempts, list):
                raise RuntimeError("run document attempts must be a list")
            attempts.append(attempt_record)
            _set_run_state(run_document, state)
            _write_json(run.path / "run.json", run_document)
            self._write_request_artifacts(attempt.path, request)

            response, generation_error = self._generate(request)
            if generation_error is not None or response is None:
                error = generation_error or "provider returned no response"
                unload, unload_error = self._unload()
                if unload_error is not None:
                    error = f"{error}\nUnload failed:\n{unload_error}"
                _write_json(
                    attempt.path / "provider_error.json",
                    {"error": error, "traceback": error},
                )
                _write_json(
                    attempt.path / "unload.json",
                    {
                        "ok": unload.ok if unload is not None else False,
                        "raw_response": unload.raw_response if unload is not None else None,
                        "error": unload_error,
                    },
                )
                error_response = {"error": error}
                _write_json(attempt.path / "response.json", error_response)
                self._finish_attempt(
                    attempt.path,
                    attempt_record,
                    state=PipelineState.PROVIDER_ERROR,
                    terminal_state=PipelineState.PROVIDER_ERROR,
                    extra={"error": error},
                )
                _set_run_state(run_document, PipelineState.PROVIDER_ERROR)
                _write_json(run.path / "run.json", run_document)
                return PipelineResult(
                    state=PipelineState.PROVIDER_ERROR,
                    run_path=run.path,
                    error=error,
                    attempts=attempt_number,
                )

            response_document = {"code": response.code, "raw_response": response.raw_response}
            _write_json(attempt.path / "response.json", response_document)

            unload, unload_error = self._unload()
            if unload_error is not None or unload is None or not unload.ok:
                error = unload_error or "provider unload did not report success"
                unload_document: dict[str, object] = {"ok": False, "error": error}
                if unload is not None:
                    unload_document["raw_response"] = unload.raw_response
                _write_json(attempt.path / "unload.json", unload_document)
                _write_text(attempt.path / "scene.py", response.code)
                self._finish_attempt(
                    attempt.path,
                    attempt_record,
                    state=PipelineState.PROVIDER_ERROR,
                    terminal_state=PipelineState.PROVIDER_ERROR,
                    extra={"error": error},
                )
                _set_run_state(run_document, PipelineState.PROVIDER_ERROR)
                _write_json(run.path / "run.json", run_document)
                return PipelineResult(
                    state=PipelineState.PROVIDER_ERROR,
                    run_path=run.path,
                    error=error,
                    attempts=attempt_number,
                )

            _write_json(
                attempt.path / "unload.json",
                {"ok": unload.ok, "raw_response": unload.raw_response},
            )
            _write_text(attempt.path / "scene.py", response.code)

            render_result = self._render(attempt.path / "scene.py", attempt.media_dir)
            render_document = _render_document(render_result)
            _write_json(attempt.path / "render.json", render_document)
            candidate = _first_candidate(render_result)
            validation = self._validate(candidate, attempt.path)
            # Observe the finished video first, then lint the source. A failing
            # attempt should carry both what the frames showed and why the code
            # produced it, so one correction can address the whole failure.
            validation = self._with_observed_scene(
                validation, candidate, attempt.path, spec.expect
            )
            validation = _with_scene_semantics(validation, response.code)
            _write_json(
                attempt.path / "validation.json", _validation_document(validation)
            )
            diagnostics = _diagnostics(render_result, validation)
            _write_json(attempt.path / "diagnostics.json", diagnostics)

            # Keep this exact gate visible: process exit alone never accepts a run.
            if render_result.exit_code == 0 and validation.valid:
                self._finish_attempt(
                    attempt.path,
                    attempt_record,
                    state=PipelineState.SUCCESS,
                    terminal_state=PipelineState.SUCCESS,
                    extra={
                        "render": _render_document(render_result),
                        "validation": _validation_document(validation),
                        "mp4_path": str(candidate) if candidate is not None else None,
                    },
                )
                _set_run_state(run_document, PipelineState.SUCCESS)
                attempt_record.update(
                    {
                        "mp4_path": str(candidate) if candidate is not None else None,
                        "render": _render_document(render_result),
                        "validation": _validation_document(validation),
                    }
                )
                _write_json(run.path / "run.json", run_document)
                return PipelineResult(
                    state=PipelineState.SUCCESS,
                    run_path=run.path,
                    mp4_path=candidate,
                    attempts=attempt_number,
                )

            self._finish_attempt(
                attempt.path,
                attempt_record,
                state=PipelineState.ATTEMPTS_EXHAUSTED
                if attempt_number == max_attempts
                else PipelineState.CORRECTING,
                terminal_state=PipelineState.CORRECTING
                if attempt_number < max_attempts
                else PipelineState.ATTEMPTS_EXHAUSTED,
                extra={
                    "render": _render_document(render_result),
                    "validation": _validation_document(validation),
                    "mp4_path": str(candidate) if candidate is not None else None,
                    "diagnostics": diagnostics,
                },
            )
            attempt_record.update(
                {
                    "state": "failed",
                    "render": _render_document(render_result),
                    "validation": _validation_document(validation),
                    "mp4_path": str(candidate) if candidate is not None else None,
                }
            )

            if attempt_number == max_attempts:
                _set_run_state(run_document, PipelineState.ATTEMPTS_EXHAUSTED)
                self._mark_terminal_state(
                    run_document,
                    PipelineState.ATTEMPTS_EXHAUSTED,
                )
                _write_json(run.path / "run.json", run_document)
                return PipelineResult(
                    state=PipelineState.ATTEMPTS_EXHAUSTED,
                    run_path=run.path,
                    attempts=attempt_number,
                )

            previous_code = response.code
            state = PipelineState.CORRECTING
            _set_run_state(run_document, state)
            _write_json(run.path / "run.json", run_document)

        # The loop always returns at a terminal branch; retain a defensive error.
        _set_run_state(run_document, PipelineState.ATTEMPTS_EXHAUSTED)
        _write_json(run.path / "run.json", run_document)
        return PipelineResult(
            state=PipelineState.ATTEMPTS_EXHAUSTED,
            run_path=run.path,
            attempts=max_attempts,
        )

    def _new_workspace(self) -> RunWorkspace:
        if self.id_factory is None:
            return RunWorkspace(root=self.output_root)
        return RunWorkspace(root=self.output_root, id_factory=self.id_factory)

    def _write_request_artifacts(
        self,
        attempt_path: Path,
        request: ProviderRequest,
    ) -> None:
        document = _request_document(request)
        _write_json(attempt_path / "request.json", document)
        prompt = build_prompt(request)
        _write_text(attempt_path / "prompt.txt", prompt)
        _write_json(
            attempt_path / "prompt_context.json",
            {"request": document, "prompt": prompt},
        )

    def _generate(
        self,
        request: ProviderRequest,
    ) -> tuple[ProviderResponse | None, str | None]:
        try:
            return self.provider.generate(request), None
        except Exception:
            return None, traceback.format_exc()

    def _unload(self) -> tuple[UnloadResult | None, str | None]:
        try:
            return self.provider.unload(), None
        except Exception:
            return None, traceback.format_exc()

    def _render(self, scene_path: Path, media_dir: Path) -> RenderResult:
        try:
            return self.runner.run(scene_path, media_dir)
        except Exception:
            return RenderResult(
                argv=[],
                exit_code=None,
                timed_out=False,
                missing_executable=False,
                stdout="",
                stderr=traceback.format_exc(),
                elapsed_seconds=0.0,
                mp4_paths=[],
            )

    def _validate(self, candidate: Path | None, attempt_path: Path) -> ValidationResult:
        target = candidate or attempt_path / "media" / "missing.mp4"
        try:
            result = self.validator.validate(target)
        except Exception:
            return ValidationResult(
                path=target,
                valid=False,
                reasons=[f"validator failed:\n{traceback.format_exc()}"],
            )
        if candidate is None and result.valid:
            return ValidationResult(
                path=target,
                valid=False,
                reasons=["MP4 candidate is missing", *result.reasons],
                width=result.width,
                height=result.height,
                duration_seconds=result.duration_seconds,
                size_bytes=result.size_bytes,
            )
        return result

    def _with_observed_scene(
        self,
        validation: ValidationResult,
        candidate: Path | None,
        attempt_path: Path,
        expectations: SceneExpectations | None,
    ) -> ValidationResult:
        """Reject a valid MP4 whose frames contradict the scene specification.

        Only a run that already produced a probeable video can be observed, so
        an attempt that failed earlier keeps its original, more precise reason.
        """

        if expectations is None or candidate is None or not validation.valid:
            return validation
        frames, error = self._observe(candidate, attempt_path / "observation")
        _write_json(
            attempt_path / "observation.json",
            {
                "expectations": _expectations_document(expectations),
                "error": error,
                "frames": [_frame_document(frame) for frame in frames],
            },
        )
        if error is not None:
            return _rejected(validation, [f"scene observation failed:\n{error}"])
        reasons = check_expectations(frames, expectations)
        if not reasons:
            return validation
        return _rejected(validation, reasons)

    def _observe(
        self,
        candidate: Path,
        frames_dir: Path,
    ) -> tuple[list[FrameObservation], str | None]:
        try:
            return self.observer.observe(candidate, frames_dir), None
        except Exception:
            return [], traceback.format_exc()

    def _finish_attempt(
        self,
        attempt_path: Path,
        attempt_record: dict[str, object],
        *,
        state: PipelineState,
        terminal_state: PipelineState,
        extra: Mapping[str, object],
    ) -> None:
        history = attempt_record.get("state_history")
        if not isinstance(history, list):
            history = []
        recorded_state = (
            "failed"
            if state in {PipelineState.CORRECTING, PipelineState.ATTEMPTS_EXHAUSTED}
            else state.value
        )
        if recorded_state not in history:
            history.append(recorded_state)
        attempt_record["state_history"] = history
        attempt_record["state"] = recorded_state
        attempt_record["terminal_state"] = terminal_state.value
        attempt_record.update(extra)
        document = {
            "attempt": attempt_record.get("attempt"),
            "initial_state": attempt_record.get("initial_state"),
            "state": recorded_state,
            "state_history": history,
            "terminal_state": terminal_state.value,
            **dict(extra),
        }
        _write_json(attempt_path / "state.json", document)

    def _mark_terminal_state(
        self,
        run_document: dict[str, object],
        terminal_state: PipelineState,
    ) -> None:
        attempts = run_document.get("attempts")
        if not isinstance(attempts, list):
            return
        for item in attempts:
            if not isinstance(item, dict):
                continue
            item["terminal_state"] = terminal_state.value
            path = item.get("path")
            if not isinstance(path, str):
                continue
            for state_path in (Path(path) / "state.json",):
                try:
                    loaded: object = json.loads(state_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    loaded = {}
                state_document = loaded
                if not isinstance(state_document, dict):
                    state_document = {}
                state_document["terminal_run_state"] = terminal_state.value
                _write_json(state_path, state_document)


# Animations that put their first mobject argument on screen.
_INTRODUCING_ANIMATIONS = frozenset(
    {
        "Create",
        "DrawBorderThenFill",
        "FadeIn",
        "GrowFromCenter",
        "GrowFromEdge",
        "GrowFromPoint",
        "ShowCreation",
        "SpiralIn",
        "Write",
    }
)
# ``Transform(a, b)`` leaves ``a`` on screen and consumes ``b``.
_CONSUMING_TRANSFORMS = frozenset({"Transform", "TransformFromCopy"})


def _rejected(validation: ValidationResult, reasons: list[str]) -> ValidationResult:
    """Carry an existing validation forward as invalid, adding new reasons."""

    return ValidationResult(
        path=validation.path,
        valid=False,
        reasons=[*validation.reasons, *reasons],
        width=validation.width,
        height=validation.height,
        duration_seconds=validation.duration_seconds,
        size_bytes=validation.size_bytes,
    )


def _expectations_document(expectations: SceneExpectations) -> dict[str, object]:
    return {
        "max_shapes": expectations.max_shapes,
        "beats": [
            {
                "shape": beat.shape,
                "color": beat.color,
                "region": beat.region,
                "moved": beat.moved,
            }
            for beat in expectations.beats
        ],
    }


def _frame_document(frame: FrameObservation) -> dict[str, object]:
    return {
        "index": frame.index,
        "shapes": [
            {
                "kind": shape.kind,
                "color": shape.color,
                "center_x": round(shape.center_x, 4),
                "center_y": round(shape.center_y, 4),
                "area_fraction": round(shape.area_fraction, 5),
                "extent": round(shape.extent, 4),
            }
            for shape in frame.shapes
        ],
    }


def _with_scene_semantics(
    validation: ValidationResult,
    code: str,
) -> ValidationResult:
    """Reject a render whose scene code cannot show what it claims to show.

    Manim exits zero and writes a probeable MP4 for a scene that animates a
    mobject the audience never sees, so process status and container validity
    cannot decide this class on their own.  Surfacing it here keeps it inside
    the correction loop instead of shipping a silently wrong video.
    """

    reasons = _scene_semantics_reasons(code)
    if not reasons:
        return validation
    return ValidationResult(
        path=validation.path,
        valid=False,
        reasons=[*validation.reasons, *reasons],
        width=validation.width,
        height=validation.height,
        duration_seconds=validation.duration_seconds,
        size_bytes=validation.size_bytes,
    )


def _scene_semantics_reasons(code: str) -> list[str]:
    """Return every observed off-screen animation in one scene candidate."""

    try:
        tree = ast.parse(code)
    except SyntaxError:
        # A syntax error is already fatal at render time with a better message.
        return []

    reasons: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "construct":
            reasons.extend(_construct_reasons(node))
    return reasons


def _construct_reasons(construct: ast.FunctionDef) -> list[str]:
    """Check one ``construct`` body in statement order."""

    assigned = {
        target.id
        for node in ast.walk(construct)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    on_screen: set[str] = set()
    reasons: list[str] = []
    for call in sorted(
        (node for node in ast.walk(construct) if isinstance(node, ast.Call)),
        key=_position,
    ):
        method = _self_method(call)
        if method == "add":
            on_screen.update(_names(call.args))
            continue
        if method != "play":
            continue
        introduced, consumed = _play_targets(call)
        for name in sorted(_referenced_names(call) & assigned):
            if name in on_screen or name in introduced or name in consumed:
                continue
            reasons.append(
                f"scene animates `{name}`, which was never added to the scene; "
                "after Transform(a, b) the mobject on screen is `a`, so later "
                "steps must animate `a`"
            )
        on_screen.update(introduced)
    return reasons


def _position(node: ast.Call) -> tuple[int, int]:
    """Order calls the way they appear in the generated source."""

    return (node.lineno, node.col_offset)


def _self_method(call: ast.Call) -> str | None:
    """Return the method name for a ``self.<name>(...)`` call."""

    func = call.func
    if not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Name):
        return None
    return func.attr if func.value.id == "self" else None


def _play_targets(call: ast.Call) -> tuple[set[str], set[str]]:
    """Return the mobjects one ``self.play`` introduces and the ones it consumes."""

    introduced: set[str] = set()
    consumed: set[str] = set()
    for node in ast.walk(call):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        name = node.func.id
        if name in _INTRODUCING_ANIMATIONS and node.args:
            introduced.update(_names(node.args[:1]))
        elif name in _CONSUMING_TRANSFORMS:
            introduced.update(_names(node.args[:1]))
            consumed.update(_target_names(node.args[1:2]))
    return introduced, consumed


def _names(nodes: list[ast.expr]) -> set[str]:
    return {node.id for node in nodes if isinstance(node, ast.Name)}


def _target_names(nodes: list[ast.expr]) -> set[str]:
    """Return the base names a transform target is written against."""

    names: set[str] = set()
    for node in nodes:
        for inner in ast.walk(node):
            if isinstance(inner, ast.Name):
                names.add(inner.id)
    return names


def _referenced_names(call: ast.Call) -> set[str]:
    return {node.id for node in ast.walk(call) if isinstance(node, ast.Name)}


def _validate_max_attempts(max_attempts: int) -> None:
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int):
        raise ValueError("max_attempts must be a positive integer")
    if max_attempts <= 0:
        raise ValueError("max_attempts must be a positive integer")


def _new_run_document(
    run: RunHandle,
    spec: SceneSpec,
    max_attempts: int,
) -> dict[str, object]:
    return {
        "run_id": run.path.name,
        "run_path": str(run.path),
        "state": PipelineState.ATTEMPTING.value,
        "state_history": [PipelineState.ATTEMPTING.value],
        "max_attempts": max_attempts,
        "scene": {
            "schema_version": spec.schema_version,
            "scene_name": spec.scene_name,
            "description": spec.description,
        },
        "attempts": [],
    }


def _set_run_state(document: dict[str, object], state: PipelineState) -> None:
    document["state"] = state.value
    history = document.get("state_history")
    if not isinstance(history, list):
        history = []
    if state.value not in history:
        history.append(state.value)
    document["state_history"] = history


def _first_candidate(result: RenderResult) -> Path | None:
    if not result.mp4_paths:
        # Keep a deterministic path in diagnostics without creating a false artifact.
        return None
    candidates = [Path(path) for path in result.mp4_paths]
    # Manim also emits per-animation fragments under ``partial_movie_files``.
    # Each fragment probes as a valid MP4, so it must never be mistaken for the
    # combined scene output.
    combined = [
        candidate
        for candidate in candidates
        if _PARTIAL_MOVIE_DIR not in candidate.parts
    ]
    return (combined or candidates)[0]


def _request_document(request: ProviderRequest) -> dict[str, object]:
    return {
        "schema_version": request.schema_version,
        "scene_name": request.scene_name,
        "description": request.description,
        "previous_code": request.previous_code,
        "diagnostics": _json_value(request.diagnostics),
    }


def _render_document(result: RenderResult) -> dict[str, object]:
    return {
        "argv": list(result.argv),
        "exit_code": result.exit_code,
        "timeout": result.timed_out,
        "missing_executable": result.missing_executable,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "elapsed_seconds": result.elapsed_seconds,
        "mp4_paths": [str(path) for path in result.mp4_paths],
    }


def _validation_document(result: ValidationResult) -> dict[str, object]:
    return {
        "path": str(result.path),
        "valid": result.valid,
        "reasons": list(result.reasons),
        "validator_reasons": list(result.reasons),
        "width": result.width,
        "height": result.height,
        "duration_seconds": result.duration_seconds,
        "size_bytes": result.size_bytes,
    }


def _diagnostics(
    render_result: RenderResult,
    validation: ValidationResult,
) -> dict[str, object]:
    rendered = _render_document(render_result)
    validated = _validation_document(validation)
    return {
        **rendered,
        "validation": validated,
        "validator_reasons": list(validation.reasons),
    }


def _write_text(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(
            _json_value(value),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )


def _json_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return str(value)


__all__ = [
    "PipelineResult",
    "PipelineState",
    "RenderPipeline",
    "RenderPipelineResult",
    "TerminalState",
]
