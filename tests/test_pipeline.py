"""Behavioral tests for the render-in-the-loop correction pipeline.

The public pipeline seam is guarded at collection time.  A missing seam is
reported from the behavioral test with the task's contract signature rather
than as a collection/import error.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pytest

from video_pipeline.provider import (
    ProviderRequest,
    ProviderResponse,
    UnloadResult,
)
from video_pipeline.rendering import RenderResult
from video_pipeline.spec import SceneSpec
from video_pipeline.validation import ValidationResult

try:
    from video_pipeline.pipeline import RenderPipeline as _RenderPipeline
except (ImportError, ModuleNotFoundError):
    _RenderPipeline = None


VALID_DESCRIPTION = (
    "Mostre um círculo no centro. Depois transforme-o em um quadrado e mova-o para a direita."
)
PROMPT_CONTEXT_SENTINEL = "PROMPT_CONTEXT_SENTINEL"


def _require_pipeline() -> type[object]:
    """Require the public pipeline seam from inside a behavioral test."""

    if _RenderPipeline is None:
        pytest.fail("RITL_CLI_CONTRACT_MISSING")
    return _RenderPipeline  # type: ignore[return-value]


def _scene(description: str = VALID_DESCRIPTION) -> SceneSpec:
    return SceneSpec(
        schema_version="1.0",
        scene_name="AcceptanceScene",
        description=description,
    )


def _response(code: str, raw_marker: str) -> ProviderResponse:
    return ProviderResponse(
        code=code,
        raw_response={"response": raw_marker, "model": "deterministic-test"},
    )


class RecordingProvider:
    """Deterministic provider fake at the declared provider boundary."""

    def __init__(self, responses: Iterable[ProviderResponse], events: list[str]) -> None:
        self._responses = iter(responses)
        self._events = events
        self.requests: list[ProviderRequest] = []
        self.responses: list[ProviderResponse] = []
        self.unload_results: list[UnloadResult] = []
        self.unload_count = 0

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        self._events.append(f"generate-{len(self.requests)}")
        response = next(self._responses)
        self.responses.append(response)
        return response

    def unload(self) -> UnloadResult:
        self.unload_count += 1
        self._events.append(f"unload-{self.unload_count}")
        result = UnloadResult(
            ok=True,
            raw_response={"response": f"UNLOAD_RESPONSE_{self.unload_count}"},
        )
        self.unload_results.append(result)
        return result


@dataclass(frozen=True)
class RenderPlan:
    exit_code: int
    stdout: str
    stderr: str
    argv_marker: str
    candidate_name: str


class RecordingRunner:
    """Fake renderer that writes attempt-local candidates and process facts."""

    def __init__(self, plans: Iterable[RenderPlan], events: list[str]) -> None:
        self._plans = iter(plans)
        self._events = events
        self.calls: list[tuple[Path, Path]] = []
        self.results: list[RenderResult] = []
        self.run_states_at_render: list[str] = []

    def run(self, scene_path: str | Path, media_dir: str | Path) -> RenderResult:
        scene = Path(scene_path)
        media = Path(media_dir)
        plan = next(self._plans)
        self.calls.append((scene, media))
        self._events.append(f"render-{len(self.calls)}")

        candidate = media / "videos" / scene.stem / "480p15" / plan.candidate_name
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_bytes(f"MP4_BYTES_{plan.candidate_name}".encode("utf-8"))
        run_json = media.parent.parent / "run.json"
        if run_json.exists():
            run_document = json.loads(run_json.read_text(encoding="utf-8"))
            self.run_states_at_render.append(str(run_document.get("state", "")))

        result = RenderResult(
            argv=["python", "-m", "manim", "render", plan.argv_marker],
            exit_code=plan.exit_code,
            timed_out=False,
            missing_executable=False,
            stdout=plan.stdout,
            stderr=plan.stderr,
            elapsed_seconds=0.001,
            mp4_paths=[candidate],
        )
        self.results.append(result)
        return result


@dataclass(frozen=True)
class ValidationPlan:
    valid: bool
    reasons: tuple[str, ...]
    width: int = 854
    height: int = 480
    duration_seconds: float = 1.0
    size_bytes: int = 1234


class RecordingValidator:
    """Fake independent validator returning one declared result per attempt."""

    def __init__(self, plans: Iterable[ValidationPlan], events: list[str]) -> None:
        self._plans = iter(plans)
        self._events = events
        self.paths: list[Path] = []
        self.results: list[ValidationResult] = []

    def validate(self, path: str | Path) -> ValidationResult:
        candidate = Path(path)
        plan = next(self._plans)
        self.paths.append(candidate)
        self._events.append(f"validate-{len(self.paths)}")
        result = ValidationResult(
            path=candidate,
            valid=plan.valid,
            reasons=list(plan.reasons),
            width=plan.width,
            height=plan.height,
            duration_seconds=plan.duration_seconds,
            size_bytes=plan.size_bytes,
        )
        self.results.append(result)
        return result


def _make_pipeline(
    pipeline_type: type[object],
    *,
    provider: RecordingProvider,
    runner: RecordingRunner,
    validator: RecordingValidator,
    output_root: Path,
    run_id: str,
) -> object:
    return pipeline_type(
        provider=provider,
        runner=runner,
        validator=validator,
        output_root=output_root,
        id_factory=lambda: run_id,
    )


def _run_directory(output_root: Path) -> Path:
    runs = [candidate for candidate in output_root.iterdir() if candidate.is_dir()]
    assert len(runs) == 1
    return runs[0]


def _attempt_directories(run_directory: Path) -> list[Path]:
    return sorted(
        candidate
        for candidate in run_directory.glob("attempt-*")
        if candidate.is_dir()
    )


def _artifact_text(directory: Path) -> str:
    """Read textual evidence without assuming private artifact filenames."""

    chunks: list[str] = []
    for artifact in sorted(directory.rglob("*")):
        if not artifact.is_file():
            continue
        try:
            chunks.append(artifact.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            continue
    return "\n".join(chunks)


def _assert_attempt_evidence(
    attempt: Path,
    *,
    mp4_path: Path,
    markers: Iterable[str],
) -> None:
    """Check preserved public evidence without prescribing private filenames."""

    files = [artifact for artifact in attempt.rglob("*") if artifact.is_file()]
    assert files
    assert all(artifact.stat().st_size > 0 for artifact in files)
    assert mp4_path.is_file()
    evidence = _artifact_text(attempt)
    assert str(mp4_path) in evidence
    for marker in markers:
        assert marker in evidence


def _state(result: object) -> str:
    value = getattr(result, "state")
    return str(getattr(value, "value", value)).upper()


def test_render_pipeline_happy_path_writes_one_success_attempt(tmp_path: Path) -> None:
    """One valid render produces one preserved attempt and terminal SUCCESS."""

    pipeline_type = _require_pipeline()
    events: list[str] = []
    provider = RecordingProvider(
        [_response("VALID_CODE_SENTINEL", "RAW_SUCCESS_RESPONSE_SENTINEL")], events
    )
    runner = RecordingRunner(
        [
            RenderPlan(
                exit_code=0,
                stdout="SUCCESS_STDOUT_SENTINEL",
                stderr="",
                argv_marker="SUCCESS_ARGV_SENTINEL",
                candidate_name="success.mp4",
            )
        ],
        events,
    )
    validator = RecordingValidator(
        [ValidationPlan(True, ("SUCCESS_VALIDATION_METADATA_SENTINEL",))], events
    )
    output_root = tmp_path / "runs"
    pipeline = _make_pipeline(
        pipeline_type,
        provider=provider,
        runner=runner,
        validator=validator,
        output_root=output_root,
        run_id="happy-run",
    )

    result = pipeline.render(_scene(), max_attempts=3)  # type: ignore[attr-defined]

    assert _state(result) == "SUCCESS"
    assert len(provider.requests) == 1
    assert provider.requests[0].schema_version == "1.0"
    assert provider.requests[0].scene_name == "AcceptanceScene"
    assert provider.requests[0].description == VALID_DESCRIPTION
    assert provider.responses == [
        _response("VALID_CODE_SENTINEL", "RAW_SUCCESS_RESPONSE_SENTINEL")
    ]
    assert provider.unload_count == 1
    assert provider.unload_results[0].ok is True
    assert provider.unload_results[0].raw_response == {
        "response": "UNLOAD_RESPONSE_1"
    }
    assert events[:3] == ["generate-1", "unload-1", "render-1"]
    assert runner.run_states_at_render == ["attempting"]
    assert runner.results[0].argv[-1] == "SUCCESS_ARGV_SENTINEL"
    assert runner.results[0].exit_code == 0
    assert runner.results[0].timed_out is False
    assert runner.results[0].stdout == "SUCCESS_STDOUT_SENTINEL"
    assert runner.results[0].stderr == ""
    assert len(validator.results) == 1
    assert validator.results[0].valid is True
    assert validator.results[0].reasons == ["SUCCESS_VALIDATION_METADATA_SENTINEL"]
    assert validator.results[0].width == 854
    assert validator.results[0].height == 480
    assert validator.results[0].duration_seconds == 1.0
    assert validator.results[0].size_bytes == 1234
    run_directory = _run_directory(output_root)
    attempts = _attempt_directories(run_directory)
    assert [attempt.name for attempt in attempts] == ["attempt-01"]
    run_document = json.loads((run_directory / "run.json").read_text(encoding="utf-8"))
    assert run_document["state"] == "success"
    run_text = json.dumps(run_document)
    assert "attempt-01" in run_text
    assert "success.mp4" in run_text
    success_mp4 = runner.results[0].mp4_paths[0]
    assert success_mp4.is_file()
    assert Path(getattr(result, "mp4_path")) == success_mp4
    assert Path(getattr(result, "run_path")) == run_directory
    _assert_attempt_evidence(
        attempts[0],
        mp4_path=success_mp4,
        markers=(
            "VALID_CODE_SENTINEL",
            "RAW_SUCCESS_RESPONSE_SENTINEL",
            "UNLOAD_RESPONSE_1",
            "SUCCESS_ARGV_SENTINEL",
            "SUCCESS_STDOUT_SENTINEL",
            "SUCCESS_VALIDATION_METADATA_SENTINEL",
            "854",
            "480",
            "1.0",
            "1234",
            "success",
        ),
    )


def test_render_pipeline_preserves_failure_and_corrects_only_after_validation(
    tmp_path: Path,
) -> None:
    """Correction receives the exact failed candidate and complete diagnostics."""

    pipeline_type = _require_pipeline()
    events: list[str] = []
    first_code = "FIRST_BROKEN_CODE_SENTINEL"
    provider = RecordingProvider(
        [
            _response(first_code, "FIRST_RAW_RESPONSE_SENTINEL"),
            _response("CORRECTED_CODE_SENTINEL", "CORRECTED_RAW_RESPONSE_SENTINEL"),
        ],
        events,
    )
    runner = RecordingRunner(
        [
            RenderPlan(
                exit_code=17,
                stdout="FIRST_STDOUT_SENTINEL",
                stderr="Traceback FIRST_TRACEBACK_SENTINEL",
                argv_marker="FIRST_ARGV_SENTINEL",
                candidate_name="first.mp4",
            ),
            RenderPlan(
                exit_code=0,
                stdout="CORRECTED_STDOUT_SENTINEL",
                stderr="",
                argv_marker="CORRECTED_ARGV_SENTINEL",
                candidate_name="corrected.mp4",
            ),
        ],
        events,
    )
    validator = RecordingValidator(
        [
            ValidationPlan(
                False,
                ("FIRST_VALIDATION_REASON_SENTINEL",),
                width=801,
                height=461,
                duration_seconds=0.0,
                size_bytes=9123,
            ),
            ValidationPlan(
                True,
                ("SECOND_VALIDATION_REASON_SENTINEL",),
                width=802,
                height=462,
                duration_seconds=2.5,
                size_bytes=9234,
            ),
        ],
        events,
    )
    output_root = tmp_path / "runs"
    pipeline = _make_pipeline(
        pipeline_type,
        provider=provider,
        runner=runner,
        validator=validator,
        output_root=output_root,
        run_id="correction-run",
    )

    scene = _scene(f"{VALID_DESCRIPTION} {PROMPT_CONTEXT_SENTINEL}")
    result = pipeline.render(scene, max_attempts=2)  # type: ignore[attr-defined]

    assert _state(result) == "SUCCESS"
    assert len(provider.requests) == 2
    correction = provider.requests[1]
    assert correction.previous_code == first_code
    diagnostics = dict(correction.diagnostics or {})
    assert correction.schema_version == "1.0"
    assert correction.scene_name == "AcceptanceScene"
    assert correction.description == scene.description
    assert diagnostics["argv"] == [
        "python",
        "-m",
        "manim",
        "render",
        "FIRST_ARGV_SENTINEL",
    ]
    assert diagnostics["exit_code"] == 17
    assert diagnostics["timeout"] is False
    assert diagnostics["stdout"] == "FIRST_STDOUT_SENTINEL"
    assert diagnostics["stderr"] == "Traceback FIRST_TRACEBACK_SENTINEL"
    assert diagnostics["validator_reasons"] == ["FIRST_VALIDATION_REASON_SENTINEL"]
    assert "17" in _artifact_text(_run_directory(output_root))

    assert provider.responses[0].code == first_code
    assert provider.responses[0].raw_response == {
        "response": "FIRST_RAW_RESPONSE_SENTINEL",
        "model": "deterministic-test",
    }
    assert provider.responses[1].code == "CORRECTED_CODE_SENTINEL"
    assert provider.responses[1].raw_response == {
        "response": "CORRECTED_RAW_RESPONSE_SENTINEL",
        "model": "deterministic-test",
    }
    assert provider.unload_results[0].ok is True
    assert provider.unload_results[0].raw_response == {
        "response": "UNLOAD_RESPONSE_1"
    }
    assert provider.unload_results[1].ok is True
    assert provider.unload_results[1].raw_response == {
        "response": "UNLOAD_RESPONSE_2"
    }
    assert runner.run_states_at_render == ["attempting", "correcting"]
    assert [render.exit_code for render in runner.results] == [17, 0]
    assert [render.timed_out for render in runner.results] == [False, False]
    assert runner.results[0].stdout == "FIRST_STDOUT_SENTINEL"
    assert runner.results[0].stderr == "Traceback FIRST_TRACEBACK_SENTINEL"
    assert runner.results[1].argv[-1] == "CORRECTED_ARGV_SENTINEL"
    assert runner.results[1].stdout == "CORRECTED_STDOUT_SENTINEL"
    assert runner.results[1].stderr == ""
    assert validator.results[0].valid is False
    assert validator.results[0].reasons == ["FIRST_VALIDATION_REASON_SENTINEL"]
    assert validator.results[0].width == 801
    assert validator.results[0].height == 461
    assert validator.results[0].duration_seconds == 0.0
    assert validator.results[0].size_bytes == 9123
    assert validator.results[1].valid is True
    assert validator.results[1].reasons == ["SECOND_VALIDATION_REASON_SENTINEL"]
    assert validator.results[1].width == 802
    assert validator.results[1].height == 462
    assert validator.results[1].duration_seconds == 2.5
    assert validator.results[1].size_bytes == 9234

    assert events.index("generate-2") > events.index("validate-1")
    assert events.index("unload-2") > events.index("generate-2")
    assert events.index("render-2") > events.index("unload-2")
    run_directory = _run_directory(output_root)
    attempts = _attempt_directories(run_directory)
    assert [attempt.name for attempt in attempts] == ["attempt-01", "attempt-02"]
    first_mp4 = runner.results[0].mp4_paths[0]
    corrected_mp4 = runner.results[1].mp4_paths[0]
    assert first_mp4.is_file()
    assert corrected_mp4.is_file()
    assert first_mp4 != corrected_mp4
    assert first_mp4.read_bytes() == b"MP4_BYTES_first.mp4"
    assert corrected_mp4.read_bytes() == b"MP4_BYTES_corrected.mp4"
    _assert_attempt_evidence(
        attempts[0],
        mp4_path=first_mp4,
        markers=(
            "FIRST_BROKEN_CODE_SENTINEL",
            "FIRST_RAW_RESPONSE_SENTINEL",
            "UNLOAD_RESPONSE_1",
            "FIRST_ARGV_SENTINEL",
            "FIRST_STDOUT_SENTINEL",
            "FIRST_TRACEBACK_SENTINEL",
            "FIRST_VALIDATION_REASON_SENTINEL",
            "801",
            "461",
            "0.0",
            "9123",
            "attempting",
        ),
    )
    first_preserved = _artifact_text(attempts[0])
    assert "CORRECTED_CODE_SENTINEL" not in first_preserved
    _assert_attempt_evidence(
        attempts[1],
        mp4_path=corrected_mp4,
        markers=(
            "FIRST_BROKEN_CODE_SENTINEL",
            "FIRST_ARGV_SENTINEL",
            "FIRST_STDOUT_SENTINEL",
            "FIRST_TRACEBACK_SENTINEL",
            "FIRST_VALIDATION_REASON_SENTINEL",
            PROMPT_CONTEXT_SENTINEL,
            "CORRECTED_CODE_SENTINEL",
            "CORRECTED_RAW_RESPONSE_SENTINEL",
            "UNLOAD_RESPONSE_2",
            "CORRECTED_ARGV_SENTINEL",
            "CORRECTED_STDOUT_SENTINEL",
            "SECOND_VALIDATION_REASON_SENTINEL",
            "802",
            "462",
            "2.5",
            "9234",
            "correcting",
            "success",
        ),
    )
    run_document = json.loads((run_directory / "run.json").read_text(encoding="utf-8"))
    assert run_document["state"] == "success"
    run_text = json.dumps(run_document)
    assert "attempt-01" in run_text
    assert "attempt-02" in run_text
    assert "corrected.mp4" in run_text
    assert Path(getattr(result, "mp4_path")) == corrected_mp4
    assert Path(getattr(result, "run_path")) == run_directory


def test_render_pipeline_exit_zero_with_invalid_validation_is_not_success(
    tmp_path: Path,
) -> None:
    """Exit zero cannot bypass the independent invalid-MP4 validation gate."""

    pipeline_type = _require_pipeline()
    events: list[str] = []
    provider = RecordingProvider(
        [_response("INVALID_MEDIA_CODE_SENTINEL", "INVALID_MEDIA_RAW_SENTINEL")],
        events,
    )
    runner = RecordingRunner(
        [
            RenderPlan(
                exit_code=0,
                stdout="EXIT_ZERO_STDOUT_SENTINEL",
                stderr="",
                argv_marker="EXIT_ZERO_ARGV_SENTINEL",
                candidate_name="invalid.mp4",
            )
        ],
        events,
    )
    validator = RecordingValidator(
        [ValidationPlan(False, ("INVALID_MP4_SENTINEL",))], events
    )
    output_root = tmp_path / "runs"
    pipeline = _make_pipeline(
        pipeline_type,
        provider=provider,
        runner=runner,
        validator=validator,
        output_root=output_root,
        run_id="invalid-media-run",
    )

    result = pipeline.render(_scene(), max_attempts=1)  # type: ignore[attr-defined]

    assert _state(result) == "ATTEMPTS_EXHAUSTED"
    assert len(provider.requests) == 1
    assert provider.unload_count == 1
    run_document = json.loads(
        (_run_directory(output_root) / "run.json").read_text(encoding="utf-8")
    )
    assert run_document["state"] == "attempts_exhausted"


def test_render_pipeline_exhaustion_preserves_every_bounded_attempt(tmp_path: Path) -> None:
    """Bounded exhaustion keeps every failed attempt and its candidate evidence."""

    pipeline_type = _require_pipeline()
    events: list[str] = []
    provider = RecordingProvider(
        [
            _response("EXHAUSTED_CODE_01_SENTINEL", "EXHAUSTED_RAW_01_SENTINEL"),
            _response("EXHAUSTED_CODE_02_SENTINEL", "EXHAUSTED_RAW_02_SENTINEL"),
            _response("EXHAUSTED_CODE_03_SENTINEL", "EXHAUSTED_RAW_03_SENTINEL"),
        ],
        events,
    )
    runner = RecordingRunner(
        [
            RenderPlan(
                0,
                "EXHAUSTED_STDOUT_01",
                "EXHAUSTED_STDERR_01",
                "EXHAUSTED_ARGV_01",
                "one.mp4",
            ),
            RenderPlan(
                0,
                "EXHAUSTED_STDOUT_02",
                "EXHAUSTED_STDERR_02",
                "EXHAUSTED_ARGV_02",
                "two.mp4",
            ),
            RenderPlan(
                0,
                "EXHAUSTED_STDOUT_03",
                "EXHAUSTED_STDERR_03",
                "EXHAUSTED_ARGV_03",
                "three.mp4",
            ),
        ],
        events,
    )
    validator = RecordingValidator(
        [
            ValidationPlan(
                False,
                ("EXHAUSTED_REASON_01",),
                width=811,
                height=471,
                duration_seconds=0.0,
                size_bytes=9311,
            ),
            ValidationPlan(
                False,
                ("EXHAUSTED_REASON_02",),
                width=812,
                height=472,
                duration_seconds=0.0,
                size_bytes=9312,
            ),
            ValidationPlan(
                False,
                ("EXHAUSTED_REASON_03",),
                width=813,
                height=473,
                duration_seconds=0.0,
                size_bytes=9313,
            ),
        ],
        events,
    )
    output_root = tmp_path / "runs"
    pipeline = _make_pipeline(
        pipeline_type,
        provider=provider,
        runner=runner,
        validator=validator,
        output_root=output_root,
        run_id="exhausted-run",
    )

    result = pipeline.render(_scene(), max_attempts=3)  # type: ignore[attr-defined]

    assert _state(result) == "ATTEMPTS_EXHAUSTED"
    assert len(provider.requests) == 3
    assert provider.unload_count == 3
    assert result.mp4_path is None  # type: ignore[attr-defined]
    run_directory = _run_directory(output_root)
    assert Path(getattr(result, "run_path")) == run_directory
    attempts = _attempt_directories(run_directory)
    assert [attempt.name for attempt in attempts] == [
        "attempt-01",
        "attempt-02",
        "attempt-03",
    ]
    assert runner.run_states_at_render == ["attempting", "correcting", "correcting"]
    assert [render.exit_code for render in runner.results] == [0, 0, 0]
    assert [render.timed_out for render in runner.results] == [False, False, False]
    assert [render.missing_executable for render in runner.results] == [False, False, False]
    assert [render.stdout for render in runner.results] == [
        "EXHAUSTED_STDOUT_01",
        "EXHAUSTED_STDOUT_02",
        "EXHAUSTED_STDOUT_03",
    ]
    assert [render.stderr for render in runner.results] == [
        "EXHAUSTED_STDERR_01",
        "EXHAUSTED_STDERR_02",
        "EXHAUSTED_STDERR_03",
    ]
    assert [validator_result.valid for validator_result in validator.results] == [
        False,
        False,
        False,
    ]
    assert [validator_result.reasons for validator_result in validator.results] == [
        ["EXHAUSTED_REASON_01"],
        ["EXHAUSTED_REASON_02"],
        ["EXHAUSTED_REASON_03"],
    ]
    assert [validator_result.width for validator_result in validator.results] == [811, 812, 813]
    assert [validator_result.height for validator_result in validator.results] == [471, 472, 473]
    assert [validator_result.duration_seconds for validator_result in validator.results] == [
        0.0,
        0.0,
        0.0,
    ]
    assert [validator_result.size_bytes for validator_result in validator.results] == [9311, 9312, 9313]

    codes = [
        "EXHAUSTED_CODE_01_SENTINEL",
        "EXHAUSTED_CODE_02_SENTINEL",
        "EXHAUSTED_CODE_03_SENTINEL",
    ]
    raw_responses = [
        "EXHAUSTED_RAW_01_SENTINEL",
        "EXHAUSTED_RAW_02_SENTINEL",
        "EXHAUSTED_RAW_03_SENTINEL",
    ]
    argv_markers = [
        "EXHAUSTED_ARGV_01",
        "EXHAUSTED_ARGV_02",
        "EXHAUSTED_ARGV_03",
    ]
    stdout_markers = [
        "EXHAUSTED_STDOUT_01",
        "EXHAUSTED_STDOUT_02",
        "EXHAUSTED_STDOUT_03",
    ]
    stderr_markers = [
        "EXHAUSTED_STDERR_01",
        "EXHAUSTED_STDERR_02",
        "EXHAUSTED_STDERR_03",
    ]
    validation_markers = [
        "EXHAUSTED_REASON_01",
        "EXHAUSTED_REASON_02",
        "EXHAUSTED_REASON_03",
    ]
    metadata = [(811, 471, "9311"), (812, 472, "9312"), (813, 473, "9313")]
    candidate_names = ["one.mp4", "two.mp4", "three.mp4"]

    assert [response.code for response in provider.responses] == codes
    assert [
        response.raw_response["response"] for response in provider.responses
    ] == raw_responses
    assert [
        unload.raw_response["response"] for unload in provider.unload_results
    ] == [
        "UNLOAD_RESPONSE_1",
        "UNLOAD_RESPONSE_2",
        "UNLOAD_RESPONSE_3",
    ]
    for index in range(1, len(provider.requests)):
        correction = provider.requests[index]
        assert correction.previous_code == codes[index - 1]
        diagnostics = dict(correction.diagnostics or {})
        assert diagnostics["argv"] == runner.results[index - 1].argv
        assert diagnostics["exit_code"] == runner.results[index - 1].exit_code
        assert diagnostics["timeout"] is runner.results[index - 1].timed_out
        assert diagnostics["stdout"] == runner.results[index - 1].stdout
        assert diagnostics["stderr"] == runner.results[index - 1].stderr
        assert diagnostics["validator_reasons"] == validator.results[index - 1].reasons

    for index, (attempt, candidate_name) in enumerate(
        zip(attempts, candidate_names, strict=True)
    ):
        width, height, size = metadata[index]
        candidate = runner.results[index].mp4_paths[0]
        assert candidate.name == candidate_name
        assert candidate.is_file()
        assert candidate.read_bytes() == f"MP4_BYTES_{candidate_name}".encode("utf-8")
        markers = (
            codes[index],
            raw_responses[index],
            f"UNLOAD_RESPONSE_{index + 1}",
            argv_markers[index],
            stdout_markers[index],
            stderr_markers[index],
            validation_markers[index],
            str(width),
            str(height),
            "0.0",
            size,
            "failed",
            "attempts_exhausted",
        )
        if index:
            markers += (
                codes[index - 1],
                argv_markers[index - 1],
                stdout_markers[index - 1],
                stderr_markers[index - 1],
            )
        _assert_attempt_evidence(attempt, mp4_path=candidate, markers=markers)

    run_document = json.loads((run_directory / "run.json").read_text(encoding="utf-8"))
    assert run_document["state"] == "attempts_exhausted"
    run_text = json.dumps(run_document)
    assert "attempts_exhausted" in run_text
    for attempt, candidate_name in zip(attempts, candidate_names, strict=True):
        assert attempt.name in run_text
        assert candidate_name in run_text
    assert not any(
        candidate.name == "accepted.mp4"
        for candidate in run_directory.rglob("*.mp4")
    )
    assert len({attempt.resolve() for attempt in attempts}) == 3


def test_pipeline_audit_contract() -> None:
    """Inventory the pipeline contract tests without production imports."""

    behavioral_tests = (
        "test_render_pipeline_happy_path_writes_one_success_attempt",
        "test_render_pipeline_preserves_failure_and_corrects_only_after_validation",
        "test_render_pipeline_exit_zero_with_invalid_validation_is_not_success",
        "test_render_pipeline_exhaustion_preserves_every_bounded_attempt",
    )

    assert all(callable(globals().get(name)) for name in behavioral_tests)
