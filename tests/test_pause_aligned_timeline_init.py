"""Public contract for pause-aligned candidate timeline initialization."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from video_pipeline.cli import main
from video_pipeline.project import Project
from video_pipeline.scene_plan import ScenePlan
from video_pipeline.timeline import PauseInterval, Timeline


class FakeAudioProbe:
    """Operation-specific fake for the staged narration probe boundary."""

    def __init__(self, facts: dict[str, object]) -> None:
        self.facts = facts
        self.calls: list[Path] = []

    def __call__(self, path: Path) -> dict[str, object]:
        self.calls.append(path)
        return dict(self.facts)


class FakeSilenceDetector:
    """Return deterministic pauses near the weighted word-count targets."""

    def __init__(self, pauses: tuple[PauseInterval, ...]) -> None:
        self.pauses = pauses
        self.calls: list[Path] = []

    def __call__(self, path: Path) -> tuple[PauseInterval, ...]:
        self.calls.append(path)
        return self.pauses


def test_init_headings_without_timestamps_creates_pause_aligned_candidate(
    tmp_path: Path,
) -> None:
    script = tmp_path / "roteiro.md"
    script_bytes = (
        "# Origem\n"
        "Vetor inicial.\n"
        "\n"
        "## Componentes\n"
        "Observe cada componente agora.\n"
        "\n"
        "## Resultado\n"
        "Compare os comprimentos finais agora juntos.\n"
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
        "duration": 12.0,
        "size": len(audio_bytes),
        "probe_result": {"format": {}, "streams": []},
    }
    probe = FakeAudioProbe(facts)
    silence_detector = FakeSilenceDetector(
        (
            PauseInterval(start_seconds=1.6, end_seconds=1.9),
            PauseInterval(start_seconds=2.1, end_seconds=2.4),
            PauseInterval(start_seconds=5.7, end_seconds=5.9),
            PauseInterval(start_seconds=6.1, end_seconds=6.3),
        )
    )
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
    assert len(probe.calls) == 1
    assert probe.calls[0].name == "narration.wav"
    assert probe.calls[0].parent.name == "audio"
    assert len(silence_detector.calls) == 1
    assert silence_detector.calls[0].name == "narration.wav"
    assert silence_detector.calls[0].parent.name == "audio"

    project_document = json.loads((project / "project.json").read_text(encoding="utf-8"))
    assert project_document["status"] == "timeline_candidate"
    assert project_document["timeline_path"] == "timeline.json"
    assert project_document["planning_state"] == "review_required"
    assert project_document["render_state"] == "pending"
    assert project_document["composition_state"] == "pending"
    assert project_document["current_run"] is None
    assert project_document["accepted_run"] is None
    assert isinstance(project_document["theme"], dict)

    loaded_project = Project.model_validate_json(
        (project / "project.json").read_text(encoding="utf-8")
    )
    assert loaded_project.status == "timeline_candidate"

    timeline = Timeline.model_validate_json(
        (project / "timeline.json").read_text(encoding="utf-8")
    )
    assert timeline.status == "candidate"
    assert timeline.method == "pause_aligned"
    assert timeline.duration_seconds == 12.0
    assert [segment.order for segment in timeline.segments] == [1, 2, 3]
    assert [segment.id for segment in timeline.segments] == [
        "origem",
        "componentes",
        "resultado",
    ]
    assert [segment.narration_text for segment in timeline.segments] == [
        "Vetor inicial.",
        "Observe cada componente agora.",
        "Compare os comprimentos finais agora juntos.",
    ]
    assert [
        (segment.start_seconds, segment.end_seconds) for segment in timeline.segments
    ] == [(0.0, 1.75), (1.75, 5.8), (5.8, 12.0)]
    assert [segment.target_duration_seconds for segment in timeline.segments] == [
        1.75,
        4.05,
        6.2,
    ]
    assert all(
        segment.start_provenance == "pause_aligned"
        and segment.end_provenance == "pause_aligned"
        for segment in timeline.segments
    )
    assert all(segment.end_seconds > segment.start_seconds for segment in timeline.segments)
    assert all(
        current.start_seconds == previous.end_seconds
        for previous, current in zip(timeline.segments, timeline.segments[1:], strict=False)
    )
    limitation = " ".join(timeline.warnings + timeline.manual_review_reasons).lower()
    assert "approx" in limitation
    assert "asr" in limitation
    assert "forced alignment" in limitation
    assert "unverified" in limitation or "correspondence" in limitation

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
