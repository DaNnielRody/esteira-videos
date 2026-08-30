from manim import LEFT, RIGHT, UP, Arrow, Create, MathTex, Text

from video_pipeline.runtime import VisualScene


class EquationFoundationScene(VisualScene):
    """Second golden scene: a recurring object and an explicit MathTex fact."""

    def construct(self) -> None:
        primary = self.scene_plan.theme.color("primary")
        accent = self.scene_plan.theme.color("accent")
        vector = self.register_visual(
            Arrow(LEFT * 3, LEFT * 1.5, color=primary),
            "vector",
            kind="arrow",
            color_role="primary",
        )
        formula = self.register_visual(
            MathTex(r"x^2 + y^2 = r^2", color=accent, font_size=40).to_edge(RIGHT, buff=1.0),
            "formula",
            kind="mathtex",
            color_role="accent",
            formula=r"x^2 + y^2 = r^2",
        )
        label = self.register_visual(
            Text("Pythagorean identity", color=self.scene_plan.theme.color("text"), font_size=26)
            .to_edge(UP, buff=0.5),
            "label",
            kind="text",
            color_role="text",
            text="Pythagorean identity",
        )
        self.play(Create(vector), run_time=1.2)
        self.checkpoint("persist", beat_id="persist")
        self.play(Create(formula), run_time=1.2)
        self.checkpoint("define", beat_id="define")
        self.play(Create(label), run_time=0.8)
        self.checkpoint("label", beat_id="label")
        self.wait(0.8)
