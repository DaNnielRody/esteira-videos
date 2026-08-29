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
    from video_pipeline.observation import FrameObservation, ObservedShape
    from video_pipeline.pipeline import RenderPipeline
    from video_pipeline.spec import SceneSpec, load_scene_spec
except (ImportError, ModuleNotFoundError):  # pragma: no cover - contract guard
    SceneBeat = None  # type: ignore[assignment,misc]
    SceneExpectations = None  # type: ignore[assignment,misc]
    FrameObservation = None  # type: ignore[assignment,misc]
    ObservedShape = None  # type: ignore[assignment,misc]
    RenderPipeline = None  # type: ignore[assignment,misc]
    SceneSpec = None  # type: ignore[assignment,misc]
    load_scene_spec = None  # type: ignore[assignment]


GOOD_CODE = "GOOD_SCENE_CODE_SENTINEL"
BAD_CODE = "BAD_SCENE_CODE_SENTINEL"


def _require_contract() -> None:
    if RenderPipeline is None or load_scene_spec is None:
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
        schema_version="1.0",
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

    def observe(self, mp4_path: Path, frames_dir: Path) -> list[FrameObservation]:
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
        return [FrameObservation(index=i, shapes=row) for i, row in enumerate(rows)]


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
        schema_version="1.0",
        scene_name="AcceptanceScene",
        description="circle, then square, then right",
    )

    result = pipeline.render(spec, max_attempts=1)

    assert str(getattr(result.state, "value", result.state)).upper() == "SUCCESS"


def test_scene_spec_loads_declared_expectations(tmp_path: Path) -> None:
    """The Scene Spec carries its semantic contract as strict, validated data."""

    _require_contract()
    path = tmp_path / "scene.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "scene_name": "AcceptanceScene",
                "description": "circle, then square, then right",
                "expect": {
                    "max_shapes": 1,
                    "beats": [
                        {"shape": "circle", "region": "center"},
                        {"shape": "square", "moved": "right"},
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    spec = load_scene_spec(path)

    assert spec.expect is not None
    assert spec.expect.max_shapes == 1
    assert [beat.shape for beat in spec.expect.beats] == ["circle", "square"]
    assert spec.expect.beats[1].moved == "right"


def test_scene_spec_rejects_an_unknown_beat_field(tmp_path: Path) -> None:
    """An unreadable expectation is a spec error, never a silently skipped check."""

    _require_contract()
    path = tmp_path / "scene.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "scene_name": "AcceptanceScene",
                "description": "circle",
                "expect": {"beats": [{"shape": "circle", "colour": "blue"}]},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_scene_spec(path)


def test_semantic_gate_audit_contract() -> None:
    """Inventory the semantic-gate evidence contract without production imports."""

    assert callable(globals().get("test_valid_mp4_showing_the_wrong_scene_is_not_success"))
    assert callable(globals().get("test_the_loop_corrects_a_semantic_failure"))
