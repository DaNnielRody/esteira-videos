"""Public CLI behavior for defining, rendering, and inspecting videos."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from test_project_init import EmptySilenceDetector, FakeAudioProbe

from video_pipeline.cli import main


def test_init_creates_the_definitive_editable_video_project(tmp_path: Path) -> None:
    script = tmp_path / "roteiro.md"
    script_bytes = "# Abertura\nApresente vetores.\n".encode("utf-8")
    script.write_bytes(script_bytes)
    audio = tmp_path / "narracao.wav"
    audio_bytes = b"fake wav"
    audio.write_bytes(audio_bytes)
    probe = FakeAudioProbe(
        {
            "path": "audio/narration.wav",
            "hash": hashlib.sha256(audio_bytes).hexdigest(),
            "container": "wav",
            "codec": "pcm_s16le",
            "stream": 0,
            "sample_rate": 48_000,
            "channels": 1,
            "duration": 1.0,
            "size": len(audio_bytes),
            "probe_result": {"format": {}, "streams": []},
        }
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
        silence_detector=EmptySilenceDetector(),
    )

    assert exit_code == 0
    document = json.loads((project / "project.json").read_text(encoding="utf-8"))
    assert document["id"] == "2026_vetores"
    assert (project / "script.md").read_bytes() == script_bytes
    assert (project / "audio" / "narration.wav").read_bytes() == audio_bytes
