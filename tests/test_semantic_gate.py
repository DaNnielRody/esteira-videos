"""Behavioral tests for the semantic-fidelity gate inside the render loop."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_pipeline.provider import ProviderRequest, ProviderResponse, UnloadResult
from video_pipeline.rendering import RenderResult
from video_pipeline.validation import ValidationResult

try:
    from video_pipeline.expectations import SceneBeat, SceneExpectations
    from video_pipeline.observation import FrameObservation, ObservationResult, ObservedShape
    from video_pipeline.pipeline import RenderPipeline
    from video_pipeline.spec import SceneSpec
except (ImportError, ModuleNotFoundError):  # pragma: no cover - contract guard
    SceneBeat = None  # type: ignore[assignment,misc]
    SceneExpectations = None  # type: ignore[assignment,misc]
    FrameObservation = None  # type: ignore[assignment,misc]
    ObservedShape = None  # type: ignore[assignment,misc]
    ObservationResult = None  # type: ignore[assignment,misc]
    RenderPipeline = None  # type: ignore[assignment,misc]
    SceneSpec = None  # type: ignore[assignment,misc]


GOOD_CODE = "GOOD_SCENE_CODE_SENTINEL"
BAD_CODE = "BAD_SCENE_CODE_SENTINEL"


def _require_contract() -> None:
    if RenderPipeline is None:
        pytest.fail("SEMANTIC_GATE_CONTRACT_MISSING")
    if not hasattr(SceneSpec, "model_fields") or "expect" not in SceneSpec.model_fields:
        pytest.fail("SEMANTIC_GATE_CONTRACT_MISSING")


def _shape(kind: str, center_x: float, color: str = "white") -> ObservedShape:
    return ObservedShape(
        kind=kind,
        color=color,
        center_x=center_x,
        center_y=0.5,
        area_fraction=0.05,
        extent=0.8,
    )


def _expectations() -> SceneExpectations:
    return SceneExpectations(
        max_shapes=1,
        beats=[
            SceneBeat(shape="circle", region="center"),
            SceneBeat(shape="square", region="center"),
            SceneBeat(shape="square", moved="right"),
        ],
    )


def _spec() -> SceneSpec:
    return SceneSpec(
        id="acceptance",
        scene_name="AcceptanceScene",
        description="circle, then square, then right",
        expect=_expectations(),
    )


class ScriptedProvider:
    """Return a fixed candidate per attempt and record the correction context."""

    def __init__(self, codes: list[str]) -> None:
        self.codes = codes
        self.requests: list[ProviderRequest] = []

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        code = self.codes[min(len(self.requests) - 1, len(self.codes) - 1)]
        return ProviderResponse(code=code, raw_response={"response": code})

    def unload(self) -> UnloadResult:
        return UnloadResult(ok=True, raw_response={"response": "UNLOADED"})


class RecordingRunner:
    """Always succeed and produce one MP4 path inside the attempt media root."""

    def run(self, scene_path: Path, media_dir: Path) -> RenderResult:
        media = Path(media_dir)
        media.mkdir(parents=True, exist_ok=True)
        candidate = media / "scene.mp4"
        candidate.write_bytes(b"MP4_SENTINEL")
        return RenderResult(
            argv=["RENDER_ARGV_SENTINEL"],
            exit_code=0,
            timed_out=False,
            missing_executable=False,
            stdout="RENDER_STDOUT_SENTINEL",
            stderr="",
            elapsed_seconds=0.1,
            mp4_paths=[candidate],
        )


class AcceptingValidator:
    """Accept the container so only the semantic gate can reject the attempt."""

    def validate(self, path: Path) -> ValidationResult:
        return ValidationResult(
            path=Path(path),
            valid=True,
            reasons=[],
            width=854,
            height=480,
            duration_seconds=2.0,
            size_bytes=1024,
        )


class ScriptedObserver:
    """Return a storyboard chosen by the candidate code under observation."""

    def __init__(self) -> None:
        self.calls: list[Path] = []

    def observe(self, mp4_path: Path, frames_dir: Path) -> ObservationResult:
        self.calls.append(Path(frames_dir))
        Path(frames_dir).mkdir(parents=True, exist_ok=True)
        good = Path(mp4_path).parent.parent / "scene.py"
        rows: list[list[ObservedShape]]
        if good.is_file() and good.read_text(encoding="utf-8") == GOOD_CODE:
            rows = [
                [_shape("circle", 0.50)],
                [_shape("square", 0.50)],
                [_shape("square", 0.64)],
            ]
        else:
            # The duplicated-mobject failure: two squares on screen at the end.
            rows = [
                [_shape("circle", 0.50)],
                [_shape("square", 0.50)],
                [_shape("square", 0.50), _shape("square", 0.64)],
            ]
        return ObservationResult.success(
            [FrameObservation(index=i, shapes=row) for i, row in enumerate(rows)]
        )


class CrashingObserver:
    """Represent an unavailable frame sensor, not a semantically wrong scene."""

    def observe(self, mp4_path: Path, frames_dir: Path) -> list[FrameObservation]:
        raise RuntimeError("FRAME_SENSOR_CRASH_SENTINEL")


class CrashingLatexValidator:
    """Represent an unexpected failure inside the LaTeX sensor boundary."""

    def observe(self, expectations: object, frames_dir: Path, evidence_dir: Path) -> object:
        raise RuntimeError("LATEX_SENSOR_CRASH_SENTINEL")


def _pipeline(provider: ScriptedProvider, output_root: Path) -> RenderPipeline:
    return RenderPipeline(
        provider=provider,
        runner=RecordingRunner(),
        validator=AcceptingValidator(),
        observer=ScriptedObserver(),
        output_root=output_root,
        id_factory=lambda: "semantic-run",
    )


def test_valid_mp4_showing_the_wrong_scene_is_not_success(tmp_path: Path) -> None:
    """A probeable MP4 whose frames contradict the spec must never be SUCCESS."""

    _require_contract()
    provider = ScriptedProvider([BAD_CODE])
    pipeline = _pipeline(provider, tmp_path / "runs")

    result = pipeline.render(_spec(), max_attempts=1)

    assert str(getattr(result.state, "value", result.state)).upper() == "ATTEMPTS_EXHAUSTED"
    assert result.mp4_path is None


def test_semantic_failure_reaches_the_provider_as_a_diagnostic(tmp_path: Path) -> None:
    """The correction prompt must carry what the frames actually showed."""

    _require_contract()
    provider = ScriptedProvider([BAD_CODE])
    pipeline = _pipeline(provider, tmp_path / "runs")

    pipeline.render(_spec(), max_attempts=2)

    assert len(provider.requests) == 2
    correction = provider.requests[1]
    assert correction.previous_code == BAD_CODE
    reasons = dict(correction.diagnostics or {}).get("validator_reasons")
    assert isinstance(reasons, list)
    assert any("2 shapes" in str(reason) for reason in reasons)


def test_a_semantically_correct_scene_still_succeeds(tmp_path: Path) -> None:
    """The gate accepts a storyboard that satisfies every declared beat."""

    _require_contract()
    provider = ScriptedProvider([GOOD_CODE])
    pipeline = _pipeline(provider, tmp_path / "runs")

    result = pipeline.render(_spec(), max_attempts=1)

    assert str(getattr(result.state, "value", result.state)).upper() == "SUCCESS"
    assert result.mp4_path is not None


def test_the_loop_corrects_a_semantic_failure(tmp_path: Path) -> None:
    """A bad first candidate followed by a good one terminates in SUCCESS."""

    _require_contract()
    provider = ScriptedProvider([BAD_CODE, GOOD_CODE])
    pipeline = _pipeline(provider, tmp_path / "runs")

    result = pipeline.render(_spec(), max_attempts=3)

    assert str(getattr(result.state, "value", result.state)).upper() == "SUCCESS"
    assert len(provider.requests) == 2


def test_observation_evidence_is_preserved_per_attempt(tmp_path: Path) -> None:
    """Each attempt keeps the storyboard the verdict was taken from."""

    _require_contract()
    provider = ScriptedProvider([BAD_CODE])
    pipeline = _pipeline(provider, tmp_path / "runs")

    result = pipeline.render(_spec(), max_attempts=1)

    attempt = result.run_path / "attempt-01"
    document = json.loads((attempt / "observation.json").read_text(encoding="utf-8"))
    assert document["frames"]
    assert any(len(frame["shapes"]) == 2 for frame in document["frames"])


def test_a_spec_without_expectations_skips_the_semantic_gate(tmp_path: Path) -> None:
    """Semantic checking is opt-in; an unannotated spec keeps the old contract."""

    _require_contract()
    provider = ScriptedProvider([BAD_CODE])
    pipeline = _pipeline(provider, tmp_path / "runs")
    spec = SceneSpec(
        id="acceptance",
        scene_name="AcceptanceScene",
        description="circle, then square, then right",
    )

    result = pipeline.render(spec, max_attempts=1)

    assert str(getattr(result.state, "value", result.state)).upper() == "SUCCESS"


def test_sensor_failure_is_terminal_and_never_sent_to_qwen(tmp_path: Path) -> None:
    """Infrastructure failure must not consume retries by blaming correct code."""

    _require_contract()
    provider = ScriptedProvider([GOOD_CODE])
    pipeline = RenderPipeline(
        provider=provider,
        runner=RecordingRunner(),
        validator=AcceptingValidator(),
        observer=CrashingObserver(),
        output_root=tmp_path / "runs",
        id_factory=lambda: "sensor-error-run",
    )

    result = pipeline.render(_spec(), max_attempts=3)

    assert str(getattr(result.state, "value", result.state)).upper() == "SENSOR_ERROR"
    assert len(provider.requests) == 1
    observation = json.loads(
        (result.run_path / "attempt-01" / "observation.json").read_text(encoding="utf-8")
    )
    assert observation["sensor"]["status"] == "failure"
    assert observation["sensor"]["failure"]["code"] == "observer_exception"
    assert "FRAME_SENSOR_CRASH_SENTINEL" in observation["sensor"]["failure"]["detail"]


def test_latex_sensor_exception_is_terminal_and_never_sent_to_qwen(tmp_path: Path) -> None:
    """Unexpected LaTeX verifier crashes still close the run as sensor failure."""

    _require_contract()
    provider = ScriptedProvider([GOOD_CODE])
    pipeline = RenderPipeline(
        provider=provider,
        runner=RecordingRunner(),
        validator=AcceptingValidator(),
        observer=ScriptedObserver(),
        latex_validator=CrashingLatexValidator(),
        output_root=tmp_path / "runs",
        id_factory=lambda: "latex-sensor-error-run",
    )

    result = pipeline.render(_spec(), max_attempts=3)

    assert result.state.value == "sensor_error"
    assert len(provider.requests) == 1
    validation = json.loads(
        (result.run_path / "attempt-01" / "validation.json").read_text(encoding="utf-8")
    )
    assert validation["sensor_failure"]["code"] == "latex_validator_exception"
    assert "LATEX_SENSOR_CRASH_SENTINEL" in validation["sensor_failure"]["detail"]


def test_semantic_gate_audit_contract() -> None:
    """Inventory the semantic-gate evidence contract without production imports."""

    assert callable(globals().get("test_valid_mp4_showing_the_wrong_scene_is_not_success"))
    assert callable(globals().get("test_the_loop_corrects_a_semantic_failure"))
