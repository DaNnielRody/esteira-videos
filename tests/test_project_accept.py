"""Public acceptance contract for canonical audiovisual projects."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
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
    MissingValidationComposer,
)

from video_pipeline.cli import main
from video_pipeline.golden import validate_golden_project
from video_pipeline.project import Project
from video_pipeline.provider import ProviderRequest, ProviderResponse
from video_pipeline.scene_plan import ScenePlan


class DistinctProvider(FakeProvider):
    """Return a byte-distinct candidate for the second accepted run."""

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        response = super().generate(request)
        return ProviderResponse(
            code=f"{response.code}\n# deliberately distinct run-002 candidate\n",
            raw_response=response.raw_response,
        )


def _render_with_fakes(
    project_json: Path,
    provider: FakeProvider,
    run_id: str,
    *,
    composer: FakeComposer | None = None,
) -> int:
    normalized_validator = FakeNormalizedValidator()
    return main(
        ["render", str(project_json), "--max-attempts", "1"],
        provider=provider,
        runner=FakeManimRunner(),
        validator=FakeRawValidator(),
        observer=FakeObserver(),
        temporal_normalizer=FakeTemporalNormalizer(normalized_validator),
        normalized_validator=normalized_validator,
        final_validator=FakeFinalValidator(),
        composer=composer or FakeComposer(),
        id_factory=lambda: run_id,
    )


def _initialize_project(tmp_path: Path) -> tuple[Path, Path]:
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
    for scene_ref in json.loads(project_json.read_text(encoding="utf-8"))["scenes"]:
        plan_path = project / scene_ref["plan_path"]
        plan = ScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
        plan_path.write_text(
            json.dumps(
                plan.model_copy(update={"capabilities": ["basic_geometry"]}).to_document(),
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    return project, project_json


def _accepted_project(tmp_path: Path) -> tuple[Path, Path]:
    project, project_json = _initialize_project(tmp_path)
    assert _render_with_fakes(project_json, FakeProvider(project_json), "run-001") == 0
    assert main(["accept", str(project_json), "--run", "run-001"]) == 0
    return project, project_json


def test_accept_rejects_ready_run_when_timeline_input_hash_changes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project, project_json = _initialize_project(tmp_path)
    assert _render_with_fakes(project_json, FakeProvider(project_json), "run-001") == 0
    capsys.readouterr()

    run_json = project / "artifacts" / "run-001" / "run.json"
    run_document = json.loads(run_json.read_text(encoding="utf-8"))
    run_document["input_hashes"]["timeline_sha256"] = "0" * 64
    run_json.write_text(json.dumps(run_document), encoding="utf-8")

    assert main(["accept", str(project_json), "--run", "run-001"]) == 1
    assert "ERROR" in capsys.readouterr().out
    assert not (project / "golden" / "manifest.json").exists()
    for scene in json.loads(project_json.read_text(encoding="utf-8"))["scenes"]:
        scene_root = project / scene["path"]
        assert not (scene_root / "scene.py").exists()
        assert not (scene_root / "code-provenance.json").exists()


def test_accept_rejects_model_free_ready_run_without_capabilities(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project, project_json = _initialize_project(tmp_path)
    project_document = json.loads(project_json.read_text(encoding="utf-8"))
    for scene_ref in project_document["scenes"]:
        plan_path = project / scene_ref["plan_path"]
        plan = ScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
        plan_path.write_text(
            json.dumps(
                plan.model_copy(update={"capabilities": []}).to_document(),
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    assert _render_with_fakes(project_json, FakeProvider(project_json), "run-001") == 0
    capsys.readouterr()

    run_json = project / "artifacts" / "run-001" / "run.json"
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

    assert main(["accept", str(project_json), "--run", "run-001"]) == 1
    output = capsys.readouterr().out
    assert "ERROR" in output
    assert "capabil" in output.lower()

    after_project = json.loads(project_json.read_text(encoding="utf-8"))
    assert after_project["status"] == "ready"
    assert after_project["accepted_run"] is None
    assert project_json.read_bytes() == project_snapshot
    assert run_json.read_bytes() == run_snapshot
    assert golden_root.exists() == golden_exists
    assert snapshot_tree(golden_root) == golden_snapshot
    for path, snapshot in permanent_snapshots.items():
        assert path.is_file() == (snapshot is not None)
        if snapshot is not None:
            assert path.read_bytes() == snapshot


def test_accept_rejects_same_size_final_bytes_before_publication(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, project_json = _initialize_project(tmp_path)
    assert _render_with_fakes(project_json, FakeProvider(project_json), "run-001") == 0
    capsys.readouterr()

    final_path = project / "artifacts" / "run-001" / "final.mp4"
    original = final_path.read_bytes()
    replacement = b"x" * len(original)
    assert replacement != original
    final_path.write_bytes(replacement)
    run_json = final_path.with_name("run.json")
    snapshots = {project_json: project_json.read_bytes(), run_json: run_json.read_bytes()}
    replace_calls: list[Path] = []
    original_replace = Path.replace

    def observe_replace(self: Path, target: str | Path) -> Path:
        replace_calls.append(Path(target))
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", observe_replace)
    assert main(["accept", str(project_json), "--run", "run-001"]) == 1
    assert "ERROR" in capsys.readouterr().out
    assert replace_calls == []
    assert not (project / "golden" / "manifest.json").exists()
    for path, snapshot in snapshots.items():
        assert path.read_bytes() == snapshot


def _mutate_composition(document: dict[str, object], case: str) -> None:
    if case == "argv_empty":
        document["argv"] = []
    elif case == "argv_blank":
        document["argv"] = [""]
    elif case == "argv_non_string":
        document["argv"] = [1]
    elif case == "exit_code_nonzero":
        document["exit_code"] = 1
    elif case == "exit_code_bool":
        document["exit_code"] = True
    elif case == "error":
        document["error"] = "failed"


def test_render_rebases_fallback_validation_before_accept(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project, project_json = _initialize_project(tmp_path)
    assert (
        _render_with_fakes(
            project_json,
            FakeProvider(project_json),
            "run-001",
            composer=MissingValidationComposer(),
        )
        == 0
    )
    capsys.readouterr()

    run_path = project / "artifacts" / "run-001"
    composition = json.loads((run_path / "composition.json").read_text(encoding="utf-8"))
    run_document = json.loads((run_path / "run.json").read_text(encoding="utf-8"))
    assert composition["validation"] is not None
    assert composition["validation"]["path"] == str(run_path / "final.mp4")
    assert run_document["composition"] == composition
    assert main(["accept", str(project_json), "--run", "run-001"]) == 0
    assert validate_golden_project(project).valid


@pytest.mark.parametrize(
    "case",
    [
        "argv_empty",
        "argv_blank",
        "argv_non_string",
        "exit_code_nonzero",
        "exit_code_bool",
        "error",
    ],
)
def test_accept_rejects_invalid_composition_before_publication(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    project, project_json = _initialize_project(tmp_path)
    assert _render_with_fakes(project_json, FakeProvider(project_json), "run-001") == 0
    capsys.readouterr()

    run_path = project / "artifacts" / "run-001"
    run_json = run_path / "run.json"
    composition_path = run_path / "composition.json"
    run_document = json.loads(run_json.read_text(encoding="utf-8"))
    composition = json.loads(composition_path.read_text(encoding="utf-8"))
    _mutate_composition(run_document["composition"], case)
    _mutate_composition(composition, case)
    run_json.write_text(json.dumps(run_document), encoding="utf-8")
    composition_path.write_text(json.dumps(composition), encoding="utf-8")
    snapshots = {
        project_json: project_json.read_bytes(),
        run_json: run_json.read_bytes(),
        composition_path: composition_path.read_bytes(),
    }
    replace_calls: list[Path] = []
    original_replace = Path.replace

    def observe_replace(self: Path, target: str | Path) -> Path:
        replace_calls.append(Path(target))
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", observe_replace)
    assert main(["accept", str(project_json), "--run", "run-001"]) == 1
    assert "ERROR" in capsys.readouterr().out
    assert replace_calls == []
    for path, snapshot in snapshots.items():
        assert path.read_bytes() == snapshot, str(path)


@pytest.mark.parametrize(
    "case",
    [
        "argv_empty",
        "argv_blank",
        "argv_non_string",
        "exit_code_nonzero",
        "exit_code_bool",
        "error",
    ],
)
def test_validate_golden_rejects_synchronized_invalid_composition(
    tmp_path: Path,
    case: str,
) -> None:
    project, _ = _accepted_project(tmp_path)
    manifest_path = project / "golden" / "manifest.json"
    run_path = project / "artifacts" / "run-001"
    run_json = run_path / "run.json"
    composition_path = run_path / "composition.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    run_document = json.loads(run_json.read_text(encoding="utf-8"))
    composition = json.loads(composition_path.read_text(encoding="utf-8"))
    _mutate_composition(manifest["composition"], case)
    _mutate_composition(run_document["composition"], case)
    _mutate_composition(composition, case)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    run_json.write_text(json.dumps(run_document), encoding="utf-8")
    composition_path.write_text(json.dumps(composition), encoding="utf-8")

    result = validate_golden_project(project)

    assert result.valid is False, case


def test_validate_audiovisual_golden_rejects_timeline_projection_mismatch(
    tmp_path: Path,
) -> None:
    project, _ = _accepted_project(tmp_path)
    manifest_path = project / "golden" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["timeline"]["segments"][0]["narration_text"] = "adulterated"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = validate_golden_project(project)

    assert result.valid is False
    assert any("timeline" in reason.lower() for reason in result.reasons)


def test_accepted_golden_uses_immutable_timeline_and_scene_package_snapshots(
    tmp_path: Path,
) -> None:
    project, project_json = _accepted_project(tmp_path)
    project_document = json.loads(project_json.read_text(encoding="utf-8"))

    live_paths = [
        project / project_document["timeline_path"],
        *[
            project / scene[key]
            for scene in project_document["scenes"]
            for key in ("plan_path", "brief_path", "expectations_path")
        ],
    ]
    for path in live_paths:
        path.write_bytes(path.read_bytes() + b"\n")

    result = validate_golden_project(project)

    assert result.valid, result.reasons


@pytest.mark.parametrize(
    "case",
    [
        "timeline_file",
        "scene_omitted",
        "scene_reordered",
        "plan_file",
        "brief_file",
        "expectations_file",
        "code_file",
        "composition_output",
        "composition_validation_path",
        "final_validation_path",
        "final_artifact_path",
        "provenance_run",
        "run_input_hashes",
        "run_package_hashes",
        "project_current_scene",
        "run_current_scene",
        "run_current_scene_missing",
    ],
)
def test_validate_audiovisual_golden_rejects_deep_snapshot_tampering(
    tmp_path: Path,
    case: str,
) -> None:
    project, _ = _accepted_project(tmp_path)
    manifest_path = project / "golden" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    run_path = project / "artifacts" / "run-001"
    first_scene = manifest["scenes"][0]
    raw_path = first_scene["raw_media"]["path"]

    if case == "timeline_file":
        timeline_path = project / manifest["timeline"]["path"]
        timeline_path.write_bytes(timeline_path.read_bytes() + b"\n")
    elif case == "scene_omitted":
        manifest["scenes"].pop()
    elif case == "scene_reordered":
        manifest["scenes"].reverse()
    elif case == "plan_file":
        plan_path = project / first_scene["plan_path"]
        plan_path.write_bytes(plan_path.read_bytes() + b"\n")
    elif case == "brief_file":
        brief_path = project / first_scene["brief_path"]
        brief_path.write_bytes(brief_path.read_bytes() + b"\n")
    elif case == "expectations_file":
        expectations_path = project / first_scene["expectations_path"]
        expectations_path.write_bytes(expectations_path.read_bytes() + b"\n")
    elif case == "code_file":
        code_path = project / first_scene["code_path"]
        code_path.write_bytes(code_path.read_bytes() + b"\n")
    elif case == "composition_output":
        manifest["composition"]["output_path"] = raw_path
    elif case == "composition_validation_path":
        manifest["composition"]["validation"]["path"] = raw_path
    elif case == "final_validation_path":
        manifest["final_validation"]["path"] = raw_path
    elif case == "final_artifact_path":
        raw_media = first_scene["raw_media"]
        manifest["artifacts"]["final"] = {
            "path": raw_media["path"],
            "sha256": raw_media["sha256"],
            "size_bytes": raw_media["size_bytes"],
        }
    elif case == "provenance_run":
        manifest["provenance"]["run"] = "artifacts/run-001/composition.json"
    elif case == "run_input_hashes":
        run_document_path = run_path / "run.json"
        run_document = json.loads(run_document_path.read_text(encoding="utf-8"))
        run_document["input_hashes"]["timeline_sha256"] = "0" * 64
        run_document_path.write_text(json.dumps(run_document), encoding="utf-8")
    elif case == "run_package_hashes":
        run_document_path = run_path / "run.json"
        run_document = json.loads(run_document_path.read_text(encoding="utf-8"))
        run_document["package_hashes"][first_scene["id"]] = "0" * 64
        run_document_path.write_text(json.dumps(run_document), encoding="utf-8")
    elif case == "project_current_scene":
        project_json = project / "project.json"
        project_document = json.loads(project_json.read_text(encoding="utf-8"))
        project_document["current_scene"] = first_scene["id"]
        project_json.write_text(json.dumps(project_document), encoding="utf-8")
    elif case == "run_current_scene":
        run_document_path = run_path / "run.json"
        run_document = json.loads(run_document_path.read_text(encoding="utf-8"))
        run_document["current_scene"] = first_scene["id"]
        run_document_path.write_text(json.dumps(run_document), encoding="utf-8")
    elif case == "run_current_scene_missing":
        run_document_path = run_path / "run.json"
        run_document = json.loads(run_document_path.read_text(encoding="utf-8"))
        run_document.pop("current_scene")
        run_document_path.write_text(json.dumps(run_document), encoding="utf-8")

    if case not in {"timeline_file", "plan_file", "brief_file", "expectations_file", "code_file"}:
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = validate_golden_project(project)

    assert result.valid is False, case


@pytest.mark.parametrize("case", ["provenance", "diagnostics", "observation", "quality"])
def test_validate_audiovisual_golden_rejects_tampered_provenance_and_evidence(
    tmp_path: Path,
    case: str,
) -> None:
    project, _ = _accepted_project(tmp_path)
    manifest = json.loads(
        (project / "golden" / "manifest.json").read_text(encoding="utf-8")
    )
    scene = manifest["scenes"][0]
    evidence_key = {
        "diagnostics": "diagnostics",
        "observation": "semantic",
        "quality": "quality",
    }
    relative_path = (
        scene["provenance_path"]
        if case == "provenance"
        else scene["evidence"][evidence_key[case]]["path"]
    )
    path = project / relative_path
    path.write_bytes(path.read_bytes() + b"\n")

    result = validate_golden_project(project)

    assert result.valid is False, case


def _mutate_final_validation(document: dict[str, object], case: str) -> None:
    raw_probe = document["raw_probe"]
    video_stream = document["video_streams"][0]
    audio_stream = document["audio_streams"][0]
    raw_video_stream = raw_probe["streams"][0]
    raw_audio_stream = raw_probe["streams"][1]
    if case == "streams_empty":
        document["video_streams"] = []
        document["audio_streams"] = []
        raw_probe["streams"] = []
    elif case == "streams_projection":
        document["video_streams"] = []
    elif case == "streams_duplicated":
        document["video_streams"].append(deepcopy(video_stream))
        raw_probe["streams"].append(deepcopy(raw_video_stream))
    elif case == "invalid_flag":
        document["valid"] = False
    elif case == "nonempty_reasons":
        document["reasons"] = ["tampered"]
    elif case == "video_codec":
        video_stream["codec_name"] = "vp9"
        raw_video_stream["codec_name"] = "vp9"
    elif case == "audio_codec":
        audio_stream["codec_name"] = "opus"
        raw_audio_stream["codec_name"] = "opus"
    elif case == "audio_unusable":
        audio_stream["sample_rate"] = "0"
        audio_stream["channels"] = 0
        raw_audio_stream["sample_rate"] = "0"
        raw_audio_stream["channels"] = 0
    elif case == "video_pixel_format":
        video_stream["pix_fmt"] = "yuv444p"
        raw_video_stream["pix_fmt"] = "yuv444p"
    elif case == "video_resolution":
        video_stream["width"] = 640
        raw_video_stream["width"] = 640
    elif case == "contract_projection":
        video_stream["width"] = 640
        raw_video_stream["width"] = 640
    elif case == "video_fps":
        video_stream["avg_frame_rate"] = "30/1"
        raw_video_stream["avg_frame_rate"] = "30/1"
    elif case == "video_timebase":
        video_stream["time_base"] = "1/1000"
        raw_video_stream["time_base"] = "1/1000"
    elif case == "probe_returncode":
        document["probe_returncode"] = 1
    elif case == "size":
        document["size_bytes"] += 1
    elif case == "probe_size":
        document["probe_size_bytes"] += 1
    elif case == "probe_format_size":
        raw_probe["format"]["size"] = str(int(raw_probe["format"]["size"]) + 1)
    elif case == "format_duration_missing":
        raw_probe["format"].pop("duration", None)
    elif case == "format_duration_wrong":
        raw_probe["format"]["duration"] = "999"
    elif case == "format_duration_nonfinite":
        raw_probe["format"]["duration"] = "nan"
    elif case == "validation_path":
        document["path"] = "artifacts/run-001/scenes/01_abertura/raw.mp4"
    elif case == "video_duration_incoherent":
        document["video_duration_seconds"] = 9.0
        raw_video_stream["duration"] = "9.0"
        video_stream["duration"] = "9.0"
    elif case == "video_drift_incoherent":
        document["video_drift_seconds"] = 1.0
    elif case == "video_drift_outside":
        document["video_duration_seconds"] = 8.0
        document["video_drift_seconds"] = -2.0
        raw_video_stream["duration"] = "8.0"
        video_stream["duration"] = "8.0"
    elif case == "audio_duration_incoherent":
        document["audio_duration_seconds"] = 9.0
        raw_audio_stream["duration"] = "9.0"
        audio_stream["duration"] = "9.0"
    elif case == "audio_drift_incoherent":
        document["audio_drift_seconds"] = 1.0
    elif case == "audio_drift_outside":
        document["audio_duration_seconds"] = 8.0
        document["audio_drift_seconds"] = -2.0
        document["audio_video_drift_seconds"] = -2.0
        raw_audio_stream["duration"] = "8.0"
        audio_stream["duration"] = "8.0"
    elif case == "av_drift_incoherent":
        document["audio_video_drift_seconds"] = 1.0
    elif case == "av_drift_outside":
        document["audio_duration_seconds"] = 8.0
        document["audio_drift_seconds"] = -2.0
        document["audio_video_drift_seconds"] = -2.0
        raw_audio_stream["duration"] = "8.0"
        audio_stream["duration"] = "8.0"
    elif case == "video_duration_nonfinite":
        document["video_duration_seconds"] = None
    elif case == "tolerance_relaxed":
        document["audio_duration_seconds"] = 8.0
        document["audio_drift_seconds"] = -2.0
        document["audio_video_drift_seconds"] = -2.0
        raw_audio_stream["duration"] = "8.0"
        audio_stream["duration"] = "8.0"


@pytest.mark.parametrize(
    "case",
    [
        "streams_empty",
        "streams_projection",
        "streams_duplicated",
        "invalid_flag",
        "nonempty_reasons",
        "video_codec",
        "audio_codec",
        "audio_unusable",
        "video_pixel_format",
        "video_resolution",
        "contract_projection",
        "video_fps",
        "video_timebase",
        "probe_returncode",
        "size",
        "probe_size",
        "probe_format_size",
        "format_duration_missing",
        "format_duration_wrong",
        "format_duration_nonfinite",
        "video_duration_incoherent",
        "video_drift_incoherent",
        "video_drift_outside",
        "audio_duration_incoherent",
        "audio_drift_incoherent",
        "audio_drift_outside",
        "av_drift_incoherent",
        "av_drift_outside",
        "video_duration_nonfinite",
        "tolerance_relaxed",
    ],
)
def test_validate_audiovisual_golden_rejects_invalid_final_media_facts(
    tmp_path: Path,
    case: str,
) -> None:
    project, _ = _accepted_project(tmp_path)
    manifest_path = project / "golden" / "manifest.json"
    run_path = project / "artifacts" / "run-001"
    composition_path = run_path / "composition.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    run_document = json.loads((run_path / "run.json").read_text(encoding="utf-8"))
    composition = json.loads(composition_path.read_text(encoding="utf-8"))
    validation_documents = [
        manifest["final_validation"],
        manifest["composition"]["validation"],
        run_document["final_validation"],
        run_document["composition"]["validation"],
        composition["validation"],
    ]
    for document in validation_documents:
        _mutate_final_validation(document, case)
    if case == "contract_projection":
        manifest["final_media_contract"]["resolution"]["width"] = 640
    elif case == "tolerance_relaxed":
        manifest["tolerances"]["final_duration_seconds"] = 2.0
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (run_path / "run.json").write_text(json.dumps(run_document), encoding="utf-8")
    composition_path.write_text(json.dumps(composition), encoding="utf-8")

    result = validate_golden_project(project)

    assert result.valid is False, case


@pytest.mark.parametrize(
    "case",
    [
        "validation_path",
        "validation_streams_empty",
        "validation_streams_projection",
        "validation_streams_duplicated",
        "validation_invalid_flag",
        "validation_nonempty_reasons",
        "validation_video_codec",
        "validation_audio_codec",
        "validation_audio_unusable",
        "validation_video_pixel_format",
        "validation_video_resolution",
        "validation_contract_projection",
        "validation_video_fps",
        "validation_video_timebase",
        "validation_probe_returncode",
        "validation_size",
        "validation_probe_size",
        "validation_probe_format_size",
        "validation_format_duration_missing",
        "validation_format_duration_wrong",
        "validation_format_duration_nonfinite",
        "validation_video_duration_incoherent",
        "validation_video_drift_incoherent",
        "validation_video_drift_outside",
        "validation_audio_duration_incoherent",
        "validation_audio_drift_incoherent",
        "validation_audio_drift_outside",
        "validation_av_drift_incoherent",
        "validation_av_drift_outside",
        "validation_video_duration_nonfinite",
        "validation_tolerance_relaxed",
    ],
)
def test_accept_rejects_tampered_final_validation_before_publication(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    project, project_json = _initialize_project(tmp_path)
    assert _render_with_fakes(project_json, FakeProvider(project_json), "run-001") == 0
    capsys.readouterr()

    run_path = project / "artifacts" / "run-001"
    run_json = run_path / "run.json"
    composition_path = run_path / "composition.json"
    run_document = json.loads(run_json.read_text(encoding="utf-8"))
    composition = json.loads(composition_path.read_text(encoding="utf-8"))
    validation_documents = [
        run_document["final_validation"],
        run_document["composition"]["validation"],
        composition["validation"],
    ]
    if case == "validation_path":
        for document in validation_documents:
            _mutate_final_validation(document, "validation_path")
    else:
        for document in validation_documents:
            _mutate_final_validation(document, case.removeprefix("validation_"))
    run_json.write_text(json.dumps(run_document), encoding="utf-8")
    composition_path.write_text(json.dumps(composition), encoding="utf-8")

    permanent_paths = [
        project / scene["path"] / filename
        for scene in json.loads(project_json.read_text(encoding="utf-8"))["scenes"]
        for filename in ("scene.py", "code-provenance.json")
    ]
    snapshots = {
        path: path.read_bytes()
        for path in (project_json, run_json, composition_path, *permanent_paths)
        if path.is_file()
    }
    replace_calls: list[Path] = []
    original_replace = Path.replace

    def observe_replace(self: Path, target: str | Path) -> Path:
        replace_calls.append(Path(target))
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", observe_replace)
    assert main(["accept", str(project_json), "--run", "run-001"]) == 1
    assert "ERROR" in capsys.readouterr().out
    assert replace_calls == []
    for path, snapshot in snapshots.items():
        assert path.read_bytes() == snapshot, str(path)


@pytest.mark.parametrize(
    ("case", "expected_error"),
    [
        ("requested_run", "run is not the project\'s current run"),
        ("project_non_ready", "only a ready project can be accepted"),
        ("run_non_ready", "run must be ready before acceptance"),
        ("script_hash", "script hash does not match project.json"),
        ("audio_hash", "audio hash does not match project.json"),
        ("timeline_hash", "ready run input hashes do not match"),
        ("package_hash", "ready run package hashes do not match"),
        ("plan_package", "ready run package hashes do not match"),
        ("brief_package", "ready run package hashes do not match"),
        ("expectations_package", "ready run package hashes do not match"),
        ("candidate_code_hash", "stored code_sha256 does not match"),
        ("record_code_path", "code_path is not canonical"),
        ("project_current_scene", "ready project current_scene must be null"),
        ("run_current_scene", "ready run current_scene must be null"),
        ("run_current_scene_missing", "ready run current_scene is required"),
    ],
)
def test_accept_rejects_invalid_ready_snapshot_without_replacing_prior_publication(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_error: str,
) -> None:
    project, project_json = _initialize_project(tmp_path)
    assert _render_with_fakes(project_json, FakeProvider(project_json), "run-001") == 0
    capsys.readouterr()
    assert main(["accept", str(project_json), "--run", "run-001"]) == 0
    capsys.readouterr()
    assert _render_with_fakes(
        project_json,
        DistinctProvider(project_json),
        "run-002",
    ) == 0
    capsys.readouterr()

    run_json = project / "artifacts" / "run-002" / "run.json"
    manifest_path = project / "golden" / "manifest.json"
    project_document = json.loads(project_json.read_text(encoding="utf-8"))
    run_document = json.loads(run_json.read_text(encoding="utf-8"))
    first_scene = project_document["scenes"][0]
    if case == "project_non_ready":
        project_document["status"] = "rendering"
        project_json.write_text(json.dumps(project_document), encoding="utf-8")
    elif case == "run_non_ready":
        run_document["state"] = "rendering"
        run_json.write_text(json.dumps(run_document), encoding="utf-8")
    elif case == "script_hash":
        (project / project_document["script_path"]).write_bytes(
            (project / project_document["script_path"]).read_bytes() + b"\nchanged"
        )
    elif case == "audio_hash":
        (project / project_document["audio_path"]).write_bytes(
            (project / project_document["audio_path"]).read_bytes() + b"\x00"
        )
    elif case == "timeline_hash":
        run_document["input_hashes"]["timeline_sha256"] = "0" * 64
        run_json.write_text(json.dumps(run_document), encoding="utf-8")
    elif case == "package_hash":
        run_document["package_hashes"][first_scene["id"]] = "0" * 64
        run_json.write_text(json.dumps(run_document), encoding="utf-8")
    elif case in {"plan_package", "brief_package", "expectations_package"}:
        package_key = {
            "plan_package": "plan_path",
            "brief_package": "brief_path",
            "expectations_package": "expectations_path",
        }[case]
        package_path = project / first_scene[package_key]
        package_path.write_bytes(package_path.read_bytes() + b"\n")
    elif case == "candidate_code_hash":
        candidate_code = project / "artifacts" / "run-002" / first_scene["path"] / "scene.py"
        candidate_code.write_bytes(candidate_code.read_bytes() + b"\nchanged")
    elif case == "record_code_path":
        run_document["scenes"][0]["code_path"] = str(
            project / first_scene["path"] / "scene.py"
        )
        run_json.write_text(json.dumps(run_document), encoding="utf-8")
    elif case == "project_current_scene":
        project_document["current_scene"] = first_scene["id"]
        project_json.write_text(json.dumps(project_document), encoding="utf-8")
    elif case == "run_current_scene":
        run_document["current_scene"] = first_scene["id"]
        run_json.write_text(json.dumps(run_document), encoding="utf-8")
    elif case == "run_current_scene_missing":
        run_document.pop("current_scene")
        run_json.write_text(json.dumps(run_document), encoding="utf-8")

    requested_run = "run-001" if case == "requested_run" else "run-002"
    permanent_paths = [
        project / scene["path"] / filename
        for scene in project_document["scenes"]
        for filename in ("scene.py", "code-provenance.json")
    ]
    candidate_paths = [
        project / "artifacts" / "run-002" / scene["path"] / filename
        for scene in project_document["scenes"]
        for filename in ("scene.py", "code-provenance.json")
    ]
    snapshot_paths = (
        project_json,
        run_json,
        manifest_path,
        project / project_document["script_path"],
        project / project_document["audio_path"],
        *permanent_paths,
        *candidate_paths,
    )
    snapshots = {path: path.read_bytes() for path in snapshot_paths}
    replace_calls: list[Path] = []
    original_replace = Path.replace

    def observe_replace(self: Path, target: str | Path) -> Path:
        replace_calls.append(Path(target))
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", observe_replace)
    assert main(["accept", str(project_json), "--run", requested_run]) == 1
    output = capsys.readouterr().out
    assert expected_error.lower() in output.lower()
    assert replace_calls == []
    for path, snapshot in snapshots.items():
        assert path.read_bytes() == snapshot, str(path)


@pytest.mark.parametrize(
    "case",
    [
        "run_composition",
        "run_final_validation",
        "provenance",
        "diagnostics",
        "observation",
        "quality",
    ],
)
def test_accept_rejects_tampered_ready_attestation_before_publication(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    project, project_json = _initialize_project(tmp_path)
    assert _render_with_fakes(project_json, FakeProvider(project_json), "run-001") == 0
    capsys.readouterr()

    run_json = project / "artifacts" / "run-001" / "run.json"
    run_document = json.loads(run_json.read_text(encoding="utf-8"))
    project_document = json.loads(project_json.read_text(encoding="utf-8"))
    first_scene = run_document["scenes"][0]
    if case == "run_composition":
        run_document["composition"]["argv"].append("tampered")
        run_json.write_text(json.dumps(run_document), encoding="utf-8")
    elif case == "run_final_validation":
        run_document["final_validation"]["video_duration_seconds"] = 9.0
        run_json.write_text(json.dumps(run_document), encoding="utf-8")
    else:
        evidence_name = {
            "provenance": "code-provenance.json",
            "diagnostics": "diagnostics.json",
            "observation": "observation.json",
            "quality": "quality-report.json",
        }[case]
        if case == "provenance":
            evidence_path = (
                project
                / "artifacts"
                / "run-001"
                / project_document["scenes"][0]["path"]
                / evidence_name
            )
        else:
            evidence_path = Path(first_scene["latest_attempt_path"]) / evidence_name
        assert evidence_path.is_file()
        evidence_path.write_bytes(evidence_path.read_bytes() + b"\n")

    manifest_path = project / "golden" / "manifest.json"
    candidate_paths = [
        project / "artifacts" / "run-001" / scene["path"] / filename
        for scene in project_document["scenes"]
        for filename in ("scene.py", "code-provenance.json")
    ]
    snapshots = {
        path: path.read_bytes()
        for path in (project_json, run_json, *candidate_paths)
        if path.is_file()
    }
    replace_calls: list[Path] = []
    original_replace = Path.replace

    def observe_replace(self: Path, target: str | Path) -> Path:
        replace_calls.append(Path(target))
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", observe_replace)
    assert main(["accept", str(project_json), "--run", "run-001"]) == 1
    assert "ERROR" in capsys.readouterr().out
    assert replace_calls == []
    assert not manifest_path.exists()
    for path, snapshot in snapshots.items():
        assert path.read_bytes() == snapshot, str(path)


def test_accept_promotes_ready_project_to_audiovisual_golden_without_execution(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = tmp_path / "roteiro.md"
    script_bytes = (
        b"# Abertura\n"
        b"@start: 0\n"
        b"@end: 4\n"
        b"@objective: Introduza vetores.\n"
        b"Esta e a abertura exata.\n\n"
        b"## Explicacao\n"
        b"@start: 4\n"
        b"@end: 10\n"
        b"@objective: Explique a soma.\n"
        b"Esta e a explicacao exata.\n"
    )
    script.write_bytes(script_bytes)
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
    for scene_ref in json.loads(project_json.read_text(encoding="utf-8"))["scenes"]:
        plan_path = project / scene_ref["plan_path"]
        plan = ScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
        plan_path.write_text(
            json.dumps(
                plan.model_copy(update={"capabilities": ["basic_geometry"]}).to_document(),
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    provider = FakeProvider(project_json)
    runner = FakeManimRunner()
    raw_validator = FakeRawValidator()
    observer = FakeObserver()
    normalized_validator = FakeNormalizedValidator()
    normalizer = FakeTemporalNormalizer(normalized_validator)
    final_validator = FakeFinalValidator()
    composer = FakeComposer()
    assert (
        main(
            ["render", str(project_json), "--max-attempts", "1"],
            provider=provider,
            runner=runner,
            validator=raw_validator,
            observer=observer,
            temporal_normalizer=normalizer,
            normalized_validator=normalized_validator,
            final_validator=final_validator,
            composer=composer,
            id_factory=lambda: "run-001",
        )
        == 0
    )
    capsys.readouterr()

    before_project = Project.model_validate_json(project_json.read_text(encoding="utf-8"))
    permanent_code_paths = [
        project / scene.path / "scene.py"
        for scene in before_project.scenes
    ]
    permanent_provenance_paths = [
        project / scene.path / "code-provenance.json"
        for scene in before_project.scenes
    ]
    assert all(not path.exists() for path in permanent_code_paths)
    assert all(not path.exists() for path in permanent_provenance_paths)
    candidate_paths = [
        project / "artifacts" / "run-001" / scene.path / "scene.py"
        for scene in before_project.scenes
    ] + [
        project / "artifacts" / "run-001" / scene.path / "code-provenance.json"
        for scene in before_project.scenes
    ]
    assert all(path.is_file() for path in candidate_paths)
    input_and_code_paths = [
        project / before_project.script_path,
        project / before_project.audio_path,
        *candidate_paths,
    ]
    before_input_and_code = {
        path.relative_to(project).as_posix(): path.read_bytes()
        for path in input_and_code_paths
    }
    counts = {
        "provider": len(provider.requests),
        "runner": len(runner.scene_paths),
        "raw_validator": len(raw_validator.calls),
        "normalizer": len(normalizer.calls),
        "normalized_validator": len(normalized_validator.calls),
        "final_validator": len(final_validator.calls),
        "composer": len(composer.scene_paths),
    }

    assert main(["accept", str(project_json), "--run", "run-001"]) == 0
    output = capsys.readouterr().out
    assert "ACCEPTED" in output

    after_project_document = json.loads(project_json.read_text(encoding="utf-8"))
    assert after_project_document["status"] == "accepted"
    assert after_project_document["current_run"] == "run-001"
    assert after_project_document["accepted_run"] == "run-001"
    assert after_project_document["script_sha256"] == hashlib.sha256(script_bytes).hexdigest()
    assert after_project_document["audio"]["hash"] == hashlib.sha256(audio_bytes).hexdigest()
    assert after_project_document["audio"]["duration"] == 10.0

    manifest_path = project / "golden" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["profile"] == "audiovisual"
    assert manifest["schema_version"] == "golden.manifest/1"
    assert manifest["version"] == 1
    assert manifest["status"] == "accepted"
    assert manifest["project_id"] == "2026_vetores"
    assert manifest["title"] == "Vetores"
    assert "accepted" not in manifest
    assert manifest["run_id"] == "run-001"
    assert manifest["inputs"]["script"]["path"] == "script.md"
    assert manifest["inputs"]["script"]["sha256"] == after_project_document["script_sha256"]
    assert manifest["inputs"]["audio"]["path"] == "audio/narration.wav"
    assert manifest["inputs"]["audio"]["facts"] == after_project_document["audio"]
    assert manifest["timeline"]["method"] == "explicit_timestamp"
    assert manifest["timeline"]["sha256"] == hashlib.sha256(
        (project / "timeline.json").read_bytes()
    ).hexdigest()
    assert [scene["id"] for scene in manifest["timeline"]["segments"]] == [
        "abertura",
        "explicacao",
    ]
    assert manifest["theme"]["id"] == "production"
    assert manifest["capabilities"] == ["basic_geometry"]
    assert manifest["tolerances"]
    assert manifest["runtime_versions"]["python"]
    assert manifest["provenance"]["project"] == "project.json"

    assert len(manifest["scenes"]) == 2
    for scene_ref, scene in zip(
        before_project.scenes,
        manifest["scenes"],
        strict=True,
    ):
        assert scene["plan_path"].endswith("/plan.json")
        assert len(scene["plan_sha256"]) == 64
        assert scene["brief_path"].endswith("/brief.json")
        assert scene["brief_sha256"] == hashlib.sha256(
            (project / scene_ref.brief_path).read_bytes()
        ).hexdigest()
        assert scene["expectations_path"].endswith("/expectations.json")
        assert scene["expectations_sha256"] == hashlib.sha256(
            (project / scene_ref.expectations_path).read_bytes()
        ).hexdigest()
        assert scene["code_path"].endswith("/scene.py")
        assert len(scene["code_sha256"]) == 64
        assert scene["provenance_path"].endswith("/code-provenance.json")
        candidate_code = project / "artifacts" / "run-001" / scene_ref.path / "scene.py"
        candidate_provenance = (
            project
            / "artifacts"
            / "run-001"
            / scene_ref.path
            / "code-provenance.json"
        )
        permanent_code = project / scene_ref.path / "scene.py"
        permanent_provenance = project / scene_ref.path / "code-provenance.json"
        assert permanent_code.read_bytes() == candidate_code.read_bytes()
        assert permanent_provenance.read_bytes() == candidate_provenance.read_bytes()
        assert scene["code_sha256"] == hashlib.sha256(candidate_code.read_bytes()).hexdigest()
        assert scene["evidence"]["raw"]["path"].endswith("/raw.mp4")
        assert scene["evidence"]["normalized"]["path"].endswith("/normalized.mp4")
        assert scene["evidence"]["normalization"]["path"].endswith(
            "/normalization.json"
        )
        assert scene["semantic"] is not None
        assert scene["quality"] is not None
        assert scene["raw_media"]["size_bytes"] > 0
        assert scene["normalized_media"]["size_bytes"] > 0

    assert manifest["composition"]["output_path"].endswith("artifacts/run-001/final.mp4")
    assert manifest["composition"]["argv"]
    assert manifest["final_validation"]["valid"] is True
    assert manifest["final_validation"]["video_duration_seconds"] == 10.0
    assert manifest["final_media_contract"]["video_codec"] == "libx264"
    assert manifest["final_media_contract"]["audio_codec"] == "aac"
    assert manifest["final_media_contract"]["pixel_format"] == "yuv420p"
    assert manifest["final_media_contract"]["resolution"] == {"width": 854, "height": 480}
    assert manifest["artifacts"]["final"]["path"] == "artifacts/run-001/final.mp4"
    assert manifest["artifacts"]["final"]["size_bytes"] == (
        project / "artifacts" / "run-001" / "final.mp4"
    ).stat().st_size
    assert not (project / "golden" / "final.mp4").exists()
    golden_validation = validate_golden_project(project)
    assert golden_validation.valid, golden_validation.reasons

    assert counts == {
        "provider": len(provider.requests),
        "runner": len(runner.scene_paths),
        "raw_validator": len(raw_validator.calls),
        "normalizer": len(normalizer.calls),
        "normalized_validator": len(normalized_validator.calls),
        "final_validator": len(final_validator.calls),
        "composer": len(composer.scene_paths),
    }
    for relative_path, content in before_input_and_code.items():
        assert (project / relative_path).read_bytes() == content


def test_accept_rolls_back_all_targets_when_publishing_second_run_fails(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, project_json = _initialize_project(tmp_path)
    assert _render_with_fakes(
        project_json,
        FakeProvider(project_json),
        "run-001",
    ) == 0
    capsys.readouterr()
    assert main(["accept", str(project_json), "--run", "run-001"]) == 0
    capsys.readouterr()

    manifest_path = project / "golden" / "manifest.json"
    permanent_paths = [
        project / scene["path"] / filename
        for scene in json.loads(project_json.read_text(encoding="utf-8"))["scenes"]
        for filename in ("scene.py", "code-provenance.json")
    ]
    accepted_snapshot = {
        path: path.read_bytes()
        for path in (manifest_path, *permanent_paths)
    }

    assert _render_with_fakes(
        project_json,
        DistinctProvider(project_json),
        "run-002",
    ) == 0
    capsys.readouterr()
    accepted_snapshot[project_json] = project_json.read_bytes()
    run_two_document = json.loads(project_json.read_text(encoding="utf-8"))
    candidate_paths = [
        project / "artifacts" / "run-002" / scene["path"] / filename
        for scene in run_two_document["scenes"]
        for filename in ("scene.py", "code-provenance.json")
    ]
    candidate_snapshot = {path: path.read_bytes() for path in candidate_paths}

    original_replace = Path.replace
    injected = False

    def fail_manifest_replace(self: Path, target: str | Path) -> Path:
        nonlocal injected
        if Path(target) == manifest_path and not injected:
            injected = True
            raise OSError("injected accept publication failure")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_manifest_replace)
    assert main(["accept", str(project_json), "--run", "run-002"]) == 1
    assert injected is True
    assert "ERROR" in capsys.readouterr().out

    for path, snapshot in accepted_snapshot.items():
        assert path.read_bytes() == snapshot, str(path)
    for path, snapshot in candidate_snapshot.items():
        assert path.read_bytes() == snapshot

    monkeypatch.undo()
    assert main(["accept", str(project_json), "--run", "run-002"]) == 0
    capsys.readouterr()
    accepted_run_two = json.loads(project_json.read_text(encoding="utf-8"))
    assert accepted_run_two["status"] == "accepted"
    assert accepted_run_two["accepted_run"] == "run-002"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["run_id"] == "run-002"
    for scene in accepted_run_two["scenes"]:
        for filename in ("scene.py", "code-provenance.json"):
            permanent = project / scene["path"] / filename
            candidate = project / "artifacts" / "run-002" / scene["path"] / filename
            assert permanent.read_bytes() == candidate.read_bytes()
