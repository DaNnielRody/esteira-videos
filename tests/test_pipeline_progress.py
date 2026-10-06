"""Behavioral RED for the fine-grained RenderPipeline progress contract."""

from __future__ import annotations

import importlib
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path

import pytest
from test_pipeline import (
    RecordingProvider,
    RecordingRunner,
    RecordingValidator,
    RenderPlan,
    ValidationPlan,
    _response,
)
from test_project_lifecycle import _initialize_confirmed_project, _render_dependencies

from video_pipeline.expectations import SceneBeat, SceneExpectations
from video_pipeline.observation import FrameObservation, ObservationResult, ObservedShape
from video_pipeline.spec import SceneSpec


def _require_progress_contract() -> tuple[object, object, object, object, object]:
    """Guard the behavioral tests against a collection-time missing seam."""

    try:
        pipeline_module = importlib.import_module("video_pipeline.pipeline")
        video_module = importlib.import_module("video_pipeline.video")
        public_module = importlib.import_module("video_pipeline")
    except (ImportError, ModuleNotFoundError):
        pytest.fail("PIPELINE_PROGRESS_CONTRACT_MISSING")

    pipeline_type = getattr(pipeline_module, "RenderPipeline", None)
    event_type = getattr(pipeline_module, "PipelineEvent", None)
    stage_type = getattr(pipeline_module, "PipelineStage", None)
    project_event_type = getattr(video_module, "ProjectPipelineEvent", None)
    video_pipeline_type = getattr(video_module, "VideoPipeline", None)
    if (
        pipeline_type is None
        or event_type is None
        or stage_type is None
        or project_event_type is None
        or video_pipeline_type is None
        or getattr(public_module, "PipelineEvent", None) is not event_type
        or getattr(public_module, "PipelineStage", None) is not stage_type
        or getattr(public_module, "ProjectPipelineEvent", None) is not project_event_type
    ):
        pytest.fail("PIPELINE_PROGRESS_CONTRACT_MISSING")
    return pipeline_type, event_type, stage_type, project_event_type, video_pipeline_type


def _event_field(event: object, name: str) -> object:
    if isinstance(event, dict):
        return event[name]
    return getattr(event, name)


def _event_field_any(event: object, *names: str) -> object:
    for name in names:
        try:
            return _event_field(event, name)
        except (AttributeError, KeyError, TypeError):
            continue
    pytest.fail("PIPELINE_PROGRESS_CONTRACT_MISSING")


def _enum_value(value: object) -> object:
    return getattr(value, "value", value)


def _stage(event: object) -> str:
    return str(_enum_value(_event_field(event, "stage"))).lower()


def _state(event: object) -> str:
    return str(_enum_value(_event_field(event, "state"))).lower()


def _scene_with_expectations() -> SceneSpec:
    return SceneSpec(
        id="acceptance",
        scene_name="AcceptanceScene",
        description="Mostre um círculo no centro.",
        expect=SceneExpectations(
            max_shapes=1,
            beats=[SceneBeat(shape="circle", region="center")],
        ),
    )


def _frame(kind: str) -> FrameObservation:
    return FrameObservation(
        index=0,
        shapes=[
            ObservedShape(
                kind=kind,
                color="blue",
                center_x=0.5,
                center_y=0.5,
                area_fraction=0.05,
                extent=0.8,
            )
        ],
    )


class ProgressObserver:
    """Deterministic observer fake returning real-shaped frame evidence."""

    def __init__(self, rows: list[list[FrameObservation]]) -> None:
        self._rows = iter(rows)
        self.calls: list[tuple[Path, Path]] = []

    def observe(self, mp4_path: str | Path, frames_dir: str | Path) -> ObservationResult:
        media = Path(mp4_path)
        frames = Path(frames_dir)
        frames.mkdir(parents=True, exist_ok=True)
        self.calls.append((media, frames))
        return ObservationResult.success(next(self._rows))


class FailingProvider(RecordingProvider):
    """Fail at generation, before a candidate can reach rendering or observation."""

    def generate(self, request: object) -> object:
        del request
        raise RuntimeError("PROGRESS_PROVIDER_FAILURE_SENTINEL")


def _pipeline(
    pipeline_type: object,
    output_root: Path,
    run_id: str,
    *,
    observations: list[list[FrameObservation]],
    validation_results: tuple[bool, ...] = (True,),
    runner_exit_codes: tuple[int, ...] = (0,),
    provider_failure: bool = False,
) -> tuple[object, ProgressObserver, RecordingProvider, RecordingRunner]:
    trace: list[str] = []
    if provider_failure:
        provider = FailingProvider([], trace)
    else:
        provider = RecordingProvider(
            [
                _response("PROGRESS_CODE_01_SENTINEL", "PROGRESS_RESPONSE_01_SENTINEL"),
                _response("PROGRESS_CODE_02_SENTINEL", "PROGRESS_RESPONSE_02_SENTINEL"),
            ],
            trace,
        )
    runner = RecordingRunner(
        [
            RenderPlan(
                exit_code=exit_code,
                stdout="PROGRESS_STDOUT_SENTINEL",
                stderr="",
                argv_marker=f"PROGRESS_ARGV_{index}_SENTINEL",
                candidate_name=f"progress-{index}.mp4",
            )
            for index, exit_code in enumerate(runner_exit_codes, start=1)
        ],
        trace,
    )
    validator = RecordingValidator(
        [
            ValidationPlan(valid, (f"PROGRESS_VALIDATION_{index}_SENTINEL",))
            for index, valid in enumerate(validation_results, start=1)
        ],
        trace,
    )
    observer = ProgressObserver(observations)
    pipeline = pipeline_type(
        provider=provider,
        runner=runner,
        validator=validator,
        observer=observer,
        output_root=output_root,
        id_factory=lambda: run_id,
    )
    return pipeline, observer, provider, runner


def _normalise_run_paths(value: object, root: Path) -> object:
    if isinstance(value, Path):
        return value.as_posix().replace(root.resolve().as_posix(), "<RUN_ROOT>")
    if isinstance(value, tuple):
        return tuple(_normalise_run_paths(item, root) for item in value)
    if isinstance(value, dict):
        return {key: _normalise_run_paths(item, root) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalise_run_paths(item, root) for item in value]
    if isinstance(value, str):
        return value.replace(str(root.resolve()), "<RUN_ROOT>")
    return value


def _normalise_result(result: object, root: Path) -> object:
    value: object = asdict(result) if is_dataclass(result) else vars(result)
    return _normalise_run_paths(value, root)


def test_progress_callback_emits_ordered_stages_with_internal_run_and_attempt(
    tmp_path: Path,
) -> None:
    pipeline_type, event_type, stage_type, _, _ = _require_progress_contract()
    pipeline, observer, _, _ = _pipeline(
        pipeline_type,
        tmp_path / "runs",
        "progress-run",
        observations=[[_frame("circle")]],
    )
    received: list[object] = []

    result = pipeline.render(  # type: ignore[attr-defined]
        _scene_with_expectations(),
        max_attempts=1,
        on_progress=received.append,
    )

    assert str(getattr(result.state, "value", result.state)).lower() == "success"
    assert len(observer.calls) == 1
    assert received
    assert all(isinstance(event, event_type) for event in received)
    assert all(isinstance(_event_field(event, "stage"), stage_type) for event in received)
    assert [_stage(event) for event in received] == [
        "generating",
        "unloading",
        "rendering",
        "validating",
        "observing",
        "terminal",
    ]
    assert [_event_field(event, "attempt") for event in received] == [1] * len(received)
    assert {_event_field(event, "run_id") for event in received} == {"progress-run"}
    assert _state(received[-1]) == "success"


def test_progress_retry_emits_correcting_after_rejected_observation(
    tmp_path: Path,
) -> None:
    pipeline_type, event_type, stage_type, _, _ = _require_progress_contract()
    pipeline, observer, provider, _ = _pipeline(
        pipeline_type,
        tmp_path / "runs",
        "retry-run",
        observations=[[_frame("square")], [_frame("circle")]],
        validation_results=(True, True),
        runner_exit_codes=(0, 0),
    )
    received: list[object] = []

    result = pipeline.render(  # type: ignore[attr-defined]
        _scene_with_expectations(),
        max_attempts=2,
        on_progress=received.append,
    )

    assert str(getattr(result.state, "value", result.state)).lower() == "success"
    assert len(provider.requests) == 2
    assert len(observer.calls) == 2
    assert all(isinstance(event, event_type) for event in received)
    assert all(isinstance(_event_field(event, "stage"), stage_type) for event in received)
    assert [_stage(event) for event in received] == [
        "generating",
        "unloading",
        "rendering",
        "validating",
        "observing",
        "correcting",
        "generating",
        "unloading",
        "rendering",
        "validating",
        "observing",
        "terminal",
    ]
    assert _event_field(received[0], "attempt") == 1
    assert _event_field(received[6], "attempt") == 2
    assert _event_field(received[-1], "attempt") == 2
    assert {_event_field(event, "run_id") for event in received} == {"retry-run"}


def test_progress_callback_failure_does_not_change_result_or_run_json(
    tmp_path: Path,
) -> None:
    pipeline_type, _, _, _, _ = _require_progress_contract()
    control_root = tmp_path / "control"
    raising_root = tmp_path / "raising"
    control_pipeline, _, _, _ = _pipeline(
        pipeline_type,
        control_root,
        "best-effort-run",
        observations=[[_frame("circle")]],
    )
    raising_pipeline, _, _, _ = _pipeline(
        pipeline_type,
        raising_root,
        "best-effort-run",
        observations=[[_frame("circle")]],
    )
    received: list[object] = []

    control_result = control_pipeline.render(  # type: ignore[attr-defined]
        _scene_with_expectations(),
        max_attempts=1,
    )

    def raising_callback(event: object) -> None:
        received.append(event)
        raise RuntimeError("PROGRESS_CALLBACK_SENTINEL")

    raising_result = raising_pipeline.render(  # type: ignore[attr-defined]
        _scene_with_expectations(),
        max_attempts=1,
        on_progress=raising_callback,
    )

    # Separate roots make only absolute paths differ; all result fields (state,
    # attempts, error, mp4, normalized and temporal facts) must otherwise match.
    assert _normalise_result(control_result, control_root) == _normalise_result(
        raising_result,
        raising_root,
    )
    assert Path(raising_result.mp4_path).read_bytes() == Path(control_result.mp4_path).read_bytes()  # type: ignore[attr-defined]
    control_run = Path(control_result.run_path)  # type: ignore[attr-defined]
    raising_run = Path(raising_result.run_path)  # type: ignore[attr-defined]
    control_document = json.loads((control_run / "run.json").read_text(encoding="utf-8"))
    raising_document = json.loads((raising_run / "run.json").read_text(encoding="utf-8"))
    assert _normalise_run_paths(control_document, control_root) == _normalise_run_paths(
        raising_document,
        raising_root,
    )
    assert control_document["state"] == raising_document["state"] == "success"
    assert len(received) == 6


def test_terminal_failure_marks_observation_not_applicable(
    tmp_path: Path,
) -> None:
    pipeline_type, _, _, _, _ = _require_progress_contract()
    pipeline, observer, _, runner = _pipeline(
        pipeline_type,
        tmp_path / "runs",
        "failed-run",
        observations=[],
        validation_results=(False,),
        runner_exit_codes=(0,),
    )
    received: list[object] = []

    result = pipeline.render(  # type: ignore[attr-defined]
        _scene_with_expectations(),
        max_attempts=1,
        on_progress=received.append,
    )

    assert str(getattr(result.state, "value", result.state)).lower() == "attempts_exhausted"
    assert observer.calls == []
    assert len(runner.calls) == 1
    assert [_stage(event) for event in received] == [
        "generating",
        "unloading",
        "rendering",
        "validating",
        "terminal",
    ]
    assert _stage(received[-1]) == "terminal"
    assert _state(received[-1]) == "attempts_exhausted"
    assert _enum_value(_event_field(received[-1], "observation")) == "not_applicable"


def test_video_pipeline_propagates_project_run_and_scene_id_in_progress_events(
    tmp_path: Path,
) -> None:
    _, event_type, stage_type, project_event_type, video_pipeline_type = (
        _require_progress_contract()
    )
    _, project_json = _initialize_confirmed_project(tmp_path)
    provider = importlib.import_module("test_project_render").FakeProvider(project_json)
    dependencies = _render_dependencies(provider)
    pipeline = video_pipeline_type(
        id_factory=lambda: "project-progress-run",
        **dependencies,
    )
    received: list[object] = []

    result = pipeline.render(  # type: ignore[attr-defined]
        project_json,
        max_attempts=1,
        on_progress=received.append,
    )

    assert str(getattr(result.state, "value", result.state)).lower() == "ready"
    project_document = json.loads(project_json.read_text(encoding="utf-8"))
    assert project_document["current_run"] == "project-progress-run"
    project_events = [event for event in received if isinstance(event, project_event_type)]
    assert project_events
    assert all(isinstance(event, event_type) for event in project_events)
    assert {
        _event_field_any(event, "project_run_id", "project_run") for event in project_events
    } == {"project-progress-run"}
    assert {_event_field(event, "scene_id") for event in project_events} == {
        "abertura",
        "explicacao",
    }
    for scene_id in ("abertura", "explicacao"):
        scene_events = [
            event for event in project_events if _event_field(event, "scene_id") == scene_id
        ]
        assert [_stage(event) for event in scene_events] == [
            "generating",
            "unloading",
            "rendering",
            "validating",
            "observing",
            "terminal",
        ]
        assert [_event_field(event, "attempt") for event in scene_events] == [1] * len(
            scene_events
        )
        internal_run_ids = {
            str(_event_field(event, "run_id")) for event in scene_events
        }
        assert len(internal_run_ids) == 1
        internal_run_id = next(iter(internal_run_ids))
        assert internal_run_id
        assert internal_run_id != "project-progress-run"
        assert all(isinstance(_event_field(event, "stage"), stage_type) for event in scene_events)
