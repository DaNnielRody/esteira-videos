"""Explicit visual plans that precede generated Manim source code."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from video_pipeline.expectations import SceneExpectations
from video_pipeline.theme import DEFAULT_VIDEO_THEME, ColorRole, VideoTheme


class VisualObject(BaseModel):
    """One semantically named object the generated scene is expected to show."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    kind: str = Field(min_length=1, max_length=80)
    color_role: ColorRole | None = None
    semantic_role: str | None = None
    region: str | None = None
    text: str | None = None
    formula: str | None = None
    group_id: str | None = None
    required: bool = True
    visible: bool = True
    z_index: int = Field(default=0, ge=-1000, le=1000)
    overlap_policy: Literal["forbidden", "intentional", "transient", "group", "background"] = (
        "forbidden"
    )
    min_width: float | None = Field(default=None, gt=0.0)
    min_height: float | None = Field(default=None, gt=0.0)

    @field_validator("kind", "semantic_role", "region", "group_id")
    @classmethod
    def _optional_names_are_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("visual object names must not be blank")
        return value

    @field_validator("text", "formula")
    @classmethod
    def _content_is_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("visual object content must not be blank")
        return value

    @model_validator(mode="after")
    def _content_is_unambiguous(self) -> VisualObject:
        if self.text is not None and self.formula is not None:
            raise ValueError("visual object cannot declare both text and formula")
        return self


class Beat(BaseModel):
    """One ordered, measurable visual moment in a :class:`ScenePlan`."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]*$")
    action: str = Field(default="hold", min_length=1, max_length=80)
    objects: list[str] = Field(default_factory=list, max_length=64)
    start_seconds: float | None = Field(default=None, ge=0.0)
    end_seconds: float | None = Field(default=None, gt=0.0)
    duration_seconds: float | None = Field(default=None, gt=0.0)
    expected_state: dict[str, object] = Field(default_factory=dict)
    region: str | None = None
    highlight: ColorRole | None = None
    text: str | None = None
    formula: str | None = None
    movement: str | None = None
    min_read_seconds: float | None = Field(default=None, ge=0.0)

    @field_validator("action", "region", "movement")
    @classmethod
    def _names_are_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("beat labels must not be blank")
        return value

    @field_validator("objects")
    @classmethod
    def _objects_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("beat object IDs must be unique")
        if any(not item.strip() for item in value):
            raise ValueError("beat object IDs must not be blank")
        return value

    @model_validator(mode="after")
    def _interval_is_consistent(self) -> Beat:
        if self.end_seconds is not None and self.start_seconds is None:
            raise ValueError("beat end_seconds requires start_seconds")
        if self.start_seconds is not None and self.end_seconds is not None:
            if self.end_seconds <= self.start_seconds:
                raise ValueError("beat end_seconds must be after start_seconds")
            interval = self.end_seconds - self.start_seconds
            if self.duration_seconds is not None and abs(interval - self.duration_seconds) > 1e-6:
                raise ValueError("beat duration_seconds must match its interval")
        if self.text is not None and self.formula is not None:
            raise ValueError("beat cannot declare both text and formula")
        return self

    @property
    def effective_duration(self) -> float:
        """Return the declared duration, or the interval length."""

        if self.duration_seconds is not None:
            return self.duration_seconds
        if self.start_seconds is not None and self.end_seconds is not None:
            return self.end_seconds - self.start_seconds
        return 0.0


class LayoutExpectation(BaseModel):
    """Expected semantic placement and density constraints for a scene."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    object_regions: dict[str, str] = Field(default_factory=dict)
    minimum_spacing: float | None = Field(default=None, gt=0.0, le=10.0)
    max_text_lines: int = Field(default=3, ge=1, le=20)
    max_content_density: float = Field(default=0.65, gt=0.0, le=1.0)
    allow_background_overlap: bool = True

    @field_validator("object_regions")
    @classmethod
    def _regions_are_named(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not key.strip() or not region.strip() for key, region in value.items()):
            raise ValueError("layout object regions must not be blank")
        return value


class ContinuityPlan(BaseModel):
    """Declarative continuity requirements at one scene boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    required: bool = False
    expected_transition: str = "default"
    allow_changes: list[
        Literal[
            "theme",
            "background",
            "camera",
            "scale",
            "objects",
            "colors",
            "typography",
            "persistent_elements",
            "transition",
        ]
    ] = Field(default_factory=list)
    recurring_object_ids: list[str] = Field(default_factory=list)

    @field_validator("expected_transition")
    @classmethod
    def _transition_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("expected transition must not be blank")
        return value

    @field_validator("allow_changes", "recurring_object_ids")
    @classmethod
    def _unique_items(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("continuity entries must be unique")
        return value


class ScenePlan(BaseModel):
    """The visual contract consumed before a provider can generate code."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["visual.scene-plan/1"] = "visual.scene-plan/1"
    id: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    scene_name: str = Field(pattern=r"^[A-Z][A-Za-z0-9_]*$")
    objective: str = Field(min_length=1, max_length=1000)
    duration_seconds: float = Field(gt=0.0, le=3600.0)
    narration_text: str | None = Field(default=None, min_length=1)
    start_seconds: float | None = Field(default=None, ge=0.0)
    end_seconds: float | None = Field(default=None, gt=0.0)
    theme: VideoTheme = Field(default_factory=lambda: DEFAULT_VIDEO_THEME)
    capabilities: list[str] = Field(default_factory=list, max_length=32)
    objects: list[VisualObject] = Field(default_factory=list, max_length=256)
    beats: list[Beat] = Field(default_factory=list, max_length=256)
    layout: LayoutExpectation = Field(default_factory=LayoutExpectation)
    expectations: SceneExpectations | None = None
    continuity_in: ContinuityPlan | None = None
    continuity_out: ContinuityPlan | None = None

    @field_validator("objective")
    @classmethod
    def _objective_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("scene objective must not be blank")
        return value

    @field_validator("narration_text")
    @classmethod
    def _narration_text_is_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("narration text must not be blank")
        return value

    @field_validator("capabilities")
    @classmethod
    def _capabilities_are_unique_and_named(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("capabilities must be unique")
        if any(not item.strip() for item in value):
            raise ValueError("capability IDs must not be blank")
        return value

    @field_validator("objects")
    @classmethod
    def _object_ids_are_unique(cls, value: list[VisualObject]) -> list[VisualObject]:
        ids = [item.id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("visual object IDs must be unique")
        return value

    @field_validator("beats")
    @classmethod
    def _beat_ids_are_unique(cls, value: list[Beat]) -> list[Beat]:
        ids = [item.id for item in value if item.id is not None]
        if len(ids) != len(set(ids)):
            raise ValueError("beat IDs must be unique")
        return value

    @model_validator(mode="after")
    def _references_and_timing_are_valid(self) -> ScenePlan:
        if self.end_seconds is not None and self.start_seconds is None:
            raise ValueError("end_seconds requires start_seconds")
        if self.start_seconds is not None and self.end_seconds is not None:
            if self.end_seconds <= self.start_seconds:
                raise ValueError("end_seconds must be after start_seconds")
            interval = self.end_seconds - self.start_seconds
            if abs(interval - self.duration_seconds) > 1e-6:
                raise ValueError("scene duration must match its narration interval")
        object_ids = {item.id for item in self.objects}
        for beat in self.beats:
            unknown = set(beat.objects) - object_ids
            if unknown:
                raise ValueError(f"unknown object IDs in beat: {', '.join(sorted(unknown))}")
            if beat.region is not None and beat.region not in self.theme.regions:
                raise ValueError(f"unknown beat region: {beat.region}")
        unknown_layout = set(self.layout.object_regions) - object_ids
        if unknown_layout:
            raise ValueError(f"unknown object IDs in layout: {', '.join(sorted(unknown_layout))}")
        for boundary_name, boundary in (
            ("continuity_in", self.continuity_in),
            ("continuity_out", self.continuity_out),
        ):
            if boundary is None:
                continue
            unknown_recurring = set(boundary.recurring_object_ids) - object_ids
            if unknown_recurring:
                raise ValueError(
                    f"unknown object IDs in {boundary_name}: {', '.join(sorted(unknown_recurring))}"
                )
        starts: list[float] = []
        for beat in self.beats:
            if beat.start_seconds is not None:
                starts.append(beat.start_seconds)
        if starts != sorted(starts):
            raise ValueError("beats must be ordered by start_seconds")
        if self.total_beat_duration > self.duration_seconds + 1e-6:
            raise ValueError("beat durations exceed scene duration")
        return self

    @property
    def total_beat_duration(self) -> float:
        """Sum explicit beat durations; un-timed holds contribute no budget."""

        return sum(beat.effective_duration for beat in self.beats)

    def color_for(self, object_id: str) -> str:
        """Resolve an object's semantic colour through the scene theme."""

        for item in self.objects:
            if item.id == object_id:
                if item.color_role is None:
                    return self.theme.text_color
                return self.theme.color(item.color_role)
        raise KeyError(object_id)

    def to_document(self) -> dict[str, object]:
        """Return the versioned JSON-ready plan document."""

        # Pydantic's JSON serializer is dynamically typed; validation has
        # already completed, so this is the sole serialization boundary.
        return {
            "schema_version": "visual.scene-plan/1",
            **self.model_dump(mode="json"),  # type: ignore[misc]
        }


__all__ = [
    "Beat",
    "ContinuityPlan",
    "LayoutExpectation",
    "ScenePlan",
    "VisualObject",
]
