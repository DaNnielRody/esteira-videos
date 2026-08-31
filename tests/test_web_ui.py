"""RED contracts for the package-owned operator UI and its read-only projection."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from html.parser import HTMLParser
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread
from typing import Protocol

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

from video_pipeline.video import VideoPipeline, VideoResult

CONTRACT_MISSING = "OPERATOR_UI_CONTRACT_MISSING"
PUBLIC_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class _HttpServer(Protocol):
    server_address: tuple[str, int]

    def serve_forever(self) -> None: ...

    def shutdown(self) -> None: ...

    def server_close(self) -> None: ...


class _StaticService:
    """Minimal public-boundary fake; static requests must not consult it."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def list_audio(self) -> list[dict[str, str]]:
        self.calls.append("list_audio")
        return []


class _DocumentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.elements: list[tuple[str, dict[str, str]]] = []
        self.labels_for: set[str] = set()
        self.control_names: list[str] = []
        self.button_names: list[tuple[dict[str, str], str]] = []
        self._control: tuple[str, dict[str, str]] | None = None
        self._control_text: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = {name: value or "" for name, value in attrs}
        self.elements.append((tag, attributes))
        if tag == "label" and attributes.get("for"):
            self.labels_for.add(attributes["for"])
        if tag == "button":
            self._control = (tag, attributes)
            self._control_text = []

    def handle_data(self, data: str) -> None:
        if self._control is not None:
            self._control_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "button" or self._control is None:
            return
        _, attributes = self._control
        name = attributes.get("aria-label") or " ".join(self._control_text).strip()
        self.control_names.append(name)
        self.button_names.append((attributes, name))
        self._control = None
        self._control_text = []


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


class _CanonicalPipelineFactory:
    """Use the canonical pipeline while faking only external media boundaries."""

    def __call__(self, run_id: str) -> _CanonicalPipeline:
        return _CanonicalPipeline(run_id)


class _CanonicalPipeline:
    def __init__(self, run_id: str) -> None:
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
        project_json = Path(project_path)
        normalized_validator = FakeNormalizedValidator()
        pipeline = VideoPipeline(
            provider=FakeProvider(project_json),
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
            on_progress=on_progress,  # type: ignore[arg-type]
        )


def _load_contract() -> tuple[Callable[..., _HttpServer], type[object], type[object]]:
    """Load lazily so the intended missing UI is the only RED signature."""

    try:
        from video_pipeline.revisions import RevisionStore
        from video_pipeline.web.server import create_server
        from video_pipeline.web.service import WebService
    except (ImportError, ModuleNotFoundError) as exc:  # pragma: no cover - RED seam
        pytest.fail(f"{CONTRACT_MISSING}: {exc}", pytrace=False)

    static_root = Path(__file__).parents[1] / "src/video_pipeline/web/static"
    required = tuple(static_root / name for name in ("index.html", "app.css", "app.js"))
    if (
        not callable(create_server)
        or not isinstance(WebService, type)
        or not isinstance(RevisionStore, type)
        or any(not path.is_file() for path in required)
    ):
        pytest.fail(CONTRACT_MISSING, pytrace=False)
    return create_server, WebService, RevisionStore


@contextmanager
def _running_server(service: object) -> Iterator[tuple[_HttpServer, int]]:
    create_server, _, _ = _load_contract()
    server = create_server(
        service,
        host="127.0.0.1",
        port=0,
        csrf_token_factory=lambda: "operator-ui-csrf",
    )
    thread = Thread(target=server.serve_forever, name="test-web-ui", daemon=True)
    thread.start()
    try:
        yield server, server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def _request(port: int, method: str, path: str) -> tuple[int, dict[str, str], bytes]:
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(method, path, headers={"Host": f"127.0.0.1:{port}"})
        response = connection.getresponse()
        headers = {name.lower(): value for name, value in response.getheaders()}
        return response.status, headers, response.read()
    finally:
        connection.close()


def _create_project_with_revision(
    tmp_path: Path,
) -> tuple[object, Path, str, tuple[str, str], str]:
    _, WebService, _ = _load_contract()
    audio_root = tmp_path / "audio"
    audio_root.mkdir()
    (audio_root / "narration.wav").write_bytes(b"operator ui narration")
    projects_root = tmp_path / "projects"
    project_id = "2026_operator_ui"
    run_ids = ("run-one", "run-two")
    scene_id = "abertura"
    job_ids = iter(("job-one", "job-two"))
    generated_runs = iter(run_ids)
    service = WebService(  # type: ignore[call-arg]
        projects_root,
        audio_root,
        audio_probe=_FakeAudioProbe(),
        silence_detector=_NoSilence(),
        project_id_factory=lambda: project_id,
        job_id_factory=lambda: next(job_ids),
        run_id_factory=lambda: next(generated_runs),
        pipeline_factory=_CanonicalPipelineFactory(),
    )
    service.create_project(  # type: ignore[attr-defined]
        title="Operacao local",
        script=(
            "# Abertura\nUma cena canonica.\n\n"
            "## Explicacao\nUma segunda cena canonica.\n"
        ),
        audio_asset_id="audio-narration",
    )
    service.confirm_timeline(project_id)  # type: ignore[attr-defined]
    first = service.enqueue_render(project_id)  # type: ignore[attr-defined]
    first_result = service.wait_job(first.job_id, timeout=10)  # type: ignore[attr-defined]
    assert first_result.state == "success"
    second = service.enqueue_regeneration(  # type: ignore[attr-defined]
        project_id,
        base_run_id=run_ids[0],
        scene_id=scene_id,
        correction="Aumente o contraste do resultado.",
    )
    second_result = service.wait_job(second.job_id, timeout=10)  # type: ignore[attr-defined]
    assert second_result.state == "success"
    service.checkout_revision(project_id, "v001")  # type: ignore[attr-defined]
    return service, projects_root / project_id, project_id, run_ids, scene_id


def _snapshot_tree(root: Path) -> dict[str, tuple[str, bytes | str]]:
    snapshot: dict[str, tuple[str, bytes | str]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        mode = path.lstat().st_mode
        if path.is_symlink():
            snapshot[relative] = (f"symlink:{mode:o}", os.readlink(path))
        elif path.is_dir():
            snapshot[relative] = (f"directory:{mode:o}", "")
        else:
            snapshot[relative] = (f"file:{mode:o}", path.read_bytes())
    return snapshot


def test_package_owned_static_files_are_served_over_real_http_with_api_precedence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cwd = tmp_path / "untrusted-cwd"
    cwd.mkdir()
    (cwd / "index.html").write_text("CWD_PRIVATE_SENTINEL", encoding="utf-8")
    (cwd / "private.txt").write_text("CWD_PRIVATE_SENTINEL", encoding="utf-8")
    monkeypatch.chdir(cwd)
    service = _StaticService()

    with _running_server(service) as (_, port):
        for route, content_type in (
            ("/", "text/html"),
            ("/index.html", "text/html"),
            ("/app.css", "text/css"),
            ("/app.js", "text/javascript"),
        ):
            status, headers, body = _request(port, "GET", route)
            assert status == 200
            assert headers["content-type"].startswith(content_type)
            assert int(headers["content-length"]) == len(body)
            assert not any(name.startswith("access-control-") for name in headers)
            assert body
            assert b"CWD_PRIVATE_SENTINEL" not in body
            head_status, head_headers, head_body = _request(port, "HEAD", route)
            assert head_status == status
            assert head_headers["content-type"] == headers["content-type"]
            assert head_headers["content-length"] == headers["content-length"]
            assert not any(name.startswith("access-control-") for name in head_headers)
            assert head_body == b""

        api_status, api_headers, api_body = _request(port, "GET", "/api/session")
        assert api_status == 200
        assert api_headers["content-type"].startswith("application/json")
        assert not any(name.startswith("access-control-") for name in api_headers)
        assert json.loads(api_body) == {"csrf_token": "operator-ui-csrf"}
        for hostile in (
            "/private.txt",
            "/server.py",
            "/../private.txt",
            "/%2e%2e/private.txt",
            "/unknown",
        ):
            status, headers, body = _request(port, "GET", hostile)
            assert status == 404
            assert not any(name.startswith("access-control-") for name in headers)
            assert b"CWD_PRIVATE_SENTINEL" not in body

    assert service.calls == []


def test_inspection_projects_revision_history_and_scoped_opaque_media_ids(
    tmp_path: Path,
) -> None:
    service, project_root, project_id, run_ids, scene_id = _create_project_with_revision(
        tmp_path
    )
    try:
        before = _snapshot_tree(project_root)
        projection = service.inspect(project_id)  # type: ignore[attr-defined]
        after = _snapshot_tree(project_root)
        assert before == after
        ui = projection["ui"]
        assert isinstance(ui, dict)
        assert ui["current_revision_id"] == "v001"
        revisions = ui["revisions"]
        assert isinstance(revisions, list) and len(revisions) == 2
        assert [revision["revision_id"] for revision in revisions] == ["v001", "v002"]
        assert [revision["run_id"] for revision in revisions] == list(run_ids)
        media = ui["media"]
        assert isinstance(media, dict)
        final_id = media["final_asset_id"]
        scenes = media["scenes"]
        assert isinstance(final_id, str) and PUBLIC_ID.fullmatch(final_id)
        assert isinstance(scenes, list) and len(scenes) == 2
        assert scenes[0]["scene_id"] == scene_id
        normalized_id = scenes[0]["normalized_asset_id"]
        assert isinstance(normalized_id, str) and PUBLIC_ID.fullmatch(normalized_id)
        assert final_id != normalized_id
        serialized = json.dumps(projection)
        assert str(tmp_path) not in serialized

        final_root, final_path = service.resolve_asset(final_id)  # type: ignore[attr-defined]
        scene_root, scene_path = service.resolve_asset(normalized_id)  # type: ignore[attr-defined]
        assert final_path == project_root / "artifacts" / run_ids[0] / "final.mp4"
        assert scene_path == (
            project_root
            / "artifacts"
            / run_ids[0]
            / "scenes"
            / "01_abertura"
            / "normalized.mp4"
        )
        final_path.resolve(strict=True).relative_to(final_root.resolve(strict=True))
        scene_path.resolve(strict=True).relative_to(scene_root.resolve(strict=True))

        service.checkout_revision(project_id, "v002")  # type: ignore[attr-defined]
        working_root = project_root / "ui" / "working"
        shutil.rmtree(working_root)
        before_second_inspect = _snapshot_tree(project_root)
        second_projection = service.inspect(project_id)  # type: ignore[attr-defined]
        assert _snapshot_tree(project_root) == before_second_inspect
        assert not working_root.exists()
        second_ui = second_projection["ui"]
        assert isinstance(second_ui, dict)
        assert second_ui["current_revision_id"] == "v002"
        second_media = second_ui["media"]
        assert isinstance(second_media, dict)
        second_final_id = second_media["final_asset_id"]
        second_scenes = second_media["scenes"]
        assert isinstance(second_final_id, str) and PUBLIC_ID.fullmatch(second_final_id)
        assert isinstance(second_scenes, list) and len(second_scenes) == 2
        second_normalized_id = second_scenes[0]["normalized_asset_id"]
        assert isinstance(second_normalized_id, str)
        assert PUBLIC_ID.fullmatch(second_normalized_id)
        assert second_scenes[0]["scene_id"] == scene_id
        assert second_final_id != final_id
        assert second_normalized_id != normalized_id
        assert str(tmp_path) not in json.dumps(second_projection)

        old_final_root, old_final_path = service.resolve_asset(  # type: ignore[attr-defined]
            final_id
        )
        old_scene_root, old_scene_path = service.resolve_asset(  # type: ignore[attr-defined]
            normalized_id
        )
        new_final_root, new_final_path = service.resolve_asset(  # type: ignore[attr-defined]
            second_final_id
        )
        new_scene_root, new_scene_path = service.resolve_asset(  # type: ignore[attr-defined]
            second_normalized_id
        )
        assert (old_final_root, old_final_path) == (final_root, final_path)
        assert (old_scene_root, old_scene_path) == (scene_root, scene_path)
        assert new_final_path == project_root / "artifacts" / run_ids[1] / "final.mp4"
        assert new_scene_path == (
            project_root
            / "artifacts"
            / run_ids[1]
            / "scenes"
            / "01_abertura"
            / "normalized.mp4"
        )
        new_final_path.resolve(strict=True).relative_to(new_final_root.resolve(strict=True))
        new_scene_path.resolve(strict=True).relative_to(new_scene_root.resolve(strict=True))
        for hostile_id in ("../final", "asset/final", "asset\\final", "unknown-safe-id"):
            with pytest.raises(ValueError):
                service.resolve_asset(hostile_id)  # type: ignore[attr-defined]

        missing_scene = (
            project_root
            / "artifacts"
            / run_ids[1]
            / "scenes"
            / "02_explicacao"
            / "normalized.mp4"
        )
        missing_scene.unlink()
        incomplete_projection = service.inspect(project_id)  # type: ignore[attr-defined]
        assert "ui" not in incomplete_projection

        final_path.unlink()
        final_path.mkdir()
        with pytest.raises(ValueError):
            service.resolve_asset(final_id)  # type: ignore[attr-defined]
        shutil.rmtree(final_path)
        outside = tmp_path / "outside.mp4"
        outside.write_bytes(b"private outside media")
        scene_path.unlink()
        scene_path.symlink_to(outside)
        with pytest.raises(ValueError):
            service.resolve_asset(normalized_id)  # type: ignore[attr-defined]
    finally:
        service.close()  # type: ignore[attr-defined]


def test_document_css_and_javascript_expose_the_accessible_three_region_workflow() -> None:
    _load_contract()
    static_root = Path(__file__).parents[1] / "src/video_pipeline/web/static"
    html = (static_root / "index.html").read_text(encoding="utf-8")
    css = (static_root / "app.css").read_text(encoding="utf-8")
    javascript = (static_root / "app.js").read_text(encoding="utf-8")
    parser = _DocumentParser()
    parser.feed(html)

    regions = [attrs for tag, attrs in parser.elements if tag in {"aside", "main"}]
    assert len(regions) == 3
    assert all(attrs.get("aria-label") or attrs.get("aria-labelledby") for attrs in regions)
    live_regions = [
        attrs
        for _, attrs in parser.elements
        if attrs.get("role") == "log" and attrs.get("aria-live") == "polite"
    ]
    assert len(live_regions) == 1
    assert any(
        attrs.get("id") == "revision-conversation"
        for _, attrs in parser.elements
    )
    controls = [
        attrs
        for tag, attrs in parser.elements
        if tag in {"button", "input", "select", "textarea"}
    ]
    for attributes in controls:
        assert (
            attributes.get("aria-label")
            or attributes.get("aria-labelledby")
            or attributes.get("id") in parser.labels_for
            or any(
                button_attrs is attributes and bool(name)
                for button_attrs, name in parser.button_names
            )
        )
    capabilities = " ".join(parser.control_names).casefold()
    assert any(word in capabilities for word in ("criar", "create"))
    assert any(word in capabilities for word in ("confirmar", "confirm"))
    assert any(word in capabilities for word in ("gerar", "render"))
    assert any(word in capabilities for word in ("regenerar", "regenerate"))
    assert any(word in capabilities for word in ("restaurar", "checkout"))
    assert any(word in capabilities for word in ("aceitar", "accept"))

    videos = [attrs for tag, attrs in parser.elements if tag == "video"]
    assert len(videos) == 2
    assert all(
        "controls" in attrs and (attrs.get("aria-label") or attrs.get("aria-labelledby"))
        for attrs in videos
    )
    assert len(
        {
            attrs.get("aria-label") or attrs.get("aria-labelledby")
            for attrs in videos
        }
    ) == 2
    selection_owners = [
        attrs
        for _, attrs in parser.elements
        if attrs.get("role") == "group"
    ]
    assert len(selection_owners) >= 2
    assert all(
        attrs.get("aria-label") or attrs.get("aria-labelledby")
        for attrs in selection_owners
    )
    assert 'button.setAttribute("aria-pressed"' in javascript
    assert 'button.setAttribute("aria-selected"' not in javascript
    assert "renderConversation(state.selectedRevision)" in javascript

    stylesheet_links = [
        attrs
        for tag, attrs in parser.elements
        if tag == "link" and attrs.get("rel") == "stylesheet"
    ]
    scripts = [attrs for tag, attrs in parser.elements if tag == "script" and attrs.get("src")]
    assert any(attrs.get("href") == "/app.css" for attrs in stylesheet_links)
    assert any(attrs.get("src") == "/app.js" for attrs in scripts)
    color_tokens = re.findall(
        r"--(?:color|ink|surface|accent|state)-[a-z0-9-]+\s*:\s*([^;]+)",
        css.casefold(),
    )
    assert len(color_tokens) >= 3
    assert all("hsl(" in value for value in color_tokens)
    assert javascript.strip()


def test_revision_history_is_numeric_across_the_v999_boundary(tmp_path: Path) -> None:
    _, _, RevisionStore = _load_contract()
    project_root = tmp_path / "2026_revision_order"
    project_root.mkdir()
    store = RevisionStore(project_root)  # type: ignore[call-arg]
    revisions_root = project_root / "ui" / "revisions"
    for revision_id, job_id, run_id, parent in (
        ("v999", "job-999", "run-999", None),
        ("v1000", "job-1000", "run-1000", "v999"),
    ):
        document = {
            "schema_version": "project.ui-revision/1",
            "revision_id": revision_id,
            "project_id": "2026_revision_order",
            "job_id": job_id,
            "run_id": run_id,
            "status": "success",
            "parent_revision_id": parent,
            "base_package_hashes": {},
            "correction": None,
            "messages": [],
            "asset_ids": ["final"],
        }
        (revisions_root / f"{revision_id}.json").write_text(
            json.dumps(document),
            encoding="utf-8",
        )
    assert [
        revision.revision_id  # type: ignore[attr-defined]
        for revision in store.list_revisions()  # type: ignore[attr-defined]
    ] == ["v999", "v1000"]
