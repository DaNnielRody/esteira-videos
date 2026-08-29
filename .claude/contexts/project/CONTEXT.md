# Project foundation

Owns the greenfield repository charter and the transition from the current
README-only state to the first executable render-in-the-loop pipeline slice.

## Language

**Scene specification**:
Structured input describing what one scene communicates and how it should be
animated, independently from its generated Manim implementation.
_Avoid_: roteiro, because the user supplies the script/content upstream.

**RITL**:
The execution loop in which a real Manim render decides success and failures
feed code plus diagnostics back to the replaceable model provider.
_Avoid_: code generation, which omits execution and correction.

## Contracts

- The first milestone ends at one validated MP4 for one scene specification.
- Real renderer exit status and output artifacts are the source of truth.
- The MVP runtime is Manim Community 0.21.0 through its subprocess CLI; ManimGL
  is reference-only and generated scenes target the Community API.
- A render is successful only when Manim exits zero, independent MP4
  validation observes a non-empty video stream with positive dimensions and
  duration, and the generated scene code animates no mobject that never
  entered the scene.
- Renderer exit status and container validity do not decide scene semantics.
  A scene that keeps animating `b` after `self.play(Transform(a, b))` exits
  zero and writes a probeable MP4 showing two shapes instead of one, so that
  class is checked in the pipeline and fed back as a correction diagnostic.
- Script/content generation, montage, audio, subtitles, and multi-scene editing
  are outside the first milestone.

## Relationships

- `cli` -> `spec` -> `pipeline`; `pipeline` -> `prompts` -> `provider`
  (generation and unload), `rendering` (bounded Manim subprocess), `validation`
  (ffprobe), `workspace` (per-run and per-attempt directories).

## Operational surface

- Python 3.13.9, Ollama 0.33.2, and FFmpeg 6.1.1 are available locally.
- Manim Community 0.21.0 is installed only in the repository-local `.venv` and
  is observed compatible with Python 3.13.9 using Cairo at 854x480/15 fps.
- The default configurable local model is `qwen2.5-coder:7b`.
- The 16 GB host requires Ollama inference and Manim rendering to be serialized,
  with the Ollama model unloaded before rendering.

## Proven patterns

- `README.md` — keeps the product scope limited to transforming supplied scripts
  into rendered videos.
- `/home/dan/saas/ads4you/pyproject.toml` — compatible modular src-layout
  configuration for uv dependency groups, pytest, Ruff, and strict mypy.
- `.claude/tmp/spike-ritl/REPORT.md` — observed Manim success, traceback, MP4
  location/metadata, and timeout behavior for the chosen runtime.
- `src/video_pipeline/prompts.py` — one prompt builder serves both the provider
  request and the preserved `prompt.txt`, so the stored artifact reproduces
  what was sent.
- `src/video_pipeline/prompts.py` — a correction prompt converges a 7B local
  model only when it leads with the failing generated source line and the root
  error and closes with the corrective instruction. A `repr()` dump of the full
  Rich traceback made the model re-emit identical code every attempt.

## Flagged ambiguities

- The first tracer bullet is delivered and accepted. The open gap is semantic
  fidelity: nothing verifies that the rendered video shows what the description
  asks. The pipeline only rejects one mechanical class (animating a mobject
  never added to the scene); a scene that renders the wrong content correctly
  still passes.
