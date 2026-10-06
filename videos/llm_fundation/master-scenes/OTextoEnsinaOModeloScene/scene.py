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
class AssetPolish:
    def person(self,mood="curious",color=None):
        person=super().person(mood,color)
        person[0].scale(0.94).set_stroke(width=2.5)
        person[1].scale(0.94).set_stroke(width=3)
        person[2].scale(0.9).shift(DOWN*0.02)
        person[3].set_stroke(width=2)
        person[4].set_stroke(width=2.5)
        for limb in [*person[5],*person[6]]:limb.set_stroke(width=3)
        return person

    def open_book(self):
        book=super().open_book()
        book[0].set_stroke(width=2.3)
        book[1].set_stroke(width=2.3)
        book[2].set_stroke(width=1.6)
        book[3].set_stroke(width=1.2,opacity=0.32)
        return book
class OTextoEnsinaOModeloScene(AssetPolish, Director, VisualScene, MovingCameraScene):
    def construct(self):
        self.begin()
        person=self.person().scale(1.2).move_to([4.7,-0.5,0])
        page=self.paper_page("",width=4.2,height=4.5).move_to([-2.7,0,0])
        page[3].set_opacity(0)
        exercise=self.label("cachorro",29,self.cyan,[-2.4,0.65,0])
        prefix=self.label("O",29,self.paper).next_to(exercise,LEFT,buff=0.12)
        context=VGroup(prefix,self.label("correu pelo parque.",27,self.paper,[-2.7,0.0,0]).set_width(3.5),self.label("Depois voltou para casa.",25,self.paper,[-2.7,-0.5,0]).set_width(3.6))
        context.set_z_index(5)
        hook=self.label("Quem ensina?",76,self.ink).set_width(11.2)
        self.add(hook)
        self.play(ReplacementTransform(hook,exercise),FadeIn(page),FadeIn(context),FadeIn(person),run_time=1.8)
        manual_frame_width=self.camera.frame.width
        manual_frame_center=self.camera.frame.get_center().copy()
        self.at(1.85,self.camera.frame.animate.set_width(6.8).move_to([-2.0,-0.35,0]),duration=1.6)
        hand=self.hand().scale(0.7).move_to([-0.1,-0.5,0]).set_z_index(8)
        open_hand=hand.copy()
        def pose(position,pressed=False):
            target=open_hand.copy().move_to(position)
            if pressed:
                # Fold only fingertips; the palm and cuff keep their identity.
                origin=target[0].get_center()
                target[0].apply_function(lambda p: p+DOWN*max(0,p[1]-origin[1])*0.65)
            return target
        answer=self.label("animal",38,self.green,[-2.7,-1.4,0])
        self.at(3.73,FadeIn(hand,shift=LEFT),self.look_at(person,page),duration=0.45)
        self.play(Transform(hand,pose([-1.55,-0.55,0])),run_time=0.45)
        self.play(Transform(hand,pose([-1.55,-0.66,0],True)),page.animate.shift(DOWN*0.06),run_time=0.3)
        self.play(Write(answer),page[0].animate.set_stroke(self.green).set_fill("#203D38"),run_time=0.55)
        self.play(Transform(hand,pose([-1.25,-0.35,0])),page.animate.shift(UP*0.06),self.look_at(person,answer),run_time=0.45)
        self.at(6.6,Transform(hand,pose([-1.4,0.45,0])),duration=0.65)
        self.play(Transform(hand,pose([-1.25,-0.35,0])),run_time=0.6)
        rng=np.random.default_rng(16)
        pages=VGroup(*[self.paper_page("").scale(0.48+rng.uniform(-0.04,0.04)).rotate(rng.uniform(-0.10,0.10)).move_to([-5.5+i%7*1.75+rng.uniform(-0.2,0.2),1.6-i//7*1.35+rng.uniform(-0.15,0.15),0]) for i in range(21)])
        stamps=VGroup()
        self.at(8.65,self.camera.frame.animate.set_width(manual_frame_width).move_to(manual_frame_center),FadeOut(exercise),FadeOut(context),FadeOut(answer),FadeOut(page),LaggedStart(*[FadeIn(p,shift=LEFT*0.5) for p in list(pages)[:7]],lag_ratio=0.05),person.animate.set_opacity(0.4).move_to([5.6,-2.7,0]).scale(0.55),Transform(hand,pose([-5.6,1.1,0])),duration=1.2)
        for index,speed in enumerate([0.5,0.36,0.25,0.18]):
            doc=pages[index]
            if index in [1,2]:
                self.play(LaggedStart(*[FadeIn(p,shift=UP*0.15) for p in list(pages)[index*7:(index+1)*7]],lag_ratio=0.03),run_time=0.35)
            point=doc.get_center()+RIGHT*0.35
            self.play(Transform(hand,pose(point)),run_time=speed)
            self.play(Transform(hand,pose(point+DOWN*0.08,True)),doc.animate.shift(DOWN*0.06),run_time=speed*0.55)
            stamp=self.label("✓",24,self.green,doc.get_center()+DOWN*0.2);stamps.add(stamp)
            self.play(FadeIn(stamp),doc[0].animate.set_stroke(self.green).set_fill("#203D38"),run_time=speed*0.5)
            self.play(Transform(hand,pose(point+UP*0.2)),doc.animate.shift(UP*0.06),run_time=speed*0.55)
        self.until(16.89)
        self.remove(stamps,*stamps)
        forum=self.browser("",width=10.8,height=5.4)
        question=self.label("Quanto é 2 + 2?",54,self.ink,[0,1.5,0])
        self.at(16.89,FadeOut(pages),FadeOut(person),FadeOut(hand),FadeIn(forum),Write(question),duration=1.8)
        wrong=self.label("5",86,self.ink,[0,0,0])
        person.set_opacity(1).set_height(2.0).move_to([4,-0.4,0])
        self.at(22.4,Write(wrong),FadeIn(person),duration=1.3)
        self.play(self.look_at(person,wrong),self.confused(person),run_time=0.8)
        correction=self.label("4",86,self.green,[0,0,0]);strike=Line([-0.6,-0.3,0],[0.6,0.3,0],color=self.pink,stroke_width=5)
        self.at(25.19,Create(strike),duration=0.8)
        self.play(FadeOut(wrong,shift=DOWN*0.4),FadeOut(strike),FadeIn(correction,shift=UP*0.4),run_time=1.2)
        model=self.machine().set_width(3.8).move_to([3.7,0,0])
        # The corrected forum becomes one document in the corpus, not a discarded slide.
        forum_document=VGroup(forum,question,correction)
        self.at(28.15,forum_document.animate.set_width(4.3).move_to([-4,0,0]),FadeOut(person),FadeIn(model),duration=1.5)
        rng_corpus=np.random.default_rng(314)
        documents=VGroup()
        for depth in range(3):
            for row in range(8):
                for col in range(13):
                    x=-12.8+col*1.85+depth*0.32+rng_corpus.uniform(-0.18,0.18)
                    y=-6.8+row*1.9+depth*0.28
                    page=Rectangle(width=1.28,height=1.58,color=self.paper,stroke_width=0.8,fill_color="#192F45",fill_opacity=1)
                    lines=VGroup(*[Line([-0.46,v,0],[0.46,v,0],color=self.cyan,stroke_width=0.7) for v in [0.4,0.1,-0.2,-0.5]])
                    document=VGroup(page,lines).scale([0.72,0.88,1.0][depth]).move_to([x,y,0]).set_z_index(-4+depth)
                    documents.add(document)
        documents=VGroup(*sorted(documents,key=lambda p:np.linalg.norm(p.get_center()-forum_document.get_center())))
        # Three staged waves: legible examples, many pages, then four-edge excess.
        self.at(30.0,LaggedStart(*[GrowFromPoint(p,forum_document.get_center()) for p in list(documents)[:24]],lag_ratio=0.025),duration=1.1)
        self.at(31.21,LaggedStart(*[GrowFromPoint(p,forum_document.get_center()) for p in list(documents)[24:104]],lag_ratio=0.012),self.camera.frame.animate.set_width(18.5).move_to([-1.5,0.2,0]),duration=1.4)
        self.at(32.7,LaggedStart(*[GrowFromPoint(p,forum_document.get_center()) for p in list(documents)[104:]],lag_ratio=0.004),duration=1.3)
        self.checkpoint("corpus-four-edge-saturation")
        # Two narrated ingestion waves. Quantity resolves into the same model.
        for wave,t in enumerate([34.51,38.31]):
            batch=list(documents)[wave::2]
            self.at(t,LaggedStart(*[page.animate.move_to(model.get_left()).scale(0.025).set_opacity(0) for page in batch],lag_ratio=0.025),self.camera.frame.animate.move_to([0,0,0]).set_width(15.5 if wave==0 else 12.8),duration=2.5 if wave==0 else 1.5)
            self.remove(*batch)
            self.play(Indicate(model[-1][1],color=self.green,scale_factor=1.03),run_time=0.3)
        source=self.open_book().set_width(4.3).move_to([-4,0,0])
        self.at(40.2,FadeOut(forum_document,shift=RIGHT*0.3),FadeIn(source,shift=RIGHT*0.3),duration=0.6)
        self.remove(documents,*documents)
        model[-1][1].set_color(self.yellow)
        # Restore the exact stage before 04:47; the protected probability take follows unchanged.
        for t in [41.1,43.81]:
            strip=VGroup(*[Line([-0.55,y,0],[0.55,y,0],color=self.cyan,stroke_width=3) for y in [0.3,0,-0.3]]).move_to(source.get_right())
            self.until(t);self.add(strip)
            self.at(t,strip.animate.move_to(model[0]).scale(0.1),duration=1.5);self.remove(strip)
            self.play(model[-1][1].animate.set_color(self.green if t>40 else self.yellow),run_time=0.6)
        self.play(FadeOut(source),FadeOut(model),self.camera.frame.animate.set_width(14.22),run_time=0.8)
        self.cue("esta-frase", 46.97)
        first=self.text("O cachorro correu",72,self.ink).set_width(12.0).move_to([0,1.0,0])
        second=self.text("atrás da",72,self.ink).set_width(5.1).move_to([-3.4,-0.65,0])
        phrase=VGroup(first,second)
        blank=Line([-0.1,-1.15,0],[5.8,-1.15,0],color=self.cyan,stroke_width=5)
        self.track(phrase,"dog-context-text");self.track(blank,"hidden-target")
        self.play(Write(phrase),Create(blank),run_time=1.6)
        self.cue("prever-token",50.57)
        self.play(first.animate.move_to([0,2.9,0]),second.animate.move_to([-3.4,1.65,0]),blank.animate.shift(UP*2.3),run_time=0.7)
        names=["comida","gato","carro","lua","rua","porta","bola","casa"]
        values=[28,19,14,3,12,10,6,8]
        labels=VGroup();bars=VGroup();numbers=VGroup()
        rows=[]
        def bar(v,y,color):
            return Rectangle(width=max(v*0.098,0.06),height=0.24,stroke_width=0,fill_color=color,fill_opacity=0.9).move_to([-3.65+max(v*0.098,0.06)/2,y,0])
        for i,(name,v) in enumerate(zip(names,values)):
            y=0.45-i*0.46
            lab=self.text(name,27,self.cyan).move_to([-5.05,y,0])
            b=bar(v,y,self.cyan);num=self.text(str(v)+"%",24,self.ink).move_to([5.4,y,0])
            labels.add(lab);bars.add(b);numbers.add(num);rows.append(VGroup(lab,b,num))
        self.play(LaggedStart(*[FadeIn(r,shift=RIGHT*0.6) for r in rows],lag_ratio=0.055),run_time=1.4)
        guess=labels[0].copy()
        self.cue("ele-erra",53.97)
        self.play(guess.animate.set_width(4.2).move_to([2.9,1.65,0]).set_color(self.pink),run_time=1)
        self.cue("parametros-ajustados",55.2)
        feedback=self.curve(guess.get_bottom(),bars[0].get_right(),bend=-0.5,color=self.pink,width=5)
        self.play(Create(feedback),blank.animate.set_color(self.pink),run_time=1.4)
        self.cue("tenta-de-novo",57.2)
        self.play(guess.animate.move_to(labels[0]).set_width(labels[0].width),Uncreate(feedback),blank.animate.set_color(self.cyan),run_time=0.8);self.remove(guess)
        def rebalance(vals,duration):
            order=sorted(range(8),key=lambda i:vals[i],reverse=True)
            ys={i:0.45-rank*0.46 for rank,i in enumerate(order)}
            self.play(*[labels[i].animate.set_y(ys[i]) for i in range(8)],*[Transform(bars[i],bar(v,ys[i],self.cyan)) for i,v in enumerate(vals)],*[Transform(numbers[i],self.text(str(v)+"%",24,self.ink).move_to([5.4,ys[i],0])) for i,v in enumerate(vals)],run_time=duration)
        self.cue("repetir-em-escala",59.19)
        rebalance([20,25,12,3,9,7,18,6],2)
        self.until(62.2);rebalance([16,19,10,2,7,5,36,5],2)
        self.until(65);rebalance([11,12,6,1,5,4,58,3],1.5)
        self.cue("bola",66.97)
        answer=labels[6]
        self.remove(rows[6]);self.add(answer,bars[6],numbers[6])
        self.play(answer.animate.set_color(self.green).set_width(7).move_to([-1.5,-0.1,0]),bars[6].animate.set_color(self.green),*[r.animate.shift(DOWN*0.55).set_opacity(0.16) for i,r in enumerate(rows) if i!=6],run_time=0.9)
        destination=answer.copy().set_height(second.height).move_to([2.9,1.65,0])
        path=ArcBetweenPoints(answer.get_center(),destination.get_center(),angle=-0.45)
        self.play(MoveAlongPath(answer,path),run_time=0.7)
        self.play(Transform(answer,destination),blank.animate.set_color(self.green).stretch(1.06,0),run_time=0.45)
        self.play(blank.animate.stretch(1/1.06,0),run_time=0.2)
        dot=self.text(".",72,self.green).next_to(answer,RIGHT,buff=0.04).align_to(answer,DOWN);self.add(dot)
        self.cue("lua",70.67)
        self.play(labels[3].animate.set_opacity(1),bars[3].animate.set_opacity(1),numbers[3].animate.set_opacity(1),run_time=0.7)
        self.cue("texto-bruto-vira-treino",74.15)
        self.play(*[r.animate.shift(DOWN*8) for i,r in enumerate(rows) if i!=6],bars[6].animate.shift(DOWN*8),numbers[6].animate.shift(DOWN*8),Uncreate(blank),run_time=0.8)
        for r in rows:self.remove(r)
        self.remove(bars[6],numbers[6]);self.add(answer)
        self.remove(labels,bars,numbers,phrase,*rows,first,second,answer,dot)
        completed=VGroup(first,second,answer,dot)
        self.add(completed)
        self.play(completed.animate.move_to([0,0.7,0]),run_time=0.9)
        context=SurroundingRectangle(completed,buff=0.25,color=self.cyan,corner_radius=0.12)
        tag=self.text("ENTRADA DO PRÓXIMO CICLO",28,self.cyan).next_to(context,DOWN,buff=0.35)
        self.play(Create(context),Write(tag),answer.animate.set_color(self.cyan),run_time=1)
        self.until(78)
        self.play(Uncreate(context),FadeOut(tag),completed.animate.set_width(5.8).move_to([-3.4,0,0]),run_time=1.1)
        model=self.machine().set_width(5.4).move_to([3.5,0,0]);self.play(Create(model),run_time=1)
        continuations=VGroup()
        for index,(t,word) in enumerate([(81,"Depois"),(84,"voltou"),(87,".")]):
            self.until(t)
            speed=[0.65,0.5,0.3][index]
            signal=Dot(completed.get_right()+RIGHT*0.1,radius=0.07,color=self.cyan);self.add(signal)
            self.play(signal.animate.move_to(model.get_left()),run_time=speed);self.remove(signal)
            token=self.text(word,30,self.green).move_to([3.7,-2.5,0])
            self.play(Indicate(model[-1][1],color=self.green,scale_factor=1),run_time=speed*0.25)
            self.play(Write(token),run_time=speed*0.35)
            target=[-5+index*1.7,-1.4,0]
            self.play(MoveAlongPath(token,ArcBetweenPoints(token.get_center(),target,angle=-0.45)),run_time=speed*1.3)
            self.play(token.animate.set_color(self.cyan),run_time=0.15)
            continuations.add(token)
            completed.add(token)
        book=self.book("Livros").move_to([-3.5,0,0])
        self.play(ReplacementTransform(completed,book),model.animate.set_width(3.4).move_to([3,0,0]),run_time=0.9)
        self.until(90.583333333)
        self.checkpoint("final")
        self.audit_composition("final");self.save_composition_audit()
