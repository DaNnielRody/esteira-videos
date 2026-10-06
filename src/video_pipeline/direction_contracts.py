"""Versioned, opt-in observable promises from the approved visual direction."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DirectionWindow(BaseModel):
    """Scene-local seconds; evidence must cover the declared boundaries."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)

    start_seconds: float = Field(ge=0.0)
    end_seconds: float = Field(gt=0.0)

    @model_validator(mode="after")
    def _ordered(self) -> DirectionWindow:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("direction interval must have positive duration")
        return self


class TokenContract(DirectionWindow):
    """Text and container coexist on [start, end), then EXIT together."""

    text_id: str = Field(min_length=1)
    container_id: str = Field(min_length=1)
    padding: float = Field(default=0.01, ge=0.0, le=0.5)

    @model_validator(mode="after")
    def _distinct(self) -> TokenContract:
        if self.text_id == self.container_id:
            raise ValueError("token text and container must be distinct")
        return self


class AspectContract(DirectionWindow):
    """Expected axis-aligned width/height, corrected for camera frame dimensions."""

    object_id: str = Field(min_length=1)
    expected_ratio: float = Field(gt=0.0)
    relative_tolerance: float = Field(default=0.02, ge=0.0, le=0.1)


class ProcessingContract(DirectionWindow):
    """A recorded model animation must finish before its fresh output appears."""

    model_id: str = Field(min_length=1)
    output_id: str = Field(min_length=1)
    animation: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    output_by_seconds: float = Field(gt=0.0)

    @model_validator(mode="after")
    def _output_follows_processing(self) -> ProcessingContract:
        if self.output_by_seconds < self.end_seconds:
            raise ValueError("output deadline precedes processing completion")
        if self.model_id == self.output_id:
            raise ValueError("model and fresh output must have distinct IDs")
        return self


class DirectionContract(BaseModel):
    """Additive contract; legacy plans without it keep their existing behavior."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["visual.direction/1"] = "visual.direction/1"
    tokens: list[TokenContract] = Field(default_factory=list, max_length=64)
    aspects: list[AspectContract] = Field(default_factory=list, max_length=64)
    processing: list[ProcessingContract] = Field(default_factory=list, max_length=64)

    def validate_references(self, object_ids: set[str], duration: float) -> None:
        """Reject silently unobservable references and out-of-scene windows."""
        for token in self.tokens:
            if {token.text_id, token.container_id} - object_ids:
                raise ValueError("unknown object IDs in direction contract")
            if token.end_seconds > duration:
                raise ValueError("direction interval exceeds scene duration")
        for aspect in self.aspects:
            if aspect.object_id not in object_ids:
                raise ValueError("unknown object IDs in direction contract")
            if aspect.end_seconds > duration:
                raise ValueError("direction interval exceeds scene duration")
        for process in self.processing:
            if {process.model_id, process.output_id} - object_ids:
                raise ValueError("unknown object IDs in direction contract")
            if process.output_by_seconds > duration:
                raise ValueError("direction interval exceeds scene duration")
