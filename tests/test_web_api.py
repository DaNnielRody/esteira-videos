"""RED contracts for the loopback-only stdlib HTTP boundary."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread
from typing import Protocol

import pytest

CONTRACT_MISSING = "WEB_CANONICAL_SERVICE_CONTRACT_MISSING"
FILE_SENTINEL = "PRIVATE_FILE_SENTINEL"
STATIC_SENTINEL = "CWD_STATIC_SENTINEL"


class _HttpServer(Protocol):
    """The small lifecycle surface supplied by the stdlib HTTP server."""

    server_address: tuple[str, int]

    def serve_forever(self) -> None:
        ...

    def shutdown(self) -> None:
        ...

    def server_close(self) -> None:
        ...


class _FakeService:
    """Minimal fake at the public service method boundary."""

    def __init__(
        self,
        *,
        error_detail: str = "",
        asset_root: Path | None = None,
        asset_paths: dict[str, Path] | None = None,
        queue_full_error: type[ValueError] = ValueError,
    ) -> None:
        self.error_detail = error_detail
        self._asset_root = asset_root
        self._asset_paths = asset_paths or {}
        self.queue_full_error = queue_full_error
        self.create_calls: list[dict[str, str]] = []
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.queue_full = False
        self.runtime_failure = False

    def _record(self, name: str, **values: object) -> None:
        self.calls.append((name, values))

    def _missing(self, kind: str, value: str) -> None:
        raise KeyError(f"unknown {kind} {value} {self.error_detail}")

    def list_audio(self) -> list[dict[str, object]]:
        self._record("list_audio")
        return [{"id": "audio-narration", "label": "Narration"}]

    def resolve_asset(self, asset_id: str) -> tuple[Path, Path]:
        """Return the explicit root and candidate without serializing either path."""

        self._record("resolve_asset", asset_id=asset_id)
        candidate = self._asset_paths.get(asset_id)
        if candidate is None:
            self._missing("asset", asset_id)
        if self._asset_root is None:
            raise RuntimeError("asset root was not configured")
        return self._asset_root, candidate

    def create_project(
        self,
        *,
        title: str,
        script: str,
        audio_asset_id: str,
    ) -> dict[str, object]:
        self.create_calls.append(
            {
                "title": title,
                "script": script,
                "audio_asset_id": audio_asset_id,
            }
        )
        self._record(
            "create_project",
            title=title,
            script=script,
            audio_asset_id=audio_asset_id,
        )
        return {
            "project": {"id": "fake-project"},
            "timeline": {"status": "candidate"},
        }

    def inspect(self, project_id: str) -> dict[str, object]:
        self._record("inspect", project_id=project_id)
        if project_id != "fake-project":
            self._missing("project", project_id)
        return {
            "project": {"id": project_id, "status": "timeline_candidate"},
            "timeline": {"status": "candidate"},
        }

    def confirm_timeline(self, project_id: str) -> dict[str, object]:
        if project_id != "fake-project":
            self._missing("project", project_id)
        self._record("confirm_timeline", project_id=project_id)
        return {
            "project": {"id": project_id, "status": "timeline_confirmed"},
            "timeline": {"status": "confirmed"},
        }

    def enqueue_render(
        self,
        project_id: str,
        *,
        max_attempts: int = 3,
        retry_of: str | None = None,
    ) -> dict[str, object]:
        if project_id != "fake-project":
            self._missing("project", project_id)
        self._record(
            "enqueue_render",
            project_id=project_id,
            max_attempts=max_attempts,
            retry_of=retry_of,
        )
        if self.runtime_failure:
            raise RuntimeError(f"unexpected render failure {self.error_detail}")
        if self.queue_full:
            raise self.queue_full_error(f"queue full {self.error_detail}")
        return {"job_id": "render-job", "state": "queued"}

    def enqueue_regeneration(
        self,
        project_id: str,
        *,
        base_run_id: str,
        scene_id: str,
        correction: str,
        retry_of: str | None = None,
    ) -> dict[str, object]:
        if project_id != "fake-project":
            self._missing("project", project_id)
        self._record(
            "enqueue_regeneration",
            project_id=project_id,
            base_run_id=base_run_id,
            scene_id=scene_id,
            correction=correction,
            retry_of=retry_of,
        )
        return {"job_id": "regeneration-job", "state": "queued"}

    def get_job(self, job_id: str) -> dict[str, object]:
        self._record("get_job", job_id=job_id)
        if job_id not in {"render-job", "regeneration-job"}:
            self._missing("job", job_id)
        return {"job_id": job_id, "state": "queued"}

    def checkout_revision(
        self,
        project_id: str,
        revision_id: str,
    ) -> dict[str, object]:
        if project_id != "fake-project":
            self._missing("project", project_id)
        if revision_id != "v001":
            self._missing("revision", revision_id)
        self._record(
            "checkout_revision",
            project_id=project_id,
            revision_id=revision_id,
        )
        return {"revision_id": revision_id}

    def accept_run(self, project_id: str, run_id: str) -> dict[str, object]:
        if project_id != "fake-project":
            self._missing("project", project_id)
        if run_id != "run-ready":
            self._missing("run", run_id)
        self._record("accept_run", project_id=project_id, run_id=run_id)
        return {"project": {"id": project_id}, "run_id": run_id}


@dataclass(frozen=True)
class _MutationCase:
    name: str
    path: str
    payload: dict[str, object]
    expected_status: int
    service_call: str


class _TokenFactory:
    def __init__(self, token: str) -> None:
        self.token = token
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        return self.token


def _load_http_contract() -> tuple[
    Callable[..., _HttpServer],
    Callable[..., object],
    type[ValueError],
]:
    """Import the HTTP seam only while a test is executing."""

    try:
        from video_pipeline.web import QueueFullError as public_queue_full_error
        from video_pipeline.web import create_server as public_create_server
        from video_pipeline.web import serve as public_serve
        from video_pipeline.web.server import create_server, serve
        from video_pipeline.web.service import QueueFullError
    except (ImportError, ModuleNotFoundError) as exc:  # pragma: no cover - intentional RED seam
        pytest.fail(f"{CONTRACT_MISSING}: {exc}", pytrace=False)

    if (
        public_create_server is not create_server
        or public_serve is not serve
        or public_queue_full_error is not QueueFullError
    ):
        pytest.fail(CONTRACT_MISSING, pytrace=False)
    if (
        not callable(create_server)
        or not callable(serve)
        or not isinstance(QueueFullError, type)
        or not issubclass(QueueFullError, ValueError)
    ):
        pytest.fail(CONTRACT_MISSING, pytrace=False)
    return create_server, serve, QueueFullError


@contextmanager
def _running_server(
    service: _FakeService,
    *,
    csrf_token_factory: Callable[[], str],
) -> Iterator[tuple[_HttpServer, int]]:
    create_server, _, _ = _load_http_contract()
    server = create_server(
        service,
        host="127.0.0.1",
        port=0,
        csrf_token_factory=csrf_token_factory,
    )
    thread = Thread(target=server.serve_forever, name="test-web-api", daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        assert host == "127.0.0.1"
        yield server, port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def _request(
    port: int,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> tuple[int, dict[str, str], bytes]:
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        response_headers = {
            name.lower(): value for name, value in response.getheaders()
        }
        return response.status, response_headers, response.read()
    finally:
        connection.close()


def _assert_no_cors(headers: dict[str, str]) -> None:
    assert "access-control-allow-origin" not in headers


def _assert_json_api(headers: dict[str, str]) -> None:
    assert headers.get("content-type", "").startswith("application/json")


def _assert_safe_error(body: bytes, *, token: str, tmp_path: Path) -> None:
    rendered = body.decode("utf-8", errors="replace")
    payload = json.loads(body)
    assert isinstance(payload, dict)
    assert token not in rendered
    assert str(tmp_path) not in rendered
    assert FILE_SENTINEL not in rendered
    assert "traceback" not in rendered.lower()


def _assert_safe_response(body: bytes, *, tmp_path: Path) -> None:
    rendered = body.decode("utf-8", errors="replace")
    assert str(tmp_path) not in rendered
    assert FILE_SENTINEL not in rendered
    assert "traceback" not in rendered.lower()


def _assert_root_relative(candidate: Path, root: Path) -> None:
    """Keep the asset guard equivalent to ``candidate.relative_to(root)``."""

    candidate.relative_to(root)


def _json_body(payload: dict[str, object]) -> bytes:
    return json.dumps(payload).encode("utf-8")


def _mutation_headers(port: int, token: str) -> dict[str, str]:
    host = f"127.0.0.1:{port}"
    return {
        "Host": host,
        "Origin": f"http://{host}",
        "Content-Type": "application/json",
        "X-CSRF-Token": token,
    }


def _assert_expected_status(status: int, expected: int) -> None:
    assert status == expected


def test_create_server_rejects_host_other_than_loopback() -> None:
    create_server, _, _ = _load_http_contract()

    with pytest.raises(ValueError):
        create_server(_FakeService(), host="0.0.0.0")


def test_session_returns_process_csrf_without_cors_and_requires_loopback_host(
    tmp_path: Path,
) -> None:
    token = "process-scoped-csrf-token"
    token_factory = _TokenFactory(token)
    service = _FakeService()

    with _running_server(service, csrf_token_factory=token_factory) as (_, port):
        loopback_host = f"127.0.0.1:{port}"
        status, headers, body = _request(
            port,
            "GET",
            "/api/session",
            headers={"Host": loopback_host},
        )
        assert status == 200
        _assert_no_cors(headers)
        _assert_json_api(headers)
        session = json.loads(body)
        assert session["csrf_token"] == token

        repeat_status, repeat_headers, repeat_body = _request(
            port,
            "GET",
            "/api/session",
            headers={"Host": loopback_host},
        )
        assert repeat_status == 200
        _assert_no_cors(repeat_headers)
        _assert_json_api(repeat_headers)
        assert json.loads(repeat_body)["csrf_token"] == token
        assert token_factory.calls == 1

        rejected_status, rejected_headers, rejected_body = _request(
            port,
            "GET",
            "/api/session",
            headers={"Host": "evil.example"},
        )
        assert rejected_status == 403
        _assert_no_cors(rejected_headers)
        _assert_json_api(rejected_headers)
        _assert_safe_error(rejected_body, token=token, tmp_path=tmp_path)


def test_project_mutation_requires_exact_same_origin_json_and_csrf(
    tmp_path: Path,
) -> None:
    token = "mutation-csrf-token"
    service = _FakeService()
    payload = json.dumps(
        {
            "title": "Projeto HTTP",
            "script": "# Abertura\nTexto.\n",
            "audio_asset_id": "audio-narration",
        }
    ).encode("utf-8")

    with _running_server(service, csrf_token_factory=_TokenFactory(token)) as (_, port):
        host = f"127.0.0.1:{port}"
        origin = f"http://{host}"
        common_headers = {
            "Host": host,
            "Content-Type": "application/json",
            "X-CSRF-Token": token,
        }

        bad_origin_status, bad_origin_headers, bad_origin_body = _request(
            port,
            "POST",
            "/api/projects",
            headers={**common_headers, "Origin": "http://evil.example"},
            body=payload,
        )
        assert bad_origin_status == 403
        _assert_no_cors(bad_origin_headers)
        _assert_json_api(bad_origin_headers)
        _assert_safe_error(bad_origin_body, token=token, tmp_path=tmp_path)
        assert service.create_calls == []

        missing_origin_status, missing_origin_headers, missing_origin_body = _request(
            port,
            "POST",
            "/api/projects",
            headers=common_headers,
            body=payload,
        )
        assert missing_origin_status == 403
        _assert_no_cors(missing_origin_headers)
        _assert_json_api(missing_origin_headers)
        _assert_safe_error(missing_origin_body, token=token, tmp_path=tmp_path)
        assert service.create_calls == []

        missing_csrf_headers = dict(common_headers)
        missing_csrf_headers.pop("X-CSRF-Token")
        missing_csrf_headers["Origin"] = origin
        missing_csrf_status, missing_csrf_response_headers, missing_csrf_body = _request(
            port,
            "POST",
            "/api/projects",
            headers=missing_csrf_headers,
            body=payload,
        )
        assert missing_csrf_status == 403
        _assert_no_cors(missing_csrf_response_headers)
        _assert_json_api(missing_csrf_response_headers)
        _assert_safe_error(missing_csrf_body, token=token, tmp_path=tmp_path)
        assert service.create_calls == []

        bad_token_status, bad_token_headers, bad_token_body = _request(
            port,
            "POST",
            "/api/projects",
            headers={**common_headers, "Origin": origin, "X-CSRF-Token": "wrong"},
            body=payload,
        )
        assert bad_token_status == 403
        _assert_no_cors(bad_token_headers)
        _assert_json_api(bad_token_headers)
        _assert_safe_error(bad_token_body, token=token, tmp_path=tmp_path)
        assert service.create_calls == []

        wrong_type_status, wrong_type_headers, wrong_type_body = _request(
            port,
            "POST",
            "/api/projects",
            headers={**common_headers, "Origin": origin, "Content-Type": "text/plain"},
            body=payload,
        )
        assert wrong_type_status == 415
        _assert_no_cors(wrong_type_headers)
        _assert_json_api(wrong_type_headers)
        _assert_safe_error(wrong_type_body, token=token, tmp_path=tmp_path)
        assert service.create_calls == []

        success_status, success_headers, success_body = _request(
            port,
            "POST",
            "/api/projects",
            headers={**common_headers, "Origin": origin},
            body=payload,
        )
        assert success_status == 201
        _assert_no_cors(success_headers)
        _assert_json_api(success_headers)
        assert json.loads(success_body)["project"]["id"] == "fake-project"
        assert service.create_calls == [
            {
                "title": "Projeto HTTP",
                "script": "# Abertura\nTexto.\n",
                "audio_asset_id": "audio-narration",
        }
    ]


def test_options_preflight_never_emits_cors_header(tmp_path: Path) -> None:
    token = "preflight-csrf-token"
    service = _FakeService(
        error_detail=f"{tmp_path / 'private.txt'} {FILE_SENTINEL} {token}"
    )

    with _running_server(service, csrf_token_factory=_TokenFactory(token)) as (_, port):
        host = f"127.0.0.1:{port}"
        status, headers, body = _request(
            port,
            "OPTIONS",
            "/api/projects",
            headers={
                "Host": host,
                "Origin": f"http://{host}",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert status == 405
        _assert_no_cors(headers)
        _assert_json_api(headers)
        _assert_safe_error(body, token=token, tmp_path=tmp_path)


def test_read_routes_return_service_documents_without_paths_or_cors(
    tmp_path: Path,
) -> None:
    token = "read-route-csrf-token"
    service = _FakeService(
        error_detail=f"{tmp_path / 'private.txt'} {FILE_SENTINEL} {token}"
    )

    with _running_server(service, csrf_token_factory=_TokenFactory(token)) as (_, port):
        read_cases = (
            (
                "/api/audio",
                [{"id": "audio-narration", "label": "Narration"}],
                "list_audio",
            ),
            (
                "/api/projects/fake-project",
                {
                    "project": {"id": "fake-project", "status": "timeline_candidate"},
                    "timeline": {"status": "candidate"},
                },
                "inspect",
            ),
            (
                "/api/jobs/render-job",
                {"job_id": "render-job", "state": "queued"},
                "get_job",
            ),
        )
        for path, expected_payload, expected_call in read_cases:
            status, headers, body = _request(
                port,
                "GET",
                path,
                headers={"Host": f"127.0.0.1:{port}"},
            )
            assert status == 200
            _assert_no_cors(headers)
            _assert_json_api(headers)
            _assert_safe_response(body, tmp_path=tmp_path)
            assert json.loads(body) == expected_payload
            assert service.calls[-1][0] == expected_call


def test_mutation_routes_delegate_public_service_methods_and_statuses(
    tmp_path: Path,
) -> None:
    token = "mutation-route-csrf-token"
    service = _FakeService(
        error_detail=f"{tmp_path / 'private.txt'} {FILE_SENTINEL} {token}"
    )
    cases = (
        _MutationCase(
            name="create project",
            path="/api/projects",
            payload={
                "title": "Projeto HTTP",
                "script": "# Abertura\nTexto.\n",
                "audio_asset_id": "audio-narration",
            },
            expected_status=201,
            service_call="create_project",
        ),
        _MutationCase(
            name="confirm timeline",
            path="/api/projects/fake-project/timeline/confirm",
            payload={},
            expected_status=200,
            service_call="confirm_timeline",
        ),
        _MutationCase(
            name="enqueue render",
            path="/api/projects/fake-project/render",
            payload={"max_attempts": 4, "retry_of": "retry-source"},
            expected_status=202,
            service_call="enqueue_render",
        ),
        _MutationCase(
            name="enqueue regeneration",
            path="/api/projects/fake-project/regenerate",
            payload={
                "base_run_id": "run-base",
                "scene_id": "abertura",
                "correction": "Aumente o contraste",
                "retry_of": "retry-source",
            },
            expected_status=202,
            service_call="enqueue_regeneration",
        ),
        _MutationCase(
            name="checkout revision",
            path="/api/projects/fake-project/checkout",
            payload={"revision_id": "v001"},
            expected_status=200,
            service_call="checkout_revision",
        ),
        _MutationCase(
            name="accept run",
            path="/api/projects/fake-project/accept",
            payload={"run_id": "run-ready"},
            expected_status=200,
            service_call="accept_run",
        ),
    )

    with _running_server(service, csrf_token_factory=_TokenFactory(token)) as (_, port):
        for case in cases:
            status, headers, body = _request(
                port,
                "POST",
                case.path,
                headers=_mutation_headers(port, token),
                body=_json_body(case.payload),
            )
            _assert_expected_status(status, case.expected_status)
            _assert_no_cors(headers)
            _assert_json_api(headers)
            _assert_safe_response(body, tmp_path=tmp_path)
            assert case.service_call in {name for name, _ in service.calls}

    assert [name for name, _ in service.calls] == [case.service_call for case in cases]
    render_call = next(
        values
        for name, values in service.calls
        if name == "enqueue_render"
    )
    assert render_call["retry_of"] == "retry-source"
    regeneration_call = next(
        values
        for name, values in service.calls
        if name == "enqueue_regeneration"
    )
    assert regeneration_call["retry_of"] == "retry-source"
    assert service.create_calls == [
        {
            "title": "Projeto HTTP",
            "script": "# Abertura\nTexto.\n",
            "audio_asset_id": "audio-narration",
        }
    ]


def test_mutation_errors_are_statused_and_sanitized(
    tmp_path: Path,
) -> None:
    token = "error-matrix-csrf-token"
    _, _, queue_full_error = _load_http_contract()
    service = _FakeService(
        error_detail=f"{tmp_path / 'private.txt'} {FILE_SENTINEL} {token}",
        queue_full_error=queue_full_error,
    )
    valid_render_body = _json_body({"max_attempts": 3})
    oversized_body = b"{" + b"x" * (1024 * 1024 + 1) + b"}"
    error_cases = (
        (
            "malformed JSON",
            "/api/projects",
            b"{\"title\":",
            400,
            False,
            False,
        ),
        (
            "oversized body",
            "/api/projects",
            oversized_body,
            413,
            False,
            False,
        ),
        (
            "invalid project ID",
            "/api/projects/bad%20id/timeline/confirm",
            _json_body({}),
            422,
            False,
            False,
        ),
        (
            "invalid input",
            "/api/projects",
            _json_body({"script": "# Abertura\nTexto.\n", "audio_asset_id": "audio-narration"}),
            422,
            False,
            False,
        ),
        (
            "overlong script",
            "/api/projects",
            _json_body(
                {
                    "title": "Projeto HTTP",
                    "script": "x" * 50_001,
                    "audio_asset_id": "audio-narration",
                }
            ),
            422,
            False,
            False,
        ),
        (
            "overlong correction",
            "/api/projects/fake-project/regenerate",
            _json_body(
                {
                    "base_run_id": "run-base",
                    "scene_id": "abertura",
                    "correction": "x" * 5_001,
                }
            ),
            422,
            False,
            False,
        ),
        (
            "queue full",
            "/api/projects/fake-project/render",
            valid_render_body,
            429,
            True,
            False,
        ),
        (
            "unexpected runtime failure",
            "/api/projects/fake-project/render",
            valid_render_body,
            500,
            False,
            True,
        ),
    )

    with _running_server(service, csrf_token_factory=_TokenFactory(token)) as (_, port):
        for name, path, body, expected_status, queue_full, runtime_failure in error_cases:
            service.queue_full = queue_full
            service.runtime_failure = runtime_failure
            status, headers, response_body = _request(
                port,
                "POST",
                path,
                headers=_mutation_headers(port, token),
                body=body,
            )
            assert status == expected_status, name
            _assert_no_cors(headers)
            _assert_json_api(headers)
            _assert_safe_error(response_body, token=token, tmp_path=tmp_path)

    assert [name for name, _ in service.calls] == ["enqueue_render", "enqueue_render"]


def test_unknown_ids_and_routes_return_not_found_without_leaks(
    tmp_path: Path,
) -> None:
    token = "not-found-csrf-token"
    service = _FakeService(
        error_detail=f"{tmp_path / 'private.txt'} {FILE_SENTINEL} {token}"
    )

    with _running_server(service, csrf_token_factory=_TokenFactory(token)) as (_, port):
        unknown_cases = (
            "/api/projects/unknown-project",
            "/api/jobs/unknown-job",
            "/api/not-a-route",
        )
        for path in unknown_cases:
            status, headers, body = _request(
                port,
                "GET",
                path,
                headers={"Host": f"127.0.0.1:{port}"},
            )
            assert status == 404
            _assert_no_cors(headers)
            _assert_json_api(headers)
            _assert_safe_error(body, token=token, tmp_path=tmp_path)

    assert [name for name, _ in service.calls] == ["inspect", "get_job"]


def test_assets_are_strictly_root_relative_and_support_full_single_range_and_head(
    tmp_path: Path,
) -> None:
    token = "asset-route-csrf-token"
    asset_root = tmp_path / "asset-root"
    asset_root.mkdir()
    asset_path = asset_root / "preview.mp4"
    asset_bytes = b"0123456789"
    asset_path.write_bytes(asset_bytes)
    outside_path = tmp_path / "outside.mp4"
    outside_path.write_bytes(FILE_SENTINEL.encode("ascii"))
    escape_path = asset_root / "escape.mp4"
    escape_path.symlink_to(outside_path)
    direct_escape_path = tmp_path / "direct-outside.mp4"
    direct_escape_path.write_bytes(FILE_SENTINEL.encode("ascii"))
    service = _FakeService(
        error_detail=f"{tmp_path / 'private.txt'} {FILE_SENTINEL} {token}",
        asset_root=asset_root,
        asset_paths={
            "asset-preview": asset_path,
            "asset-escape": escape_path,
            "asset-direct-escape": direct_escape_path,
        },
    )

    resolved_root, resolved_candidate = service.resolve_asset("asset-preview")
    _assert_root_relative(
        resolved_candidate.resolve(strict=True),
        resolved_root.resolve(strict=True),
    )
    escape_root, escape_candidate = service.resolve_asset("asset-escape")
    assert escape_root == asset_root
    with pytest.raises(ValueError):
        _assert_root_relative(
            escape_candidate.resolve(strict=True),
            escape_root.resolve(strict=True),
        )
    direct_root, direct_candidate = service.resolve_asset("asset-direct-escape")
    assert direct_root == asset_root
    with pytest.raises(ValueError):
        _assert_root_relative(
            direct_candidate.resolve(strict=True),
            direct_root.resolve(strict=True),
        )

    with _running_server(service, csrf_token_factory=_TokenFactory(token)) as (_, port):
        host = f"127.0.0.1:{port}"
        full_status, full_headers, full_body = _request(
            port,
            "GET",
            "/api/assets/asset-preview",
            headers={"Host": host},
        )
        assert full_status == 200
        _assert_no_cors(full_headers)
        assert full_headers["content-type"] == "video/mp4"
        assert full_headers["accept-ranges"] == "bytes"
        assert full_headers["content-length"] == str(len(asset_bytes))
        assert full_body == asset_bytes
        _assert_safe_response(full_body, tmp_path=tmp_path)

        range_cases = (
            ("bytes=2-5", 2, 5),
            ("bytes=2-", 2, 9),
            ("bytes=-4", 6, 9),
        )
        for range_value, start, end in range_cases:
            range_status, range_headers, range_body = _request(
                port,
                "GET",
                "/api/assets/asset-preview",
                headers={"Host": host, "Range": range_value},
            )
            assert range_status == 206
            _assert_no_cors(range_headers)
            assert range_headers["content-type"] == "video/mp4"
            assert range_headers["accept-ranges"] == "bytes"
            assert range_headers["content-range"] == f"bytes {start}-{end}/10"
            assert range_headers["content-length"] == str(end - start + 1)
            assert range_body == asset_bytes[start : end + 1]
            _assert_safe_response(range_body, tmp_path=tmp_path)

        head_status, head_headers, head_body = _request(
            port,
            "HEAD",
            "/api/assets/asset-preview",
            headers={"Host": host},
        )
        assert head_status == 200
        _assert_no_cors(head_headers)
        assert head_headers["content-type"] == "video/mp4"
        assert head_headers["accept-ranges"] == "bytes"
        assert head_headers["content-length"] == str(len(asset_bytes))
        assert head_body == b""

        head_range_status, head_range_headers, head_range_body = _request(
            port,
            "HEAD",
            "/api/assets/asset-preview",
            headers={"Host": host, "Range": "bytes=2-5"},
        )
        assert head_range_status == 206
        _assert_no_cors(head_range_headers)
        assert head_range_headers["content-type"] == "video/mp4"
        assert head_range_headers["accept-ranges"] == "bytes"
        assert head_range_headers["content-range"] == f"bytes 2-5/{len(asset_bytes)}"
        assert head_range_headers["content-length"] == "4"
        assert head_range_body == b""

        rejected_asset_cases = (
            "/api/assets/missing-asset",
            "/api/assets/../outside.mp4",
            "/api/assets/%2e%2e%2foutside.mp4",
            "/api/assets/asset-escape",
            "/api/assets/asset-direct-escape",
        )
        for path in rejected_asset_cases:
            status, headers, body = _request(
                port,
                "GET",
                path,
                headers={"Host": host},
            )
            assert status == 404
            _assert_no_cors(headers)
            _assert_json_api(headers)
            _assert_safe_error(body, token=token, tmp_path=tmp_path)

        invalid_range_cases = (
            "bytes=bad",
            "bytes=0-1,3-4",
            "bytes=999-1000",
        )
        for range_value in invalid_range_cases:
            status, headers, body = _request(
                port,
                "GET",
                "/api/assets/asset-preview",
                headers={"Host": host, "Range": range_value},
            )
            assert status == 416
            _assert_no_cors(headers)
            _assert_json_api(headers)
            assert headers["content-range"] == "bytes */10"
            _assert_safe_error(body, token=token, tmp_path=tmp_path)

    assert [name for name, _ in service.calls].count("resolve_asset") >= 11
    delegated_asset_ids = [
        values["asset_id"]
        for name, values in service.calls
        if name == "resolve_asset"
    ]
    assert all(isinstance(asset_id, str) for asset_id in delegated_asset_ids)
    assert all(
        "/" not in asset_id
        and "%2f" not in asset_id.lower()
        and ".." not in asset_id
        for asset_id in delegated_asset_ids
    )
    assert all(
        "path" not in values
        for name, values in service.calls
        if name == "resolve_asset"
    )


def test_root_static_is_package_owned_not_cwd_owned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / "index.html").write_text(STATIC_SENTINEL, encoding="utf-8")
    static_dir = cwd / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text(STATIC_SENTINEL, encoding="utf-8")
    monkeypatch.chdir(cwd)
    token = "static-route-csrf-token"
    service = _FakeService(
        error_detail=f"{tmp_path / 'private.txt'} {FILE_SENTINEL} {token}"
    )

    with _running_server(service, csrf_token_factory=_TokenFactory(token)) as (_, port):
        _, headers, body = _request(
            port,
            "GET",
            "/",
            headers={"Host": f"127.0.0.1:{port}"},
        )
        _assert_no_cors(headers)
        _assert_safe_response(body, tmp_path=tmp_path)
        assert STATIC_SENTINEL not in body.decode("utf-8", errors="replace")
        assert "frame-ancestors 'none'" in headers["content-security-policy"]


def test_cli_web_help_exposes_port_and_roots_without_host_option(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from video_pipeline.cli import main

    with pytest.raises(SystemExit) as raised:
        main(["web", "--help"])
    assert raised.value.code == 0
    help_text = capsys.readouterr().out
    for option in ("--port", "--projects-root", "--audio-root"):
        assert option in help_text
    for forbidden_option in ("--host", "--bind", "--static-root", "--static-dir"):
        assert forbidden_option not in help_text


def test_cli_web_defaults_reach_fixed_loopback_handler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from video_pipeline.cli import main

    observed: dict[str, object] = {}

    def fake_serve(service: object, *, host: str, port: int) -> None:
        observed.update(
            {
                "projects_root": service.projects_root,  # type: ignore[attr-defined]
                "audio_root": service.audio_root,  # type: ignore[attr-defined]
                "host": host,
                "port": port,
            }
        )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("video_pipeline.web.serve", fake_serve)

    assert main(["web", "--port", "8766"]) == 0
    assert observed == {
        "projects_root": (tmp_path / "projects").resolve(),
        "audio_root": (tmp_path / "audio").resolve(),
        "host": "127.0.0.1",
        "port": 8766,
    }
