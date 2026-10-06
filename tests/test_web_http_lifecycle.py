"""HTTP lifecycle and grammar contracts for the loopback Web UI."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from http import HTTPStatus
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread

import pytest

from video_pipeline.web.server import create_server


class _Service:
    """Small public service seam for HTTP routing and real asset files."""

    def __init__(self, root: Path, candidate: Path) -> None:
        self.root = root
        self.candidate = candidate

    def enqueue_render(
        self,
        project_id: str,
        *,
        max_attempts: int = 3,
        retry_of: str | None = None,
    ) -> object:
        del project_id, max_attempts, retry_of
        raise KeyError("unexpected service lookup failure")

    def resolve_asset(self, asset_id: str) -> tuple[Path, Path]:
        if asset_id != "clip":
            raise ValueError("unknown asset")
        return self.root, self.candidate


@contextmanager
def _server(service: _Service) -> Iterator[tuple[object, int]]:
    server = create_server(
        service,  # type: ignore[arg-type]
        host="127.0.0.1",
        port=0,
        csrf_token_factory=lambda: "lifecycle-token",
    )
    thread = Thread(
        target=server.serve_forever,
        name="test-web-http-lifecycle",
        daemon=True,
    )
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


def _post_headers(port: int, *, host: str | None = None) -> dict[str, str]:
    actual_host = host or f"127.0.0.1:{port}"
    return {
        "Host": actual_host,
        "Origin": f"http://{actual_host}",
        "Content-Type": "application/json",
        "X-CSRF-Token": "lifecycle-token",
    }


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


def _asset_service(tmp_path: Path) -> _Service:
    root = tmp_path / "assets"
    root.mkdir()
    candidate = root / "clip.mp4"
    candidate.write_bytes(b"0123456789abcdefghij")
    return _Service(root, candidate)


def test_unknown_post_route_is_404_without_swallowing_service_keyerror(
    tmp_path: Path,
) -> None:
    service = _asset_service(tmp_path)
    payload = b"{}"

    with _server(service) as (_, port):
        unknown_status, _, unknown_body = _request(
            port,
            "POST",
            "/api/projects/2026_demo/unknown-action",
            headers=_post_headers(port),
            body=payload,
        )
        internal_status, _, internal_body = _request(
            port,
            "POST",
            "/api/projects/2026_demo/render",
            headers=_post_headers(port),
            body=payload,
        )

    assert unknown_status == HTTPStatus.NOT_FOUND
    assert unknown_body == b'{"error":"not found"}'
    assert internal_status == HTTPStatus.INTERNAL_SERVER_ERROR
    assert internal_body == b'{"error":"internal server error"}'


@pytest.mark.parametrize(
    ("name", "headers", "body"),
    (
        (
            "invalid-host",
            {"Host": "evil.example", "Content-Type": "application/json"},
            b'{"body":"must not remain queued"}',
        ),
        (
            "invalid-csrf",
            {
                "Host": "{host}",
                "Origin": "http://{host}",
                "Content-Type": "application/json",
                "X-CSRF-Token": "wrong-token",
            },
            b'{"body":"must not remain queued"}',
        ),
        (
            "wrong-content-type",
            {
                "Host": "{host}",
                "Origin": "http://{host}",
                "Content-Type": "text/plain",
                "X-CSRF-Token": "lifecycle-token",
            },
            b'{"body":"must not remain queued"}',
        ),
        (
            "invalid-content-length",
            {
                "Host": "{host}",
                "Origin": "http://{host}",
                "Content-Type": "application/json",
                "X-CSRF-Token": "lifecycle-token",
                "Content-Length": "not-a-number",
            },
            b'{"body":"must not remain queued"}',
        ),
        (
            "truncated-json",
            {
                "Host": "{host}",
                "Origin": "http://{host}",
                "Content-Type": "application/json",
                "X-CSRF-Token": "lifecycle-token",
                "Content-Length": "2",
            },
            b'{"body":"must not remain queued"}',
        ),
    ),
)
def test_early_post_rejection_closes_connection_before_leftover_body(
    tmp_path: Path,
    name: str,
    headers: dict[str, str],
    body: bytes,
) -> None:
    del name
    service = _asset_service(tmp_path)

    with _server(service) as (_, port):
        rendered_headers = {
            key: value.format(host=f"127.0.0.1:{port}")
            for key, value in headers.items()
        }
        connection = HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            connection.request(
                "POST",
                "/api/projects",
                headers=rendered_headers,
                body=body,
            )
            response = connection.getresponse()
            response.read()
            socket = connection.sock
            assert socket is not None
            socket.settimeout(1)
            assert socket.recv(1) == b""
        finally:
            connection.close()


@pytest.mark.parametrize(
    "range_header",
    (
        "bytes=+1-3",
        "bytes=1-+3",
        "bytes=1_0-13",
    ),
)
def test_range_rejects_non_digit_byte_positions(
    tmp_path: Path,
    range_header: str,
) -> None:
    service = _asset_service(tmp_path)

    with _server(service) as (_, port):
        status, headers, body = _request(
            port,
            "GET",
            "/api/assets/clip",
            headers={
                "Host": f"127.0.0.1:{port}",
                "Range": range_header,
            },
        )

    assert status == HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE
    assert headers["content-range"] == "bytes */20"
    assert body == b'{"error":"invalid range"}'
