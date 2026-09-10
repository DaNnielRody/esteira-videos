"""Checks for the Manim runtime boundary and actual in-memory geometry."""

from __future__ import annotations

import json

import pytest

from video_pipeline.critics import check_plan_coherence
from video_pipeline.runtime import VisualScene
from video_pipeline.scene_plan import Beat, ScenePlan, VisualObject


class _Mobject:
    def get_all_points(self) -> list[list[float]]:
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


def test_visual_scene_default_wait_matches_manim_and_records_one_second(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from manim import Scene

    durations: list[float] = []

    def record_wait(_scene: object, duration: float, *_args: object, **_kwargs: object) -> None:
        durations.append(duration)

    monkeypatch.setattr(Scene, "wait", record_wait)
    scene = VisualScene()
    scene.wait()
    assert durations == [1.0]
    assert scene.checkpoint("after-default-wait").instant_seconds == 1.0


def test_wait_is_counted_once_when_manim_delegates_it_to_play(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from manim import Scene

    def render_boundary(_scene: object, *_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(Scene, "play", render_boundary)
    scene = VisualScene()
    scene.wait(2.0)
    assert scene.checkpoint("after-wait").instant_seconds == 2.0


def test_visual_scene_exposes_its_authoritative_theme_to_generated_code() -> None:
    plan = ScenePlan(
        id="theme-scene", scene_name="ThemeScene", objective="Theme", duration_seconds=2.0
    )
    scene = VisualScene(scene_plan=plan)
    assert scene.theme is plan.theme
    assert scene.theme.palette["primary"] == plan.theme.palette["primary"]


@pytest.mark.parametrize("grouped", [False, True])
def test_checkpoint_measures_real_manim_geometry(grouped: bool) -> None:
    from manim import RIGHT, Square, VGroup

    scene = VisualScene()
    scene.camera.frame_width = 16.0
    scene.camera.frame_height = 8.0
    shape = Square(side_length=2).shift(2 * RIGHT)
    if grouped:
        shape = VGroup(shape, Square(side_length=2).shift(-2 * RIGHT))
    scene.register_visual(shape, "geometry", kind="rectangle")
    scene.add(shape)

    observed = scene.checkpoint("geometry").objects[0]

    assert observed.width == pytest.approx(0.375 if grouped else 0.125)
    assert observed.height == pytest.approx(0.25)
    assert observed.center_x == pytest.approx(0.5 if grouped else 0.625)
    assert observed.bbox.top == pytest.approx(0.375)
    assert observed.bbox.bottom == pytest.approx(0.625)


def test_checkpoint_tracks_registered_child_inside_visible_group() -> None:
    from manim import Square, VGroup

    scene = VisualScene()
    child = Square(side_length=2)
    scene.register_visual(child, "child", kind="square")
    group = VGroup(child)
    scene.add(group)

    assert [item.id for item in scene.checkpoint("group-added").objects] == ["child"]
    scene.remove(group)
    assert scene.checkpoint("group-removed").objects == []


def test_vertical_line_with_visible_stroke_has_positive_observed_width() -> None:
    from manim import Line

    scene = VisualScene()
    line = Line([0, -1, 0], [0, 1, 0], stroke_width=4)
    scene.register_visual(line, "line", kind="line")
    scene.add(line)
    measured = scene.checkpoint("visible-line").objects[0]
    assert measured.width > 0
    assert measured.height > 0
