from manim import LEFT, ORIGIN, PI, Arrow, Create, Transform

from video_pipeline.runtime import VisualScene


class GeometryFoundationScene(VisualScene):
    """First golden scene: bounded geometry and a measurable transform."""

    def construct(self) -> None:
        primary = self.scene_plan.theme.color("primary")
        vector = self.register_visual(
            Arrow(LEFT * 3, LEFT * 1.5, color=primary),
            "vector",
            kind="arrow",
            color_role="primary",
        )
        self.play(Create(vector), run_time=1.2)
        self.checkpoint("introduce", beat_id="introduce")
        target = vector.copy().rotate(PI / 2, about_point=ORIGIN)
        self.play(Transform(vector, target), run_time=1.2)
        self.checkpoint("transform", beat_id="transform")
        self.wait(1.6)
