"""Narration preparation, preserving source pauses for voice conversion."""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import tempfile
import wave
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from video_pipeline.elevenlabs import (
    DEFAULT_VOICE_SETTINGS,
    ElevenLabsVoiceChanger,
    VoiceConversionProvider,
    VoiceSettings,
)
from video_pipeline.project import validate_project_timeline
from video_pipeline.scene_plan import ScenePlan
from video_pipeline.timeline import Timeline, load_timeline

SAMPLE_RATE = 48000


@dataclass(frozen=True, slots=True)
class SpeechSpan:
    start_sample: int
    end_sample: int
    scene_id: str | None = None
    beat_ids: tuple[str, ...] = ()
    emphasize: bool = False

    @property
    def can_convert(self) -> bool:
        """Retain very short articulations/noises instead of amplifying codec delay."""
        return self.end_sample - self.start_sample >= round(0.3 * SAMPLE_RATE)

    def to_document(self) -> dict[str, object]:
        return {
            "start_seconds": self.start_sample / SAMPLE_RATE,
            "end_seconds": self.end_sample / SAMPLE_RATE,
            "scene_id": self.scene_id,
            "beat_ids": list(self.beat_ids),
            "emphasize": self.emphasize,
            "conversion": "voice_changer" if self.can_convert else "preserve_short_source",
        }


@dataclass(frozen=True, slots=True)
class NarrationPlan:
    samples: NDArray[np.float32]
    spans: tuple[SpeechSpan, ...]

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": "narration-plan/1",
            "duration_seconds": len(self.samples) / SAMPLE_RATE,
            "request_count": sum(span.can_convert for span in self.spans),
            "conversion_seconds": sum(
                span.end_sample - span.start_sample for span in self.spans if span.can_convert
            )
            / SAMPLE_RATE,
            "spans": [span.to_document() for span in self.spans],
            "review_required": True,
            "limitations": [
                "Voice Changer preserves delivery; enthusiasm is an experiment.",
                "Pause and total duration preservation do not prove word alignment.",
            ],
        }


def read_audio(path: Path) -> NDArray[np.float32]:
    """Decode narration to mono 48 kHz; WAV PCM16 uses the standard library."""
    return decode_audio(path.read_bytes())


def decode_audio(data: bytes) -> NDArray[np.float32]:
    """Use the existing FFmpeg dependency for MP3 and other audio formats."""
    if data.startswith(b"RIFF"):
        try:
            with wave.open(io.BytesIO(data), "rb") as audio:
                if (
                    audio.getsampwidth() == 2
                    and audio.getnchannels() == 1
                    and audio.getframerate() == SAMPLE_RATE
                ):
                    return (
                        np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(
                            np.float32
                        )
                        / 32768.0
                    )
        except (wave.Error, EOFError):
            pass  # FFmpeg also handles valid WAV formats unsupported by wave.
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            "pipe:0",
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-f",
            "f32le",
            "pipe:1",
        ],
        input=data,
        capture_output=True,
        check=False,
        timeout=120,
    )
    if result.returncode or not result.stdout or len(result.stdout) % 4:
        raise ValueError("cannot decode narration audio")
    samples = np.frombuffer(result.stdout, dtype="<f4").copy()
    if not np.isfinite(samples).all():
        raise ValueError("decoded narration contains non-finite samples")
    return samples


def wav_bytes(samples: NDArray[np.float32]) -> bytes:
    """Encode a portable PCM16 WAV without external processes."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
        output.writeframes((np.clip(samples, -1, 1 - 1 / 32768) * 32768).astype("<i2").tobytes())
    return buffer.getvalue()


def plan_narration(
    source: Path,
    *,
    timeline: Timeline | None = None,
    scene_plans: Sequence[ScenePlan] = (),
) -> NarrationPlan:
    """Find speech between pauses; never cut a spoken phrase to fit API limits."""
    samples = read_audio(source)
    if not len(samples) or not np.isfinite(samples).all():
        raise ValueError("narration must contain finite audio samples")
    duration = len(samples) / SAMPLE_RATE
    if timeline is not None and abs(duration - timeline.duration_seconds) > 0.002:
        raise ValueError("audio duration differs from timeline; use the canonical narration")
    if scene_plans and timeline is None:
        raise ValueError("scene plans require their canonical timeline")
    beat_windows: list[tuple[str, str, float, float]] = []
    if timeline is not None:
        segments = {segment.id: segment for segment in timeline.segments}
        for scene_plan in scene_plans:
            segment = segments.get(scene_plan.id)
            if (
                segment is None
                or abs(scene_plan.duration_seconds - segment.duration_seconds) > 0.002
            ):
                raise ValueError("scene plan differs from canonical timeline")
            for beat in scene_plan.beats:
                if beat.start_seconds is not None and beat.end_seconds is not None:
                    beat_windows.append(
                        (
                            f"{scene_plan.id}/{beat.id}",
                            beat.action,
                            segment.start_seconds + beat.start_seconds,
                            segment.start_seconds + beat.end_seconds,
                        )
                    )
    # 10 ms energy windows; keep short intra-phrase pauses in the conversion.
    window = 480
    padded = np.pad(samples, (0, (-len(samples)) % window))
    energy = np.sqrt(np.mean(padded.reshape(-1, window) ** 2, axis=1))
    active = np.flatnonzero(energy > 10 ** (-35 / 20))
    spans: list[SpeechSpan] = []
    if len(active):
        groups = np.split(active, np.flatnonzero(np.diff(active) > 35) + 1)
        for group in groups:
            start = max(0, int(group[0]) * window - 2880)
            end = min(len(samples), (int(group[-1]) + 1) * window + 2880)
            if end - start > 540 * SAMPLE_RATE:
                raise ValueError("speech exceeds 9 minutes without a safe pause")
            midpoint = (start + end) / (2 * SAMPLE_RATE)
            scene_id = None
            if timeline is not None:
                scene_id = next(
                    (
                        segment.id
                        for segment in timeline.segments
                        if segment.start_seconds <= midpoint < segment.end_seconds
                    ),
                    None,
                )
            overlapping = [
                (beat_id, action)
                for beat_id, action, left, right in beat_windows
                if start / SAMPLE_RATE < right and end / SAMPLE_RATE > left
            ]
            emphasize = any(
                action in {"reveal", "transform", "connect", "emphasize", "payoff"}
                for _, action in overlapping
            )
            spans.append(
                SpeechSpan(
                    start, end, scene_id, tuple(beat_id for beat_id, _ in overlapping), emphasize
                )
            )
    return NarrationPlan(samples, tuple(spans))


class NarrationEnhancer:
    """Reusable post-generation service that publishes reviewable audio candidates."""

    def __init__(self, provider: VoiceConversionProvider) -> None:
        self.provider = provider

    def enhance(
        self,
        source: Path,
        output: Path,
        *,
        voice_id: str,
        timeline: Timeline | None = None,
        settings: VoiceSettings = DEFAULT_VOICE_SETTINGS,
        scene_plans: Sequence[ScenePlan] = (),
    ) -> Path:
        """Keep pauses and phrase boundaries; never mutate a canonical project."""
        if output.exists():
            raise ValueError("narration output already exists; choose a new candidate directory")
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        plan = plan_narration(source, timeline=timeline, scene_plans=scene_plans)
        converted = plan.samples.copy()
        report = plan.to_document()
        report.update(
            {
                "schema_version": "narration-enhancement/1",
                "voice_id": voice_id,
                "source_sha256": source_hash,
                "settings": settings.to_document(),
                "model_id": "eleven_multilingual_sts_v2",
                "phrases": [],
            }
        )
        phrases: list[dict[str, object]] = []
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".narration-", dir=output.parent) as directory:
            staging = Path(directory)
            for index, span in enumerate(plan.spans):
                original = plan.samples[span.start_sample : span.end_sample]
                if not span.can_convert:
                    phrases.append(
                        {
                            **span.to_document(),
                            "converted": False,
                            "reason": "preserve_short_source",
                        }
                    )
                    continue
                phrase_settings = settings
                if span.emphasize:
                    phrase_settings = VoiceSettings(
                        stability=max(0, settings.stability - 0.05),
                        similarity_boost=settings.similarity_boost,
                        style=min(1, settings.style + 0.15),
                        use_speaker_boost=settings.use_speaker_boost,
                    )
                data = self.provider.convert(
                    wav_bytes(original), voice_id=voice_id, settings=phrase_settings
                )
                # Retain the raw converted phrase for auditing and listening.
                (staging / f"phrase-{index + 1:04d}.mp3").write_bytes(data)
                samples = decode_audio(data)
                if not len(samples) or not np.isfinite(samples).all():
                    raise ValueError("converted phrase has invalid samples")
                if np.max(np.abs(samples)) < 1e-5:
                    raise ValueError("converted phrase is silent")
                ratio = len(samples) / len(original)
                if abs(ratio - 1) > 0.05:
                    raise ValueError(
                        "converted phrase timing drift exceeds 5%; candidate not published"
                    )
                end_padding_samples = 0
                end_trim_samples = 0
                if len(samples) != len(original):
                    result = subprocess.run(
                        [
                            "ffmpeg",
                            "-v",
                            "error",
                            "-i",
                            "pipe:0",
                            "-af",
                            f"atempo={ratio:.12f}",
                            "-ac",
                            "1",
                            "-ar",
                            str(SAMPLE_RATE),
                            "-f",
                            "f32le",
                            "pipe:1",
                        ],
                        input=wav_bytes(samples),
                        capture_output=True,
                        check=False,
                        timeout=120,
                    )
                    if result.returncode or not result.stdout or len(result.stdout) % 4:
                        raise ValueError("phrase timing normalization failed")
                    samples = np.frombuffer(result.stdout, dtype="<f4").copy()
                    residual = len(samples) - len(original)
                    if not np.isfinite(samples).all() or residual > 480 or residual < -2880:
                        raise ValueError("phrase normalization would trim speech")
                    end_padding_samples = max(0, -residual)
                    end_trim_samples = max(0, residual)
                    samples = np.pad(samples, (0, max(0, len(original) - len(samples))))[
                        : len(original)
                    ]
                converted[span.start_sample : span.end_sample] = samples
                phrases.append(
                    {
                        **span.to_document(),
                        "raw_duration_ratio": ratio,
                        "timing_adjusted": ratio != 1,
                        "audio": f"phrase-{index + 1:04d}.mp3",
                        "settings": phrase_settings.to_document(),
                        "converted": True,
                        "end_padding_seconds": end_padding_samples / SAMPLE_RATE,
                        "end_trim_seconds": end_trim_samples / SAMPLE_RATE,
                    }
                )
            if hashlib.sha256(source.read_bytes()).hexdigest() != source_hash:
                raise ValueError("source narration changed during conversion")
            encoded = wav_bytes(converted)
            (staging / "narration.wav").write_bytes(encoded)
            report["phrases"] = phrases
            report["output_sha256"] = hashlib.sha256(encoded).hexdigest()
            (staging / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            # Create-once directory publication; originals and earlier candidates remain intact.
            if output.exists():
                raise ValueError("narration output was created during conversion")
            os.rename(staging, output)
        return output / "narration.wav"


def run_narration_command(options: object) -> int:
    """CLI adapter kept separate from project/render orchestration."""
    # argparse's namespace is a dynamic boundary, as in the existing CLI.
    from argparse import Namespace

    if not isinstance(options, Namespace):
        raise ValueError("invalid narration command")
    if options.narration_command == "clone":
        if options.output.exists():
            raise ValueError(
                "voice metadata already exists; reuse it instead of creating another clone"
            )
        if not np.isfinite(options.start) or options.start < 0:
            raise ValueError("sample start must be finite and non-negative")
        if not np.isfinite(options.duration) or not 30 <= options.duration <= 180:
            raise ValueError("clone sample duration must be between 30 and 180 seconds")
        samples = read_audio(options.source)
        start, end = (
            round(options.start * SAMPLE_RATE),
            round((options.start + options.duration) * SAMPLE_RATE),
        )
        if end > len(samples):
            raise ValueError("clone sample extends beyond source audio")
        provider = ElevenLabsVoiceChanger.from_environment(options.env_file)
        metadata = provider.clone(wav_bytes(samples[start:end]), name=options.name)
        options.output.parent.mkdir(parents=True, exist_ok=True)
        with options.output.open("x", encoding="utf-8") as output:
            json.dump(metadata, output, indent=2)
        print(json.dumps(metadata, ensure_ascii=False))
        return 0
    timeline = load_timeline(options.timeline) if options.timeline else None
    scene_plans: list[ScenePlan] = []
    if options.project:
        project, timeline = validate_project_timeline(options.project)
        root = options.project.resolve().parent
        for scene in project.scenes:
            path = (root / scene.plan_path).resolve(strict=True)
            if not path.is_relative_to(root):
                raise ValueError("scene plan escapes project root")
            scene_plans.append(ScenePlan.model_validate_json(path.read_text(encoding="utf-8")))
    if options.narration_command == "plan":
        plan = plan_narration(options.source, timeline=timeline, scene_plans=scene_plans)
        print(json.dumps(plan.to_document(), ensure_ascii=False))
        return 0
    voice_id = options.voice_id or os.environ.get("ELEVENLABS_VOICE_ID")
    if options.voice_file:
        metadata = json.loads(options.voice_file.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict) or metadata.get("requires_verification"):
            raise ValueError("clone metadata is invalid or requires verification")
        voice_id = metadata.get("voice_id")
    if not isinstance(voice_id, str) or not voice_id:
        raise ValueError("provide --voice-id, --voice-file or ELEVENLABS_VOICE_ID")
    result = NarrationEnhancer(ElevenLabsVoiceChanger.from_environment(options.env_file)).enhance(
        options.source,
        options.output,
        voice_id=voice_id,
        timeline=timeline,
        scene_plans=scene_plans,
        settings=VoiceSettings(
            stability=options.stability, similarity_boost=options.similarity, style=options.style
        ),
    )
    print(f"NARRATION: {result}")
    return 0
