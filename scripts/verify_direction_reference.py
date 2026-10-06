"""Read-only verification of the approved direction reference (run from any cwd)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "docs/direction/reference-v1.json").read_text())
    failures: list[str] = []
    if manifest.get("schema_version") != "visual.direction-reference/1":
        raise ValueError("unsupported direction reference schema")
    records = manifest["files"]
    if not records or len({r["path"] for r in records}) != len(records):
        raise ValueError("reference needs a nonempty, unique file inventory")
    master = next(r for r in records if r["path"] == manifest["master_path"])
    if master["sha256"] != manifest["master_sha256"]:
        raise ValueError("master hash differs from inventory")
    for record in records:
        path = (root / record["path"]).resolve()
        if not path.is_relative_to(root):
            raise ValueError("reference path escapes repository")
        try:
            with path.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            if digest != record["sha256"] or path.stat().st_size != record["bytes"]:
                failures.append(f"CHANGED: {record['path']}")
        except OSError:
            failures.append(f"MISSING/UNREADABLE: {record['path']}")
    if failures:
        print("\n".join(failures))
        return 1
    print(f"Verified {len(records)} files. Approved master: {manifest['master_sha256']}")
    print("Integrity only; this does not perform a new audiovisual review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
