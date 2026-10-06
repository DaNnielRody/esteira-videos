"""Browser evidence for the local Web UI's complete recovery loop.

The test talks to geckodriver's W3C WebDriver endpoint with the Python
standard library.  The WebService and HTTP server are real; only the pipeline
boundary is deterministic so this test never invokes Ollama, Manim, FFmpeg,
or ffprobe.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.client import HTTPConnection
from pathlib import Path
from threading import Event, Thread
from typing import Protocol
from urllib.parse import urlsplit

import pytest

from video_pipeline.pipeline import PipelineStage, PipelineState
from video_pipeline.project import Project, _project_package_hashes
from video_pipeline.revisions import RevisionStore
from video_pipeline.video import ProjectPipelineEvent, VideoResult
from video_pipeline.web.server import create_server
from video_pipeline.web.service import WebService

_FIREFOX = Path("/usr/bin/firefox")
_FIREFOX_SNAP_BINARY = Path("/snap/firefox/current/usr/lib/firefox/firefox")
_GECKODRIVER = Path("/snap/bin/geckodriver")
_ELEMENT_KEY = "element-6066-11e4-a52e-4f735466cecf"

pytestmark = pytest.mark.integration


class _Server(Protocol):
    server_address: tuple[str, int]

    def serve_forever(self) -> None: ...

    def shutdown(self) -> None: ...

    def server_close(self) -> None: ...


class _BrowserAudioProbe:
    """Return canonical audio facts without crossing a media-tool boundary."""

    def __call__(self, path: Path) -> dict[str, object]:
        payload = path.read_bytes()
        return {
            "path": "audio/narration.wav",
            "hash": hashlib.sha256(payload).hexdigest(),
            "container": "wav",
            "codec": "pcm_s16le",
            "stream": 0,
            "sample_rate": 48_000,
            "channels": 1,
            "duration": 10.0,
            "size": len(payload),
            "probe_result": {"format": {"format_name": "wav", "duration": "10.0"}},
        }


class _BrowserSilenceDetector:
    def __call__(self, path: Path) -> tuple[object, ...]:
        del path
        return ()


class _BrowserPipelineFactory:
    """Fake only the VideoPipeline boundary while writing real project media."""

    def __init__(self) -> None:
        self.started = Event()
        self.released = Event()
        self.completed = Event()
        self.calls: list[dict[str, object]] = []
        self._ordinal = 0

    def __call__(self, run_id: str) -> _BrowserPipeline:
        self._ordinal += 1
        return _BrowserPipeline(self, run_id, self._ordinal)


class _BrowserPipeline:
    def __init__(
        self,
        owner: _BrowserPipelineFactory,
        run_id: str,
        ordinal: int,
    ) -> None:
        self.owner = owner
        self.run_id = run_id
        self.ordinal = ordinal

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
        project_file = Path(project_path)
        project = Project.model_validate_json(project_file.read_text(encoding="utf-8"))
        self.owner.calls.append(
            {
                "run_id": self.run_id,
                "max_attempts": max_attempts,
                "scene": scene,
                "base_run_id": base_run_id,
                "correction": correction,
            }
        )
        project_document = json.loads(project_file.read_text(encoding="utf-8"))
        project_document.update(
            {
                "status": "ready",
                "current_run": self.run_id,
                "current_scene": None,
                "render_state": "ready",
                "composition_state": "ready",
            }
        )
        project_file.write_text(json.dumps(project_document), encoding="utf-8")
        run_path = project_file.parent / "artifacts" / self.run_id
        run_path.mkdir(parents=True, exist_ok=True)
        scene_records: list[dict[str, object]] = []
        for scene_ref in project.scenes:
            scene_root = run_path / scene_ref.path
            scene_root.mkdir(parents=True, exist_ok=True)
            (scene_root / "normalized.mp4").write_bytes(
                f"{self.run_id}:{scene_ref.id}".encode("utf-8")
            )
            scene_records.append(
                {
                    "id": scene_ref.id,
                    "state": "ready",
                    "attempts": 1,
                    "action_next": "include normalized scene in composition",
                    "error": None,
                }
            )
        final_path = run_path / "final.mp4"
        final_path.write_bytes(f"final:{self.run_id}".encode("utf-8"))
        (run_path / "run.json").write_text(
            json.dumps(
                {
                    "schema_version": "project.render-run/1",
                    "run_id": self.run_id,
                    "project_id": project.id,
                    "state": "ready",
                    "state_history": ["rendering", "ready"],
                    "current_scene": None,
                    "scenes": scene_records,
                    "composition": {"output_path": str(final_path)},
                    "final_validation": {"valid": True, "reasons": []},
                    "output_path": str(final_path),
                    "action_next": "accept this run explicitly",
                    "error": None,
                }
            ),
            encoding="utf-8",
        )
        if on_progress is not None:
            first_scene = project.scenes[0]
            on_progress(
                ProjectPipelineEvent(
                    run_id=f"{self.run_id}-attempt",
                    attempt=1,
                    stage=PipelineStage.GENERATING,
                    state=PipelineState.ATTEMPTING,
                    observation="not_applicable",
                    project_run_id=self.run_id,
                    scene_id=first_scene.id,
                )
            )
        if self.ordinal == 1:
            self.owner.started.set()
            if not self.owner.released.wait(timeout=10):
                raise AssertionError("browser E2E release was not signaled")
        if on_progress is not None:
            on_progress(
                ProjectPipelineEvent(
                    run_id=f"{self.run_id}-attempt",
                    attempt=1,
                    stage=PipelineStage.TERMINAL,
                    state=PipelineState.SUCCESS,
                    observation="not_applicable",
                    project_run_id=self.run_id,
                    scene_id=project.scenes[0].id,
                )
            )
        self.owner.completed.set()
        return VideoResult(state="ready", run_path=run_path, output_path=final_path)


class _WebElement:
    def __init__(self, driver: _WebDriver, element_id: str) -> None:
        self.driver = driver
        self.element_id = element_id

    def click(self) -> None:
        self.driver._command(
            "POST",
            f"/session/{self.driver.session_id}/element/{self.element_id}/click",
            {},
        )

    def clear(self) -> None:
        self.driver._command(
            "POST",
            f"/session/{self.driver.session_id}/element/{self.element_id}/clear",
            {},
        )

    def send_keys(self, text: str) -> None:
        self.driver._command(
            "POST",
            f"/session/{self.driver.session_id}/element/{self.element_id}/value",
            {"text": text, "value": list(text)},
        )

    def text(self) -> str:
        value = self.driver._command(
            "GET",
            f"/session/{self.driver.session_id}/element/{self.element_id}/text",
        )
        if not isinstance(value, str):
            raise AssertionError("WebDriver returned non-text element content")
        return value

    def attribute(self, name: str) -> str | None:
        value = self.driver._command(
            "GET",
            f"/session/{self.driver.session_id}/element/{self.element_id}/attribute/{name}",
        )
        if value is not None and not isinstance(value, str):
            raise AssertionError("WebDriver returned a non-text attribute")
        return value

    def enabled(self) -> bool:
        value = self.driver._command(
            "GET",
            f"/session/{self.driver.session_id}/element/{self.element_id}/enabled",
        )
        if not isinstance(value, bool):
            raise AssertionError("WebDriver returned a non-boolean enabled state")
        return value


class _WebDriver:
    def __init__(self, process: subprocess.Popen[bytes], port: int, session_id: str) -> None:
        self.process = process
        self.port = port
        self.session_id = session_id

    @classmethod
    def launch(cls) -> _WebDriver:
        port = _free_port()
        process = subprocess.Popen(
            [str(_GECKODRIVER), "--port", str(port), "--log", "fatal"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        try:
            _wait_for_driver(port, process)
            firefox_options: dict[str, object] = {
                "args": ["-headless", "--no-remote"],
            }
            firefox_binary = _firefox_binary()
            if firefox_binary is not None:
                firefox_options["binary"] = str(firefox_binary)
            value = cls._raw_command(
                port,
                "POST",
                "/session",
                {
                    "capabilities": {
                        "alwaysMatch": {
                            "browserName": "firefox",
                            "pageLoadStrategy": "eager",
                            "moz:firefoxOptions": firefox_options,
                        }
                    }
                },
            )
            document = _object(value)
            session_id = document.get("sessionId")
            if not isinstance(session_id, str) or not session_id:
                raise AssertionError("geckodriver did not return a W3C session id")
            return cls(process, port, session_id)
        except BaseException:
            _stop_process(process)
            raise

    def close(self) -> None:
        try:
            self._command("DELETE", f"/session/{self.session_id}")
        except (OSError, AssertionError):
            pass
        _stop_process(self.process)

    def get(self, url: str) -> None:
        self._command("POST", f"/session/{self.session_id}/url", {"url": url})

    def refresh(self) -> None:
        self._command("POST", f"/session/{self.session_id}/refresh", {})

    def execute(self, script: str, *args: object) -> object:
        return self._command(
            "POST",
            f"/session/{self.session_id}/execute/sync",
            {"script": script, "args": list(args)},
        )

    def find(self, selector: str) -> _WebElement:
        value = _object(
            self._command(
                "POST",
                f"/session/{self.session_id}/element",
                {"using": "css selector", "value": selector},
            )
        )
        element_id = value.get(_ELEMENT_KEY)
        if not isinstance(element_id, str):
            raise AssertionError("geckodriver returned an invalid element id")
        return _WebElement(self, element_id)

    def find_all(self, selector: str) -> list[_WebElement]:
        value = self._command(
            "POST",
            f"/session/{self.session_id}/elements",
            {"using": "css selector", "value": selector},
        )
        if not isinstance(value, list):
            raise AssertionError("geckodriver returned invalid elements")
        elements: list[_WebElement] = []
        for item in value:
            document = _object(item)
            element_id = document.get(_ELEMENT_KEY)
            if not isinstance(element_id, str):
                raise AssertionError("geckodriver returned an invalid element id")
            elements.append(_WebElement(self, element_id))
        return elements

    def click(self, selector: str) -> None:
        self.find(selector).click()

    def fill(self, selector: str, value: str) -> None:
        element = self.find(selector)
        element.clear()
        element.send_keys(value)

    def select(self, selector: str, value: str) -> None:
        self.execute(
            """
            const select = document.querySelector(arguments[0]);
            select.value = arguments[1];
            select.dispatchEvent(new Event('change', {bubbles: true}));
            """,
            selector,
            value,
        )

    def text(self, selector: str) -> str:
        return self.find(selector).text()

    def attribute(self, selector: str, name: str) -> str | None:
        return self.find(selector).attribute(name)

    def enabled(self, selector: str) -> bool:
        return self.find(selector).enabled()

    def wait_until(self, predicate: Callable[[], bool], *, timeout: float = 20.0) -> None:
        deadline = time.monotonic() + timeout
        last_error: AssertionError | None = None
        while time.monotonic() < deadline:
            try:
                if predicate():
                    return
            except AssertionError as exc:
                last_error = exc
            time.sleep(0.05)
        if last_error is not None:
            raise last_error
        raise AssertionError("browser condition did not become true before timeout")

    def wait_text(self, selector: str, expected: str, *, timeout: float = 20.0) -> None:
        try:
            self.wait_until(lambda: expected in self.text(selector), timeout=timeout)
        except AssertionError as exc:
            raise AssertionError(
                f"{selector!r} did not contain {expected!r}; current={self.text(selector)!r}"
            ) from exc

    def _command(self, method: str, path: str, payload: object | None = None) -> object:
        value = self._raw_command(self.port, method, path, payload)
        return value

    @staticmethod
    def _raw_command(
        port: int,
        method: str,
        path: str,
        payload: object | None = None,
    ) -> object:
        connection = HTTPConnection("127.0.0.1", port, timeout=20)
        try:
            headers = {"Accept": "application/json"}
            body: bytes | None = None
            if payload is not None:
                headers["Content-Type"] = "application/json"
                body = json.dumps(payload).encode("utf-8")
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read()
            if response.status >= 400:
                raise AssertionError(
                    f"WebDriver {method} {path} failed with {response.status}: {raw!r}"
                )
            if not raw:
                return None
            document = json.loads(raw)
            envelope = _object(document)
            return envelope.get("value")
        finally:
            connection.close()


@contextmanager
def _browser() -> Iterator[_WebDriver]:
    if not all(
        path.is_file() and os.access(path, os.X_OK)
        for path in (_FIREFOX, _GECKODRIVER)
    ):
        pytest.skip("Firefox and geckodriver are required for browser E2E")
    driver = _WebDriver.launch()
    try:
        yield driver
    finally:
        driver.close()


@contextmanager
def _running_server(
    service: WebService,
    *,
    port: int = 0,
) -> Iterator[tuple[_Server, int]]:
    server = create_server(
        service,
        host="127.0.0.1",
        port=port,
        csrf_token_factory=lambda: "browser-e2e-csrf",
    )
    thread = Thread(target=server.serve_forever, name="browser-e2e-server", daemon=True)
    thread.start()
    try:
        yield server, server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_driver(port: int, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError("geckodriver exited before becoming ready")
        try:
            _WebDriver._raw_command(port, "GET", "/status")
            return
        except (OSError, AssertionError):
            time.sleep(0.1)
    raise AssertionError("geckodriver did not become ready")


def _firefox_binary() -> Path | None:
    """Use the requested launcher when it is itself a binary, else its snap ELF."""

    try:
        if _FIREFOX.read_bytes()[:4] == b"\x7fELF":
            return _FIREFOX
    except OSError:
        return None
    if _FIREFOX_SNAP_BINARY.is_file() and os.access(_FIREFOX_SNAP_BINARY, os.X_OK):
        return _FIREFOX_SNAP_BINARY
    return None


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise AssertionError("WebDriver returned a non-object JSON value")
    return {str(key): item for key, item in value.items()}


def _service(
    tmp_path: Path,
    factory: _BrowserPipelineFactory,
    project_ids: Iterator[str],
    job_ids: Iterator[str],
    run_ids: Iterator[str],
) -> WebService:
    audio_root = tmp_path / "audio"
    audio_root.mkdir(parents=True, exist_ok=True)
    (audio_root / "narration.wav").write_bytes(b"browser fake narration")
    return WebService(
        projects_root=tmp_path / "projects",
        audio_root=audio_root,
        audio_probe=_BrowserAudioProbe(),
        silence_detector=_BrowserSilenceDetector(),
        pipeline_factory=factory,
        project_id_factory=lambda: next(project_ids),
        job_id_factory=lambda: next(job_ids),
        run_id_factory=lambda: next(run_ids),
    )


def _create_project_before_browser(service: WebService, project_id: str) -> None:
    created = service.create_project(
        title=f"Projeto {project_id}",
        script="# Abertura\nIntrodução.\n\n## Fecho\nConclusão.\n",
        audio_asset_id="audio-narration",
    )
    assert created["project"]["id"] == project_id
    service.confirm_timeline(project_id)


def _create_project_in_browser(driver: _WebDriver, project_id: str) -> None:
    driver.fill("#project-title", "Projeto criado no Firefox")
    driver.fill(
        "#project-script",
        "# Abertura\nIntrodução.\n\n## Fecho\nConclusão.\n",
    )
    driver.select("#project-audio", "audio-narration")
    driver.click("#create-form button[type='submit']")
    driver.wait_text("#project-id", project_id)


def _confirm_and_render(driver: _WebDriver, project_id: str) -> None:
    driver.wait_until(lambda: driver.enabled("#confirm-button"))
    driver.click("#confirm-button")
    driver.wait_text("#timeline-status", "confirmed")
    driver.wait_until(lambda: driver.enabled("#render-button"))
    driver.click("#render-button")
    driver.wait_text("#project-id", project_id)


def _asset_request(port: int, source: str) -> tuple[int, bytes]:
    path = urlsplit(source).path
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("GET", path, headers={"Host": f"127.0.0.1:{port}"})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def test_firefox_drives_create_render_regenerate_restore_and_restart_recovery(
    tmp_path: Path,
) -> None:
    """Prove the operator loop in a real Firefox with a deterministic pipeline seam."""

    factory = _BrowserPipelineFactory()
    project_ids = iter(("2026_e2e_beta", "2026_e2e_alpha"))
    job_ids = iter(("job-e2e-render", "job-e2e-regenerate"))
    run_ids = iter(("run-e2e-render", "run-e2e-regenerate"))
    service = _service(tmp_path, factory, project_ids, job_ids, run_ids)
    _create_project_before_browser(service, "2026_e2e_beta")
    project_root = tmp_path / "projects" / "2026_e2e_beta"

    with _browser() as driver:
        with _running_server(service) as (_, port):
            base_url = f"http://127.0.0.1:{port}/"
            driver.get(base_url)
            driver.wait_text("#connection-status", "Sessão local pronta")
            _create_project_in_browser(driver, "2026_e2e_alpha")
            _confirm_and_render(driver, "2026_e2e_alpha")
            assert factory.started.wait(timeout=5)

            driver.select("#project-selector", "2026_e2e_beta")
            driver.click("#open-project-button")
            driver.wait_text("#project-id", "2026_e2e_beta")
            factory.released.set()
            assert factory.completed.wait(timeout=5)
            driver.wait_until(
                lambda: driver.text("#project-id") == "2026_e2e_beta",
                timeout=3,
            )

            driver.select("#project-selector", "2026_e2e_alpha")
            driver.click("#open-project-button")
            driver.wait_text("#project-id", "2026_e2e_alpha")
            driver.wait_text("#revision-count", "1")
            final_before = driver.attribute("#final-video", "src")
            scene_before = driver.attribute("#scene-video", "src")
            assert final_before is not None and "/api/assets/media-" in final_before
            assert scene_before is not None and "/api/assets/media-" in scene_before
            assert _asset_request(port, final_before) == (200, b"final:run-e2e-render")
            assert _asset_request(port, scene_before)[0] == 200

            driver.wait_until(lambda: driver.enabled("#regenerate-button"))
            driver.fill("#scene-correction", "Aumentar o contraste da abertura")
            driver.click("#regenerate-button")
            driver.wait_text("#state-badge", "Concluído")
            driver.wait_text("#revision-count", "2")
            assert factory.calls[1]["scene"] == "abertura"
            assert factory.calls[1]["base_run_id"] == "run-e2e-render"
            assert factory.calls[1]["correction"] == "Aumentar o contraste da abertura"
            final_after = driver.attribute("#final-video", "src")
            assert final_after is not None and final_after != final_before
            assert _asset_request(port, final_after) == (200, b"final:run-e2e-regenerate")

            revisions = driver.find_all("#revision-list button")
            assert len(revisions) == 2
            assert "v001" in revisions[0].text()
            assert "v002" in revisions[1].text()
            revisions[0].click()
            driver.wait_until(
                lambda: driver.attribute("#final-video", "src") == final_before
                and driver.text("#state-badge") == "Revisão restaurada"
            )
            restored = driver.attribute("#final-video", "src")
            assert restored == final_before
            revisions = driver.find_all("#revision-list button")
            revisions[1].click()
            driver.wait_until(
                lambda: driver.attribute("#final-video", "src") == final_after
                and driver.text("#state-badge") == "Revisão restaurada"
            )
            assert driver.text("#state-badge") == "Revisão restaurada"

            driver.select("#project-selector", "2026_e2e_beta")
            driver.click("#open-project-button")
            driver.wait_text("#project-id", "2026_e2e_beta")
            RevisionStore(project_root).start_working(
                project_id="2026_e2e_beta",
                job_id="job-browser-interrupted",
                run_id="run-browser-interrupted",
                status="running",
                base_package_hashes=_project_package_hashes(
                    project_root,
                    Project.model_validate_json(
                        (project_root / "project.json").read_text(encoding="utf-8")
                    ),
                ),
                messages=["worker stopped before browser restart"],
                asset_ids=["audio-narration"],
            )
        service.close()

        restarted = _service(
            tmp_path,
            factory,
            iter(("unused-project",)),
            iter(("job-e2e-retry",)),
            iter(("unused-run",)),
        )
        try:
            with _running_server(restarted, port=port):
                driver.refresh()
                driver.wait_text("#project-id", "2026_e2e_beta")
                driver.wait_text("#job-list", "job-browser-interrupted")
                driver.wait_text("#job-list", "worker stopped before browser restart")
                driver.click("#job-list .job-retry")
                driver.wait_text("#state-badge", "Concluído")
                assert factory.calls[-1]["scene"] is None
                assert factory.calls[-1]["base_run_id"] is None
                assert factory.calls[-1]["correction"] is None
        finally:
            restarted.close()
