"""Strict, immutable scene specification boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from video_pipeline.expectations import SceneExpectations


class SceneSpec(BaseModel):
    """The v1 input accepted by the pipeline for one scene."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["1.0"]
    scene_name: str = Field(pattern=r"^[A-Z][A-Za-z0-9_]*$")
    description: str
    # Optional: without it the pipeline keeps the renderability-only contract.
    expect: SceneExpectations | None = None

    @field_validator("description")
    @classmethod
    def _description_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("description must not be blank")
        return value


def load_scene_spec(path: str | Path) -> SceneSpec:
    """Load and validate a UTF-8 JSON scene specification."""

    document = Path(path).read_text(encoding="utf-8")
    return SceneSpec.model_validate_json(document)
