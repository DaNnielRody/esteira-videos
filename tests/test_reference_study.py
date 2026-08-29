"""Contract tests for the versioned reference-corpus study manifest."""

from __future__ import annotations

import json
from pathlib import Path

from video_pipeline.study import ReferenceStudy, prepare_reference_study

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = PROJECT_ROOT / "examples" / "reference-study.json"
SOURCE_COMMIT = "674b966fbb6cf0307590d27744d186165e8b6a76"


def test_production_study_maps_every_case_to_an_immutable_source(tmp_path: Path) -> None:
    study = ReferenceStudy.model_validate_json(MANIFEST.read_text(encoding="utf-8"))

    assert len(study.cases) == 10
    assert len(study.sources) == len(study.cases)
    assert {source.scene_name for source in study.sources} == {
        case.scene_name for case in study.cases
    }
    assert {source.source_commit for source in study.sources} == {SOURCE_COMMIT}
    assert all(source.source_path.endswith(".py") for source in study.sources)
    assert all(source.source_scene for source in study.sources)

    prepared = prepare_reference_study(MANIFEST, tmp_path / "study")
    summary = json.loads((prepared.root / "study.json").read_text(encoding="utf-8"))
    assert summary["sources"] == [source.model_dump(mode="json") for source in study.sources]
