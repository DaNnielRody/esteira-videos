"""Public API contract for the canonical scene specification."""

from __future__ import annotations

import video_pipeline
from video_pipeline import spec


def test_public_api_exposes_scene_spec_without_video_spec_compatibility() -> None:
    assert "SceneSpec" in video_pipeline.__all__
    assert "VideoSpec" not in video_pipeline.__all__
    assert "load_video_spec" not in video_pipeline.__all__
    assert "load_scene_spec" not in video_pipeline.__all__
    assert hasattr(video_pipeline, "SceneSpec")
    assert not hasattr(video_pipeline, "VideoSpec")
    assert not hasattr(video_pipeline, "load_video_spec")
    assert not hasattr(video_pipeline, "load_scene_spec")
    assert "SceneSpec" in spec.__all__
    assert "VideoSpec" not in spec.__all__
    assert "load_video_spec" not in spec.__all__
    assert "load_scene_spec" not in spec.__all__
    assert not hasattr(spec, "VideoSpec")
    assert not hasattr(spec, "load_video_spec")
    assert not hasattr(spec, "load_scene_spec")
