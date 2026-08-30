"""Behavioral contract tests for the replaceable Ollama provider boundary."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.request import Request

import pytest

try:
    from video_pipeline.provider import (
        LLMProvider,
        OllamaProvider,
        ProviderError,
        ProviderRequest,
        ProviderResponse,
        UnloadResult,
    )
except (ImportError, ModuleNotFoundError) as exc:  # pragma: no cover - RED shim
    _CONTRACT_IMPORT_ERROR = exc
    LLMProvider = object  # type: ignore[assignment,misc]
    OllamaProvider = None  # type: ignore[assignment]
    ProviderRequest = object  # type: ignore[assignment,misc]
    ProviderResponse = object  # type: ignore[assignment,misc]
    UnloadResult = object  # type: ignore[assignment,misc]
else:
    _CONTRACT_IMPORT_ERROR = None


def _require_contract() -> None:
    if _CONTRACT_IMPORT_ERROR is not None:
        pytest.fail("SCENE_PROVIDER_CONTRACT_MISSING: provider public seam unavailable")


@dataclass
class ReplaceableProvider(LLMProvider):
    """A boundary fake proving consumers depend on the provider interface."""

    response: ProviderResponse
    calls: list[ProviderRequest]

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        return self.response

    def unload(self) -> UnloadResult:
        return UnloadResult(ok=True, raw_response={"unloaded": True})


def _request(**overrides: object) -> ProviderRequest:
    values: dict[str, object] = {
        "scene_name": "AcceptanceScene",
        "description": "Draw a circle, transform it into a square, and move it right.",
    }
    values.update(overrides)
    return ProviderRequest(**values)


def _response() -> ProviderResponse:
    return ProviderResponse(
        code="from manim import Scene\n\nclass AcceptanceScene(Scene):\n    pass\n",
        raw_response={"response": "```python\nfrom manim import Scene\n```"},
    )


def test_ollama_provider_rejects_non_local_endpoints() -> None:
    with pytest.raises(ValueError, match="local loopback"):
        OllamaProvider(base_url="https://ollama.example.com")


def test_llm_provider_is_replaceable_through_generate_and_unload() -> None:
    _require_contract()
    response = _response()
    provider = ReplaceableProvider(response=response, calls=[])
    request = _request()

    assert provider.generate(request) is response
    assert provider.calls == [request]
    assert provider.unload().ok is True


class FakeHTTPResponse:
    def __init__(self, body: object) -> None:
        self._body = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")

    def __enter__(self) -> "FakeHTTPResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class RecordingOpener:
    """Operation-specific fake for the local Ollama HTTP boundary."""

    def __init__(self, responses: list[object]) -> None:
        self.responses = iter(responses)
        self.requests: list[tuple[Request, float]] = []

    def __call__(self, request: Request, timeout: float) -> FakeHTTPResponse:
        self.requests.append((request, timeout))
        return FakeHTTPResponse(next(self.responses))


class HTTPFailureOpener:
    """Boundary fake that exposes an Ollama HTTP failure to the adapter."""

    def __call__(self, _request: Request, timeout: float) -> FakeHTTPResponse:
        assert timeout > 0
        raise HTTPError(
            url="http://localhost:11434/api/generate",
            code=503,
            msg="service unavailable",
            hdrs=None,
            fp=None,
        )


class TimeoutOpener:
    """Boundary fake that exposes a request timeout to the adapter."""

    def __call__(self, _request: Request, timeout: float) -> FakeHTTPResponse:
        assert timeout > 0
        raise TimeoutError("ollama request timed out")


def _request_body(request: Request) -> Mapping[str, object]:
    body = request.data
    if isinstance(body, bytes):
        body = body.decode("utf-8")
    return json.loads(body)


def test_ollama_generate_posts_non_streaming_json_and_extracts_fenced_code() -> None:
    _require_contract()
    opener = RecordingOpener([{"response": "Here is the scene:\n```python\nprint('scene')\n```"}])
    provider = OllamaProvider(
        model="test-model",
        base_url="http://localhost:11434",
        timeout=3.5,
        opener=opener,
    )

    response = provider.generate(_request())

    assert response.code == "print('scene')"
    assert response.raw_response["response"].startswith("Here is the scene:")
    assert len(opener.requests) == 1
    request, timeout = opener.requests[0]
    assert request.full_url == "http://localhost:11434/api/generate"
    assert timeout == 3.5
    body = _request_body(request)
    assert body["model"] == "test-model"
    assert body["stream"] is False
    assert body["keep_alive"] == 0
    assert isinstance(body["prompt"], str)
    assert "AcceptanceScene" in body["prompt"]
    assert "Draw a circle" in body["prompt"]


def test_ollama_generate_sends_reproducible_sampling_options() -> None:
    """Temperature and seed are explicit render inputs, not hidden defaults."""

    _require_contract()
    opener = RecordingOpener([{"response": "print('scene')"}])
    provider = OllamaProvider(base_url="http://localhost:11434", opener=opener)

    provider.generate(_request(temperature=0.2, seed=17))

    body = _request_body(opener.requests[0][0])
    assert body["options"] == {"temperature": 0.2, "seed": 17}


def test_ollama_generate_accepts_plain_code_and_correction_context() -> None:
    _require_contract()
    opener = RecordingOpener([{"response": "print('corrected')"}])
    provider = OllamaProvider(base_url="http://localhost:11434", opener=opener)
    diagnostics = {
        "argv": ["python", "-m", "manim", "render", "scene.py"],
        "exit_code": 17,
        "timeout": True,
        "stdout": "STDOUT_RENDER_SENTINEL",
        "stderr": "STDERR_RENDER_SENTINEL",
        "validator_reasons": ["VALIDATOR_MISSING_VIDEO", "VALIDATOR_ZERO_DURATION"],
    }
    request = _request(
        previous_code="print('broken')",
        diagnostics=diagnostics,
    )

    response = provider.generate(request)

    assert response.code == "print('corrected')"
    body = _request_body(opener.requests[0][0])
    assert body["stream"] is False
    assert body["keep_alive"] == 0
    assert isinstance(body["prompt"], str)
    prompt = body["prompt"]
    assert "1.0" in prompt
    assert "AcceptanceScene" in prompt
    assert "Draw a circle, transform it into a square, and move it right." in prompt
    assert "print('broken')" in prompt
    assert "python" in prompt
    assert "-m" in prompt
    assert "manim" in prompt
    assert "render" in prompt
    assert "scene.py" in prompt
    assert "17" in prompt
    assert "True" in prompt
    assert "STDOUT_RENDER_SENTINEL" in prompt
    assert "STDERR_RENDER_SENTINEL" in prompt
    assert "VALIDATOR_MISSING_VIDEO" in prompt
    assert "VALIDATOR_ZERO_DURATION" in prompt


def test_ollama_uses_documented_defaults() -> None:
    _require_contract()
    opener = RecordingOpener([{"response": "print('default')"}])
    provider = OllamaProvider(opener=opener)

    response = provider.generate(_request())

    assert response.code == "print('default')"
    request, _timeout = opener.requests[0]
    assert request.full_url == "http://localhost:11434/api/generate"
    body = _request_body(request)
    assert body["model"] == "qwen2.5-coder:7b"


def test_ollama_surfaces_http_failure_from_boundary() -> None:
    _require_contract()
    provider = OllamaProvider(
        base_url="http://localhost:11434",
        opener=HTTPFailureOpener(),
    )

    with pytest.raises(HTTPError):
        provider.generate(_request())


def test_ollama_surfaces_timeout_failure_from_boundary() -> None:
    _require_contract()
    provider = OllamaProvider(
        base_url="http://localhost:11434",
        opener=TimeoutOpener(),
    )

    with pytest.raises(TimeoutError):
        provider.generate(_request())


@pytest.mark.parametrize(
    "response_body",
    [
        b"not-json",
        {"model": "test-model"},
        {"response": 17},
    ],
)
def test_ollama_rejects_invalid_or_missing_response_shape(
    response_body: object,
) -> None:
    _require_contract()
    opener = RecordingOpener([response_body])
    provider = OllamaProvider(base_url="http://localhost:11434", opener=opener)

    with pytest.raises(ProviderError):
        provider.generate(_request())


def test_provider_audit_contract() -> None:
    """Inventory the declared provider contract tests without product calls."""

    behavioral_tests = (
        "test_llm_provider_is_replaceable_through_generate_and_unload",
        "test_ollama_generate_posts_non_streaming_json_and_extracts_fenced_code",
        "test_ollama_generate_accepts_plain_code_and_correction_context",
        "test_ollama_unload_posts_explicit_keep_alive_zero",
        "test_ollama_uses_documented_defaults",
        "test_ollama_surfaces_http_failure_from_boundary",
        "test_ollama_surfaces_timeout_failure_from_boundary",
        "test_ollama_rejects_invalid_or_missing_response_shape",
    )

    assert all(callable(globals().get(name)) for name in behavioral_tests)


def test_ollama_unload_posts_explicit_keep_alive_zero() -> None:
    _require_contract()
    opener = RecordingOpener([{"response": ""}])
    provider = OllamaProvider(
        model="test-model",
        base_url="http://localhost:11434",
        opener=opener,
    )

    result = provider.unload()

    assert result.ok is True
    assert len(opener.requests) == 1
    body = _request_body(opener.requests[0][0])
    assert body == {
        "model": "test-model",
        "prompt": "",
        "stream": False,
        "keep_alive": 0,
    }
