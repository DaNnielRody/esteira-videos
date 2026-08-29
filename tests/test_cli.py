"""Behavioral tests for the ``video-pipeline render`` command boundary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import pytest

from video_pipeline.provider import (
    ProviderError,
    ProviderRequest,
    ProviderResponse,
    UnloadResult,
)
from video_pipeline.rendering import RenderResult
from video_pipeline.validation import ValidationResult

try:
    from video_pipeline.cli import main as _cli_main
except (ImportError, ModuleNotFoundError):
    _cli_main = None


DESCRIPTION = (
    "Mostre um círculo no centro. Depois transforme-o em um quadrado e mova-o para a direita."
)


def _require_cli() -> object:
    """Require the CLI entrypoint from inside a behavioral test."""

    if _cli_main is None:
        pytest.fail("RITL_CLI_CONTRACT_MISSING")
    return _cli_main


def _write_scene(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "scene_name": "AcceptanceScene",
                "description": DESCRIPTION,
            }
        ),
        encoding="utf-8",
    )
    return path


class CliProvider:
    """Deterministic provider boundary for CLI-only observations."""

    def __init__(
        self,
        responses: Iterable[ProviderResponse],
        *,
        failure: Exception | None = None,
    ) -> None:
        self._responses = iter(responses)
        self._failure = failure
        self.requests: list[ProviderRequest] = []
        self.unload_calls = 0

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        if self._failure is not None:
            raise self._failure
        return next(self._responses)

    def unload(self) -> UnloadResult:
        self.unload_calls += 1
        return UnloadResult(ok=True, raw_response={"response": "CLI_UNLOAD_SENTINEL"})


class CliRunner:
    """Renderer fake that leaves an observable MP4 path in each attempt."""

    def __init__(self, exit_codes: Iterable[int]) -> None:
        self._exit_codes = iter(exit_codes)
        self.calls: list[tuple[Path, Path]] = []

    def run(self, scene_path: str | Path, media_dir: str | Path) -> RenderResult:
        scene = Path(scene_path)
        media = Path(media_dir)
        self.calls.append((scene, media))
        exit_code = next(self._exit_codes)
        candidate = (
            media
            / "videos"
            / scene.stem
            / "480p15"
            / f"cli-{len(self.calls)}.mp4"
        )
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_bytes(f"CLI_MP4_{len(self.calls)}".encode("utf-8"))
        return RenderResult(
            argv=["python", "-m", "manim", "render", f"CLI_ARGV_{len(self.calls)}"],
            exit_code=exit_code,
            timed_out=False,
            missing_executable=False,
            stdout=f"CLI_STDOUT_{len(self.calls)}",
            stderr="",
            elapsed_seconds=0.001,
            mp4_paths=[candidate],
        )


class CliValidator:
    """Independent validation fake with a bounded valid/invalid sequence."""

    def __init__(self, valid_values: Iterable[bool]) -> None:
        self._valid_values = iter(valid_values)
        self.paths: list[Path] = []

    def validate(self, path: str | Path) -> ValidationResult:
        candidate = Path(path)
        self.paths.append(candidate)
        valid = next(self._valid_values)
        return ValidationResult(
            path=candidate,
            valid=valid,
            reasons=[] if valid else ["CLI_INVALID_MP4_SENTINEL"],
            width=854 if valid else None,
            height=480 if valid else None,
            duration_seconds=1.0 if valid else None,
            size_bytes=candidate.stat().st_size if valid else None,
        )


def _response(code: str, marker: str) -> ProviderResponse:
    return ProviderResponse(code=code, raw_response={"response": marker})


def _run_directory(root: Path) -> Path:
    runs = [candidate for candidate in root.iterdir() if candidate.is_dir()]
    assert len(runs) == 1
    return runs[0]


def _invoke(
    main: object,
    argv: list[str],
    *,
    provider: CliProvider,
    runner: CliRunner,
    validator: CliValidator,
    output_root: Path,
    run_id: str,
) -> int:
    return main(  # type: ignore[operator, no-any-return]
        argv,
        provider=provider,
        runner=runner,
        validator=validator,
        output_root=output_root,
        id_factory=lambda: run_id,
    )


def test_video_pipeline_render_success_reports_terminal_state_and_paths(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The render command returns zero and reports SUCCESS, MP4, and run paths."""

    main = _require_cli()
    scene_path = _write_scene(tmp_path / "scene.json")
    output_root = tmp_path / "runs"
    provider = CliProvider([_response("CLI_SUCCESS_CODE", "CLI_SUCCESS_RAW")])
    runner = CliRunner([0])
    validator = CliValidator([True])

    exit_code = _invoke(
        main,
        [
            "render",
            str(scene_path),
            "--temperature",
            "0.35",
            "--seed",
            "23",
        ],
        provider=provider,
        runner=runner,
        validator=validator,
        output_root=output_root,
        run_id="cli-success-run",
    )

    captured = capsys.readouterr()
    run_directory = _run_directory(output_root)
    mp4_paths = sorted(output_root.rglob("*.mp4"))
    assert exit_code == 0
    assert "SUCCESS" in captured.out.upper()
    assert str(run_directory) in captured.out
    assert mp4_paths
    assert str(mp4_paths[0]) in captured.out
    assert provider.requests[0].temperature == 0.35
    assert provider.requests[0].seed == 23
    assert json.loads((run_directory / "run.json").read_text(encoding="utf-8"))["state"] == (
        "success"
    )


def test_video_pipeline_render_exhaustion_is_nonzero_and_preserves_run_path(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The command exposes bounded ATTEMPTS_EXHAUSTED as a nonzero result."""

    main = _require_cli()
    scene_path = _write_scene(tmp_path / "scene.json")
    output_root = tmp_path / "runs"
    provider = CliProvider(
        [
            _response("CLI_EXHAUSTED_CODE_01", "CLI_EXHAUSTED_RAW_01"),
            _response("CLI_EXHAUSTED_CODE_02", "CLI_EXHAUSTED_RAW_02"),
        ]
    )
    runner = CliRunner([0, 0])
    validator = CliValidator([False, False])

    exit_code = _invoke(
        main,
        ["render", str(scene_path), "--max-attempts", "2"],
        provider=provider,
        runner=runner,
        validator=validator,
        output_root=output_root,
        run_id="cli-exhausted-run",
    )

    captured = capsys.readouterr()
    run_directory = _run_directory(output_root)
    assert exit_code != 0
    assert "ATTEMPTS_EXHAUSTED" in captured.out.upper()
    assert str(run_directory) in captured.out
    assert len(list(run_directory.glob("attempt-*"))) == 2
    assert json.loads((run_directory / "run.json").read_text(encoding="utf-8"))["state"] == (
        "attempts_exhausted"
    )


def test_video_pipeline_render_provider_error_is_nonzero_and_observable(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Provider failure becomes a terminal provider-error report without a fake success."""

    main = _require_cli()
    scene_path = _write_scene(tmp_path / "scene.json")
    output_root = tmp_path / "runs"
    provider = CliProvider(
        [],
        failure=ProviderError("CLI_PROVIDER_FAILURE_SENTINEL"),
    )
    runner = CliRunner([])
    validator = CliValidator([])

    exit_code = _invoke(
        main,
        ["render", str(scene_path)],
        provider=provider,
        runner=runner,
        validator=validator,
        output_root=output_root,
        run_id="cli-provider-error-run",
    )

    captured = capsys.readouterr()
    run_directory = _run_directory(output_root)
    assert exit_code != 0
    assert "PROVIDER_ERROR" in captured.out.upper()
    assert "CLI_PROVIDER_FAILURE_SENTINEL" in captured.out
    assert str(run_directory) in captured.out
    assert json.loads((run_directory / "run.json").read_text(encoding="utf-8"))["state"] == (
        "provider_error"
    )


def test_video_pipeline_calibrate_writes_axis_error_rates(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The labeled sensor report is a repeatable CLI gate."""

    main = _require_cli()
    output = tmp_path / "sensor-calibration.json"
    golden = Path(__file__).parent / "golden"

    exit_code = main(  # type: ignore[operator, no-any-return]
        ["calibrate", "--golden-root", str(golden), "--output", str(output)]
    )

    assert exit_code == 0
    assert str(output.resolve()) in capsys.readouterr().out
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["scenes"] == 15
    assert document["axes"]["latex"]["true_positives"] == 1
    assert document["axes"]["latex"]["true_negatives"] == 1
    assert document["axes"]["text"]["true_positives"] == 2
    assert document["axes"]["text"]["true_negatives"] == 4
    assert document["axes"]["shape_count"]["false_positive_rate"] == 0.0
    assert set(document["axes"]) == {
        "shape_count",
        "shape",
        "color",
        "region",
        "motion",
        "latex",
        "text",
    }
    assert all(axis["false_positives"] == 0 for axis in document["axes"].values())
    assert all(axis["false_negatives"] == 0 for axis in document["axes"].values())


def test_prepare_study_materializes_ten_paired_control_and_treatment_specs(
    tmp_path: Path,
) -> None:
    """The few-shot experiment changes only the number of reference examples."""

    main = _require_cli()
    manifest = tmp_path / "study.json"
    cases = [
        {
            "schema_version": "1.0",
            "scene_name": f"StudyScene{index}",
            "description": f"Cena matemática controlada {index}",
            "topics": ["linear_algebra"],
        }
        for index in range(1, 11)
    ]
    manifest.write_text(
        json.dumps({"schema_version": "1.0", "name": "ai-math", "cases": cases}),
        encoding="utf-8",
    )
    output = tmp_path / "prepared"

    exit_code = main(  # type: ignore[operator, no-any-return]
        ["prepare-study", str(manifest), "--output-root", str(output)]
    )

    assert exit_code == 0
    controls = sorted((output / "control").glob("*.json"))
    treatments = sorted((output / "treatment").glob("*.json"))
    assert len(controls) == len(treatments) == 10
    for control_path, treatment_path in zip(controls, treatments, strict=True):
        control = json.loads(control_path.read_text(encoding="utf-8"))
        treatment = json.loads(treatment_path.read_text(encoding="utf-8"))
        assert control.pop("reference_examples") == 0
        assert treatment.pop("reference_examples") == 2
        assert control == treatment


def test_prepare_study_rejects_a_nonempty_destination(tmp_path: Path) -> None:
    """A rerun cannot silently mix stale samples into a paired experiment."""

    main = _require_cli()
    manifest = tmp_path / "study.json"
    cases = [
        {
            "schema_version": "1.0",
            "scene_name": f"StudyScene{index}",
            "description": f"Cena matemática controlada {index}",
            "topics": ["linear_algebra"],
        }
        for index in range(1, 11)
    ]
    manifest.write_text(
        json.dumps({"schema_version": "1.0", "name": "ai-math", "cases": cases}),
        encoding="utf-8",
    )
    output = tmp_path / "prepared"
    assert main(  # type: ignore[operator, no-any-return]
        ["prepare-study", str(manifest), "--output-root", str(output)]
    ) == 0
    stale = output / "control" / "stale.json"
    stale.write_text("{}", encoding="utf-8")

    second_exit = main(  # type: ignore[operator, no-any-return]
        ["prepare-study", str(manifest), "--output-root", str(output)]
    )

    assert second_exit == 1
    assert stale.is_file()


def test_cli_audit_contract() -> None:
    """Inventory the CLI contract tests without production imports."""

    behavioral_tests = (
        "test_video_pipeline_render_success_reports_terminal_state_and_paths",
        "test_video_pipeline_render_exhaustion_is_nonzero_and_preserves_run_path",
        "test_video_pipeline_render_provider_error_is_nonzero_and_observable",
        "test_video_pipeline_calibrate_writes_axis_error_rates",
        "test_prepare_study_materializes_ten_paired_control_and_treatment_specs",
    )

    assert all(callable(globals().get(name)) for name in behavioral_tests)
