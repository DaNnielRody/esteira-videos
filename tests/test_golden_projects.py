"""Golden manifest contract for the canonical audiovisual profile."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_pipeline.golden import read_golden_project, validate_golden_project


def _manifest_project(tmp_path: Path, *, profile: object = "audiovisual") -> Path:
    project = tmp_path / "2026_canonical"
    golden = project / "golden"
    golden.mkdir(parents=True)
    (project / "project.json").write_text(
        json.dumps(
            {
                "id": project.name,
                "status": "accepted",
                "accepted_run": "run-001",
                "title": "Canonical audiovisual fixture",
                "capabilities": ["basic_geometry"],
            }
        ),
        encoding="utf-8",
    )
    manifest: dict[str, object] = {
        "schema_version": "golden.manifest/1",
        "version": 1,
        "status": "accepted",
        "project_id": project.name,
        "title": "Canonical audiovisual fixture",
        "run_id": "run-001",
        "capabilities": ["basic_geometry"],
    }
    if profile is not None:
        manifest["profile"] = profile
    (golden / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return project


def test_read_accepts_the_canonical_audiovisual_profile(tmp_path: Path) -> None:
    project = _manifest_project(tmp_path)

    loaded = read_golden_project(project)

    assert loaded.project_id == project.name
    assert loaded.capabilities == ("basic_geometry",)


@pytest.mark.parametrize("profile", ["visual", None, "hybrid"])
def test_visual_missing_and_unknown_profiles_are_rejected(
    tmp_path: Path,
    profile: object,
) -> None:
    project = _manifest_project(tmp_path, profile=profile)

    result = validate_golden_project(project)

    assert result.valid is False
    assert result.reasons == ["golden manifest profile must be audiovisual"]
    with pytest.raises(ValueError, match="profile must be audiovisual"):
        read_golden_project(project)
