"""Public lifecycle contract for rerendering an accepted project."""

from __future__ import annotations

import hashlib
import json
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
from video_pipeline.golden import (
    discover_golden_projects,
    read_golden_project,
    validate_golden_project,
)
from video_pipeline.provider import ProviderRequest, ProviderResponse
from video_pipeline.scene_plan import ScenePlan


class AcceptedRunObserver(FakeProvider):
    """Record the accepted run while the canonical renderer is in progress."""

    def __init__(self, project_json: Path) -> None:
        super().__init__(project_json)
        self.accepted_runs: list[str | None] = []
        self.golden_statuses: list[str] = []

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        document = json.loads(self.project_json.read_text(encoding="utf-8"))
        self.accepted_runs.append(document["accepted_run"])
        self.golden_statuses.append(document["status"])
        _assert_golden_snapshot(self.project_json)
        response = super().generate(request)
        return ProviderResponse(
            code=f"{response.code}\n# deliberately distinct run-002 candidate\n",
            raw_response=response.raw_response,
        )


class FailingProvider(FakeProvider):
    """Fail a new run after an accepted snapshot has been published."""

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        del request
        raise RuntimeError("planned run-003 provider failure")


def _assert_golden_snapshot(project_json: Path) -> None:
    project = project_json.parent
    discovered = discover_golden_projects(project.parent)
    assert project in discovered
    loaded = read_golden_project(project)
    assert loaded.project_id == project.name
    validation = validate_golden_project(project)
    assert validation.valid, validation.reasons


def _render_with_fakes(project_json: Path, *, provider: FakeProvider, run_id: str) -> int:
    runner = FakeManimRunner()
    raw_validator = FakeRawValidator()
    observer = FakeObserver()
    normalized_validator = FakeNormalizedValidator()
    normalizer = FakeTemporalNormalizer(normalized_validator)
    final_validator = FakeFinalValidator()
    composer = FakeComposer()
    return main(
        ["render", str(project_json), "--max-attempts", "1"],
        provider=provider,
        runner=runner,
        validator=raw_validator,
        observer=observer,
        temporal_normalizer=normalizer,
        normalized_validator=normalized_validator,
        final_validator=final_validator,
        composer=composer,
        id_factory=lambda: run_id,
    )


def test_rerendering_accepted_project_preserves_last_accepted_run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
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

    assert _render_with_fakes(
        project_json,
        provider=FakeProvider(project_json),
        run_id="run-001",
    ) == 0
    capsys.readouterr()
    assert main(["accept", str(project_json), "--run", "run-001"]) == 0
    capsys.readouterr()

    accepted_document = json.loads(project_json.read_text(encoding="utf-8"))
    assert accepted_document["status"] == "accepted"
    assert accepted_document["current_run"] == "run-001"
    assert accepted_document["accepted_run"] == "run-001"

    permanent_snapshots: dict[Path, bytes] = {}
    for scene_ref in accepted_document["scenes"]:
        scene_root = project / scene_ref["path"]
        for filename in ("scene.py", "code-provenance.json"):
            path = scene_root / filename
            permanent_snapshots[path] = path.read_bytes()
    manifest_path = project / "golden" / "manifest.json"
    permanent_snapshots[manifest_path] = manifest_path.read_bytes()

    provider = AcceptedRunObserver(project_json)
    assert _render_with_fakes(project_json, provider=provider, run_id="run-002") == 0
    output = capsys.readouterr().out

    assert "READY" in output
    rerendered_document = json.loads(project_json.read_text(encoding="utf-8"))
    assert rerendered_document["status"] == "ready"
    assert rerendered_document["current_run"] == "run-002"
    assert rerendered_document["accepted_run"] == "run-001"
    assert provider.accepted_runs == ["run-001", "run-001"]
    assert provider.golden_statuses == ["rendering", "rendering"]
    _assert_golden_snapshot(project_json)

    assert _render_with_fakes(
        project_json,
        provider=FailingProvider(project_json),
        run_id="run-003",
    ) == 1
    capsys.readouterr()
    failed_document = json.loads(project_json.read_text(encoding="utf-8"))
    assert failed_document["status"] == "failed"
    assert failed_document["current_run"] == "run-003"
    assert failed_document["accepted_run"] == "run-001"
    _assert_golden_snapshot(project_json)

    for permanent_path, snapshot in permanent_snapshots.items():
        assert permanent_path.read_bytes() == snapshot
    for scene_ref in rerendered_document["scenes"]:
        candidate_root = project / "artifacts" / "run-002" / scene_ref["path"]
        for filename in ("scene.py", "code-provenance.json"):
            candidate_path = candidate_root / filename
            permanent_path = project / scene_ref["path"] / filename
            assert candidate_path.is_file()
            assert candidate_path.read_bytes() != permanent_path.read_bytes()
