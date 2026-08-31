"""Behavioral contract for selective scene regeneration."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from test_project_render import (
    FakeAudioProbe,
    FakeComposer,
    FakeFinalValidator,
    FakeManimRunner,
    FakeNormalizedValidator,
    FakeObserver,
    FakeProvider,
    FakeRawValidator,
    FakeTemporalNormalizer,
)

from video_pipeline.cli import main
from video_pipeline.golden import accept_project, validate_golden_project
from video_pipeline.project import inspect_project
from video_pipeline.scene_plan import ScenePlan
from video_pipeline.video import VideoPipeline


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _normalize_run_prefixes(value: object, run_root: Path) -> object:
    """Compare cloned evidence while treating each run root as one token."""

    prefix = str(run_root.resolve())
    if isinstance(value, dict):
        return {
            key: _normalize_run_prefixes(item, run_root)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_normalize_run_prefixes(item, run_root) for item in value]
    if isinstance(value, str):
        return value.replace(prefix, "<RUN>")
    return value


def _pipeline(
    project_json: Path,
    run_id: str,
) -> tuple[VideoPipeline, FakeProvider, FakeManimRunner]:
    provider = FakeProvider(project_json)
    runner = FakeManimRunner()
    normalized_validator = FakeNormalizedValidator()
    pipeline = VideoPipeline(
        provider=provider,
        runner=runner,
        validator=FakeRawValidator(),
        observer=FakeObserver(),
        temporal_normalizer=FakeTemporalNormalizer(normalized_validator),
        normalized_validator=normalized_validator,
        final_validator=FakeFinalValidator(),
        composer=FakeComposer(),
        id_factory=lambda: run_id,
    )
    return pipeline, provider, runner


def _ready_base_run(
    tmp_path: Path,
) -> tuple[Path, dict[str, object], Path]:
    script = tmp_path / "roteiro.md"
    script.write_text(
        "# Abertura\n"
        "@start: 0\n"
        "@end: 4\n"
        "@objective: Introduza vetores.\n"
        "Esta e a abertura exata.\n\n"
        "## Explicacao\n"
        "@start: 4\n"
        "@end: 10\n"
        "@objective: Explique a soma.\n"
        "Esta e a explicacao exata.\n",
        encoding="utf-8",
    )
    audio = tmp_path / "narracao.wav"
    audio_bytes = b"immutable narration bytes\x00"
    audio.write_bytes(audio_bytes)
    facts = {
        "path": "audio/narration.wav",
        "hash": hashlib.sha256(audio_bytes).hexdigest(),
        "container": "wav",
        "codec": "pcm_s16le",
        "stream": 0,
        "sample_rate": 48_000,
        "channels": 2,
        "duration": 10.0,
        "size": len(audio_bytes),
        "probe_result": {"format": {}, "streams": []},
    }
    project = tmp_path / "projects" / "2026_vetores"
    assert (
        main(
            [
                "init",
                str(project),
                "--title",
                "Vetores",
                "--script",
                str(script),
                "--audio",
                str(audio),
            ],
            audio_probe=FakeAudioProbe(facts),
        )
        == 0
    )

    project_json = project / "project.json"
    project_document = json.loads(project_json.read_text(encoding="utf-8"))
    for scene_ref in project_document["scenes"]:
        plan_path = project / scene_ref["plan_path"]
        plan = ScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
        plan_path.write_text(
            json.dumps(
                plan.model_copy(update={"capabilities": ["basic_geometry"]}).to_document(),
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    base_pipeline, _, _ = _pipeline(project_json, "run-001")
    assert base_pipeline.render(project_json, max_attempts=1).state == "ready"
    return project_json, project_document, project / "artifacts" / "run-001"


def test_regenerates_one_scene_from_ready_run_without_mutating_base(
    tmp_path: Path,
) -> None:
    project_json, project_document, base_run = _ready_base_run(tmp_path)
    project = project_json.parent
    base_run_document_path = base_run / "run.json"
    base_run_document = json.loads(
        base_run_document_path.read_text(encoding="utf-8")
    )
    sibling_id = project_document["scenes"][1]["id"]
    sibling_record = next(
        scene for scene in base_run_document["scenes"] if scene["id"] == sibling_id
    )
    sibling_record["attempt_history"][0]["diagnostics"]["stderr"] = (
        f"trace at {base_run.resolve()}/pipeline/explicacao/"
        "run-001-02/attempt-01/scene.py"
    )
    sibling_history = json.loads(json.dumps(sibling_record["attempt_history"]))
    sibling_record["error"] = "stale"
    base_run_document_path.write_text(
        json.dumps(base_run_document, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    base_snapshot = _snapshot(base_run)
    sibling_relative_path = project_document["scenes"][1]["path"]
    sibling_snapshot = _snapshot(base_run / sibling_relative_path)

    pipeline, provider, runner = _pipeline(project_json, "run-002")
    correction = "Use uma seta azul mais espessa"
    result = pipeline.render(
        project_json,
        max_attempts=1,
        scene="abertura",
        base_run_id="run-001",
        correction=correction,
    )

    assert result.state == "ready"
    new_run = project / "artifacts" / "run-002"
    run_document = json.loads((new_run / "run.json").read_text(encoding="utf-8"))
    assert run_document["state"] == "ready"
    assert run_document["base_run_id"] == "run-001"
    assert run_document["selected_scene_id"] == "abertura"
    assert run_document["correction"] == correction
    assert len(provider.requests) == 1
    assert len(runner.scene_paths) == 1
    assert provider.requests[0].scene_name == "AberturaScene"
    assert correction in provider.requests[0].description
    assert _snapshot(base_run) == base_snapshot
    assert _snapshot(new_run / sibling_relative_path) == sibling_snapshot
    new_sibling_record = next(
        scene for scene in run_document["scenes"] if scene["id"] == sibling_id
    )
    assert new_sibling_record["state"] == "ready"
    assert new_sibling_record["error"] is None
    cloned_history = new_sibling_record["attempt_history"]
    assert len(cloned_history) == len(sibling_history)
    assert [
        (entry["state"], entry["attempt"]) for entry in cloned_history
    ] == [
        (entry["state"], entry["attempt"]) for entry in sibling_history
    ]
    assert _normalize_run_prefixes(cloned_history, new_run) == (
        _normalize_run_prefixes(sibling_history, base_run)
    )
    assert str(base_run.resolve()) not in json.dumps(run_document, ensure_ascii=False)
    base_after = json.loads(base_run_document_path.read_text(encoding="utf-8"))
    base_sibling_after = next(
        scene for scene in base_after["scenes"] if scene["id"] == sibling_id
    )
    assert base_sibling_after["error"] == "stale"
    assert base_sibling_after["attempt_history"] == sibling_history
    assert _snapshot(base_run) == base_snapshot
    assert (new_run / "final.mp4").is_file()

    inspection = inspect_project(project_json)
    assert inspection["project"]["current_run"] == "run-002"
    accepted = accept_project(project_json, "run-002")
    assert accepted.status.value == "accepted"
    assert accepted.accepted_run == "run-002"
    golden_validation = validate_golden_project(project)
    assert golden_validation.valid, golden_validation.reasons
    assert golden_validation.reasons == []
    assert golden_validation.inference_calls == 0
    manifest = json.loads(
        (project / "golden" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["base_run_id"] == "run-001"
    assert manifest["selected_scene_id"] == "abertura"
    assert manifest["correction"] == correction
    manifest_scenes = {scene["id"]: scene for scene in manifest["scenes"]}
    for scene_id, expected_run_id in (
        ("abertura", "run-002"),
        ("explicacao", "run-001"),
    ):
        expected_run_path = f"artifacts/{expected_run_id}"
        expected_source_prefix = f"{expected_run_path}/pipeline/{scene_id}/"
        manifest_provenance = manifest_scenes[scene_id]["provenance"]
        provenance_path = project / manifest_scenes[scene_id]["provenance_path"]
        file_provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        for provenance in (manifest_provenance, file_provenance):
            assert provenance["run_id"] == expected_run_id
            assert provenance["run_path"] == expected_run_path
            assert provenance["source_path"].startswith(expected_source_prefix)
            assert provenance["source_path"].endswith("/scene.py")


def test_accepts_selective_regeneration_chain_with_ancestral_sibling_lineage(
    tmp_path: Path,
) -> None:
    project_json, project_document, _ = _ready_base_run(tmp_path)
    project = project_json.parent

    run_two_pipeline, _, _ = _pipeline(project_json, "run-002")
    assert (
        run_two_pipeline.render(
            project_json,
            max_attempts=1,
            scene="abertura",
            base_run_id="run-001",
            correction="Make the arrow thicker",
        ).state
        == "ready"
    )
    sibling_path = project / "artifacts" / "run-002" / project_document["scenes"][1]["path"]
    run_two_sibling_provenance = json.loads(
        (sibling_path / "code-provenance.json").read_text(encoding="utf-8")
    )
    assert run_two_sibling_provenance["run_id"] == "run-001"

    run_three_pipeline, _, _ = _pipeline(project_json, "run-003")
    assert (
        run_three_pipeline.render(
            project_json,
            max_attempts=1,
            scene="abertura",
            base_run_id="run-002",
            correction="Make the arrow even thicker",
        ).state
        == "ready"
    )
    run_three = project / "artifacts" / "run-003"
    run_three_document = json.loads(
        (run_three / "run.json").read_text(encoding="utf-8")
    )
    assert run_three_document["state"] == "ready"
    assert run_three_document["base_run_id"] == "run-002"
    run_three_sibling_provenance = json.loads(
        (
            run_three / project_document["scenes"][1]["path"] / "code-provenance.json"
        ).read_text(encoding="utf-8")
    )
    assert run_three_sibling_provenance == run_two_sibling_provenance

    accepted = accept_project(project_json, "run-003")
    assert accepted.status.value == "accepted"
    validation = validate_golden_project(project)
    assert validation.valid, validation.reasons
    manifest = json.loads(
        (project / "golden" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["base_run_id"] == "run-002"
    assert manifest["selected_scene_id"] == "abertura"
    manifest_scenes = {scene["id"]: scene for scene in manifest["scenes"]}
    assert manifest_scenes["abertura"]["provenance"]["run_id"] == "run-003"
    sibling_manifest_provenance = manifest_scenes["explicacao"]["provenance"]
    assert sibling_manifest_provenance["run_id"] == "run-001"
    assert sibling_manifest_provenance["run_path"] == "artifacts/run-001"
    assert sibling_manifest_provenance["source_path"].startswith(
        "artifacts/run-001/pipeline/explicacao/"
    )


def test_accept_rejects_selective_sibling_code_drift_and_rolls_back(
    tmp_path: Path,
) -> None:
    project_json, project_document, base_run = _ready_base_run(tmp_path)
    project = project_json.parent
    run_two_pipeline, _, _ = _pipeline(project_json, "run-002")
    assert (
        run_two_pipeline.render(
            project_json,
            max_attempts=1,
            scene="abertura",
            base_run_id="run-001",
            correction="Make the arrow thicker",
        ).state
        == "ready"
    )

    base_snapshot = _snapshot(base_run)
    run_path = project / "artifacts" / "run-002"
    run_json = run_path / "run.json"
    run_document = json.loads(run_json.read_text(encoding="utf-8"))
    sibling_scene = project_document["scenes"][1]
    sibling_code_path = run_path / sibling_scene["path"] / "scene.py"
    sibling_code_path.write_bytes(sibling_code_path.read_bytes() + b"\n# drift\n")
    sibling_record = next(
        scene for scene in run_document["scenes"] if scene["id"] == sibling_scene["id"]
    )
    sibling_record["code_sha256"] = hashlib.sha256(
        sibling_code_path.read_bytes()
    ).hexdigest()
    run_json.write_text(
        json.dumps(run_document, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    project_snapshot = project_json.read_bytes()
    run_snapshot = run_json.read_bytes()
    golden_root = project / "golden"
    golden_exists = golden_root.exists()
    golden_snapshot = _snapshot(golden_root) if golden_exists else {}
    permanent_paths = [
        project / scene_ref["path"] / filename
        for scene_ref in project_document["scenes"]
        for filename in ("scene.py", "code-provenance.json")
    ]
    permanent_snapshots = {
        path: path.read_bytes() if path.is_file() else None
        for path in permanent_paths
    }

    with pytest.raises(ValueError, match=r"lineage|provenance|base|scene"):
        accept_project(project_json, "run-002")

    assert project_json.read_bytes() == project_snapshot
    assert run_json.read_bytes() == run_snapshot
    assert _snapshot(base_run) == base_snapshot
    assert golden_root.exists() == golden_exists
    assert (golden_snapshot if golden_exists else {}) == (
        _snapshot(golden_root) if golden_root.exists() else {}
    )
    for path, snapshot in permanent_snapshots.items():
        assert path.is_file() == (snapshot is not None)
        if snapshot is not None:
            assert path.read_bytes() == snapshot


@pytest.mark.parametrize("case", ["sibling_run_evil", "selected_base_run"])
def test_accept_rejects_selective_lineage_tampering(
    tmp_path: Path,
    case: str,
) -> None:
    project_json, project_document, _ = _ready_base_run(tmp_path)
    project = project_json.parent
    pipeline, _, _ = _pipeline(project_json, "run-002")
    assert (
        pipeline.render(
            project_json,
            max_attempts=1,
            scene="abertura",
            base_run_id="run-001",
            correction="Use uma seta azul mais espessa",
        ).state
        == "ready"
    )

    run_path = project / "artifacts" / "run-002"
    run_json = run_path / "run.json"
    run_document = json.loads(run_json.read_text(encoding="utf-8"))
    scene_id = "explicacao" if case == "sibling_run_evil" else "abertura"
    scene_ref = next(
        scene for scene in project_document["scenes"] if scene["id"] == scene_id
    )
    provenance_path = run_path / scene_ref["path"] / "code-provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    lineage_run_id = "run-evil" if case == "sibling_run_evil" else "run-001"
    provenance.update(
        {
            "run_id": lineage_run_id,
            "run_path": f"artifacts/{lineage_run_id}",
            "source_path": (
                f"artifacts/{lineage_run_id}/pipeline/{scene_id}/"
                f"{lineage_run_id}-01/attempt-01/scene.py"
            ),
        }
    )
    provenance_path.write_text(
        json.dumps(provenance, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    scene_record = next(
        scene for scene in run_document["scenes"] if scene["id"] == scene_id
    )
    scene_record["provenance_sha256"] = hashlib.sha256(
        provenance_path.read_bytes()
    ).hexdigest()
    run_json.write_text(
        json.dumps(run_document, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    golden_root = project / "golden"

    def snapshot_tree(root: Path) -> dict[str, bytes]:
        if not root.exists():
            return {}
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }

    project_snapshot = project_json.read_bytes()
    run_snapshot = run_json.read_bytes()
    golden_exists = golden_root.exists()
    golden_snapshot = snapshot_tree(golden_root)
    permanent_paths = [
        project / scene_ref["path"] / filename
        for scene_ref in project_document["scenes"]
        for filename in ("scene.py", "code-provenance.json")
    ]
    permanent_snapshots = {
        path: path.read_bytes() if path.is_file() else None
        for path in permanent_paths
    }

    with pytest.raises(ValueError, match="provenance"):
        accept_project(project_json, "run-002")

    assert project_json.read_bytes() == project_snapshot
    assert run_json.read_bytes() == run_snapshot
    assert golden_root.exists() == golden_exists
    assert snapshot_tree(golden_root) == golden_snapshot
    for path, snapshot in permanent_snapshots.items():
        assert path.is_file() == (snapshot is not None)
        if snapshot is not None:
            assert path.read_bytes() == snapshot


@pytest.mark.parametrize("tamper", ["project_id", "sibling_normalized"])
def test_selective_rejects_tampered_base_before_provider(
    tmp_path: Path,
    tamper: str,
) -> None:
    project_json, project_document, base_run = _ready_base_run(tmp_path)
    base_run_document_path = base_run / "run.json"
    if tamper == "project_id":
        base_run_document = json.loads(
            base_run_document_path.read_text(encoding="utf-8")
        )
        base_run_document["project_id"] = "outro_projeto"
        base_run_document_path.write_text(
            json.dumps(base_run_document, ensure_ascii=False),
            encoding="utf-8",
        )
    else:
        sibling_relative_path = project_document["scenes"][1]["path"]
        (base_run / sibling_relative_path / "normalized.mp4").write_bytes(b"tampered")

    project_bytes = project_json.read_bytes()
    base_snapshot = _snapshot(base_run)
    pipeline, provider, runner = _pipeline(project_json, "run-002")

    with pytest.raises(ValueError, match="base run"):
        pipeline.render(
            project_json,
            max_attempts=1,
            scene="abertura",
            base_run_id="run-001",
            correction="Use uma seta azul mais espessa",
        )

    assert len(provider.requests) == 0
    assert len(runner.scene_paths) == 0
    assert not (base_run.parent / "run-002").exists()
    assert project_json.read_bytes() == project_bytes
    assert _snapshot(base_run) == base_snapshot


def test_selective_rejects_base_without_final_attestation_before_provider(
    tmp_path: Path,
) -> None:
    project_json, _, base_run = _ready_base_run(tmp_path)
    base_run_json = base_run / "run.json"
    base_document = json.loads(base_run_json.read_text(encoding="utf-8"))
    base_document.pop("final_sha256", None)
    base_document.pop("final_size_bytes", None)
    base_run_json.write_text(
        json.dumps(base_document, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    (base_run / "final.mp4").unlink()

    project_bytes = project_json.read_bytes()
    pipeline, provider, runner = _pipeline(project_json, "run-002")

    with pytest.raises(ValueError, match=r"final attestation|final MP4"):
        pipeline.render(
            project_json,
            max_attempts=1,
            scene="abertura",
            base_run_id="run-001",
            correction="Use uma seta azul mais espessa",
        )

    assert len(provider.requests) == 0
    assert len(runner.scene_paths) == 0
    assert not (base_run.parent / "run-002").exists()
    assert project_json.read_bytes() == project_bytes


@pytest.mark.parametrize("tamper", ["symlink", "unmanifested_extra", "fifo"])
def test_selective_rejects_unmanifested_base_scene_entries_before_provider(
    tmp_path: Path,
    tamper: str,
) -> None:
    project_json, project_document, base_run = _ready_base_run(tmp_path)
    sibling_scene_root = base_run / project_document["scenes"][1]["path"]
    if tamper == "symlink":
        sentinel = tmp_path / "external-sentinel.bin"
        sentinel.write_bytes(b"must never be read or copied")
        (sibling_scene_root / "leak.txt").symlink_to(sentinel)
        expected_error = "symlink"
    elif tamper == "fifo":
        os.mkfifo(sibling_scene_root / "special.fifo")
        expected_error = "non-regular"
    else:
        (sibling_scene_root / "extra.bin").write_bytes(b"unmanifested evidence")
        expected_error = "unmanifested"

    project_bytes = project_json.read_bytes()
    base_snapshot = _snapshot(base_run)
    pipeline, provider, runner = _pipeline(project_json, "run-002")

    with pytest.raises(ValueError, match=expected_error):
        pipeline.render(
            project_json,
            max_attempts=1,
            scene="abertura",
            base_run_id="run-001",
            correction="Use uma seta azul mais espessa",
        )

    assert len(provider.requests) == 0
    assert len(runner.scene_paths) == 0
    assert not (base_run.parent / "run-002").exists()
    assert project_json.read_bytes() == project_bytes
    for relative_path, content in base_snapshot.items():
        assert (base_run / relative_path).read_bytes() == content
