from __future__ import annotations

import hashlib

import pytest
from manim import Rectangle, VGroup

from video_pipeline.visual_assets import asset_manifest, edge_between, lucide_icon


def test_manifest_exposes_pinned_lucide_assets_with_matching_hashes() -> None:
    manifest = asset_manifest()

    assert manifest["library"] == "lucide"
    assert manifest["source_revision"] == "0.468.0"
    assert manifest["license"] == "ISC"
    assert manifest["normalization"]["view_box"] == "0 0 24 24"

    assets = manifest["assets"]
    assert "thumbs-up" in assets
    assert "cpu" in assets
    assert "file-text" in assets
    for record in assets.values():
        digest = hashlib.sha256(record["path"].read_bytes()).hexdigest()
        assert digest == record["sha256"]


def test_lucide_icon_returns_colored_normalized_group() -> None:
    icon = lucide_icon("thumbs-up", color="#4CC9F0", stroke_width=3.0)

    assert isinstance(icon, VGroup)
    assert icon.width > 0
    assert icon.height > 0
    assert icon.get_color().to_hex() == "#4CC9F0"
    assert all(
        abs(part.get_stroke_width() - 3.0) < 1e-6
        for part in icon.family_members_with_points()
    )


def test_lucide_icon_rejects_unknown_or_path_like_names() -> None:
    with pytest.raises(ValueError, match="Unknown visual asset"):
        lucide_icon("brain")
    with pytest.raises(ValueError, match="Unknown visual asset"):
        lucide_icon("../ui/thumbs-up")


def test_edge_between_connects_group_boundaries_without_crossing_nodes() -> None:
    source = Rectangle(width=2, height=1).move_to([-2, 0, 0])
    target = Rectangle(width=2, height=1).move_to([2, 0, 0])

    edge = edge_between(source, target, color="#FFD166", buff=0.1)

    assert edge.get_start()[0] > source.get_right()[0] - 1e-6
    assert edge.get_end()[0] < target.get_left()[0] + 1e-6
    assert edge.get_start()[0] < edge.get_end()[0]
