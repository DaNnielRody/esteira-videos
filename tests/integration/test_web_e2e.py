"""RED browser contract for the local operator workflow in real Firefox."""

from __future__ import annotations

import hashlib
import json
import shutil
import socket
import subprocess
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import replace
from http.client import HTTPConnection
from pathlib import Path
from threading import Event, Thread
from typing import Protocol

import pytest

from tests.test_project_render import (
    FakeComposer,
    FakeFinalValidator,
    FakeManimRunner,
    FakeNormalizedValidator,
    FakeObserver,
    FakeProvider,
    FakeRawValidator,
    FakeTemporalNormalizer,
)
from video_pipeline.temporal import TemporalNormalizationResult
from video_pipeline.video import (
    CompositionProfile,
    CompositionResult,
    VideoPipeline,
    VideoResult,
)

CONTRACT_MISSING = "OPERATOR_UI_CONTRACT_MISSING"


class _HttpServer(Protocol):
    server_address: tuple[str, int]

    def serve_forever(self) -> None: ...

    def shutdown(self) -> None: ...

    def server_close(self) -> None: ...


class _FakeAudioProbe:
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
            "probe_result": {
                "format": {"format_name": "wav", "duration": "10.0"},
                "streams": [
                    {
                        "index": 0,
                        "codec_type": "audio",
                        "codec_name": "pcm_s16le",
                        "sample_rate": "48000",
                        "channels": 1,
                    }
                ],
            },
        }


class _NoSilence:
    def __call__(self, _path: Path) -> tuple[object, ...]:
        return ()


class _PlayableTemporalNormalizer(FakeTemporalNormalizer):
    def __init__(self, validator: FakeNormalizedValidator, media: bytes) -> None:
        super().__init__(validator)
        self.media = media

    def normalize(
        self,
        raw_path: str | Path,
        *,
        normalized_path: str | Path,
        observed_duration_seconds: float,
        target_duration_seconds: float,
        target_resolution: tuple[int, int],
        target_fps: int,
        target_timebase: int,
        target_pixel_format: str,
        validator: object | None = None,
    ) -> TemporalNormalizationResult:
        result = super().normalize(
            raw_path,
            normalized_path=normalized_path,
            observed_duration_seconds=observed_duration_seconds,
            target_duration_seconds=target_duration_seconds,
            target_resolution=target_resolution,
            target_fps=target_fps,
            target_timebase=target_timebase,
            target_pixel_format=target_pixel_format,
            validator=validator,
        )
        Path(normalized_path).write_bytes(self.media)
        return result


class _PlayableComposer(FakeComposer):
    def __init__(self, media: bytes) -> None:
        super().__init__()
        self.media = media

    def compose(
        self,
        scene_paths: Sequence[Path],
        narration_path: Path,
        output_path: Path | None = None,
        *,
        expected_duration_seconds: float | None = None,
        profile: CompositionProfile | None = None,
        validator: object | None = None,
    ) -> CompositionResult:
        result = super().compose(
            scene_paths,
            narration_path,
            output_path,
            expected_duration_seconds=expected_duration_seconds,
            profile=profile,
            validator=validator,
        )
        assert output_path is not None
        output_path.write_bytes(self.media)
        validate = getattr(validator, "validate", None)
        assert callable(validate)
        return replace(result, validation=validate(output_path))


class _ControlledPipelineFactory:
    """Canonical pipeline with deterministic start/release and one failure."""

    def __init__(self, run_ids: tuple[str, ...], *, failure_run: str) -> None:
        self.started = {run_id: Event() for run_id in run_ids}
        self.release = {run_id: Event() for run_id in run_ids}
        self.failure_run = failure_run
        for run_id, event in self.release.items():
            if run_id != run_ids[0]:
                event.set()

    def __call__(self, run_id: str) -> _ControlledPipeline:
        return _ControlledPipeline(self, run_id)


class _ControlledPipeline:
    def __init__(self, owner: _ControlledPipelineFactory, run_id: str) -> None:
        self.owner = owner
        self.run_id = run_id

    def render(
        self,
        project_path: str | Path,
        *,
        max_attempts: int = 3,
        scene: str | None = None,
        base_run_id: str | None = None,
        correction: str | None = None,
        on_progress: Callable[[object], None] | None = None,
    ) -> VideoResult:
        self.owner.started[self.run_id].set()
        if not self.owner.release[self.run_id].wait(timeout=10):
            raise TimeoutError("browser test did not release the canonical render")
        if self.run_id == self.owner.failure_run:
            raise RuntimeError("deterministic provider failure")
        project_json = Path(project_path)
        media = (
            Path(__file__).parents[2] / "examples/rendered/colored-scene.mp4"
        ).read_bytes()
        normalized_validator = FakeNormalizedValidator()
        pipeline = VideoPipeline(
            provider=FakeProvider(project_json),
            runner=FakeManimRunner(),
            validator=FakeRawValidator(),
            observer=FakeObserver(),
            temporal_normalizer=_PlayableTemporalNormalizer(
                normalized_validator,
                media,
            ),
            normalized_validator=normalized_validator,
            final_validator=FakeFinalValidator(),
            composer=_PlayableComposer(media),
            id_factory=lambda: self.run_id,
        )
        return pipeline.render(
            project_json,
            max_attempts=max_attempts,
            scene=scene,
            base_run_id=base_run_id,
            correction=correction,
            on_progress=on_progress,  # type: ignore[arg-type]
        )


def _load_contract() -> tuple[type[object], Callable[..., _HttpServer]]:
    """Fail before environment setup when the operator UI is not implemented."""

    try:
        from video_pipeline.web.server import create_server
        from video_pipeline.web.service import WebService
    except (ImportError, ModuleNotFoundError) as exc:  # pragma: no cover - RED seam
        pytest.fail(f"{CONTRACT_MISSING}: {exc}", pytrace=False)
    static_root = Path(__file__).parents[2] / "src/video_pipeline/web/static"
    required = tuple(static_root / name for name in ("index.html", "app.css", "app.js"))
    if (
        not isinstance(WebService, type)
        or not callable(create_server)
        or any(not path.is_file() for path in required)
    ):
        pytest.fail(CONTRACT_MISSING, pytrace=False)
    return WebService, create_server


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class _Driver:
    def __init__(self, port: int, session_id: str) -> None:
        self.port = port
        self.session_id = session_id

    def command(
        self,
        method: str,
        suffix: str,
        payload: dict[str, object] | None = None,
    ) -> object:
        connection = HTTPConnection("127.0.0.1", self.port, timeout=15)
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json; charset=utf-8"}
        try:
            connection.request(
                method,
                f"/session/{self.session_id}{suffix}",
                body=body,
                headers=headers,
            )
            response = connection.getresponse()
            document = json.loads(response.read() or b"{}")
        finally:
            connection.close()
        if response.status >= 400:
            raise AssertionError(f"WebDriver {method} {suffix} failed: {document}")
        return document.get("value")

    def navigate(self, url: str) -> None:
        self.command("POST", "/url", {"url": url})

    def execute(self, script: str, *args: object) -> object:
        return self.command("POST", "/execute/sync", {"script": script, "args": args})

    def execute_async(self, script: str, *args: object) -> object:
        return self.command("POST", "/execute/async", {"script": script, "args": args})

    def rect(self, width: int, height: int = 900) -> None:
        self.command("POST", "/window/rect", {"x": 0, "y": 0, "width": width, "height": height})

    def tab(self) -> None:
        self.command(
            "POST",
            "/actions",
            {
                "actions": [
                    {
                        "type": "key",
                        "id": "keyboard",
                        "actions": [
                            {"type": "keyDown", "value": "\ue004"},
                            {"type": "keyUp", "value": "\ue004"},
                        ],
                    }
                ]
            },
        )
        self.command("DELETE", "/actions")


def _webdriver_request(
    port: int,
    method: str,
    path: str,
    payload: dict[str, object] | None = None,
) -> tuple[int, dict[str, object]]:
    connection = HTTPConnection("127.0.0.1", port, timeout=15)
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    try:
        connection.request(
            method,
            path,
            body=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        response = connection.getresponse()
        raw = response.read()
        return response.status, json.loads(raw or b"{}")
    finally:
        connection.close()


@contextmanager
def _firefox() -> Iterator[_Driver]:
    geckodriver = shutil.which("geckodriver")
    firefox = shutil.which("firefox")
    if geckodriver is None or firefox is None:
        pytest.fail(CONTRACT_MISSING, pytrace=False)
    port = _free_port()
    process = subprocess.Popen(  # noqa: S603
        [geckodriver, "--port", str(port), "--log", "fatal"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    session_id: str | None = None
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                status, document = _webdriver_request(
                    port,
                    "POST",
                    "/session",
                    {
                        "capabilities": {
                            "alwaysMatch": {
                                "browserName": "firefox",
                                "moz:firefoxOptions": {
                                    "args": ["-headless"],
                                    "prefs": {"ui.prefersReducedMotion": 1},
                                },
                            }
                        }
                    },
                )
                if status < 400:
                    value = document["value"]
                    assert isinstance(value, dict)
                    raw_session_id = value["sessionId"]
                    assert isinstance(raw_session_id, str)
                    session_id = raw_session_id
                    break
            except (ConnectionError, OSError):
                pass
            if time.monotonic() >= deadline:
                raise AssertionError("geckodriver did not create a Firefox session")
            time.sleep(0.05)
        yield _Driver(port, session_id)
    finally:
        if session_id is not None:
            try:
                _webdriver_request(port, "DELETE", f"/session/{session_id}")
            except OSError:
                pass
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@contextmanager
def _application(
    tmp_path: Path,
    *,
    project_ids: tuple[str, ...] = ("2026_browser_ui",),
    failure_run: str = "run-4",
) -> Iterator[tuple[object, int, _ControlledPipelineFactory]]:
    WebService, create_server = _load_contract()
    audio_root = tmp_path / "audio"
    audio_root.mkdir()
    (audio_root / "narration.wav").write_bytes(b"browser narration")
    run_ids = tuple(f"run-{number}" for number in range(1, 5))
    jobs = iter(f"job-{number}" for number in range(1, 5))
    runs = iter(run_ids)
    projects = iter(project_ids)
    pipeline_factory = _ControlledPipelineFactory(run_ids, failure_run=failure_run)
    service = WebService(  # type: ignore[call-arg]
        tmp_path / "projects",
        audio_root,
        audio_probe=_FakeAudioProbe(),
        silence_detector=_NoSilence(),
        project_id_factory=lambda: next(projects),
        job_id_factory=lambda: next(jobs),
        run_id_factory=lambda: next(runs),
        pipeline_factory=pipeline_factory,
    )
    server = create_server(
        service,
        host="127.0.0.1",
        port=0,
        csrf_token_factory=lambda: "browser-csrf",
    )
    thread = Thread(target=server.serve_forever, name="browser-ui-http", daemon=True)
    thread.start()
    try:
        yield service, server.server_address[1], pipeline_factory
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        service.close()  # type: ignore[attr-defined]
        assert not thread.is_alive()


def _wait(
    driver: _Driver,
    script: str,
    *args: object,
    timeout: float = 10,
) -> object:
    deadline = time.monotonic() + timeout
    while True:
        value = driver.execute(script, *args)
        if value:
            return value
        if time.monotonic() >= deadline:
            raise AssertionError(f"browser condition timed out: {script[:100]}")
        time.sleep(0.05)


def _click(driver: _Driver, *words: str) -> None:
    clicked = driver.execute(
        """
        const words = arguments[0].map(value => value.toLocaleLowerCase());
        const visible = element => Boolean(element.offsetWidth || element.offsetHeight);
        const button = [...document.querySelectorAll('button')].find(element => {
          const name = (element.getAttribute('aria-label') || element.innerText || '')
            .toLocaleLowerCase();
          return visible(element) && !element.disabled && words.some(word => name.includes(word));
        });
        if (!button) return false;
        button.click();
        return true;
        """,
        list(words),
    )
    assert clicked is True


def _fill(driver: _Driver, name: str, value: str) -> None:
    changed = driver.execute(
        """
        const control = document.querySelector(`[name="${arguments[0]}"]`);
        if (!control) return false;
        control.value = arguments[1];
        control.dispatchEvent(new Event('input', {bubbles: true}));
        control.dispatchEvent(new Event('change', {bubbles: true}));
        return true;
        """,
        name,
        value,
    )
    assert changed is True


def _body_contains(driver: _Driver, value: str) -> bool:
    result = driver.execute(
        "return document.body.innerText.toLocaleLowerCase().includes(arguments[0]);",
        value.casefold(),
    )
    return result is True


def _click_revision(driver: _Driver, revision_id: str) -> None:
    selected = driver.execute(
        """
        const target = [...document.querySelectorAll('[role="option"], [role="tab"], button')]
          .find(element => (element.innerText || '').trim().includes(arguments[0]));
        if (!target || target.disabled) return false;
        target.click();
        return true;
        """,
        revision_id,
    )
    assert selected is True


@pytest.mark.integration
def test_firefox_operates_canonical_revisions_and_rejects_stale_polling(
    tmp_path: Path,
) -> None:
    _load_contract()
    project_id = "2026_browser_ui"
    with _application(tmp_path) as (service, port, pipeline_factory), _firefox() as driver:
        driver.navigate(f"http://127.0.0.1:{port}/")
        _wait(driver, "return document.readyState === 'complete';")
        assert driver.execute(
            "return document.querySelectorAll('[role=\"log\"][aria-live=\"polite\"]').length;"
        ) == 1
        assert not _body_contains(driver, str(tmp_path).casefold())
        assert driver.execute("return document.querySelectorAll('main, aside').length;") == 3
        assert driver.execute(
            "return [...document.querySelectorAll('video')].every(video => !video.src);"
        ) is True
        assert driver.execute(
            "return document.querySelectorAll('[aria-pressed=\"true\"]').length;"
        ) == 0

        _wait(
            driver,
            """
            return [...document.querySelectorAll('[name="audio_asset_id"] option')]
              .some(option => Boolean(option.value));
            """,
        )
        _fill(driver, "title", "Operacao browser")
        _fill(
            driver,
            "script",
            "# Abertura\n@capabilities: basic_geometry\nUma cena canonica.\n",
        )
        selected_audio = driver.execute(
            """
            const select = document.querySelector('[name="audio_asset_id"]');
            const option = select ? [...select.options].find(item => Boolean(item.value)) : null;
            if (select && option) {
              select.value = option.value;
              select.dispatchEvent(new Event('change', {bubbles: true}));
            }
            return Boolean(select && option && select.value === option.value);
            """
        )
        assert selected_audio is True
        _click(driver, "criar", "create")
        _wait(driver, "return document.body.innerText.includes('2026_browser_ui');")
        assert _body_contains(driver, "candidate") or _body_contains(driver, "candidata")
        _click(driver, "confirmar", "confirm")
        _wait(
            driver,
            "return /confirmed|confirmada/i.test(document.body.innerText);",
        )

        _click(driver, "gerar", "render")
        assert pipeline_factory.started["run-1"].wait(timeout=5)
        _wait(driver, "return /fila|queued|running|gerando/i.test(document.body.innerText);")
        pipeline_factory.release["run-1"].set()
        _wait(driver, "return document.body.innerText.includes('v001');", timeout=15)
        _wait(
            driver,
            "return [...document.querySelectorAll('video')].every(video => Boolean(video.src));",
        )
        media_urls = driver.execute(
            "return [...document.querySelectorAll('video')].map(video => video.src);"
        )
        assert isinstance(media_urls, list) and len(media_urls) == 2
        assert all("/api/assets/" in url and str(tmp_path) not in url for url in media_urls)
        playback = driver.execute_async(
            """
            const done = arguments[arguments.length - 1];
            const videos = [...document.querySelectorAll('video')];
            Promise.all(videos.map(async video => {
              if (video.readyState < 1) {
                await new Promise((resolve, reject) => {
                  video.addEventListener('loadedmetadata', resolve, {once: true});
                  video.addEventListener('error', () => reject(new Error('media error')),
                    {once: true});
                });
              }
              video.muted = true;
              await video.play();
              const played = !video.paused && Number.isFinite(video.duration);
              video.pause();
              return {played, ready: video.readyState, duration: video.duration};
            })).then(value => done({value})).catch(error => done({error: String(error)}));
            """
        )
        assert isinstance(playback, dict) and "error" not in playback
        played_media = playback["value"]
        assert isinstance(played_media, list) and len(played_media) == 2
        assert all(media["played"] and media["ready"] >= 1 for media in played_media)

        _fill(driver, "correction", "Aumente o contraste visual.")
        _click(driver, "regenerar", "regenerate")
        _wait(driver, "return document.body.innerText.includes('v002');", timeout=15)
        assert driver.execute("return document.querySelector('#accept-button').disabled;") is False
        _click_revision(driver, "v001")
        _wait(
            driver,
            """
            return Boolean(document.querySelector('[aria-pressed="true"]')
              ?.innerText.includes('v001'));
            """,
        )
        assert service.inspect(project_id)["ui"]["current_revision_id"] == "v001"  # type: ignore[index,attr-defined]
        assert driver.execute("return document.querySelector('#accept-button').disabled;") is True
        _click_revision(driver, "v002")
        _wait(
            driver,
            """
            return Boolean(document.querySelector('[aria-pressed="true"]')
              ?.innerText.includes('v002'));
            """,
        )
        assert service.inspect(project_id)["ui"]["current_revision_id"] == "v002"  # type: ignore[index,attr-defined]
        assert driver.execute("return document.querySelector('#accept-button').disabled;") is False
        driver.execute(
            r"""
            window.__releaseStale = false;
            window.__staleCaptured = false;
            window.__staleDeliveryWindowElapsed = false;
            const originalFetch = window.fetch.bind(window);
            window.fetch = async (...args) => {
              const response = await originalFetch(...args);
              const url = typeof args[0] === 'string' ? args[0] : String(args[0]?.url || '');
              if (!window.__staleCaptured && /\/api\/jobs\//.test(url)) {
                const payload = await response.clone().json();
                if (payload.state === 'success' && payload.revision_id) {
                  window.__staleCaptured = true;
                  await new Promise(resolve => {
                    const check = () => window.__releaseStale ? resolve() : setTimeout(check, 10);
                    check();
                  });
                  setTimeout(() => { window.__staleDeliveryWindowElapsed = true; }, 100);
                }
              }
              return response;
            };
            return true;
            """
        )
        _fill(driver, "correction", "Primeira resposta atrasada.")
        _click(driver, "regenerar", "regenerate")
        _wait(driver, "return window.__staleCaptured === true;", timeout=15)
        _click_revision(driver, "v001")
        _wait(
            driver,
            """
            return document.querySelector('[aria-pressed="true"]')
              ?.innerText.includes('v001');
            """,
        )
        assert service.inspect(project_id)["ui"]["current_revision_id"] == "v001"  # type: ignore[index,attr-defined]
        driver.execute("window.__releaseStale = true; return true;")
        _wait(
            driver,
            "return window.__staleDeliveryWindowElapsed === true;",
        )
        selected_text = driver.execute(
            "return document.querySelector('[aria-pressed=\"true\"]')?.innerText;"
        )
        assert isinstance(selected_text, str) and "v001" in selected_text
        assert service.inspect(project_id)["ui"]["current_revision_id"] == "v001"  # type: ignore[index,attr-defined]
        assert not _body_contains(driver, str(tmp_path).casefold())

        _click_revision(driver, "v002")
        _wait(
            driver,
            """
            return document.querySelector('[aria-pressed="true"]')
              ?.innerText.includes('v002');
            """,
        )
        assert service.inspect(project_id)["ui"]["current_revision_id"] == "v002"  # type: ignore[index,attr-defined]
        _fill(driver, "correction", "Falha deterministica.")
        _click(driver, "regenerar", "regenerate")
        _wait(driver, "return /failure|falha|erro/i.test(document.body.innerText);")
        assert _body_contains(driver, "v002")
        assert driver.execute(
            "return document.querySelectorAll('[role=\"log\"][aria-live=\"polite\"]').length;"
        ) == 1
        assert not _body_contains(driver, str(tmp_path).casefold())
        failure_selection = driver.execute(
            "return document.querySelector('[aria-pressed=\"true\"]')?.innerText;"
        )
        assert isinstance(failure_selection, str) and "v002" in failure_selection
        assert service.inspect(project_id)["ui"]["current_revision_id"] == "v002"  # type: ignore[index,attr-defined]
        assert driver.execute(
            """
            return [...document.querySelectorAll('#revision-list button')]
              .find(button => button.innerText.includes('v004'))?.disabled;
            """
        ) is True
        for width in (375, 768, 1280):
            driver.rect(width)
            metrics = driver.execute(
                """
                return {
                  overflow: document.documentElement.scrollWidth > window.innerWidth + 1,
                  viewport: window.innerWidth,
                  videos: [...document.querySelectorAll('video')].map(video => ({
                    width: video.getBoundingClientRect().width,
                    ratio: getComputedStyle(video).aspectRatio,
                  })),
                };
                """
            )
            assert isinstance(metrics, dict)
            assert metrics["overflow"] is False
            assert len(metrics["videos"]) == 2
            assert all(
                video["width"] <= metrics["viewport"] for video in metrics["videos"]
            )
            assert all(video["ratio"] != "auto" for video in metrics["videos"])

        driver.execute("document.body.focus(); return true;")
        driver.tab()
        focus = driver.execute(
            """
            const active = document.activeElement;
            const style = getComputedStyle(active);
            return {
              named: Boolean(active && (active.innerText || active.getAttribute('aria-label') ||
                document.querySelector(`label[for="${active.id}"]`)?.innerText)),
              visible: style.outlineStyle !== 'none' || style.boxShadow !== 'none',
            };
            """
        )
        assert isinstance(focus, dict) and focus == {"named": True, "visible": True}
        motion = driver.execute(
            """
            return {
              reduced: matchMedia('(prefers-reduced-motion: reduce)').matches,
              active: [...document.querySelectorAll('*')].filter(element => {
                const style = getComputedStyle(element);
                return style.animationDuration !== '0s' || style.transitionDuration !== '0s';
              }).length,
            };
            """
        )
        assert motion == {"reduced": True, "active": 0}
        selected_cue = driver.execute(
            """
            const selected = document.querySelector('[aria-pressed="true"]');
            if (!selected) return null;
            const style = getComputedStyle(selected);
            return Number.parseFloat(style.borderWidth) > 0 ||
              style.fontWeight === '700' || style.textDecorationLine !== 'none';
            """
        )
        assert selected_cue is True


@pytest.mark.integration
def test_firefox_keeps_an_initial_failure_visible_after_refreshing_inspection(
    tmp_path: Path,
) -> None:
    _load_contract()
    with (
        _application(tmp_path, failure_run="run-1") as (_, port, pipeline_factory),
        _firefox() as driver,
    ):
        driver.navigate(f"http://127.0.0.1:{port}/")
        _wait(
            driver,
            """
            return [...document.querySelectorAll('[name="audio_asset_id"] option')]
              .some(option => Boolean(option.value));
            """,
        )
        _fill(driver, "title", "Falha inicial")
        _fill(
            driver,
            "script",
            "# Abertura\n@capabilities: basic_geometry\nUma cena canonica.\n",
        )
        assert driver.execute(
            """
            const select = document.querySelector('[name="audio_asset_id"]');
            const option = select ? [...select.options].find(item => Boolean(item.value)) : null;
            if (select && option) select.value = option.value;
            return Boolean(select && option && select.value === option.value);
            """
        ) is True
        _click(driver, "criar", "create")
        _wait(driver, "return document.body.innerText.includes('2026_browser_ui');")
        _click(driver, "confirmar", "confirm")
        _wait(driver, "return /confirmed|confirmada/i.test(document.body.innerText);")
        _click(driver, "gerar", "render")
        assert pipeline_factory.started["run-1"].wait(timeout=5)
        pipeline_factory.release["run-1"].set()
        _wait(
            driver,
            "return document.querySelector('#state-badge')?.dataset.state === 'failure';",
        )
        refreshed = driver.execute_async(
            """
            const done = arguments[arguments.length - 1];
            refreshProject().then(done).catch(error => done({error: String(error)}));
            """
        )
        assert refreshed is True
        assert driver.execute(
            "return document.querySelector('#state-badge')?.dataset.state;"
        ) == "failure"
        assert driver.execute(
            "return document.querySelector('#diagnostic')?.hidden;"
        ) is False


@pytest.mark.integration
def test_firefox_accepts_the_current_ready_run_without_moving_ui_revision(
    tmp_path: Path,
) -> None:
    _load_contract()
    project_id = "2026_browser_ui"
    with _application(tmp_path) as (service, port, pipeline_factory), _firefox() as driver:
        driver.navigate(f"http://127.0.0.1:{port}/")
        _wait(
            driver,
            """
            return [...document.querySelectorAll('[name="audio_asset_id"] option')]
              .some(option => Boolean(option.value));
            """,
        )
        _fill(driver, "title", "Aceite browser")
        _fill(
            driver,
            "script",
            "# Abertura\n@capabilities: basic_geometry\nUma cena canonica.\n",
        )
        selected_audio = driver.execute(
            """
            const select = document.querySelector('[name="audio_asset_id"]');
            const option = select ? [...select.options].find(item => Boolean(item.value)) : null;
            if (select && option) {
              select.value = option.value;
              select.dispatchEvent(new Event('change', {bubbles: true}));
            }
            return Boolean(select && option && select.value === option.value);
            """
        )
        assert selected_audio is True
        _click(driver, "criar", "create")
        _wait(driver, "return document.body.innerText.includes('2026_browser_ui');")
        _click(driver, "confirmar", "confirm")
        _wait(driver, "return /confirmed|confirmada/i.test(document.body.innerText);")
        _click(driver, "gerar", "render")
        assert pipeline_factory.started["run-1"].wait(timeout=5)
        pipeline_factory.release["run-1"].set()
        _wait(driver, "return document.body.innerText.includes('v001');", timeout=15)
        project_json = tmp_path / "projects" / project_id / "project.json"
        project_before_rejected_accept = project_json.read_bytes()
        golden_root = project_json.parent / "golden"
        golden_before_rejected_accept = (
            {
                path.relative_to(golden_root).as_posix(): path.read_bytes()
                for path in golden_root.rglob("*")
                if path.is_file()
            }
            if golden_root.exists()
            else {}
        )
        pipeline_factory.release["run-2"].clear()
        _fill(driver, "correction", "Aumente o contraste visual.")
        _click(driver, "regenerar", "regenerate")
        assert pipeline_factory.started["run-2"].wait(timeout=5)
        with pytest.raises(ValueError, match="accept.*active job|queued|running"):
            service.accept_run(project_id, "run-1")  # type: ignore[attr-defined]
        assert project_json.read_bytes() == project_before_rejected_accept
        assert (
            {
                path.relative_to(golden_root).as_posix(): path.read_bytes()
                for path in golden_root.rglob("*")
                if path.is_file()
            }
            if golden_root.exists()
            else {}
        ) == golden_before_rejected_accept
        pipeline_factory.release["run-2"].set()
        _wait(driver, "return document.body.innerText.includes('v002');", timeout=15)
        assert service.inspect(project_id)["ui"]["current_revision_id"] == "v002"  # type: ignore[index,attr-defined]

        _click(driver, "aceitar", "accept")
        _wait(
            driver,
            "return document.querySelector('#golden-status')?.dataset.accepted === 'true';",
        )
        accepted = service.inspect(project_id)  # type: ignore[attr-defined]
        assert accepted["project"]["accepted_run"] == "run-2"  # type: ignore[index]
        assert accepted["project"]["current_run"] == "run-2"  # type: ignore[index]
        assert accepted["ui"]["current_revision_id"] == "v002"  # type: ignore[index]
        golden = json.loads(
            (
                tmp_path
                / "projects"
                / project_id
                / "golden"
                / "manifest.json"
            ).read_text(encoding="utf-8")
        )
        assert golden["run_id"] == "run-2"
        assert not _body_contains(driver, str(tmp_path).casefold())
        assert driver.execute("return document.querySelector('#accept-button').disabled;") is True


@pytest.mark.integration
def test_creating_another_project_invalidates_polling_from_the_previous_project(
    tmp_path: Path,
) -> None:
    _load_contract()
    first_project = "2026_browser_first"
    second_project = "2026_browser_second"
    with _application(
        tmp_path,
        project_ids=(first_project, second_project),
    ) as (service, port, pipeline_factory), _firefox() as driver:
        driver.navigate(f"http://127.0.0.1:{port}/")
        _wait(
            driver,
            """
            return [...document.querySelectorAll('[name="audio_asset_id"] option')]
              .some(option => Boolean(option.value));
            """,
        )
        _fill(driver, "title", "Primeiro projeto")
        _fill(
            driver,
            "script",
            "# Abertura\n@capabilities: basic_geometry\nUma cena canonica.\n",
        )
        selected_audio = driver.execute(
            """
            const select = document.querySelector('[name="audio_asset_id"]');
            const option = select ? [...select.options].find(item => Boolean(item.value)) : null;
            if (select && option) select.value = option.value;
            return Boolean(select && option && select.value === option.value);
            """
        )
        assert selected_audio is True
        _click(driver, "criar", "create")
        _wait(driver, f"return document.body.innerText.includes('{first_project}');")
        _click(driver, "confirmar", "confirm")
        _wait(driver, "return /confirmed|confirmada/i.test(document.body.innerText);")
        _click(driver, "gerar", "render")
        assert pipeline_factory.started["run-1"].wait(timeout=5)

        _fill(driver, "title", "Segundo projeto")
        _click(driver, "criar", "create")
        _wait(driver, f"return document.body.innerText.includes('{second_project}');")
        pipeline_factory.release["run-1"].set()
        assert driver.execute_async(
            "const done = arguments[arguments.length - 1]; setTimeout(() => done(true), 400);"
        ) is True

        assert _body_contains(driver, second_project)
        assert not _body_contains(driver, first_project)
        assert not _body_contains(driver, "v001")
        assert service.inspect(first_project)["ui"]["current_revision_id"] == "v001"  # type: ignore[index,attr-defined]
        assert "ui" not in service.inspect(second_project)  # type: ignore[operator,attr-defined]
