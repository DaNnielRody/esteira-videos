"""Optional, replaceable multimodal review boundary.

No model adapter lives here.  Deterministic critics remain authoritative; a
future local reviewer may return only validated warnings through this seam.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from video_pipeline.quality import QualityFinding
from video_pipeline.runtime import ObservedScene
from video_pipeline.scene_plan import ScenePlan


class VisualCritic(Protocol):
    """Replaceable reviewer receiving visual evidence and the authored plan."""

    def review(
        self,
        contact_sheet: Path,
        keyframes: Sequence[Path],
        scene_plan: ScenePlan,
        observed_facts: ObservedScene,
        deterministic_findings: Sequence[QualityFinding],
    ) -> object:
        """Return a JSON object or a validated visual review."""


class VisualReview(BaseModel):
    """Validated optional review; all findings are explicitly non-blocking."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: str = "visual.visual-review/1"
    summary: str = Field(default="", max_length=2000)
    findings: list[QualityFinding] = Field(default_factory=list)
    authoritative: bool = False

    def to_document(self) -> dict[str, object]:
        """Return the warning-only review document."""

        return self.model_dump(mode="json")


def parse_visual_review(document: object, *, scene_id: str) -> VisualReview:
    """Validate one reviewer response and downgrade all findings to warnings."""

    try:
        payload: object
        if isinstance(document, (str, bytes, bytearray)):
            payload = json.loads(document)
        else:
            payload = document
        if not isinstance(payload, Mapping):
            raise ValueError("visual review must be a JSON object")
        summary = payload.get("summary", "")
        findings_value = payload.get("findings", [])
        if not isinstance(summary, str) or not isinstance(findings_value, list):
            raise ValueError("visual review summary must be text and findings must be a list")
        findings: list[QualityFinding] = []
        for raw in findings_value:
            if not isinstance(raw, Mapping):
                raise ValueError("every visual review finding must be an object")
            item = dict(raw)
            item.setdefault("scene_id", scene_id)
            item["severity"] = "warning"
            findings.append(QualityFinding.model_validate(item))
        return VisualReview(summary=summary, findings=findings, authoritative=False)
    except (ValueError, TypeError, json.JSONDecodeError, ValidationError) as exc:
        return _invalid_review(scene_id, str(exc))


def run_visual_review(
    critic: VisualCritic,
    contact_sheet: str | Path,
    keyframes: Sequence[str | Path],
    scene_plan: ScenePlan,
    observed_facts: ObservedScene,
    *,
    deterministic_findings: Sequence[QualityFinding] = (),
) -> VisualReview:
    """Invoke one optional reviewer and preserve deterministic authority."""

    path = Path(contact_sheet)
    if not path.is_file():
        return _invalid_review(scene_plan.id, f"contact sheet is missing: {path}")
    frame_paths = tuple(Path(item) for item in keyframes)
    missing_frame = next((item for item in frame_paths if not item.is_file()), None)
    if missing_frame is not None:
        return _invalid_review(scene_plan.id, f"keyframe is missing: {missing_frame}")
    try:
        response = critic.review(
            path, frame_paths, scene_plan, observed_facts, deterministic_findings
        )
    except Exception as exc:
        return _invalid_review(scene_plan.id, str(exc))
    review = (
        response
        if isinstance(response, VisualReview)
        else parse_visual_review(response, scene_id=scene_plan.id)
    )
    if deterministic_findings:
        extra = [
            finding.model_copy(update={"severity": "warning"}) for finding in deterministic_findings
        ]
        review = review.model_copy(update={"findings": [*review.findings, *extra]})
    return review


def _invalid_review(scene_id: str, detail: str) -> VisualReview:
    return VisualReview(
        summary=(
            "Optional visual review was unavailable; deterministic critics remain authoritative."
        ),
        findings=[
            QualityFinding(
                code="MULTIMODAL_REVIEW_INVALID",
                severity="warning",
                scene_id=scene_id,
                observed={"error": detail},
                expected={"valid_json": True},
                explanation="The optional multimodal response could not be validated.",
                suggestion=(
                    "Inspect deterministic findings; retry the optional reviewer only "
                    "when available."
                ),
            )
        ],
        authoritative=False,
    )


__all__ = ["VisualCritic", "VisualReview", "parse_visual_review", "run_visual_review"]
