"""Strict, immutable scene specification boundary."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from video_pipeline.expectations import SceneExpectations
from video_pipeline.project import (
    AudioMediaFacts,
    Project,
    ProjectSceneRef,
    ProjectStageState,
    ProjectState,
    load_project,
)
from video_pipeline.reference_catalog import ReferenceTopic
from video_pipeline.scene_plan import ScenePlan
from video_pipeline.timeline import (
    PauseInterval,
    SilenceDetector,
    Timeline,
    TimelineSegment,
    load_timeline,
)


class SceneSpec(BaseModel):
    """One authored scene in a video."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    scene_name: str = Field(pattern=r"^[A-Z][A-Za-z0-9_]*$")
    description: str
    # Optional for existing render-only manifests; when supplied this is the
    # authoritative visual contract sent to the coder and quality critics.
    plan: ScenePlan | None = None
    # Optional: without it the pipeline keeps the renderability-only contract.
    expect: SceneExpectations | None = None
    # Explicit topics select a small authorized 3b1b-derived Community corpus.
    topics: list[ReferenceTopic] = Field(default_factory=list, max_length=4)
    # Zero disables local few-shot references for this scene.
    reference_examples: int = Field(default=2, ge=0, le=3)

    @field_validator("description")
    @classmethod
    def _description_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("description must not be blank")
        return value

    @field_validator("topics")
    @classmethod
    def _topics_must_be_unique(cls, value: list[ReferenceTopic]) -> list[ReferenceTopic]:
        if len(value) != len(set(value)):
            raise ValueError("topics must be unique")
        return value

    @model_validator(mode="after")
    def _plan_identity_matches_scene(self) -> SceneSpec:
        if self.plan is not None:
            if self.plan.id != self.id or self.plan.scene_name != self.scene_name:
                raise ValueError("scene plan identity must match scene identity")
        return self


__all__ = [
    "AudioMediaFacts",
    "Project",
    "ProjectSceneRef",
    "ProjectStageState",
    "ProjectState",
    "PauseInterval",
    "SceneSpec",
    "SilenceDetector",
    "Timeline",
    "TimelineSegment",
    "load_project",
    "load_timeline",
]
