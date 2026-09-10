"""Fake-only optional multimodal review contract."""

from __future__ import annotations

from pathlib import Path

from video_pipeline.multimodal import VisualReview, parse_visual_review, run_visual_review
from video_pipeline.runtime import ObservedScene
from video_pipeline.scene_plan import ScenePlan


def _plan() -> ScenePlan:
    return ScenePlan(
        id="review",
        scene_name="ReviewScene",
        objective="Show a reviewable visual.",
        duration_seconds=2.0,
    )


def _observed() -> ObservedScene:
    return ObservedScene(scene_id="review", scene_name="ReviewScene")


class FakeCritic:
    def review(
        self,
        contact_sheet: Path,
        keyframes: tuple[Path, ...],
        scene_plan: ScenePlan,
        observed_facts: ObservedScene,
        deterministic_findings: tuple[object, ...],
    ) -> dict[str, object]:
        assert contact_sheet.name == "contact.png"
        assert keyframes == ()
        assert scene_plan.id == observed_facts.scene_id
        assert deterministic_findings == ()
        return {
            "summary": "The composition is readable.",
            "findings": [
                {
                    "code": "AESTHETIC_NOTE",
                    "severity": "failure",
                    "scene_id": "review",
                    "observed": {"note": "balanced"},
                    "expected": {},
                    "explanation": "Optional model note.",
                    "suggestion": "Keep the current arrangement.",
                }
            ],
        }


def test_optional_review_validates_json_and_downgrades_findings_to_warnings(tmp_path: Path) -> None:
    contact = tmp_path / "contact.png"
    contact.write_bytes(b"fake")

    review = run_visual_review(FakeCritic(), contact, (), _plan(), _observed())

    assert isinstance(review, VisualReview)
    assert review.findings[0].severity == "warning"
    assert review.findings[0].code == "AESTHETIC_NOTE"


def test_invalid_optional_review_becomes_structured_non_blocking_warning() -> None:
    review = parse_visual_review({"summary": 42, "findings": []}, scene_id="review")

    assert review.findings[0].code == "MULTIMODAL_REVIEW_INVALID"
    assert review.findings[0].severity == "warning"
