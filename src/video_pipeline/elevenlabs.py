"""Synchronous ElevenLabs adapter; credentials never enter artifact documents."""

from __future__ import annotations

import json
import math
import os
import re
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol


class ElevenLabsError(ValueError):
    """Sanitized provider failure safe to report at the CLI boundary."""


@dataclass(frozen=True, slots=True)
class VoiceSettings:
    stability: float = 0.3
    similarity_boost: float = 0.85
    style: float = 0.5
    use_speaker_boost: bool = True

    def __post_init__(self) -> None:
        for value in (self.stability, self.similarity_boost, self.style):
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("voice settings must be finite values between zero and one")

    def to_document(self) -> dict[str, object]:
        return asdict(self)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        raise ElevenLabsError("ElevenLabs redirect refused")


DEFAULT_VOICE_SETTINGS = VoiceSettings()


class VoiceConversionProvider(Protocol):
    def convert(
        self,
        audio: bytes,
        *,
        voice_id: str,
        settings: VoiceSettings,
    ) -> bytes:
        """Convert one input phrase through an injectable external boundary."""


class ElevenLabsVoiceChanger:
    """Voice conversion and explicit instant cloning with fixed HTTPS endpoints."""

    def __init__(self, api_key: str, *, timeout: float = 180) -> None:
        if not api_key.strip() or "\n" in api_key or "\r" in api_key:
            raise ValueError("configure ELEVENLABS_API_KEY or ELEVEN_LABS_API_KEY")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("ElevenLabs timeout must be finite and positive")
        self._api_key = api_key.strip()
        self.timeout = timeout

    @classmethod
    def from_environment(cls, env_file: Path | None = None) -> ElevenLabsVoiceChanger:
        """Read an explicitly selected dotenv file without executing its contents."""
        values: dict[str, str] = {}
        if env_file is not None:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                    name, value = line.split("=", 1)
                    values[name.strip()] = value.strip().strip("\"'")
        key = (
            os.environ.get("ELEVENLABS_API_KEY")
            or os.environ.get("ELEVEN_LABS_API_KEY")
            or values.get("ELEVENLABS_API_KEY")
            or values.get("ELEVEN_LABS_API_KEY")
            or ""
        )
        return cls(key)

    def convert(
        self,
        audio: bytes,
        *,
        voice_id: str,
        settings: VoiceSettings = DEFAULT_VOICE_SETTINGS,
    ) -> bytes:
        """Convert one phrase, retaining the source performance as the API input."""
        if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", voice_id) is None:
            raise ValueError("invalid ElevenLabs voice id")
        return self._post(
            f"/v1/speech-to-speech/{voice_id}?output_format=mp3_44100_128",
            {
                "model_id": "eleven_multilingual_sts_v2",
                "voice_settings": json.dumps(settings.to_document()),
                "remove_background_noise": "false",
                "file_format": "other",
            },
            "audio",
            audio,
        )

    def clone(self, sample: bytes, *, name: str) -> dict[str, object]:
        """Create a voice only when the caller explicitly requests cloning."""
        if not name.strip() or len(name) > 100:
            raise ValueError("clone name must contain 1 to 100 characters")
        body = self._post(
            "/v1/voices/add",
            {"name": name, "description": "Owner narration voice"},
            "files",
            sample,
        )
        try:
            document = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            raise ElevenLabsError("ElevenLabs returned invalid clone metadata") from None
        if not isinstance(document, dict) or not isinstance(document.get("voice_id"), str):
            raise ElevenLabsError("ElevenLabs did not return a clone voice id")
        return {
            "voice_id": document["voice_id"],
            "name": name,
            "requires_verification": document.get("requires_verification", False),
        }

    def _post(
        self,
        endpoint: str,
        fields: dict[str, str],
        file_field: str,
        audio: bytes,
    ) -> bytes:
        if not audio or len(audio) > 50 * 1024 * 1024:
            raise ValueError("ElevenLabs input must contain at most 50 MB of audio")
        boundary = f"video-pipeline-{uuid.uuid4().hex}"
        parts: list[bytes] = []
        for name, value in fields.items():
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
                + value.encode()
                + b"\r\n"
            )
        parts.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
                'filename="narration.wav"\r\nContent-Type: audio/wav\r\n\r\n'
            ).encode()
            + audio
            + f"\r\n--{boundary}--\r\n".encode()
        )
        request = urllib.request.Request(
            f"https://api.elevenlabs.io{endpoint}",
            data=b"".join(parts),
            headers={
                "xi-api-key": self._api_key,
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )
        try:
            with urllib.request.build_opener(_NoRedirect()).open(
                request,
                timeout=self.timeout,
            ) as response:
                result = response.read()
        except urllib.error.HTTPError as exc:
            # Expose only a validated machine code; never provider prose or headers.
            reason = "unknown_error"
            try:
                failure = json.loads(exc.read(4096))
                detail = failure.get("detail") if isinstance(failure, dict) else None
                status = detail.get("status") if isinstance(detail, dict) else None
                if (
                    isinstance(status, str)
                    and re.fullmatch(r"[a-z_]{1,80}", status)
                    and self._api_key not in status
                ):
                    reason = status
            except (OSError, ValueError):
                pass
            raise ElevenLabsError(
                f"ElevenLabs HTTP {exc.code} ({reason}); check plan, credits and permissions"
            ) from None
        except (OSError, urllib.error.URLError):
            raise ElevenLabsError("ElevenLabs connection failed; no automatic paid retry") from None
        if not result:
            raise ElevenLabsError("ElevenLabs returned empty audio")
        return result
