"""HTTP validation contracts for WebService project creation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread

import pytest

from video_pipeline.web.server import create_server
from video_pipeline.web.service import WebService


class _AudioProbe:
    """Return deterministic facts at the audio probing boundary."""

    def __init__(self, *, duration: float = 10.0) -> None:
        self.duration = duration

    def __call__(self, path: Path) -> dict[str, object]:
        content = path.read_bytes()
        return {
            "path": "audio/narration.wav",
            "hash": hashlib.sha256(content).hexdigest(),
            "container": "wav",
            "codec": "pcm_s16le",
            "stream": 0,
            "sample_rate": 48_000,
            "channels": 1,
            "duration": self.duration,
            "size": len(content),
            "probe_result": {
                "format": {"format_name": "wav", "duration": str(self.duration)},
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


class _FailingAudioProbe:
    """Model an unexpected probe defect without faking the service itself."""

    def __call__(self, path: Path) -> dict[str, object]:
        raise ValueError(f"unexpected probe failure at {path}")


class _SilenceDetector:
    """Do not cross the FFmpeg boundary in normal tests."""

    def __call__(self, path: Path) -> tuple[object, ...]:
        del path
        return ()


class _Server:
    def __init__(self, service: WebService, token: str) -> None:
        self.server = create_server(
            service,
            host="127.0.0.1",
            port=0,
            csrf_token_factory=lambda: token,
        )
        self.thread = Thread(
            target=self.server.serve_forever,
            name="test-web-project-validation",
            daemon=True,
        )

    def __enter__(self) -> int:
        self.thread.start()
        host, port = self.server.server_address
        assert host == "127.0.0.1"
        return port

    def __exit__(self, *_args: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        assert not self.thread.is_alive()


@contextmanager
def _service(tmp_path: Path, *, probe: object) -> Iterator[tuple[WebService, Path]]:
    audio_root = tmp_path / "audio-catalog"
    audio_root.mkdir()
    (audio_root / "narration.wav").write_bytes(b"fake narration")
    projects_root = tmp_path / "projects"
    service = WebService(
        projects_root=projects_root,
        audio_root=audio_root,
        audio_probe=probe,  # type: ignore[arg-type]
        silence_detector=_SilenceDetector(),
        project_id_factory=lambda: "2026_invalid",
    )
    try:
        yield service, projects_root
    finally:
        service.close()


def _post_project(port: int, token: str, script: str) -> tuple[int, bytes]:
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        host = f"127.0.0.1:{port}"
        body = json.dumps(
            {
                "title": "Projeto inválido",
                "script": script,
                "audio_asset_id": "audio-narration",
            }
        ).encode("utf-8")
        connection.request(
            "POST",
            "/api/projects",
            body=body,
            headers={
                "Host": host,
                "Origin": f"http://{host}",
                "Content-Type": "application/json",
                "X-CSRF-Token": token,
            },
        )
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("name", "script"),
    (
        (
            "missing-end",
            "# Abertura\n@start: 0\nNarração sem fim explícito.\n",
        ),
        (
            "unknown-capability",
            "# Abertura\n@start: 0\n@end: 10\n"
            "@capabilities: capability_that_does_not_exist\n"
            "Narração com capacidade inválida.\n",
        ),
        (
            "inconsistent-timeline",
            "# Abertura\n@start: 0\n@end: 9\n"
            "Narração que não cobre a duração do áudio.\n",
        ),
    ),
)
def test_create_project_rejects_domain_script_validation_at_http_boundary(
    tmp_path: Path,
    name: str,
    script: str,
) -> None:
    del name
    token = "validation-token"
    with _service(tmp_path, probe=_AudioProbe()) as (service, projects_root):
        with _Server(service, token) as port:
            status, body = _post_project(port, token, script)

    assert status == 422
    assert json.loads(body) == {"error": "invalid request"}
    rendered = body.decode("utf-8")
    assert str(tmp_path) not in rendered
    assert "traceback" not in rendered.lower()
    assert not (projects_root / "2026_invalid").exists()
    assert not any(projects_root.rglob("*")) if projects_root.exists() else True


def test_create_project_keeps_unexpected_probe_value_error_as_http_500(
    tmp_path: Path,
) -> None:
    token = "internal-error-token"
    with _service(tmp_path, probe=_FailingAudioProbe()) as (service, projects_root):
        with _Server(service, token) as port:
            status, body = _post_project(
                port,
                token,
                "# Abertura\nNarração válida para o probe.\n",
            )

    assert status == 500
    assert json.loads(body) == {"error": "internal server error"}
    rendered = body.decode("utf-8")
    assert str(tmp_path) not in rendered
    assert "traceback" not in rendered.lower()
    assert not (projects_root / "2026_invalid").exists()
