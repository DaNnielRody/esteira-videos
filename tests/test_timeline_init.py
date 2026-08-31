"""Public contract for explicit-timestamp timeline initialization."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from video_pipeline.cli import main
from video_pipeline.project import Project
from video_pipeline.scene_plan import ScenePlan
from video_pipeline.timeline import Timeline


class FakeAudioProbe:
    """Operation-specific fake for the staged narration probe boundary."""

    def __init__(self, facts: dict[str, object]) -> None:
        self.facts = facts
        self.calls: list[Path] = []

    def __call__(self, path: Path) -> dict[str, object]:
        self.calls.append(path)
        return dict(self.facts)


class ExplodingSilenceDetector:
    """The explicit timestamp path must never consult silence detection."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, path: Path) -> list[tuple[float, float]]:
        self.calls += 1
        raise AssertionError(f"silence detection was called for {path}")


def test_init_explicit_timestamps_create_confirmed_timeline_and_plans(
    tmp_path: Path,
) -> None:
    script = tmp_path / "roteiro.md"
    script_bytes = (
        "# Abertura\n"
        "@capabilities: basic_geometry, typography\n"
        "@start: 0\n"
        "@end: 4.000\n"
        "@objective: Mostre o vetor inicial.\n"
        "O vetor parte da origem.\n"
        "\n"
        "## Explicacao\n"
        "@capabilities: equations\n"
        "@start: 4.000\n"
        "@end: 10.000\n"
        "@objective: Explique a decomposição.\n"
        "Observe as componentes x e y.\n"
    ).encode("utf-8")
    script.write_bytes(script_bytes)

    audio = tmp_path / "narracao.wav"
    audio_bytes = b"deterministic fake wav bytes\x00"
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
    probe = FakeAudioProbe(facts)
    silence_detector = ExplodingSilenceDetector()
    project = tmp_path / "projects" / "2026_vetores"

    exit_code = main(
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
        audio_probe=probe,
        silence_detector=silence_detector,
    )

    assert exit_code == 0
    assert silence_detector.calls == 0
    assert probe.calls[0].name == "narration.wav"
    assert probe.calls[0].parent.name == "audio"

    project_document = json.loads((project / "project.json").read_text(encoding="utf-8"))
    assert project_document["status"] == "timeline_confirmed"
    assert project_document["timeline_path"] == "timeline.json"
    assert project_document["current_run"] is None
    assert project_document["accepted_run"] is None
    assert project_document["planning_state"] == "ready"
    assert project_document["render_state"] == "pending"
    assert project_document["composition_state"] == "pending"
    assert isinstance(project_document["theme"], dict)

    loaded_project = Project.model_validate_json(
        (project / "project.json").read_text(encoding="utf-8")
    )
    assert loaded_project.status == "timeline_confirmed"

    timeline = Timeline.model_validate_json(
        (project / "timeline.json").read_text(encoding="utf-8")
    )
    assert timeline.status == "confirmed"
    assert timeline.method == "explicit_timestamp"
    assert timeline.duration_seconds == 10.0
    assert [segment.order for segment in timeline.segments] == [1, 2]
    assert [segment.narration_text for segment in timeline.segments] == [
        "O vetor parte da origem.",
        "Observe as componentes x e y.",
    ]
    assert [(segment.start_seconds, segment.end_seconds) for segment in timeline.segments] == [
        (0.0, 4.0),
        (4.0, 10.0),
    ]
    assert [segment.target_duration_seconds for segment in timeline.segments] == [4.0, 6.0]
    assert all(
        segment.start_provenance == "explicit_timestamp"
        and segment.end_provenance == "explicit_timestamp"
        for segment in timeline.segments
    )

    assert [scene["id"] for scene in project_document["scenes"]] == [
        "abertura",
        "explicacao",
    ]
    persisted_capabilities: list[list[str]] = []
    for scene, segment in zip(project_document["scenes"], timeline.segments, strict=True):
        plan_path = Path(scene["plan_path"])
        assert not plan_path.is_absolute()
        assert ".." not in plan_path.parts
        assert scene["plan_path"] == segment.plan_path
        plan = ScenePlan.model_validate_json((project / plan_path).read_text(encoding="utf-8"))
        assert plan.narration_text == segment.narration_text
        assert plan.start_seconds == segment.start_seconds
        assert plan.end_seconds == segment.end_seconds
        assert plan.duration_seconds == segment.target_duration_seconds
        assert plan.objective == segment.objective
        assert plan.theme == loaded_project.theme
        persisted_capabilities.append(plan.capabilities)
    assert persisted_capabilities == [["basic_geometry", "typography"], ["equations"]]


@pytest.mark.parametrize(
    ("case", "expected_error"),
    [
        ("unknown_capability", "unknown visual capability"),
        ("unsupported_capability", "not supported"),
        ("partial", "every scene"),
    ],
)
def test_init_rejects_unknown_unsupported_or_partial_capabilities(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    case: str,
    expected_error: str,
) -> None:
    capability = {
        "unknown_capability": "unknown_capability",
        "unsupported_capability": "function_graphs",
        "partial": "basic_geometry",
    }[case]
    second_capabilities = "" if case == "partial" else f"@capabilities: {capability}\n"
    script = tmp_path / "roteiro.md"
    script.write_text(
        "# Abertura\n"
        f"@capabilities: {capability}\n"
        "@start: 0\n"
        "@end: 4.000\n"
        "@objective: Mostre o vetor inicial.\n"
        "O vetor parte da origem.\n"
        "\n"
        "## Explicacao\n"
        f"{second_capabilities}"
        "@start: 4.000\n"
        "@end: 10.000\n"
        "@objective: Explique a decomposição.\n"
        "Observe as componentes x e y.\n",
        encoding="utf-8",
    )
    audio = tmp_path / "narracao.wav"
    audio_bytes = b"deterministic fake wav bytes\x00"
    audio.write_bytes(audio_bytes)
    probe = FakeAudioProbe(
        {
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
    )
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
            audio_probe=probe,
            silence_detector=ExplodingSilenceDetector(),
        )
        == 1
    )
    assert expected_error in capsys.readouterr().out
    assert not project.exists()
