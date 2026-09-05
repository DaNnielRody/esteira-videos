"""Browser contract for selecting a failed revision after a ready revision."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_web_e2e import (
    _asset_request,
    _browser,
    _BrowserPipelineFactory,
    _create_project_before_browser,
    _running_server,
    _service,
)

from video_pipeline.project import Project, _project_package_hashes
from video_pipeline.revisions import RevisionStore

pytestmark = pytest.mark.integration


def test_selecting_failed_revision_clears_media_and_review_actions(
    tmp_path: Path,
) -> None:
    project_id = "2026_revision_failure"
    factory = _BrowserPipelineFactory()
    service = _service(
        tmp_path,
        factory,
        iter((project_id,)),
        iter(("job-revision-success",)),
        iter(("run-revision-success",)),
    )
    _create_project_before_browser(service, project_id)
    factory.released.set()
    rendered = service.enqueue_render(project_id)
    assert service.wait_job(rendered.job_id, timeout=5).state == "success"

    project_root = tmp_path / "projects" / project_id
    project = Project.model_validate_json(
        (project_root / "project.json").read_text(encoding="utf-8")
    )
    store = RevisionStore(project_root)
    store.start_working(
        project_id=project_id,
        job_id="job-revision-failure",
        run_id="run-revision-failure",
        status="queued",
        base_package_hashes=_project_package_hashes(project_root, project),
        messages=["Falha de render deliberada"],
        asset_ids=["audio-narration"],
    )
    failed = store.publish_terminal(
        project_id=project_id,
        job_id="job-revision-failure",
        run_id="run-revision-failure",
        status="failure",
        base_package_hashes=_project_package_hashes(project_root, project),
        messages=["Falha de render deliberada"],
        asset_ids=["audio-narration"],
    )
    assert failed.revision_id == "v002"
    store.checkout("v001")

    try:
        with _browser() as driver:
            with _running_server(service) as (_, port):
                driver.get(f"http://127.0.0.1:{port}/")
                driver.wait_text("#connection-status", "Sessão local pronta")
                driver.wait_text("#project-id", project_id)
                driver.wait_text("#revision-count", "2")
                driver.wait_until(
                    lambda: driver.enabled("#regenerate-button")
                    and driver.enabled("#accept-button")
                )

                final_before = driver.attribute("#final-video", "src")
                scene_before = driver.attribute("#scene-video", "src")
                assert final_before is not None and "/api/assets/media-" in final_before
                assert scene_before is not None and "/api/assets/media-" in scene_before
                assert _asset_request(port, final_before) == (
                    200,
                    b"final:run-revision-success",
                )

                revisions = driver.find_all("#revision-list button")
                assert len(revisions) == 2
                revisions[1].click()
                driver.wait_text("#state-badge", "Falha")
                driver.wait_text("#diagnostic", "Falha de render deliberada")
                assert driver.text("#run-id") == "run-revision-failure"
                assert driver.attribute("#final-video", "src") in {None, ""}
                assert driver.attribute("#scene-video", "src") in {None, ""}
                assert not driver.enabled("#regenerate-button")
                assert not driver.enabled("#accept-button")
                assert all(
                    not button.enabled() for button in driver.find_all("#scene-list button")
                )

                driver.find_all("#revision-list button")[0].click()
                driver.wait_until(
                    lambda: driver.text("#state-badge") == "Revisão restaurada"
                    and driver.attribute("#final-video", "src") == final_before
                    and driver.attribute("#scene-video", "src") == scene_before
                )
                assert driver.enabled("#regenerate-button")
                assert driver.enabled("#accept-button")
                assert all(
                    button.enabled() for button in driver.find_all("#scene-list button")
                )
    finally:
        service.close()
