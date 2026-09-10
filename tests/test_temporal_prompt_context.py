"""Public prompt contract for temporal scene context."""

from __future__ import annotations

import json
from pathlib import Path

from video_pipeline.pipeline import _request_document
from video_pipeline.prompts import build_prompt
from video_pipeline.provider import ProviderRequest


def test_prompt_and_request_artifact_preserve_complete_temporal_scene_context(
    tmp_path: Path,
) -> None:
    previous_scene = {
        "id": "previous",
        "scene_name": "PreviousScene",
        "end_seconds": 2.25,
    }
    next_scene = {
        "id": "next",
        "scene_name": "NextScene",
        "start_seconds": 7.75,
    }
    theme = {"id": "theme-temporal", "marker": "THEME_SENTINEL"}
    expectations = {
        "required_objects": ["vector"],
        "marker": "EXPECTATIONS_SENTINEL",
    }
    request = ProviderRequest(
        scene_name="TemporalScene",
        description="Draw the planned vector.",
        narration_text="EXACT NARRATION TEXT",
        start_seconds=2.25,
        end_seconds=7.75,
        target_duration_seconds=5.5,
        objective="EXACT OBJECTIVE",
        previous_scene=previous_scene,
        next_scene=next_scene,
        required_objects=("vector", "label"),
        required_elements=("axis", "caption"),
        resolution=(1280, 720),
        fps=24,
        theme=theme,
        capabilities=("vector_geometry",),
        expectations=expectations,
        prior_findings=("PRIOR_FINDING_SENTINEL",),
    )

    prompt = build_prompt(request)

    assert "EXACT NARRATION TEXT" in prompt
    assert "start_seconds: 2.25" in prompt
    assert "end_seconds: 7.75" in prompt
    assert "target_duration_seconds: 5.5" in prompt
    assert "EXACT OBJECTIVE" in prompt
    assert '"id": "previous"' in prompt
    assert '"id": "next"' in prompt
    assert "vector" in prompt
    assert "label" in prompt
    assert "axis" in prompt
    assert "caption" in prompt
    assert "1280" in prompt
    assert "720" in prompt
    assert "24" in prompt
    assert "THEME_SENTINEL" in prompt
    assert "vector_geometry" in prompt
    assert "EXPECTATIONS_SENTINEL" in prompt
    assert "PRIOR_FINDING_SENTINEL" in prompt
    assert "VisualScene" in prompt
    assert "TemporalScene" in prompt

    artifact = tmp_path / "request.json"
    artifact.write_text(
        json.dumps(_request_document(request), ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    persisted = json.loads(artifact.read_text(encoding="utf-8"))
    assert persisted["narration_text"] == "EXACT NARRATION TEXT"
    assert persisted["start_seconds"] == 2.25
    assert persisted["end_seconds"] == 7.75
    assert persisted["target_duration_seconds"] == 5.5
    assert persisted["objective"] == "EXACT OBJECTIVE"
    assert persisted["previous_scene"] == previous_scene
    assert persisted["next_scene"] == next_scene
    assert persisted["required_objects"] == ["vector", "label"]
    assert persisted["required_elements"] == ["axis", "caption"]
    assert persisted["resolution"] == [1280, 720]
    assert persisted["fps"] == 24
    assert persisted["theme"] == theme
    assert persisted["capabilities"] == ["vector_geometry"]
    assert persisted["expectations"] == expectations
    assert persisted["prior_findings"] == ["PRIOR_FINDING_SENTINEL"]
