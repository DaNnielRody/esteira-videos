"""Public contract for resuming a failed canonical project render."""

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
    FakeRawValidator,
    FakeTemporalNormalizer,
)

from video_pipeline.cli import main
from video_pipeline.project import Project, _project_package_hashes
from video_pipeline.provider import ProviderRequest, ProviderResponse, UnloadResult


class ResumableProvider:
    """Fail scene two once, then return accepted code on its retry."""

    def __init__(self, project_json: Path) -> None:
        self.project_json = project_json
        self.requests: list[ProviderRequest] = []
        self._failed_scene_two = False

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        if request.scene_name == "ExplicacaoScene" and not self._failed_scene_two:
            self._failed_scene_two = True
            raise RuntimeError("planned scene-two provider failure")
        code = (
            "from manim import Scene\n\nclass AberturaScene(Scene):\n"
            "    def construct(self):\n        pass\n"
            if request.scene_name == "AberturaScene"
            else "from manim import Scene\n\nclass ExplicacaoScene(Scene):\n"
            "    def construct(self):\n        pass\n"
        )
        document = json.loads(self.project_json.read_text(encoding="utf-8"))
        assert document["status"] == "rendering"
        return ProviderResponse(code=code, raw_response={"fake": True})

    def unload(self) -> UnloadResult:
        return UnloadResult(ok=True, raw_response={"fake": True})


def test_render_resumes_failed_run_and_reuses_ready_scenes(
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

    provider = ResumableProvider(project_json)
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
        == 1
    )
    assert "ERROR" in capsys.readouterr().out

    project_document = json.loads(project_json.read_text(encoding="utf-8"))
    assert project_document["status"] == "failed"
    assert project_document["current_run"] == "run-001"
    run_path = project / "artifacts" / "run-001"
    run_json = run_path / "run.json"
    failed_run = json.loads(run_json.read_text(encoding="utf-8"))
    assert failed_run["state"] == "failed"
    assert [scene["state"] for scene in failed_run["scenes"]] == [
        "ready",
        "failed",
    ]
    assert [scene["attempts"] for scene in failed_run["scenes"]] == [1, 1]
    assert len(failed_run["scenes"][0]["attempt_history"]) == 1
    assert len(failed_run["scenes"][1]["attempt_history"]) == 1
    assert failed_run["scenes"][1]["action_next"]
    assert failed_run["input_hashes"]["script_sha256"] == hashlib.sha256(
        (project / "script.md").read_bytes()
    ).hexdigest()
    assert failed_run["input_hashes"]["audio_sha256"] == hashlib.sha256(
        (project / "audio" / "narration.wav").read_bytes()
    ).hexdigest()
    assert failed_run["input_hashes"]["timeline_sha256"] == hashlib.sha256(
        (project / "timeline.json").read_bytes()
    ).hexdigest()
    project_model = Project.model_validate_json(project_json.read_text(encoding="utf-8"))
    assert failed_run["package_hashes"] == _project_package_hashes(project, project_model)
    assert list(failed_run["package_hashes"]) == sorted(failed_run["package_hashes"])

    scene_one_ref = project_document["scenes"][0]
    scene_one_root = run_path / scene_one_ref["path"]
    stable_paths = (
        scene_one_root / "scene.py",
        scene_one_root / "code-provenance.json",
        run_path / scene_one_ref["path"] / "raw.mp4",
        run_path / scene_one_ref["path"] / "normalized.mp4",
        run_path / scene_one_ref["path"] / "normalization.json",
    )
    stable_bytes = {path: path.read_bytes() for path in stable_paths}
    request_count_before_resume = len(provider.requests)
    runner_count_before_resume = len(runner.scene_paths)
    normalizer_count_before_resume = len(normalizer.calls)
    composer_count_before_resume = composer.validator_calls

    id_factory_calls = 0

    def no_new_run() -> str:
        nonlocal id_factory_calls
        id_factory_calls += 1
        raise AssertionError("resume must not allocate a new run")

    changed_plan_path = project / failed_run["scenes"][1]["plan_path"]
    original_plan_bytes = changed_plan_path.read_bytes()
    project_before_hash_failure = project_json.read_bytes()
    run_before_hash_failure = run_json.read_bytes()
    changed_plan_path.write_bytes(original_plan_bytes + b"\n")
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
            id_factory=no_new_run,
        )
        == 1
    )
    hash_failure_output = capsys.readouterr().out
    assert "hash" in hash_failure_output.lower()
    assert project_json.read_bytes() == project_before_hash_failure
    assert run_json.read_bytes() == run_before_hash_failure
    assert len(provider.requests) == request_count_before_resume
    assert len(runner.scene_paths) == runner_count_before_resume
    assert len(normalizer.calls) == normalizer_count_before_resume
    assert composer.validator_calls == composer_count_before_resume
    changed_plan_path.write_bytes(original_plan_bytes)

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
            id_factory=no_new_run,
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "READY" in output
    assert id_factory_calls == 0

    ready_project = json.loads(project_json.read_text(encoding="utf-8"))
    assert ready_project["status"] == "ready"
    assert ready_project["current_run"] == "run-001"
    assert ready_project["accepted_run"] is None
    ready_run = json.loads(run_json.read_text(encoding="utf-8"))
    assert ready_run["state"] == "ready"
    final_path = run_path / "final.mp4"
    assert ready_run["final_sha256"] == hashlib.sha256(
        final_path.read_bytes()
    ).hexdigest()
    assert ready_run["final_size_bytes"] == final_path.stat().st_size
    assert [scene["state"] for scene in ready_run["scenes"]] == ["ready", "ready"]
    assert [scene["attempts"] for scene in ready_run["scenes"]] == [1, 2]
    assert [len(scene["attempt_history"]) for scene in ready_run["scenes"]] == [1, 2]
    assert ready_run["scenes"][1]["attempt_history"][0]["state"] == "failed"
    assert ready_run["scenes"][1]["attempt_history"][1]["state"] == "ready"

    assert [request.scene_name for request in provider.requests] == [
        "AberturaScene",
        "ExplicacaoScene",
        "ExplicacaoScene",
    ]
    assert len(provider.requests) == request_count_before_resume + 1
    assert len(runner.scene_paths) == runner_count_before_resume + 1
    assert runner.scene_paths[-1].name == "scene.py"
    assert len(normalizer.calls) == normalizer_count_before_resume + 1
    assert composer.validator_calls == composer_count_before_resume + 1
    assert [path.read_bytes() for path in composer.scene_paths] == [
        b"normalized:raw:scene",
        b"normalized:raw:scene",
    ]
    assert composer.scene_paths == [
        run_path / "scenes" / "01_abertura" / "normalized.mp4",
        run_path / "scenes" / "02_explicacao" / "normalized.mp4",
    ]

    for path, original in stable_bytes.items():
        assert path.read_bytes() == original
