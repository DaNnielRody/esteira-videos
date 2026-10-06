"""Public admission contracts for serialized WebService jobs."""

from __future__ import annotations

import json
from collections.abc import Callable
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
from test_web_service import (
    _BlockingPipelineFactory,
    _create_confirmed_projects,
    _job_id,
    _make_service,
)
from test_web_ui import _CanonicalPipelineFactory, _FakeAudioProbe, _NoSilence

from video_pipeline.provider import ProviderRequest, ProviderResponse
from video_pipeline.video import ProjectPipelineEvent, VideoPipeline, VideoResult
from video_pipeline.web import WebService
from video_pipeline.web_errors import StateConflictError


def test_same_project_render_is_rejected_while_existing_job_is_running(
    tmp_path: Path,
) -> None:
    """A second render cannot allocate a job or draft for the same project."""

    factory = _BlockingPipelineFactory(expected_terminal=1)
    job_ids = iter(("job-first", "job-second"))
    run_ids = iter(("run-first", "run-second"))
    service, projects_root, _, _ = _make_service(
        tmp_path,
        project_id="2026_admission",
        pipeline_factory=factory,
        job_id_factory=lambda: next(job_ids),
        run_id_factory=lambda: next(run_ids),
    )
    project_id = "2026_admission"

    try:
        _create_confirmed_projects(service, [project_id])
        first = service.enqueue_render(project_id)  # type: ignore[attr-defined]
        assert factory.first_started.wait(timeout=5)

        with pytest.raises(StateConflictError, match="active job"):
            service.enqueue_render(project_id)  # type: ignore[attr-defined]

        assert _job_id(first) == "job-first"
        assert [
            path.name
            for path in (projects_root / project_id / "ui" / "working").glob("*.json")
        ] == ["job-first.json"]
        assert [job["job_id"] for job in service.list_jobs()] == ["job-first"]  # type: ignore[attr-defined]
    finally:
        factory.release.set()
        service.close()  # type: ignore[attr-defined]


def test_different_projects_still_queue_fifo_behind_an_active_render(
    tmp_path: Path,
) -> None:
    """The per-project guard does not change the global FIFO worker."""

    project_ids = ["2026_fifo_first", "2026_fifo_second"]
    factory = _BlockingPipelineFactory(expected_terminal=2)
    service, projects_root, _, _ = _make_service(
        tmp_path,
        project_ids=project_ids,
        pipeline_factory=factory,
    )

    try:
        _create_confirmed_projects(service, project_ids)
        first = service.enqueue_render(project_ids[0])  # type: ignore[attr-defined]
        assert factory.first_started.wait(timeout=5)
        second = service.enqueue_render(project_ids[1])  # type: ignore[attr-defined]
        assert second.state == "queued"  # type: ignore[attr-defined]

        # A browser double-click can arrive before the queued project's status
        # changes from timeline_confirmed.  Admission must still be per-project
        # and must reject before allocating another job/draft.
        with pytest.raises(StateConflictError, match="active job"):
            service.enqueue_render(project_ids[1])  # type: ignore[attr-defined]
        assert len(
            list((projects_root / project_ids[1] / "ui" / "working").glob("*.json"))
        ) == 1

        factory.release.set()
        assert factory.all_terminal.wait(timeout=5)
        assert factory.started_projects == project_ids
        assert service.wait_job(_job_id(first), timeout=5).state == "success"  # type: ignore[attr-defined]
        assert service.wait_job(_job_id(second), timeout=5).state == "success"  # type: ignore[attr-defined]
    finally:
        factory.release.set()
        service.close()  # type: ignore[attr-defined]


class _FailingProvider(FakeProvider):
    def generate(self, request: ProviderRequest) -> ProviderResponse:
        raise RuntimeError("controlled selective correction failure")


class _CanonicalPipelineWithOptionalFailure:
    def __init__(self, run_id: str, *, fail: bool) -> None:
        self.run_id = run_id
        self.fail = fail

    def render(
        self,
        project_path: str | Path,
        *,
        max_attempts: int = 3,
        scene: str | None = None,
        base_run_id: str | None = None,
        correction: str | None = None,
        on_progress: Callable[[ProjectPipelineEvent], None] | None = None,
    ) -> VideoResult:
        project_json = Path(project_path)
        normalized_validator = FakeNormalizedValidator()
        provider = (
            _FailingProvider(project_json)
            if self.fail
            else FakeProvider(project_json)
        )
        pipeline = VideoPipeline(
            provider=provider,
            runner=FakeManimRunner(),
            validator=FakeRawValidator(),
            observer=FakeObserver(),
            temporal_normalizer=FakeTemporalNormalizer(normalized_validator),
            normalized_validator=normalized_validator,
            final_validator=FakeFinalValidator(),
            composer=FakeComposer(),
            id_factory=lambda: self.run_id,
        )
        return pipeline.render(
            project_json,
            max_attempts=max_attempts,
            scene=scene,
            base_run_id=base_run_id,
            correction=correction,
            on_progress=on_progress,
        )


class _SelectiveFailureFactory:
    def __init__(self) -> None:
        self.ordinal = 0

    def __call__(self, run_id: str) -> _CanonicalPipelineWithOptionalFailure:
        self.ordinal += 1
        return _CanonicalPipelineWithOptionalFailure(run_id, fail=self.ordinal == 2)


def _canonical_service_with_ready_base(
    tmp_path: Path,
    *,
    pipeline_factory: object | None = None,
) -> tuple[WebService, Path, str]:
    audio_root = tmp_path / "audio"
    audio_root.mkdir(parents=True, exist_ok=True)
    audio_payload = b"admission narration"
    (audio_root / "narration.wav").write_bytes(audio_payload)
    project_id = "2026_ready_base"
    project_root = tmp_path / "projects" / project_id
    job_ids = iter(("job-base", "job-fail", "job-recover"))
    run_ids = iter(("run-base", "run-fail", "run-recover"))
    service = WebService(
        projects_root=tmp_path / "projects",
        audio_root=audio_root,
        audio_probe=_FakeAudioProbe(),
        silence_detector=_NoSilence(),
        pipeline_factory=pipeline_factory or _CanonicalPipelineFactory(),  # type: ignore[arg-type]
        project_id_factory=lambda: project_id,
        job_id_factory=lambda: next(job_ids),
        run_id_factory=lambda: next(run_ids),
    )
    service.create_project(
        title="Projeto de admissão",
        script=(
            "# Abertura\n"
            "@capabilities: basic_geometry\n"
            "Introdução.\n\n"
            "## Fecho\n"
            "@capabilities: basic_geometry\n"
            "Conclusão.\n"
        ),
        audio_asset_id="audio-narration",
    )
    service.confirm_timeline(project_id)
    base_job = service.enqueue_render(project_id)
    assert service.wait_job(base_job.job_id, timeout=10).state == "success"
    assert (project_root / "artifacts" / "run-base" / "run.json").is_file()
    return service, project_root, project_id


def test_ready_base_can_regenerate_after_a_real_selective_failure(
    tmp_path: Path,
) -> None:
    """A failed project can recover from an earlier ready base run."""

    service, project_root, project_id = _canonical_service_with_ready_base(
        tmp_path,
        pipeline_factory=_SelectiveFailureFactory(),
    )
    try:
        failed_job = service.enqueue_regeneration(
            project_id,
            base_run_id="run-base",
            scene_id="abertura",
            correction="Falha controlada na primeira correção.",
        )
        failed = service.wait_job(failed_job.job_id, timeout=10)
        assert failed.state == "failure"

        failed_project = json.loads(
            (project_root / "project.json").read_text(encoding="utf-8")
        )
        assert failed_project["status"] == "failed"
        assert failed_project["current_run"] == "run-fail"

        recovered_job = service.enqueue_regeneration(
            project_id,
            base_run_id="run-base",
            scene_id="abertura",
            correction="Correção diferente após a falha.",
        )
        terminal = service.wait_job(recovered_job.job_id, timeout=10)

        assert terminal.state == "success"
        assert terminal.run_id == "run-recover"
        assert (project_root / "artifacts" / "run-recover" / "run.json").is_file()
    finally:
        service.close()


def test_ready_base_can_regenerate_after_real_acceptance(
    tmp_path: Path,
) -> None:
    """Accepting a run does not make its ready media unusable for correction."""

    service, project_root, project_id = _canonical_service_with_ready_base(tmp_path)
    try:
        accepted = service.accept_run(project_id, "run-base")
        assert accepted["project"]["status"] == "accepted"  # type: ignore[index]
        golden_before = (project_root / "golden" / "manifest.json").read_bytes()

        job = service.enqueue_regeneration(
            project_id,
            base_run_id="run-base",
            scene_id="abertura",
            correction="Aumente o contraste após a aceitação.",
        )
        terminal = service.wait_job(job.job_id, timeout=10)

        assert terminal.state == "success"
        assert terminal.run_id == "run-fail"
        assert (project_root / "golden" / "manifest.json").read_bytes() == golden_before
    finally:
        service.close()
