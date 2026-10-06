"""Public durability contracts for whole-project render lifecycle state."""

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

import video_pipeline.video as video_module
from video_pipeline.cli import main
from video_pipeline.provider import ProviderRequest, ProviderResponse


def _initialize_confirmed_project(tmp_path: Path) -> tuple[Path, Path]:
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
    project = tmp_path / "projects" / "2026_lifecycle"
    assert (
        main(
            [
                "init",
                str(project),
                "--title",
                "Lifecycle",
                "--script",
                str(script),
                "--audio",
                str(audio),
            ],
            audio_probe=FakeAudioProbe(facts),
        )
        == 0
    )
    return project, project / "project.json"


def _initialize_three_scene_project(tmp_path: Path) -> tuple[Path, Path]:
    script = tmp_path / "roteiro.md"
    script.write_text(
        "# Abertura\n"
        "@start: 0\n"
        "@end: 4\n"
        "@objective: Introduza vetores.\n"
        "Esta e a abertura exata.\n\n"
        "## Explicacao\n"
        "@start: 4\n"
        "@end: 7\n"
        "@objective: Explique a soma.\n"
        "Esta e a explicacao exata.\n\n"
        "## Conclusao\n"
        "@start: 7\n"
        "@end: 10\n"
        "@objective: Feche a ideia.\n"
        "Esta e a conclusao exata.\n",
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
    project = tmp_path / "projects" / "2026_three_lifecycle"
    assert (
        main(
            [
                "init",
                str(project),
                "--title",
                "Three lifecycle",
                "--script",
                str(script),
                "--audio",
                str(audio),
            ],
            audio_probe=FakeAudioProbe(facts),
        )
        == 0
    )
    return project, project / "project.json"
def _render_dependencies(
    provider: object,
    *,
    runner: object | None = None,
) -> dict[str, object]:
    normalized_validator = FakeNormalizedValidator()
    return {
        "provider": provider,
        "runner": runner or FakeManimRunner(),
        "validator": FakeRawValidator(),
        "observer": FakeObserver(),
        "temporal_normalizer": FakeTemporalNormalizer(normalized_validator),
        "normalized_validator": normalized_validator,
        "final_validator": FakeFinalValidator(),
        "composer": FakeComposer(),
    }


class DurableBoundaryProvider(FakeProvider):
    """Observe the durable scene lifecycle at each provider boundary."""

    _scene_ids = {"AberturaScene": "abertura", "ExplicacaoScene": "explicacao"}

    def __init__(self, project_json: Path) -> None:
        super().__init__(project_json)
        self.boundary_snapshots: list[dict[str, object]] = []

    def _assert_boundary(self, request: ProviderRequest) -> None:
        scene_id = self._scene_ids[request.scene_name]
        project_document = json.loads(self.project_json.read_text(encoding="utf-8"))
        run_id = project_document["current_run"]
        run_document = json.loads(
            (
                self.project_json.parent
                / "artifacts"
                / str(run_id)
                / "run.json"
            ).read_text(encoding="utf-8")
        )
        record = next(
            item for item in run_document["scenes"] if item["id"] == scene_id
        )
        assert project_document["status"] == "rendering"
        assert project_document["current_scene"] == scene_id
        assert run_document["state"] == "rendering"
        assert run_document["current_scene"] == scene_id
        assert record["state"] == "rendering"
        assert record["action_next"] == "generate, observe, and normalize scene"
        self.boundary_snapshots.append(
            {
                "scene": scene_id,
                "project_current_scene": project_document["current_scene"],
                "run_current_scene": run_document["current_scene"],
                "scene_state": record["state"],
                "action_next": record["action_next"],
            }
        )

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self._assert_boundary(request)
        return super().generate(request)


class FailingDurableBoundaryProvider(DurableBoundaryProvider):
    """Fail the second scene after observing its durable boundary state."""

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self._assert_boundary(request)
        if request.scene_name == "ExplicacaoScene":
            raise RuntimeError("planned durable scene failure")
        return FakeProvider.generate(self, request)


class InterruptingDurableBoundaryProvider(DurableBoundaryProvider):
    """Interrupt once at scene two, then complete it on the resumed run."""

    def __init__(self, project_json: Path) -> None:
        super().__init__(project_json)
        self.interrupt_scene_two = True

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self._assert_boundary(request)
        if request.scene_name == "ExplicacaoScene" and self.interrupt_scene_two:
            self.interrupt_scene_two = False
            raise KeyboardInterrupt("deliberate scene-two interruption")
        return FakeProvider.generate(self, request)


class ThreeSceneProvider(FakeProvider):
    """Generate three deterministic scene candidates and verify boundaries."""

    _scene_ids = {
        "AberturaScene": "abertura",
        "ExplicacaoScene": "explicacao",
        "ConclusaoScene": "conclusao",
    }

    def __init__(self, project_json: Path) -> None:
        super().__init__(project_json)
        self.codes = iter(
            tuple(
                (
                    f"from manim import Scene\n\nclass {scene_name}(Scene):\n"
                    "    def construct(self):\n        pass\n"
                )
                for scene_name in self._scene_ids
            )
        )

    def _assert_boundary(self, request: ProviderRequest) -> None:
        scene_id = self._scene_ids[request.scene_name]
        project_document = json.loads(self.project_json.read_text(encoding="utf-8"))
        run_id = project_document["current_run"]
        run_document = json.loads(
            (
                self.project_json.parent
                / "artifacts"
                / str(run_id)
                / "run.json"
            ).read_text(encoding="utf-8")
        )
        record = next(
            item for item in run_document["scenes"] if item["id"] == scene_id
        )
        assert project_document["status"] == "rendering"
        assert project_document["current_scene"] == scene_id
        assert run_document["state"] == "rendering"
        assert run_document["current_scene"] == scene_id
        assert record["state"] == "rendering"

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self._assert_boundary(request)
        return FakeProvider.generate(self, request)


class ThreeSceneInterruptingProvider(ThreeSceneProvider):
    """Interrupt one selected scene once, then generate it on resume."""

    def __init__(self, project_json: Path, interrupt_scene: str) -> None:
        super().__init__(project_json)
        self.interrupt_scene = interrupt_scene
        self.interrupt_once = True

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self._assert_boundary(request)
        if request.scene_name == self.interrupt_scene and self.interrupt_once:
            self.interrupt_once = False
            self.requests.append(request)
            raise KeyboardInterrupt(f"interrupt {self.interrupt_scene}")
        return FakeProvider.generate(self, request)


class InterruptingComposer(FakeComposer):
    """Finish one fake composition and interrupt before the result is recorded."""

    def __init__(self) -> None:
        super().__init__()
        self.interrupt_once = True

    def compose(self, *args: object, **kwargs: object) -> object:
        result = super().compose(*args, **kwargs)
        if self.interrupt_once:
            self.interrupt_once = False
            raise KeyboardInterrupt("deliberate composition interruption")
        return result


def test_new_run_initial_publication_rolls_back_before_rendering(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, project_json = _initialize_confirmed_project(tmp_path)
    original_project = project_json.read_bytes()
    provider = FakeProvider(project_json)
    runner = FakeManimRunner()
    original_replace = Path.replace
    replace_count = 0
    injected = False

    def fail_second_replace(self: Path, target: str | Path) -> Path:
        nonlocal injected, replace_count
        replace_count += 1
        if replace_count == 2:
            injected = True
            raise OSError("injected initial publication failure")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_second_replace)
    dependencies = _render_dependencies(provider, runner=runner)

    exit_code = main(
        ["render", str(project_json), "--max-attempts", "1"],
        id_factory=lambda: "run-001",
        **dependencies,
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "injected initial publication failure" in output
    assert injected
    assert replace_count >= 2
    assert provider.requests == []
    assert runner.scene_paths == []
    assert project_json.read_bytes() == original_project
    run_path = project / "artifacts" / "run-001"
    assert not run_path.exists()
    artifacts = project / "artifacts"
    if artifacts.exists():
        assert not list(artifacts.glob(".run-001*"))


def test_render_persists_current_scene_at_provider_boundary_and_clears_on_ready(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project, project_json = _initialize_confirmed_project(tmp_path)
    provider = DurableBoundaryProvider(project_json)

    assert (
        main(
            ["render", str(project_json), "--max-attempts", "1"],
            id_factory=lambda: "run-001",
            **_render_dependencies(provider),
        )
        == 0
    )
    capsys.readouterr()

    project_document = json.loads(project_json.read_text(encoding="utf-8"))
    run_document = json.loads(
        (project / "artifacts" / "run-001" / "run.json").read_text(encoding="utf-8")
    )
    assert [item["scene"] for item in provider.boundary_snapshots] == [
        "abertura",
        "explicacao",
    ]
    assert project_document["status"] == "ready"
    assert project_document["current_scene"] is None
    assert run_document["state"] == "ready"
    assert run_document["current_scene"] is None
    assert run_document["state_history"] == ["rendering", "composing", "ready"]


def test_render_failure_retains_failing_current_scene_and_action(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project, project_json = _initialize_confirmed_project(tmp_path)
    provider = FailingDurableBoundaryProvider(project_json)

    assert (
        main(
            ["render", str(project_json), "--max-attempts", "1"],
            id_factory=lambda: "run-001",
            **_render_dependencies(provider),
        )
        == 1
    )
    output = capsys.readouterr().out
    assert "planned durable scene failure" in output

    project_document = json.loads(project_json.read_text(encoding="utf-8"))
    run_document = json.loads(
        (project / "artifacts" / "run-001" / "run.json").read_text(encoding="utf-8")
    )
    failed_scene = next(
        item for item in run_document["scenes"] if item["id"] == "explicacao"
    )
    assert project_document["status"] == "failed"
    assert project_document["current_scene"] == "explicacao"
    assert run_document["state"] == "failed"
    assert run_document["current_scene"] == "explicacao"
    assert [item["state"] for item in run_document["scenes"]] == [
        "ready",
        "failed",
    ]
    assert failed_scene["action_next"] == "inspect diagnostics and retry this scene"


def test_render_resumes_interrupted_run_without_colliding_inner_scene_run(
    tmp_path: Path,
) -> None:
    project, project_json = _initialize_confirmed_project(tmp_path)
    provider = InterruptingDurableBoundaryProvider(project_json)
    runner = FakeManimRunner()
    dependencies = _render_dependencies(provider, runner=runner)

    with pytest.raises(KeyboardInterrupt, match="deliberate scene-two interruption"):
        main(
            ["render", str(project_json), "--max-attempts", "1"],
            id_factory=lambda: "run-001",
            **dependencies,
        )

    run_path = project / "artifacts" / "run-001"
    project_after_interrupt = json.loads(project_json.read_text(encoding="utf-8"))
    run_after_interrupt = json.loads(
        (run_path / "run.json").read_text(encoding="utf-8")
    )
    assert project_after_interrupt["status"] == "rendering"
    assert project_after_interrupt["current_run"] == "run-001"
    assert project_after_interrupt["current_scene"] == "explicacao"
    assert run_after_interrupt["state"] == "rendering"
    assert run_after_interrupt["current_scene"] == "explicacao"
    assert project_after_interrupt["current_scene"] == run_after_interrupt["current_scene"]
    assert [item["state"] for item in run_after_interrupt["scenes"]] == [
        "ready",
        "rendering",
    ]

    scene_one_root = run_path / "scenes" / "01_abertura"
    stable_scene_one = {
        path: path.read_bytes()
        for path in (
            scene_one_root / "scene.py",
            scene_one_root / "code-provenance.json",
            scene_one_root / "raw.mp4",
            scene_one_root / "normalized.mp4",
            scene_one_root / "normalization.json",
        )
    }
    interrupted_inner_run = run_path / "pipeline" / "explicacao" / "run-001-02"
    assert interrupted_inner_run.is_dir()
    interrupted_run_json = interrupted_inner_run / "run.json"
    interrupted_run_bytes = interrupted_run_json.read_bytes()
    assert (interrupted_inner_run / "attempt-01" / "request.json").is_file()
    assert len(provider.requests) == 1

    def no_new_run() -> str:
        raise AssertionError("resumed rendering run must not allocate another run")

    assert (
        main(
            ["render", str(project_json), "--max-attempts", "1"],
            id_factory=no_new_run,
            **dependencies,
        )
        == 0
    )

    project_after_resume = json.loads(project_json.read_text(encoding="utf-8"))
    run_after_resume = json.loads(
        (run_path / "run.json").read_text(encoding="utf-8")
    )
    assert project_after_resume["status"] == "ready"
    assert project_after_resume["current_run"] == "run-001"
    assert project_after_resume["current_scene"] is None
    assert run_after_resume["state"] == "ready"
    assert run_after_resume["current_scene"] is None
    assert [item["state"] for item in run_after_resume["scenes"]] == [
        "ready",
        "ready",
    ]
    assert [request.scene_name for request in provider.requests] == [
        "AberturaScene",
        "ExplicacaoScene",
    ]
    assert len(runner.scene_paths) == 2
    resumed_inner_runs = sorted(
        path
        for path in (run_path / "pipeline" / "explicacao").iterdir()
        if path.is_dir()
    )
    assert interrupted_inner_run in resumed_inner_runs
    assert len(resumed_inner_runs) == 2
    assert any(path != interrupted_inner_run for path in resumed_inner_runs)
    assert interrupted_run_json.read_bytes() == interrupted_run_bytes
    for path, original in stable_scene_one.items():
        assert path.read_bytes() == original


@pytest.mark.parametrize(
    ("interrupt_scene", "expected_states"),
    (
        ("AberturaScene", ["rendering", "queued", "queued"]),
        ("ExplicacaoScene", ["ready", "rendering", "queued"]),
    ),
)
def test_three_scene_interruptions_resume_same_run_and_preserve_queue(
    tmp_path: Path,
    interrupt_scene: str,
    expected_states: list[str],
) -> None:
    project, project_json = _initialize_three_scene_project(tmp_path)
    provider = ThreeSceneInterruptingProvider(project_json, interrupt_scene)
    runner = FakeManimRunner()
    dependencies = _render_dependencies(provider, runner=runner)

    with pytest.raises(KeyboardInterrupt, match=f"interrupt {interrupt_scene}"):
        main(
            ["render", str(project_json), "--max-attempts", "1"],
            id_factory=lambda: "run-001",
            **dependencies,
        )

    run_path = project / "artifacts" / "run-001"
    project_after_interrupt = json.loads(project_json.read_text(encoding="utf-8"))
    run_after_interrupt = json.loads((run_path / "run.json").read_text(encoding="utf-8"))
    assert project_after_interrupt["status"] == "rendering"
    assert project_after_interrupt["current_run"] == "run-001"
    assert project_after_interrupt["current_scene"] == interrupt_scene.removesuffix("Scene").lower()
    assert run_after_interrupt["state"] == "rendering"
    assert run_after_interrupt["current_scene"] == project_after_interrupt["current_scene"]
    assert [record["state"] for record in run_after_interrupt["scenes"]] == expected_states

    ready_scene_bytes = {
        record["id"]: {
            path: path.read_bytes()
            for path in (
                run_path
                / "scenes"
                / f"{record['order']:02d}_{record['id']}"
                / "scene.py",
                run_path
                / "scenes"
                / f"{record['order']:02d}_{record['id']}"
                / "code-provenance.json",
                run_path
                / "scenes"
                / f"{record['order']:02d}_{record['id']}"
                / "raw.mp4",
                run_path
                / "scenes"
                / f"{record['order']:02d}_{record['id']}"
                / "normalized.mp4",
                run_path
                / "scenes"
                / f"{record['order']:02d}_{record['id']}"
                / "normalization.json",
            )
        }
        for record in run_after_interrupt["scenes"]
        if record["state"] == "ready"
    }
    interrupted_inner_run = run_path / "pipeline" / interrupt_scene.removesuffix("Scene").lower()
    interrupted_inner_run = interrupted_inner_run / (
        "run-001-01" if interrupt_scene == "AberturaScene" else "run-001-02"
    )
    interrupted_run_bytes = (interrupted_inner_run / "run.json").read_bytes()
    assert (interrupted_inner_run / "attempt-01" / "request.json").is_file()

    def no_new_run() -> str:
        raise AssertionError("resumed rendering run must not allocate another run")

    assert (
        main(
            ["render", str(project_json), "--max-attempts", "1"],
            id_factory=no_new_run,
            **dependencies,
        )
        == 0
    )

    project_after_resume = json.loads(project_json.read_text(encoding="utf-8"))
    run_after_resume = json.loads((run_path / "run.json").read_text(encoding="utf-8"))
    assert project_after_resume["status"] == "ready"
    assert project_after_resume["current_run"] == "run-001"
    assert project_after_resume["current_scene"] is None
    assert run_after_resume["state"] == "ready"
    assert run_after_resume["current_scene"] is None
    assert [record["state"] for record in run_after_resume["scenes"]] == [
        "ready",
        "ready",
        "ready",
    ]
    expected_requests = [
        "AberturaScene",
        "ExplicacaoScene",
        "ConclusaoScene",
    ]
    interrupted_index = expected_requests.index(interrupt_scene)
    assert [request.scene_name for request in provider.requests] == (
        expected_requests[:interrupted_index]
        + [interrupt_scene]
        + expected_requests[interrupted_index:]
    )
    assert len(runner.scene_paths) == 3
    assert (interrupted_inner_run / "run.json").read_bytes() == interrupted_run_bytes
    interrupted_scene_root = run_path / "pipeline" / interrupt_scene.removesuffix("Scene").lower()
    assert any(
        path != interrupted_inner_run
        for path in interrupted_scene_root.iterdir()
        if path.is_dir()
    )
    for _scene_id, paths in ready_scene_bytes.items():
        for path, original in paths.items():
            assert path.read_bytes() == original


def test_render_resumes_from_idle_scene_ready_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, project_json = _initialize_three_scene_project(tmp_path)
    provider = ThreeSceneProvider(project_json)
    dependencies = _render_dependencies(provider)
    original_atomic = video_module._atomic_update_json_documents
    interrupted = False

    def persist_then_interrupt(payloads: object) -> None:
        nonlocal interrupted
        documents = list(payloads)  # type: ignore[arg-type]
        run_document = next(
            document
            for path, document in documents
            if Path(path).name == "run.json"
        )
        records = run_document.get("scenes")
        if (
            not interrupted
            and run_document.get("state") == "rendering"
            and run_document.get("current_scene") is None
            and isinstance(records, list)
            and any(record.get("state") == "ready" for record in records)
            and any(record.get("state") == "queued" for record in records)
        ):
            original_atomic(documents)
            interrupted = True
            raise KeyboardInterrupt("idle scene checkpoint interruption")
        original_atomic(documents)

    monkeypatch.setattr(video_module, "_atomic_update_json_documents", persist_then_interrupt)
    with pytest.raises(KeyboardInterrupt, match="idle scene checkpoint interruption"):
        main(
            ["render", str(project_json), "--max-attempts", "1"],
            id_factory=lambda: "run-001",
            **dependencies,
        )
    assert interrupted

    run_path = project / "artifacts" / "run-001"
    project_after_interrupt = json.loads(project_json.read_text(encoding="utf-8"))
    run_after_interrupt = json.loads((run_path / "run.json").read_text(encoding="utf-8"))
    assert project_after_interrupt["status"] == "rendering"
    assert project_after_interrupt["current_scene"] is None
    assert run_after_interrupt["state"] == "rendering"
    assert run_after_interrupt["current_scene"] is None
    assert not any(record["state"] == "rendering" for record in run_after_interrupt["scenes"])
    assert [record["state"] for record in run_after_interrupt["scenes"]] == [
        "ready",
        "queued",
        "queued",
    ]

    def no_new_run() -> str:
        raise AssertionError("idle checkpoint resume must not allocate another run")

    assert (
        main(
            ["render", str(project_json), "--max-attempts", "1"],
            id_factory=no_new_run,
            **dependencies,
        )
        == 0
    )
    assert [request.scene_name for request in provider.requests] == [
        "AberturaScene",
        "ExplicacaoScene",
        "ConclusaoScene",
    ]


def test_outer_scene_publication_failure_marks_record_failed_and_resumes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project, project_json = _initialize_confirmed_project(tmp_path)
    provider = FakeProvider(project_json)
    runner = FakeManimRunner()
    dependencies = _render_dependencies(provider, runner=runner)
    original_copy = video_module._copy_file_atomic
    injected = False

    def fail_outer_copy(source: Path, destination: Path) -> None:
        nonlocal injected
        if not injected and destination.name == "raw.mp4":
            injected = True
            raise OSError("injected outer scene publication failure")
        original_copy(source, destination)

    monkeypatch.setattr(video_module, "_copy_file_atomic", fail_outer_copy)
    assert (
        main(
            ["render", str(project_json), "--max-attempts", "1"],
            id_factory=lambda: "run-001",
            **dependencies,
        )
        == 1
    )
    assert "injected outer scene publication failure" in capsys.readouterr().out
    assert injected

    run_path = project / "artifacts" / "run-001"
    run_document = json.loads((run_path / "run.json").read_text(encoding="utf-8"))
    assert run_document["state"] == "failed"
    assert run_document["current_scene"] == "abertura"
    assert [record["state"] for record in run_document["scenes"]] == [
        "failed",
        "queued",
    ]
    failed_inner_run = run_path / "pipeline" / "abertura" / "run-001-01"
    failed_inner_bytes = (failed_inner_run / "run.json").read_bytes()
    assert (failed_inner_run / "attempt-01" / "request.json").is_file()

    provider.codes = iter(
        (
            "from manim import Scene\n\nclass AberturaScene(Scene):\n"
            "    def construct(self):\n        pass\n",
            "from manim import Scene\n\nclass ExplicacaoScene(Scene):\n"
            "    def construct(self):\n        pass\n",
        )
    )
    monkeypatch.setattr(video_module, "_copy_file_atomic", original_copy)

    def no_new_run() -> str:
        raise AssertionError("failed run resume must not allocate another run")

    assert (
        main(
            ["render", str(project_json), "--max-attempts", "1"],
            id_factory=no_new_run,
            **dependencies,
        )
        == 0
    )
    assert (failed_inner_run / "run.json").read_bytes() == failed_inner_bytes
    assert len(
        [path for path in (run_path / "pipeline" / "abertura").iterdir() if path.is_dir()]
    ) == 2
    assert json.loads((run_path / "run.json").read_text(encoding="utf-8"))["state"] == "ready"


def test_composer_interruption_resumes_same_run_without_scene_work(
    tmp_path: Path,
) -> None:
    project, project_json = _initialize_confirmed_project(tmp_path)
    provider = FakeProvider(project_json)
    runner = FakeManimRunner()
    composer = InterruptingComposer()
    dependencies = _render_dependencies(provider, runner=runner)
    dependencies["composer"] = composer

    with pytest.raises(KeyboardInterrupt, match="deliberate composition interruption"):
        main(
            ["render", str(project_json), "--max-attempts", "1"],
            id_factory=lambda: "run-001",
            **dependencies,
        )

    run_path = project / "artifacts" / "run-001"
    project_after_interrupt = json.loads(project_json.read_text(encoding="utf-8"))
    run_after_interrupt = json.loads((run_path / "run.json").read_text(encoding="utf-8"))
    assert project_after_interrupt["status"] == "rendering"
    assert project_after_interrupt["current_scene"] is None
    assert run_after_interrupt["state"] == "composing"
    assert run_after_interrupt["current_scene"] is None
    assert [record["state"] for record in run_after_interrupt["scenes"]] == [
        "ready",
        "ready",
    ]
    scene_bytes = {
        path: path.read_bytes()
        for path in (
            run_path / "scenes" / "01_abertura" / "scene.py",
            run_path / "scenes" / "02_explicacao" / "scene.py",
        )
    }
    provider_calls = len(provider.requests)
    runner_calls = len(runner.scene_paths)

    def no_new_run() -> str:
        raise AssertionError("composer resume must not allocate another run")

    assert (
        main(
            ["render", str(project_json), "--max-attempts", "1"],
            id_factory=no_new_run,
            **dependencies,
        )
        == 0
    )
    assert len(provider.requests) == provider_calls
    assert len(runner.scene_paths) == runner_calls
    assert all(path.read_bytes() == original for path, original in scene_bytes.items())
    assert json.loads((run_path / "run.json").read_text(encoding="utf-8"))["state"] == "ready"
