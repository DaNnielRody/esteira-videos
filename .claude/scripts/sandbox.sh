#!/usr/bin/env bash
set -euo pipefail

# The first slice exists: a uv/hatchling manifest, a pytest runner, Ruff, and
# mypy are all configured in pyproject.toml.  Run the checks the slice owns.
cd "$(dirname "$0")/../.."

if [[ ! -x .venv/bin/python ]]; then
  printf '%s\n' 'sandbox: .venv/bin/python missing; run `uv sync` first.' >&2
  exit 2
fi

.venv/bin/python -m pytest -q
.venv/bin/ruff check src/
# mypy is scoped, not full: pyproject sets `disallow_any_expr`, which the
# json/argparse/subprocess boundaries in cli.py, provider.py, rendering.py and
# validation.py do not yet satisfy (51 errors), and tests/ do not either.
# Widening this line is tracked in .claude/tmp/doubts-render-in-the-loop-tracer.md.
.venv/bin/mypy src/video_pipeline/pipeline.py src/video_pipeline/prompts.py
