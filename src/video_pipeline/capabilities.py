"""Small, evidence-backed registry of visual capabilities."""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field, field_validator


class VisualCapability(BaseModel):
    """One capability a plan may request from the local coder."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    description: str = Field(min_length=1)
    helpers: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    expectations_supported: list[str] = Field(default_factory=list)
    critics_applicable: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    golden_projects: list[str] = Field(default_factory=list)
    evidence_tests: list[str] = Field(default_factory=list)
    supported: bool = True

    @field_validator(
        "helpers",
        "examples",
        "expectations_supported",
        "critics_applicable",
        "limitations",
        "golden_projects",
        "evidence_tests",
    )
    @classmethod
    def _entries_are_unique_and_named(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("capability entries must be unique")
        if any(not item.strip() for item in value):
            raise ValueError("capability entries must not be blank")
        return value

    @field_validator("supported")
    @classmethod
    def _supported_requires_evidence(cls, value: bool) -> bool:
        return value

    def model_post_init(self, __context: object) -> None:
        if self.supported and (not self.golden_projects or not self.evidence_tests):
            raise ValueError("a supported capability requires golden_projects and evidence_tests")


class CapabilityRegistry:
    """Deterministic registry with strict selection for one scene."""

    def __init__(self, capabilities: Iterable[VisualCapability] = ()) -> None:
        values = list(capabilities)
        ids = [item.id for item in values]
        if len(ids) != len(set(ids)):
            raise ValueError("capability IDs must be unique")
        self._capabilities = {item.id: item for item in values}

    @property
    def capabilities(self) -> tuple[VisualCapability, ...]:
        """Return capabilities in registration order."""

        return tuple(self._capabilities.values())

    def get(self, capability_id: str) -> VisualCapability:
        """Return one capability or a stable unknown-capability error."""

        try:
            return self._capabilities[capability_id]
        except KeyError as exc:
            raise ValueError(f"unknown visual capability: {capability_id}") from exc

    def require(self, capability_ids: Iterable[str]) -> tuple[VisualCapability, ...]:
        """Resolve only supported capabilities in the plan's authored order."""

        selected: list[VisualCapability] = []
        for capability_id in capability_ids:
            capability = self.get(capability_id)
            if not capability.supported:
                raise ValueError(f"visual capability {capability_id!r} is not supported")
            selected.append(capability)
        return tuple(selected)

    def prompt_context(self, capability_ids: Iterable[str]) -> list[dict[str, object]]:
        """Produce bounded context for the prompt, never the whole registry."""

        return [
            {
                "id": item.id,
                "description": item.description,
                "helpers": item.helpers,
                "examples": item.examples,
                "expectations_supported": item.expectations_supported,
                "critics_applicable": item.critics_applicable,
                "limitations": item.limitations,
                "golden_projects": item.golden_projects,
            }
            for item in self.require(capability_ids)
        ]


def _supported(
    capability_id: str,
    description: str,
    *,
    helpers: list[str],
    expectations: list[str],
    critics: list[str],
    examples: list[str],
) -> VisualCapability:
    return VisualCapability(
        id=capability_id,
        description=description,
        helpers=helpers,
        expectations_supported=expectations,
        critics_applicable=critics,
        examples=examples,
        limitations=["Validated for 2D Cairo scenes at the production resolution."],
        golden_projects=["2026_visual_foundation"],
        evidence_tests=["tests/test_capabilities.py", "tests/test_visual_critics.py"],
    )


def default_capability_registry() -> CapabilityRegistry:
    """Build the production registry with initial proven 2D capabilities."""

    return CapabilityRegistry(
        [
            _supported(
                "typography",
                "Theme-controlled Text, Tex, and MathTex layout.",
                helpers=["VisualScene.register_visual", "theme.font_sizes"],
                expectations=["text", "latex"],
                critics=["safe_area", "contrast", "legibility", "plan_coherence"],
                examples=["projects/2026_visual_foundation/scenes/02_equation"],
            ),
            _supported(
                "equations",
                "Fixed MathTex expressions with deterministic layout checks.",
                helpers=["MathTex", "LatexExpectation"],
                expectations=["latex"],
                critics=["safe_area", "contrast", "legibility"],
                examples=["projects/2026_visual_foundation/scenes/02_equation"],
            ),
            _supported(
                "basic_geometry",
                "Circles, squares, arrows, and bounded transformations.",
                helpers=["Circle", "Square", "Arrow", "Transform"],
                expectations=["beats", "shape", "motion"],
                critics=["safe_area", "overlap", "rhythm", "plan_coherence"],
                examples=["projects/2026_visual_foundation/scenes/01_geometry"],
            ),
            VisualCapability(
                id="coordinate_systems",
                description="Theme-safe axes and coordinate annotations.",
                helpers=["Axes", "NumberPlane"],
                expectations_supported=["regions", "beats"],
                critics_applicable=["safe_area", "overlap", "plan_coherence"],
                limitations=["Not yet proven by an accepted coordinate-system golden scene."],
                supported=False,
            ),
            VisualCapability(
                id="function_graphs",
                description="Deterministic 2D graphs over explicit x ranges.",
                helpers=["Axes.plot"],
                expectations_supported=["regions", "beats", "motion"],
                critics_applicable=["safe_area", "contrast", "rhythm"],
                limitations=["Not yet proven by an accepted function-graph golden scene."],
                supported=False,
            ),
            VisualCapability(
                id="svg_assets",
                description="Local SVG assets with explicit semantic registration.",
                helpers=["SVGMobject", "VisualScene.register_visual"],
                expectations_supported=["regions", "colors"],
                critics_applicable=["safe_area", "contrast", "overlap"],
                limitations=["Not yet proven by an accepted SVG golden scene."],
                supported=False,
            ),
            VisualCapability(
                id="raster_images",
                description="Local raster images bounded by a planned region.",
                helpers=["ImageMobject", "VisualScene.register_visual"],
                expectations_supported=["regions"],
                critics_applicable=["safe_area", "contrast", "overlap"],
                limitations=["Not yet proven by an accepted raster-image golden scene."],
                supported=False,
            ),
            VisualCapability(
                id="three_d",
                description="3D mobjects and camera orientation.",
                limitations=["Not yet proven by the observable runtime and golden set."],
                supported=False,
            ),
            VisualCapability(
                id="complex_camera",
                description="Complex camera choreography across a scene.",
                limitations=["Only fixed 2D camera facts are currently recorded."],
                supported=False,
            ),
        ]
    )


DEFAULT_CAPABILITY_REGISTRY = default_capability_registry()


__all__ = [
    "CapabilityRegistry",
    "DEFAULT_CAPABILITY_REGISTRY",
    "VisualCapability",
    "default_capability_registry",
]
