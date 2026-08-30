"""Fake-only checks for the Manim runtime boundary."""

from __future__ import annotations

import json

import pytest

from video_pipeline.critics import check_plan_coherence
from video_pipeline.runtime import VisualScene
from video_pipeline.scene_plan import Beat, ScenePlan, VisualObject


class _Mobject:
    def get_bounding_box(self) -> list[list[float]]:
        return [[-1.0, -0.5, 0.0], [1.0, 0.5, 0.0]]

    def get_center(self) -> list[float]:
        return [0.0, 0.0, 0.0]


class _RotatingMobject(_Mobject):
    def __init__(self) -> None:
        self.angle = 0.0

    def get_angle(self) -> float:
        return self.angle


class _Animation:
    def __init__(self, mobject: object, run_time: float) -> None:
        self.mobject = mobject
        self.run_time = run_time


def test_visual_scene_discovers_plan_from_subprocess_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = ScenePlan(
        id="subprocess-plan",
        scene_name="SubprocessPlanScene",
        objective="Prove that a Manim-created scene can discover its plan.",
        duration_seconds=2.0,
    )
    monkeypatch.setenv("VIDEO_PIPELINE_SCENE_PLAN", plan.model_dump_json())

    scene = VisualScene()

    assert scene.scene_plan is not None
    assert scene.scene_plan.id == "subprocess-plan"


def test_visual_scene_captures_initial_state_before_construct_mutations() -> None:
    scene = VisualScene()
    mobject = _Mobject()
    scene.register_visual(mobject, "shape", kind="rectangle")
    scene.mobjects.append(mobject)

    observed = scene._observed_scene()  # noqa: SLF001 - contract seam test

    assert observed.initial_state == []
    assert [item.id for item in observed.final_state] == ["shape"]
    assert observed.checkpoints[0].id == "initial"
    assert json.loads(observed.model_dump_json())["initial_state"] == []


def test_visual_scene_records_rotation_orientation_and_animation_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "video_pipeline.runtime._ManimScene.play",
        lambda self, *args, **kwargs: None,
    )
    scene = VisualScene()
    mobject = _RotatingMobject()
    scene.register_visual(mobject, "vector", kind="arrow")
    scene.mobjects.append(mobject)
    scene.checkpoint("start", beat_id="start")
    scene.play(_Animation(mobject, run_time=2.5))
    mobject.angle = 1.0
    scene.checkpoint("rotate", beat_id="rotate")

    observed = scene._observed_scene()  # noqa: SLF001 - contract seam test
    plan = ScenePlan(
        id="visualscene",
        scene_name="VisualScene",
        objective="Observe a vector rotation.",
        duration_seconds=4.0,
        objects=[VisualObject(id="vector", kind="arrow")],
        beats=[
            Beat(id="start", action="introduce", objects=["vector"]),
            Beat(
                id="rotate",
                action="transform",
                objects=["vector"],
                movement="rotate",
            ),
        ],
    )

    findings = check_plan_coherence(plan, observed)

    assert observed.animations[0].run_time == 2.5
    assert observed.checkpoints[-1].objects[0].orientation == 1.0
    assert not any(finding.code == "BEAT_MOVEMENT_MISSING" for finding in findings)
