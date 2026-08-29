"""Materialize paired control/treatment specs for the reference-corpus study."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from video_pipeline.spec import SceneSpec


class StudySource(BaseModel):
    """Immutable upstream scene targeted by one paired study case."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    scene_name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    source_repo: str = Field(pattern=r"^https://github\.com/3b1b/videos$")
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_path: str = Field(pattern=r"^[A-Za-z0-9_./-]+\.py$")
    source_scene: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")


class ReferenceStudy(BaseModel):
    """At least ten fixed scenes tested with and without reference examples."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: str = Field(pattern=r"^1\.0$")
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    cases: list[SceneSpec] = Field(min_length=10)
    sources: tuple[StudySource, ...] = ()

    @model_validator(mode="after")
    def source_names_match_cases(self) -> ReferenceStudy:
        """Require a complete one-to-one mapping when provenance is supplied."""

        if not self.sources:
            return self
        case_names = [case.scene_name for case in self.cases]
        source_names = [source.scene_name for source in self.sources]
        if len(source_names) != len(set(source_names)):
            raise ValueError("study source scene_name values must be unique")
        if set(source_names) != set(case_names):
            raise ValueError("study sources must map every case scene_name exactly once")
        return self


@dataclass(frozen=True, slots=True)
class PreparedStudy:
    """Paths and sample counts for one materialized paired study."""

    root: Path
    control_specs: list[Path]
    treatment_specs: list[Path]


def prepare_reference_study(
    manifest_path: str | Path,
    output_root: str | Path,
) -> PreparedStudy:
    """Write paired specs whose only condition delta is few-shot count."""

    source = Path(manifest_path)
    study = ReferenceStudy.model_validate_json(source.read_text(encoding="utf-8"))
    root = Path(output_root).resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"study output root must be empty: {root}")
    control_root = root / "control"
    treatment_root = root / "treatment"
    control_root.mkdir(parents=True, exist_ok=True)
    treatment_root.mkdir(parents=True, exist_ok=True)

    controls: list[Path] = []
    treatments: list[Path] = []
    for index, case in enumerate(study.cases, start=1):
        name = f"{index:02d}-{case.scene_name}.json"
        control_path = control_root / name
        treatment_path = treatment_root / name
        common = case.model_dump(mode="json")
        control = {**common, "reference_examples": 0}
        treatment = {**common, "reference_examples": 2}
        _write_json(control_path, control)
        _write_json(treatment_path, treatment)
        controls.append(control_path)
        treatments.append(treatment_path)

    summary = {
        "name": study.name,
        "schema_version": study.schema_version,
        "samples_per_condition": len(study.cases),
        "conditions": {"control": 0, "treatment": 2},
        "sources": [source.model_dump(mode="json") for source in study.sources],
        "control_specs": [str(path) for path in controls],
        "treatment_specs": [str(path) for path in treatments],
    }
    _write_json(root / "study.json", summary)
    return PreparedStudy(root=root, control_specs=controls, treatment_specs=treatments)


def _write_json(path: Path, document: object) -> None:
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


__all__ = [
    "PreparedStudy",
    "ReferenceStudy",
    "StudySource",
    "prepare_reference_study",
]
