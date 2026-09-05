"""HTTP asset streaming contracts for the loopback Web UI."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread
from typing import BinaryIO

import pytest

from video_pipeline.web.server import create_server
from video_pipeline.web.service import WebService

_CHUNK_SIZE = 64 * 1024


class _ReadRecorder:
    """Filesystem wrapper that records every read size without changing bytes."""

    def __init__(self, handle: BinaryIO, reads: list[int]) -> None:
        self._handle = handle
        self._reads = reads

    def __enter__(self) -> _ReadRecorder:
        self._handle.__enter__()
        return self

    def __exit__(self, *args: object) -> object:
        return self._handle.__exit__(*args)

    def seek(self, *args: object) -> object:
        return self._handle.seek(*args)

    def read(self, size: int = -1) -> bytes:
        self._reads.append(size)
        value = self._handle.read(size)
        assert isinstance(value, bytes)
        return value


@contextmanager
def _asset_server(
    tmp_path: Path,
    payload: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[int, list[list[int]], WebService]]:
    audio_root = tmp_path / "audio-catalog"
    audio_root.mkdir()
    (audio_root / "clip.mp4").write_bytes(payload)
    projects_root = tmp_path / "projects"
    service = WebService(projects_root=projects_root, audio_root=audio_root)
    reads: list[list[int]] = []
    original_open = Path.open

    def guarded_open(path: Path, *args: object, **kwargs: object) -> _ReadRecorder:
        handle = original_open(path, *args, **kwargs)
        request_reads: list[int] = []
        reads.append(request_reads)
        return _ReadRecorder(handle, request_reads)

    monkeypatch.setattr(Path, "open", guarded_open)
    server = create_server(
        service,
        host="127.0.0.1",
        port=0,
        csrf_token_factory=lambda: "asset-token",
    )
    thread = Thread(
        target=server.serve_forever,
        name="test-web-asset-streaming",
        daemon=True,
    )
    thread.start()
    try:
        host, port = server.server_address
        assert host == "127.0.0.1"
        yield port, reads, service
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
        service.close()


def _request(
    port: int,
    method: str,
    path: str,
    *,
    range_header: str | None = None,
) -> tuple[int, dict[str, str], bytes]:
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        headers = {"Host": f"127.0.0.1:{port}"}
        if range_header is not None:
            headers["Range"] = range_header
        connection.request(method, path, headers=headers)
        response = connection.getresponse()
        response_headers = {
            name.lower(): value for name, value in response.getheaders()
        }
        return response.status, response_headers, response.read()
    finally:
        connection.close()


def _payload(size: int) -> bytes:
    pattern = b"0123456789abcdef"
    return (pattern * ((size + len(pattern) - 1) // len(pattern)))[:size]


def test_get_asset_streams_full_file_in_bounded_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _payload(_CHUNK_SIZE * 3 + 17)

    with _asset_server(tmp_path, payload, monkeypatch) as (port, reads, _service):
        status, headers, body = _request(port, "GET", "/api/assets/audio-clip")

    assert status == 200
    assert headers["content-length"] == str(len(payload))
    assert body == payload
    assert reads
    assert reads[0]
    assert max(reads[0]) <= _CHUNK_SIZE


def test_range_asset_streams_exact_slice_in_bounded_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _payload(_CHUNK_SIZE * 4 + 31)
    start = 123
    end = start + _CHUNK_SIZE * 2 + 19

    with _asset_server(tmp_path, payload, monkeypatch) as (port, reads, _service):
        status, headers, body = _request(
            port,
            "GET",
            "/api/assets/audio-clip",
            range_header=f"bytes={start}-{end}",
        )

    assert status == 206
    assert headers["content-length"] == str(end - start + 1)
    assert headers["content-range"] == f"bytes {start}-{end}/{len(payload)}"
    assert body == payload[start : end + 1]
    assert reads
    assert reads[0]
    assert max(reads[0]) <= _CHUNK_SIZE


def test_head_asset_returns_headers_without_reading_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _payload(_CHUNK_SIZE * 2 + 1)

    with _asset_server(tmp_path, payload, monkeypatch) as (port, reads, _service):
        status, headers, body = _request(port, "HEAD", "/api/assets/audio-clip")

    assert status == 200
    assert headers["content-length"] == str(len(payload))
    assert body == b""
    assert reads == []
