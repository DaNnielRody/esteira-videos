"""Public narration commands remain offline until execution is requested."""

import io
import json
import subprocess
import urllib.error
import wave
from pathlib import Path

import numpy as np
import pytest

from video_pipeline.cli import main
from video_pipeline.elevenlabs import VoiceSettings
from video_pipeline.narration import NarrationEnhancer
from video_pipeline.scene_plan import Beat
from video_pipeline.theme import VideoTheme
from video_pipeline.timeline import build_explicit_timeline


def audio_bytes(samples: list[int]) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        output.writeframes(np.array(samples, dtype="<i2").tobytes())
    return buffer.getvalue()


def test_narration_plan_is_available_without_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "speech.wav"
    with wave.open(str(source), "wb") as output:
        output.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        output.writeframes(b"\0\0" * 48000)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("ELEVEN_LABS_API_KEY", raising=False)
    try:
        result = main(["narration", "plan", str(source)])
    except SystemExit as exc:
        result = exc.code
    assert result == 0, "narration plan must inspect audio without an API key"
    assert '"request_count": 0' in capsys.readouterr().out


def test_enhance_preserves_pauses_duration_and_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "speech.wav"
    original = audio_bytes([0] * 48000 + [4000] * 48000 + [0] * 48000)
    source.write_bytes(original)
    output = tmp_path / "candidate"
    requests: list[object] = []

    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            pass

        def read(self) -> bytes:
            return audio_bytes([8000] * 53760)

    def opener(_self: object, request: object, **_kwargs: object) -> Response:
        from email import policy
        from email.parser import BytesParser
        from urllib.request import Request

        assert isinstance(request, Request)
        assert request.full_url == (
            "https://api.elevenlabs.io/v1/speech-to-speech/testVoice?output_format=mp3_44100_128"
        )
        assert request.data is not None
        message = BytesParser(policy=policy.default).parsebytes(
            f"Content-Type: {request.get_header('Content-type')}\r\n\r\n".encode() + request.data
        )
        fields = {
            part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
            for part in message.iter_parts()
        }
        assert fields["model_id"] == b"eleven_multilingual_sts_v2"
        assert json.loads(fields["voice_settings"])["style"] == 0.5
        assert fields["audio"].startswith(b"RIFF")
        requests.append(request)
        return Response()

    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-secret")
    monkeypatch.setattr("urllib.request.OpenerDirector.open", opener)
    # Patch the external opener, allowing all internal collaborators to run.
    try:
        result = main(["narration", "enhance", str(source), str(output), "--voice-id", "testVoice"])
    except SystemExit as exc:
        result = exc.code
    assert result == 0, "enhance must publish a candidate with preserved timing"
    with wave.open(str(output / "narration.wav"), "rb") as audio:
        assert audio.getnframes() == 144000
        values = np.frombuffer(audio.readframes(144000), dtype="<i2")
    assert np.all(values[:40000] == 0)
    assert np.all(values[105000:] == 0)
    assert values[70000] == 8000
    assert source.read_bytes() == original
    assert len(requests) == 1
    assert "test-secret" not in (output / "report.json").read_text()


def test_reveal_beat_increases_expression_without_moving_audio(tmp_path: Path) -> None:
    source = tmp_path / "speech.wav"
    source.write_bytes(audio_bytes([0] * 48000 + [4000] * 48000 + [0] * 48000))
    built = build_explicit_timeline(
        "# Descoberta\n@start: 0\n@end: 3\nAgora apareceu a resposta!",
        3.0,
        theme=VideoTheme(),
    )
    assert built is not None
    timeline, plans = built
    plan = plans[0].model_copy(
        update={
            "beats": [
                Beat(id="resposta", action="reveal", start_seconds=1.0, end_seconds=2.0),
            ]
        }
    )
    settings_seen: list[VoiceSettings] = []

    class Provider:
        def convert(
            self,
            audio: bytes,
            *,
            voice_id: str,
            settings: VoiceSettings,
        ) -> bytes:
            settings_seen.append(settings)
            return audio_bytes([8000] * 53760)

    output = tmp_path / "candidate"
    NarrationEnhancer(Provider()).enhance(
        source,
        output,
        voice_id="ownedVoice",
        timeline=timeline,
        scene_plans=[plan],
    )
    assert settings_seen[0].style == pytest.approx(0.65)
    assert settings_seen[0].stability == pytest.approx(0.25)
    report = json.loads((output / "report.json").read_text())
    assert report["phrases"][0]["beat_ids"] == ["descoberta/resposta"]
    assert report["phrases"][0]["start_seconds"] == 0.94
    assert report["phrases"][0]["end_seconds"] == 2.06


def test_clone_success_persists_only_voice_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "sample.wav"
    source.write_bytes(audio_bytes([4000] * (48000 * 30)))

    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            pass

        def read(self) -> bytes:
            return b'{"voice_id":"ownedVoice","requires_verification":true}'

    def opener(_self: object, request: object, **_kwargs: object) -> Response:
        from urllib.request import Request

        assert isinstance(request, Request)
        assert request.full_url == "https://api.elevenlabs.io/v1/voices/add"
        assert request.data is not None and b'name="files"' in request.data
        return Response()

    monkeypatch.setenv("ELEVEN_LABS_API_KEY", "test-secret")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setattr("urllib.request.OpenerDirector.open", opener)
    output = tmp_path / "voice.json"
    assert (
        main(
            [
                "narration",
                "clone",
                str(source),
                "--name",
                "Dan",
                "--duration",
                "30",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert json.loads(output.read_text()) == {
        "voice_id": "ownedVoice",
        "name": "Dan",
        "requires_verification": True,
    }
    assert "test-secret" not in output.read_text()


def test_existing_candidate_is_rejected_before_api_call(tmp_path: Path) -> None:
    output = tmp_path / "candidate"
    output.mkdir()
    (output / "narration.wav").write_bytes(b"previous candidate")

    class Provider:
        def convert(
            self,
            audio: bytes,
            *,
            voice_id: str,
            settings: VoiceSettings,
        ) -> bytes:
            pytest.fail("an existing output must not trigger a paid request")

    with pytest.raises(ValueError, match="already exists"):
        NarrationEnhancer(Provider()).enhance(tmp_path / "source.wav", output, voice_id="voice")
    assert (output / "narration.wav").read_bytes() == b"previous candidate"


def test_bad_wav_is_reported_without_traceback_or_ffmpeg_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "bad.wav"
    source.write_bytes(b"invalid wave")

    def run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess([], 1, b"", b"private decoder detail")

    monkeypatch.setattr("subprocess.run", run)
    assert main(["narration", "plan", str(source)]) == 1
    output = capsys.readouterr().out
    assert "cannot decode narration" in output
    assert "private decoder detail" not in output


@pytest.mark.parametrize("normalized_count", [53760, 52748])
def test_small_duration_drift_is_adjusted_without_shifting_pauses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    normalized_count: int,
) -> None:
    source = tmp_path / "speech.wav"
    source.write_bytes(audio_bytes([0] * 48000 + [4000] * 48000 + [0] * 48000))

    class Provider:
        def convert(
            self,
            audio: bytes,
            *,
            voice_id: str,
            settings: VoiceSettings,
        ) -> bytes:
            return audio_bytes([8000] * 55000)

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        filters = args[args.index("-af") + 1]
        assert float(filters.removeprefix("atempo=")) == pytest.approx(55000 / 53760)
        return subprocess.CompletedProcess(
            args, 0, np.full(normalized_count, 0.25, dtype="<f4").tobytes(), b""
        )

    monkeypatch.setattr("subprocess.run", run)
    output = tmp_path / "candidate"
    NarrationEnhancer(Provider()).enhance(source, output, voice_id="ownedVoice")
    with wave.open(str(output / "narration.wav"), "rb") as audio:
        values = np.frombuffer(audio.readframes(144000), dtype="<i2")
    assert len(values) == 144000
    assert values[70000] == 8192
    assert np.all(values[:40000] == 0)
    assert np.all(values[105000:] == 0)
    report = json.loads((output / "report.json").read_text())
    assert report["phrases"][0]["timing_adjusted"] is True


def test_tiny_source_spans_are_preserved_without_paid_conversion(tmp_path: Path) -> None:
    source = tmp_path / "speech.wav"
    source.write_bytes(
        audio_bytes([0] * 48000 + [4000] * 48000 + [0] * 48000 + [4000] * 1920 + [0] * 48000)
    )
    calls: list[str] = []

    class Provider:
        def convert(
            self,
            audio: bytes,
            *,
            voice_id: str,
            settings: VoiceSettings,
        ) -> bytes:
            calls.append(voice_id)
            return audio_bytes([8000] * 53760)

    output = tmp_path / "candidate"
    NarrationEnhancer(Provider()).enhance(source, output, voice_id="ownedVoice")
    with wave.open(str(output / "narration.wav"), "rb") as audio:
        values = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2")
    assert len(calls) == 1
    assert values[70000] == 8000
    assert values[145000] == 4000
    report = json.loads((output / "report.json").read_text())
    assert report["request_count"] == 1
    assert report["phrases"][1]["converted"] is False


def test_clone_reports_provider_reason_without_exposing_body_or_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "sample.wav"
    source.write_bytes(audio_bytes([4000] * (48000 * 30)))

    def opener(_self: object, request: object, **_kwargs: object) -> None:
        body = io.BytesIO(
            json.dumps(
                {
                    "detail": {
                        "status": "paid_plan_required",
                        "message": "test-secret /private/path",
                    }
                }
            ).encode()
        )
        raise urllib.error.HTTPError("https://api.elevenlabs.io", 400, "error", {}, body)

    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-secret")
    monkeypatch.setattr("urllib.request.OpenerDirector.open", opener)
    output = tmp_path / "voice.json"
    assert (
        main(
            [
                "narration",
                "clone",
                str(source),
                "--name",
                "Owned voice",
                "--duration",
                "30",
                "--output",
                str(output),
            ]
        )
        == 1
    )
    message = capsys.readouterr().out
    assert "paid_plan_required" in message
    assert "test-secret" not in message
    assert "/private/path" not in message
    assert not output.exists()


@pytest.mark.parametrize("sample_count,value", [(70000, 8000), (53760, 0)])
def test_bad_conversion_never_publishes_candidate(
    tmp_path: Path,
    sample_count: int,
    value: int,
) -> None:
    source = tmp_path / "speech.wav"
    original = audio_bytes([0] * 48000 + [4000] * 48000 + [0] * 48000)
    source.write_bytes(original)

    class Provider:
        def convert(
            self,
            audio: bytes,
            *,
            voice_id: str,
            settings: VoiceSettings,
        ) -> bytes:
            return audio_bytes([value] * sample_count)

    output = tmp_path / "candidate"
    with pytest.raises(ValueError, match="timing drift|silent"):
        NarrationEnhancer(Provider()).enhance(source, output, voice_id="ownedVoice")
    assert not output.exists()
    assert source.read_bytes() == original
