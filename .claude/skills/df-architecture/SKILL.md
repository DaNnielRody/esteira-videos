---
name: df-architecture
description: Define file placement and executable contracts for Darkagent changes; use first in batch 1 when an issue changes module boundaries or public interfaces.
---

# DF Architecture

Mission: define the smallest structure and contracts needed by the issue. Batch
1, before implementation specialists. Scope: paths, ownership, dependency
direction, public seams, and ADR decisions; do not implement behavior.

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

Then read the issue, `.claude/skills/CONTEXT-MAP.MD`,
`.claude/contexts/project/CONTEXT.md`,
`.claude/tmp/contexts/<slug>/RUN.md`, and
`.claude/tmp/contexts/<slug>/project.md`. Read
`/home/dan/workflow/darkagent/references/specialists.md` before proposing
structure; it owns the correction ladder and architecture rubric. Read
`/home/dan/workflow/darkagent/references/evidence-contract.md` before emitting
gate evidence.

## Proven project patterns

- `README.md` — Python pipeline starts from user-supplied scripts.
- `.claude/contexts/project/CONTEXT.md` — one validated MP4 is the first boundary.
- `.claude/contexts/project/CONTEXT.md` — real rendering, not model assertion, decides success.
- `.claude/skills/CONTEXT-MAP.MD` — new application boundaries require mapped contexts.
- `.claude/SANDBOX.md` — first tracer bullet owns packaging, test, and integration setup.
- `.claude/scripts/sandbox.sh` — exit 2 preserves the pending state until that setup exists.

## Project rules

- Keep the first slice at Scene Spec -> provider -> Manim subprocess -> validator -> MP4.
- Keep the provider contract independent of Ollama while implementing only the local adapter now.
- Apply the injected `constrained-ram-stage-order` rule when assigning resource ownership.
- Verify the installed Manim version and CLI before freezing renderer arguments or output paths.
- Add an abstraction only for a second same-intent consumer or an observed test seam.

## Evidence and completion

Run `.claude/scripts/sandbox.sh`; design command is not applicable. Use only
host-declared command IDs, argv, bounds, paths, RED signature, and evidence IDs
from the issue. Append unresolved judgments immediately to
`.claude/tmp/doubts-<slug>.md` as:

```md
### <file>:<symbol> — <question>
- decision: <choice>
- basis: <pattern, policy, measurement, or guess>
```

Complete when every changed boundary has one owner and dependency direction,
every new dependency has an ADR, the rubric labels each finding, only assigned
paths changed, run-context deltas are recorded, and the declared gate evidence
is fresh. On Ralph attempt 2+, include `diagnosis.failed_check`, `root_cause`,
`evidence`, and `next_change`.
