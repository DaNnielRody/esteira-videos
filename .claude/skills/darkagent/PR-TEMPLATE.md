## Summary

<what changed and why; include root cause when applicable>

## Changes

### <concern>

- `<real path>`: <specific behavior>

### Tests

- `<real path or command id>`: <behavior and evidence id>

## Verification

```bash
.claude/scripts/sandbox.sh
```

| Gate | Command ID | Evidence ID | Verdict |
|---|---|---|---|
| <gate> | <stable command id> | <observed id> | <green/red/inconclusive> |

## Not validated

<explicit gaps and requested reviewer attention, or “No known gaps.”>

## Context and retry state

- Context reconciliation: <complete/blocker>
- Attempts and diagnoses: <task/count/diagnosis>
- Antipattern IDs and coverage: <ids/evidence>

## Conclave

<validated findings, dissent, actions, and fresh downstream evidence>

## Checklist

- [ ] Every required gate is green
- [ ] Context write-back and memory hook results are complete
- [ ] No dependency lacks an ADR and official URL/version evidence
- [ ] Conclave corrections were re-tested and re-gated
- [ ] Unvalidated paths are explicit

## Issues

<one `Closes #<n>` per published issue; explain when no remote exists>

