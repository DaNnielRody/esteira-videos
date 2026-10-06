from manim import VGroup, RoundedRectangle

class TokenBox(VGroup):
    """Measure final glyphs once, then own the text/rim lifecycle as one unit."""
    def __init__(self, text, color, horizontal_padding=0.16, vertical_padding=0.12):
        self.text = text
        self.rim = RoundedRectangle(
            width=text.width + 2 * horizontal_padding,
            height=text.height + 2 * vertical_padding,
            corner_radius=0.06, stroke_color=color, stroke_width=1.5,
            fill_opacity=0,
        ).move_to(text)
        super().__init__(self.rim, text)

    def assert_padding(self):
        assert self.rim.get_left()[0] < self.text.get_left()[0]
        assert self.rim.get_right()[0] > self.text.get_right()[0]
        assert self.rim.get_bottom()[1] < self.text.get_bottom()[1]
        assert self.rim.get_top()[1] > self.text.get_top()[1]
