# Context Map — Esteira de Vídeos

Index of the module contexts used by Darkagent runs.

## Protocol

- Skill: `/home/dan/workflow/darkagent/skills/context-map/SKILL.md`
- Each run opens `.claude/tmp/contexts/<slug>/RUN.md` and one file per affected
  module, then reconciles every delta through the skill before completion.
- Reconciliation records each promoted delta through
  `/home/dan/saas/esteira-videos/.claude/hooks/memory-reconcile.sh`.

## Contexts

- [Project foundation](../contexts/project/CONTEXT.md) — owns the greenfield
  repository charter and the first pipeline slice until concrete modules exist.

## Relationships

- None yet; the application has not been scaffolded.

## Engineering policy

- Replicate existing patterns: no product-code pattern exists beyond the Python
  direction documented in `README.md`.
- Add dependencies only through an ADR.
- During implementation, prefer the smallest diff that preserves contracts;
  the quality batch owns refactoring beyond the issue.
- Tests: no runner or convention exists yet; the first tracer-bullet issue must
  establish both and keep the generated sandbox green.

