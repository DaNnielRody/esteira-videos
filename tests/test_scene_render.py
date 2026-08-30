"""Public contract for isolated canonical scene rendering."""

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
from video_pipeline.provider import ProviderRequest, ProviderResponse, UnloadResult
from video_pipeline.scene_plan import ScenePlan


class SelectableProvider:
    """Fail the second scene once, then succeed when it is selected."""

    def __init__(self, project_json: Path) -> None:
        self.project_json = project_json
        self.requests: list[ProviderRequest] = []
        self._failed_scene_two = False

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        if request.scene_name == "ExplicacaoScene" and not self._failed_scene_two:
            self._failed_scene_two = True
            raise RuntimeError("planned scene-two provider failure")
        document = json.loads(self.project_json.read_text(encoding="utf-8"))
        assert document["status"] == "rendering"
        if request.scene_name == "AberturaScene":
            code = (
                "from manim import Scene\n\nclass AberturaScene(Scene):\n"
                "    def construct(self):\n        pass\n"
            )
        else:
            code = (
                "from manim import Scene\n\nclass ExplicacaoScene(Scene):\n"
                "    def construct(self):\n        pass\n"
            )
        return ProviderResponse(code=code, raw_response={"fake": True})

    def unload(self) -> UnloadResult:
        return UnloadResult(ok=True, raw_response={"fake": True})


def _snapshot_tree(root: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_render_selected_failed_scene_reuses_ready_evidence_and_composes(
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
    provider = SelectableProvider(project_json)
    runner = FakeManimRunner()
    raw_validator = FakeRawValidator()
    observer = FakeObserver()
    normalized_validator = FakeNormalizedValidator()
    normalizer = FakeTemporalNormalizer(normalized_validator)
    final_validator = FakeFinalValidator()
    composer = FakeComposer()
    render_dependencies = {
        "provider": provider,
        "runner": runner,
        "validator": raw_validator,
        "observer": observer,
        "temporal_normalizer": normalizer,
        "normalized_validator": normalized_validator,
        "final_validator": final_validator,
        "composer": composer,
    }

    assert (
        main(
            ["render", str(project_json), "--max-attempts", "1"],
            id_factory=lambda: "run-001",
            **render_dependencies,
        )
        == 1
    )
    capsys.readouterr()

    project_document = json.loads(project_json.read_text(encoding="utf-8"))
    assert project_document["status"] == "failed"
    run_path = project / "artifacts" / "run-001"
    run_document = json.loads((run_path / "run.json").read_text(encoding="utf-8"))
    assert [scene["state"] for scene in run_document["scenes"]] == [
        "ready",
        "failed",
    ]
    first_scene = project_document["scenes"][0]
    first_scene_root = project / first_scene["path"]
    first_run_scene_root = run_path / first_scene["path"]
    stable_roots = (first_scene_root, first_run_scene_root)
    stable_tree = {
        root: _snapshot_tree(root)
        for root in stable_roots
    }
    stable_inputs = {
        project / "script.md": (project / "script.md").read_bytes(),
        project / "audio" / "narration.wav": (
            project / "audio" / "narration.wav"
        ).read_bytes(),
        project / "timeline.json": (project / "timeline.json").read_bytes(),
        project / first_scene["plan_path"]: (
            project / first_scene["plan_path"]
        ).read_bytes(),
    }
    request_count = len(provider.requests)
    runner_count = len(runner.scene_paths)
    normalizer_count = len(normalizer.calls)

    id_factory_calls = 0

    def no_new_run() -> str:
        nonlocal id_factory_calls
        id_factory_calls += 1
        raise AssertionError("isolated render must reuse the current failed run")

    assert (
        main(
            [
                "render",
                str(project_json),
                "--scene",
                "explicacao",
                "--max-attempts",
                "1",
            ],
            id_factory=no_new_run,
            **render_dependencies,
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "READY" in output
    assert id_factory_calls == 0

    ready_project = json.loads(project_json.read_text(encoding="utf-8"))
    assert ready_project["status"] == "ready"
    assert ready_project["current_run"] == "run-001"
    ready_run = json.loads((run_path / "run.json").read_text(encoding="utf-8"))
    assert [scene["state"] for scene in ready_run["scenes"]] == ["ready", "ready"]
    assert [scene["attempts"] for scene in ready_run["scenes"]] == [1, 2]
    assert len(provider.requests) == request_count + 1
    assert [request.scene_name for request in provider.requests] == [
        "AberturaScene",
        "ExplicacaoScene",
        "ExplicacaoScene",
    ]
    assert len(runner.scene_paths) == runner_count + 1
    assert len(normalizer.calls) == normalizer_count + 1
    assert composer.scene_paths == [
        run_path / "scenes" / "01_abertura" / "normalized.mp4",
        run_path / "scenes" / "02_explicacao" / "normalized.mp4",
    ]
    assert composer.narration_path == project / "audio" / "narration.wav"

    for root, original in stable_tree.items():
        assert _snapshot_tree(root) == original
    for path, original in stable_inputs.items():
        assert path.read_bytes() == original


def test_render_rejects_unknown_or_ambiguous_scene_before_mutation(
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
    provider = FakeProvider(project_json)
    runner = FakeManimRunner()
    raw_validator = FakeRawValidator()
    observer = FakeObserver()
    normalized_validator = FakeNormalizedValidator()
    normalizer = FakeTemporalNormalizer(normalized_validator)
    final_validator = FakeFinalValidator()
    composer = FakeComposer()
    dependencies = {
        "provider": provider,
        "runner": runner,
        "validator": raw_validator,
        "observer": observer,
        "temporal_normalizer": normalizer,
        "normalized_validator": normalized_validator,
        "final_validator": final_validator,
        "composer": composer,
    }
    id_factory_calls = 0

    def no_new_run() -> str:
        nonlocal id_factory_calls
        id_factory_calls += 1
        raise AssertionError("invalid scene selection must not allocate a run")

    before_unknown = _snapshot_tree(project)
    assert (
        main(
            ["render", str(project_json), "--scene", "does-not-exist"],
            id_factory=no_new_run,
            **dependencies,
        )
        == 1
    )
    assert "unknown scene" in capsys.readouterr().out.lower()
    assert _snapshot_tree(project) == before_unknown
    assert provider.requests == []
    assert runner.scene_paths == []
    assert normalizer.calls == []
    assert composer.validator_calls == 0
    assert id_factory_calls == 0

    project_document = json.loads(project_json.read_text(encoding="utf-8"))
    second_plan_path = project / project_document["scenes"][1]["plan_path"]
    second_plan = ScenePlan.model_validate_json(
        second_plan_path.read_text(encoding="utf-8")
    )
    second_plan_path.write_text(
        json.dumps(
            second_plan.model_copy(update={"scene_name": "AberturaScene"}).to_document(),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    before_ambiguous = _snapshot_tree(project)
    assert (
        main(
            ["render", str(project_json), "--scene", "AberturaScene"],
            id_factory=no_new_run,
            **dependencies,
        )
        == 1
    )
    assert "ambiguous scene" in capsys.readouterr().out.lower()
    assert _snapshot_tree(project) == before_ambiguous
    assert provider.requests == []
    assert runner.scene_paths == []
    assert normalizer.calls == []
    assert composer.validator_calls == 0
    assert id_factory_calls == 0
