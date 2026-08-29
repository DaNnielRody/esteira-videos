"""Shared contract for deterministic sensors used by the semantic gate."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, TypeVar


class SensorFailureCode(StrEnum):
    """Stable infrastructure failures emitted by every sensor."""

    DURATION_UNAVAILABLE = "duration_unavailable"
    FRAME_EXTRACTION_TIMEOUT = "frame_extraction_timeout"
    FRAME_EXTRACTION_FAILED = "frame_extraction_failed"
    FRAME_DECODE_FAILED = "frame_decode_failed"
    NO_FRAMES_EXTRACTED = "no_frames_extracted"
    LATEX_REFERENCE_RENDER_FAILED = "latex_reference_render_failed"
    LATEX_VALIDATOR_EXCEPTION = "latex_validator_exception"
    OBSERVER_EXCEPTION = "observer_exception"


@dataclass(frozen=True, slots=True)
class SensorFailure:
    """A sensor failed to produce evidence; this is not a scene verdict."""

    code: SensorFailureCode
    detail: str


Evidence = TypeVar("Evidence")


@dataclass(frozen=True, slots=True)
class SensorResult(Generic[Evidence]):
    """Exactly one successful evidence value or one explicit sensor failure."""

    evidence: Evidence | None
    failure: SensorFailure | None

    def __post_init__(self) -> None:
        if (self.evidence is None) == (self.failure is None):
            raise ValueError("sensor result requires exactly one of evidence or failure")

    @classmethod
    def success(cls, evidence: Evidence) -> SensorResult[Evidence]:
        """Build a successful sensor observation, including empty collections."""

        return cls(evidence=evidence, failure=None)

    @classmethod
    def failed(cls, failure: SensorFailure) -> SensorResult[Evidence]:
        """Build an explicit infrastructure failure without fake evidence."""

        return cls(evidence=None, failure=failure)


__all__ = ["SensorFailure", "SensorFailureCode", "SensorResult"]
