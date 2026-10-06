#!/usr/bin/env bash
set -euo pipefail

# Run the definitive product checks without model or network access.
cd "$(dirname "$0")/../.."

if [[ ! -x .venv/bin/python ]]; then
  printf '%s\n' 'sandbox: .venv/bin/python missing; run `uv sync` first.' >&2
  exit 2
fi

.venv/bin/python -m pytest -q -m "not integration"
.venv/bin/ruff check .
# Mypy checks the 13 modules whose public contracts are statically typed.
# golden.py, runtime.py, and pixel/AST evidence adapters remain outside this
# gate because their JSON, Manim, NumPy/OpenCV, and AST boundaries are dynamic.
# provider.py, rendering.py, and cli.py are also excluded: their subprocess,
# provider, and argparse seams are intentionally dynamic.  Those boundaries
# are covered by deterministic behavioural tests and Ruff instead.
.venv/bin/mypy \
  --follow-imports=silent \
  src/video_pipeline/video.py \
  src/video_pipeline/pipeline.py \
  src/video_pipeline/prompts.py \
  src/video_pipeline/expectations.py \
  src/video_pipeline/spec.py \
  src/video_pipeline/theme.py \
  src/video_pipeline/scene_plan.py \
  src/video_pipeline/quality.py \
  src/video_pipeline/capabilities.py \
  src/video_pipeline/project.py \
  src/video_pipeline/timeline.py \
  src/video_pipeline/temporal.py \
  src/video_pipeline/validation.py
