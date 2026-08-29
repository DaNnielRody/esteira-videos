"""Check an observed storyboard against what the scene specification asked for.

A beat is one thing the finished video must show, in the order written.  Beats
are matched as a subsequence of the sampled frames, so the checker constrains
what happens and in which order, not exactly when.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from video_pipeline.observation import FrameObservation, ObservedShape

Shape = Literal["circle", "square", "polygon", "any"]
Region = Literal["left", "center", "right", "top", "middle", "bottom"]
Direction = Literal["left", "right", "up", "down"]
Colour = Literal[
    "red", "orange", "yellow", "green", "teal", "blue", "purple", "pink",
    "white", "grey", "black",
]

# A frame is split into thirds; the middle third is "center"/"middle".
_LOW = 1.0 / 3.0
_HIGH = 2.0 / 3.0
# Motion smaller than this is sampling noise, not a move the viewer sees.
_MIN_MOTION = 0.02


class SceneBeat(BaseModel):
    """One observable moment the rendered video must contain."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    shape: Shape = "any"
    color: Colour | None = None
    region: Region | None = None
    moved: Direction | None = None

    def describe(self) -> str:
        """Name this beat the way a diagnostic should read."""

        noun = self.shape if self.shape != "any" else "shape"
        parts = [f"a {self.color} {noun}" if self.color else f"a {noun}"]
        if self.region is not None:
            parts.append(f"in the {self.region} of the frame")
        if self.moved is not None:
            parts.append(f"moved {self.moved} from the previous beat")
        return " ".join(parts)


class LatexExpectation(BaseModel):
    """One formula whose typography is fixed enough for deterministic sensing."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    tex: str
    font_size: int = Field(ge=12, le=144)
    color: Colour
    x: float = Field(ge=-7.0, le=7.0)
    y: float = Field(ge=-4.0, le=4.0)
    min_iou: float = Field(default=0.95, ge=0.5, le=1.0)

    @field_validator("tex")
    @classmethod
    def _tex_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("latex tex must not be blank")
        return value


class TextExpectation(BaseModel):
    """Fixed Pango Text or LaTeX Tex content for deterministic sensing."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    renderer: Literal["text", "tex"]
    content: str
    font: str | None = None
    font_size: int = Field(ge=12, le=144)
    color: Colour
    x: float = Field(ge=-7.0, le=7.0)
    y: float = Field(ge=-4.0, le=4.0)
    min_iou: float = Field(default=0.95, ge=0.5, le=1.0)

    @field_validator("content")
    @classmethod
    def _content_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text content must not be blank")
        return value

    @model_validator(mode="after")
    def _renderer_has_fixed_font_contract(self) -> TextExpectation:
        if self.renderer == "text" and (self.font is None or not self.font.strip()):
            raise ValueError("text renderer requires an explicit font")
        if self.renderer == "tex" and self.font is not None:
            raise ValueError("tex renderer does not accept a Pango font")
        return self


class SceneExpectations(BaseModel):
    """The complete semantic contract for one rendered scene."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    max_shapes: int = Field(default=1, ge=1)
    beats: list[SceneBeat] = Field(default_factory=list)
    latex: list[LatexExpectation] = Field(default_factory=list)
    text: list[TextExpectation] = Field(default_factory=list)


def check_expectations(
    frames: list[FrameObservation],
    expectations: SceneExpectations,
) -> list[str]:
    """Return every way the observed storyboard fails the declared contract."""

    if not frames:
        return ["no frame could be read back from the rendered video"]

    reasons = _budget_reasons(frames, expectations.max_shapes)
    reasons.extend(_beat_reasons(frames, expectations.beats))
    return reasons


def _shape_count(frame: FrameObservation) -> int:
    return len(frame.shapes)


def _budget_reasons(frames: list[FrameObservation], max_shapes: int) -> list[str]:
    """Reject a frame showing more shapes than the scene declared."""

    worst = max(frames, key=_shape_count)
    if len(worst.shapes) <= max_shapes:
        return []
    kinds = ", ".join(f"{shape.color} {shape.kind}" for shape in worst.shapes)
    return [
        f"frame {worst.index} shows {len(worst.shapes)} shapes ({kinds}) but the scene "
        f"declares at most {max_shapes}; a duplicate usually means a mobject was "
        "animated without ever replacing the one already on screen"
    ]


def _beat_reasons(
    frames: list[FrameObservation],
    beats: list[SceneBeat],
) -> list[str]:
    """Match every beat forward through the sampled frames, in order."""

    cursor = 0
    previous: ObservedShape | None = None
    for position, beat in enumerate(beats, start=1):
        match = _find(frames, beat, start=cursor, previous=previous)
        if match is None:
            return [
                f"beat {position} was never observed: expected {beat.describe()}; "
                f"observed instead {_summarize(frames[cursor:])}"
            ]
        frame_index, shape = match
        cursor = frame_index + 1
        previous = shape
    return []


def _find(
    frames: list[FrameObservation],
    beat: SceneBeat,
    *,
    start: int,
    previous: ObservedShape | None,
) -> tuple[int, ObservedShape] | None:
    for index in range(start, len(frames)):
        for shape in frames[index].shapes:
            if _matches(shape, beat, previous):
                return index, shape
    return None


def _matches(
    shape: ObservedShape,
    beat: SceneBeat,
    previous: ObservedShape | None,
) -> bool:
    if beat.shape != "any" and shape.kind != beat.shape:
        return False
    if beat.color is not None and shape.color != beat.color:
        return False
    if beat.region is not None and not _in_region(shape, beat.region):
        return False
    if beat.moved is not None and not _has_moved(shape, previous, beat.moved):
        return False
    return True


def _in_region(shape: ObservedShape, region: Region) -> bool:
    if region == "left":
        return shape.center_x < _LOW
    if region == "right":
        return shape.center_x > _HIGH
    if region == "center":
        return _LOW <= shape.center_x <= _HIGH
    if region == "top":
        return shape.center_y < _LOW
    if region == "bottom":
        return shape.center_y > _HIGH
    return _LOW <= shape.center_y <= _HIGH


def _has_moved(
    shape: ObservedShape,
    previous: ObservedShape | None,
    direction: Direction,
) -> bool:
    if previous is None:
        return False
    horizontal = shape.center_x - previous.center_x
    # Image rows grow downward, so a smaller centre_y is higher on screen.
    vertical = previous.center_y - shape.center_y
    if direction == "right":
        return horizontal >= _MIN_MOTION
    if direction == "left":
        return horizontal <= -_MIN_MOTION
    if direction == "up":
        return vertical >= _MIN_MOTION
    return vertical <= -_MIN_MOTION


def _summarize(frames: list[FrameObservation]) -> str:
    """Describe what the remaining frames did show, for the correction prompt."""

    if not frames:
        return "no further frame"
    seen: list[str] = []
    for frame in frames:
        for shape in frame.shapes:
            entry = (
                f"{shape.color} {shape.kind} at "
                f"x={shape.center_x:.2f},y={shape.center_y:.2f}"
            )
            if entry not in seen:
                seen.append(entry)
    return ", ".join(seen) if seen else "an empty frame"


__all__ = [
    "LatexExpectation",
    "SceneBeat",
    "SceneExpectations",
    "TextExpectation",
    "check_expectations",
]
