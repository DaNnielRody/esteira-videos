"""Corruption of curated evidence must not silently pass as a golden reference."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "catalog_check", ROOT / "scripts/verify_direction_catalog.py"
)
assert spec and spec.loader
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def test_packaged_catalog() -> None:
    assert checker.verify(checker.DEFAULT) == []


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("source", "source hash mismatch"),
        ("interval", "invalid frame interval"),
        ("control", "control mislabeled"),
        ("duplicate", "duplicate case"),
        ("corruption", "asset hash/size mismatch"),
    ],
)
def test_rejects_corrupt_catalog(tmp_path: Path, mutation: str, message: str) -> None:
    data = json.loads(checker.DEFAULT.read_text())
    # Valid tiny assets isolate manifest failures without copying video files.
    for name, asset in data["assets"].items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
        asset.update(sha256=hashlib.sha256(b"fixture").hexdigest(), bytes=7)
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(data))
    assert checker.verify(path) == []
    if mutation == "source":
        data["cases"][0]["clips"]["before"]["source_sha256"] = "0" * 64
    elif mutation == "interval":
        data["cases"][0]["clips"]["before"]["end_frame_exclusive"] = 999999
    elif mutation == "control":
        data["cases"][-1]["kind"] = "regression-pair"
    elif mutation == "duplicate":
        data["cases"].append(data["cases"][0])
    elif mutation == "corruption":
        (tmp_path / next(iter(data["assets"]))).write_bytes(b"corrupt")
    path.write_text(json.dumps(data))
    assert any(message in e for e in checker.verify(path))
