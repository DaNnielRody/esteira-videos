"""Behavioral contract tests for strict Scene Spec loading.

These tests deliberately import only the public loader and model.  The
implementation is free to choose its validation details, but callers must see
an immutable, strictly validated spec at the boundary.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


try:
    from video_pipeline.spec import SceneSpec, load_scene_spec
except (ImportError, ModuleNotFoundError) as exc:  # pragma: no cover - RED shim
    _CONTRACT_IMPORT_ERROR = exc
    SceneSpec = object  # type: ignore[assignment,misc]
    load_scene_spec = None  # type: ignore[assignment]
else:
    _CONTRACT_IMPORT_ERROR = None


VALID_SPEC = {
    "schema_version": "1.0",
    "scene_name": "AcceptanceScene",
    "description": (
        "Mostre um círculo no centro. Depois transforme-o em um quadrado "
        "e mova-o para a direita."
    ),
}


def _write_spec(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "scene.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _require_contract() -> None:
    if _CONTRACT_IMPORT_ERROR is not None:
        pytest.fail(
            "SCENE_PROVIDER_CONTRACT_MISSING: scene-spec public seam unavailable"
        )


def test_load_scene_spec_returns_immutable_valid_scene(tmp_path: Path) -> None:
    """A valid JSON document loads with the declared public values."""

    _require_contract()
    spec = load_scene_spec(_write_spec(tmp_path, VALID_SPEC))

    assert isinstance(spec, SceneSpec)
    assert spec.schema_version == "1.0"
    assert spec.scene_name == "AcceptanceScene"
    assert spec.description == VALID_SPEC["description"]

    with pytest.raises((TypeError, AttributeError, ValueError)):
        spec.scene_name = "OtherScene"  # type: ignore[misc]


@pytest.mark.parametrize(
    "invalid_payload",
    [
        {
            "scene_name": "AcceptanceScene",
            "description": "A scene",
        },
        {
            "schema_version": "1.0",
            "scene_name": "AcceptanceScene",
            "description": "A scene",
            "unexpected": True,
        },
        {
            "schema_version": "not-a-version",
            "scene_name": "AcceptanceScene",
            "description": "A scene",
        },
    ],
)
def test_load_scene_spec_rejects_invalid_structure_before_use(
    tmp_path: Path, invalid_payload: dict[str, object]
) -> None:
    """Missing, extra, and malformed schema fields are rejected."""

    _require_contract()
    with pytest.raises(Exception):
        load_scene_spec(_write_spec(tmp_path, invalid_payload))


def test_load_scene_spec_rejects_malformed_json(tmp_path: Path) -> None:
    _require_contract()
    path = tmp_path / "scene.json"
    path.write_text('{"schema_version":', encoding="utf-8")

    with pytest.raises(Exception):
        load_scene_spec(path)


@pytest.mark.parametrize(
    "scene_name",
    ["acceptanceScene", "1AcceptanceScene", "Acceptance Scene", "Acceptance-Scene"],
)
def test_load_scene_spec_rejects_unsafe_scene_names(
    tmp_path: Path, scene_name: str
) -> None:
    _require_contract()
    payload = {**VALID_SPEC, "scene_name": scene_name}

    with pytest.raises(Exception):
        load_scene_spec(_write_spec(tmp_path, payload))


@pytest.mark.parametrize("description", ["", "   ", "\n\t"])
def test_load_scene_spec_rejects_blank_descriptions(
    tmp_path: Path, description: str
) -> None:
    _require_contract()
    payload = {**VALID_SPEC, "description": description}

    with pytest.raises(Exception):
        load_scene_spec(_write_spec(tmp_path, payload))


def test_scene_spec_audit_contract() -> None:
    """Inventory the declared scene-spec contract tests without product calls."""

    behavioral_tests = (
        "test_load_scene_spec_returns_immutable_valid_scene",
        "test_load_scene_spec_rejects_invalid_structure_before_use",
        "test_load_scene_spec_rejects_malformed_json",
        "test_load_scene_spec_rejects_unsafe_scene_names",
        "test_load_scene_spec_rejects_blank_descriptions",
    )

    assert all(callable(globals().get(name)) for name in behavioral_tests)
