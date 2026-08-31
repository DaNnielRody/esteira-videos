"""Loopback-only stdlib HTTP boundary for the canonical Web service."""

from __future__ import annotations

import json
import re
import secrets
import stat
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import unquote, urlsplit

from video_pipeline.revisions import RevisionManifest
from video_pipeline.web.limits import MAX_CORRECTION_CHARS, MAX_SCRIPT_CHARS
from video_pipeline.web.service import JobSnapshot, QueueFullError

_MAX_BODY = 1024 * 1024
_PUBLIC_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_STATIC_ROOT = Path(__file__).with_name("static")
_STATIC_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


class WebServiceProtocol(Protocol):
    def list_audio(self) -> object: ...

    def resolve_asset(self, asset_id: str) -> tuple[Path, Path]: ...

    def create_project(self, *, title: str, script: str, audio_asset_id: str) -> object: ...

    def inspect(self, project_id: str) -> object: ...

    def confirm_timeline(self, project_id: str) -> object: ...

    def enqueue_render(
        self,
        project_id: str,
        *,
        max_attempts: int = 3,
        retry_of: str | None = None,
    ) -> object: ...

    def enqueue_regeneration(
        self,
        project_id: str,
        *,
        base_run_id: str,
        scene_id: str,
        correction: str,
        retry_of: str | None = None,
    ) -> object: ...

    def get_job(self, job_id: str) -> object: ...

    def checkout_revision(self, project_id: str, revision_id: str) -> object: ...

    def accept_run(self, project_id: str, run_id: str) -> object: ...


class _WebServer(ThreadingHTTPServer):
    server_address: tuple[str, int]
    service: WebServiceProtocol
    csrf_token: str


def create_server(
    service: WebServiceProtocol,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    csrf_token_factory: Callable[[], str] | None = None,
) -> ThreadingHTTPServer:
    """Create a loopback HTTP server without starting its serving loop."""

    if host != "127.0.0.1":
        raise ValueError("web server must bind to IPv4 loopback")
    if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65_535:
        raise ValueError("port must be between 0 and 65535")
    server = _WebServer((host, port), _Handler)
    server.service = service
    factory = csrf_token_factory or (lambda: secrets.token_urlsafe(32))
    server.csrf_token = factory()
    if not server.csrf_token:
        server.server_close()
        raise ValueError("csrf token factory returned an empty token")
    return server


def serve(
    service: WebServiceProtocol,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Serve until interrupted, always closing the listening socket."""

    server = create_server(service, host=host, port=port)
    try:
        server.serve_forever()
    finally:
        server.server_close()


class _Handler(BaseHTTPRequestHandler):
    server: _WebServer
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_OPTIONS(self) -> None:  # noqa: N802
        if not self._valid_host():
            self._error(HTTPStatus.FORBIDDEN)
            return
        self._error(HTTPStatus.METHOD_NOT_ALLOWED)

    def do_GET(self) -> None:  # noqa: N802
        self._read(head_only=False)

    def do_HEAD(self) -> None:  # noqa: N802
        self._read(head_only=True)

    def do_POST(self) -> None:  # noqa: N802
        if not self._valid_host() or not self._valid_mutation_auth():
            self._error(HTTPStatus.FORBIDDEN)
            return
        if self.headers.get_content_type() != "application/json":
            self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return
        try:
            payload = self._read_json_object()
        except _RequestError as exc:
            self._error(exc.status)
            return
        path = unquote(urlsplit(self.path).path)
        try:
            status, result = self._dispatch_post(path, payload)
        except QueueFullError:
            self._error(HTTPStatus.TOO_MANY_REQUESTS)
            return
        except KeyError:
            self._error(HTTPStatus.NOT_FOUND)
            return
        except ValueError as exc:
            message = str(exc).lower()
            if "confirmed" in message or "only a ready" in message:
                self._error(HTTPStatus.CONFLICT)
            elif "unknown" in message or "not found" in message:
                self._error(HTTPStatus.NOT_FOUND)
            else:
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY)
            return
        except Exception:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self._json(status, result)

    def _read(self, *, head_only: bool) -> None:
        if not self._valid_host():
            self._error(HTTPStatus.FORBIDDEN, head_only=head_only)
            return
        raw_path = urlsplit(self.path).path
        path = unquote(raw_path)
        try:
            if path == "/api/session":
                self._json(
                    HTTPStatus.OK,
                    {"csrf_token": self.server.csrf_token},
                    head_only=head_only,
                )
                return
            if path == "/api/audio":
                self._json(
                    HTTPStatus.OK,
                    self.server.service.list_audio(),
                    head_only=head_only,
                )
                return
            if path.startswith("/api/assets/"):
                self._asset(raw_path, path, head_only=head_only)
                return
            parts = _route_parts(path)
            if len(parts) == 3 and parts[:2] == ["api", "projects"]:
                project_id = _required_id(parts[2])
                self._json(
                    HTTPStatus.OK,
                    self.server.service.inspect(project_id),
                    head_only=head_only,
                )
                return
            if len(parts) == 3 and parts[:2] == ["api", "jobs"]:
                job_id = _required_id(parts[2])
                self._json(
                    HTTPStatus.OK,
                    self.server.service.get_job(job_id),
                    head_only=head_only,
                )
                return
            static_asset = _STATIC_ASSETS.get(path)
            if static_asset is not None:
                self._static(static_asset, head_only=head_only)
                return
        except (KeyError, ValueError):
            self._error(HTTPStatus.NOT_FOUND, head_only=head_only)
            return
        except Exception:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, head_only=head_only)
            return
        self._error(HTTPStatus.NOT_FOUND, head_only=head_only)

    def _static(self, asset: tuple[str, str], *, head_only: bool) -> None:
        filename, content_type = asset
        candidate = _STATIC_ROOT / filename
        try:
            if not _is_package_static(candidate):
                raise ValueError("missing package asset")
            body = candidate.read_bytes()
        except (OSError, ValueError):
            self._error(HTTPStatus.NOT_FOUND, head_only=head_only)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; media-src 'self'; connect-src 'self'; "
            "img-src 'self'; style-src 'self'; script-src 'self'; "
            "frame-ancestors 'none'",
        )
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _dispatch_post(
        self,
        path: str,
        payload: dict[str, object],
    ) -> tuple[HTTPStatus, object]:
        if path == "/api/projects":
            title = _required_text(payload, "title")
            script = _required_text(payload, "script", allow_blank=False)
            audio_asset_id = _required_id(_required_text(payload, "audio_asset_id"))
            if len(script) > MAX_SCRIPT_CHARS:
                raise ValueError("script limit")
            return (
                HTTPStatus.CREATED,
                self.server.service.create_project(
                    title=title,
                    script=script,
                    audio_asset_id=audio_asset_id,
                ),
            )
        parts = _route_parts(path)
        if len(parts) < 4 or parts[:2] != ["api", "projects"]:
            raise KeyError("route")
        project_id = _required_id(parts[2])
        suffix = parts[3:]
        if suffix == ["timeline", "confirm"]:
            return HTTPStatus.OK, self.server.service.confirm_timeline(project_id)
        if suffix == ["render"]:
            max_attempts = payload.get("max_attempts", 3)
            if (
                not isinstance(max_attempts, int)
                or isinstance(max_attempts, bool)
                or max_attempts < 1
            ):
                raise ValueError("max attempts")
            retry_of = _optional_id(payload, "retry_of")
            return (
                HTTPStatus.ACCEPTED,
                self.server.service.enqueue_render(
                    project_id,
                    max_attempts=max_attempts,
                    retry_of=retry_of,
                ),
            )
        if suffix == ["regenerate"]:
            base_run_id = _required_id(_required_text(payload, "base_run_id"))
            scene_id = _required_id(_required_text(payload, "scene_id"))
            correction = _required_text(payload, "correction")
            if len(correction) > MAX_CORRECTION_CHARS:
                raise ValueError("correction limit")
            retry_of = _optional_id(payload, "retry_of")
            return (
                HTTPStatus.ACCEPTED,
                self.server.service.enqueue_regeneration(
                    project_id,
                    base_run_id=base_run_id,
                    scene_id=scene_id,
                    correction=correction,
                    retry_of=retry_of,
                ),
            )
        if suffix == ["checkout"]:
            revision_id = _required_id(_required_text(payload, "revision_id"))
            return (
                HTTPStatus.OK,
                self.server.service.checkout_revision(project_id, revision_id),
            )
        if suffix == ["accept"]:
            run_id = _required_id(_required_text(payload, "run_id"))
            return HTTPStatus.OK, self.server.service.accept_run(project_id, run_id)
        raise KeyError("route")

    def _asset(self, raw_path: str, decoded_path: str, *, head_only: bool) -> None:
        raw_id = raw_path.removeprefix("/api/assets/")
        asset_id = decoded_path.removeprefix("/api/assets/")
        if (
            not raw_id
            or "/" in asset_id
            or "\\" in asset_id
            or ".." in asset_id
            or _PUBLIC_ID.fullmatch(asset_id) is None
        ):
            self._error(HTTPStatus.NOT_FOUND, head_only=head_only)
            return
        try:
            raw_root, raw_candidate = self.server.service.resolve_asset(asset_id)
            if raw_root.is_symlink() or raw_candidate.is_symlink():
                raise ValueError("unsafe asset")
            root = raw_root.resolve(strict=True)
            candidate = raw_candidate.resolve(strict=True)
            candidate.relative_to(root)
            mode = candidate.lstat().st_mode
            if not stat.S_ISREG(mode):
                raise ValueError("asset must be a regular file")
            size = candidate.stat().st_size
        except (KeyError, OSError, ValueError):
            self._error(HTTPStatus.NOT_FOUND, head_only=head_only)
            return
        requested_range = self.headers.get("Range")
        if requested_range is None:
            self._file(
                candidate,
                start=0,
                end=max(0, size - 1),
                size=size,
                status=HTTPStatus.OK,
                head_only=head_only,
            )
            return
        byte_range = _parse_range(requested_range, size)
        if byte_range is None:
            self._error(
                HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE,
                head_only=head_only,
                extra_headers={"Content-Range": f"bytes */{size}"},
            )
            return
        start, end = byte_range
        self._file(
            candidate,
            start=start,
            end=end,
            size=size,
            status=HTTPStatus.PARTIAL_CONTENT,
            head_only=head_only,
        )

    def _file(
        self,
        path: Path,
        *,
        start: int,
        end: int,
        size: int,
        status: HTTPStatus,
        head_only: bool,
    ) -> None:
        length = 0 if size == 0 else end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if head_only or length == 0:
            return
        with path.open("rb") as asset:
            asset.seek(start)
            self.wfile.write(asset.read(length))

    def _valid_host(self) -> bool:
        host, port = self.server.server_address
        return self.headers.get("Host") == f"{host}:{port}"

    def _valid_mutation_auth(self) -> bool:
        host = self.headers.get("Host", "")
        return (
            self.headers.get("Origin") == f"http://{host}"
            and secrets.compare_digest(
                self.headers.get("X-CSRF-Token", ""),
                self.server.csrf_token,
            )
        )

    def _read_json_object(self) -> dict[str, object]:
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or "0")
        except ValueError as exc:
            raise _RequestError(HTTPStatus.BAD_REQUEST) from exc
        if length < 0:
            raise _RequestError(HTTPStatus.BAD_REQUEST)
        if length > _MAX_BODY:
            raise _RequestError(HTTPStatus.CONTENT_TOO_LARGE)
        try:
            value: object = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _RequestError(HTTPStatus.BAD_REQUEST) from exc
        if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
            raise _RequestError(HTTPStatus.BAD_REQUEST)
        return {str(key): item for key, item in value.items()}

    def _json(
        self,
        status: HTTPStatus,
        value: object,
        *,
        head_only: bool = False,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            default=_json_default,
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for name, header_value in (extra_headers or {}).items():
            self.send_header(name, header_value)
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _error(
        self,
        status: HTTPStatus,
        *,
        head_only: bool = False,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._json(
            status,
            {"error": _safe_error_label(status)},
            head_only=head_only,
            extra_headers=extra_headers,
        )


class _RequestError(ValueError):
    def __init__(self, status: HTTPStatus) -> None:
        super().__init__(status.phrase)
        self.status = status


def _route_parts(path: str) -> list[str]:
    return [part for part in path.split("/") if part]


def _required_id(value: str) -> str:
    if _PUBLIC_ID.fullmatch(value) is None:
        raise ValueError("invalid identifier")
    return value


def _required_text(
    document: dict[str, object],
    field: str,
    *,
    allow_blank: bool = False,
) -> str:
    value = document.get(field)
    if not isinstance(value, str) or (not allow_blank and not value.strip()):
        raise ValueError(f"invalid {field}")
    return value


def _optional_id(document: dict[str, object], field: str) -> str | None:
    value = document.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"invalid {field}")
    return _required_id(value)


def _parse_range(value: str, size: int) -> tuple[int, int] | None:
    if size <= 0 or not value.startswith("bytes=") or "," in value:
        return None
    spec = value.removeprefix("bytes=")
    if "-" not in spec:
        return None
    start_text, end_text = spec.split("-", 1)
    try:
        if not start_text:
            suffix = int(end_text)
            if suffix <= 0:
                return None
            start = max(0, size - suffix)
            return start, size - 1
        start = int(start_text)
        if start < 0 or start >= size:
            return None
        end = size - 1 if not end_text else int(end_text)
        if end < start:
            return None
        return start, min(end, size - 1)
    except ValueError:
        return None


def _json_default(value: object) -> object:
    if isinstance(value, JobSnapshot):
        return {
            "job_id": value.job_id,
            "project_id": value.project_id,
            "run_id": value.run_id,
            "state": value.state,
            "stage": value.stage,
            "revision_id": value.revision_id,
            "retry_of": value.retry_of,
            "error": value.error,
        }
    if isinstance(value, RevisionManifest):
        return value.to_document()
    raise TypeError("response is not JSON serializable")


def _safe_error_label(status: HTTPStatus) -> str:
    labels = {
        HTTPStatus.BAD_REQUEST: "bad request",
        HTTPStatus.FORBIDDEN: "forbidden",
        HTTPStatus.NOT_FOUND: "not found",
        HTTPStatus.METHOD_NOT_ALLOWED: "method not allowed",
        HTTPStatus.CONTENT_TOO_LARGE: "request too large",
        HTTPStatus.UNSUPPORTED_MEDIA_TYPE: "unsupported media type",
        HTTPStatus.UNPROCESSABLE_ENTITY: "invalid request",
        HTTPStatus.TOO_MANY_REQUESTS: "queue full",
        HTTPStatus.CONFLICT: "invalid state",
        HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE: "invalid range",
        HTTPStatus.INTERNAL_SERVER_ERROR: "internal server error",
    }
    return labels.get(status, "request failed")


def _is_package_static(path: Path) -> bool:
    try:
        root_mode = _STATIC_ROOT.lstat().st_mode
        path_mode = path.lstat().st_mode
        root = _STATIC_ROOT.resolve(strict=True)
        candidate = path.resolve(strict=True)
        candidate.relative_to(root)
    except (OSError, ValueError):
        return False
    return (
        stat.S_ISDIR(root_mode)
        and not stat.S_ISLNK(root_mode)
        and stat.S_ISREG(path_mode)
        and not stat.S_ISLNK(path_mode)
    )


__all__ = ["create_server", "serve"]
