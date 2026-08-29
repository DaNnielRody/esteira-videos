# Sandbox contract

Status: `pending first slice`

The repository has no application manifest, dependency lock, test runner, or
runtime entry point. The first tracer-bullet issue must replace the pending
script with a reproducible Python sandbox after it verifies the selected Manim
and Python versions.

## Required surfaces

- Unit tests: required for the first product slice.
- Integration render: required; execute one real headless Manim scene and
  validate its MP4 artifact.
- Frontend build: not applicable.
- Database services: not applicable.
- Design gate: not applicable.

## Command

```bash
.claude/scripts/sandbox.sh
```

Exit `2` is intentional until the first slice supplies pinned dependencies and
the real commands. The eventual sandbox must run as uid 1001, drop all Linux
capabilities, enable `no-new-privileges`, avoid real secrets and host ports,
and use only disposable writable state.

