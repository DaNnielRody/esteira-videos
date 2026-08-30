"""Fake-only RITL integration for plans, runtime facts, and quality reports."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from video_pipeline.observation import ObservationResult
from video_pipeline.pipeline import RenderPipeline
from video_pipeline.provider import ProviderRequest, ProviderResponse, UnloadResult
from video_pipeline.rendering import RenderResult
from video_pipeline.runtime import BoundingBox, ObservedObject, ObservedScene, VisualScene
from video_pipeline.scene_plan import Beat, ScenePlan, VisualObject
from video_pipeline.spec import SceneSpec
from video_pipeline.validation import ValidationResult


class _Provider:
    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []
        self.codes = iter(["BAD_CODE", "GOOD_CODE"])

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        code = next(self.codes)
        return ProviderResponse(code=code, raw_response={"response": code})

    def unload(self) -> UnloadResult:
        return UnloadResult(ok=True, raw_response={"response": "ok"})


class _Runner:
    def run(self, scene_path: str | Path, media_dir: str | Path) -> RenderResult:
        media = Path(media_dir)
        media.mkdir(parents=True, exist_ok=True)
        candidate = media / "scene.mp4"
        candidate.write_bytes(b"mp4")
        return RenderResult(
            argv=["fake-manim"],
            exit_code=0,
            timed_out=False,
            missing_executable=False,
            stdout="",
            stderr="",
            elapsed_seconds=0.1,
            mp4_paths=[candidate],
        )


class _PlanAwareRunner(_Runner):
    """Fake subprocess boundary that constructs the child scene from env."""

    def __init__(self) -> None:
        self.child_plan_id: str | None = None

    def run(self, scene_path: str | Path, media_dir: str | Path) -> RenderResult:
        del scene_path
        payload = os.environ.get("VIDEO_PIPELINE_SCENE_PLAN")
        assert payload is not None
        child = VisualScene()
        assert child.scene_plan is not None
        self.child_plan_id = child.scene_plan.id
        return super().run("ignored.py", media_dir)


class _Validator:
    def validate(self, path: str | Path) -> ValidationResult:
        return ValidationResult(
            path=Path(path),
            valid=True,
            reasons=[],
            width=854,
            height=480,
            duration_seconds=2.0,
            size_bytes=3,
        )


class _Observer:
    def observe(self, mp4_path: str | Path, frames_dir: str | Path) -> ObservationResult:
        candidate = Path(mp4_path)
        source = candidate.parent.parent / "scene.py"
        frames = Path(frames_dir)
        frames.mkdir(parents=True, exist_ok=True)
        good = source.read_text(encoding="utf-8") == "GOOD_CODE"
        object_now = ObservedObject(
            id="definition" if good else "wrong",
            kind="text",
            bbox=BoundingBox(left=0.2, top=0.2, right=0.8, bottom=0.4),
            center_x=0.5,
            center_y=0.3,
            width=0.6,
            height=0.2,
            text="definition" if good else "wrong",
        )
        facts = ObservedScene(
            scene_id="visual",
            scene_name="VisualScene",
            initial_state=[object_now],
            final_state=[object_now],
        )
        facts_path = candidate.parent / "visual-facts.json"
        facts_path.write_text(json.dumps(facts.to_document()), encoding="utf-8")
        return ObservationResult.success([])


class _PlanObserver(_Observer):
    """Observer fake matching the explicit plan identity in the env test."""

    def observe(self, mp4_path: str | Path, frames_dir: str | Path) -> ObservationResult:
        candidate = Path(mp4_path)
        facts_path = candidate.parent / "visual-facts.json"
        facts = ObservedScene(
            scene_id="subprocess-plan",
            scene_name="SubprocessPlanScene",
            initial_state=[],
            final_state=[
                ObservedObject(
                    id="definition",
                    kind="text",
                    bbox=BoundingBox(left=0.2, top=0.2, right=0.8, bottom=0.4),
                    center_x=0.5,
                    center_y=0.3,
                    width=0.6,
                    height=0.2,
                    text="definition",
                )
            ],
        )
        facts_path.write_text(json.dumps(facts.to_document()), encoding="utf-8")
        return ObservationResult.success([])


def test_visual_failure_is_preserved_and_corrected_by_fake_qwen(tmp_path: Path) -> None:
    plan = ScenePlan(
        id="visual",
        scene_name="VisualScene",
        objective="Show definition.",
        duration_seconds=2.0,
        objects=[VisualObject(id="definition", kind="text", text="definition")],
        beats=[Beat(id="show", action="introduce", objects=["definition"], duration_seconds=1.5)],
    )
    spec = SceneSpec(
        id="visual",
        scene_name="VisualScene",
        description="Show definition.",
        plan=plan,
    )
    provider = _Provider()
    pipeline = RenderPipeline(
        provider=provider,
        runner=_Runner(),
        validator=_Validator(),
        observer=_Observer(),
        output_root=tmp_path / "runs",
        id_factory=lambda: "visual-run",
    )

    result = pipeline.render(spec, max_attempts=2)

    assert result.state.value == "success"
    assert len(provider.requests) == 2
    diagnostics = dict(provider.requests[1].diagnostics or {})
    assert any("REQUIRED_OBJECT_MISSING" in str(item) for item in diagnostics["quality_findings"])
    first_report = json.loads(
        (result.run_path / "attempt-01" / "quality-report.json").read_text(encoding="utf-8")
    )
    assert first_report["accepted"] is False


def test_unproven_capability_is_rejected_before_provider_generation(tmp_path: Path) -> None:
    plan = ScenePlan(
        id="unsupported",
        scene_name="UnsupportedScene",
        objective="Try an unproven graph capability.",
        duration_seconds=2.0,
        capabilities=["function_graphs"],
    )
    spec = SceneSpec(
        id="unsupported",
        scene_name="UnsupportedScene",
        description=plan.objective,
        plan=plan,
    )
    provider = _Provider()
    pipeline = RenderPipeline(
        provider=provider,
        runner=_Runner(),
        validator=_Validator(),
        observer=_Observer(),
        output_root=tmp_path / "runs",
        id_factory=lambda: "unsupported-run",
    )

    with pytest.raises(ValueError, match="not supported"):
        pipeline.render(spec, max_attempts=1)

    assert provider.requests == []


def test_pipeline_plan_reaches_manins_child_scene_through_subprocess_environment(
    tmp_path: Path,
) -> None:
    plan = ScenePlan(
        id="subprocess-plan",
        scene_name="SubprocessPlanScene",
        objective="Pass the explicit plan through the renderer boundary.",
        duration_seconds=2.0,
        objects=[VisualObject(id="definition", kind="text", text="definition")],
        beats=[Beat(id="show", action="introduce", objects=["definition"], duration_seconds=1.5)],
    )
    spec = SceneSpec(
        id="subprocess-plan",
        scene_name="SubprocessPlanScene",
        description=plan.objective,
        plan=plan,
    )
    provider = _Provider()
    runner = _PlanAwareRunner()
    pipeline = RenderPipeline(
        provider=provider,
        runner=runner,
        validator=_Validator(),
        observer=_PlanObserver(),
        output_root=tmp_path / "runs",
        id_factory=lambda: "subprocess-plan-run",
    )

    result = pipeline.render(spec, max_attempts=1)

    assert result.state.value == "success"
    assert runner.child_plan_id == "subprocess-plan"
