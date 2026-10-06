"""Public JSON contract for canonical project inspection."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from test_project_render import (
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
from video_pipeline.provider import ProviderRequest, ProviderResponse


class FakeAudioProbe:
    """Return deterministic facts for one copied narration file."""

    def __init__(self, facts: dict[str, object]) -> None:
        self.facts = facts

    def __call__(self, path: Path) -> dict[str, object]:
        assert path.name == "narration.wav"
        assert path.parent.name == "audio"
        return dict(self.facts)


class EmptySilenceDetector:
    """Create a candidate without probing real audio."""

    def __call__(self, path: Path) -> tuple[object, ...]:
        del path
        return ()


class InspectInterruptingProvider(FakeProvider):
    """Interrupt once at scene two so inspect can report a partial run."""

    def __init__(self, project_json: Path) -> None:
        super().__init__(project_json)
        self.interrupt_scene_two = True

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        if request.scene_name == "ExplicacaoScene" and self.interrupt_scene_two:
            self.interrupt_scene_two = False
            raise KeyboardInterrupt("inspect interruption")
        return super().generate(request)


def _audio_facts(audio_bytes: bytes, duration: float) -> dict[str, object]:
    return {
        "path": "audio/narration.wav",
        "hash": hashlib.sha256(audio_bytes).hexdigest(),
        "container": "wav",
        "codec": "pcm_s16le",
        "stream": 0,
        "sample_rate": 48_000,
        "channels": 2,
        "duration": duration,
        "size": len(audio_bytes),
        "probe_result": {"format": {}, "streams": []},
    }


def _initialize(
    root: Path,
    *,
    name: str,
    script_text: str,
    duration: float,
    silence_detector: object | None = None,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    script = root / f"{name}.md"
    script.write_text(script_text, encoding="utf-8")
    audio = root / f"{name}.wav"
    audio_bytes = f"immutable narration {name}".encode("utf-8")
    audio.write_bytes(audio_bytes)
    project = root / "projects" / f"2026_{name}"
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
            audio_probe=FakeAudioProbe(_audio_facts(audio_bytes, duration)),
            silence_detector=silence_detector,
        )
        == 0
    )
    return project


def _assert_no_absolute_project_root(value: object, project_root: Path) -> None:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
    assert str(project_root.resolve()) not in serialized


def test_inspect_reports_candidate_and_ready_project_without_execution(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    candidate = _initialize(
        tmp_path,
        name="candidate",
        script_text=(
            "# Origem\nVetor inicial.\n\n"
            "## Resultado\nCompare os comprimentos finais.\n"
        ),
        duration=10.0,
        silence_detector=EmptySilenceDetector(),
    )
    capsys.readouterr()

    candidate_exit = main(["inspect", str(candidate / "project.json")])
    assert candidate_exit == 0
    candidate_output = capsys.readouterr().out
    candidate_summary = json.loads(candidate_output)
    _assert_no_absolute_project_root(candidate_summary, candidate)
    assert candidate_summary["project"]["status"] == "timeline_candidate"
    assert candidate_summary["project"]["current_run"] is None
    assert candidate_summary["project"]["current_scene"] is None
    assert candidate_summary["project"]["accepted_run"] is None
    assert candidate_summary["project"]["planning_state"] == "review_required"
    assert candidate_summary["project"]["render_state"] == "pending"
    assert candidate_summary["project"]["composition_state"] == "pending"
    assert candidate_summary["timeline"]["status"] == "candidate"
    assert candidate_summary["timeline"]["method"] == "proportional_fallback"
    limitations = [
        *candidate_summary["timeline"]["warnings"],
        *candidate_summary["timeline"]["manual_review_reasons"],
    ]
    assert any("ASR" in reason or "forced alignment" in reason for reason in limitations)
    assert [scene["id"] for scene in candidate_summary["scenes"]] == [
        "origem",
        "resultado",
    ]
    assert candidate_summary["latest_run"] is None
    for scene in candidate_summary["scenes"]:
        assert not Path(scene["path"]).is_absolute()
        assert ".." not in Path(scene["path"]).parts

    ready = _initialize(
        tmp_path / "ready",
        name="ready",
        script_text=(
            "# Abertura\n"
            "@start: 0\n"
            "@end: 4\n"
            "@objective: Introduza vetores.\n"
            "Esta e a abertura exata.\n\n"
            "## Explicacao\n"
            "@start: 4\n"
            "@end: 10\n"
            "@objective: Explique a soma.\n"
            "Esta e a explicacao exata.\n"
        ),
        duration=10.0,
    )
    ready_json = ready / "project.json"
    provider = FakeProvider(ready_json)
    normalized_validator = FakeNormalizedValidator()
    assert (
        main(
            ["render", str(ready_json), "--max-attempts", "1"],
            provider=provider,
            runner=FakeManimRunner(),
            validator=FakeRawValidator(),
            observer=FakeObserver(),
            temporal_normalizer=FakeTemporalNormalizer(normalized_validator),
            normalized_validator=normalized_validator,
            final_validator=FakeFinalValidator(),
            composer=FakeComposer(),
            id_factory=lambda: "inspect-run",
        )
        == 0
    )
    capsys.readouterr()

    ready_exit = main(["inspect", str(ready_json)])
    assert ready_exit == 0
    ready_summary = json.loads(capsys.readouterr().out)
    _assert_no_absolute_project_root(ready_summary, ready)
    assert ready_summary["project"]["status"] == "ready"
    assert ready_summary["project"]["current_run"] == "inspect-run"
    assert ready_summary["project"]["current_scene"] is None
    assert ready_summary["project"]["accepted_run"] is None
    assert ready_summary["project"]["planning_state"] == "ready"
    assert ready_summary["project"]["render_state"] == "ready"
    assert ready_summary["project"]["composition_state"] == "ready"
    assert ready_summary["timeline"]["status"] == "confirmed"
    assert ready_summary["timeline"]["method"] == "explicit_timestamp"

    run = ready_summary["latest_run"]
    assert run["state"] == "ready"
    assert run["current_scene"] is None
    assert run["error"] is None
    assert "accept" in run["action_next"].lower()
    assert run["progress"] == {
        "total": 2,
        "queued": 0,
        "rendering": 0,
        "ready": 2,
        "failed": 0,
    }
    assert run["composition"]["state"] == "ready"
    assert run["composition"]["exit_code"] == 0
    assert run["composition"]["error"] is None
    assert run["composition"]["elapsed_seconds"] == pytest.approx(0.03)
    assert run["composition"]["output"]["path"] == "artifacts/inspect-run/final.mp4"
    assert run["composition"]["validation"] == {
        "valid": True,
        "reasons": [],
        "video_codecs": ["h264"],
        "audio_codecs": ["aac"],
        "video_duration_seconds": 10.0,
        "audio_duration_seconds": 10.0,
        "expected_duration_seconds": 10.0,
        "video_drift_seconds": 0.0,
        "audio_drift_seconds": 0.0,
        "audio_video_drift_seconds": 0.0,
    }
    assert [scene["id"] for scene in run["scenes"]] == [
        "abertura",
        "explicacao",
    ]
    for scene in run["scenes"]:
        assert scene["state"] == "ready"
        assert scene["attempts"] == 1
        assert scene["error"] is None
        assert scene["action_next"]
        correction = scene["temporal_correction"]
        assert correction == {
            "status": "normalized",
            "operation": "normalize",
            "observed_duration_seconds": scene["id"] == "abertura" and 4.0 or 6.0,
            "target_duration_seconds": scene["id"] == "abertura" and 4.0 or 6.0,
            "delta_seconds": 0.0,
            "validated_duration_seconds": scene["id"] == "abertura" and 4.0 or 6.0,
            "reasons": [],
        }
        for artifact_name in ("raw", "normalized", "code", "provenance"):
            artifact = scene["artifacts"][artifact_name]
            artifact_path = Path(artifact["path"])
            assert not artifact_path.is_absolute()
            assert ".." not in artifact_path.parts
            assert artifact_path.parts[:2] == ("artifacts", "inspect-run")
            assert artifact["size_bytes"] == (ready / artifact_path).stat().st_size
    final_artifact = run["artifacts"]["final"]
    final_path = Path(final_artifact["path"])
    assert not final_path.is_absolute()
    assert final_artifact["size_bytes"] == (ready / final_path).stat().st_size


def test_inspect_reports_interrupted_run_and_missing_evidence_without_execution(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = _initialize(
        tmp_path,
        name="interrupted",
        script_text=(
            "# Abertura\n"
            "@start: 0\n"
            "@end: 4\n"
            "@objective: Introduza vetores.\n"
            "Esta e a abertura exata.\n\n"
            "## Explicacao\n"
            "@start: 4\n"
            "@end: 10\n"
            "@objective: Explique a soma.\n"
            "Esta e a explicacao exata.\n"
        ),
        duration=10.0,
    )
    project_json = project / "project.json"
    normalized_validator = FakeNormalizedValidator()
    with pytest.raises(KeyboardInterrupt, match="inspect interruption"):
        main(
            ["render", str(project_json), "--max-attempts", "1"],
            provider=InspectInterruptingProvider(project_json),
            runner=FakeManimRunner(),
            validator=FakeRawValidator(),
            observer=FakeObserver(),
            temporal_normalizer=FakeTemporalNormalizer(normalized_validator),
            normalized_validator=normalized_validator,
            final_validator=FakeFinalValidator(),
            composer=FakeComposer(),
            id_factory=lambda: "inspect-interrupted-run",
        )
    capsys.readouterr()

    run_path = project / "artifacts" / "inspect-interrupted-run" / "run.json"
    run_document = json.loads(run_path.read_text(encoding="utf-8"))
    run_document["error"] = f"provider failed below {project.resolve()}"
    run_path.write_text(
        json.dumps(run_document, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    assert main(["inspect", str(project_json)]) == 0
    summary = json.loads(capsys.readouterr().out)
    _assert_no_absolute_project_root(summary, project)
    assert summary["project"]["status"] == "rendering"
    assert summary["project"]["current_run"] == "inspect-interrupted-run"
    assert summary["project"]["current_scene"] == "explicacao"
    assert summary["project"]["composition_state"] == "pending"
    run = summary["latest_run"]
    assert run["state"] == "rendering"
    assert run["current_scene"] == "explicacao"
    assert run["composition"]["state"] == "pending"
    assert run["progress"] == {
        "total": 2,
        "queued": 0,
        "rendering": 1,
        "ready": 1,
        "failed": 0,
    }
    scenes = {scene["id"]: scene for scene in run["scenes"]}
    assert scenes["abertura"]["state"] == "ready"
    assert scenes["abertura"]["attempts"] == 1
    assert scenes["abertura"]["action_next"]
    assert scenes["explicacao"]["state"] == "rendering"
    assert scenes["explicacao"]["attempts"] == 0
    assert scenes["explicacao"]["action_next"] == (
        "generate, observe, and normalize scene"
    )
    assert scenes["explicacao"]["temporal_correction"]["status"] == "missing"
    assert scenes["explicacao"]["temporal_correction"]["error"]
