---
name: df-testing
description: Author and audit behavioral tests for Darkagent issues; use in batch 1 whenever an evidence row requires TDD, blind authorship, test audit, or mutation proof.
---

# DF Testing

Mission: produce the issue-declared behavioral RED and test evidence without
reading implementation-plan contents when blind authorship is active. Batch 1.
Scope: declared test paths and evidence artifacts only.

Use terse, telegraphic status, reports, and prompts. Preserve full clarity for
security warnings, irreversible actions, specifications, code, commits, and
pull requests.

## Inputs

Before opening an assigned file, run:

```bash
python3 /home/dan/workflow/darkagent/skills/memory/scripts/memory.py context \
  --files <permitted-context-paths> --max-tokens 300 \
  --db /home/dan/saas/esteira-videos/.claude/memory.db
```

Read the issue evidence row, its PRD row and public seam, only its
`permitted_context_paths`, the relevant persistent contexts, and corresponding
run-module files. Read `/home/dan/workflow/darkagent/references/specialists.md`
for blind-role ordering and `/home/dan/workflow/darkagent/references/evidence-contract.md`
for the closed schemas before authoring evidence.

## Proven project patterns

- `README.md` — tests cover operational video production, not upstream writing.
- `.claude/contexts/project/CONTEXT.md` — render execution is the behavioral oracle.
- `.claude/contexts/project/CONTEXT.md` — correction retries are part of the RITL contract.
- `.claude/SANDBOX.md` — unit and real headless render surfaces are both required.
- `.claude/scripts/sandbox.sh` — current exit 2 is an environment gap, not a behavioral RED.
- `.claude/skills/CONTEXT-MAP.MD` — test deltas reconcile through their owning module.

## Testing rules

- Assert provider and renderer behavior through public seams; do not assert private call choreography unless it is contractual.
- Distinguish Manim traceback failure from timeout, missing executable, invalid output, and provider failure.
- Use deterministic fakes for unit tests and one real Manim scene for integration evidence.
- A RED is valid only when every declared test path runs and output contains the declared behavioral signature.
- Preserve the exact blind-role separation, audit producer, and mutation budget declared by the issue.

## Evidence and completion

Run only the host-declared argv from the issue; the current general sandbox
command is `.claude/scripts/sandbox.sh`, and design is not applicable. Append
unresolved judgments to `.claude/tmp/doubts-<slug>.md`:

```md
### <file>:<symbol> — <question>
- decision: <choice>
- basis: <pattern, policy, measurement, or guess>
```

Complete when RED has the declared signature, test paths and test-set hash;
blind barriers have empty intersection; any required audit has exactly the five
closed checks; mutation requirements or the zero-budget skip have fresh GREEN
IDs; and run deltas are recorded. On Ralph attempt 2+, include
`diagnosis.failed_check`, `root_cause`, `evidence`, and `next_change`.

