---
name: df-quality
description: Review and minimally improve green Darkagent changes; use alone in batch 2 after every implementation task and required test gate is green.
---

# DF Quality

Mission: adversarially review the accepted diff for correctness, duplication,
operational safety, and measured performance while freezing behavior. Batch 2.
Scope: files touched by the run and explicit issue-owned corrections.

Use terse, telegraphic status, reports, and prompts. Preserve full clarity for
security warnings, irreversible actions, specifications, code, commits, and
pull requests.

## Inputs

Before opening an assigned file, run:

```bash
python3 /home/dan/workflow/darkagent/skills/memory/scripts/memory.py context \
  --files <changed-path-prefixes> --max-tokens 300 \
  --db /home/dan/saas/esteira-videos/.claude/memory.db
```

Read the issue, accepted diff, `.claude/skills/CONTEXT-MAP.MD`, assigned
persistent contexts, `.claude/tmp/contexts/<slug>/RUN.md`, and assigned
run-module files. Read `/home/dan/workflow/darkagent/references/specialists.md`
for batch-2 ownership and `/home/dan/workflow/darkagent/references/evidence-contract.md`
before consuming or producing evidence.

## Proven project patterns

- `README.md` — the product automates rendering from supplied content.
- `.claude/contexts/project/CONTEXT.md` — one validated scene MP4 bounds milestone one.
- `.claude/contexts/project/CONTEXT.md` — model claims never override renderer evidence.
- `.claude/contexts/project/CONTEXT.md` — 16 GB resource ownership is sequential.
- `.claude/SANDBOX.md` — unit and headless integration evidence are delivery requirements.
- `.claude/skills/CONTEXT-MAP.MD` — the first slice must reconcile newly created module boundaries.
- `.claude/hooks/memory-reconcile.sh` — promoted durable facts enter project memory through one owner.

## Quality rules

- Reproduce a finding before changing code; label speculative risks without blocking.
- Check timeout cleanup, process termination, workspace containment, log redaction, and Ollama unload behavior.
- Check retry state for bounded attempts, immutable attempt artifacts, and one terminal outcome.
- Measure before changing performance and preserve the injected `constrained-ram-stage-order` rule.
- Refactor only issue-touched code and only while every existing evidence command stays green.

## Supervision branches

For delegated control-plane work, read
`/home/dan/workflow/darkagent/references/watching-long-runs.md` before any
supervision endpoint request, take its direct adapter path, and record whether
Watcher or Solo owns the next OODA iteration. For explicit inline work, read
`/home/dan/workflow/darkagent/references/watching-inline-runs.md` instead.

## Evidence and completion

Run `.claude/scripts/sandbox.sh`; design is not applicable. Re-run every
issue-declared gate command after a correction. Append unresolved judgments to
`.claude/tmp/doubts-<slug>.md`:

```md
### <file>:<symbol> — <question>
- decision: <choice>
- basis: <pattern, policy, measurement, or guess>
```

Complete when every finding has force and reproduction evidence, only
issue-owned corrections changed, all required gate IDs resolve to fresh green
observations, behavior stayed frozen, and durable deltas are recorded. On
Ralph attempt 2+, include `diagnosis.failed_check`, `root_cause`, `evidence`,
and `next_change`.
