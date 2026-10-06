"""Smoke coverage for the public Web UI startup command."""

from __future__ import annotations

from pathlib import Path

import pytest


class _InterruptingServer:
    server_address = ("127.0.0.1", 8766)

    def __init__(self) -> None:
        self.closed = False

    def serve_forever(self) -> None:
        raise KeyboardInterrupt

    def server_close(self) -> None:
        self.closed = True


def test_serve_announces_only_the_bound_local_url_and_closes_on_interrupt(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import video_pipeline.web.server as server_module

    fake_server = _InterruptingServer()

    def fake_create_server(
        _service: object,
        *,
        host: str,
        port: int,
    ) -> _InterruptingServer:
        assert host == "127.0.0.1"
        assert port == 0
        return fake_server

    monkeypatch.setattr(server_module, "create_server", fake_create_server)
    with pytest.raises(KeyboardInterrupt):
        server_module.serve(object(), host="127.0.0.1", port=0)

    assert capsys.readouterr().out == "http://127.0.0.1:8766/\n"
    assert fake_server.closed


def test_cli_web_handles_ctrl_c_without_error_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from video_pipeline.cli import main

    def interrupting_serve(_service: object, *, host: str, port: int) -> None:
        assert host == "127.0.0.1"
        assert port == 8766
        raise KeyboardInterrupt

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("video_pipeline.web.serve", interrupting_serve)

    assert main(["web", "--port", "8766"]) == 0
    assert capsys.readouterr().out == ""


def test_web_can_use_authored_candidates_through_the_real_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from video_pipeline.cli import main
    from video_pipeline.provider import ProviderRequest

    source = tmp_path / "candidates" / "ExampleScene" / "scene.py"
    source.parent.mkdir(parents=True)
    source.write_text("class ExampleScene: pass\n")

    def inspect_serve(service: object, *, host: str, port: int) -> None:
        pipeline = service.pipeline_factory("authored-run")
        result = pipeline.provider.generate(
            ProviderRequest(scene_name="ExampleScene", description="example")
        )
        assert result.code == source.read_text()
        assert result.raw_response["provider"] == "authored_source"

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("video_pipeline.web.serve", inspect_serve)
    assert main(["web", "--authored-sources", str(source.parent.parent)]) == 0
