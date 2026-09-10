"""Replaceable synchronous provider contracts and the Ollama adapter."""

from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Protocol, runtime_checkable
from urllib.parse import urlparse

from video_pipeline.prompts import build_prompt


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    """One generation request, including all context needed for correction."""

    scene_name: str
    description: str
    topics: tuple[str, ...] = ()
    reference_examples: int = 2
    expectations: Mapping[str, object] | None = None
    temperature: float = 0.0
    seed: int = 42
    previous_code: str | None = None
    diagnostics: Mapping[str, object] | None = None
    # Visual context is explicit and persisted so a correction can be replayed
    # without reconstructing state from generated source.
    theme: Mapping[str, object] | None = None
    scene_plan: Mapping[str, object] | None = None
    capabilities: tuple[str, ...] = ()
    capability_context: tuple[Mapping[str, object], ...] = ()
    # Temporal context is kept flat at the provider boundary so callers can
    # persist exactly what the coder received without introducing another plan
    # document or model.
    narration_text: str | None = None
    start_seconds: float | None = None
    end_seconds: float | None = None
    target_duration_seconds: float | None = None
    objective: str | None = None
    previous_scene: Mapping[str, object] | None = None
    next_scene: Mapping[str, object] | None = None
    required_objects: tuple[str, ...] = ()
    required_elements: tuple[str, ...] = ()
    resolution: tuple[int, int] | None = None
    fps: int | None = None
    prior_findings: tuple[object, ...] = ()


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    """Generated Python plus the unmodified provider response payload."""

    code: str
    raw_response: Mapping[str, object]
    normalization: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class UnloadResult:
    """Result of an explicit model-unload request."""

    ok: bool
    raw_response: Mapping[str, object] | None = None


class ProviderError(RuntimeError):
    """Raised when the provider response cannot satisfy its boundary contract."""


@runtime_checkable
class LLMProvider(Protocol):
    """Synchronous provider seam used by the correction loop."""

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        """Generate or correct Manim Community Python for a scene."""

    def unload(self) -> UnloadResult:
        """Release the model before a renderer invocation."""


class _HTTPResponse(Protocol):
    def __enter__(self) -> _HTTPResponse:
        """
        Enter the response context.

        The local protocol keeps the injectable opener as narrow as the
        urllib boundary used by the adapter.
        """

    def __exit__(self, *_args: object) -> None:
        """Leave the response context."""

    def read(self) -> bytes:
        """Read the response body."""


Opener = Callable[[urllib.request.Request, float], _HTTPResponse]


_FENCED_CODE = re.compile(
    r"```(?P<language>[^\r\n`]*)\r?\n(?P<code>.*?)```",
    flags=re.IGNORECASE | re.DOTALL,
)


class AuthoredSourceProvider:
    """Load authored candidates without inference or changing their source."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        if not re.fullmatch(r"[A-Z][A-Za-z0-9_]*", request.scene_name):
            raise ProviderError("invalid authored scene name")
        source = (self.root / request.scene_name / "scene.py").resolve()
        if not source.is_relative_to(self.root):
            raise ProviderError("authored source must remain inside its root")
        try:
            data = source.read_bytes()
            code = data.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise ProviderError(f"cannot read authored source for {request.scene_name}") from exc
        if not code.strip():
            raise ProviderError("authored source is empty")
        return ProviderResponse(
            code=code,
            raw_response={
                "provider": "authored_source",
                "source": str(source),
                "sha256": hashlib.sha256(data).hexdigest(),
                "response": code,
            },
        )

    def unload(self) -> UnloadResult:
        return UnloadResult(ok=True, raw_response={"provider": "authored_source"})


class OllamaProvider:
    """Small stdlib adapter for Ollama's non-streaming generate endpoint."""

    def __init__(
        self,
        *,
        model: str = "qwen2.5-coder:7b",
        base_url: str = "http://localhost:11434",
        timeout: float | None = None,
        opener: Opener | None = None,
        num_ctx: int | None = None,
    ) -> None:
        if not model.strip():
            raise ValueError("model must not be blank")
        if not base_url.strip():
            raise ValueError("base_url must not be blank")
        parsed_url = urlparse(base_url)
        if parsed_url.scheme not in {"http", "https"} or parsed_url.hostname not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise ValueError("Ollama base URL must be a local loopback endpoint")
        try:
            timeout = float(
                timeout
                if timeout is not None
                else os.environ.get("VIDEO_PIPELINE_OLLAMA_TIMEOUT", "120")
            )
        except (ValueError, TypeError) as exc:
            raise ValueError("Ollama timeout must be a finite positive number") from exc
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Ollama timeout must be a finite positive number")

        try:
            configured_context = (
                num_ctx
                if num_ctx is not None
                else int(os.environ.get("VIDEO_PIPELINE_OLLAMA_NUM_CTX", "16384"))
            )
        except ValueError as exc:
            raise ValueError("Ollama context must be a positive integer") from exc
        if type(configured_context) is not int or configured_context <= 0:
            raise ValueError("Ollama context must be a positive integer")
        self.num_ctx = configured_context

        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)
        self._opener = opener or urllib.request.urlopen

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        """Generate candidate Python and retain the complete raw response."""

        prompt = build_prompt(request)
        raw_response = self._post(
            prompt,
            options={
                "temperature": request.temperature,
                "seed": request.seed,
                "num_ctx": self.num_ctx,
            },
        )
        response_text = raw_response.get("response")
        if not isinstance(response_text, str):
            raise ProviderError("Ollama response must contain a string response")

        code = _extract_python_code(response_text)
        if not code:
            raise ProviderError("Ollama response did not contain Python code")
        normalization: dict[str, object] | None = None
        if request.scene_plan is not None:
            # The application owns the runtime binding. Preserve the model's
            # entire construct body and retain the original response verbatim.
            try:
                tree = ast.parse(code)
            except SyntaxError:
                tree = None  # Let the existing render/correction loop diagnose syntax.
            if tree is not None:
                for index, node in enumerate(tree.body):
                    if (
                        isinstance(node, ast.ClassDef)
                        and node.name == request.scene_name
                        and len(node.bases) == 1
                        and isinstance(node.bases[0], ast.Name)
                        and node.bases[0].id in {"Scene", "VisualScene"}
                    ):
                        node.bases = [ast.Name(id="VisualScene", ctx=ast.Load())]
                        tree.body.insert(
                            index,
                            ast.ImportFrom(
                                module="video_pipeline.runtime",
                                names=[ast.alias(name="VisualScene")],
                                level=0,
                            ),
                        )
                        code = ast.unparse(tree) + "\n"
                        normalization = {"kind": "visual_runtime_binding", "version": 1}
                        break
        return ProviderResponse(code=code, raw_response=raw_response, normalization=normalization)

    def unload(self) -> UnloadResult:
        """Ask Ollama to unload this model with an explicit keep-alive of zero."""

        raw_response = self._post("")
        response_text = raw_response.get("response")
        if not isinstance(response_text, str):
            raise ProviderError("Ollama unload response must contain a string response")
        return UnloadResult(ok=True, raw_response=raw_response)

    def _post(
        self,
        prompt: str,
        *,
        options: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        body = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": 0,
        }
        if options is not None:
            body["options"] = dict(options)
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        # Transport exceptions intentionally pass through unchanged.  The
        # caller can distinguish HTTP and timeout failures at this boundary.
        with self._opener(request, timeout=self.timeout) as response:
            encoded = response.read()
        try:
            decoded = json.loads(encoded.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderError("Ollama returned invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise ProviderError("Ollama response must be a JSON object")
        return decoded


def _extract_python_code(response: str) -> str:
    """Extract the first fenced Python block, or use a plain response."""

    generic_match: str | None = None
    for match in _FENCED_CODE.finditer(response):
        language = match.group("language").strip().lower()
        code = match.group("code").strip()
        if language in {"", "python", "py"} or language.startswith("python "):
            return code
        if generic_match is None:
            generic_match = code
    return generic_match or response.strip()


__all__ = [
    "LLMProvider",
    "OllamaProvider",
    "Opener",
    "ProviderError",
    "ProviderRequest",
    "ProviderResponse",
    "UnloadResult",
]
