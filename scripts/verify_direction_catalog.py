"""Verify packaged golden evidence, never infer artistic approval from hashes."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "docs/direction/golden-v1/catalog.json"
APPROVED_SHA256 = "7d52455e20cecb8c958bafceceb250558640e85f581b804d94bc524b8c409f33"


def verify(catalog_path: Path, *, probe: bool = False) -> list[str]:
    """Check source references, intervals and actual packaged bytes offline."""
    errors: list[str] = []
    data = json.loads(catalog_path.read_text())
    if data["schema"] != "visual.direction.golden/1":
        errors.append("unsupported schema")
    sources = {s["id"]: s for s in data["source_inventory"]}
    if len(sources) != len(data["source_inventory"]):
        errors.append("duplicate source id")
    approved = sources[data["approved_source_id"]]
    if approved["sha256"] != APPROVED_SHA256:
        errors.append("approved reference changed")
    seen: set[str] = set()
    referenced: set[str] = set()
    for case in data["cases"]:
        cid = case["id"]
        if cid in seen:
            errors.append(f"{cid}: duplicate case")
        seen.add(cid)
        if case["kind"] not in {"positive-control", "regression-pair"}:
            errors.append(f"{cid}: invalid kind")
        if (case["kind"] == "positive-control") != (
            case["category"] == "positive-control"
        ):
            errors.append(f"{cid}: control mislabeled")
        if not case["observation"] or not case["acceptance"]:
            errors.append(f"{cid}: missing review criteria")
        referenced.update([case["evidence"], case["contact_sheet"]])
        for side in ("before", "after"):
            clip = case["clips"][side]
            referenced.add(clip["path"])
            source = sources[clip["source_id"]]
            if clip["source_sha256"] != source["sha256"]:
                errors.append(f"{cid}/{side}: source hash mismatch")
            if side == "after" and source["id"] != approved["id"]:
                errors.append(f"{cid}: after is not approved master")
            start, end = clip["start_frame"], clip["end_frame_exclusive"]
            if not (
                isinstance(start, int)
                and isinstance(end, int)
                and 0 <= start < end <= source["frames"]
                and end - start == clip["expected_frames"]
                and source["fps"] == clip["fps"] == "30/1"
            ):
                errors.append(f"{cid}/{side}: invalid frame interval")
            if not all(
                start <= round(t * 30) < end
                for t in case["review"]["sample_seconds"]
            ):
                errors.append(f"{cid}/{side}: sample outside excerpt")
        if any(
            case["clips"]["before"][key] != case["clips"]["after"][key]
            for key in ("start_frame", "end_frame_exclusive", "fps")
        ):
            errors.append(f"{cid}: timeline windows differ")
    if referenced != set(data["assets"]):
        errors.append("asset inventory differs from referenced evidence")
    for name, expected in data["assets"].items():
        path = (catalog_path.parent / name).resolve()
        if not path.is_relative_to(catalog_path.parent.resolve()):
            errors.append(f"{name}: unsafe path")
            continue
        if not path.is_file():
            errors.append(f"{name}: missing asset (run git lfs pull)")
            continue
        with path.open("rb") as stream:
            sha = hashlib.file_digest(stream, "sha256").hexdigest()
        if sha != expected["sha256"] or path.stat().st_size != expected["bytes"]:
            errors.append(f"{name}: asset hash/size mismatch (check LFS)")
            continue
        if probe and path.suffix == ".mp4":
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
                capture_output=True, text=True, check=True,
            )
            streams = json.loads(result.stdout)["streams"]
            video = next(s for s in streams if s["codec_type"] == "video")
            clip = next(
                c for case in data["cases"] for c in case["clips"].values()
                if c["path"] == name
            )
            if (
                int(video["nb_frames"]) != clip["expected_frames"]
                or video["r_frame_rate"] != clip["fps"]
                or (video["width"], video["height"]) != (1920, 1080)
                or not any(s["codec_type"] == "audio" for s in streams)
            ):
                errors.append(f"{name}: unexpected media geometry, frames or audio")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalog", nargs="?", type=Path, default=DEFAULT)
    parser.add_argument("--probe", action="store_true", help="also check media with ffprobe")
    args = parser.parse_args()
    try:
        errors = verify(args.catalog, probe=args.probe)
    except (
        OSError, ValueError, KeyError, TypeError, StopIteration, subprocess.SubprocessError
    ) as exc:
        print(f"Invalid catalog: {exc}")
        return 1
    for error in errors:
        print(error)
    if not errors:
        print("Golden catalog: integrity OK; artistic quality is not inferred.")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
