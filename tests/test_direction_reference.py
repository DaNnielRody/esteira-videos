"""Integrity verification must reject changed or missing approved artifacts."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("state, expected_code", [("approved", 0), ("changed", 1), ("missing", 1)])
def test_reference_verifier_detects_artifact_changes(
    tmp_path: Path,
    state: str,
    expected_code: int,
) -> None:
    root = Path(__file__).resolve().parents[1]
    script = tmp_path / "scripts/verify_direction_reference.py"
    script.parent.mkdir()
    shutil.copyfile(root / "scripts/verify_direction_reference.py", script)
    manifest = tmp_path / "docs/direction/reference-v1.json"
    manifest.parent.mkdir(parents=True)
    approved = b"approved artifact bytes"
    digest = hashlib.sha256(approved).hexdigest()
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "visual.direction-reference/1",
                "master_path": "master.mp4",
                "master_sha256": digest,
                "files": [{"path": "master.mp4", "sha256": digest, "bytes": len(approved)}],
            }
        )
    )
    if state != "missing":
        (tmp_path / "master.mp4").write_bytes(approved if state == "approved" else b"changed")
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == expected_code, result.stderr
    assert ("Verified" in result.stdout) == (state == "approved")
