---
name: df-python
description: Implement Python pipeline behavior for Darkagent issues; use in batch 1 for scene, provider, renderer, validator, loop, workspace, or CLI changes.
---

# DF Python

Mission: implement the issue's Python behavior through its public seam with the
smallest verified diff. Batch 1. Scope is limited to the issue's declared
Python, test-support, and configuration paths.

Use terse, telegraphic status, reports, and prompts. Preserve full clarity for
security warnings, irreversible actions, specifications, code, commits, and
pull requests.

## Inputs

Before opening an assigned file, run:

```bash
python3 /home/dan/workflow/darkagent/skills/memory/scripts/memory.py context \
  --files <assigned-path-prefixes> --max-tokens 300 \
  --db /home/dan/saas/esteira-videos/.claude/memory.db
```

Then read the issue, `.claude/skills/CONTEXT-MAP.MD`, each assigned persistent
context, `.claude/tmp/contexts/<slug>/RUN.md`, and its assigned run-module
files. Read `/home/dan/workflow/darkagent/references/specialists.md` before the
first edit; it owns `delete -> platform -> existing owner -> correct value ->
add`. Read `/home/dan/workflow/darkagent/references/evidence-contract.md` before
producing evidence.

## Proven project patterns

- `README.md` — Python is the selected implementation language.
- `README.md` — content and scripts enter the pipeline already authored.
- `.claude/contexts/project/CONTEXT.md` — scene specification stays separate from generated code.
- `.claude/contexts/project/CONTEXT.md` — renderer outcome is the source of truth.
- `.claude/contexts/project/CONTEXT.md` — montage, audio, subtitles, and multi-scene work are deferred.
- `.claude/SANDBOX.md` — the first slice must establish pinned dependencies and both test surfaces.
- `.claude/skills/CONTEXT-MAP.MD` — concrete module creation requires context-map reconciliation.

## Python rules

- Use an injected provider protocol; keep Ollama lifecycle behavior inside its adapter.
- Execute Manim with bounded subprocess time, captured stdout/stderr, explicit workspace, and checked exit status.
- Treat an expected, valid MP4 as part of success; exit zero alone is insufficient.
- Persist attempt input, code, diagnostics, correction, and terminal state without secrets.
- Implement the injected `constrained-ram-stage-order` lifecycle inside the Ollama adapter boundary.

## Evidence and completion

Run `.claude/scripts/sandbox.sh`; design command is not applicable. Select only
the issue's host-declared evidence commands and report the literal
`requires_tdd` branch, test paths, RED signature, and required gate evidence
IDs. Append unresolved judgments to `.claude/tmp/doubts-<slug>.md`:

```md
### <file>:<symbol> — <question>
- decision: <choice>
- basis: <pattern, policy, measurement, or guess>
```

Complete when the public behavior is green, assigned outputs exist, every
changed path is in scope, no earlier correction-ladder rung can solve remaining
additions, durable green deltas are recorded, and fresh gate evidence matches
the issue. On Ralph attempt 2+, include `diagnosis.failed_check`, `root_cause`,
`evidence`, and `next_change`.
