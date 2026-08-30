"""Capability registry evidence and prompt selection."""

from __future__ import annotations

import pytest

from video_pipeline.capabilities import (
    CapabilityRegistry,
    VisualCapability,
    default_capability_registry,
)


def test_default_registry_exposes_only_evidenced_initial_capabilities() -> None:
    registry = default_capability_registry()

    selected = registry.require(["typography", "equations"])

    assert [item.id for item in selected] == ["typography", "equations"]
    assert all(item.supported and item.golden_projects for item in selected)


def test_registry_rejects_unproven_capability_instead_of_claiming_support() -> None:
    registry = CapabilityRegistry(
        [
            VisualCapability(
                id="camera_3d",
                description="Complex 3D camera motion.",
                limitations=["No deterministic runtime evidence yet."],
                supported=False,
            )
        ]
    )

    with pytest.raises(ValueError, match="not supported"):
        registry.require(["camera_3d"])


def test_default_registry_keeps_unproven_asset_and_graph_capabilities_explicitly_unavailable() -> (
    None
):
    registry = default_capability_registry()

    with pytest.raises(ValueError, match="not supported"):
        registry.require(["coordinate_systems", "function_graphs", "svg_assets", "raster_images"])
