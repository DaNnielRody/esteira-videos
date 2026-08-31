# Issues — Video Flow UI MVP

PRD: `docs/prd/PRD-video-flow-ui.md`

## DAG

Exactly six tasks are planned in a serialized DAG. `accept-contracts` records
the backend corrections required by the real accept flow; `selective-revisions`
waits for it and `progress-events`. The web service waits for both feature
tasks, the operator UI waits for the service, and delivery waits for every
preceding task.

```text
accept-contracts → progress-events → selective-revisions → web-service-api → operator-ui → delivery
```

## 1 — accept-contracts

- Tracker: pending publication
- Behavior: invalid acceptance rolls back atomically; selective lineage accepts
  only attested reused siblings; init exposes canonical capability declarations.
- Modules: Project foundation; Render pipeline.
- Blocked by: none.
- Specialists: Sol review/audit → Luna implementation → mutation/regression.
- Inherited criteria: AC3, AC5.

```json
{"requires_tdd":true,"planned_paths":["src/video_pipeline/golden.py","src/video_pipeline/project.py","src/video_pipeline/timeline.py","tests/test_audiovisual_composition.py","tests/test_project_accept.py","tests/test_project_resume.py","tests/test_timeline_init.py",".claude/tmp/test-audit-accept-contracts.json"],"expected_outputs":["src/video_pipeline/golden.py","src/video_pipeline/project.py","src/video_pipeline/timeline.py","tests/test_audiovisual_composition.py","tests/test_project_accept.py","tests/test_project_resume.py","tests/test_timeline_init.py",".claude/tmp/test-audit-accept-contracts.json"],"expects_changes":true,"requires_diff_coverage":true,"test_paths":["tests/test_audiovisual_composition.py","tests/test_project_accept.py","tests/test_project_resume.py","tests/test_timeline_init.py"],"red_failure_signature":"ACCEPT_CONTRACTS_MISSING","blind_test_authorship":false,"test_author":"df-testing:test-author:accept-contracts","blind_roles":{"test_author":"df-testing:test-author:accept-contracts","implementer":"gpt-5.6-luna:max:accept-contracts","test_auditor":"sol-high:test-auditor:accept-contracts"},"implementation_plan_paths":[],"permitted_context_paths":["docs/prd/PRD-video-flow-ui.md","src/video_pipeline/golden.py","src/video_pipeline/project.py","src/video_pipeline/timeline.py"],"blind_attestation":{"status":"not-applicable","implementation_plan_paths_intersection":[]},"requires_test_audit":true,"required_mutation_kills":0,"mutation_probes":[],"change_scope":{"allowed_globs":["src/video_pipeline/golden.py","src/video_pipeline/project.py","src/video_pipeline/timeline.py","tests/test_audiovisual_composition.py","tests/test_project_accept.py","tests/test_project_resume.py","tests/test_timeline_init.py"],"generated_globs":[".claude/tmp/test-audit-accept-contracts.json"],"optional_globs":[],"bundles":[{"id":"accept-source","globs":["src/video_pipeline/golden.py","src/video_pipeline/project.py","src/video_pipeline/timeline.py"]},{"id":"accept-tests","globs":["tests/test_audiovisual_composition.py","tests/test_project_accept.py","tests/test_project_resume.py","tests/test_timeline_init.py"]},{"id":"accept-audit","globs":[".claude/tmp/test-audit-accept-contracts.json"]}]},"active_antipattern_ids":[],"evidence_commands":{"red":{"command_id":"accept-red","kind":"red","argv":[".venv/bin/python","-m","pytest","-q","tests/test_audiovisual_composition.py","tests/test_project_accept.py","tests/test_project_resume.py","tests/test_timeline_init.py"],"timeout":180,"max_output_bytes":80000,"test_paths":["tests/test_audiovisual_composition.py","tests/test_project_accept.py","tests/test_project_resume.py","tests/test_timeline_init.py"],"red_failure_signature":"ACCEPT_CONTRACTS_MISSING"},"test-audit":{"command_id":"accept-test-audit","kind":"test-audit","argv":[".venv/bin/python","-m","pytest","-q","tests/test_audiovisual_composition.py","tests/test_project_accept.py","tests/test_project_resume.py","tests/test_timeline_init.py"],"timeout":180,"max_output_bytes":80000,"test_paths":["tests/test_audiovisual_composition.py","tests/test_project_accept.py","tests/test_project_resume.py","tests/test_timeline_init.py"]},"green":{"command_id":"accept-green","kind":"green","argv":[".venv/bin/python","-m","pytest","-q","tests/test_audiovisual_composition.py","tests/test_project_accept.py","tests/test_project_resume.py","tests/test_timeline_init.py"],"timeout":180,"max_output_bytes":80000,"test_paths":["tests/test_audiovisual_composition.py","tests/test_project_accept.py","tests/test_project_resume.py","tests/test_timeline_init.py"]}},"required_gates":["green"],"required_gate_evidence_ids":["accept-green"],"required_output_fields":["summary","coverage_manifest","context_delta"]}
```

## 2 — progress-events

- Tracker: pending publication
- Behavior: optional typed callbacks expose the actual fine-grained stage order
  for success, correction, and terminal failure without changing existing
  callers or replacing durable `run.json` evidence.
- Modules: Render pipeline.
- Blocked by: none.
- Specialists: `df-testing` test-author → test-audit → implementation →
  mutation/regression.
- Context: `.claude/contexts/render-pipeline/CONTEXT.md`,
  `.claude/tmp/contexts/video-flow-ui/render-pipeline.md`.
- Inherited criteria: S2, AC2.

```json
{"requires_tdd":true,"planned_paths":["src/video_pipeline/pipeline.py","src/video_pipeline/video.py","src/video_pipeline/__init__.py","tests/test_pipeline_progress.py",".claude/tmp/test-audit-pipeline-progress.json"],"expected_outputs":["src/video_pipeline/pipeline.py","src/video_pipeline/video.py","src/video_pipeline/__init__.py","tests/test_pipeline_progress.py",".claude/tmp/test-audit-pipeline-progress.json"],"expects_changes":true,"requires_diff_coverage":true,"test_paths":["tests/test_pipeline_progress.py"],"red_failure_signature":"PIPELINE_PROGRESS_CONTRACT_MISSING","blind_test_authorship":false,"test_author":"df-testing:test-author:progress-events","blind_roles":{"test_author":"df-testing:test-author:progress-events","implementer":"gpt-5.6-luna:max:progress-events","test_auditor":"sol-high:test-auditor:progress-events"},"implementation_plan_paths":[],"permitted_context_paths":["docs/prd/PRD-video-flow-ui.md","tests/test_pipeline.py","tests/test_project_lifecycle.py"],"blind_attestation":{"status":"not-applicable","implementation_plan_paths_intersection":[]},"requires_test_audit":true,"required_mutation_kills":1,"mutation_probes":[{"mutation_id":"progress-render-stage","path":"src/video_pipeline/pipeline.py","before":"PipelineStage.RENDERING","after":"PipelineStage.GENERATING","behavior_command":"progress-green"}],"change_scope":{"allowed_globs":["src/video_pipeline/pipeline.py","src/video_pipeline/video.py","src/video_pipeline/__init__.py","tests/test_pipeline_progress.py"],"generated_globs":[".claude/tmp/test-audit-pipeline-progress.json"],"optional_globs":[],"bundles":[{"id":"progress-source","globs":["src/video_pipeline/pipeline.py","src/video_pipeline/video.py","src/video_pipeline/__init__.py"]},{"id":"progress-tests","globs":["tests/test_pipeline_progress.py"]},{"id":"progress-audit","globs":[".claude/tmp/test-audit-pipeline-progress.json"]}]},"active_antipattern_ids":[],"evidence_commands":{"red":{"command_id":"progress-red","kind":"red","argv":[".venv/bin/python","-m","pytest","-q","tests/test_pipeline_progress.py"],"timeout":90,"max_output_bytes":40000,"test_paths":["tests/test_pipeline_progress.py"],"red_failure_signature":"PIPELINE_PROGRESS_CONTRACT_MISSING"},"test-audit":{"command_id":"progress-test-audit","kind":"test-audit","argv":[".venv/bin/python","-m","pytest","-q","tests/test_pipeline_progress.py"],"timeout":90,"max_output_bytes":40000,"test_paths":["tests/test_pipeline_progress.py"]},"green":{"command_id":"progress-green","kind":"green","argv":[".venv/bin/python","-m","pytest","-q","tests/test_pipeline_progress.py"],"timeout":90,"max_output_bytes":40000,"test_paths":["tests/test_pipeline_progress.py"]}},"required_gates":["green"],"required_gate_evidence_ids":["progress-green"],"required_output_fields":["summary","coverage_manifest","mutation","context_delta"]}
```

## 3 — selective-revisions

- Tracker: pending publication
- Behavior: a terminal correction regenerates one canonical scene from a ready
  base run in a new outer run, reuses and verifies ready siblings by hash,
  composes a new final MP4, and creates the corresponding immutable UI revision
  with checkout/branch parent identity; the base run and golden remain untouched.
- Modules: Render pipeline; Web UI.
- Blocked by: `accept-contracts`, `progress-events`.
- Specialists: `df-testing` test-author → test-audit → implementation →
  mutation/regression.
- Context: `.claude/contexts/render-pipeline/CONTEXT.md`,
  `.claude/contexts/web-ui/CONTEXT.md`,
  `.claude/tmp/contexts/video-flow-ui/render-pipeline.md`,
  `.claude/tmp/contexts/video-flow-ui/web-ui.md`.
- Inherited criteria: S3–S4, AC3–AC4.

```json
{"requires_tdd":true,"planned_paths":["src/video_pipeline/video.py","src/video_pipeline/revisions.py","src/video_pipeline/__init__.py","tests/test_selective_regeneration.py","tests/test_project_revisions.py",".claude/tmp/test-audit-selective-revisions.json"],"expected_outputs":["src/video_pipeline/video.py","src/video_pipeline/revisions.py","src/video_pipeline/__init__.py","tests/test_selective_regeneration.py","tests/test_project_revisions.py",".claude/tmp/test-audit-selective-revisions.json"],"expects_changes":true,"requires_diff_coverage":true,"test_paths":["tests/test_selective_regeneration.py","tests/test_project_revisions.py"],"red_failure_signature":"SELECTIVE_REVISION_CONTRACT_MISSING","blind_test_authorship":false,"test_author":"df-testing:test-author:selective-revisions","blind_roles":{"test_author":"df-testing:test-author:selective-revisions","implementer":"gpt-5.6-luna:max:selective-revisions","test_auditor":"sol-high:test-auditor:selective-revisions"},"implementation_plan_paths":[],"permitted_context_paths":["docs/prd/PRD-video-flow-ui.md","src/video_pipeline/video.py","tests/test_project_lifecycle.py","tests/test_project_accept.py"],"blind_attestation":{"status":"not-applicable","implementation_plan_paths_intersection":[]},"requires_test_audit":true,"required_mutation_kills":2,"mutation_probes":[{"mutation_id":"base-run-project-check","path":"src/video_pipeline/video.py","before":"run_document.get(\"project_id\") != project.id","after":"False","behavior_command":"selective-green"},{"mutation_id":"revision-parent","path":"src/video_pipeline/revisions.py","before":"parent_revision_id=base.revision_id","after":"parent_revision_id=None","behavior_command":"selective-green"}],"change_scope":{"allowed_globs":["src/video_pipeline/video.py","src/video_pipeline/revisions.py","src/video_pipeline/__init__.py","tests/test_selective_regeneration.py","tests/test_project_revisions.py"],"generated_globs":[".claude/tmp/test-audit-selective-revisions.json"],"optional_globs":[],"bundles":[{"id":"selective-source","globs":["src/video_pipeline/video.py","src/video_pipeline/revisions.py","src/video_pipeline/__init__.py"]},{"id":"selective-tests","globs":["tests/test_selective_regeneration.py","tests/test_project_revisions.py"]},{"id":"selective-audit","globs":[".claude/tmp/test-audit-selective-revisions.json"]}]},"active_antipattern_ids":[],"evidence_commands":{"red":{"command_id":"selective-red","kind":"red","argv":[".venv/bin/python","-m","pytest","-q","tests/test_selective_regeneration.py","tests/test_project_revisions.py"],"timeout":120,"max_output_bytes":60000,"test_paths":["tests/test_selective_regeneration.py","tests/test_project_revisions.py"],"red_failure_signature":"SELECTIVE_REVISION_CONTRACT_MISSING"},"test-audit":{"command_id":"selective-test-audit","kind":"test-audit","argv":[".venv/bin/python","-m","pytest","-q","tests/test_selective_regeneration.py","tests/test_project_revisions.py"],"timeout":120,"max_output_bytes":60000,"test_paths":["tests/test_selective_regeneration.py","tests/test_project_revisions.py"]},"green":{"command_id":"selective-green","kind":"green","argv":[".venv/bin/python","-m","pytest","-q","tests/test_selective_regeneration.py","tests/test_project_revisions.py"],"timeout":120,"max_output_bytes":60000,"test_paths":["tests/test_selective_regeneration.py","tests/test_project_revisions.py"]}},"required_gates":["green"],"required_gate_evidence_ids":["selective-green"],"required_output_fields":["summary","coverage_manifest","mutation","context_delta"]}
```

## 4 — web-service-api

- Tracker: pending publication
- Behavior: expose the canonical project/timeline/render/inspect/accept flow,
  safe ID-resolved media, terminal failures, and one serialized FIFO worker over
  a loopback-only JSON API.
- Modules: Web UI; Render pipeline.
- Blocked by: `progress-events`, `selective-revisions`.
- Specialists: `df-testing` test-author → test-audit → implementation →
  mutation/regression.
- Context: both persistent and run files for Render pipeline and Web UI.
- Inherited criteria: S1–S2, S5, S7, AC1, AC2, AC5, AC8, including create →
  confirm → render → regenerate the selected canonical scene → inspect/checkout flow.

```json
{"requires_tdd":true,"planned_paths":["src/video_pipeline/web/__init__.py","src/video_pipeline/web/service.py","src/video_pipeline/web/server.py","src/video_pipeline/web/limits.py","src/video_pipeline/cli.py","tests/test_web_service.py","tests/test_web_api.py","tests/integration/test_web_flow.py",".claude/tmp/test-audit-web-service.json"],"expected_outputs":["src/video_pipeline/web/__init__.py","src/video_pipeline/web/service.py","src/video_pipeline/web/server.py","src/video_pipeline/web/limits.py","src/video_pipeline/cli.py","tests/test_web_service.py","tests/test_web_api.py","tests/integration/test_web_flow.py",".claude/tmp/test-audit-web-service.json"],"expects_changes":true,"requires_diff_coverage":true,"test_paths":["tests/test_web_service.py","tests/test_web_api.py","tests/integration/test_web_flow.py"],"red_failure_signature":"WEB_CANONICAL_SERVICE_CONTRACT_MISSING","blind_test_authorship":false,"test_author":"df-testing:test-author:web-service-api","blind_roles":{"test_author":"df-testing:test-author:web-service-api","implementer":"gpt-5.6-luna:max:web-service-api","test_auditor":"sol-high:test-auditor:web-service-api"},"implementation_plan_paths":[],"permitted_context_paths":["docs/prd/PRD-video-flow-ui.md","src/video_pipeline/project.py","src/video_pipeline/video.py","src/video_pipeline/revisions.py"],"blind_attestation":{"status":"not-applicable","implementation_plan_paths_intersection":[]},"requires_test_audit":true,"required_mutation_kills":2,"mutation_probes":[{"mutation_id":"single-worker","path":"src/video_pipeline/web/service.py","before":"max_workers=1","after":"max_workers=2","behavior_command":"web-green"},{"mutation_id":"asset-relative-root","path":"src/video_pipeline/web/server.py","before":"candidate.relative_to(root)","after":"candidate","behavior_command":"web-green"}],"change_scope":{"allowed_globs":["src/video_pipeline/web/__init__.py","src/video_pipeline/web/service.py","src/video_pipeline/web/server.py","src/video_pipeline/web/limits.py","src/video_pipeline/cli.py","tests/test_web_service.py","tests/test_web_api.py","tests/integration/test_web_flow.py"],"generated_globs":[".claude/tmp/test-audit-web-service.json"],"optional_globs":[],"bundles":[{"id":"web-source","globs":["src/video_pipeline/web/__init__.py","src/video_pipeline/web/service.py","src/video_pipeline/web/server.py","src/video_pipeline/web/limits.py","src/video_pipeline/cli.py"]},{"id":"web-tests","globs":["tests/test_web_service.py","tests/test_web_api.py","tests/integration/test_web_flow.py"]},{"id":"web-audit","globs":[".claude/tmp/test-audit-web-service.json"]}]},"active_antipattern_ids":[],"evidence_commands":{"red":{"command_id":"web-red","kind":"red","argv":[".venv/bin/python","-m","pytest","-q","tests/test_web_service.py","tests/test_web_api.py","tests/integration/test_web_flow.py"],"timeout":180,"max_output_bytes":80000,"test_paths":["tests/test_web_service.py","tests/test_web_api.py","tests/integration/test_web_flow.py"],"red_failure_signature":"WEB_CANONICAL_SERVICE_CONTRACT_MISSING"},"test-audit":{"command_id":"web-test-audit","kind":"test-audit","argv":[".venv/bin/python","-m","pytest","-q","tests/test_web_service.py","tests/test_web_api.py","tests/integration/test_web_flow.py"],"timeout":180,"max_output_bytes":80000,"test_paths":["tests/test_web_service.py","tests/test_web_api.py","tests/integration/test_web_flow.py"]},"green":{"command_id":"web-green","kind":"green","argv":[".venv/bin/python","-m","pytest","-q","tests/test_web_service.py","tests/test_web_api.py","tests/integration/test_web_flow.py"],"timeout":180,"max_output_bytes":80000,"test_paths":["tests/test_web_service.py","tests/test_web_api.py","tests/integration/test_web_flow.py"]}},"required_gates":["green"],"required_gate_evidence_ids":["web-green"],"required_output_fields":["summary","coverage_manifest","mutation","context_delta"]}
```

## 5 — operator-ui

- Tracker: pending publication
- Behavior: a responsive accessible three-region UI exposes the canonical
  project, timeline, selected scene/MP4, final MP4, conversation, corrections,
  revisions, restore and stale-safe polling.
- Modules: Web UI.
- Blocked by: `web-service-api`.
- Specialists: `df-testing` test-author → test-audit → implementation →
  mutation/regression; design gate.
- Context: `.claude/contexts/web-ui/CONTEXT.md`,
  `.claude/tmp/contexts/video-flow-ui/web-ui.md`, `docs/DESIGN.md`.
- Inherited criteria: S6, AC6–AC7.

```json
{"requires_tdd":true,"planned_paths":["src/video_pipeline/web/service.py","src/video_pipeline/web/server.py","src/video_pipeline/revisions.py","src/video_pipeline/web/static/index.html","src/video_pipeline/web/static/app.css","src/video_pipeline/web/static/app.js","tests/test_web_ui.py","tests/integration/test_web_e2e.py","docs/DESIGN.md","README.md",".claude/tmp/test-audit-operator-ui.json"],"expected_outputs":["src/video_pipeline/web/service.py","src/video_pipeline/web/server.py","src/video_pipeline/revisions.py","src/video_pipeline/web/static/index.html","src/video_pipeline/web/static/app.css","src/video_pipeline/web/static/app.js","tests/test_web_ui.py","tests/integration/test_web_e2e.py","docs/DESIGN.md","README.md",".claude/tmp/test-audit-operator-ui.json"],"expects_changes":true,"requires_diff_coverage":true,"test_paths":["tests/test_web_ui.py","tests/integration/test_web_e2e.py"],"red_failure_signature":"OPERATOR_UI_CONTRACT_MISSING","blind_test_authorship":false,"test_author":"df-testing:test-author:operator-ui","blind_roles":{"test_author":"df-testing:test-author:operator-ui","implementer":"gpt-5.6-luna:max:operator-ui","test_auditor":"sol-high:test-auditor:operator-ui"},"implementation_plan_paths":[],"permitted_context_paths":["docs/prd/PRD-video-flow-ui.md","docs/prd/ISSUES-video-flow-ui.md","docs/DESIGN.md",".claude/contexts/web-ui/CONTEXT.md"],"blind_attestation":{"status":"not-applicable","implementation_plan_paths_intersection":[]},"requires_test_audit":true,"required_mutation_kills":1,"mutation_probes":[{"mutation_id":"stale-job-guard","path":"src/video_pipeline/web/static/app.js","before":"if (token !== state.pollToken) return;","after":"if (false) return;","behavior_command":"ui-green"}],"change_scope":{"allowed_globs":["src/video_pipeline/web/service.py","src/video_pipeline/web/server.py","src/video_pipeline/revisions.py","src/video_pipeline/web/static/index.html","src/video_pipeline/web/static/app.css","src/video_pipeline/web/static/app.js","tests/test_web_ui.py","tests/integration/test_web_e2e.py","docs/DESIGN.md","README.md"],"generated_globs":[".claude/tmp/test-audit-operator-ui.json"],"optional_globs":["src/video_pipeline/web/__init__.py"],"bundles":[{"id":"ui-adapters","globs":["src/video_pipeline/web/service.py","src/video_pipeline/web/server.py","src/video_pipeline/revisions.py"]},{"id":"ui-assets","globs":["src/video_pipeline/web/static/index.html","src/video_pipeline/web/static/app.css","src/video_pipeline/web/static/app.js"]},{"id":"ui-tests","globs":["tests/test_web_ui.py","tests/integration/test_web_e2e.py"]},{"id":"ui-docs","globs":["docs/DESIGN.md","README.md"]},{"id":"ui-audit","globs":[".claude/tmp/test-audit-operator-ui.json"]}]},"active_antipattern_ids":[],"evidence_commands":{"red":{"command_id":"ui-red","kind":"red","argv":[".venv/bin/python","-m","pytest","-q","tests/test_web_ui.py","tests/integration/test_web_e2e.py"],"timeout":180,"max_output_bytes":80000,"test_paths":["tests/test_web_ui.py","tests/integration/test_web_e2e.py"],"red_failure_signature":"OPERATOR_UI_CONTRACT_MISSING"},"test-audit":{"command_id":"ui-test-audit","kind":"test-audit","argv":[".venv/bin/python","-m","pytest","-q","tests/test_web_ui.py","tests/integration/test_web_e2e.py"],"timeout":180,"max_output_bytes":80000,"test_paths":["tests/test_web_ui.py","tests/integration/test_web_e2e.py"]},"green":{"command_id":"ui-green","kind":"green","argv":[".venv/bin/python","-m","pytest","-q","tests/test_web_ui.py","tests/integration/test_web_e2e.py"],"timeout":180,"max_output_bytes":80000,"test_paths":["tests/test_web_ui.py","tests/integration/test_web_e2e.py"]},"browser-preflight":{"command_id":"ui-browser-preflight","kind":"green","argv":["bash","-lc","firefox --version && geckodriver --version"],"timeout":30,"max_output_bytes":20000,"test_paths":[]},"design-selftest":{"command_id":"ui-design-selftest","kind":"green","argv":["bash","-lc","set -euo pipefail; source .claude/tmp/gates.env; bash \"$DF_SKILL_DESIGN/scripts/selftest.sh\""],"timeout":180,"max_output_bytes":60000,"test_paths":[]},"design-runtime":{"command_id":"ui-design-runtime","kind":"green","argv":["bash","-lc","set -euo pipefail; source .claude/tmp/gates.env; .venv/bin/video-pipeline web --port 8766 & server_pid=$!; trap 'kill $server_pid' EXIT; for n in {1..30}; do curl -fsS http://127.0.0.1:8766/ >/dev/null && break; sleep 0.2; done; \"$DF_GATE_DESIGN\" --url http://127.0.0.1:8766/ --src src/video_pipeline/web/static --tokens src/video_pipeline/web/static/app.css --mode refinement --states-complete"],"timeout":240,"max_output_bytes":100000,"test_paths":[]}},"required_gates":["green","browser-preflight","design-selftest","design-runtime"],"required_gate_evidence_ids":["ui-green","ui-browser-preflight","ui-design-selftest","ui-design-runtime"],"required_output_fields":["summary","coverage_manifest","mutation","gate_matrix","context_delta"]}
```

## 6 — delivery

- Tracker: local delivery lifecycle task; it adds no product behavior.
- Behavior: prove the integrated checkout with sandbox, build, installed command
  smoke, browser/design gates, materialized diff and final conclave.
- Modules: Project foundation; Render pipeline; Web UI.
- Blocked by: `progress-events`, `selective-revisions`, `web-service-api`,
  `operator-ui`.
- Specialists: `df-quality`; host gate runner; final review.
- Context: all persistent/run context plus PRD, issues, design and dossier.
- Inherited criteria: AC9.

```json
{"requires_tdd":false,"planned_paths":["pyproject.toml","uv.lock",".claude/scripts/sandbox.sh",".claude/BOOTSTRAP.md",".claude/contexts/render-pipeline/CONTEXT.md",".claude/contexts/web-ui/CONTEXT.md",".claude/skills/CONTEXT-MAP.MD",".claude/skills/darkagent/PR-TEMPLATE.md",".claude/skills/darkagent/SKILL.md",".claude/skills/df-architecture/SKILL.md",".claude/skills/df-frontend/SKILL.md",".claude/skills/df-python/SKILL.md",".claude/skills/df-quality/SKILL.md",".claude/skills/df-testing/SKILL.md","docs/prd/PRD-video-flow-ui.md","docs/prd/ISSUES-video-flow-ui.md","docs/DESIGN.md",".claude/tmp/dossier-video-flow-ui.md",".claude/tmp/ralph-state-video-flow-ui.json",".claude/tmp/observed.diff"],"expected_outputs":[".claude/BOOTSTRAP.md",".claude/contexts/render-pipeline/CONTEXT.md",".claude/contexts/web-ui/CONTEXT.md",".claude/skills/CONTEXT-MAP.MD",".claude/skills/darkagent/PR-TEMPLATE.md",".claude/skills/darkagent/SKILL.md",".claude/skills/df-architecture/SKILL.md",".claude/skills/df-frontend/SKILL.md",".claude/skills/df-python/SKILL.md",".claude/skills/df-quality/SKILL.md",".claude/skills/df-testing/SKILL.md","docs/prd/PRD-video-flow-ui.md","docs/prd/ISSUES-video-flow-ui.md","dist/video_pipeline-0.1.0-py3-none-any.whl",".claude/tmp/dossier-video-flow-ui.md",".claude/tmp/ralph-state-video-flow-ui.json",".claude/tmp/observed.diff"],"expects_changes":false,"requires_diff_coverage":false,"test_paths":["tests"],"red_failure_signature":"not-applicable","blind_test_authorship":false,"test_author":"none","blind_roles":{"test_author":"none","implementer":"df-quality:delivery-video-flow-ui","test_auditor":"none"},"implementation_plan_paths":[],"permitted_context_paths":["docs/prd/PRD-video-flow-ui.md","docs/prd/ISSUES-video-flow-ui.md"],"blind_attestation":{"status":"not-applicable","implementation_plan_paths_intersection":[]},"requires_test_audit":false,"required_mutation_kills":0,"mutation_probes":[],"change_scope":{"allowed_globs":[".claude/BOOTSTRAP.md",".claude/contexts/render-pipeline/CONTEXT.md",".claude/contexts/web-ui/CONTEXT.md",".claude/skills/CONTEXT-MAP.MD",".claude/skills/darkagent/PR-TEMPLATE.md",".claude/skills/darkagent/SKILL.md",".claude/skills/df-architecture/SKILL.md",".claude/skills/df-frontend/SKILL.md",".claude/skills/df-python/SKILL.md",".claude/skills/df-quality/SKILL.md",".claude/skills/df-testing/SKILL.md","docs/prd/PRD-video-flow-ui.md","docs/prd/ISSUES-video-flow-ui.md"],"generated_globs":["dist/video_pipeline-0.1.0-py3-none-any.whl",".claude/tmp/dossier-video-flow-ui.md",".claude/tmp/ralph-state-video-flow-ui.json",".claude/tmp/observed.diff"],"optional_globs":[],"bundles":[{"id":"delivery-workflow","globs":[".claude/BOOTSTRAP.md",".claude/contexts/render-pipeline/CONTEXT.md",".claude/contexts/web-ui/CONTEXT.md",".claude/skills/CONTEXT-MAP.MD",".claude/skills/darkagent/PR-TEMPLATE.md",".claude/skills/darkagent/SKILL.md",".claude/skills/df-architecture/SKILL.md",".claude/skills/df-frontend/SKILL.md",".claude/skills/df-python/SKILL.md",".claude/skills/df-quality/SKILL.md",".claude/skills/df-testing/SKILL.md","docs/prd/PRD-video-flow-ui.md","docs/prd/ISSUES-video-flow-ui.md"]},{"id":"delivery-build","globs":["dist/video_pipeline-0.1.0-py3-none-any.whl"]},{"id":"delivery-review","globs":[".claude/tmp/dossier-video-flow-ui.md",".claude/tmp/ralph-state-video-flow-ui.json",".claude/tmp/observed.diff"]}]},"active_antipattern_ids":[],"evidence_commands":{"green":{"command_id":"delivery-green","kind":"green","argv":["bash",".claude/scripts/sandbox.sh"],"timeout":300,"max_output_bytes":120000,"test_paths":["tests"]},"browser-preflight":{"command_id":"browser-preflight","kind":"green","argv":["bash","-lc","firefox --version && geckodriver --version"],"timeout":30,"max_output_bytes":20000,"test_paths":[]},"build":{"command_id":"build","kind":"green","argv":["uv","build","--wheel"],"timeout":180,"max_output_bytes":50000,"test_paths":[]},"wheel-smoke":{"command_id":"wheel-smoke","kind":"green","argv":["bash","-lc","set -euo pipefail; env_dir=$(mktemp -d); python3 -m venv $env_dir; $env_dir/bin/pip install dist/video_pipeline-0.1.0-py3-none-any.whl; test -f $env_dir/lib/python3.13/site-packages/video_pipeline/web/static/index.html; $env_dir/bin/video-pipeline web --help"],"timeout":180,"max_output_bytes":60000,"test_paths":[]},"design-selftest":{"command_id":"design-selftest","kind":"green","argv":["bash","-lc","set -euo pipefail; source .claude/tmp/gates.env; bash \"$DF_SKILL_DESIGN/scripts/selftest.sh\""],"timeout":180,"max_output_bytes":60000,"test_paths":[]},"design-runtime":{"command_id":"design-runtime","kind":"green","argv":["bash","-lc","set -euo pipefail; source .claude/tmp/gates.env; .venv/bin/video-pipeline web --port 8766 & server_pid=$!; trap 'kill $server_pid' EXIT; for n in {1..30}; do curl -fsS http://127.0.0.1:8766/ >/dev/null && break; sleep 0.2; done; \"$DF_GATE_DESIGN\" --url http://127.0.0.1:8766/ --src src/video_pipeline/web/static --tokens src/video_pipeline/web/static/app.css"],"timeout":240,"max_output_bytes":100000,"test_paths":[]},"conclave":{"command_id":"conclave","kind":"green","argv":["bash","-lc","set -euo pipefail; source .claude/tmp/gates.env; \"$DF_GATE_CONCLAVE\" --job pr-local-video-flow-ui --dossier .claude/tmp/dossier-video-flow-ui.md --prd docs/prd/PRD-video-flow-ui.md --ralph-state .claude/tmp/ralph-state-video-flow-ui.json --diff .claude/tmp/observed.diff --source-root . --base-root .claude/tmp/base-tree-video-flow-ui --require-findings"],"timeout":1800,"max_output_bytes":120000,"test_paths":[]},"gate-resolve":{"command_id":"gate-resolve","kind":"green","argv":["bash","-lc","set -euo pipefail; source .claude/tmp/gates.env; test -x \"$DF_SKILL_DESIGN\"; test -x \"$DF_GATE_DESIGN\"; test -x \"$DF_GATE_CONCLAVE\""],"timeout":30,"max_output_bytes":20000,"test_paths":[]}},"required_gates":["green","browser-preflight","gate-resolve","build","wheel-smoke","design-selftest","design-runtime"],"required_gate_evidence_ids":["delivery-green","browser-preflight","gate-resolve","build","wheel-smoke","design-selftest","design-runtime"],"required_output_fields":["summary","gate_matrix","pending_gates","unvalidated_paths"]}
```

## Rebase strategy (recoverable; do not execute here)

1. Snapshot the working tree and create a named stash that includes tracked and
   untracked UI docs/assets; record the pre-rebase HEAD and `git diff` hashes.
2. Require fast-forward-only movement to
   `origin/feat/render-in-the-loop-tracer@2fcc38f`; abort if the target or
   merge-base differs. The fast-forward is expected to remove
   `.claude/skills/darkagent/*` and `scripts/orchestrator.py`; do not resurrect
   those removed local copies without a new requirement.
3. After the fast-forward, execution and supervision use the canonical,
   environment-owned Darkagent available there. Validate its adapter and Ralph
   before registering the five-task DAG; resolve `.claude` conflicts in favor of
   RTL contracts plus Web UI additions: `PROJECT/artifacts` for runs and
   `PROJECT/ui` for revisions/drafts.
4. Validate the target/base/HEAD hashes, full diff, path scope, JSON evidence
   payload equality, and the RTL regression gates before accepting anything.
5. Keep the named stash/snapshot until all verification is complete; only then
   may a human remove it. This document does not execute the rebase or delete
   recovery material.

## Supervision contract

All five lifecycle rows use Case 1: Ralph `GET /events` is the source. The wake
set is exactly `CHECKPOINT_REACHED`, `AGENT_FAILED`, `USER_INPUT_REQUIRED`, and
`RUN_COMPLETED`; terminal success is `RUN_COMPLETED` with
`next_action=respond_user`. Event-driven wake/termination is primary; polling
and timers are not success criteria. The adapter opens its only SSE
subscription before dispatch and stops at the first valid wake. A diagnostic
`GET /state` is allowed only after terminal non-zero adapter exit without a
valid wake, and cannot convert SSE failure into success.

## Preventive gate

Required because this plan changes pipeline behavior, crosses job/run state
transitions, and has trigger-dependent asset boundaries. Fresh dossier/config
and phase-one verdicts live under `.claude/tmp/issue-gate-video-flow-ui*`;
publication requires every configured lens to emit `ISSUE_GATE: GREEN`.
