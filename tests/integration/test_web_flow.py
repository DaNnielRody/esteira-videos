"""RED integration contract for the first canonical Web UI flow slice."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.client import HTTPConnection
from pathlib import Path
from threading import Event, Thread
from time import monotonic
from typing import Protocol

import pytest

CONTRACT_MISSING = "WEB_CANONICAL_SERVICE_CONTRACT_MISSING"


class _HttpServer(Protocol):
    server_address: tuple[str, int]

    def serve_forever(self) -> None:
        ...

    def shutdown(self) -> None:
        ...

    def server_close(self) -> None:
        ...


def _load_web_contract() -> tuple[
    type[object],
    type[object],
    Callable[..., _HttpServer],
]:
    """Load the future public web seam only after the behavioral test starts."""

    try:
        from video_pipeline.web import (
            ServiceLimits as PublicServiceLimits,
        )
        from video_pipeline.web import (
            WebService as PublicWebService,
        )
        from video_pipeline.web import create_server as public_create_server
        from video_pipeline.web.server import create_server
        from video_pipeline.web.service import ServiceLimits, WebService
    except (ImportError, ModuleNotFoundError) as exc:  # pragma: no cover - intentional RED seam
        pytest.fail(f"{CONTRACT_MISSING}: {exc}", pytrace=False)

    required_methods = (
        "list_audio",
        "create_project",
        "inspect",
        "confirm_timeline",
        "enqueue_render",
        "enqueue_regeneration",
        "get_job",
        "wait_job",
        "checkout_revision",
        "accept_run",
        "close",
    )
    if (
        not isinstance(PublicWebService, type)
        or not isinstance(PublicServiceLimits, type)
        or PublicWebService is not WebService
        or PublicServiceLimits is not ServiceLimits
        or public_create_server is not create_server
        or not callable(create_server)
        or any(not callable(getattr(WebService, name, None)) for name in required_methods)
    ):
        pytest.fail(CONTRACT_MISSING, pytrace=False)
    return PublicWebService, PublicServiceLimits, create_server


@contextmanager
def _running_server(service: object, *, csrf_token: str) -> Iterator[int]:
    _, _, create_server = _load_web_contract()
    server = create_server(
        service,
        host="127.0.0.1",
        port=0,
        csrf_token_factory=lambda: csrf_token,
    )
    thread = Thread(target=server.serve_forever, name="test-web-flow", daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        assert host == "127.0.0.1"
        yield port
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


def _json_body(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _mutation_headers(port: int, token: str) -> dict[str, str]:
    host = f"127.0.0.1:{port}"
    return {
        "Host": host,
        "Origin": f"http://{host}",
        "Content-Type": "application/json",
        "X-CSRF-Token": token,
    }


def _safe_json(
    body: bytes,
    headers: dict[str, str],
    *,
    tmp_path: Path,
) -> object:
    rendered = body.decode("utf-8", errors="replace")
    assert headers.get("content-type", "").startswith("application/json")
    assert str(tmp_path) not in rendered
    assert "traceback" not in rendered.lower()
    return json.loads(body)


def _safe_bytes(body: bytes, *, tmp_path: Path) -> None:
    assert str(tmp_path).encode() not in body


def _snapshot_files(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _json_document(path: Path) -> dict[str, object]:
    document = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _poll_job_success(
    port: int,
    job_id: str,
    *,
    tmp_path: Path,
    deadline_seconds: float = 15.0,
) -> dict[str, object]:
    deadline = monotonic() + deadline_seconds
    poll_gate = Event()
    while True:
        status, headers, body = _request(
            port,
            "GET",
            f"/api/jobs/{job_id}",
            headers={"Host": f"127.0.0.1:{port}"},
        )
        assert status == 200
        payload = _safe_json(body, headers, tmp_path=tmp_path)
        assert isinstance(payload, dict)
        state = payload.get("state")
        if state == "success":
            return payload
        if state == "failure":
            pytest.fail("web render job reached failure")
        remaining = deadline - monotonic()
        if remaining <= 0:
            pytest.fail("web render job did not reach success before deadline")
        poll_gate.wait(timeout=min(0.05, remaining))


@pytest.mark.integration
def test_real_web_flow_covers_render_revision_checkout_accept_and_preview(
    tmp_path: Path,
) -> None:
    web_service_type, service_limits_type, _ = _load_web_contract()
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
    from tests.test_web_service import (
        EmptySilenceDetector,
        FakeAudioProbe,
        _make_audio_root,
    )
    from video_pipeline.project import Project
    from video_pipeline.revisions import RevisionStore
    from video_pipeline.timeline import Timeline
    from video_pipeline.video import VideoPipeline

    audio_root, _ = _make_audio_root(tmp_path)
    projects_root = tmp_path / "projects"
    project_id = "2026_web_flow"
    project_root = projects_root / project_id
    project_json = project_root / "project.json"
    run_ids = iter(("run1", "run2"))
    job_ids = iter(("job1", "job2"))
    pipelines: list[tuple[FakeProvider, FakeManimRunner]] = []

    def pipeline_factory(run_id: str) -> VideoPipeline:
        provider = FakeProvider(project_json)
        runner = FakeManimRunner()
        normalized_validator = FakeNormalizedValidator()
        pipelines.append((provider, runner))
        return VideoPipeline(
            provider=provider,
            runner=runner,
            validator=FakeRawValidator(),
            observer=FakeObserver(),
            temporal_normalizer=FakeTemporalNormalizer(normalized_validator),
            normalized_validator=normalized_validator,
            final_validator=FakeFinalValidator(),
            composer=FakeComposer(),
            output_root=project_root / "artifacts",
            id_factory=lambda: run_id,
        )

    service = web_service_type(
        projects_root=projects_root,
        audio_root=audio_root,
        audio_probe=FakeAudioProbe(),
        silence_detector=EmptySilenceDetector(),
        project_id_factory=lambda: project_id,
        job_id_factory=lambda: next(job_ids),
        run_id_factory=lambda: next(run_ids),
        pipeline_factory=pipeline_factory,
        limits=service_limits_type(),
    )
    csrf_token = "web-flow-csrf-token"
    script = (
        "# Abertura\n"
        "@objective: Introduza vetores.\n"
        "@capabilities: basic_geometry\n"
        "Uma abertura determinística.\n\n"
        "## Explicacao\n"
        "@objective: Explique a soma.\n"
        "@capabilities: basic_geometry\n"
        "Uma explicação determinística.\n"
    )

    try:
        with _running_server(service, csrf_token=csrf_token) as port:
            host_headers = {"Host": f"127.0.0.1:{port}"}
            session_status, session_headers, session_body = _request(
                port,
                "GET",
                "/api/session",
                headers=host_headers,
            )
            assert session_status == 200
            session_payload = _safe_json(
                session_body,
                session_headers,
                tmp_path=tmp_path,
            )
            assert isinstance(session_payload, dict)
            token = session_payload.get("csrf_token")
            assert token == csrf_token
            assert isinstance(token, str)

            audio_status, audio_headers, audio_body = _request(
                port,
                "GET",
                "/api/audio",
                headers=host_headers,
            )
            assert audio_status == 200
            audio_payload = _safe_json(audio_body, audio_headers, tmp_path=tmp_path)
            assert isinstance(audio_payload, list)
            assert len(audio_payload) == 1
            audio_asset = audio_payload[0]
            assert isinstance(audio_asset, dict)
            audio_asset_id = audio_asset.get("id")
            assert audio_asset_id == "audio-narration"
            assert isinstance(audio_asset_id, str)
            assert not Path(audio_asset_id).is_absolute()
            assert ".." not in Path(audio_asset_id).parts
            assert "path" not in audio_asset

            create_status, create_headers, create_body = _request(
                port,
                "POST",
                "/api/projects",
                headers=_mutation_headers(port, token),
                body=_json_body(
                    {
                        "title": "Projeto Web Flow",
                        "script": script,
                        "audio_asset_id": audio_asset_id,
                    }
                ),
            )
            assert create_status == 201
            create_payload = _safe_json(
                create_body,
                create_headers,
                tmp_path=tmp_path,
            )
            assert isinstance(create_payload, dict)
            created_project = create_payload.get("project")
            created_timeline = create_payload.get("timeline")
            assert isinstance(created_project, dict)
            assert isinstance(created_timeline, dict)
            assert created_project.get("id") == project_id
            assert created_timeline.get("status") == "candidate"

            project_model = Project.model_validate_json(
                (project_root / "project.json").read_text(encoding="utf-8")
            )
            timeline_model = Timeline.model_validate_json(
                (project_root / "timeline.json").read_text(encoding="utf-8")
            )
            assert project_model.id == project_id
            assert project_model.status.value == "timeline_candidate"
            assert timeline_model.status == "candidate"
            assert len(timeline_model.segments) == 2

            preconfirm_status, preconfirm_headers, preconfirm_body = _request(
                port,
                "POST",
                f"/api/projects/{project_id}/render",
                headers=_mutation_headers(port, token),
                body=_json_body({"max_attempts": 1}),
            )
            assert preconfirm_status == 409
            _safe_json(preconfirm_body, preconfirm_headers, tmp_path=tmp_path)
            assert pipelines == []
            assert not (project_root / "golden").exists()

            confirm_status, confirm_headers, confirm_body = _request(
                port,
                "POST",
                f"/api/projects/{project_id}/timeline/confirm",
                headers=_mutation_headers(port, token),
                body=_json_body({}),
            )
            assert confirm_status == 200
            confirm_payload = _safe_json(
                confirm_body,
                confirm_headers,
                tmp_path=tmp_path,
            )
            assert isinstance(confirm_payload, dict)
            confirmed_timeline = confirm_payload.get("timeline")
            assert isinstance(confirmed_timeline, dict)
            assert confirmed_timeline.get("status") == "confirmed"
            confirmed_project = Project.model_validate_json(
                project_json.read_text(encoding="utf-8")
            )
            confirmed_timeline_model = Timeline.model_validate_json(
                (project_root / "timeline.json").read_text(encoding="utf-8")
            )
            assert confirmed_project.status.value == "timeline_confirmed"
            assert confirmed_timeline_model.status == "confirmed"

            initial_status, initial_headers, initial_body = _request(
                port,
                "POST",
                f"/api/projects/{project_id}/render",
                headers=_mutation_headers(port, token),
                body=_json_body({"max_attempts": 1}),
            )
            assert initial_status == 202
            initial_payload = _safe_json(
                initial_body,
                initial_headers,
                tmp_path=tmp_path,
            )
            assert isinstance(initial_payload, dict)
            initial_job_id = initial_payload.get("job_id")
            assert initial_job_id == "job1"
            assert isinstance(initial_job_id, str)

            initial_job = _poll_job_success(
                port,
                initial_job_id,
                tmp_path=tmp_path,
            )
            assert initial_job.get("revision_id") == "v001"
            assert pipelines
            first_provider, first_runner = pipelines[0]
            expected_scene_count = len(confirmed_project.scenes)
            assert expected_scene_count == 2
            assert len(first_provider.requests) == expected_scene_count
            assert len(first_runner.scene_paths) == expected_scene_count
            assert [request.scene_name for request in first_provider.requests] == [
                "AberturaScene",
                "ExplicacaoScene",
            ]

            run1_root = project_root / "artifacts" / "run1"
            assert (run1_root / "final.mp4").is_file()
            assert (run1_root / "final.mp4").stat().st_size > 0
            assert not (project_root / "golden").exists()

            revision_store = RevisionStore(project_root)
            assert revision_store.load_index().current_revision_id == "v001"
            revision_root = project_root / "ui" / "revisions"
            revision_v001 = _json_document(revision_root / "v001.json")
            assert revision_v001["run_id"] == "run1"
            assert revision_v001["asset_ids"]
            assert "final" in revision_v001["asset_ids"]
            run1_snapshot_before_regeneration = _snapshot_files(run1_root)

            inspect_status, inspect_headers, inspect_body = _request(
                port,
                "GET",
                f"/api/projects/{project_id}",
                headers={"Host": f"127.0.0.1:{port}"},
            )
            assert inspect_status == 200
            inspected = _safe_json(inspect_body, inspect_headers, tmp_path=tmp_path)
            assert isinstance(inspected, dict)
            ui = inspected.get("ui")
            assert isinstance(ui, dict)
            media = ui.get("media")
            assert isinstance(media, dict)
            final_asset_id = media.get("final_asset_id")
            assert isinstance(final_asset_id, str) and final_asset_id.startswith("media-")
            final_asset_path = f"/api/assets/{final_asset_id}"

            final_status, final_headers, final_body = _request(
                port,
                "GET",
                final_asset_path,
                headers={"Host": f"127.0.0.1:{port}"},
            )
            assert final_status == 200
            assert final_headers["content-type"] == "video/mp4"
            assert final_headers["accept-ranges"] == "bytes"
            assert final_headers["content-length"] == str(len(final_body))
            assert final_body == b"fake-final-mp4"
            _safe_bytes(final_body, tmp_path=tmp_path)

            range_status, range_headers, range_body = _request(
                port,
                "GET",
                final_asset_path,
                headers={
                    "Host": f"127.0.0.1:{port}",
                    "Range": "bytes=0-3",
                },
            )
            assert range_status == 206
            assert range_headers["content-type"] == "video/mp4"
            assert range_headers["accept-ranges"] == "bytes"
            assert range_headers["content-range"] == f"bytes 0-3/{len(final_body)}"
            assert range_headers["content-length"] == "4"
            assert range_body == final_body[:4]
            _safe_bytes(range_body, tmp_path=tmp_path)

            correction = "Aumente a espessura da seta azul"
            regenerate_status, regenerate_headers, regenerate_body = _request(
                port,
                "POST",
                f"/api/projects/{project_id}/regenerate",
                headers=_mutation_headers(port, token),
                body=_json_body(
                    {
                        "base_run_id": "run1",
                        "scene_id": "abertura",
                        "correction": correction,
                    }
                ),
            )
            assert regenerate_status == 202
            regenerate_payload = _safe_json(
                regenerate_body,
                regenerate_headers,
                tmp_path=tmp_path,
            )
            assert isinstance(regenerate_payload, dict)
            regenerate_job_id = regenerate_payload.get("job_id")
            assert regenerate_job_id == "job2"
            assert isinstance(regenerate_job_id, str)

            regenerated_job = _poll_job_success(
                port,
                regenerate_job_id,
                tmp_path=tmp_path,
            )
            assert regenerated_job.get("revision_id") == "v002"
            assert len(pipelines) == 2
            second_provider, second_runner = pipelines[1]
            assert len(second_provider.requests) == 1
            assert len(second_runner.scene_paths) == 1
            assert second_provider.requests[0].scene_name == "AberturaScene"
            assert correction in second_provider.requests[0].description
            assert "abertura" in second_runner.scene_paths[0].parts

            run2_root = project_root / "artifacts" / "run2"
            assert (run2_root / "final.mp4").is_file()
            assert (run2_root / "final.mp4").stat().st_size > 0
            run2_document = _json_document(run2_root / "run.json")
            assert run2_document["base_run_id"] == "run1"
            assert run2_document["selected_scene_id"] == "abertura"
            assert run2_document["correction"] == correction
            assert _snapshot_files(run1_root) == run1_snapshot_before_regeneration

            scene_refs = {scene.id: scene for scene in confirmed_project.scenes}
            sibling_ref = scene_refs["explicacao"]
            base_sibling_root = run1_root / sibling_ref.path
            new_sibling_root = run2_root / sibling_ref.path
            assert _snapshot_files(new_sibling_root) == _snapshot_files(base_sibling_root)
            sibling_provenance = _json_document(new_sibling_root / "code-provenance.json")
            assert sibling_provenance["run_id"] == "run1"
            run1_document = _json_document(run1_root / "run.json")
            run1_sibling_record = next(
                scene for scene in run1_document["scenes"] if scene["id"] == "explicacao"
            )
            run2_sibling_record = next(
                scene for scene in run2_document["scenes"] if scene["id"] == "explicacao"
            )
            for hash_key in (
                "raw_sha256",
                "normalized_sha256",
                "normalization_sha256",
                "code_sha256",
                "provenance_sha256",
                "diagnostics_sha256",
                "observation_sha256",
                "quality_sha256",
            ):
                assert run2_sibling_record[hash_key] == run1_sibling_record[hash_key]

            revision_v002 = _json_document(revision_root / "v002.json")
            assert revision_v002["run_id"] == "run2"
            assert revision_v002["parent_revision_id"] == "v001"
            assert "final" in revision_v002["asset_ids"]
            assert revision_store.load_index().current_revision_id == "v002"
            assert not (project_root / "golden").exists()

            revision_files_before_checkout = _snapshot_files(revision_root)
            project_before_checkout = project_json.read_bytes()
            timeline_before_checkout = (project_root / "timeline.json").read_bytes()
            run1_before_checkout = _snapshot_files(run1_root)
            run2_before_checkout = _snapshot_files(run2_root)
            checkout_status, checkout_headers, checkout_body = _request(
                port,
                "POST",
                f"/api/projects/{project_id}/checkout",
                headers=_mutation_headers(port, token),
                body=_json_body({"revision_id": "v001"}),
            )
            assert checkout_status == 200
            checkout_payload = _safe_json(
                checkout_body,
                checkout_headers,
                tmp_path=tmp_path,
            )
            assert isinstance(checkout_payload, dict)
            assert checkout_payload.get("revision_id") == "v001"
            assert revision_store.load_index().current_revision_id == "v001"
            assert _snapshot_files(revision_root) == revision_files_before_checkout
            assert project_json.read_bytes() == project_before_checkout
            assert (project_root / "timeline.json").read_bytes() == timeline_before_checkout
            assert _snapshot_files(run1_root) == run1_before_checkout
            assert _snapshot_files(run2_root) == run2_before_checkout
            assert not (project_root / "golden").exists()

            accept_status, accept_headers, accept_body = _request(
                port,
                "POST",
                f"/api/projects/{project_id}/accept",
                headers=_mutation_headers(port, token),
                body=_json_body({"run_id": "run2"}),
            )
            assert accept_status == 200
            accept_payload = _safe_json(
                accept_body,
                accept_headers,
                tmp_path=tmp_path,
            )
            assert isinstance(accept_payload, dict)
            assert accept_payload.get("run_id") == "run2"
            accepted_project = Project.model_validate_json(
                project_json.read_text(encoding="utf-8")
            )
            assert accepted_project.status.value == "accepted"
            assert accepted_project.current_run == "run2"
            assert accepted_project.accepted_run == "run2"
            golden_manifest = _json_document(project_root / "golden" / "manifest.json")
            assert golden_manifest["run_id"] == "run2"
            assert revision_store.load_index().current_revision_id == "v001"
            assert _snapshot_files(revision_root) == revision_files_before_checkout
            assert _snapshot_files(run1_root) == run1_before_checkout
            assert _snapshot_files(run2_root) == run2_before_checkout
    finally:
        service.close()
