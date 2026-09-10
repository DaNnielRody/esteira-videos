from pathlib import Path as FilePath
ASSET_ROOT = FilePath(__file__).resolve().parents[1] / "_assets"

# ruff: noqa: F403, F405, ANN001, ANN201, ANN202, B905
"""Authored vector illustrations and narration-led staging, Manim Community."""

from manim import *
from manim.utils.rate_functions import ease_out_cubic
from manim.scene.moving_camera_scene import MovingCameraScene

from video_pipeline.runtime import VisualScene


class StoryArt:
    def begin(self):
        self.p = self.theme.palette
        self.camera.background_color = self.p["background"]
        self.ink = "#E9EEF7"
        self.paper = "#E5D6B8"
        # Semantic roles remain stable across scenes.  Blue/red aliases keep
        # existing artwork readable while new beats use the named roles.
        self.cyan = self.p.get("primary", "#59C8F4")
        self.yellow = self.p.get("warning", "#FFD166")
        self.green = self.p.get("success", "#8ED9AE")
        self.pink = self.p.get("accent", "#F72585")
        self.blue = self.cyan
        self.red = self.pink
        self.purple = "#B5A1F4"

    def color_for(self, role=None):
        return {
            "cyan": self.cyan,
            "yellow": self.yellow,
            "green": self.green,
            "pink": self.pink,
        }.get(role, role or self.blue)

    def text(self, words, size=30, color=None):
        return Text(words, font="JetBrains Mono", font_size=size, color=color or self.ink)

    def cue(self, name, seconds):
        if self._logical_time > seconds + 0.15:
            raise RuntimeError(f"Late narration cue {name}: {self._logical_time:.3f} > {seconds}")
        self.until(seconds)
        self.checkpoint(f"cue@{seconds:.3f}:{name}")

    def until(self, seconds):
        if seconds > self._logical_time + 0.001:
            self.wait(seconds - self._logical_time)

    def track(self, obj, ident):
        self.register_visual(obj, ident, kind="diagram")
        return obj

    def show(self, obj, ident, duration=0.65, direction=UP):
        self.track(obj, ident)
        self.play(FadeIn(obj, shift=direction * 0.15), run_time=duration)
        self.add(obj)
        self.checkpoint(ident + "-shown")
        return obj

    def write(self, words, ident, position=ORIGIN, size=36, color=None, duration=0.65):
        obj = self.text(words, size, color).move_to(position)
        self.register_visual(obj, ident, kind="text", text=words)
        self.play(Write(obj), run_time=duration)
        self.checkpoint(ident + "-shown")
        return obj

    def typing(self, words, ident, position=ORIGIN, size=30, duration=1.2):
        obj = self.text(words, size).move_to(position)
        if obj.width > 11.8:
            obj.scale_to_fit_width(11.8)
        self.register_visual(obj, ident, kind="text", text=words)
        self.play(AddTextLetterByLetter(obj), run_time=duration)
        self.add(obj)
        self.checkpoint(ident + "-typed")
        return obj

    def morph(self, old, new, ident, duration=0.8):
        self.track(new, ident)
        self.play(ReplacementTransform(old, new), run_time=duration)
        self.add(new)
        self.checkpoint(ident + "-replaced")
        return new

    def clear(self, duration=0.55):
        if self.mobjects:
            self.play(*[FadeOut(obj) for obj in list(self.mobjects)], run_time=duration)
        self.checkpoint("stage-transition")

    def show_box(self, obj, ident, duration=0.25, color_role=None):
        """Enter a meaningful box with a small snap; fade is only support."""
        self.track(obj, ident)
        if color_role:
            obj.set_stroke(color=self.color_for(color_role))
        original = obj.copy()
        obj.become(original.copy().scale(0.92).set_opacity(0))
        self.add(obj)
        duration = max(0.12, duration)
        approach = duration * 0.72
        self.play(
            Transform(obj, original.copy().scale(1.03)),
            rate_func=smooth,
            run_time=approach,
        )
        self.play(
            Transform(obj, original.copy()),
            rate_func=ease_out_cubic,
            run_time=duration - approach,
        )
        self.checkpoint(ident + "-boxed")
        return obj

    def draw_arrow(self, arrow, ident, duration=0.4, color_role="cyan"):
        """Draw a relationship so its direction is legible."""
        if color_role:
            arrow.set_color(self.color_for(color_role))
        self.track(arrow, ident)
        self.play(Create(arrow), rate_func=ease_out_cubic, run_time=duration)
        self.add(arrow)
        self.checkpoint(ident + "-drawn")
        return arrow

    def stagger_items(self, items, ident_prefix, lag=0.07, duration=0.5, direction=DOWN):
        """Bring a list into its destination with an exact 60–80 ms stagger."""
        items = list(items)
        if not items:
            return items
        if duration <= lag * (len(items) - 1) + 0.16:
            raise ValueError("stagger duration must leave at least 160 ms per item")
        offset = direction * 0.22
        originals = [item.copy() for item in items]
        for index, (item, original) in enumerate(zip(items, originals)):
            self.track(item, f"{ident_prefix}-{index}")
            item.become(original.copy().shift(-offset).set_opacity(0))
            self.add(item)
        # Child starts are lag apart; the outer schedule stays linear.
        entry_time = duration - lag * (len(items) - 1)
        lag_ratio = lag / entry_time
        self.play(
            LaggedStart(
                *[
                    AnimationGroup(
                        Transform(item, original.copy()),
                        run_time=entry_time,
                        rate_func=ease_out_cubic,
                    )
                    for item, original in zip(items, originals)
                ],
                lag_ratio=lag_ratio,
                rate_func=linear,
            ),
            run_time=duration,
        )
        self.checkpoint(ident_prefix + "-staggered")
        return items

    def pulse_through_network(self, nodes, ident="inference-pulse", duration=1.2):
        """Use one physical yellow signal only for inference through layers."""
        nodes = list(nodes)
        if len(nodes) < 2:
            return None
        points = [node.get_center() for node in nodes]
        pulse = Dot(points[0], radius=0.075, color=self.yellow)
        self.register_visual(pulse, ident, kind="diagram")
        self.add(pulse)
        segment_time = duration / (len(points) - 1)
        for index in range(len(points) - 1):
            path = Line(points[index], points[index + 1], color=self.yellow)
            self.play(
                MoveAlongPath(pulse, path),
                Indicate(nodes[index + 1], color=self.yellow, scale_factor=1.06),
                rate_func=smooth,
                run_time=segment_time,
            )
            self.checkpoint(f"{ident}-{index + 1}")
        self.remove(pulse)
        self.checkpoint(ident + "-complete")
        return pulse

    def morph_token(self, old, new, ident, duration=0.8):
        """Keep one conceptual token alive while its representation changes."""
        self.track(new, ident)
        self.play(ReplacementTransform(old, new), rate_func=smooth, run_time=duration)
        self.add(new)
        self.checkpoint(ident + "-morphed")
        return new

    def count_value(
        self,
        start,
        end,
        ident,
        position=ORIGIN,
        duration=0.8,
        size=30,
        color_role="yellow",
        suffix="",
    ):
        """Count a value in the existing DejaVu Sans typography."""
        color = self.color_for(color_role)
        start_value = float(start)
        end_value = float(end)

        def formatted(value):
            if suffix and (not float(value).is_integer() or end_value < 10):
                number = f"{value:.1f}".rstrip("0").rstrip(".").replace(".", ",")
                return number + suffix
            return f"{int(round(value))}{suffix}"

        label = self.text(formatted(start_value), size, color).move_to(position)
        self.register_visual(label, ident, kind="text", text=formatted(end_value))
        tracker = ValueTracker(start_value)

        def update(mob):
            mob.become(self.text(formatted(tracker.get_value()), size, color).move_to(position))

        label.add_updater(update)
        self.add(label)
        self.play(tracker.animate.set_value(end_value), rate_func=smooth, run_time=duration)
        label.clear_updaters()
        label.become(self.text(formatted(end_value), size, color).move_to(position))
        self.checkpoint(ident + "-counted")
        return label

    def camera_focus(self, target, ident, width=None, duration=0.8):
        """Move the MovingCamera frame only when the composition needs it."""
        if not hasattr(self.camera, "frame"):
            raise RuntimeError("camera_focus requires MovingCameraScene")
        point = target.get_center() if hasattr(target, "get_center") else np.array(target)
        animation = self.camera.frame.animate.move_to(point)
        if width is not None:
            animation = animation.set_width(width)
        self.play(animation, rate_func=smooth, run_time=duration)
        self.checkpoint(ident + "-focused")
        return self.camera.frame

    def card(self, words, color=None, size=30, width=None):
        label = self.text(words, size)
        frame = RoundedRectangle(
            width=max(width or 0, label.width + 0.48),
            height=max(0.68, label.height + 0.32),
            corner_radius=0.12,
            stroke_color=color or self.blue,
            stroke_width=2,
            fill_color="#172338",
            fill_opacity=1,
        )
        return VGroup(frame, label.move_to(frame))

    def tag(self, words, color=None, size=26):
        label = self.text(words, size, color or self.green)
        w = max(1.35, label.width + 0.8)
        shape = Polygon(
            [-w / 2 + 0.25, 0.38, 0],
            [w / 2, 0.38, 0],
            [w / 2, -0.38, 0],
            [-w / 2 + 0.25, -0.38, 0],
            [-w / 2, 0, 0],
            color=color or self.green,
            fill_color="#15262B",
            fill_opacity=1,
            stroke_width=2,
        )
        hole = Circle(radius=0.065, color=color or self.green).move_to([-w / 2 + 0.25, 0, 0])
        label.shift(RIGHT * 0.14)
        return VGroup(shape, hole, label)

    def paper_page(self, kind="Artigo", color=None, width=1.5, height=2):
        c = color or self.paper
        sheet = Polygon(
            [-width / 2, -height / 2, 0],
            [width / 2, -height / 2, 0],
            [width / 2, height / 2 - 0.3, 0],
            [width / 2 - 0.3, height / 2, 0],
            [-width / 2, height / 2, 0],
            color=c,
            fill_color="#1D2735",
            fill_opacity=1,
            stroke_width=2,
        )
        fold = VMobject(color=c, stroke_width=1.5).set_points_as_corners(
            [
                [width / 2 - 0.3, height / 2, 0],
                [width / 2 - 0.3, height / 2 - 0.3, 0],
                [width / 2, height / 2 - 0.3, 0],
            ]
        )
        title = self.text(kind, 19, c)
        if title.width > width - 0.25:
            title.scale_to_fit_width(width - 0.25)
        title.move_to([0, height / 2 - 0.5, 0])
        lines = VGroup(
            *[
                Line(
                    [-width / 2 + 0.18, y, 0],
                    [width / 2 - 0.18 - (i % 3) * 0.13, y, 0],
                    color=c,
                    stroke_opacity=0.45,
                    stroke_width=1.5,
                )
                for i, y in enumerate(np.linspace(height / 2 - 0.85, -height / 2 + 0.25, 5))
            ]
        )
        return VGroup(sheet, fold, title, lines)

    def book(self, title="Livro", color=None):
        c = color or self.blue
        cover = RoundedRectangle(
            width=1.65,
            height=2.15,
            corner_radius=0.09,
            color=c,
            fill_color="#182F43",
            fill_opacity=1,
            stroke_width=2.5,
        )
        spine = Line([-0.62, -1.05, 0], [-0.62, 1.05, 0], color=c, stroke_width=3)
        pages = VGroup(
            *[
                Line([-0.56, y, 0], [0.7, y, 0], color=self.paper, stroke_width=1.1)
                for y in [-0.8, -0.86, -0.92]
            ]
        )
        name = self.text(title, 23, c)
        if name.width > 1.27:
            name.scale_to_fit_width(1.27)
        name.move_to([0.08, 0.28, 0])
        ornament = Line([-0.35, -0.12, 0], [0.5, -0.12, 0], color=c, stroke_width=2)
        return VGroup(cover, spine, pages, name, ornament)

    def open_book(self):
        left = Polygon(
            [-2, -0.7, 0],
            [-0.12, -1, 0],
            [-0.12, 1, 0],
            [-2, 1.3, 0],
            color=self.paper,
            fill_color="#273342",
            fill_opacity=1,
        )
        right = Polygon(
            [0.12, -1, 0],
            [2, -0.7, 0],
            [2, 1.3, 0],
            [0.12, 1, 0],
            color=self.paper,
            fill_color="#273342",
            fill_opacity=1,
        )
        seam = Line([0, -1, 0], [0, 1, 0], color=self.blue)
        strokes = VGroup(
            *[
                Line(
                    [side * 0.35, y, 0],
                    [side * 1.75, y + 0.2, 0],
                    color=self.paper,
                    stroke_width=1.4,
                    stroke_opacity=0.4,
                )
                for side in [-1, 1]
                for y in [-0.55, -0.25, 0.05, 0.35, 0.65]
            ]
        )
        return VGroup(left, right, seam, strokes)

    def browser(self, title="Página", width=4.4, height=2.8):
        border = RoundedRectangle(
            width=width,
            height=height,
            corner_radius=0.14,
            color=self.blue,
            fill_color="#152337",
            fill_opacity=1,
        )
        bar = Line(
            [-width / 2, height / 2 - 0.4, 0],
            [width / 2, height / 2 - 0.4, 0],
            color=self.blue,
            stroke_width=1.5,
        )
        dots = VGroup(
            *[
                Circle(radius=0.045, color=c, fill_color=c, fill_opacity=1).move_to(
                    [-width / 2 + 0.2 + i * 0.17, height / 2 - 0.2, 0]
                )
                for i, c in enumerate([self.red, self.paper, self.green])
            ]
        )
        heading = self.text(title, 22).move_to([0.25, height / 2 - 0.2, 0])
        if heading.width > width - 1.2:
            heading.scale_to_fit_width(width - 1.2)
        return VGroup(border, bar, dots, heading)

    def person(self, mood="curious", color=None):
        c = color or self.paper
        head = Circle(radius=0.38, color=c, fill_color="#26333F", fill_opacity=1).shift(UP * 0.85)
        hair = Arc(
            radius=0.39, start_angle=0.1, angle=PI - 0.2, color=self.blue, stroke_width=6
        ).shift(UP * 0.9)
        eyes = VGroup(*[Dot([x, 0.91, 0], radius=0.045, color=self.ink) for x in [-0.13, 0.13]])
        mouth = Arc(radius=0.12, start_angle=PI, angle=PI, color=c, stroke_width=2).shift(UP * 0.75)
        if mood == "confused":
            mouth = Line([-0.1, 0.67, 0], [0.1, 0.72, 0], color=c, stroke_width=2)
        shirt = Polygon(
            [-0.28, 0.43, 0],
            [0.28, 0.43, 0],
            [0.42, -0.5, 0],
            [-0.42, -0.5, 0],
            color=self.blue,
            fill_color="#245069",
            fill_opacity=1,
        )
        arms = VGroup(
            Line([-0.26, 0.28, 0], [-0.65, -0.2, 0], color=c, stroke_width=5),
            Line([0.26, 0.28, 0], [0.65, -0.2, 0], color=c, stroke_width=5),
        )
        legs = VGroup(
            Line([-0.18, -0.5, 0], [-0.25, -1, 0], color=c, stroke_width=5),
            Line([0.18, -0.5, 0], [0.25, -1, 0], color=c, stroke_width=5),
        )
        return VGroup(head, hair, eyes, mouth, shirt, arms, legs)

    def hand(self):
        outline = VMobject(color=self.paper, fill_color="#A08D73", fill_opacity=1, stroke_width=2)
        outline.set_points_smoothly(
            [
                [-0.55, -0.7, 0],
                [-0.6, 0.05, 0],
                [-0.4, 0.35, 0],
                [-0.2, 0.1, 0],
                [-0.18, 0.8, 0],
                [0.02, 0.85, 0],
                [0.11, 0.22, 0],
                [0.25, 0.65, 0],
                [0.43, 0.55, 0],
                [0.45, -0.05, 0],
                [0.3, -0.45, 0],
                [0.28, -0.7, 0],
                [-0.55, -0.7, 0],
            ]
        )
        cuff = Rectangle(
            width=0.95, height=0.35, color=self.blue, fill_color="#244B68", fill_opacity=1
        ).move_to([-0.1, -0.85, 0])
        return VGroup(outline, cuff)

    def brain(self, scale=1):
        boundary = [
            [-0.04, -0.72, 0],
            [-0.63, -0.84, 0],
            [-1.02, -0.55, 0],
            [-1.35, -0.25, 0],
            [-1.4, 0.25, 0],
            [-1.18, 0.58, 0],
            [-0.98, 0.94, 0],
            [-0.55, 1.08, 0],
            [-0.2, 1.05, 0],
            [0, 0.88, 0],
            [0.2, 1.05, 0],
            [0.55, 1.08, 0],
            [0.98, 0.94, 0],
            [1.18, 0.58, 0],
            [1.4, 0.25, 0],
            [1.35, -0.25, 0],
            [1.02, -0.55, 0],
            [0.63, -0.84, 0],
            [0.04, -0.72, 0],
            [-0.04, -0.72, 0],
        ]
        outline = VMobject(
            color=self.purple, stroke_width=3, fill_color="#302A50", fill_opacity=1
        ).set_points_smoothly(boundary)
        folds = VGroup()
        for side in [-1, 1]:
            for j in range(3):
                path = VMobject(color=self.purple, stroke_width=2).set_points_smoothly(
                    [
                        [side * 0.18, 0.65 - j * 0.43, 0],
                        [side * 0.55, 0.83 - j * 0.43, 0],
                        [side * 0.9, 0.5 - j * 0.43, 0],
                        [side * 0.6, 0.26 - j * 0.43, 0],
                    ]
                )
                folds.add(path)
        seam = Line([0, -0.55, 0], [0, 0.84, 0], color=self.purple, stroke_width=2)
        stem = Polygon(
            [-0.2, -0.75, 0],
            [0.22, -0.75, 0],
            [0.4, -1.17, 0],
            [0.05, -1.22, 0],
            color=self.purple,
            fill_color="#302A50",
            fill_opacity=1,
        )
        return VGroup(stem, outline, folds, seam).scale(scale)

    def rack(self):
        shell = RoundedRectangle(
            width=1.55,
            height=3.15,
            corner_radius=0.1,
            color=self.blue,
            fill_color="#152131",
            fill_opacity=1,
            stroke_width=2,
        )
        servers = VGroup()
        for i in range(6):
            y = 1.18 - i * 0.47
            tray = RoundedRectangle(
                width=1.25,
                height=0.34,
                corner_radius=0.035,
                color="#7890A4",
                fill_color="#273546",
                fill_opacity=1,
                stroke_width=1,
            )
            slots = VGroup(
                *[
                    Line([x, -0.08, 0], [x, 0.08, 0], color="#7890A4", stroke_width=1)
                    for x in [-0.43, -0.31, -0.19, -0.07]
                ]
            )
            light = Circle(
                radius=0.045, color=self.green, fill_color=self.green, fill_opacity=1
            ).shift(RIGHT * 0.42)
            servers.add(VGroup(tray, slots, light).move_to([0, y, 0]))
        return VGroup(shell, servers)

    def chip(self, title="GPU"):
        core = RoundedRectangle(
            width=1.3,
            height=1.1,
            corner_radius=0.07,
            color=self.paper,
            fill_color="#433B2D",
            fill_opacity=1,
            stroke_width=3,
        )
        pins = VGroup(
            *[
                Line([x, 0.58, 0], [x, 0.82, 0], color=self.paper, stroke_width=3)
                for x in [-0.45, -0.15, 0.15, 0.45]
            ]
        )
        sides = VGroup(
            pins, pins.copy().rotate(PI), pins.copy().rotate(PI / 2), pins.copy().rotate(-PI / 2)
        )
        return VGroup(core, sides, self.text(title, 28, self.paper))

    def bubble(self, words, color=None, width=None):
        card = self.card(words, color=color or self.blue, width=width)
        tail = Polygon(
            [-0.35, -0.3, 0],
            [-0.6, -0.64, 0],
            [0.1, -0.3, 0],
            color=color or self.blue,
            fill_color="#172338",
            fill_opacity=1,
        )
        return VGroup(tail, card)

    def route(self, start, end, color=None):
        return Arrow(
            start,
            end,
            buff=0.12,
            color=color or self.blue,
            stroke_width=2.5,
            max_tip_length_to_length_ratio=0.12,
        )

    def learning(self, brain, duration=1.2):
        folds = brain[2]
        self.play(
            LaggedStart(
                *[Indicate(fold, color=self.green, scale_factor=1.03) for fold in folds],
                lag_ratio=0.12,
            ),
            run_time=duration,
        )

    def step(self, index, label, base=-2.5):
        x = -5.7 + index * 1.65
        top = -1.7 + index * 0.6
        depth = np.array([0.28, 0.23, 0])
        a = np.array([x, top, 0])
        b = np.array([x + 1.6, top, 0])
        front = Polygon(
            [x, base, 0],
            [x + 1.6, base, 0],
            b,
            a,
            color=self.blue,
            fill_color="#203D55",
            fill_opacity=1,
            stroke_width=1.5,
        )
        cap = Polygon(
            a,
            b,
            b + depth,
            a + depth,
            color=self.blue,
            fill_color="#42738A",
            fill_opacity=1,
            stroke_width=1.5,
        )
        side = Polygon(
            [x + 1.6, base, 0],
            b,
            b + depth,
            [x + 1.88, base + 0.23, 0],
            color=self.blue,
            fill_color="#162939",
            fill_opacity=1,
            stroke_width=1.5,
        )
        caption = self.text(label, 20)
        if caption.width > 1.4:
            caption.scale_to_fit_width(1.4)
        caption.move_to([x + 0.8, top - 0.33, 0])
        return VGroup(side, front, cap, caption)

    def build_step(self, obj, ident, duration=0.5):
        self.track(obj, ident)
        self.play(GrowFromEdge(obj, DOWN), run_time=duration)
        self.add(obj)
        self.checkpoint(ident + "-built")

    def capability_row(self):
        translation = VGroup(
            self.bubble("Olá", self.blue).scale(0.48).shift(LEFT * 0.35 + UP * 0.2),
            self.bubble("Hello", self.green).scale(0.48).shift(RIGHT * 0.35 + DOWN * 0.2),
        )
        coding = VGroup(
            self.browser("Código", width=1.6, height=1.2),
            self.text(">_", 28, self.green).shift(DOWN * 0.15),
        )
        summary = VGroup(
            self.paper_page("Texto").scale(0.48).shift(LEFT * 0.3),
            self.tag("Resumo", self.green, size=17).scale(0.65).shift(RIGHT * 0.25 + DOWN * 0.2),
        )
        answering = self.bubble("?", self.paper).scale(0.9)
        solving = VGroup(
            *[
                Square(0.43, color=c, fill_color=c, fill_opacity=0.25)
                for c in [self.blue, self.green, self.paper, self.purple]
            ]
        ).arrange_in_grid(rows=2, cols=2, buff=0.07)
        row = VGroup()
        for i, (icon, label) in enumerate(
            zip(
                [translation, coding, summary, answering, solving],
                ["Traduzir", "Programar", "Resumir", "Responder", "Resolver"],
            )
        ):
            icon.move_to([0, 0.2, 0])
            caption = self.text(label, 23).move_to([0, -0.85, 0])
            row.add(VGroup(icon, caption).move_to([-4.8 + i * 2.4, -2.05, 0]))
        return row

    def prediction_stage(self):
        context = (
            VGroup(*[self.card(t, size=29) for t in ["O", "cachorro", "correu"]])
            .arrange(RIGHT, buff=0.15)
            .move_to([0, 2.1, 0])
        )
        brain = self.brain(0.67).move_to([0, 0.15, 0])
        unknown = self.tag("?", self.paper, size=36).move_to([3.4, 0.3, 0])
        return context, brain, unknown


class Director(StoryArt):
    def begin(self):
        super().begin()
        self._actor_roots = []
        self.composition_beats = []

    def actor(self, obj):
        self._actor_roots.append(obj)
        return obj

    def person(self, mood="curious", color=None):
        return self.actor(super().person(mood, color))

    def _consolidate_actors(self):
        live = {id(m) for root in self.mobjects for m in root.get_family()}
        for root in self._actor_roots:
            if any(id(m) in live for m in root.get_family()):
                # Child eye/arm/parameter animations can detach their parent in
                # Manim. Reunite ownership before the next whole-body move.
                self.add(root)

    def play(self, *animations, **kwargs):
        self._consolidate_actors()
        result = super().play(*animations, **kwargs)
        self._consolidate_actors()
        return result

    def at(self, seconds, *animations, duration=1.2):
        self.cue("beat-" + str(seconds), seconds)
        if animations:
            self.play(*animations, run_time=duration, rate_func=smooth)
        self.audit_composition("after-" + str(seconds))

    def text(self, words, size=30, color=None):
        return super().text(words, size, color).set_z_index(5)

    def label(self, words, size=38, color=None, pos=ORIGIN):
        obj = self.text(words, size, color).move_to(pos)
        self.register_visual(obj, "text-" + words + "-" + str(len(self._registrations)), kind="text", text=words)
        return obj

    def finish(self, end):
        self.until(end)
        self.checkpoint("final")
        self.audit_composition("final")
        self.save_composition_audit()

    def network(self, width=3.5):
        layers = VGroup(*[VGroup(*[Dot(radius=0.095, color=self.yellow) for _ in range(n)]).arrange(DOWN, buff=0.35) for n in [3,4,3]])
        layers.arrange(RIGHT, buff=0.95)
        edges = VGroup(*[Line(a.get_center(),b.get_center(),stroke_width=1.5,color=self.purple,stroke_opacity=0.65) for l,r in zip(layers,layers[1:]) for a in l for b in r])
        return self.actor(VGroup(edges, layers).set_width(width))

    def look_at(self, person, target):
        delta = target.get_center()-person[0].get_center()
        delta = delta/(np.linalg.norm(delta)+1e-8)*0.065*person.height/2.23
        return person[2].animate.move_to(person[0].get_center()+UP*0.06*person.height/2.23+delta)

    def point_at(self, person, target):
        shoulder = person[5][1].get_start()
        direction = target.get_center()-shoulder
        direction = direction/(np.linalg.norm(direction)+1e-8)
        return Transform(person[5][1], Line(shoulder, shoulder+direction*person.height*0.32,color=self.paper,stroke_width=5))

    def react_confused(self, person):
        return Rotate(VGroup(*person[:4]), angle=-0.12, about_point=person[0].get_center())

    def react_success(self, person):
        return self.celebrate_subtle(person)

    def choose(self, person, target):
        return AnimationGroup(self.look_at(person,target), self.point_at(person,target))

    def monitor(self):
        screen = RoundedRectangle(width=3.4,height=2.25,corner_radius=0.12,color=self.paper,fill_color="#162B3D",fill_opacity=1)
        foot = VGroup(Line([0,-1.12,0],[0,-1.6,0],color=self.paper),Line([-0.8,-1.6,0],[0.8,-1.6,0],color=self.paper))
        return VGroup(screen,foot)

    def curve(self, a, b, bend=1, color=None, width=3):
        return CubicBezier(a,a+UP*bend,b+UP*bend,b,stroke_color=color or self.cyan,stroke_width=width,fill_opacity=0)

    def gpu(self):
        # Reusable graphics-card actor, not a microchip symbol or product photo.
        board=RoundedRectangle(width=4.8,height=2,corner_radius=0.16,color=self.paper,fill_color="#253040",fill_opacity=1,stroke_width=3)
        bracket=VMobject(color=self.paper,stroke_width=5).set_points_as_corners([[-2.6,1.25,0],[-2.6,-1.1,0],[-2.3,-1.1,0]])
        pins=VGroup(*[Rectangle(width=0.15,height=0.22,stroke_width=0,fill_color=self.yellow,fill_opacity=1).move_to([-1.4+i*0.2,-1.13,0]) for i in range(13)])
        fans=VGroup()
        for x in [-1.15,1.15]:
            blades=VGroup(*[Ellipse(width=0.34,height=0.69,color="#8095A9",fill_color="#56687E",fill_opacity=1).shift(UP*0.38).rotate(i*TAU/7,about_point=ORIGIN) for i in range(7)])
            fan=VGroup(Circle(radius=0.81,color="#8095A9"),blades,Dot(radius=0.2,color=self.paper)).move_to([x,0,0]);fans.add(fan)
        return self.actor(VGroup(board,bracket,pins,fans))

    def harness(self):
        core=self.network(2.1)
        self._actor_roots.remove(core)
        tools=VGroup(self.card("{ }",self.cyan,size=32),self.browser("",width=1.3,height=1),self.paper_page("",width=0.9,height=1.1)).arrange(DOWN,buff=0.3).move_to([2.5,0,0])
        links=VGroup(*[self.curve(core.get_right(),obj.get_left(),bend=0.25*(i-1),color=self.green,width=3) for i,obj in enumerate(tools)])
        return self.actor(VGroup(links,core,tools))

    def machine(self, scale=1, role="base"):
        # Role, not decorative variation, determines depth and internal capacity.
        layer_count={"specialist":2,"early":3,"intermediate":4,"base":5}[role]
        coordinates=[-0.62,0.62] if role=="specialist" else [-0.75,0,0.75]
        vertical_coordinates=[-0.62,0.62] if role=="specialist" else [-0.65,0,0.65]
        connections=[(0,1),(0,2),(1,3)] if role=="specialist" else [(0,3),(1,3),(1,4),(2,5),(3,7),(4,6),(4,7),(5,8)]
        plates=VGroup()
        for i in range(layer_count):
            face=RoundedRectangle(width=2.4,height=2.2,corner_radius=0.08,color=self.cyan,fill_color="#192F45",fill_opacity=1,stroke_width=2).shift(RIGHT*i*0.22+UP*i*0.16)
            nodes=VGroup(*[Dot([x,y,0],radius=0.065,color=self.yellow) for x in coordinates for y in vertical_coordinates])
            edges=VGroup(*[Line(nodes[a].get_center(),nodes[b].get_center(),color=self.purple,stroke_width=1.5,stroke_opacity=0.7) for a,b in connections])
            cells=VGroup(edges,nodes).shift(RIGHT*i*0.22+UP*i*0.16)
            plates.add(VGroup(face,cells))
        return self.actor(plates.move_to(ORIGIN).scale(scale))

    def car(self):
        body=RoundedRectangle(width=3.5,height=1.05,corner_radius=0.3,color=self.cyan,fill_color="#203E55",fill_opacity=1)
        roof=Polygon([-1.1,0.5,0],[-0.7,1.3,0],[0.8,1.3,0],[1.4,0.5,0],color=self.cyan,fill_color="#162A3F",fill_opacity=1)
        wheels=VGroup(*[VGroup(Circle(radius=0.35,color=self.paper,fill_color="#0B1020",fill_opacity=1),Dot(radius=0.1,color=self.paper)).move_to([x,-0.5,0]) for x in [-1.05,1.05]])
        return self.actor(VGroup(roof,body,wheels))

    def blink(self, person):
        return ApplyMethod(person[2].stretch, 0.12, 1, rate_func=there_and_back)

    def confused(self, person):
        return self.react_confused(person)

    def celebrate_subtle(self, person):
        return Rotate(person[3], angle=0.10, rate_func=there_and_back)

    def cue(self, name, seconds):
        super().cue(name, seconds)
        self.audit_composition(name)

    def audit_composition(self, beat):
        frame=self.camera.frame
        left,right=frame.get_left()[0],frame.get_right()[0]
        bottom,top=frame.get_bottom()[1],frame.get_top()[1]
        subjects=[]
        for obj in self.mobjects:
            if obj is frame or not obj.has_points() and not obj.submobjects:
                continue
            leaves=[]
            for leaf in obj.family_members_with_points():
                if isinstance(leaf, VMobject):
                    opacity=max(float(np.max(leaf.get_fill_opacity())),float(np.max(leaf.get_stroke_opacity())))
                    if opacity<0.1:continue
                x0=max(left,leaf.get_left()[0]);x1=min(right,leaf.get_right()[0])
                y0=max(bottom,leaf.get_bottom()[1]);y1=min(top,leaf.get_top()[1])
                if x1>x0 and y1>y0:leaves.append((x0,y0,x1,y1))
            if not leaves:continue
            x0=min(b[0] for b in leaves);y0=min(b[1] for b in leaves)
            x1=max(b[2] for b in leaves);y1=max(b[3] for b in leaves)
            subjects.append({"type":type(obj).__name__,"text":getattr(obj,"text",None),"width_fraction":round((x1-x0)/frame.width,4),"height_fraction":round((y1-y0)/frame.height,4),"envelope_area_fraction":round((x1-x0)*(y1-y0)/(frame.width*frame.height),4)})
        subjects.sort(key=lambda x:x["envelope_area_fraction"],reverse=True)
        self.composition_beats.append({"beat":beat,"seconds":round(self._logical_time,3),"camera_width":round(frame.width,3),"largest_subjects":subjects[:4],"below_30_percent":bool(not subjects or subjects[0]["envelope_area_fraction"]<0.3)})

    def save_composition_audit(self):
        import os,json
        from pathlib import Path
        output=os.environ.get("VIDEO_PIPELINE_OBSERVATION_PATH")
        if output:
            Path(output).with_suffix(".composition.json").write_text(json.dumps({"method":"Clipped visible root envelopes in camera coordinates; estimate, not ink area or semantic identification. Thin hero typography, first GPT, spatial distance and empty question hold require narrative review.","beats":self.composition_beats},ensure_ascii=False,indent=2))

    def gpt_logo(self, width=4):
        # Original user image, unchanged; a light backing supplies contrast.
        backing=RoundedRectangle(width=width,height=width,corner_radius=width*0.16,stroke_width=0,fill_color=self.ink,fill_opacity=1)
        mark=ImageMobject(str(ASSET_ROOT / "chatgpt.png")).set_width(width*0.96)
        return Group(backing,mark)
class AEscalaDosGPTsScene(Director, VisualScene, MovingCameraScene):
    def construct(self):
        self.begin()
        self.camera.frame.set_width(10.8)
        machine=self.machine(role="early").move_to(ORIGIN)
        name=self.label("GPT-1",62,self.ink,[0,2.5,0])
        count=self.label("0M",46,self.yellow,[0,-2.3,0])
        self.add(machine)
        self.play(Write(name),run_time=1.6)
        initial_count=ValueTracker(0)
        count.add_updater(lambda m:m.become(self.text(f"{initial_count.get_value():.0f}M",46,self.yellow).move_to([0,-2.3,0])))
        self.add(count)
        self.at(3.8,initial_count.animate.set_value(117),duration=1.5)
        count.clear_updaters()
        for t in [9.8,13.1,17]:
            text=self.label("texto",26,self.cyan,[-5,0,0]);self.until(t);self.add(text)
            self.at(t,text.animate.move_to(machine[0]).scale(0.08),duration=1.5);self.remove(text)
            self.play(machine[-1][1].animate.set_color(self.green if t>12 else self.yellow),run_time=0.8)
        previous=machine.copy().move_to([-7,-2.3,0])
        ref1=self.label("GPT-1 · 117M",28,self.paper,[-7,-4.6,0])
        n2=self.label("GPT-2",70,self.ink,[0,5.7,0])
        growth2=ValueTracker(0.117)
        c2=self.label("0.117B",56,self.yellow,[0,-5.8,0])
        c2.add_updater(lambda m:m.become(self.text(f"{growth2.get_value():.3f}B",56,self.yellow).set_height(self.camera.frame.height*0.04).move_to([0,self.camera.frame.get_bottom()[1]+self.camera.frame.height*0.075,0])))
        self.at(22.4,FadeOut(count),FadeOut(name),duration=0.4)
        self.until(23);self.add(c2)
        robust=self.capacity(5,3).set_height(9.8).move_to(ORIGIN)
        self.at(23,growth2.animate.set_value(1.5),Transform(machine,robust),FadeIn(n2),FadeIn(previous),FadeIn(ref1),self.camera.frame.animate.set_width(24),duration=3)
        c2.clear_updaters();c2.become(self.label("1.5B",56,self.yellow,[0,-5.8,0]))
        self.at(26.7)
        for i,t in enumerate([33,36.5,40]):
            text=VGroup(*[Line([-1,y,0],[1,y,0],color=self.cyan,stroke_width=3) for y in [0.3,0,-0.3]]).move_to(machine.get_right())
            self.until(t);self.add(text)
            self.at(t,text.animate.shift(RIGHT*(4+i)).scale(1.2),machine[-1][1].animate.set_color(self.green),duration=2.3)
            self.play(FadeOut(text),run_time=0.7)
        prev2=machine.copy()
        ref2=self.label("GPT-2 · 1.5B",65,self.paper,[-20,-12,0]).set_height(1.25)
        self.add(prev2)
        self.at(43.4,previous.animate.move_to([-33,-10,0]),ref1.animate.set_height(1.1).move_to([-33,-13,0]),prev2.animate.set_height(11).move_to([-20,-3,0]),FadeIn(ref2),FadeOut(n2),FadeOut(c2),duration=1.7)
        n3=self.label("GPT-3",130,self.ink,[12,21,0]).set_height(2.5)
        growth3=ValueTracker(1.5)
        c3=self.label("1.5B",110,self.yellow,[12,-21,0])
        c3.add_updater(lambda m:m.become(self.text(f"{growth3.get_value():.1f}B",110,self.yellow).set_height(self.camera.frame.height*0.045).move_to([self.camera.frame.get_x(),self.camera.frame.get_bottom()[1]+self.camera.frame.height*0.075,0])).set_z_index(8).set_stroke(self.camera.background_color,width=5,background=True))
        self.until(45.75);self.add(c3)
        # New plates AND denser cells. First exceed the view; camera catches up twice.
        giant=self.capacity(9,4).set_height(27).move_to([12,0,0])
        self.at(45.75,Transform(machine,giant),growth3.animate.set_value(40),duration=0.85)
        self.at(46.65,self.camera.frame.animate.move_to([2,0,0]).set_width(46),growth3.animate.set_value(80),duration=0.9)
        self.at(47.6,machine.animate.set_height(38),growth3.animate.set_value(130),duration=0.75)
        self.at(48.45,self.camera.frame.animate.set_width(88),growth3.animate.set_value(175),FadeIn(n3),duration=1.35)
        c3.clear_updaters()
        self.play(c3.animate.move_to([12,-21,0]),run_time=0.2)
        self.at(50.0)
        self.checkpoint("scaling-payoff-three-generations-visible")
        # Resolve the large model into the same canonical base used by the next scene.
        canonical=self.machine().set_width(3.8).move_to([0,-0.35,0])
        self.at(52,FadeOut(previous),FadeOut(ref1),FadeOut(prev2),FadeOut(ref2),FadeOut(n3),FadeOut(c3),Transform(machine,canonical),self.camera.frame.animate.move_to(ORIGIN).set_width(14.22),duration=2.5)
        self.remove(machine);self.add(canonical);machine=canonical
        # A single anchored stage: camera visits ports and layers; the model stays put.
        self.task_output=None
        self.task_input=None
        self.context_strip=None
        self.task_records=[]
        self.completed_outputs=[]
        self.stage_model=machine
        self.stage_geometry=self.geometry_signature(machine)
        self.stage_identity=id(machine)
        self.front_view=machine.copy()
        self.demonstrate(machine,55.2,"casa → house\ncachorro →", "dog",layout=0,pace=1.0)
        self.demonstrate(machine,61.0,"O filme foi incrível.","POSITIVO",layout=1,pace=0.9)
        task_title=self.label("MESMO MODELO",48,self.yellow,[0,2.6,0])
        self.at(66.1,self.camera.frame.animate.move_to(ORIGIN).set_width(14.22),duration=1.0)
        self.demonstrate(machine,69.1,"Resuma:\nO cachorro pegou a bola\ne voltou.","O cachorro buscou\na bola.",layout=0,pace=0.85)
        self.demonstrate(machine,75.5,"somar a e b","somar(a,b)",layout=2,pace=0.85,instruction="Aja como um programador")
        self.demonstrate(machine,82.5,"Gostei muito!","POSITIVO",layout=1,pace=0.72)
        self.demonstrate(machine,89.0,"Resuma:\nChoveu muito.\nA rua alagou.","Chuva causou\nalagamento.",layout=2,pace=0.65)
        self.demonstrate(machine,95.2,"2 + 2","4",layout=0,pace=0.58)
        # Six actual results return for the final, readable multiplicity payoff.
        echoes=VGroup()
        positions=[[-4.8,1.7,0],[4.8,1.7,0],[-4.8,0,0],[4.8,0,0],[-4.8,-1.9,0],[4.8,-1.9,0]]
        for index,position in zip([0,1,2,3,5,6],positions):
            short={0:"dog",1:"POSITIVO",2:"Buscou a bola.",3:"somar(a,b)",5:"Alagamento",6:"4"}
            echo=self.text(short[index],36,self.green)
            echo.scale(min(3.6/echo.width,0.72/echo.height)).move_to(position).set_opacity(0.95)
            echoes.add(echo)
        task_title.set_width(6.4).move_to([0,3.3,0])
        self.at(98.0,FadeOut(self.task_output),Transform(machine,self.front_view),self.camera.frame.animate.set_width(15.5).move_to([0,0.4,0]),duration=0.55)
        self.at(98.55,LaggedStart(*[FadeIn(e) for e in echoes],lag_ratio=0.08),Write(task_title),duration=0.25)
        self.checkpoint("six-completed-results-payoff")
        self.at(101.4,LaggedStart(*[e.animate.scale(0.025).move_to(machine.get_center()).set_opacity(0) for e in echoes],lag_ratio=0.08),duration=0.6)
        self.remove(echoes,*echoes)
        self.task_output=None
        base=self.label("modelo-base",62,self.yellow,[0,2.5,0])
        expanded=self.front_view.copy()
        for i,plate in enumerate(expanded):plate.shift(RIGHT*(i-2)*0.55+UP*(i-2)*0.15)
        expanded.set_width(6.6).move_to([0,-0.5,0])
        self.at(102,Succession(FadeOut(task_title),Write(base)),Transform(machine,expanded),self.camera.frame.animate.move_to(ORIGIN).set_width(14.22),duration=2.0)
        self.at(104.4,Transform(machine,self.front_view.copy().set_width(4.8).move_to([0,-0.3,0])),duration=2.3)
        self.finish(107.708333333)
        import os,json
        from pathlib import Path
        output=os.environ.get("VIDEO_PIPELINE_OBSERVATION_PATH")
        if output:Path(output).with_suffix('.causality.json').write_text(json.dumps(self.task_records,indent=2,ensure_ascii=False))

    def capacity(self,depth,density):
        plates=VGroup()
        for i in range(depth):
            offset=RIGHT*i*0.25+UP*i*0.2
            face=RoundedRectangle(width=2.4,height=2.2,corner_radius=0.08,color=self.cyan,fill_color="#192F45",fill_opacity=1,stroke_width=2)
            nodes=VGroup(*[Dot([x,y,0],radius=0.055,color=self.yellow) for x in np.linspace(-0.78,0.78,density) for y in np.linspace(-0.68,0.68,density)])
            edges=VGroup(*[Line(nodes[j].get_center(),nodes[j+density].get_center(),color=self.purple,stroke_width=1.5,stroke_opacity=0.7) for j in range(density*(density-1))])
            plates.add(VGroup(face,VGroup(edges,nodes)).shift(offset))
        return plates.move_to(ORIGIN)

    def geometry_signature(self,machine):
        import hashlib
        points=np.concatenate([m.get_points() for m in machine.family_members_with_points()])
        return hashlib.sha256(np.round(points,5).tobytes()).hexdigest()

    def demonstrate(self,machine,t,prompt,result,layout,pace,instruction=None):
        # The same plates deform continuously into another projection, never a new model.
        profile=self.front_view.copy()
        for i,plate in enumerate(profile):
            center=plate.get_center()
            # Projection changes positions and plate outline, never node aspect ratio.
            def project(point):
                delta=point-center
                return center+np.array([delta[0]*0.62,delta[1]+delta[0]*0.20,0])
            plate[0].apply_function(project)
            for edge in plate[1][0]:edge.apply_function(project)
            for node in plate[1][1]:node.move_to(project(node.get_center()))
            plate.move_to([-2.25+i*0.98,-0.65+i*0.16,0])
        close_processing=layout==1 and len(self.task_records)==1
        output_focus=layout==1 and not close_processing
        target=profile if close_processing else self.front_view.copy()
        if output_focus:target.move_to([-2.6,-0.35,0])
        side=layout==0 and len(self.task_records)>0
        if side:target.shift(RIGHT*1.25)
        views=[([4.6,0.2,0],11.8,[3.8,2.1,0],RIGHT) if side else ([0,0,0],14.22,[-4.9,0,0],LEFT),
               ([0,0.4,0],10.6,[0,2.5,0],UP),
               ([0,0.55,0],16.5,[-4.9,0,0],LEFT)]
        focus,width,entry,direction=views[layout]
        exit=[6.5,-0.6,0] if side else [4.8,0,0]
        cleanup=[]
        for old in [self.task_input,self.task_output,self.context_strip]:
            if old is not None:cleanup.append(FadeOut(old,shift=RIGHT*0.3))
        self.at(t,*cleanup,Transform(machine,target),self.camera.frame.animate.set_width(width).move_to(focus),duration=0.6*pace)
        self.context_strip=None
        incoming=self.label(prompt,34,self.cyan,entry)
        if incoming.width>(5.8 if layout==1 else 3.8):incoming.scale_to_fit_width(5.8 if layout==1 else 3.8)
        if instruction:
            strip=self.card(instruction,self.ink,size=34,width=5.8).move_to([-4.6,1.65,0])
            strip[0].set_stroke(self.purple,width=4).set_fill("#0E1526",opacity=1)
            strip.set_width(min(strip.width,6.5))
            strip[1].set_color(WHITE).set_opacity(1).set_z_index(10)
            heading=self.label("PERSONA",30,self.yellow,[-4.6,2.45,0]).set_z_index(10)
            route=Line(strip.get_bottom(),incoming.get_top()+UP*0.12,color=self.purple,stroke_width=3)
            panel=VGroup(strip,heading,route)
            self.play(FadeIn(strip,shift=DOWN*0.15),FadeIn(heading),Create(route),run_time=0.45)
            context=RoundedRectangle(width=0.38,height=0.14,corner_radius=0.03,fill_color=self.purple,fill_opacity=1,stroke_width=0).move_to(strip.get_right())
            self.add(context)
            self.play(context.animate.move_to(machine.get_top()),run_time=0.45);self.remove(context)
            self.play(ShowPassingFlash(machine[-1][0].copy().set_fill(opacity=0).set_stroke(self.purple,width=3)),run_time=0.35)
            self.context_strip=panel
        elif layout==2:
            strip=self.card("CONTEXTO",self.ink,size=30,width=4.4).move_to([-4.9,1.65,0])
            strip[0].set_stroke(self.purple,width=3).set_fill("#29334D",opacity=1)
            route=Line(strip.get_bottom(),incoming.get_top()+UP*0.12,color=self.purple,stroke_width=3)
            self.play(FadeIn(strip),Create(route),run_time=0.35)
            self.context_strip=VGroup(strip,route)
        self.play(FadeIn(incoming,shift=direction*0.5),run_time=0.65*pace)
        self.wait(0.4*pace)
        approach=[incoming.animate.move_to(machine.get_critical_point(direction)).scale(0.045)]
        if self.context_strip is not None:
            approach.append(self.context_strip[-1].animate.put_start_and_end_on(self.context_strip[0].get_right(),machine.get_top()))
        if close_processing:
            # The arriving input takes us to a close view of the same plate stack.
            approach.append(self.camera.frame.animate.move_to(machine[0].get_center()).set_width(6.8))
        self.play(*approach,run_time=0.7*pace)
        self.remove(incoming)
        arrived=self._logical_time
        signal=Dot(machine[0].get_center(),radius=0.065,color=self.green).set_z_index(8)
        self.add(signal)
        for i,plate in enumerate(machine):
            pulse=plate[0].copy().set_fill(opacity=0).set_stroke(self.green,width=5).set_z_index(8)
            self.add(pulse)
            # Visible layer surface and cells react only while the signal reaches them.
            moves=[signal.animate.move_to(plate.get_center()),Indicate(plate[1][1],color=self.green,scale_factor=1)]
            if close_processing:moves.append(self.camera.frame.animate.move_to(plate.get_center()+RIGHT*0.5).set_width(6.8))
            self.play(*moves,run_time=0.2*pace)
            self.remove(pulse)
        self.remove(signal)
        processed=self._logical_time
        outgoing=self.label(result,36,self.green,exit)
        if outgoing.width>4.0:outgoing.scale_to_fit_width(4.0)
        if layout==1:outgoing.set_width(3.6).next_to(machine,RIGHT,buff=0.4).set_y(0)
        seed=outgoing.copy().scale(0.035).move_to(machine.get_right());self.add(seed)
        motions=[Transform(seed,outgoing)]
        if layout==1:motions.append(self.camera.frame.animate.move_to([1.4,0,0]).set_width(12.4))
        self.play(*motions,run_time=0.8*pace)
        self.task_input=None;self.task_output=seed
        self.completed_outputs.append(outgoing.copy())
        signature=self.geometry_signature(machine)
        assert id(machine)==self.stage_identity and len(machine)==5,"The same five-layer model must persist"
        self.checkpoint('task-output-'+str(len(self.task_records)+1))
        self.task_records.append(dict(prompt=prompt,result=result,instruction=instruction,layout=layout,input_arrived=arrived,processing_finished=processed,output_visible=self._logical_time,layers=len(machine),model_geometry=signature,model_identity_preserved=id(machine)==self.stage_identity,view="profile-close" if layout==1 else "side-crop" if side else "wide" if layout==2 else "medium",model_center=machine.get_center().tolist(),model_width=machine.width,camera_center=self.camera.frame.get_center().tolist(),camera_width=self.camera.frame.width,rest_color=self.yellow,processing_color=self.green))
