# Darkagent bootstrap report

Date: 2026-08-28

## Communication policy

Terse, telegraphic status, reports, and agent prompts. Full clarity remains
required for security warnings, irreversible actions, specifications, code,
commits, and pull requests. The generated specialists and project workflow
consume this policy.

## Code graph and codemap

- `rtk proxy npx --yes @colbymchenry/codegraph init`: completed; no source
  files existed to index.
- `rtk proxy npx --yes @colbymchenry/codegraph index`: completed; no entry
  points or module boundaries existed.
- Codemap route: `skipped-with-reason`. One `ls` explains the complete
  README-only checkout, so no semantic folder atlas would reduce discovery
  cost. Init, template fill, root atlas, and `AGENTS.md` registration were
  skipped by that route.

## Discovered stack

- Backend/application: absent; `README.md` selects Python for future work.
- Frontend, data store, mocks, test runner, configuration loader: absent.
- Local integrations: Python 3.13.9, Ollama 0.33.2, FFmpeg 6.1.1.
- Manim: absent from the current environment.
- Git: local `main` at `6792962`; no remote; observed PR target is `main`.

## Context, memory, and sandbox

- Project map: `.claude/skills/CONTEXT-MAP.MD`.
- Persistent boundary: `.claude/contexts/project/CONTEXT.md`.
- Memory: `.claude/memory.db` with four active project rules.
- Reconcile hook: `.claude/hooks/memory-reconcile.sh`, executable and ignored.
- Sandbox: `.claude/scripts/sandbox.sh` returned exit 2 and is explicitly
  `pending first slice`; design is not applicable.

## Generated workflow

- Specialists: `df-architecture`, `df-python`, `df-testing`, `df-quality`.
- Project pipeline: `.claude/skills/darkagent/SKILL.md`, ten ordered steps.
- PR template: `.claude/skills/darkagent/PR-TEMPLATE.md`.
- Ralph entrypoints and requirements: `scripts/`.

## Verification evidence

- From `/home/dan/workflow/darkagent`,
  `rtk python3 -m unittest discover -s scripts -p 'test_*.py'`: green, 111 tests.
- From `/home/dan/workflow/darkagent/skills/memory`,
  `rtk python3 -m unittest discover -s scripts -p 'test_memory.py'`: green, 10 tests.
- `rtk /home/dan/workflow/gates/mini-conclave/scripts/selftest.sh`: green,
  141 checks.
- Generated link/writing audit: green; five skill descriptions, ten completion
  clauses, guard pointers, specialist contracts, and context links verified.
- `rtk git diff --check`: green.
- `.claude/scripts/sandbox.sh`: inconclusive, exit 2, pending first slice.

## Writing audit

All generated agent documents have one trigger description, guard pointers
before constrained actions, observable completion criteria, real project paths,
and canonical policy pointers. No unresolved writing finding remains.

## Learning write-back

- Direct `rtk npx` invocation was incompatible. Canonical owner:
  `.claude/skills/darkagent/SKILL.md`, which now requires the concrete
  `rtk proxy npx ...` command and terminal index evidence.
- The initial Ralph and memory test failures were command-invocation mistakes
  during bootstrap, not project workflow gaps. They were rerun correctly and
  were not promoted into a project rule, script, or lesson.
- No `LESSONS.md` was created; the only retained workflow gap has imperative
  prevention in its canonical owner.
