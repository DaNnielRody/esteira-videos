"""Filesystem-integrity contracts for project render resumption."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from test_project_render import (
    FakeComposer,
    FakeFinalValidator,
    FakeManimRunner,
    FakeNormalizedValidator,
    FakeObserver,
    FakeProvider,
    FakeRawValidator,
    FakeTemporalNormalizer,
)
from test_project_resume import ResumableProvider
from test_selective_regeneration import _pipeline, _ready_base_run

from video_pipeline.video import VideoPipeline


def _failed_render_run(tmp_path: Path) -> tuple[Path, Path]:
    project_json, _, _ = _ready_base_run(tmp_path)
    provider = ResumableProvider(project_json)
    normalized_validator = FakeNormalizedValidator()
    pipeline = VideoPipeline(
        provider=provider,
        runner=FakeManimRunner(),
        validator=FakeRawValidator(),
        observer=FakeObserver(),
        temporal_normalizer=FakeTemporalNormalizer(normalized_validator),
        normalized_validator=normalized_validator,
        final_validator=FakeFinalValidator(),
        composer=FakeComposer(),
        id_factory=lambda: "run-002",
    )

    with pytest.raises(ValueError, match="scene explicacao render failed"):
        pipeline.render(project_json, max_attempts=1)
    return project_json, project_json.parent / "artifacts" / "run-002"


def test_failed_run_json_symlink_is_rejected_before_external_write(
    tmp_path: Path,
) -> None:
    project_json, run_path = _failed_render_run(tmp_path)
    run_json = run_path / "run.json"
    sentinel = tmp_path / "external-run.json"
    sentinel.write_bytes(run_json.read_bytes())
    run_json.unlink()
    run_json.symlink_to(sentinel)

    project_before = project_json.read_bytes()
    sentinel_before = sentinel.read_bytes()
    pipeline, provider, runner = _pipeline(project_json, "must-not-create-run")

    with pytest.raises(ValueError, match="run.json"):
        pipeline.render(project_json, max_attempts=1)

    assert project_json.read_bytes() == project_before
    assert sentinel.read_bytes() == sentinel_before
    assert run_json.is_symlink()
    assert provider.requests == []
    assert runner.scene_paths == []


def test_ready_base_run_json_symlink_is_rejected_before_provider(
    tmp_path: Path,
) -> None:
    project_json, _, _ = _ready_base_run(tmp_path)
    project_before = project_json.read_bytes()
    base_run_json = project_json.parent / "artifacts" / "run-001" / "run.json"
    sentinel = tmp_path / "external-base-run.json"
    sentinel.write_bytes(base_run_json.read_bytes())
    base_run_json.unlink()
    base_run_json.symlink_to(sentinel)
    sentinel_before = sentinel.read_bytes()

    pipeline, provider, runner = _pipeline(project_json, "must-not-create-run")

    with pytest.raises(ValueError, match="run.json"):
        pipeline.render(
            project_json,
            max_attempts=1,
            scene="abertura",
            base_run_id="run-001",
            correction="Use uma seta azul mais espessa",
        )

    assert project_json.read_bytes() == project_before
    assert sentinel.read_bytes() == sentinel_before
    assert base_run_json.is_symlink()
    assert not (project_json.parent / "artifacts" / "run-002").exists()
    assert provider.requests == []
    assert runner.scene_paths == []


def test_default_artifacts_symlink_is_rejected_before_external_read(
    tmp_path: Path,
) -> None:
    project_json, run_path = _failed_render_run(tmp_path)
    project = project_json.parent
    artifacts_root = project / "artifacts"
    external_artifacts = tmp_path / "external-artifacts"
    shutil.copytree(artifacts_root, external_artifacts)
    shutil.rmtree(artifacts_root)
    artifacts_root.symlink_to(external_artifacts, target_is_directory=True)

    external_run_json = external_artifacts / run_path.name / "run.json"
    external_before = external_run_json.read_bytes()
    project_before = project_json.read_bytes()
    pipeline, provider, runner = _pipeline(project_json, "must-not-create-run")

    with pytest.raises(ValueError, match="artifacts"):
        pipeline.render(project_json, max_attempts=1)

    assert project_json.read_bytes() == project_before
    assert external_run_json.read_bytes() == external_before
    assert artifacts_root.is_symlink()
    assert provider.requests == []
    assert runner.scene_paths == []


def test_explicit_canonical_artifacts_symlink_is_rejected_before_external_write(
    tmp_path: Path,
) -> None:
    project_json, run_path = _failed_render_run(tmp_path)
    project = project_json.parent
    artifacts_root = project / "artifacts"
    external_artifacts = tmp_path / "external-artifacts"
    shutil.copytree(artifacts_root, external_artifacts)
    shutil.rmtree(artifacts_root)
    artifacts_root.symlink_to(external_artifacts, target_is_directory=True)

    external_run_json = external_artifacts / run_path.name / "run.json"
    external_before = external_run_json.read_bytes()
    project_before = project_json.read_bytes()
    provider = FakeProvider(project_json)
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
        output_root=artifacts_root,
        id_factory=lambda: "must-not-create-run",
    )

    with pytest.raises(ValueError, match="artifacts"):
        pipeline.render(project_json, max_attempts=1)

    assert project_json.read_bytes() == project_before
    assert external_run_json.read_bytes() == external_before
    assert artifacts_root.is_symlink()
    assert provider.requests == []
    assert runner.scene_paths == []
