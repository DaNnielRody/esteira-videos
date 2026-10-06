"""Deterministic visual assets shared by authored Manim scenes.

The module deliberately exposes only a small, vendored Lucide subset.  It
does not download assets or register objects with a scene.  Abstract concepts
remain authored from Manim primitives; these helpers cover concrete UI,
hardware, and document symbols when a universal outline improves recognition.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TypedDict

import numpy as np
from manim import Line, Mobject, SVGMobject, VGroup

_ICON_ROOT = Path(__file__).resolve().parents[2] / "assets" / "llm_fundation" / "icons"
_MANIFEST_PATH = _ICON_ROOT / "manifest.json"


class _ManifestAsset(TypedDict):
    category: str
    path: Path
    sha256: str


class _Manifest(TypedDict):
    schema_version: str
    library: str
    source_url: str
    source_revision: str
    license: str
    license_path: Path
    normalization: dict[str, str]
    assets: dict[str, _ManifestAsset]


def asset_manifest() -> _Manifest:
    """Load and verify the vendored asset manifest.

    Hash verification happens at load time so a changed asset cannot silently
    enter a render.  Returned paths are absolute and ready for Manim.
    """

    try:
        raw_value: object = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Unable to load visual asset manifest: {_MANIFEST_PATH}") from error
    if not isinstance(raw_value, dict):
        raise RuntimeError("Visual asset manifest must be a JSON object")

    required_strings = ("schema_version", "library", "source_url", "source_revision", "license")
    for key in required_strings:
        if not isinstance(raw_value.get(key), str):
            raise RuntimeError(f"Visual asset manifest field {key!r} must be a string")
    normalization_value = raw_value.get("normalization")
    if not isinstance(normalization_value, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in normalization_value.items()
    ):
        raise RuntimeError("Visual asset manifest normalization must be a string mapping")
    assets_value = raw_value.get("assets")
    if not isinstance(assets_value, dict):
        raise RuntimeError("Visual asset manifest assets must be an object")

    assets: dict[str, _ManifestAsset] = {}
    for name, record_value in assets_value.items():
        if not isinstance(name, str) or not isinstance(record_value, dict):
            raise RuntimeError("Visual asset records must be named JSON objects")
        category = record_value.get("category")
        relative_path = record_value.get("path")
        expected_hash = record_value.get("sha256")
        if not all(isinstance(value, str) for value in (category, relative_path, expected_hash)):
            raise RuntimeError(f"Visual asset record {name!r} is incomplete")
        path = _ICON_ROOT / relative_path
        if path.parent != _ICON_ROOT / category or not path.is_file():
            raise RuntimeError(f"Visual asset record {name!r} points outside the asset set")
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            raise RuntimeError(f"Visual asset hash mismatch for {name!r}")
        assets[name] = {"category": category, "path": path, "sha256": expected_hash}

    license_path_value = raw_value.get("license_path")
    if not isinstance(license_path_value, str):
        raise RuntimeError("Visual asset manifest license_path must be a string")
    license_path = _ICON_ROOT / license_path_value
    if not license_path.is_file():
        raise RuntimeError("Visual asset license file is missing")

    return {
        "schema_version": raw_value["schema_version"],
        "library": raw_value["library"],
        "source_url": raw_value["source_url"],
        "source_revision": raw_value["source_revision"],
        "license": raw_value["license"],
        "license_path": license_path,
        "normalization": normalization_value,
        "assets": assets,
    }


def lucide_icon(
    name: str,
    *,
    color: str = "#F8FAFC",
    stroke_width: float = 2.0,
    fill_opacity: float = 0.0,
) -> VGroup:
    """Return one normalized, recolorable icon group from the pinned subset."""

    manifest = asset_manifest()
    record = manifest["assets"].get(name)
    if record is None:
        raise ValueError(f"Unknown visual asset: {name}")
    icon = SVGMobject(str(record["path"]), should_center=True)
    group = VGroup(icon)
    group.set_color(color)
    group.set_stroke(color=color, width=stroke_width)
    group.set_fill(color=color, opacity=fill_opacity)
    return group


def svg_asset(
    name: str,
    *,
    color: str = "#F8FAFC",
    stroke_width: float = 2.0,
    fill_opacity: float = 0.0,
) -> VGroup:
    """Named alias for semantic scene code that is not Lucide-specific."""

    return lucide_icon(
        name,
        color=color,
        stroke_width=stroke_width,
        fill_opacity=fill_opacity,
    )


def edge_between(
    source: Mobject,
    target: Mobject,
    *,
    color: str,
    buff: float = 0.12,
    stroke_width: float = 2.0,
) -> Line:
    """Connect two objects from their boundaries, leaving labels unobscured."""

    if buff < 0:
        raise ValueError("edge buffer must be non-negative")
    delta = target.get_center() - source.get_center()
    distance = float(np.linalg.norm(delta))
    if distance <= 1e-9:
        raise ValueError("edge endpoints must have distinct centers")
    direction = delta / distance
    start = source.get_boundary_point(direction) + direction * buff
    end = target.get_boundary_point(-direction) - direction * buff
    return Line(start, end, color=color, stroke_width=stroke_width)


__all__ = ["asset_manifest", "edge_between", "lucide_icon", "svg_asset"]
