# Issues — Render-in-the-Loop tracer bullet

## Coverage matrix

| PRD source | Owner | Public seam | Independent check |
|---|---|---|---|
| S0, AC0, prototype decision | `prototype-manim-runtime` | repository-local Manim CLI + spike report | real MP4/ffprobe regression probe |
| S1, AC1, AC2 | `scene-spec-provider` | `load_scene_spec`, `LLMProvider`, `OllamaProvider` | blind schema/provider tests |
| S2, AC3, AC4 | `render-validator-workspace` | `ManimRunner`, `RenderValidator`, `RunWorkspace` | blind subprocess/artifact tests + mutation |
| S3, S4, AC5–AC8 | `ritl-cli-integration` | `RenderPipeline`, `video-pipeline render` | blind loop/CLI tests + real Manim + mutation |

Topological order:

1. `prototype-manim-runtime`
2. `scene-spec-provider`
3. `render-validator-workspace`
4. `ritl-cli-integration`

All issues write the shared project run-context file, so the dependency chain
also serializes that writer. No human-only prerequisite exists. Tracker
publication is blocked because `git remote -v` is empty; the complete local
issues below are authoritative until a remote is configured.

## Issue 1 — prototype-manim-runtime

- Lifecycle: `prototype`
- Behavior: accept the already executed runtime spike as runnable evidence;
  prototype files remain disposable and are not delivery code.
- Inherited criteria: AC0.
- Blocked by: none.
- Required specialist: `df-python` as evidence recorder; no blind test author.
- Affected modules: Project foundation.
- Persistent context: `.claude/contexts/project/CONTEXT.md`.
- Run context: `.claude/tmp/contexts/render-in-the-loop-tracer/project.md`.
- Prototype question: can Manim Community 0.21.0 under Python 3.13.9 render
  the acceptance motion headlessly and expose success, traceback, output-path,
  and timeout facts?
- Dependent decisions: runtime version, renderer argv, MP4 discovery, timeout
  handling, and validator metadata.
- Handoff: submit `prototype_evidence` with `question`, `command`,
  `observation`, and `decision`, then GREEN evidence and the zero-mutation skip.

Canonical payload copied from evidence row C0:

```json
{
  "blocked_by": [],
  "prototype_question": "Can Manim Community 0.21.0 under Python 3.13.9 render the acceptance motion headlessly and expose success, traceback, output-path, and timeout facts?",
  "requires_tdd": false,
  "planned_paths": [".claude/tmp/spike-ritl/**"],
  "expected_outputs": [".claude/tmp/spike-ritl/REPORT.md", ".claude/tmp/spike-ritl/media/success/videos/manual_scene/480p15/AcceptanceScene.mp4"],
  "expects_changes": false,
  "requires_diff_coverage": false,
  "test_paths": [".claude/tmp/spike-ritl/test_spike.py"],
  "red_failure_signature": "PROTOTYPE_RUNTIME_UNPROVEN",
  "blind_test_authorship": false,
  "test_author": "none",
  "blind_roles": {"test_author": "none", "implementer": "df-python:prototype-manim-runtime", "test_auditor": "none"},
  "implementation_plan_paths": [".claude/tmp/spike-ritl/**"],
  "permitted_context_paths": ["docs/prd/PRD-render-in-the-loop-tracer.md", "docs/adr/0001-manim-community-runtime.md"],
  "blind_attestation": {"status": "not-applicable", "implementation_plan_paths_intersection": []},
  "requires_test_audit": false,
  "required_mutation_kills": 0,
  "mutation_probes": [],
  "change_scope": {
    "allowed_globs": [],
    "generated_globs": [],
    "optional_globs": [".claude/tmp/spike-ritl/**"],
    "bundles": [{"id": "prototype-evidence", "globs": [".claude/tmp/spike-ritl/**"]}]
  },
  "active_antipattern_ids": [],
  "required_gates": ["green"],
  "required_gate_evidence_ids": ["green"],
  "required_output_fields": ["summary", "prototype_evidence", "mutation"],
  "evidence_commands": {
    "red": {"command_id": "red", "kind": "red", "argv": [".venv/bin/python", "-m", "pytest", "-q", ".claude/tmp/spike-ritl/test_spike.py"], "timeout": 30, "max_output_bytes": 20000, "test_paths": [".claude/tmp/spike-ritl/test_spike.py"], "red_failure_signature": "PROTOTYPE_RUNTIME_UNPROVEN"},
    "green": {"command_id": "green", "kind": "green", "argv": [".venv/bin/python", "-m", "pytest", "-q", ".claude/tmp/spike-ritl/test_spike.py"], "timeout": 30, "max_output_bytes": 20000, "test_paths": [".claude/tmp/spike-ritl/test_spike.py"]}
  }
}
```

## Issue 2 — scene-spec-provider

- Lifecycle: `tdd`.
- Behavior: deliver the strict Scene Spec loader, package/tooling baseline,
  replaceable provider seam, Ollama generation/correction adapter, code-fence
  extraction, and explicit unload.
- Inherited criteria: AC1, AC2.
- Blocked by: `prototype-manim-runtime`.
- Required specialists/order: `df-testing` blind test author → `df-testing`
  independent test auditor → `df-python` implementer → `df-testing`
  regression recorder.
- Affected modules: Project foundation.
- Persistent context: `.claude/contexts/project/CONTEXT.md`.
- Run context: `.claude/tmp/contexts/render-in-the-loop-tracer/project.md`.
- Public seam: `load_scene_spec`, `LLMProvider`, `OllamaProvider`.
- Independent check: blind schema/provider tests; local Ollama remains a later
  acceptance observation, not the unit-test oracle.
- Handoff: test author receives only the PRD row/public seam/permitted context;
  auditor submits the five-check artifact linked to observed RED before the
  implementer is claimed.

Canonical payload copied from evidence row C1:

```json
{
  "blocked_by": ["prototype-manim-runtime"],
  "requires_tdd": true,
  "planned_paths": ["pyproject.toml", "uv.lock", "src/video_pipeline/__init__.py", "src/video_pipeline/spec.py", "src/video_pipeline/provider.py", "tests/test_scene_spec.py", "tests/test_provider.py", ".claude/tmp/test-audit-scene-spec-provider.json"],
  "expected_outputs": ["pyproject.toml", "uv.lock", "src/video_pipeline/__init__.py", "src/video_pipeline/spec.py", "src/video_pipeline/provider.py", "tests/test_scene_spec.py", "tests/test_provider.py", ".claude/tmp/test-audit-scene-spec-provider.json"],
  "expects_changes": true,
  "requires_diff_coverage": true,
  "test_paths": ["tests/test_provider.py", "tests/test_scene_spec.py"],
  "red_failure_signature": "SCENE_PROVIDER_CONTRACT_MISSING",
  "blind_test_authorship": true,
  "test_author": "df-testing:test-author:scene-spec-provider",
  "blind_roles": {"test_author": "df-testing:test-author:scene-spec-provider", "implementer": "df-python:scene-spec-provider", "test_auditor": "df-testing:test-auditor:scene-spec-provider"},
  "implementation_plan_paths": ["pyproject.toml", "src/video_pipeline/**"],
  "permitted_context_paths": ["docs/prd/PRD-render-in-the-loop-tracer.md", "docs/adr/0001-manim-community-runtime.md"],
  "blind_attestation": {"status": "pass", "implementation_plan_paths_intersection": []},
  "requires_test_audit": true,
  "required_mutation_kills": 0,
  "mutation_probes": [],
  "change_scope": {
    "allowed_globs": ["pyproject.toml", "uv.lock", "src/video_pipeline/__init__.py", "src/video_pipeline/spec.py", "src/video_pipeline/provider.py", "tests/test_scene_spec.py", "tests/test_provider.py"],
    "generated_globs": [".claude/tmp/test-audit-scene-spec-provider.json"],
    "optional_globs": [],
    "bundles": [
      {"id": "scene-provider-source", "globs": ["pyproject.toml", "uv.lock", "src/video_pipeline/__init__.py", "src/video_pipeline/spec.py", "src/video_pipeline/provider.py"]},
      {"id": "scene-provider-tests", "globs": ["tests/test_provider.py", "tests/test_scene_spec.py"]},
      {"id": "scene-provider-audit", "globs": [".claude/tmp/test-audit-scene-spec-provider.json"]}
    ]
  },
  "active_antipattern_ids": [],
  "required_gates": ["green"],
  "required_gate_evidence_ids": ["red", "green", "test-audit"],
  "required_output_fields": ["summary", "coverage_manifest", "mutation", "context_delta"],
  "evidence_commands": {
    "red": {"command_id": "red", "kind": "red", "argv": [".venv/bin/python", "-m", "pytest", "-q", "tests/test_scene_spec.py", "tests/test_provider.py"], "timeout": 60, "max_output_bytes": 40000, "test_paths": ["tests/test_provider.py", "tests/test_scene_spec.py"], "red_failure_signature": "SCENE_PROVIDER_CONTRACT_MISSING"},
    "green": {"command_id": "green", "kind": "green", "argv": [".venv/bin/python", "-m", "pytest", "-q", "tests/test_scene_spec.py", "tests/test_provider.py"], "timeout": 60, "max_output_bytes": 40000, "test_paths": ["tests/test_provider.py", "tests/test_scene_spec.py"]},
    "test-audit": {"command_id": "test-audit", "kind": "test-audit", "argv": [".venv/bin/python", "-m", "pytest", "-q", "tests/test_scene_spec.py::test_scene_spec_audit_contract", "tests/test_provider.py::test_provider_audit_contract"], "timeout": 60, "max_output_bytes": 40000, "test_paths": ["tests/test_scene_spec.py::test_scene_spec_audit_contract", "tests/test_provider.py::test_provider_audit_contract"]}
  }
}
```

## Issue 3 — render-validator-workspace

- Lifecycle: `tdd`.
- Behavior: deliver isolated run/attempt workspaces, the bounded Manim
  subprocess result, process-group timeout handling, MP4 discovery, and
  independent ffprobe validation.
- Inherited criteria: AC3, AC4.
- Blocked by: `scene-spec-provider`.
- Required specialists/order: `df-testing` blind test author → `df-testing`
  independent test auditor → `df-python` implementer → `df-testing` mutation
  and regression recorder.
- Affected modules: Project foundation.
- Persistent context: `.claude/contexts/project/CONTEXT.md`.
- Run context: `.claude/tmp/contexts/render-in-the-loop-tracer/project.md`.
- Public seam: `ManimRunner`, `RenderValidator`, `RunWorkspace`.
- Independent check: controlled subprocess/ffprobe fixtures, timeout facts,
  and the declared zero-duration mutation kill.

Canonical payload copied from evidence row C2:

```json
{
  "blocked_by": ["scene-spec-provider"],
  "requires_tdd": true,
  "planned_paths": ["src/video_pipeline/rendering.py", "src/video_pipeline/validation.py", "src/video_pipeline/workspace.py", "tests/test_rendering.py", "tests/test_validation.py", "tests/test_workspace.py", ".claude/tmp/test-audit-render-validator.json"],
  "expected_outputs": ["src/video_pipeline/rendering.py", "src/video_pipeline/validation.py", "src/video_pipeline/workspace.py", "tests/test_rendering.py", "tests/test_validation.py", "tests/test_workspace.py", ".claude/tmp/test-audit-render-validator.json"],
  "expects_changes": true,
  "requires_diff_coverage": true,
  "test_paths": ["tests/test_rendering.py", "tests/test_validation.py", "tests/test_workspace.py"],
  "red_failure_signature": "RENDER_VALIDATOR_CONTRACT_MISSING",
  "blind_test_authorship": true,
  "test_author": "df-testing:test-author:render-validator-workspace",
  "blind_roles": {"test_author": "df-testing:test-author:render-validator-workspace", "implementer": "df-python:render-validator-workspace", "test_auditor": "df-testing:test-auditor:render-validator-workspace"},
  "implementation_plan_paths": ["src/video_pipeline/rendering.py", "src/video_pipeline/validation.py", "src/video_pipeline/workspace.py"],
  "permitted_context_paths": ["docs/prd/PRD-render-in-the-loop-tracer.md", "docs/adr/0001-manim-community-runtime.md"],
  "blind_attestation": {"status": "pass", "implementation_plan_paths_intersection": []},
  "requires_test_audit": true,
  "required_mutation_kills": 1,
  "mutation_probes": [{"mutation_id": "validator-zero-duration", "path": "src/video_pipeline/validation.py", "before": "if duration_seconds <= 0:", "after": "if duration_seconds < 0:", "behavior_command": "green"}],
  "change_scope": {
    "allowed_globs": ["src/video_pipeline/rendering.py", "src/video_pipeline/validation.py", "src/video_pipeline/workspace.py", "tests/test_rendering.py", "tests/test_validation.py", "tests/test_workspace.py"],
    "generated_globs": [".claude/tmp/test-audit-render-validator.json"],
    "optional_globs": [],
    "bundles": [
      {"id": "render-validator-source", "globs": ["src/video_pipeline/rendering.py", "src/video_pipeline/validation.py", "src/video_pipeline/workspace.py"]},
      {"id": "render-validator-tests", "globs": ["tests/test_rendering.py", "tests/test_validation.py", "tests/test_workspace.py"]},
      {"id": "render-validator-audit", "globs": [".claude/tmp/test-audit-render-validator.json"]}
    ]
  },
  "active_antipattern_ids": [],
  "required_gates": ["green"],
  "required_gate_evidence_ids": ["red", "green", "test-audit"],
  "required_output_fields": ["summary", "coverage_manifest", "mutation", "context_delta"],
  "evidence_commands": {
    "red": {"command_id": "red", "kind": "red", "argv": [".venv/bin/python", "-m", "pytest", "-q", "tests/test_rendering.py", "tests/test_validation.py", "tests/test_workspace.py"], "timeout": 90, "max_output_bytes": 50000, "test_paths": ["tests/test_rendering.py", "tests/test_validation.py", "tests/test_workspace.py"], "red_failure_signature": "RENDER_VALIDATOR_CONTRACT_MISSING"},
    "green": {"command_id": "green", "kind": "green", "argv": [".venv/bin/python", "-m", "pytest", "-q", "tests/test_rendering.py", "tests/test_validation.py", "tests/test_workspace.py"], "timeout": 90, "max_output_bytes": 50000, "test_paths": ["tests/test_rendering.py", "tests/test_validation.py", "tests/test_workspace.py"]},
    "test-audit": {"command_id": "test-audit", "kind": "test-audit", "argv": [".venv/bin/python", "-m", "pytest", "-q", "tests/test_rendering.py::test_rendering_audit_contract", "tests/test_validation.py::test_validation_audit_contract", "tests/test_workspace.py::test_workspace_audit_contract"], "timeout": 90, "max_output_bytes": 50000, "test_paths": ["tests/test_rendering.py::test_rendering_audit_contract", "tests/test_validation.py::test_validation_audit_contract", "tests/test_workspace.py::test_workspace_audit_contract"]}
  }
}
```

## Issue 4 — ritl-cli-integration

- Lifecycle: `tdd`.
- Behavior: deliver prompts, deterministic pipeline state machine, full
  per-attempt preservation, bounded correction, CLI, acceptance spec, happy
  and retry tests, and real Manim integration.
- Inherited criteria: AC5, AC6, AC7, AC8.
- Blocked by: `render-validator-workspace`.
- Required specialists/order: `df-testing` blind test author → `df-testing`
  independent test auditor → `df-python` implementer → `df-testing` mutation
  and regression recorder.
- Affected modules: Project foundation.
- Persistent context: `.claude/contexts/project/CONTEXT.md`.
- Run context: `.claude/tmp/contexts/render-in-the-loop-tracer/project.md`.
- Public seam: `RenderPipeline.render`, `video-pipeline render`.
- Independent check: fake-provider happy/retry/exhaustion/order tests, a real
  Manim MP4 integration, and the valid-MP4 mutation kill.

Canonical payload copied from evidence row C3:

```json
{
  "blocked_by": ["render-validator-workspace"],
  "requires_tdd": true,
  "planned_paths": ["src/video_pipeline/pipeline.py", "src/video_pipeline/cli.py", "src/video_pipeline/prompts.py", "tests/test_pipeline.py", "tests/test_cli.py", "tests/integration/test_real_manim.py", "examples/acceptance-scene.json", "README.md", ".gitignore", ".claude/tmp/test-audit-ritl-cli.json", "artifacts/runs/**"],
  "expected_outputs": ["src/video_pipeline/pipeline.py", "src/video_pipeline/cli.py", "src/video_pipeline/prompts.py", "tests/test_pipeline.py", "tests/test_cli.py", "tests/integration/test_real_manim.py", "examples/acceptance-scene.json", "README.md", ".gitignore", ".claude/tmp/test-audit-ritl-cli.json"],
  "expects_changes": true,
  "requires_diff_coverage": true,
  "test_paths": ["tests/integration/test_real_manim.py", "tests/test_cli.py", "tests/test_pipeline.py"],
  "red_failure_signature": "RITL_CLI_CONTRACT_MISSING",
  "blind_test_authorship": true,
  "test_author": "df-testing:test-author:ritl-cli-integration",
  "blind_roles": {"test_author": "df-testing:test-author:ritl-cli-integration", "implementer": "df-python:ritl-cli-integration", "test_auditor": "df-testing:test-auditor:ritl-cli-integration"},
  "implementation_plan_paths": ["src/video_pipeline/pipeline.py", "src/video_pipeline/cli.py", "src/video_pipeline/prompts.py"],
  "permitted_context_paths": ["docs/prd/PRD-render-in-the-loop-tracer.md", "docs/adr/0001-manim-community-runtime.md"],
  "blind_attestation": {"status": "pass", "implementation_plan_paths_intersection": []},
  "requires_test_audit": true,
  "required_mutation_kills": 1,
  "mutation_probes": [{"mutation_id": "pipeline-requires-valid-mp4", "path": "src/video_pipeline/pipeline.py", "before": "if render_result.exit_code == 0 and validation.valid:", "after": "if render_result.exit_code == 0:", "behavior_command": "green"}],
  "change_scope": {
    "allowed_globs": ["src/video_pipeline/pipeline.py", "src/video_pipeline/cli.py", "src/video_pipeline/prompts.py", "tests/test_pipeline.py", "tests/test_cli.py", "tests/integration/test_real_manim.py", "examples/acceptance-scene.json", "README.md", ".gitignore"],
    "generated_globs": [".claude/tmp/test-audit-ritl-cli.json", "artifacts/runs/**"],
    "optional_globs": [],
    "bundles": [
      {"id": "ritl-source", "globs": ["src/video_pipeline/pipeline.py", "src/video_pipeline/cli.py", "src/video_pipeline/prompts.py"]},
      {"id": "ritl-tests", "globs": ["tests/integration/test_real_manim.py", "tests/test_cli.py", "tests/test_pipeline.py"]},
      {"id": "ritl-docs", "globs": ["examples/acceptance-scene.json", "README.md", ".gitignore"]},
      {"id": "ritl-audit", "globs": [".claude/tmp/test-audit-ritl-cli.json"]},
      {"id": "ritl-artifacts", "globs": ["artifacts/runs/**"]}
    ]
  },
  "active_antipattern_ids": [],
  "required_gates": ["green"],
  "required_gate_evidence_ids": ["red", "green", "test-audit"],
  "required_output_fields": ["summary", "coverage_manifest", "mutation", "context_delta"],
  "evidence_commands": {
    "red": {"command_id": "red", "kind": "red", "argv": [".venv/bin/python", "-m", "pytest", "-q", "tests/test_pipeline.py", "tests/test_cli.py", "tests/integration/test_real_manim.py"], "timeout": 240, "max_output_bytes": 80000, "test_paths": ["tests/integration/test_real_manim.py", "tests/test_cli.py", "tests/test_pipeline.py"], "red_failure_signature": "RITL_CLI_CONTRACT_MISSING"},
    "green": {"command_id": "green", "kind": "green", "argv": [".venv/bin/python", "-m", "pytest", "-q", "tests/test_pipeline.py", "tests/test_cli.py", "tests/integration/test_real_manim.py"], "timeout": 240, "max_output_bytes": 80000, "test_paths": ["tests/integration/test_real_manim.py", "tests/test_cli.py", "tests/test_pipeline.py"]},
    "test-audit": {"command_id": "test-audit", "kind": "test-audit", "argv": [".venv/bin/python", "-m", "pytest", "-q", "tests/test_pipeline.py::test_pipeline_audit_contract", "tests/test_cli.py::test_cli_audit_contract", "tests/integration/test_real_manim.py::test_real_manim_audit_contract"], "timeout": 240, "max_output_bytes": 80000, "test_paths": ["tests/test_pipeline.py::test_pipeline_audit_contract", "tests/test_cli.py::test_cli_audit_contract", "tests/integration/test_real_manim.py::test_real_manim_audit_contract"]}
  }
}
```

## Preventive gate applicability

Required. Issues 2–4 cross state transitions and trigger-dependent provider,
timeout, render, validation, retry, and exhaustion boundaries. The preventive
gate dossier and independent lens outputs are recorded under
`.claude/tmp/issue-gate-render-in-the-loop-tracer.md` and its revisioned job
directory.

Final preventive verdict: revision `r15` has four of four
`ISSUE_GATE: GREEN` outputs and no blocking/dissent marker.

## Mandatory TDD execution order

For each of Issues 2–4, publication/registration creates only the task plan;
no product implementation is authorized until its task reaches step 4:

1. The assigned blind `test_author` writes the declared public-seam tests using
   only the PRD row and permitted context.
2. Ralph's host runner executes the exact `red` command on every declared
   `test_paths` and must observe a non-zero behavioral RED containing that
   issue's exact `red_failure_signature`. Collection, import, fixture,
   environment, timeout, and executable failures do not advance the task.
3. The distinct `test_auditor` runs the exact `test-audit` declaration and
   submits the five-check artifact linked to the observed RED evidence ID.
4. Only after steps 2–3 pass may the declared `implementer` write product code.
5. Ralph's host runner executes the exact `green` command and records a fresh
   zero-exit observation against the same test set.
6. Issues 3–4 run and restore their declared mutation probe, then repeat GREEN;
   Issue 2 records the strict zero-mutation skip using its fresh GREEN ID.

This numbered order is a pre-execution contract. The issue gate validates the
order before registration; the later Ralph evidence records prove each action
actually occurred.

## Supervision contract

Applies to all four Ralph rows.

- `case`: Case 1 (Ralph gates host/specialist checkpoints).
- `source`: Ralph `GET /events` SSE stream.
- `owner_next_ooda`: root Sol orchestrator after every valid wake; the assigned
  specialist owns only its bounded Act output.
- `wake_set`: `CHECKPOINT_REACHED`, `AGENT_FAILED`, `USER_INPUT_REQUIRED`,
  `RUN_COMPLETED`.
- `terminal_event`: `RUN_COMPLETED` with `next_action=respond_user`.
- `primary_criterion`: event-driven wake/termination only; no polling or timer
  is accepted as progress proof.
- `transport_path`: one documented `scripts/orchestrator.py --supervise`
  invocation is the first supervision endpoint request, owns the continuous
  `GET /events` subscription and one dispatch, and ends on one valid wake.
- `state_diagnostic`: absent unless that adapter exits non-zero without a wake;
  then at most one labelled diagnostic-only `GET /state` may explain, never
  replace, the failed adapter outcome.

Blind barriers for Issues 2–4 are the literal disjoint
`implementation_plan_paths`, `permitted_context_paths`, and passing
`blind_attestation` in each payload. Test authors receive no implementation
plan contents.

## Publication status

- Remote/tracker: none configured.
- Published identifiers: unavailable.
- Exact blocker: `git remote -v` returns no entries, so issue publication and
  remote dependency registration cannot occur. Ralph's local task IDs and
  dependency edges are still required and recorded during execution.

## Reconciliation after acceptance (2026-08-29)

- `required_gates` for C1–C3 is `["green"]`. RED and test-audit remain
  mandatory and are still enforced by Ralph before an implementer may claim a
  task, but they are not gates of the final delta and therefore do not appear
  in `required_gates` / `required_gate_evidence_ids`.
- `test_paths` are documented in the order Ralph stores them (sorted), so the
  documents and the exported run state compare equal.
- `expected_outputs` is resolved as literal existing files by the control
  plane, so the glob `artifacts/runs/**` was removed from it. The glob remains
  declared in `planned_paths` and `change_scope.generated_globs`, which are
  glob-matched.
