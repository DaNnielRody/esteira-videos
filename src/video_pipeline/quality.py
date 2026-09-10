"""Stable, serialisable findings emitted by deterministic visual critics."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FindingSeverity = Literal["failure", "warning"]


class QualityFinding(BaseModel):
    """One objective visual issue with evidence and a concrete correction."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", min_length=3, max_length=80)
    severity: FindingSeverity
    scene_id: str
    beat_id: str | None = None
    instant_seconds: float | None = Field(default=None, ge=0.0)
    object_ids: list[str] = Field(default_factory=list, max_length=64)
    object_id: str | None = None
    observed: dict[str, object] = Field(default_factory=dict)
    expected: dict[str, object] = Field(default_factory=dict)
    explanation: str = Field(min_length=1, max_length=2000)
    suggestion: str = Field(min_length=1, max_length=2000)

    @field_validator("scene_id", "beat_id", "object_id")
    @classmethod
    def _identifiers_are_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("finding identifiers must not be blank")
        return value

    @field_validator("object_ids")
    @classmethod
    def _object_ids_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("finding object IDs must be unique")
        if any(not item.strip() for item in value):
            raise ValueError("finding object IDs must not be blank")
        return value

    @field_validator("explanation", "suggestion")
    @classmethod
    def _messages_are_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("finding messages must not be blank")
        return value

    @model_validator(mode="after")
    def _normalise_object_reference(self) -> QualityFinding:
        if self.object_id is not None:
            if self.object_ids and self.object_ids != [self.object_id]:
                raise ValueError("object_id and object_ids disagree")
            if not self.object_ids:
                object.__setattr__(self, "object_ids", [self.object_id])  # type: ignore[misc]
        return self

    @property
    def is_failure(self) -> bool:
        """Whether this finding blocks deterministic scene acceptance."""

        return self.severity == "failure"

    def to_document(self) -> dict[str, object]:
        """Return the canonical JSON-ready finding."""

        document: dict[str, object] = {
            "code": self.code,
            "severity": self.severity,
            "scene_id": self.scene_id,
            "object_ids": list(self.object_ids),
            "observed": dict(self.observed),
            "expected": dict(self.expected),
            "explanation": self.explanation,
            "suggestion": self.suggestion,
        }
        if self.beat_id is not None:
            document["beat_id"] = self.beat_id
        if self.instant_seconds is not None:
            document["instant_seconds"] = self.instant_seconds
        if self.object_id is not None:
            document["object_id"] = self.object_id
        return document


class QualityReport(BaseModel):
    """A complete deterministic quality result for one scene attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["visual.quality-report/1"] = "visual.quality-report/1"
    scene_id: str
    attempt: int = Field(default=1, ge=1)
    findings: list[QualityFinding] = Field(default_factory=list)
    deterministic: bool = True

    @field_validator("scene_id")
    @classmethod
    def _scene_id_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("quality report scene_id must not be blank")
        return value

    @model_validator(mode="after")
    def _codes_are_stable(self) -> QualityReport:
        if any(re.fullmatch(r"[A-Z][A-Z0-9_]*", finding.code) is None for finding in self.findings):
            raise ValueError("quality finding codes must be stable uppercase identifiers")
        return self

    @property
    def has_failures(self) -> bool:
        """Whether at least one blocking finding exists."""

        return any(finding.is_failure for finding in self.findings)

    @property
    def accepted(self) -> bool:
        """Whether deterministic checks found no failures."""

        return not self.has_failures

    @property
    def failures(self) -> list[QualityFinding]:
        """Return only blocking findings."""

        return [finding for finding in self.findings if finding.is_failure]

    @property
    def warnings(self) -> list[QualityFinding]:
        """Return non-blocking findings."""

        return [finding for finding in self.findings if not finding.is_failure]

    def to_document(self) -> dict[str, object]:
        """Return a stable versioned JSON document."""

        return {
            "schema_version": self.schema_version,
            "scene_id": self.scene_id,
            "attempt": self.attempt,
            "deterministic": self.deterministic,
            "accepted": self.accepted,
            "findings": [finding.to_document() for finding in self.findings],
        }

    def to_prompt_payload(self) -> dict[str, object]:
        """Return the compact correction context sent to the local coder."""

        return self.to_document()


__all__ = ["FindingSeverity", "QualityFinding", "QualityReport"]
