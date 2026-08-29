"""Curated Manim Community references adapted from authorized 3b1b sources.

The upstream video repository targets ManimGL and private helpers, so raw scenes
are poor few-shot examples for this pipeline.  Each card keeps its provenance
but presents the technique as a small, self-contained Manim Community 0.21.0
scene.  Selection is deterministic and bounded for the local model's context.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

ReferenceTopic = Literal[
    "linear_algebra",
    "calculus",
    "neural_networks",
    "machine_learning",
    "transformers",
    "probability",
    "fourier",
    "convolution",
]

REFERENCE_TOPICS: tuple[ReferenceTopic, ...] = (
    "linear_algebra",
    "calculus",
    "neural_networks",
    "machine_learning",
    "transformers",
    "probability",
    "fourier",
    "convolution",
)

SOURCE_REPOSITORY = "https://github.com/3b1b/videos"
SOURCE_COMMIT = "674b966fbb6cf0307590d27744d186165e8b6a76"


@dataclass(frozen=True, slots=True)
class ReferenceExample:
    """One prompt-ready technique with traceable upstream inspiration."""

    identifier: str
    title: str
    topics: tuple[ReferenceTopic, ...]
    source_path: str
    source_scene: str
    techniques: tuple[str, ...]
    code: str

    @property
    def source_url(self) -> str:
        """Return an immutable link to the upstream source revision."""

        return f"{SOURCE_REPOSITORY}/blob/{SOURCE_COMMIT}/{self.source_path}"


REFERENCE_EXAMPLES: tuple[ReferenceExample, ...] = (
    ReferenceExample(
        identifier="linear-map-basis",
        title="Transform a grid and its basis vectors together",
        topics=("linear_algebra",),
        source_path="_2016/eola/chapter3.py",
        source_scene="FollowIHatJHat",
        techniques=("basis vectors", "matrix transformation", "object continuity"),
        code='''from manim import *

class LinearMapReference(Scene):
    def construct(self):
        plane = NumberPlane()
        basis = VGroup(
            Vector([1, 0], color=GREEN),
            Vector([0, 1], color=RED),
        )
        matrix = [[1, 1], [0, 1]]
        self.play(Create(plane), Create(basis))
        self.play(
            plane.animate.apply_matrix(matrix),
            *[vector.animate.apply_matrix(matrix) for vector in basis],
        )
        self.wait()
''',
    ),
    ReferenceExample(
        identifier="derivative-secant-tangent",
        title="Turn a secant into a tangent line",
        topics=("calculus",),
        source_path="_2017/eoc/chapter2.py",
        source_scene="SecantLineToTangentLine",
        techniques=("axes coordinates", "graph", "geometric limit"),
        code='''from manim import *

class DerivativeReference(Scene):
    def construct(self):
        axes = Axes(x_range=[-1, 3, 1], y_range=[0, 5, 1])
        graph = axes.plot(lambda x: x**2, x_range=[-1, 2.2], color=BLUE)
        secant = axes.get_secant_slope_group(1, graph, dx=1, secant_line_color=YELLOW)
        tangent = axes.get_secant_slope_group(1, graph, dx=0.01, secant_line_color=YELLOW)
        self.play(Create(axes), Create(graph))
        self.play(Create(secant))
        self.play(Transform(secant, tangent))
        self.wait()
''',
    ),
    ReferenceExample(
        identifier="neural-network-layers",
        title="Reveal a neural network layer by layer",
        topics=("neural_networks", "machine_learning", "linear_algebra"),
        source_path="_2017/nn/part1.py",
        source_scene="IntroduceEachLayer",
        techniques=("layered graph", "edges behind nodes", "lagged reveal"),
        code='''from manim import *

class NeuralNetworkReference(Scene):
    def construct(self):
        layers = VGroup(*[
            VGroup(*[Circle(radius=0.16) for _ in range(size)]).arrange(DOWN, buff=0.18)
            for size in (3, 4, 2)
        ]).arrange(RIGHT, buff=1.5)
        edges = VGroup(*[
            Line(left.get_center(), right.get_center(), stroke_opacity=0.35)
            for first, second in zip(layers, layers[1:])
            for left in first for right in second
        ])
        self.play(Create(edges), LaggedStart(*[Create(layer) for layer in layers], lag_ratio=0.3))
        self.play(layers[1].animate.set_fill(YELLOW, opacity=0.7))
        self.wait()
''',
    ),
    ReferenceExample(
        identifier="machine-learning-regression",
        title="Fit a model to data and expose residuals",
        topics=("machine_learning", "linear_algebra"),
        source_path="_2024/transformers/ml_basics.py",
        source_scene="LinearRegression",
        techniques=("data points", "model line", "residual geometry"),
        code='''from manim import *

class RegressionReference(Scene):
    def construct(self):
        axes = Axes(x_range=[0, 5, 1], y_range=[0, 5, 1])
        samples = [(1, 1.3), (2, 1.8), (3, 3.2), (4, 3.7)]
        dots = VGroup(*[Dot(axes.c2p(x, y), color=YELLOW) for x, y in samples])
        model = axes.plot(lambda x: 0.85 * x + 0.4, x_range=[0.5, 4.5], color=BLUE)
        residuals = VGroup(*[
            DashedLine(axes.c2p(x, y), axes.c2p(x, 0.85 * x + 0.4), color=RED)
            for x, y in samples
        ])
        self.play(Create(axes), LaggedStart(*[FadeIn(dot) for dot in dots]))
        self.play(Create(model), Create(residuals))
        self.wait()
''',
    ),
    ReferenceExample(
        identifier="attention-matrix",
        title="Connect tokens through an attention matrix",
        topics=("transformers", "machine_learning", "linear_algebra"),
        source_path="_2024/transformers/attention.py",
        source_scene="AttentionPatterns",
        techniques=("token rows", "weight matrix", "opacity encodes strength"),
        code='''from manim import *

class AttentionReference(Scene):
    def construct(self):
        tokens = VGroup(*[Text(word, font_size=28) for word in ("the", "model", "learns")])
        tokens.arrange(DOWN, aligned_edge=LEFT).to_edge(LEFT)
        weights = [[0.8, 0.1, 0.1], [0.2, 0.6, 0.2], [0.1, 0.3, 0.6]]
        cells = VGroup(*[
            Square(0.45).set_fill(BLUE, opacity=value).set_stroke(GREY, 1)
            for row in weights for value in row
        ]).arrange_in_grid(3, 3, buff=0.05)
        self.play(LaggedStart(*[Write(token) for token in tokens], lag_ratio=0.2))
        self.play(LaggedStart(*[FadeIn(cell) for cell in cells], lag_ratio=0.06))
        self.wait()
''',
    ),
    ReferenceExample(
        identifier="bayesian-update",
        title="Animate prior belief becoming a posterior",
        topics=("probability",),
        source_path="_2020/beta/beta3.py",
        source_scene="ShowBayesianUpdating",
        techniques=("distribution bars", "prior-to-posterior transform", "consistent labels"),
        code='''from manim import *

class BayesianUpdateReference(Scene):
    def construct(self):
        prior = BarChart([0.5, 0.5], y_range=[0, 1, 0.25], bar_colors=[BLUE, BLUE])
        posterior = BarChart([0.2, 0.8], y_range=[0, 1, 0.25], bar_colors=[BLUE, YELLOW])
        prior_label = Text("prior").next_to(prior, UP)
        posterior_label = Text("posterior").next_to(posterior, UP)
        self.play(Create(prior), Write(prior_label))
        self.play(Transform(prior, posterior), Transform(prior_label, posterior_label))
        self.wait()
''',
    ),
    ReferenceExample(
        identifier="fourier-partial-sums",
        title="Build a signal from successive Fourier partial sums",
        topics=("fourier",),
        source_path="_2019/diffyq/part2/fourier_series.py",
        source_scene="FourierSeriesIntroBackground4",
        techniques=("partial sums", "stable axes", "successive refinement"),
        code='''from manim import *

class FourierReference(Scene):
    def construct(self):
        axes = Axes(x_range=[-PI, PI, PI / 2], y_range=[-1.5, 1.5, 0.5])
        def partial_sum(x, terms):
            return sum(4 * np.sin((2 * k + 1) * x) / (PI * (2 * k + 1)) for k in range(terms))
        first = axes.plot(lambda x: partial_sum(x, 1), color=BLUE)
        refined = axes.plot(lambda x: partial_sum(x, 5), color=YELLOW)
        self.play(Create(axes), Create(first))
        self.play(Transform(first, refined))
        self.wait()
''',
    ),
    ReferenceExample(
        identifier="discrete-convolution-window",
        title="Slide a convolution kernel across discrete data",
        topics=("convolution", "machine_learning"),
        source_path="_2022/convolutions/discrete.py",
        source_scene="MovingAverageExample",
        techniques=("discrete cells", "sliding window", "local aggregation"),
        code='''from manim import *

class ConvolutionReference(Scene):
    def construct(self):
        values = [1, 3, 2, 4, 1]
        cells = VGroup(*[
            VGroup(Square(0.75), Integer(value)).arrange(IN, buff=0)
            for value in values
        ]).arrange(RIGHT, buff=0.08)
        window = SurroundingRectangle(VGroup(*cells[:3]), color=YELLOW, buff=0.08)
        self.play(LaggedStart(*[FadeIn(cell) for cell in cells], lag_ratio=0.1))
        self.play(Create(window))
        self.play(window.animate.move_to(VGroup(*cells[1:4])))
        self.play(window.animate.move_to(VGroup(*cells[2:5])))
        self.wait()
''',
    ),
)


def select_reference_examples(
    topics: Sequence[str],
    *,
    limit: int = 2,
) -> tuple[ReferenceExample, ...]:
    """Select a small, deterministic set that covers requested topics in order."""

    if limit < 0:
        raise ValueError("reference example limit cannot be negative")
    if limit == 0:
        return ()

    requested: list[str] = []
    for topic in topics:
        if topic in REFERENCE_TOPICS and topic not in requested:
            requested.append(topic)
    selected: list[ReferenceExample] = []
    for topic in requested:
        for example in REFERENCE_EXAMPLES:
            if topic in example.topics and example not in selected:
                selected.append(example)
                break
        if len(selected) == limit:
            break
    if len(selected) < limit:
        for example in REFERENCE_EXAMPLES:
            if example in selected or not any(topic in example.topics for topic in requested):
                continue
            selected.append(example)
            if len(selected) == limit:
                break
    return tuple(selected)


__all__ = [
    "REFERENCE_EXAMPLES",
    "REFERENCE_TOPICS",
    "ReferenceExample",
    "ReferenceTopic",
    "SOURCE_COMMIT",
    "SOURCE_REPOSITORY",
    "select_reference_examples",
]
