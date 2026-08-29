---
name: darkagent
description: Deliver Esteira de Vídeos changes through context, specification, an evidence-backed Ralph issue DAG, TDD, quality, conclave, and PR delivery; use for full-lifecycle feature or architecture work.
---

# Darkagent — Esteira de Vídeos

Execute steps 1–10 in order on Linux with Bash. Use terse, telegraphic status,
reports, and agent prompts. Preserve full clarity for security warnings,
irreversible actions, specifications, code, commits, and pull requests. Prefix
shell commands with `rtk`; use `rtk proxy` when passthrough is required.
When refreshing CodeGraph, run `rtk proxy npx --yes @colbymchenry/codegraph sync`
and require its terminal index summary before source navigation.

Canonical evidence schema:
`/home/dan/workflow/darkagent/references/evidence-contract.md`. Read it before
creating the PRD, issue payloads, or evidence.

## 1. Open rules and context, then grill

Before choosing scope, run:

```bash
rtk python3 /home/dan/workflow/darkagent/skills/memory/scripts/memory.py rule inject \
  --db /home/dan/saas/esteira-videos/.claude/memory.db
```

Apply the complete workflow-rule block with required instructions. Read and use
`/home/dan/workflow/darkagent/skills/context-map/SKILL.md` to open
`.claude/tmp/contexts/<slug>/RUN.md` and one run file per affected context.
Only after the map identifies observable file prefixes, run memory context
injection for those prefixes. Run
`/home/dan/workflow/darkagent/skills/grilling/SKILL.md`; record whether one
runnable uncertainty needs a prototype. Route later user corrections to one
owner: persistent behavior to memory rule, reusable ordered branch to a local
skill, current mission state to run context. Completion: all active rules are
present; scoped memory matches observed prefixes; run context is open; grilling
has no product decision open; each correction has one owner.

## 2. Specify and plan evidence

Run `/home/dan/workflow/darkagent/skills/spec/SKILL.md` to create
`docs/prd/PRD-<slug>.md`. External dependencies need official URLs and exact
versions or dated revisions. Match active entries from
`/home/dan/workflow/darkagent/references/antipatterns.md`, then run
`/home/dan/workflow/darkagent/skills/evidence-path/SKILL.md`. Completion: every
PRD claim has one complete `## Evidence plan` row conforming to the canonical
evidence schema, including host command IDs/argv and bounds, test and RED data,
blind roles, audit/mutation budgets, scope, antipattern IDs, gates, stable gate
command IDs, outputs, and explicit applicability decisions.

## 3. Create issues and Ralph DAG

Run `/home/dan/workflow/darkagent/skills/issue/SKILL.md` to create
`docs/prd/ISSUES-<slug>.md`, publish tracker issues when a remote exists, and
register their real dependencies through `scripts/orchestrator.py`. A runnable
uncertainty becomes a disposable `prototype` task; a manual-only blocker is a
`human_gate` with a checklist. Copy each evidence row literally into its task
payload. Completion: issue document, tracker IDs or explicit no-remote blocker,
Ralph state, dependency edges, and every payload field trace to the PRD.

## 4. Branch

Discover the current PR target from Git evidence; use `main` while it remains
the only observed branch and no remote exists. Create a focused branch from its
observed commit. Completion: Git reports the new branch at that target commit.

## 5. Execute issue frontier

For each ready task, read
`/home/dan/workflow/darkagent/references/watching-long-runs.md` before the first
supervision endpoint request and take its direct adapter path. Record the
Ralph/SSE source `GET /events`, wake set `CHECKPOINT_REACHED`, `AGENT_FAILED`,
`USER_INPUT_REQUIRED`, `RUN_COMPLETED`, and terminal `RUN_COMPLETED` with
`next_action=respond_user`. Case 3 inline work reads
`/home/dan/workflow/darkagent/references/watching-inline-runs.md` instead.

Run `/home/dan/workflow/darkagent/skills/tdd/SKILL.md` in declared order:
test-author → test-audit → implementer → mutation/regression. Give a blind test
author only the PRD/evidence row, public seam, permitted context, and path-only
disjointness attestation. Dispatch `df-architecture` first when needed, then
`df-python`; use `df-testing` for declared test roles. Serialize overlapping
file or run-context writers. Apply the injected `orchestrator-reviewer-model`
rule to every dispatch. Before a task with gold history, query
`GET /canonical?stage=<stage>` and cite the accepted pattern. Completion: every
accepted issue has observed RED, every declared audit/mutation/antipattern has
compatible evidence, no writers overlap, and Ralph contains a structured
diagnosis for every attempt 2+.

## 6. Quality, rebase, reconcile, delivery task

Run `df-quality` alone after all batch-1 work is green. Rebase on the observed
PR target; block non-trivial conflicts with diagnosis. Reconcile every context
delta through `/home/dan/workflow/darkagent/skills/context-map/SKILL.md`, whose
promoted deltas call `.claude/hooks/memory-reconcile.sh`. Run the sandbox and
register `delivery-<slug>` blocked by every issue task. Completion: quality,
rebase, all context verdicts/hook results, sandbox, and delivery task have fresh
observable state; no delta remains pending.

## 7. Gate loop

Run `.claude/scripts/sandbox.sh` and every issue-declared gate command. Design
is not applicable until a frontend exists. Return RED evidence to its owning
specialist for at most three attempts; preserve exit 2 as an explicit delivery
gap. Completion: each required gate has a fresh verdict and each RED is green
or exhausted with its final structured diagnosis.

## 8. Dossier and conclave

Build `.claude/tmp/dossier-<slug>.md` from PRD, modules, paths and decisive
hunks, budgets, Ralph attempts/human gates, doubts, external evidence,
antipattern coverage, and unvalidated paths. Export Ralph state and materialize
the complete tracked plus untracked changeset before review:

```bash
rtk python3 scripts/orchestrator.py --run-id <run-id> --export-state \
  > .claude/tmp/ralph-state-<slug>.json
```

Create `.claude/tmp/observed.diff` against the merge-base, including every
untracked path, and a temporary archived base tree:

```bash
OBSERVED_DIFF=.claude/tmp/observed.diff
BASE_COMMIT="$(rtk git merge-base <observed-pr-target> HEAD)"
BASE_ROOT="$(rtk mktemp -d .claude/tmp/base-tree.XXXXXX)"
rtk git archive "$BASE_COMMIT" | rtk tar -x -C "$BASE_ROOT"
rtk git diff --no-ext-diff --binary "$BASE_COMMIT" > "$OBSERVED_DIFF"
while IFS= read -r -d '' path; do
  if [[ ! -e "$path" && ! -L "$path" ]]; then
    rtk printf 'untracked path disappeared during snapshot: %s\n' "$path" >&2
    exit 2
  fi
  if rtk git diff --no-ext-diff --no-index --binary /dev/null "$path" >> "$OBSERVED_DIFF"; then
    :
  else
    status=$?
    if [[ "$status" -ne 1 ]]; then exit "$status"; fi
  fi
done < <(rtk git ls-files --others --exclude-standard -z)
rtk test -f "$OBSERVED_DIFF"
```

Then run:

```bash
rtk /home/dan/workflow/gates/mini-conclave/scripts/conclave-gate.sh \
  --job pr-<id> --dossier .claude/tmp/dossier-<slug>.md \
  --prd docs/prd/PRD-<slug>.md \
  --ralph-state .claude/tmp/ralph-state-<slug>.json \
  --diff .claude/tmp/observed.diff --source-root . --base-root <base-tree> \
  --require-findings
```

Add `--lessons LESSONS.md` only when it exists; never provide `--findings`.
Apply findings through their owning specialist, then repeat affected tests,
reconciliation, snapshot, and gates. Completion: dossier and observed diff are
complete, conclave output and extracted findings validate, and each correction
has fresh downstream evidence.

## 9. Accept and deliver

Accept `delivery-<slug>` only when every required gate is green. Create focused
conventional commits and a PR with `.claude/skills/darkagent/PR-TEMPLATE.md`,
including one `Closes #<n>` per published issue. With no remote, write the full
PR body locally and report push/issue/PR creation as blocked. Completion: Ralph
records delivery acceptance and the PR or local body contains all closures,
gate evidence, gaps, and conclave findings.

## 10. Report

Report executed issues, dispatched specialists and model roles, host command
IDs, retry counts, gate evidence IDs/verdicts, antipattern IDs/coverage,
conclave dissent, unvalidated paths, and PR URL or exact no-remote blocker.
Completion: every field comes from observed run evidence; otherwise name the
blocked task, attempt count, and final violations.
