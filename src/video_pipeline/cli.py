"""Command-line interface for defining, rendering, and reviewing videos."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Sequence
from pathlib import Path

from video_pipeline.golden import accept_project
from video_pipeline.observation import SceneObserver
from video_pipeline.project import (
    AudioProbe,
    SilenceDetector,
    confirm_project_timeline,
    initialize_project,
    inspect_project,
    validate_project_timeline,
)
from video_pipeline.provider import LLMProvider
from video_pipeline.rendering import ManimRunner
from video_pipeline.temporal import TemporalValidator
from video_pipeline.timeline import SilenceSubprocessRun
from video_pipeline.validation import RenderValidator
from video_pipeline.video import (
    FinalCompositionValidator,
    TemporalNormalizer,
    VideoComposer,
    VideoPipeline,
)


def main(
    argv: Sequence[str] | None = None,
    *,
    provider: LLMProvider | None = None,
    runner: ManimRunner | None = None,
    validator: RenderValidator | None = None,
    composer: VideoComposer | None = None,
    id_factory: Callable[[], str] | None = None,
    audio_probe: AudioProbe | None = None,
    silence_detector: SilenceDetector | None = None,
    silence_subprocess_run: SilenceSubprocessRun | None = None,
    observer: SceneObserver | None = None,
    temporal_normalizer: TemporalNormalizer | None = None,
    normalized_validator: TemporalValidator | None = None,
    final_validator: FinalCompositionValidator | None = None,
) -> int:
    """Execute one definitive product command."""

    parser = _build_parser()
    options = parser.parse_args(argv)
    try:
        if options.command == "init":
            return _init_video(
                options,
                audio_probe=audio_probe,
                silence_detector=silence_detector,
                silence_subprocess_run=silence_subprocess_run,
            )
        if options.command == "timeline":
            if options.timeline_command == "validate":
                return _validate_timeline(options.project)
            if options.timeline_command == "confirm":
                return _confirm_timeline(options.project)
            parser.error("a timeline command is required")
        if options.command == "inspect":
            return _inspect_project(options.project)
        if options.command == "accept":
            return _accept_project(options.project, options.run)
        if options.command == "render":
            return _render_video(
                options,
                provider=provider,
                runner=runner,
                validator=validator,
                observer=observer,
                temporal_normalizer=temporal_normalizer,
                normalized_validator=normalized_validator,
                final_validator=final_validator,
                composer=composer,
                id_factory=id_factory,
            )
        if options.command == "web":
            return _serve_web(options)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1
    parser.error("a command is required")
    return 2


def _init_video(
    options: argparse.Namespace,
    *,
    audio_probe: AudioProbe | None = None,
    silence_detector: SilenceDetector | None = None,
    silence_subprocess_run: SilenceSubprocessRun | None = None,
) -> int:
    initialize_project(
        options.video,
        title=options.title,
        script=options.script,
        audio=options.audio,
        audio_probe=audio_probe,
        silence_detector=silence_detector,
        silence_subprocess_run=silence_subprocess_run,
    )
    print(f"PROJECT: {Path(options.video).resolve()}")
    return 0


def _validate_timeline(project_path: Path) -> int:
    _, timeline = validate_project_timeline(project_path)
    print(f"TIMELINE: {timeline.status.upper()}")
    print(f"METHOD: {timeline.method}")
    if timeline.status == "candidate":
        print("REVIEW: MANUAL REVIEW REQUIRED")
        for reason in timeline.manual_review_reasons:
            print(f"REVIEW_REASON: {reason}")
    return 0


def _confirm_timeline(project_path: Path) -> int:
    _, timeline = confirm_project_timeline(project_path)
    print(f"TIMELINE: {timeline.status.upper()}")
    return 0


def _render_video(
    options: argparse.Namespace,
    *,
    provider: LLMProvider | None = None,
    runner: ManimRunner | None = None,
    validator: RenderValidator | None = None,
    observer: SceneObserver | None = None,
    temporal_normalizer: TemporalNormalizer | None = None,
    normalized_validator: TemporalValidator | None = None,
    final_validator: FinalCompositionValidator | None = None,
    composer: VideoComposer | None = None,
    id_factory: Callable[[], str] | None = None,
) -> int:
    """Render one confirmed canonical project through one orchestration path."""

    pipeline = VideoPipeline(
        provider=provider,
        runner=runner,
        validator=validator,
        observer=observer,
        temporal_normalizer=temporal_normalizer,
        normalized_validator=normalized_validator,
        final_validator=final_validator,
        composer=composer,
        id_factory=id_factory,
    )
    result = pipeline.render(
        options.video,
        max_attempts=options.max_attempts,
        scene=options.scene,
        base_run_id=options.base_run,
        correction=options.correction,
    )
    if result.output_path is None:
        raise ValueError("render completed without a final output")
    print(f"READY: {result.output_path}")
    print(f"RUN: {result.run_path}")
    return 0


def _inspect_project(project_path: Path) -> int:
    print(json.dumps(inspect_project(project_path), ensure_ascii=False, sort_keys=True))
    return 0


def _accept_project(project_path: Path, run_id: str) -> int:
    project = accept_project(project_path, run_id)
    print(f"ACCEPTED: {project.id}")
    print(f"RUN: {run_id}")
    return 0


def _serve_web(options: argparse.Namespace) -> int:
    from video_pipeline.web import WebService, serve

    with WebService(
        projects_root=options.projects_root,
        audio_root=options.audio_root,
    ) as service:
        serve(service, host="127.0.0.1", port=options.port)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="video-pipeline")
    subparsers = parser.add_subparsers(dest="command")

    init = subparsers.add_parser("init", help="create a canonical audiovisual project")
    init.add_argument("video", type=Path)
    init.add_argument("--title", required=True)
    init.add_argument("--script", type=Path, required=True)
    init.add_argument("--audio", type=Path, required=True)

    timeline = subparsers.add_parser("timeline", help="validate or confirm a project timeline")
    timeline_commands = timeline.add_subparsers(dest="timeline_command")
    validate = timeline_commands.add_parser("validate", help="review a project timeline candidate")
    validate.add_argument("project", type=Path)
    confirm = timeline_commands.add_parser("confirm", help="confirm a project timeline")
    confirm.add_argument("project", type=Path)

    render = subparsers.add_parser("render", help="render a confirmed project")
    render.add_argument("video", type=Path)
    render.add_argument("--max-attempts", type=int, default=3)
    render.add_argument("--scene")
    render.add_argument("--base-run")
    render.add_argument("--correction")

    inspect = subparsers.add_parser("inspect", help="inspect a canonical project")
    inspect.add_argument("project", type=Path)

    accept = subparsers.add_parser("accept", help="promote a ready run to golden")
    accept.add_argument("project", type=Path)
    accept.add_argument("--run", required=True)

    web = subparsers.add_parser("web", help="serve the local operator Web UI")
    web.add_argument("--port", type=int, default=8000)
    web.add_argument("--projects-root", type=Path, default=Path("projects"))
    web.add_argument("--audio-root", type=Path, default=Path("audio"))
    return parser


__all__ = ["main"]
