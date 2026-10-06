"""Browser contracts for stale navigation responses."""

from __future__ import annotations

from pathlib import Path
from threading import Event

import pytest
from test_web_e2e import (
    _browser,
    _BrowserPipelineFactory,
    _create_project_before_browser,
    _running_server,
    _service,
    _WebDriver,
)

from video_pipeline.project import Project, _project_package_hashes
from video_pipeline.revisions import RevisionManifest, RevisionStore
from video_pipeline.web.service import JobSnapshot

pytestmark = pytest.mark.integration


def _watch_async_operation(driver: _WebDriver, path_fragment: str) -> None:
    """Expose a browser-side network-idle barrier for one async UI operation."""

    driver.execute(
        """
        window.__navigationWatch = {started: false, pending: 0, idle: false};
        const watch = window.__navigationWatch;
        const operationFragment = arguments[0];
        const originalFetch = window.fetch.bind(window);
        window.fetch = (...args) => {
          const url = String(args[0]);
          if (!watch.started && url.includes(operationFragment)) watch.started = true;
          const tracked = watch.started && (
            url.includes("/api/projects/") || url.includes("/api/jobs")
          );
          if (!tracked) return originalFetch(...args);
          watch.idle = false;
          watch.pending += 1;
          return originalFetch(...args).then(response => {
            const originalJson = response.json.bind(response);
            let settled = false;
            const settle = () => {
              if (settled) return;
              settled = true;
              watch.pending -= 1;
              window.setTimeout(() => {
                if (watch.pending === 0) watch.idle = true;
              }, 0);
            };
            response.json = (...jsonArgs) => originalJson(...jsonArgs).then(
              payload => {
                settle();
                return payload;
              },
              error => {
                settle();
                throw error;
              },
            );
            return response;
          }, error => {
            watch.pending -= 1;
            throw error;
          });
        };
        """,
        path_fragment,
    )


def _wait_for_operation_idle(driver: _WebDriver) -> None:
    driver.wait_until(
        lambda: driver.execute(
            "return Boolean(window.__navigationWatch && window.__navigationWatch.idle);"
        )
        is True
    )


def test_delayed_checkout_cannot_overwrite_a_newer_project_selection(
    tmp_path: Path,
) -> None:
    alpha_id = "2026_navigation_alpha"
    beta_id = "2026_navigation_beta"
    factory = _BrowserPipelineFactory()
    service = _service(
        tmp_path,
        factory,
        iter((alpha_id, beta_id)),
        iter(("job-navigation-alpha", "job-navigation-beta")),
        iter(("run-navigation-alpha", "run-navigation-beta")),
    )
    _create_project_before_browser(service, alpha_id)
    _create_project_before_browser(service, beta_id)
    factory.released.set()
    for project_id in (alpha_id, beta_id):
        job = service.enqueue_render(project_id)
        assert service.wait_job(job.job_id, timeout=5).state == "success"

    alpha_root = tmp_path / "projects" / alpha_id
    alpha_project = Project.model_validate_json(
        (alpha_root / "project.json").read_text(encoding="utf-8")
    )
    store = RevisionStore(alpha_root)
    base_hashes = _project_package_hashes(alpha_root, alpha_project)
    store.start_working(
        project_id=alpha_id,
        job_id="job-navigation-alpha-second",
        run_id="run-navigation-alpha-second",
        status="queued",
        base_package_hashes=base_hashes,
        messages=["segunda revisão"],
        asset_ids=["audio-narration"],
    )
    second = store.publish_terminal(
        project_id=alpha_id,
        job_id="job-navigation-alpha-second",
        run_id="run-navigation-alpha-second",
        status="success",
        base_package_hashes=base_hashes,
        messages=["segunda revisão"],
        asset_ids=["audio-narration"],
    )
    assert second.revision_id == "v002"
    store.checkout("v001")

    checkout_started = Event()
    checkout_released = Event()
    checkout_finished = Event()
    original_checkout = service.checkout_revision

    def delayed_checkout(project_id: str, revision_id: str) -> RevisionManifest:
        checkout_started.set()
        if not checkout_released.wait(timeout=5):
            raise AssertionError("checkout release was not signaled")
        try:
            return original_checkout(project_id, revision_id)
        finally:
            checkout_finished.set()

    service.checkout_revision = delayed_checkout  # type: ignore[method-assign]

    try:
        with _browser() as driver:
            with _running_server(service) as (_, port):
                driver.get(f"http://127.0.0.1:{port}/")
                driver.wait_text("#connection-status", "Sessão local pronta")
                driver.select("#project-selector", alpha_id)
                driver.click("#open-project-button")
                driver.wait_text("#project-id", alpha_id)
                driver.wait_text("#revision-count", "2")

                _watch_async_operation(driver, "/checkout")
                driver.find_all("#revision-list button")[1].click()
                assert checkout_started.wait(timeout=5)

                driver.select("#project-selector", beta_id)
                driver.click("#open-project-button")
                driver.wait_text("#project-id", beta_id)
                driver.wait_text("#revision-count", "1")
                driver.wait_text("#run-id", "run-navigation-beta")
                driver.wait_text("#state-badge", "Conteúdo pronto")

                checkout_released.set()
                assert checkout_finished.wait(timeout=5)
                _wait_for_operation_idle(driver)
                assert driver.text("#project-id") == beta_id
                assert driver.text("#revision-count") == "1"
                assert driver.text("#run-id") == "run-navigation-beta"
                assert driver.text("#state-badge") == "Conteúdo pronto"
    finally:
        service.close()


def test_delayed_accept_cannot_update_a_newer_project_selection(
    tmp_path: Path,
) -> None:
    alpha_id = "2026_navigation_accept_alpha"
    beta_id = "2026_navigation_accept_beta"
    factory = _BrowserPipelineFactory()
    service = _service(
        tmp_path,
        factory,
        iter((alpha_id, beta_id)),
        iter(("job-navigation-accept-alpha", "job-navigation-accept-beta")),
        iter(("run-navigation-accept-alpha", "run-navigation-accept-beta")),
    )
    _create_project_before_browser(service, alpha_id)
    _create_project_before_browser(service, beta_id)
    factory.released.set()
    for project_id in (alpha_id, beta_id):
        job = service.enqueue_render(project_id)
        assert service.wait_job(job.job_id, timeout=5).state == "success"

    accept_started = Event()
    accept_released = Event()
    accept_finished = Event()

    def delayed_accept(project_id: str, run_id: str) -> dict[str, object]:
        del run_id
        accept_started.set()
        if not accept_released.wait(timeout=5):
            raise AssertionError("accept release was not signaled")
        accept_finished.set()
        return service.inspect(project_id)

    service.accept_run = delayed_accept  # type: ignore[method-assign]

    try:
        with _browser() as driver:
            with _running_server(service) as (_, port):
                driver.get(f"http://127.0.0.1:{port}/")
                driver.wait_text("#connection-status", "Sessão local pronta")
                driver.select("#project-selector", alpha_id)
                driver.click("#open-project-button")
                driver.wait_text("#project-id", alpha_id)
                driver.wait_text("#run-id", "run-navigation-accept-alpha")
                driver.wait_until(lambda: driver.enabled("#accept-button"))

                _watch_async_operation(driver, "/accept")
                driver.click("#accept-button")
                assert accept_started.wait(timeout=5)

                driver.select("#project-selector", beta_id)
                driver.click("#open-project-button")
                driver.wait_text("#project-id", beta_id)
                driver.wait_text("#run-id", "run-navigation-accept-beta")
                driver.wait_text("#state-badge", "Conteúdo pronto")

                accept_released.set()
                assert accept_finished.wait(timeout=5)
                _wait_for_operation_idle(driver)
                assert driver.text("#project-id") == beta_id
                assert driver.text("#run-id") == "run-navigation-accept-beta"
                assert driver.text("#state-badge") == "Conteúdo pronto"
                assert driver.text("#golden-status") == "Golden ainda não publicado"
    finally:
        service.close()


def test_stale_open_project_failure_cannot_replace_newer_selection(
    tmp_path: Path,
) -> None:
    alpha_id = "2026_navigation_open_alpha"
    beta_id = "2026_navigation_open_beta"
    factory = _BrowserPipelineFactory()
    service = _service(
        tmp_path,
        factory,
        iter((alpha_id, beta_id)),
        iter(("job-navigation-open-alpha", "job-navigation-open-beta")),
        iter(("run-navigation-open-alpha", "run-navigation-open-beta")),
    )
    _create_project_before_browser(service, alpha_id)
    _create_project_before_browser(service, beta_id)

    inspect_started = Event()
    inspect_released = Event()
    inspect_finished = Event()
    original_inspect = service.inspect

    def delayed_inspect(project_id: str) -> dict[str, object]:
        if project_id == alpha_id and not inspect_started.is_set():
            inspect_started.set()
            if not inspect_released.wait(timeout=5):
                raise AssertionError("inspection release was not signaled")
            inspect_finished.set()
            raise RuntimeError("stale alpha inspection")
        return original_inspect(project_id)

    service.inspect = delayed_inspect  # type: ignore[method-assign]

    try:
        with _browser() as driver:
            with _running_server(service) as (_, port):
                driver.get(f"http://127.0.0.1:{port}/")
                assert inspect_started.wait(timeout=5)

                driver.select("#project-selector", beta_id)
                driver.click("#open-project-button")
                driver.wait_text("#project-id", beta_id)
                driver.wait_text("#state-badge", "Projeto aberto")

                inspect_released.set()
                assert inspect_finished.wait(timeout=5)
                driver.wait_text("#connection-status", "Sessão local pronta")
                assert driver.text("#project-id") == beta_id
                assert driver.text("#state-badge") == "Projeto aberto"
    finally:
        service.close()


def test_delayed_confirmation_cannot_overwrite_a_newer_project_selection(
    tmp_path: Path,
) -> None:
    alpha_id = "2026_navigation_confirm_alpha"
    beta_id = "2026_navigation_confirm_beta"
    factory = _BrowserPipelineFactory()
    service = _service(
        tmp_path,
        factory,
        iter((alpha_id, beta_id)),
        iter(("job-navigation-confirm-alpha", "job-navigation-confirm-beta")),
        iter(("run-navigation-confirm-alpha", "run-navigation-confirm-beta")),
    )
    for project_id in (alpha_id, beta_id):
        created = service.create_project(
            title=f"Projeto {project_id}",
            script="# Abertura\nIntrodução.\n\n## Fecho\nConclusão.\n",
            audio_asset_id="audio-narration",
        )
        assert created["project"]["id"] == project_id
    confirm_started = Event()
    confirm_released = Event()
    confirm_finished = Event()
    original_confirm = service.confirm_timeline

    def delayed_confirm(project_id: str) -> dict[str, object]:
        confirm_started.set()
        if not confirm_released.wait(timeout=5):
            raise AssertionError("confirmation release was not signaled")
        try:
            return original_confirm(project_id)
        finally:
            confirm_finished.set()

    service.confirm_timeline = delayed_confirm  # type: ignore[method-assign]

    try:
        with _browser() as driver:
            with _running_server(service) as (_, port):
                driver.get(f"http://127.0.0.1:{port}/")
                driver.wait_text("#connection-status", "Sessão local pronta")
                driver.select("#project-selector", alpha_id)
                driver.click("#open-project-button")
                driver.wait_text("#project-id", alpha_id)
                driver.wait_until(lambda: driver.enabled("#confirm-button"))

                _watch_async_operation(driver, "/timeline/confirm")
                driver.click("#confirm-button")
                assert confirm_started.wait(timeout=5)

                driver.select("#project-selector", beta_id)
                driver.click("#open-project-button")
                driver.wait_text("#project-id", beta_id)
                driver.wait_text("#timeline-status", "candidate")
                driver.wait_text("#state-badge", "Projeto aberto")

                confirm_released.set()
                assert confirm_finished.wait(timeout=5)
                _wait_for_operation_idle(driver)
                assert driver.text("#project-id") == beta_id
                assert driver.text("#timeline-status") == "Timeline candidate"
                assert driver.text("#state-badge") == "Projeto aberto"
    finally:
        service.close()


def test_delayed_render_cannot_start_polling_for_a_newer_project_selection(
    tmp_path: Path,
) -> None:
    alpha_id = "2026_navigation_render_alpha"
    beta_id = "2026_navigation_render_beta"
    factory = _BrowserPipelineFactory()
    service = _service(
        tmp_path,
        factory,
        iter((alpha_id, beta_id)),
        iter(("job-navigation-render-alpha",)),
        iter(("run-navigation-render-alpha",)),
    )
    _create_project_before_browser(service, alpha_id)
    _create_project_before_browser(service, beta_id)
    factory.released.set()
    render_started = Event()
    render_released = Event()
    render_finished = Event()
    original_enqueue = service.enqueue_render

    def delayed_enqueue(
        project_id: str,
        *,
        max_attempts: int = 3,
        retry_of: str | None = None,
    ) -> object:
        render_started.set()
        if not render_released.wait(timeout=5):
            raise AssertionError("render release was not signaled")
        try:
            return original_enqueue(
                project_id,
                max_attempts=max_attempts,
                retry_of=retry_of,
            )
        finally:
            render_finished.set()

    service.enqueue_render = delayed_enqueue  # type: ignore[method-assign]

    try:
        with _browser() as driver:
            with _running_server(service) as (_, port):
                driver.get(f"http://127.0.0.1:{port}/")
                driver.wait_text("#connection-status", "Sessão local pronta")
                driver.select("#project-selector", alpha_id)
                driver.click("#open-project-button")
                driver.wait_text("#project-id", alpha_id)
                driver.wait_until(lambda: driver.enabled("#render-button"))

                _watch_async_operation(driver, "/render")
                driver.click("#render-button")
                assert render_started.wait(timeout=5)

                driver.select("#project-selector", beta_id)
                driver.click("#open-project-button")
                driver.wait_text("#project-id", beta_id)
                driver.wait_text("#state-badge", "Projeto aberto")

                render_released.set()
                assert render_finished.wait(timeout=5)
                _wait_for_operation_idle(driver)
                assert driver.text("#project-id") == beta_id
                assert driver.text("#run-id") == "—"
                assert driver.text("#state-badge") == "Projeto aberto"
    finally:
        service.close()


def test_delayed_regeneration_cannot_start_polling_for_a_newer_project_selection(
    tmp_path: Path,
) -> None:
    alpha_id = "2026_navigation_regenerate_alpha"
    beta_id = "2026_navigation_regenerate_beta"
    factory = _BrowserPipelineFactory()
    service = _service(
        tmp_path,
        factory,
        iter((alpha_id, beta_id)),
        iter(
            (
                "job-navigation-regenerate-alpha",
                "job-navigation-regenerate-beta",
                "job-navigation-regenerate-selective",
            )
        ),
        iter(
            (
                "run-navigation-regenerate-alpha",
                "run-navigation-regenerate-beta",
                "run-navigation-regenerate-selective",
            )
        ),
    )
    _create_project_before_browser(service, alpha_id)
    _create_project_before_browser(service, beta_id)
    factory.released.set()
    for project_id in (alpha_id, beta_id):
        job = service.enqueue_render(project_id)
        assert service.wait_job(job.job_id, timeout=5).state == "success"

    regeneration_started = Event()
    regeneration_released = Event()
    regeneration_finished = Event()
    original_enqueue = service.enqueue_regeneration

    def delayed_regeneration(
        project_id: str,
        *,
        base_run_id: str,
        scene_id: str,
        correction: str,
        retry_of: str | None = None,
    ) -> object:
        regeneration_started.set()
        if not regeneration_released.wait(timeout=5):
            raise AssertionError("regeneration release was not signaled")
        try:
            return original_enqueue(
                project_id,
                base_run_id=base_run_id,
                scene_id=scene_id,
                correction=correction,
                retry_of=retry_of,
            )
        finally:
            regeneration_finished.set()

    service.enqueue_regeneration = delayed_regeneration  # type: ignore[method-assign]

    try:
        with _browser() as driver:
            with _running_server(service) as (_, port):
                driver.get(f"http://127.0.0.1:{port}/")
                driver.wait_text("#connection-status", "Sessão local pronta")
                driver.select("#project-selector", alpha_id)
                driver.click("#open-project-button")
                driver.wait_text("#project-id", alpha_id)
                driver.wait_text("#run-id", "run-navigation-regenerate-alpha")
                driver.wait_until(lambda: driver.enabled("#regenerate-button"))
                driver.fill("#scene-correction", "Ajustar a abertura")

                _watch_async_operation(driver, "/regenerate")
                driver.click("#regenerate-button")
                assert regeneration_started.wait(timeout=5)

                driver.select("#project-selector", beta_id)
                driver.click("#open-project-button")
                driver.wait_text("#project-id", beta_id)
                driver.wait_text("#run-id", "run-navigation-regenerate-beta")
                driver.wait_text("#state-badge", "Conteúdo pronto")

                regeneration_released.set()
                assert regeneration_finished.wait(timeout=5)
                _wait_for_operation_idle(driver)
                assert driver.text("#project-id") == beta_id
                assert driver.text("#run-id") == "run-navigation-regenerate-beta"
                assert driver.text("#state-badge") == "Conteúdo pronto"
    finally:
        service.close()


def test_delayed_retry_cannot_start_polling_for_a_newer_project_selection(
    tmp_path: Path,
) -> None:
    alpha_id = "2026_navigation_retry_alpha"
    beta_id = "2026_navigation_retry_beta"
    failed_job_id = "job-navigation-retry-failed"
    failed_run_id = "run-navigation-retry-failed"
    factory = _BrowserPipelineFactory()
    service = _service(
        tmp_path,
        factory,
        iter((alpha_id, beta_id)),
        iter(("job-navigation-retry-new",)),
        iter(("run-navigation-retry-new",)),
    )
    _create_project_before_browser(service, alpha_id)
    _create_project_before_browser(service, beta_id)
    alpha_root = tmp_path / "projects" / alpha_id
    alpha_project = Project.model_validate_json(
        (alpha_root / "project.json").read_text(encoding="utf-8")
    )
    store = RevisionStore(alpha_root)
    base_hashes = _project_package_hashes(alpha_root, alpha_project)
    store.start_working(
        project_id=alpha_id,
        job_id=failed_job_id,
        run_id=failed_run_id,
        status="queued",
        base_package_hashes=base_hashes,
        messages=["Falha recuperável"],
        asset_ids=["audio-narration"],
    )
    failed = store.publish_terminal(
        project_id=alpha_id,
        job_id=failed_job_id,
        run_id=failed_run_id,
        status="failure",
        base_package_hashes=base_hashes,
        messages=["Falha recuperável"],
        asset_ids=["audio-narration"],
    )
    assert failed.status == "failure"
    factory.released.set()
    retry_started = Event()
    retry_released = Event()
    retry_finished = Event()
    original_retry = service.retry_job

    def delayed_retry(job_id: str) -> object:
        retry_started.set()
        if not retry_released.wait(timeout=5):
            raise AssertionError("retry release was not signaled")
        try:
            return original_retry(job_id)
        finally:
            retry_finished.set()

    service.retry_job = delayed_retry  # type: ignore[method-assign]

    try:
        with _browser() as driver:
            with _running_server(service) as (_, port):
                driver.get(f"http://127.0.0.1:{port}/")
                driver.wait_text("#connection-status", "Sessão local pronta")
                driver.select("#project-selector", alpha_id)
                driver.click("#open-project-button")
                driver.wait_text("#project-id", alpha_id)
                driver.wait_text("#job-list", failed_job_id)

                _watch_async_operation(driver, "/retry")
                driver.click("#job-list .job-retry")
                assert retry_started.wait(timeout=5)

                driver.select("#project-selector", beta_id)
                driver.click("#open-project-button")
                driver.wait_text("#project-id", beta_id)
                driver.wait_text("#timeline-status", "confirmed")
                driver.wait_text("#state-badge", "Projeto aberto")

                retry_released.set()
                assert retry_finished.wait(timeout=5)
                _wait_for_operation_idle(driver)
                assert driver.text("#project-id") == beta_id
                assert driver.text("#state-badge") == "Projeto aberto"
    finally:
        service.close()


def test_delayed_create_cannot_replace_a_newer_project_selection(
    tmp_path: Path,
) -> None:
    alpha_id = "2026_navigation_create_alpha"
    beta_id = "2026_navigation_create_beta"
    created_id = "2026_navigation_create_new"
    factory = _BrowserPipelineFactory()
    service = _service(
        tmp_path,
        factory,
        iter((alpha_id, beta_id, created_id)),
        iter(()),
        iter(()),
    )
    _create_project_before_browser(service, alpha_id)
    _create_project_before_browser(service, beta_id)
    create_started = Event()
    create_released = Event()
    create_finished = Event()
    original_create = service.create_project

    def delayed_create(
        *,
        title: str,
        script: str,
        audio_asset_id: str,
    ) -> dict[str, object]:
        create_started.set()
        if not create_released.wait(timeout=5):
            raise AssertionError("create release was not signaled")
        try:
            return original_create(
                title=title,
                script=script,
                audio_asset_id=audio_asset_id,
            )
        finally:
            create_finished.set()

    service.create_project = delayed_create  # type: ignore[method-assign]

    try:
        with _browser() as driver:
            with _running_server(service) as (_, port):
                driver.get(f"http://127.0.0.1:{port}/")
                driver.wait_text("#connection-status", "Sessão local pronta")
                driver.fill("#project-title", "Novo projeto atrasado")
                driver.fill(
                    "#project-script",
                    "# Abertura\nIntrodução.\n\n## Fecho\nConclusão.\n",
                )
                driver.select("#project-audio", "audio-narration")

                _watch_async_operation(driver, "/api/projects")
                driver.click("#create-form button[type='submit']")
                assert create_started.wait(timeout=5)

                driver.select("#project-selector", beta_id)
                driver.click("#open-project-button")
                driver.wait_text("#project-id", beta_id)
                driver.wait_text("#timeline-status", "confirmed")
                driver.wait_text("#state-badge", "Projeto aberto")

                create_released.set()
                assert create_finished.wait(timeout=5)
                _wait_for_operation_idle(driver)
                assert driver.text("#project-id") == beta_id
                assert driver.text("#state-badge") == "Projeto aberto"
    finally:
        service.close()


def test_created_project_remains_selectable_when_catalog_refresh_is_invalidated(
    tmp_path: Path,
) -> None:
    existing_id = "2026_navigation_catalog_existing"
    created_id = "2026_navigation_catalog_created"
    factory = _BrowserPipelineFactory()
    service = _service(
        tmp_path,
        factory,
        iter((existing_id, created_id)),
        iter(()),
        iter(()),
    )
    _create_project_before_browser(service, existing_id)

    catalog_started = Event()
    catalog_released = Event()
    catalog_finished = Event()
    armed = Event()
    original_list_projects = service.list_projects

    def delayed_list_projects() -> list[dict[str, object]]:
        if armed.is_set() and not catalog_started.is_set():
            catalog_started.set()
            if not catalog_released.wait(timeout=5):
                raise AssertionError("catalog release was not signaled")
        try:
            return original_list_projects()
        finally:
            catalog_finished.set()

    service.list_projects = delayed_list_projects  # type: ignore[method-assign]

    try:
        with _browser() as driver:
            with _running_server(service) as (_, port):
                driver.get(f"http://127.0.0.1:{port}/")
                driver.wait_text("#connection-status", "Sessão local pronta")
                armed.set()
                driver.fill("#project-title", "Projeto criado com catálogo atrasado")
                driver.fill(
                    "#project-script",
                    "# Abertura\nIntrodução.\n\n## Fecho\nConclusão.\n",
                )
                driver.select("#project-audio", "audio-narration")
                driver.click("#create-form button[type='submit']")
                driver.wait_text("#project-id", created_id)
                assert catalog_started.wait(timeout=5)

                driver.wait_until(lambda: driver.enabled("#confirm-button"))
                driver.click("#confirm-button")
                driver.wait_text("#timeline-status", "confirmed")
                driver.select("#project-selector", existing_id)
                driver.click("#open-project-button")
                driver.wait_text("#project-id", existing_id)

                catalog_released.set()
                assert catalog_finished.wait(timeout=5)
                driver.wait_until(
                    lambda: driver.execute(
                        "return [...document.querySelectorAll('#project-selector option')]"
                        ".some(option => option.value === arguments[0]);",
                        created_id,
                    )
                    is True
                )
                driver.select("#project-selector", created_id)
                driver.click("#open-project-button")
                driver.wait_text("#project-id", created_id)
    finally:
        catalog_released.set()
        service.close()


def test_stale_poll_refresh_jobs_cannot_update_a_newer_project_selection(
    tmp_path: Path,
) -> None:
    alpha_id = "2026_navigation_poll_alpha"
    beta_id = "2026_navigation_poll_beta"
    factory = _BrowserPipelineFactory()
    service = _service(
        tmp_path,
        factory,
        iter((alpha_id, beta_id)),
        iter(("job-navigation-poll-alpha",)),
        iter(("run-navigation-poll-alpha",)),
    )
    _create_project_before_browser(service, alpha_id)
    _create_project_before_browser(service, beta_id)
    factory.released.set()

    jobs_started = Event()
    jobs_released = Event()
    begin_refresh_done = Event()
    armed = Event()
    original_list_jobs = service.list_jobs

    def delayed_list_jobs() -> list[dict[str, object]]:
        if armed.is_set():
            if not begin_refresh_done.is_set():
                begin_refresh_done.set()
            elif not jobs_started.is_set():
                jobs_started.set()
                if not jobs_released.wait(timeout=5):
                    raise AssertionError("poll refresh release was not signaled")
        return original_list_jobs()

    service.list_jobs = delayed_list_jobs  # type: ignore[method-assign]

    try:
        with _browser() as driver:
            with _running_server(service) as (_, port):
                driver.get(f"http://127.0.0.1:{port}/")
                driver.wait_text("#connection-status", "Sessão local pronta")
                driver.select("#project-selector", alpha_id)
                driver.click("#open-project-button")
                driver.wait_text("#project-id", alpha_id)
                driver.wait_until(lambda: driver.enabled("#render-button"))

                _watch_async_operation(driver, "/render")
                armed.set()
                driver.click("#render-button")
                assert begin_refresh_done.wait(timeout=5)
                assert jobs_started.wait(timeout=5)

                driver.select("#project-selector", beta_id)
                driver.click("#open-project-button")
                driver.wait_text("#project-id", beta_id)
                driver.wait_text("#timeline-status", "confirmed")
                driver.wait_text("#state-badge", "Projeto aberto")

                jobs_released.set()
                _wait_for_operation_idle(driver)
                assert driver.text("#project-id") == beta_id
                assert driver.text("#state-badge") == "Projeto aberto"
    finally:
        service.close()


def test_browser_reload_resumes_polling_for_an_existing_render(
    tmp_path: Path,
) -> None:
    project_id = "2026_navigation_reload"
    factory = _BrowserPipelineFactory()
    service = _service(
        tmp_path,
        factory,
        iter((project_id,)),
        iter(("job-navigation-reload",)),
        iter(("run-navigation-reload",)),
    )
    _create_project_before_browser(service, project_id)

    try:
        with _browser() as driver:
            with _running_server(service) as (_, port):
                driver.get(f"http://127.0.0.1:{port}/")
                driver.wait_text("#connection-status", "Sessão local pronta")
                driver.wait_text("#project-id", project_id)
                driver.wait_until(lambda: driver.enabled("#render-button"))
                driver.click("#render-button")
                assert factory.started.wait(timeout=5)

                driver.refresh()
                driver.wait_text("#connection-status", "Sessão local pronta")
                driver.wait_text("#project-id", project_id)
                assert driver.text("#revision-count") == "0"

                factory.released.set()
                assert factory.completed.wait(timeout=5)
                driver.wait_text("#revision-count", "1", timeout=5)
                driver.wait_text("#state-badge", "Concluído", timeout=5)
    finally:
        factory.released.set()
        service.close()


def test_navigation_pending_disables_actions_from_the_previous_project(
    tmp_path: Path,
) -> None:
    alpha_id = "2026_navigation_pending_alpha"
    beta_id = "2026_navigation_pending_beta"
    factory = _BrowserPipelineFactory()
    service = _service(
        tmp_path,
        factory,
        iter((alpha_id, beta_id)),
        iter(()),
        iter(()),
    )
    for project_id in (alpha_id, beta_id):
        created = service.create_project(
            title=f"Projeto {project_id}",
            script="# Abertura\nIntrodução.\n\n## Fecho\nConclusão.\n",
            audio_asset_id="audio-narration",
        )
        assert created["project"]["id"] == project_id

    inspect_started = Event()
    inspect_released = Event()
    inspect_finished = Event()
    original_inspect = service.inspect

    def delayed_inspect(project_id: str) -> dict[str, object]:
        delayed = project_id == beta_id and not inspect_started.is_set()
        if delayed:
            inspect_started.set()
            if not inspect_released.wait(timeout=5):
                raise AssertionError("pending navigation release was not signaled")
        try:
            return original_inspect(project_id)
        finally:
            if delayed:
                inspect_finished.set()

    confirm_called = Event()
    original_confirm = service.confirm_timeline

    def tracking_confirm(project_id: str) -> dict[str, object]:
        confirm_called.set()
        return original_confirm(project_id)

    service.inspect = delayed_inspect  # type: ignore[method-assign]
    service.confirm_timeline = tracking_confirm  # type: ignore[method-assign]

    try:
        with _browser() as driver:
            with _running_server(service) as (_, port):
                driver.get(f"http://127.0.0.1:{port}/")
                driver.wait_text("#connection-status", "Sessão local pronta")
                driver.select("#project-selector", alpha_id)
                driver.click("#open-project-button")
                driver.wait_text("#project-id", alpha_id)
                driver.wait_until(lambda: driver.enabled("#confirm-button"))

                driver.select("#project-selector", beta_id)
                driver.click("#open-project-button")
                assert inspect_started.wait(timeout=5)
                assert not driver.enabled("#confirm-button")
                driver.click("#confirm-button")
                assert not confirm_called.is_set()
                inspect_released.set()
                assert inspect_finished.wait(timeout=5)
    finally:
        inspect_released.set()
        service.close()


def test_bootstrap_does_not_reopen_first_project_after_user_create(
    tmp_path: Path,
) -> None:
    existing_id = "2026_navigation_bootstrap_existing"
    created_id = "2026_navigation_bootstrap_created"
    factory = _BrowserPipelineFactory()
    service = _service(
        tmp_path,
        factory,
        iter((existing_id, created_id)),
        iter(()),
        iter(()),
    )
    _create_project_before_browser(service, existing_id)

    jobs_started = Event()
    jobs_released = Event()
    jobs_finished = Event()
    original_list_jobs = service.list_jobs

    def delayed_list_jobs() -> list[dict[str, object]]:
        jobs_started.set()
        if not jobs_released.wait(timeout=5):
            raise AssertionError("bootstrap jobs release was not signaled")
        try:
            return original_list_jobs()
        finally:
            jobs_finished.set()

    service.list_jobs = delayed_list_jobs  # type: ignore[method-assign]

    try:
        with _browser() as driver:
            with _running_server(service) as (_, port):
                driver.get(f"http://127.0.0.1:{port}/")
                driver.wait_until(
                    lambda: driver.execute(
                        "return document.querySelector('#project-selector').options.length === 1;"
                    )
                    is True
                )
                assert jobs_started.wait(timeout=5)

                driver.fill("#project-title", "Projeto criado durante bootstrap")
                driver.fill(
                    "#project-script",
                    "# Abertura\nIntrodução.\n\n## Fecho\nConclusão.\n",
                )
                driver.select("#project-audio", "audio-narration")
                driver.click("#create-form button[type='submit']")
                driver.wait_text("#project-id", created_id)

                jobs_released.set()
                assert jobs_finished.wait(timeout=5)
                driver.wait_text("#connection-status", "Sessão local pronta")
                assert driver.text("#project-id") == created_id
    finally:
        jobs_released.set()
        service.close()


def test_failed_revision_selection_invalidates_pending_polling(
    tmp_path: Path,
) -> None:
    project_id = "2026_navigation_failure_selection"
    factory = _BrowserPipelineFactory()
    job_id = "job-navigation-failure-selection"
    service = _service(
        tmp_path,
        factory,
        iter((project_id,)),
        iter((job_id,)),
        iter(("run-navigation-failure-selection",)),
    )
    _create_project_before_browser(service, project_id)
    project_root = tmp_path / "projects" / project_id
    project = Project.model_validate_json(
        (project_root / "project.json").read_text(encoding="utf-8")
    )
    store = RevisionStore(project_root)
    base_hashes = _project_package_hashes(project_root, project)
    store.start_working(
        project_id=project_id,
        job_id="job-navigation-failure-history",
        run_id="run-navigation-failure-history",
        status="queued",
        base_package_hashes=base_hashes,
        messages=["Falha histórica"],
        asset_ids=["audio-narration"],
    )
    failed = store.publish_terminal(
        project_id=project_id,
        job_id="job-navigation-failure-history",
        run_id="run-navigation-failure-history",
        status="failure",
        base_package_hashes=base_hashes,
        messages=["Falha histórica"],
        asset_ids=["audio-narration"],
    )
    assert failed.revision_id == "v001"

    running_started = Event()
    running_released = Event()
    running_finished = Event()
    original_get_job = service.get_job

    def delayed_get_job(requested_job_id: str) -> JobSnapshot:
        snapshot = original_get_job(requested_job_id)
        delayed = (
            requested_job_id == job_id
            and snapshot.state in {"queued", "running"}
            and not running_started.is_set()
        )
        if delayed:
            running_started.set()
            if not running_released.wait(timeout=5):
                raise AssertionError("running poll release was not signaled")
        if delayed:
            running_finished.set()
        return snapshot

    service.get_job = delayed_get_job  # type: ignore[method-assign]

    try:
        with _browser() as driver:
            with _running_server(service) as (_, port):
                driver.get(f"http://127.0.0.1:{port}/")
                driver.wait_text("#connection-status", "Sessão local pronta")
                driver.wait_text("#project-id", project_id)
                driver.wait_text("#revision-count", "1")
                driver.wait_until(lambda: driver.enabled("#render-button"))

                _watch_async_operation(driver, "/render")
                driver.click("#render-button")
                assert factory.started.wait(timeout=5)
                assert running_started.wait(timeout=5)
                driver.find_all("#revision-list button")[0].click()
                driver.wait_text("#state-badge", "Falha")

                running_released.set()
                assert running_finished.wait(timeout=5)
                _wait_for_operation_idle(driver)
                assert driver.text("#state-badge") == "Falha"
                assert driver.text("#run-id") == "run-navigation-failure-history"
                factory.released.set()
                assert factory.completed.wait(timeout=5)
    finally:
        factory.released.set()
        running_released.set()
        service.close()
