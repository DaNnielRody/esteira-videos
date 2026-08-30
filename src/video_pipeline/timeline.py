"""Strict timeline contracts for narration-led audiovisual projects."""

from __future__ import annotations

import json
import math
import re
import subprocess
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from video_pipeline.scene_plan import ScenePlan
from video_pipeline.theme import VideoTheme

BoundaryProvenance = Literal[
    "explicit_timestamp",
    "pause_aligned",
    "proportional_fallback",
    "manual",
    "forced_alignment",
]
TimelineMethod = Literal[
    "explicit_timestamp",
    "pause_aligned",
    "proportional_fallback",
    "manual",
    "forced_alignment",
]
TimelineStatus = Literal["candidate", "confirmed"]

PAUSE_SEARCH_TOLERANCE_SECONDS = 1.0

_ID = re.compile(r"^[a-z][a-z0-9-]*$")
_HEADING = re.compile(r"^#{1,2}[ \t]+(.+?)[ \t]*$")
_METADATA = re.compile(r"^@(start|end|objective):[ \t]*(.*?)[ \t]*$")


class PauseInterval(BaseModel):
    """One strictly validated silence interval returned by the detector."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    start_seconds: float = Field(ge=0.0)
    end_seconds: float = Field(gt=0.0)

    @model_validator(mode="after")
    def _interval_must_be_ordered(self) -> PauseInterval:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("pause interval end must be after start")
        return self

    @property
    def midpoint_seconds(self) -> float:
        """Return the deterministic midpoint used for boundary selection."""

        return round((self.start_seconds + self.end_seconds) / 2.0, 9)


class SceneBrief(BaseModel):
    """Temporal and narration input projected into an existing scene plan."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["scene-brief/1"] = "scene-brief/1"
    id: str = Field(pattern=_ID.pattern)
    order: int = Field(ge=1)
    narration_text: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    start_seconds: float = Field(ge=0.0)
    end_seconds: float = Field(gt=0.0)
    duration_seconds: float = Field(gt=0.0)
    start_provenance: BoundaryProvenance
    end_provenance: BoundaryProvenance
    plan_path: str

    @field_validator("narration_text", "objective")
    @classmethod
    def _text_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("scene brief text must not be blank")
        return value

    @field_validator("plan_path")
    @classmethod
    def _plan_path_must_be_safe_relative(cls, value: str) -> str:
        if not _is_safe_relative_path(value):
            raise ValueError("scene brief plan_path must be a safe relative path")
        return value

    @model_validator(mode="after")
    def _timing_must_be_consistent(self) -> SceneBrief:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("scene brief end must be after start")
        if abs(self.end_seconds - self.start_seconds - self.duration_seconds) > 1e-6:
            raise ValueError("scene brief duration must match its interval")
        return self

    def to_document(self) -> dict[str, object]:
        """Return the JSON-ready temporal brief document."""

        serialized: object = json.loads(self.model_dump_json())
        if not isinstance(serialized, dict):
            raise ValueError("scene brief document must be a JSON object")
        return {key: value for key, value in serialized.items() if isinstance(key, str)}


class SilenceDetector(Protocol):
    """Replaceable boundary that returns strict pause intervals."""

    def __call__(self, path: Path) -> Sequence[PauseInterval]:
        """Detect silence intervals in one copied narration file."""


class SilenceSubprocessRun(Protocol):
    """Replaceable local FFmpeg process boundary for silence detection."""

    def __call__(
        self,
        args: Sequence[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        """Run FFmpeg and return captured process facts."""


class FFmpegSilenceDetector:
    """Detect silence intervals through the local FFmpeg filter."""

    def __init__(
        self,
        *,
        subprocess_run: SilenceSubprocessRun | None = None,
        noise_db: float = -35.0,
        minimum_duration_seconds: float = 0.2,
    ) -> None:
        if not math.isfinite(noise_db):
            raise ValueError("silence detector noise threshold must be finite")
        if not math.isfinite(minimum_duration_seconds) or minimum_duration_seconds <= 0:
            raise ValueError("silence detector minimum duration must be positive")
        self._subprocess_run = subprocess_run or _run_ffmpeg_silence
        self.noise_db = noise_db
        self.minimum_duration_seconds = minimum_duration_seconds

    def __call__(self, path: Path) -> tuple[PauseInterval, ...]:
        candidate = path.resolve()
        if not candidate.is_file():
            raise ValueError(f"silence detector audio does not exist: {candidate}")
        argv = [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-loglevel",
            "info",
            "-i",
            str(candidate),
            "-af",
            f"silencedetect=noise={self.noise_db:g}dB:d={self.minimum_duration_seconds:g}",
            "-f",
            "null",
            "-",
        ]
        try:
            result = self._subprocess_run(
                argv,
                capture_output=True,
                text=True,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError(f"ffmpeg silence detection failed: {exc}") from exc
        if result.returncode != 0:
            detail = _process_text(result.stderr) or _process_text(result.stdout)
            suffix = f": {detail.strip()}" if detail.strip() else ""
            raise ValueError(f"ffmpeg silence detection failed{suffix}")
        return _parse_silence_output(
            f"{_process_text(result.stdout)}\n{_process_text(result.stderr)}"
        )


class TimelineSegment(BaseModel):
    """One ordered narration interval and its visual-plan reference."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: str = Field(pattern=_ID.pattern)
    order: int = Field(ge=1)
    narration_text: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    start_seconds: float = Field(ge=0.0)
    end_seconds: float = Field(gt=0.0)
    target_duration_seconds: float = Field(gt=0.0)
    start_provenance: BoundaryProvenance
    end_provenance: BoundaryProvenance
    transitions: list[str] = Field(default_factory=list)
    expectations: dict[str, object] = Field(default_factory=dict)
    plan_path: str

    @field_validator("narration_text", "objective")
    @classmethod
    def _text_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("timeline text must not be blank")
        return value

    @field_validator("plan_path")
    @classmethod
    def _plan_path_must_be_safe_relative(cls, value: str) -> str:
        if not _is_safe_relative_path(value):
            raise ValueError("timeline plan_path must be a safe relative path")
        return value

    @model_validator(mode="after")
    def _timing_must_be_consistent(self) -> TimelineSegment:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("timeline segment end must be after start")
        interval = self.end_seconds - self.start_seconds
        if abs(interval - self.target_duration_seconds) > 1e-6:
            raise ValueError("timeline target duration must match its interval")
        return self

    @property
    def duration_seconds(self) -> float:
        """Return the interval duration derived from its boundaries."""

        return self.end_seconds - self.start_seconds


class Timeline(BaseModel):
    """Versioned, validated narration timeline."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["timeline/1"] = "timeline/1"
    duration_seconds: float = Field(gt=0.0)
    status: TimelineStatus
    method: TimelineMethod
    segments: list[TimelineSegment] = Field(min_length=1)
    tolerance_seconds: float = Field(default=0.001, ge=0.0)
    warnings: list[str] = Field(default_factory=list)
    manual_review_reasons: list[str] = Field(default_factory=list)
    pause_search_tolerance_seconds: float | None = Field(default=None, ge=0.0)

    @model_validator(mode="after")
    def _ordered_segments_cover_audio(self) -> Timeline:
        expected_orders = list(range(1, len(self.segments) + 1))
        if [segment.order for segment in self.segments] != expected_orders:
            raise ValueError("timeline segment orders must be contiguous and authored")
        if self.method == "explicit_timestamp":
            if any(
                segment.start_provenance != "explicit_timestamp"
                or segment.end_provenance != "explicit_timestamp"
                for segment in self.segments
            ):
                raise ValueError("explicit timeline boundaries require explicit provenance")
        first = self.segments[0]
        if abs(first.start_seconds) > self.tolerance_seconds:
            raise ValueError("timeline must start at zero")
        for previous, current in zip(self.segments, self.segments[1:], strict=False):
            if abs(current.start_seconds - previous.end_seconds) > self.tolerance_seconds:
                raise ValueError("timeline cannot contain gaps or overlaps")
        last = self.segments[-1]
        if abs(last.end_seconds - self.duration_seconds) > self.tolerance_seconds:
            raise ValueError("timeline must end at audio duration")
        return self

    def to_document(self) -> dict[str, object]:
        """Return a JSON-ready timeline document."""

        serialized: object = json.loads(self.model_dump_json())
        if not isinstance(serialized, dict):
            raise ValueError("timeline document must be a JSON object")
        return {key: value for key, value in serialized.items() if isinstance(key, str)}


@dataclass(frozen=True, slots=True)
class ExplicitScene:
    """Parsed scene section with authoritative explicit timestamps."""

    id: str
    title: str
    objective: str
    narration_text: str
    start_seconds: float | None
    end_seconds: float | None


def parse_explicit_timeline(script: str) -> tuple[ExplicitScene, ...] | None:
    """Parse explicit ``#``/``##`` timestamp sections, if present."""

    parsed, explicit_sections = _parse_sections(script)
    if not explicit_sections:
        return None
    if any(scene.start_seconds is None or scene.end_seconds is None for scene in parsed):
        raise ValueError("every scene requires explicit @start and @end timestamps")
    return parsed


def parse_heading_sections(script: str) -> tuple[ExplicitScene, ...] | None:
    """Parse heading- or block-delimited sections without inferring timing."""

    parsed, explicit_sections = _parse_sections(script)
    if explicit_sections:
        raise ValueError("heading sections contain explicit timestamps")
    return parsed or None


def _parse_sections(script: str) -> tuple[tuple[ExplicitScene, ...], bool]:
    lines = script.splitlines()
    heading_indexes = [
        (index, match.group(1))
        for index, line in enumerate(lines)
        if (match := _HEADING.fullmatch(line)) is not None
    ]
    if not heading_indexes:
        blocks: list[list[str]] = []
        current_block: list[str] = []
        for line in lines:
            if line.strip():
                current_block.append(line)
            elif current_block:
                blocks.append(current_block)
                current_block = []
        if current_block:
            blocks.append(current_block)
        parsed_blocks = tuple(
            ExplicitScene(
                id=f"scene-{index:02d}",
                title=f"Scene {index:02d}",
                objective=next(line.strip() for line in block if line.strip()),
                narration_text="\n".join(block),
                start_seconds=None,
                end_seconds=None,
            )
            for index, block in enumerate(blocks, start=1)
        )
        return parsed_blocks, False

    parsed: list[ExplicitScene] = []
    seen_ids: dict[str, int] = {}
    explicit_sections = False
    for section_index, (heading_index, title) in enumerate(heading_indexes):
        end_index = (
            heading_indexes[section_index + 1][0]
            if section_index + 1 < len(heading_indexes)
            else len(lines)
        )
        metadata: dict[str, str] = {}
        cursor = heading_index + 1
        while cursor < end_index:
            line = lines[cursor]
            match = _METADATA.fullmatch(line)
            if match is None:
                if line.strip().startswith("@"):
                    raise ValueError(f"unsupported scene metadata: {line.strip()}")
                break
            metadata_key, metadata_value = line.split(":", maxsplit=1)
            key = metadata_key[1:]
            value = metadata_value.strip()
            if key in metadata:
                raise ValueError(f"duplicate scene metadata: @{key}")
            if not value.strip():
                raise ValueError(f"scene metadata @{key} must not be blank")
            metadata[key] = value
            cursor += 1

        narration_text = "\n".join(lines[cursor:end_index]).strip("\r\n")
        if not narration_text.strip():
            raise ValueError(f"scene {title!r} narration body must not be blank")
        if "start" not in metadata or "end" not in metadata:
            if "start" in metadata or "end" in metadata:
                raise ValueError(f"scene {title!r} requires both @start and @end")
            parsed.append(
                ExplicitScene(
                    id=_scene_id(title, seen_ids),
                    title=title,
                    objective=metadata.get("objective", title),
                    narration_text=narration_text,
                    start_seconds=None,
                    end_seconds=None,
                )
            )
            continue
        explicit_sections = True
        start_seconds = _parse_timestamp(metadata["start"])
        end_seconds = _parse_timestamp(metadata["end"])
        if end_seconds <= start_seconds:
            raise ValueError(f"scene {title!r} end must be after start")
        parsed.append(
            ExplicitScene(
                id=_scene_id(title, seen_ids),
                title=title,
                objective=metadata.get("objective", title),
                narration_text=narration_text,
                start_seconds=start_seconds,
                end_seconds=end_seconds,
            )
        )

    return tuple(parsed), explicit_sections


def build_explicit_timeline(
    script: str,
    audio_duration: float,
    *,
    theme: VideoTheme,
) -> tuple[Timeline, tuple[ScenePlan, ...]] | None:
    """Build one confirmed timeline and its existing visual ScenePlans."""

    scenes = parse_explicit_timeline(script)
    if scenes is None:
        return None
    plans: list[ScenePlan] = []
    segments: list[TimelineSegment] = []
    for order, scene in enumerate(scenes, start=1):
        if scene.start_seconds is None or scene.end_seconds is None:
            raise ValueError("every scene requires explicit @start and @end timestamps")
        start_seconds = scene.start_seconds
        end_seconds = scene.end_seconds
        duration = end_seconds - start_seconds
        scene_name = _scene_name(scene.title)
        plan_path = f"scenes/{order:02d}_{scene.id}/plan.json"
        plan = ScenePlan(
            id=scene.id,
            scene_name=scene_name,
            objective=scene.objective,
            duration_seconds=duration,
            theme=theme,
            narration_text=scene.narration_text,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
        )
        plans.append(plan)
        segments.append(
            TimelineSegment(
                id=scene.id,
                order=order,
                narration_text=scene.narration_text,
                objective=scene.objective,
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                target_duration_seconds=duration,
                start_provenance="explicit_timestamp",
                end_provenance="explicit_timestamp",
                plan_path=plan_path,
            )
        )
    timeline = Timeline(
        duration_seconds=audio_duration,
        status="confirmed",
        method="explicit_timestamp",
        segments=segments,
    )
    return timeline, tuple(plans)


def build_pause_aligned_timeline(
    script: str,
    audio_duration: float,
    pauses: Sequence[PauseInterval],
    *,
    theme: VideoTheme,
    search_tolerance_seconds: float = PAUSE_SEARCH_TOLERANCE_SECONDS,
) -> tuple[Timeline, tuple[ScenePlan, ...]] | None:
    """Build a reviewable candidate by aligning weighted scene targets to pauses.

    The central search tolerance is one second.  For each internal weighted
    word-count target, the closest pause midpoint is selected; ties prefer the
    earlier midpoint and then the original detector order.  A boundary without
    an eligible pause uses proportional fallback independently of other
    boundaries.
    """

    scenes = parse_heading_sections(script)
    if scenes is None:
        return None
    if search_tolerance_seconds < 0 or not math.isfinite(search_tolerance_seconds):
        raise ValueError("pause search tolerance must be finite and non-negative")
    if any(scene.start_seconds is not None or scene.end_seconds is not None for scene in scenes):
        raise ValueError("pause alignment requires headings without explicit timestamps")
    normalized_pauses = tuple(pauses)
    word_counts = [len(scene.narration_text.split()) for scene in scenes]
    total_words = sum(word_counts)
    if total_words <= 0:
        raise ValueError("pause alignment requires non-empty narration text")
    targets = [
        audio_duration * sum(word_counts[:index]) / total_words
        for index in range(1, len(scenes))
    ]
    boundaries = [0.0]
    boundary_provenance: list[BoundaryProvenance] = []
    used_indexes: set[int] = set()
    used_pause = False
    used_fallback = False
    for target_index, target in enumerate(targets):
        eligible: list[tuple[int, PauseInterval]] = [
            (index, pause)
            for index, pause in enumerate(normalized_pauses)
            if index not in used_indexes
            and boundaries[-1] < pause.midpoint_seconds < audio_duration
            and abs(pause.midpoint_seconds - target) <= search_tolerance_seconds
        ]
        if not eligible:
            remaining_boundaries = len(targets) - target_index
            boundary = _proportional_boundary(
                target,
                previous=boundaries[-1],
                audio_duration=audio_duration,
                remaining_boundaries=remaining_boundaries,
            )
            boundaries.append(boundary)
            boundary_provenance.append("proportional_fallback")
            used_fallback = True
            continue
        selected_index, selected_pause = _select_pause(eligible, target)
        used_indexes.add(selected_index)
        boundaries.append(selected_pause.midpoint_seconds)
        boundary_provenance.append("pause_aligned")
        used_pause = True

    boundaries.append(audio_duration)
    if used_pause and used_fallback:
        method: TimelineMethod = "pause_aligned"
        limitation = (
            "Mixed timing: some boundaries use pause-aligned midpoints and others "
            "use proportional fallback. Timing is approximate. Spoken-content "
            "correspondence remains unverified without ASR or forced alignment."
        )
    elif used_pause:
        method = "pause_aligned"
        limitation = (
            "Timing is approximate; spoken-content correspondence is unverified "
            "without ASR or forced alignment."
        )
    else:
        method = "proportional_fallback"
        limitation = (
            "Silence pauses were unavailable for every internal boundary. Timing "
            "is proportional and approximate. Spoken-content correspondence "
            "remains unverified without ASR or forced alignment."
        )
    endpoint_provenance: BoundaryProvenance = (
        "pause_aligned" if used_pause else "proportional_fallback"
    )
    boundary_provenance.insert(0, endpoint_provenance)
    boundary_provenance.append(endpoint_provenance)

    if len(boundary_provenance) != len(boundaries):
        raise RuntimeError("timeline boundary provenance is incomplete")

    plans: list[ScenePlan] = []
    segments: list[TimelineSegment] = []
    for order, (scene, start, end) in enumerate(
        zip(scenes, boundaries[:-1], boundaries[1:], strict=True),
        start=1,
    ):
        duration = end - start
        scene_name = _scene_name(scene.title)
        plan_path = f"scenes/{order:02d}_{scene.id}/plan.json"
        plan = ScenePlan(
            id=scene.id,
            scene_name=scene_name,
            objective=scene.objective,
            duration_seconds=duration,
            theme=theme,
            narration_text=scene.narration_text,
            start_seconds=start,
            end_seconds=end,
        )
        plans.append(plan)
        segments.append(
            TimelineSegment(
                id=scene.id,
                order=order,
                narration_text=scene.narration_text,
                objective=scene.objective,
                start_seconds=start,
                end_seconds=end,
                target_duration_seconds=duration,
                start_provenance=boundary_provenance[order - 1],
                end_provenance=boundary_provenance[order],
                plan_path=plan_path,
            )
        )
    timeline = Timeline(
        duration_seconds=audio_duration,
        status="candidate",
        method=method,
        segments=segments,
        warnings=[limitation],
        manual_review_reasons=[limitation],
        pause_search_tolerance_seconds=search_tolerance_seconds,
    )
    return timeline, tuple(plans)


def load_timeline(path: str | Path) -> Timeline:
    """Load and validate one UTF-8 timeline document."""

    return Timeline.model_validate_json(Path(path).read_text(encoding="utf-8"))


def confirm_timeline(timeline: Timeline) -> Timeline:
    """Revalidate a candidate with strict coverage before confirming it."""

    document = timeline.to_document()
    document["status"] = "confirmed"
    return Timeline.model_validate(document)


def _pause_selection_key(
    item: tuple[int, PauseInterval],
    target: float,
) -> tuple[float, float, int]:
    index, pause = item
    return (abs(pause.midpoint_seconds - target), pause.midpoint_seconds, index)


def _select_pause(
    eligible: list[tuple[int, PauseInterval]],
    target: float,
) -> tuple[int, PauseInterval]:
    selected = eligible[0]
    selected_key = _pause_selection_key(selected, target)
    for candidate in eligible[1:]:
        candidate_key = _pause_selection_key(candidate, target)
        if candidate_key < selected_key:
            selected = candidate
            selected_key = candidate_key
    return selected


def _proportional_boundary(
    target: float,
    *,
    previous: float,
    audio_duration: float,
    remaining_boundaries: int,
) -> float:
    """Keep a fallback boundary proportional while preserving positive intervals."""

    epsilon = 1e-6
    lower = previous + epsilon
    upper = audio_duration - (remaining_boundaries - 1) * epsilon
    if upper <= lower:
        raise ValueError("timeline fallback cannot preserve positive intervals")
    return min(max(target, lower), upper)


_SILENCE_START = re.compile(
    r"\bsilence_start:\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
)
_SILENCE_END = re.compile(
    r"\bsilence_end:\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
)


def _parse_silence_output(output: str) -> tuple[PauseInterval, ...]:
    """Parse ordered FFmpeg silence events into strict intervals."""

    pending_start: float | None = None
    intervals: list[PauseInterval] = []
    saw_marker = False
    for line in output.splitlines():
        start_match = _SILENCE_START.search(line)
        end_match = _SILENCE_END.search(line)
        if start_match is not None:
            saw_marker = True
            if pending_start is not None:
                raise ValueError("ffmpeg silence output has consecutive silence_start events")
            pending_start = float(start_match.group(1))
        if end_match is not None:
            saw_marker = True
            if pending_start is None:
                raise ValueError("ffmpeg silence output has silence_end without silence_start")
            end_seconds = float(end_match.group(1))
            try:
                intervals.append(
                    PauseInterval(
                        start_seconds=pending_start,
                        end_seconds=end_seconds,
                    )
                )
            except ValueError as exc:
                raise ValueError("ffmpeg silence output contains an invalid interval") from exc
            pending_start = None
    if pending_start is not None:
        raise ValueError("ffmpeg silence output has an unclosed silence_start event")
    if not saw_marker and any(
        marker in output for marker in ("silence_start", "silence_end")
    ):
        raise ValueError("ffmpeg silence output is unparseable")
    return tuple(intervals)


def _run_ffmpeg_silence(
    args: Sequence[str],
    *,
    capture_output: bool,
    text: bool,
    check: bool,
) -> subprocess.CompletedProcess[str]:
    if not text:
        raise ValueError("FFmpeg silence detection requires text process output")
    return subprocess.run(
        list(args),
        capture_output=capture_output,
        text=True,
        check=check,
    )


def _process_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _parse_timestamp(value: str) -> float:
    text = value.strip()
    try:
        if ":" not in text:
            seconds = float(text)
        else:
            parts = text.split(":")
            if len(parts) != 3:
                raise ValueError
            hours = float(parts[0])
            minutes = float(parts[1])
            seconds = hours * 3600.0 + minutes * 60.0 + float(parts[2])
            if hours < 0 or minutes < 0 or minutes >= 60 or float(parts[2]) < 0:
                raise ValueError
    except ValueError as exc:
        raise ValueError(f"invalid timestamp: {value!r}") from exc
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError(f"invalid timestamp: {value!r}")
    return seconds


def _scene_id(title: str, seen: dict[str, int]) -> str:
    normalized = unicodedata.normalize("NFKD", title)
    ascii_title = normalized.encode("ascii", "ignore").decode("ascii")
    base = re.sub(r"[^a-z0-9]+", "-", ascii_title.lower()).strip("-") or "scene"
    if base[0].isdigit():
        base = f"scene-{base}"
    seen[base] = seen.get(base, 0) + 1
    return base if seen[base] == 1 else f"{base}-{seen[base]}"


def _scene_name(title: str) -> str:
    normalized = unicodedata.normalize("NFKD", title)
    ascii_title = normalized.encode("ascii", "ignore").decode("ascii")
    words = [
        part
        for part in "".join(
            character if character.isalnum() else " " for character in ascii_title
        ).split()
        if part
    ]
    name = "".join(word[:1].upper() + word[1:] for word in words) or "Scene"
    if not name[0].isalpha():
        name = f"Scene{name}"
    return f"{name}Scene"


def _is_safe_relative_path(value: str) -> bool:
    if not value or value.startswith("/") or "\\" in value:
        return False
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return False
    return not PurePosixPath(value).is_absolute()


__all__ = [
    "BoundaryProvenance",
    "ExplicitScene",
    "FFmpegSilenceDetector",
    "PAUSE_SEARCH_TOLERANCE_SECONDS",
    "PauseInterval",
    "SceneBrief",
    "SilenceDetector",
    "SilenceSubprocessRun",
    "Timeline",
    "TimelineMethod",
    "TimelineSegment",
    "TimelineStatus",
    "build_pause_aligned_timeline",
    "build_explicit_timeline",
    "confirm_timeline",
    "load_timeline",
    "parse_heading_sections",
    "parse_explicit_timeline",
]
