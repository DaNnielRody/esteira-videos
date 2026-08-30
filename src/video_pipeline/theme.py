"""The immutable visual identity contract used by every authored scene.

The theme is deliberately data only.  Scene plans refer to semantic colour
roles and regions, while the renderer and critics resolve those roles through
this contract.  Keeping the values in one validated object prevents generated
scenes from silently drifting to a different palette or geometry.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ColorRole = Literal[
    "primary",
    "secondary",
    "accent",
    "success",
    "warning",
    "text",
    "muted",
    "background",
]
ThemeRegion = Literal["left", "center", "right", "top", "middle", "bottom"]

_COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")
_COLOR_ROLES = frozenset(
    {"primary", "secondary", "accent", "success", "warning", "text", "muted", "background"}
)
_FONT_KEYS = frozenset({"title", "body", "label", "formula"})
_FONT_SIZE_KEYS = frozenset({"title", "body", "label", "formula"})
_STROKE_KEYS = frozenset({"thin", "normal", "emphasis"})
_ANIMATION_KEYS = frozenset({"create", "transform", "emphasis", "fade"})

_PRODUCTION_PALETTE: dict[str, str] = {
    "primary": "#4CC9F0",
    "secondary": "#4361EE",
    "accent": "#F72585",
    "success": "#80ED99",
    "warning": "#FFD166",
    "text": "#F8FAFC",
    "muted": "#94A3B8",
    "background": "#0B1020",
}


class SafeArea(BaseModel):
    """Normalised frame bounds in left, top, right, bottom order."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    left: float = Field(default=0.08, ge=0.0, le=1.0)
    right: float = Field(default=0.92, ge=0.0, le=1.0)
    top: float = Field(default=0.08, ge=0.0, le=1.0)
    bottom: float = Field(default=0.92, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _bounds_are_ordered(self) -> SafeArea:
        if self.left >= self.right or self.top >= self.bottom:
            raise ValueError("safe area bounds must be ordered")
        return self


class TransitionPreset(BaseModel):
    """A named, measurable transition policy."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    enter: Literal["fade", "slide", "none", "transform"] = "fade"
    exit: Literal["fade", "slide", "none", "transform"] = "fade"
    duration_seconds: float = Field(default=0.5, gt=0.0, le=30.0)


class SemanticRegion(BaseModel):
    """A normalised semantic region available to a scene plan."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    left: float = Field(ge=0.0, le=1.0)
    top: float = Field(ge=0.0, le=1.0)
    right: float = Field(ge=0.0, le=1.0)
    bottom: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _bounds_are_ordered(self) -> SemanticRegion:
        if self.left >= self.right or self.top >= self.bottom:
            raise ValueError("semantic region bounds must be ordered")
        return self


def _default_regions() -> dict[str, SemanticRegion]:
    return {
        "left": SemanticRegion(left=0.08, top=0.08, right=0.38, bottom=0.92),
        "center": SemanticRegion(left=0.30, top=0.08, right=0.70, bottom=0.92),
        "right": SemanticRegion(left=0.62, top=0.08, right=0.92, bottom=0.92),
        "top": SemanticRegion(left=0.08, top=0.08, right=0.92, bottom=0.38),
        "middle": SemanticRegion(left=0.08, top=0.30, right=0.92, bottom=0.70),
        "bottom": SemanticRegion(left=0.08, top=0.62, right=0.92, bottom=0.92),
    }


class VideoTheme(BaseModel):
    """Strict visual identity shared by plans, generated code, and critics."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: str = Field(default="production", min_length=1, max_length=64)
    palette: dict[str, str] = Field(default_factory=lambda: dict(_PRODUCTION_PALETTE))
    font_families: dict[str, str] = Field(
        default_factory=lambda: {
            "title": "DejaVu Sans",
            "body": "DejaVu Sans",
            "label": "DejaVu Sans",
            "formula": "Computer Modern",
        }
    )
    font_sizes: dict[str, int] = Field(
        default_factory=lambda: {"title": 48, "body": 32, "label": 26, "formula": 40}
    )
    stroke_widths: dict[str, float] = Field(
        default_factory=lambda: {"thin": 1.0, "normal": 2.0, "emphasis": 4.0}
    )
    safe_area: SafeArea = Field(default_factory=SafeArea)
    min_spacing: float = Field(default=0.18, gt=0.0, le=10.0)
    resolution: tuple[int, int] = (854, 480)
    fps: int = Field(default=15, ge=1, le=120)
    animation_durations: dict[str, float] = Field(
        default_factory=lambda: {
            "create": 0.8,
            "transform": 1.2,
            "emphasis": 0.6,
            "fade": 0.5,
        }
    )
    min_read_duration: float = Field(default=1.2, gt=0.0, le=120.0)
    transition_presets: dict[str, TransitionPreset] = Field(
        default_factory=lambda: {
            "default": TransitionPreset(),
            "hard_cut": TransitionPreset(enter="none", exit="none", duration_seconds=0.1),
        }
    )
    regions: dict[str, SemanticRegion] = Field(default_factory=_default_regions)

    @model_validator(mode="before")
    @classmethod
    def _normalise_defaults(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        # Pydantic intentionally exposes the pre-validation payload as an
        # untyped mapping.  The field validators below establish the strict
        # contract before this document is used by the pipeline.
        data = dict(value)  # type: ignore[misc]
        palette = dict(_PRODUCTION_PALETTE)
        supplied_palette = data.get("palette")  # type: ignore[misc]
        if supplied_palette is not None:  # type: ignore[misc]
            if not isinstance(supplied_palette, dict):  # type: ignore[misc]
                return value
            palette.update(supplied_palette)
            data["palette"] = palette  # type: ignore[misc]
        resolution = data.get("resolution")  # type: ignore[misc]
        if isinstance(resolution, list):  # type: ignore[misc]
            data["resolution"] = tuple(resolution)  # type: ignore[misc]
        return data  # type: ignore[misc]

    @model_validator(mode="after")
    def _validate_contract(self) -> VideoTheme:
        palette_keys = set(self.palette)
        unknown_roles = palette_keys - _COLOR_ROLES
        missing_roles = _COLOR_ROLES - palette_keys
        if unknown_roles:
            raise ValueError(f"unknown color roles: {', '.join(sorted(unknown_roles))}")
        if missing_roles:
            raise ValueError(f"missing color roles: {', '.join(sorted(missing_roles))}")
        for role, color in self.palette.items():
            if not isinstance(color, str) or _COLOR_PATTERN.fullmatch(color) is None:
                raise ValueError(f"color role {role!r} must be a six-digit hex color")

        self._validate_named_strings(self.font_families, _FONT_KEYS, "font families")
        self._validate_positive_ints(self.font_sizes, _FONT_SIZE_KEYS, "font sizes")
        if self.font_sizes["title"] <= self.font_sizes["body"]:
            raise ValueError("title font size must be larger than body font size")
        self._validate_positive_numbers(self.stroke_widths, _STROKE_KEYS, "stroke widths")
        self._validate_positive_numbers(
            self.animation_durations, _ANIMATION_KEYS, "animation durations"
        )
        width, height = self.resolution
        if width < 16 or height < 16 or width > 7680 or height > 4320:
            raise ValueError("resolution dimensions are impossible")
        if self.min_spacing >= min(
            self.safe_area.right - self.safe_area.left, self.safe_area.bottom - self.safe_area.top
        ):
            raise ValueError("minimum spacing does not fit inside the safe area")
        return self

    @staticmethod
    def _validate_named_strings(
        values: dict[str, str], required: frozenset[str], label: str
    ) -> None:
        unknown = set(values) - required
        missing = required - set(values)
        if unknown:
            raise ValueError(f"unknown {label}: {', '.join(sorted(unknown))}")
        if missing:
            raise ValueError(f"missing {label}: {', '.join(sorted(missing))}")
        if any(not value.strip() for value in values.values()):
            raise ValueError(f"{label} must not contain blank values")

    @staticmethod
    def _validate_positive_ints(
        values: dict[str, int], required: frozenset[str], label: str
    ) -> None:
        unknown = set(values) - required
        missing = required - set(values)
        if unknown:
            raise ValueError(f"unknown {label}: {', '.join(sorted(unknown))}")
        if missing:
            raise ValueError(f"missing {label}: {', '.join(sorted(missing))}")
        if any(value <= 0 or value > 512 for value in values.values()):
            raise ValueError(f"{label} must be positive and at most 512")

    @staticmethod
    def _validate_positive_numbers(
        values: dict[str, float], required: frozenset[str], label: str
    ) -> None:
        unknown = set(values) - required
        missing = required - set(values)
        if unknown:
            raise ValueError(f"unknown {label}: {', '.join(sorted(unknown))}")
        if missing:
            raise ValueError(f"missing {label}: {', '.join(sorted(missing))}")
        if any(value <= 0 for value in values.values()):
            raise ValueError(f"{label} must be positive")

    @classmethod
    def production(cls) -> VideoTheme:
        """Return the standard production identity."""

        return cls()

    @property
    def background_color(self) -> str:
        """Resolve the background role for renderers and critics."""

        return self.palette["background"]

    @property
    def text_color(self) -> str:
        """Resolve the text role without allowing scene-local hex values."""

        return self.palette["text"]

    @property
    def accent_color(self) -> str:
        """Resolve the accent role."""

        return self.palette["accent"]

    def color(self, role: ColorRole) -> str:
        """Resolve one semantic role to its validated colour."""

        return self.palette[role]

    def to_document(self) -> dict[str, object]:
        """Return a typed JSON-ready representation of the visual identity."""

        return {
            "id": self.id,
            "palette": dict(self.palette),
            "font_families": dict(self.font_families),
            "font_sizes": dict(self.font_sizes),
            "stroke_widths": dict(self.stroke_widths),
            "safe_area": {
                "left": self.safe_area.left,
                "right": self.safe_area.right,
                "top": self.safe_area.top,
                "bottom": self.safe_area.bottom,
            },
            "min_spacing": self.min_spacing,
            "resolution": list(self.resolution),
            "fps": self.fps,
            "animation_durations": dict(self.animation_durations),
            "min_read_duration": self.min_read_duration,
            "transition_presets": {
                name: {
                    "enter": preset.enter,
                    "exit": preset.exit,
                    "duration_seconds": preset.duration_seconds,
                }
                for name, preset in self.transition_presets.items()
            },
            "regions": {
                name: {
                    "left": region.left,
                    "top": region.top,
                    "right": region.right,
                    "bottom": region.bottom,
                }
                for name, region in self.regions.items()
            },
        }


DEFAULT_VIDEO_THEME = VideoTheme.production()


__all__ = [
    "ColorRole",
    "DEFAULT_VIDEO_THEME",
    "SafeArea",
    "SemanticRegion",
    "ThemeRegion",
    "TransitionPreset",
    "VideoTheme",
]
