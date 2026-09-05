"""Input validation contracts for whole-project render orchestration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_project_render import (
    FakeComposer,
    FakeFinalValidator,
    FakeManimRunner,
    FakeNormalizedValidator,
    FakeObserver,
    FakeRawValidator,
    FakeTemporalNormalizer,
)
from test_selective_regeneration import (
    _FailOnceProvider,
    _pipeline,
    _ready_base_run,
)

from video_pipeline.video import VideoPipeline


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.mark.parametrize(
    "max_attempts",
    (0, -1, True, 1.0),
    ids=("zero", "negative", "bool", "float"),
)
def test_invalid_max_attempts_has_no_project_or_run_side_effects(
    tmp_path: Path,
    max_attempts: object,
) -> None:
    project_json, _, _ = _ready_base_run(tmp_path)
    project_root = project_json.parent
    before_project = project_json.read_bytes()
    before_files = _snapshot(project_root)
    pipeline, provider, runner = _pipeline(project_json, "must-not-create-run")

    with pytest.raises(ValueError, match="max_attempts must be a positive integer"):
        pipeline.render(project_json, max_attempts=max_attempts)  # type: ignore[arg-type]

    assert project_json.read_bytes() == before_project
    assert _snapshot(project_root) == before_files
    assert provider.requests == []
    assert runner.scene_paths == []


def _failed_selective_run(tmp_path: Path) -> tuple[Path, Path]:
    project_json, _, _ = _ready_base_run(tmp_path)
    provider = _FailOnceProvider(project_json)
    runner = FakeManimRunner()
    normalized_validator = FakeNormalizedValidator()
    pipeline = VideoPipeline(
        provider=provider,
        runner=runner,
        validator=FakeRawValidator(),
        observer=FakeObserver(),
        temporal_normalizer=FakeTemporalNormalizer(normalized_validator),
        normalized_validator=normalized_validator,
        final_validator=FakeFinalValidator(),
        composer=FakeComposer(),
        id_factory=lambda: "run-002",
    )

    with pytest.raises(ValueError, match="scene abertura render failed"):
        pipeline.render(
            project_json,
            max_attempts=1,
            scene="abertura",
            base_run_id="run-001",
            correction="Use uma seta azul mais espessa",
        )
    return project_json, project_json.parent / "artifacts" / "run-002"


@pytest.mark.parametrize(
    "tamper",
    ("unknown_scene", "missing_base", "unsafe_base", "same_run"),
)
def test_invalid_selective_resume_metadata_has_no_side_effects(
    tmp_path: Path,
    tamper: str,
) -> None:
    project_json, run_path = _failed_selective_run(tmp_path)
    run_json = run_path / "run.json"
    run_document = json.loads(run_json.read_text(encoding="utf-8"))
    if tamper == "unknown_scene":
        run_document["selected_scene_id"] = "scene-inexistente"
    elif tamper == "missing_base":
        run_document.pop("base_run_id")
    elif tamper == "unsafe_base":
        run_document["base_run_id"] = "../run-001"
    else:
        run_document["base_run_id"] = "run-002"
    run_json.write_text(
        json.dumps(run_document, ensure_ascii=False),
        encoding="utf-8",
    )

    project_root = project_json.parent
    before_files = _snapshot(project_root)
    pipeline, provider, runner = _pipeline(project_json, "must-not-create-run")

    with pytest.raises(ValueError, match="(selective|base run)"):
        pipeline.render(project_json, max_attempts=1)

    assert _snapshot(project_root) == before_files
    assert provider.requests == []
    assert runner.scene_paths == []
