# PRD — Render-in-the-Loop tracer bullet

## Problem

The repository describes an automated video pipeline but has no executable
path from a scene description to a renderer-verified video. An LLM can claim
that generated Manim code works without executing it, and a zero process exit
alone cannot prove that a usable MP4 exists. On this 16 GB host, naive overlap
between Ollama inference and Manim rendering can also exhaust memory.

## Outcome

`video-pipeline render scene.json` loads one strict Scene Spec, asks a
replaceable provider for Manim Community code, unloads the Ollama model, writes
the candidate to an isolated attempt directory, renders it with real Manim,
validates the resulting MP4 with ffprobe, and either reports `SUCCESS` with an
observable path or feeds the preserved failure back for a bounded correction.

## Scenarios

### S0 — Runtime compatibility is observed

The repository-local environment runs Manim Community 0.21.0 on Python 3.13.9
headlessly. A valid acceptance scene produces an H.264 MP4 at the requested
resolution/frame rate; a deliberate exception exposes exit 1 plus traceback;
a hard timeout terminates and produces no final MP4.

### S1 — Strict Scene Spec and replaceable provider

A valid `scene.json` with `schema_version`, `scene_name`, and `description`
loads into an immutable Scene Spec. Missing, extra, malformed, or unsafe scene
class names fail before provider or renderer work. `LLMProvider` accepts one
generation request; `OllamaProvider` calls the local non-streaming generate API
with configurable model/URL/timeouts, extracts plain or fenced Python, records
the raw response, and exposes an explicit unload operation using
`keep_alive: 0`.

### S2 — Real render result and independent MP4 validation

`ManimRunner` invokes `python -m manim render` with Cairo, MP4, explicit media
directory, 854x480, 15 fps, and a bounded timeout. It returns argv, exit code,
timeout state, stdout, stderr, elapsed time, and discovered final MP4 paths.
`RenderValidator` rejects missing, empty, unprobeable, zero-duration, or
non-video artifacts even when Manim exited zero.

### S3 — Bounded correction and observable terminal state

Each run and attempt has its own directory. Every attempt retains provider
request/response, prompt-relevant context, code, unload result, argv, stdout,
stderr, traceback-bearing diagnostics, validation, state, and MP4. The loop is
strictly sequential: provider generation and unload complete before Manim
starts; the provider is called again only after a failed render/validation.
The first valid render ends in `SUCCESS`; otherwise the prior code and complete
diagnostic are supplied for correction until the configurable attempt limit,
then `ATTEMPTS_EXHAUSTED` is returned with a non-zero CLI exit.

### S4 — Acceptance scene and real integration

The supplied acceptance spec says: “Mostre um círculo no centro. Depois
transforme-o em um quadrado e mova-o para a direita.” A deterministic provider
fixture proves the real Manim integration without requiring Ollama. When local
Ollama and Manim are available, the same CLI can exercise `OllamaProvider`; its
result is accepted only through the same render and validation gates.

## Contracts

### Scene Spec v1

```json
{
  "schema_version": "1.0",
  "scene_name": "AcceptanceScene",
  "description": "Mostre um círculo no centro. Depois transforme-o em um quadrado e mova-o para a direita."
}
```

- Unknown keys are rejected.
- `scene_name` matches `^[A-Z][A-Za-z0-9_]*$`.
- `description` is non-blank.

### Provider

- `LLMProvider.generate(request) -> ProviderResponse` is synchronous and
  replaceable.
- `LLMProvider.unload() -> UnloadResult` is called after every generation
  attempt and before every render.
- The correction request includes the original spec, exact previous code,
  executed argv, exit/timeout facts, stdout, stderr, and validator reasons.
- Ollama defaults to `qwen2.5-coder:7b`, but model and base URL are configurable.
- Ollama generation is non-streaming and uses `keep_alive: 0`; unload is also
  an explicit empty generation request with `keep_alive: 0`.

### Renderer and validation

- Runtime: Manim Community 0.21.0, Cairo, MP4, 854x480, 15 fps.
- Timeout is configurable and kills the complete subprocess group.
- Success requires both `render_result.exit_code == 0` and
  `validation.valid == true` for a newly observed attempt-local MP4.
- ffprobe must observe at least one video stream, positive width/height,
  positive duration, and positive file size.

### Workspace and state

- Default root: `artifacts/runs`; caller may override it.
- Run directories use a collision-resistant ID; attempts are `attempt-01`,
  `attempt-02`, and so on and are never overwritten.
- `run.json` is the read-only progress source and records one of:
  `attempting`, `correcting`, `success`, `provider_error`, or
  `attempts_exhausted`.
- JSON and text artifacts are UTF-8 and contain no model assertion that can
  override renderer/validator facts.

## Implementation decisions

- Pin Python `>=3.13,<3.14`, Manim Community `0.21.0`, and Pydantic `2.12.4`.
- Use a src-layout installable package and uv lockfile, adapting the sibling
  `ads4you` pytest/Ruff/mypy modular configuration.
- Use stdlib `urllib.request` for the narrow Ollama JSON boundary and stdlib
  `subprocess` for Manim/ffprobe; no provider SDK or general agent framework.
- Keep the application loop Ralph-like and deterministic, with the model
  limited to candidate generation/correction.
- Use dependency injection only at the provider, renderer, validator, clock/ID,
  and output-root seams required for deterministic tests.
- Preserve full outputs on disk; correction prompt formatting may bound the
  diagnostic included in model context without truncating stored evidence.
- Runtime decision history is in
  [ADR 0001](../adr/0001-manim-community-runtime.md).

## Test seams

- `load_scene_spec(path)` for strict JSON/schema boundaries.
- `LLMProvider`, `OllamaProvider`, and injectable URL opener for provider JSON,
  fenced code, timeout, and unload behavior.
- `ManimRunner.run(...)` with injectable subprocess function for argv,
  traceback, missing MP4, and timeout facts.
- `RenderValidator.validate(path)` with injectable ffprobe execution.
- `RenderPipeline.render(spec, max_attempts)` with fake provider/runner and real
  filesystem workspaces for ordering, preservation, retry, and exhaustion.
- `video_pipeline.cli.main(argv)` plus the installed `video-pipeline` command.
- A marked real-Manim integration test that produces and probes the acceptance
  MP4 when Manim is importable.

## Acceptance criteria

- **AC0:** The recorded local spike proves Manim version, exact command/flags,
  exit codes, final MP4 path/metadata, traceback behavior, and timeout behavior.
- **AC1:** Valid Scene Spec loads; invalid structure/name/content fails before
  any provider call.
- **AC2:** The Ollama adapter sends non-streaming JSON, supports correction
  context, returns extracted code/raw response, and unloads with
  `keep_alive: 0` before renderer invocation.
- **AC3:** The runner captures exact argv, stdout, stderr, exit code, traceback,
  timeout, elapsed duration, and attempt-local MP4 candidates.
- **AC4:** Exit zero with no valid MP4 is failure; ffprobe-confirmed positive
  video metadata is required for success.
- **AC5:** Happy path creates one preserved attempt and terminal `SUCCESS`.
- **AC6:** A deliberate first render failure feeds prior code plus diagnostics
  into one correction, produces a second isolated attempt, and can end in
  `SUCCESS`; exhaustion preserves every failed attempt and exits non-zero.
- **AC7:** Event ordering proves no Ollama/Manim overlap and that the model is
  reloaded only when correction is necessary.
- **AC8:** The acceptance fixture produces a real, independently probed MP4
  with Manim when available.

## Exclusions and unvalidated risks

- Excluded: OpenMontage, montage/composition, audio, subtitles, scripts,
  multiple scenes, visual-semantic scoring, RITL-DOC/RAG, fine-tuning, frontend,
  sandboxing arbitrary generated Python beyond the bounded local subprocess.
- Model semantic fidelity is not proven by render success; this tracer bullet
  proves functional rendering and artifact validity only.
- Generated Python remains trusted local execution in the MVP. Running
  untrusted remote prompts requires a future OS/container isolation decision.
- Live Ollama output quality depends on the installed model and host load; the
  deterministic real-Manim fixture remains the integration gate.

## Grilling coverage

| Decision | Preserved in |
|---|---|
| Manim Community 0.21.0 on Python 3.13 | Contracts, S0, ADR 0001, AC0 |
| src-layout uv/pytest/Ruff/mypy tooling | Implementation decisions, C1 evidence row |
| Strict Pydantic Scene Spec | S1, Contracts, AC1 |
| Replaceable provider and stdlib Ollama adapter | S1, Provider contract, AC2 |
| Default `qwen2.5-coder:7b` is configurable | Provider contract |
| Mandatory explicit model unload before render | S3, Provider contract, AC2, AC7 |
| Real subprocess render and independent ffprobe | S2, Renderer contract, AC3, AC4 |
| Full per-attempt preservation | S3, Workspace contract, AC5, AC6 |
| Bounded correction loop | S3, loop contract, AC6 |
| OpenMontage/audio/subtitles/multi-scene deferred | Exclusions |

## Prototype decision

`prototype_required: yes (completed)`

Runnable question: can Manim Community 0.21.0 under host Python 3.13.9 render
the acceptance motion headlessly and expose success, traceback, output-path,
and timeout facts? The commands and observations are recorded in
`.claude/tmp/spike-ritl/REPORT.md`. Runtime version, renderer argv, MP4 discovery,
timeout behavior, and validator metadata were blocked until this spike passed;
the prototype issue must accept that evidence before implementation tasks run.

## External dependency evidence

| Decision | URL | Version |
|---|---|---|
| Use Manim Community and its CLI/API | https://github.com/ManimCommunity/manim/tree/v0.21.0 | 0.21.0, tag `861cd4849b17db1db3515b531ffe80b297848f93` |
| Support host Python | https://raw.githubusercontent.com/ManimCommunity/manim/v0.21.0/pyproject.toml | Python 3.13.9 host; Manim requires Python >=3.11 |
| Treat ManimGL as incompatible reference-only runtime | https://github.com/3b1b/manim | revision `9d57bcf9edea2486f214e190931de2a5537f23c1`, consulted 2026-08-28 |
| Use renderer feedback and preserve execution logs | https://github.com/makefinks/manim-generator | revision `23d9865a0a511a5c3dffa15e5188c1a759a8754f`, consulted 2026-08-28 |
| Use bounded RITL correction, defer RITL-DOC | https://github.com/SuienS/manim-trainer | revision `44eeb77438313411ab5b3cf5a4756102ead6d89a`, consulted 2026-08-28 |
| Defer montage/composition scope | https://github.com/open-montage/OpenMontage | dated revision consulted 2026-08-28 |
| Unload Ollama immediately | https://docs.ollama.com/api/generate | Ollama 0.33.2 host behavior; docs consulted 2026-08-28 |
| Validate JSON with Pydantic | https://docs.pydantic.dev/2.12/ | 2.12.4 |
| Probe MP4 metadata | https://ffmpeg.org/ffprobe.html | ffprobe 6.1.1 host binary |

## Active antipattern coverage

The Ralph store query for module `Project foundation` and globs
`src/video_pipeline/**,tests/**,pyproject.toml,examples/**,README.md` returned
`{"items":[]}` on 2026-08-28. No active antipattern ID applies.

## Evidence plan

Each structured cell below is literal JSON and is copied unchanged into its
owning issue payload.

Audit correction: for C1-C3, the canonical issue payload runs one named
audit-only contract-inventory test in every declared test file. This gives
Ralph a real zero-exit pytest observation before implementation while the
independent closed-schema artifact owns the five substantive audit checks.
RED and GREEN continue to execute the complete behavioral test files.

| Claim | Path | Affordance | Owner slice | planned_paths | expected_outputs | expects_changes | requires_tdd | requires_diff_coverage | test_paths | red_failure_signature | blind_test_authorship | test_author | blind_roles | implementation_plan_paths | permitted_context_paths | blind_attestation | requires_test_audit | required_mutation_kills | mutation_probes | change_scope | active_antipattern_ids | required_gates | required_gate_evidence_ids | required_output_fields | evidence_commands |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| C0 / AC0 — local Manim runtime is observable | Strong path: replay the repository-local pytest probe against the real spike MP4/report. Cheaper: version/import only. Fallback: rerun the three manual commands. Limitation: one host/runtime. | Durable spike report, scenes, MP4, and ffprobe regression test under `.claude/tmp/spike-ritl`. | `prototype-manim-runtime` | `[".claude/tmp/spike-ritl/**"]` | `[".claude/tmp/spike-ritl/REPORT.md",".claude/tmp/spike-ritl/media/success/videos/manual_scene/480p15/AcceptanceScene.mp4"]` | `false` | `false` | `false` | `[".claude/tmp/spike-ritl/test_spike.py"]` | `PROTOTYPE_RUNTIME_UNPROVEN` | `false` | `none` | `{"test_author":"none","implementer":"df-python:prototype-manim-runtime","test_auditor":"none"}` | `[".claude/tmp/spike-ritl/**"]` | `["docs/prd/PRD-render-in-the-loop-tracer.md","docs/adr/0001-manim-community-runtime.md"]` | `{"status":"not-applicable","implementation_plan_paths_intersection":[]}` | `false` | `0` | `[]` | `{"allowed_globs":[],"generated_globs":[],"optional_globs":[".claude/tmp/spike-ritl/**"],"bundles":[{"id":"prototype-evidence","globs":[".claude/tmp/spike-ritl/**"]}]}` | `[]` | `["green"]` | `["green"]` | `["summary","prototype_evidence","mutation"]` | `{"red":{"command_id":"red","kind":"red","argv":[".venv/bin/python","-m","pytest","-q",".claude/tmp/spike-ritl/test_spike.py"],"timeout":30,"max_output_bytes":20000,"test_paths":[".claude/tmp/spike-ritl/test_spike.py"],"red_failure_signature":"PROTOTYPE_RUNTIME_UNPROVEN"},"green":{"command_id":"green","kind":"green","argv":[".venv/bin/python","-m","pytest","-q",".claude/tmp/spike-ritl/test_spike.py"],"timeout":30,"max_output_bytes":20000,"test_paths":[".claude/tmp/spike-ritl/test_spike.py"]}}` |
| C1 / AC1–AC2 — strict spec and provider contract | Strong path: blind unit tests drive valid/invalid JSON, fenced code, correction body, HTTP/timeout errors, and unload payload through public seams. Cheaper: schema snapshots. Fallback: local Ollama smoke. Limitation: unit tests do not score model quality. | Injectable URL opener and immutable request/response records are durable production seams. | `scene-spec-provider` | `["pyproject.toml","uv.lock","src/video_pipeline/__init__.py","src/video_pipeline/spec.py","src/video_pipeline/provider.py","tests/test_scene_spec.py","tests/test_provider.py",".claude/tmp/test-audit-scene-spec-provider.json"]` | `["pyproject.toml","uv.lock","src/video_pipeline/__init__.py","src/video_pipeline/spec.py","src/video_pipeline/provider.py","tests/test_scene_spec.py","tests/test_provider.py",".claude/tmp/test-audit-scene-spec-provider.json"]` | `true` | `true` | `true` | `["tests/test_provider.py","tests/test_scene_spec.py"]` | `SCENE_PROVIDER_CONTRACT_MISSING` | `true` | `df-testing:test-author:scene-spec-provider` | `{"test_author":"df-testing:test-author:scene-spec-provider","implementer":"df-python:scene-spec-provider","test_auditor":"df-testing:test-auditor:scene-spec-provider"}` | `["pyproject.toml","src/video_pipeline/**"]` | `["docs/prd/PRD-render-in-the-loop-tracer.md","docs/adr/0001-manim-community-runtime.md"]` | `{"status":"pass","implementation_plan_paths_intersection":[]}` | `true` | `0` | `[]` | `{"allowed_globs":["pyproject.toml","uv.lock","src/video_pipeline/__init__.py","src/video_pipeline/spec.py","src/video_pipeline/provider.py","tests/test_scene_spec.py","tests/test_provider.py"],"generated_globs":[".claude/tmp/test-audit-scene-spec-provider.json"],"optional_globs":[],"bundles":[{"id":"scene-provider-source","globs":["pyproject.toml","uv.lock","src/video_pipeline/__init__.py","src/video_pipeline/spec.py","src/video_pipeline/provider.py"]},{"id":"scene-provider-tests","globs":["tests/test_provider.py","tests/test_scene_spec.py"]},{"id":"scene-provider-audit","globs":[".claude/tmp/test-audit-scene-spec-provider.json"]}]}` | `[]` | `["green"]` | `["green"]` | `["summary","coverage_manifest","mutation","context_delta"]` | `{"red":{"command_id":"red","kind":"red","argv":[".venv/bin/python","-m","pytest","-q","tests/test_scene_spec.py","tests/test_provider.py"],"timeout":60,"max_output_bytes":40000,"test_paths":["tests/test_provider.py","tests/test_scene_spec.py"],"red_failure_signature":"SCENE_PROVIDER_CONTRACT_MISSING"},"green":{"command_id":"green","kind":"green","argv":[".venv/bin/python","-m","pytest","-q","tests/test_scene_spec.py","tests/test_provider.py"],"timeout":60,"max_output_bytes":40000,"test_paths":["tests/test_provider.py","tests/test_scene_spec.py"]},"test-audit":{"command_id":"test-audit","kind":"test-audit","argv":[".venv/bin/python","-m","pytest","-q","tests/test_scene_spec.py","tests/test_provider.py"],"timeout":60,"max_output_bytes":40000,"test_paths":["tests/test_provider.py","tests/test_scene_spec.py"]}}` |
| C2 / AC3–AC4 — subprocess facts and valid MP4 gate | Strong path: blind unit tests execute controlled subprocess fixtures for success, nonzero exit, timeout, missing/empty/corrupt/zero-duration media, plus ffprobe JSON. Cheaper: mock return objects. Fallback: manual spike. Limitation: fixtures cannot cover every Manim codec failure. | Injectable subprocess call, process-group termination, explicit media root, and ffprobe seam are durable. | `render-validator-workspace` | `["src/video_pipeline/rendering.py","src/video_pipeline/validation.py","src/video_pipeline/workspace.py","tests/test_rendering.py","tests/test_validation.py","tests/test_workspace.py",".claude/tmp/test-audit-render-validator.json"]` | `["src/video_pipeline/rendering.py","src/video_pipeline/validation.py","src/video_pipeline/workspace.py","tests/test_rendering.py","tests/test_validation.py","tests/test_workspace.py",".claude/tmp/test-audit-render-validator.json"]` | `true` | `true` | `true` | `["tests/test_rendering.py","tests/test_validation.py","tests/test_workspace.py"]` | `RENDER_VALIDATOR_CONTRACT_MISSING` | `true` | `df-testing:test-author:render-validator-workspace` | `{"test_author":"df-testing:test-author:render-validator-workspace","implementer":"df-python:render-validator-workspace","test_auditor":"df-testing:test-auditor:render-validator-workspace"}` | `["src/video_pipeline/rendering.py","src/video_pipeline/validation.py","src/video_pipeline/workspace.py"]` | `["docs/prd/PRD-render-in-the-loop-tracer.md","docs/adr/0001-manim-community-runtime.md"]` | `{"status":"pass","implementation_plan_paths_intersection":[]}` | `true` | `1` | `[{"mutation_id":"validator-zero-duration","path":"src/video_pipeline/validation.py","before":"if duration_seconds <= 0:","after":"if duration_seconds < 0:","behavior_command":"green"}]` | `{"allowed_globs":["src/video_pipeline/rendering.py","src/video_pipeline/validation.py","src/video_pipeline/workspace.py","tests/test_rendering.py","tests/test_validation.py","tests/test_workspace.py"],"generated_globs":[".claude/tmp/test-audit-render-validator.json"],"optional_globs":[],"bundles":[{"id":"render-validator-source","globs":["src/video_pipeline/rendering.py","src/video_pipeline/validation.py","src/video_pipeline/workspace.py"]},{"id":"render-validator-tests","globs":["tests/test_rendering.py","tests/test_validation.py","tests/test_workspace.py"]},{"id":"render-validator-audit","globs":[".claude/tmp/test-audit-render-validator.json"]}]}` | `[]` | `["green"]` | `["green"]` | `["summary","coverage_manifest","mutation","context_delta"]` | `{"red":{"command_id":"red","kind":"red","argv":[".venv/bin/python","-m","pytest","-q","tests/test_rendering.py","tests/test_validation.py","tests/test_workspace.py"],"timeout":90,"max_output_bytes":50000,"test_paths":["tests/test_rendering.py","tests/test_validation.py","tests/test_workspace.py"],"red_failure_signature":"RENDER_VALIDATOR_CONTRACT_MISSING"},"green":{"command_id":"green","kind":"green","argv":[".venv/bin/python","-m","pytest","-q","tests/test_rendering.py","tests/test_validation.py","tests/test_workspace.py"],"timeout":90,"max_output_bytes":50000,"test_paths":["tests/test_rendering.py","tests/test_validation.py","tests/test_workspace.py"]},"test-audit":{"command_id":"test-audit","kind":"test-audit","argv":[".venv/bin/python","-m","pytest","-q","tests/test_rendering.py","tests/test_validation.py","tests/test_workspace.py"],"timeout":90,"max_output_bytes":50000,"test_paths":["tests/test_rendering.py","tests/test_validation.py","tests/test_workspace.py"]}}` |
| C3 / AC5–AC8 — full CLI loop, retry ordering, and real MP4 | Strong path: blind happy/retry/exhaustion/ordering CLI tests plus real Manim integration using deterministic generated code; final acceptance optionally uses live Ollama. Cheaper: fake-only loop. Fallback: invoke runner and validator manually. Limitation: render success does not prove semantic visual fidelity. | Injectable provider/runner/validator/ID/output root; durable run.json and attempt artifacts; integration marker. | `ritl-cli-integration` | `["src/video_pipeline/pipeline.py","src/video_pipeline/cli.py","src/video_pipeline/prompts.py","tests/test_pipeline.py","tests/test_cli.py","tests/integration/test_real_manim.py","examples/acceptance-scene.json","README.md",".gitignore",".claude/tmp/test-audit-ritl-cli.json","artifacts/runs/**"]` | `["src/video_pipeline/pipeline.py","src/video_pipeline/cli.py","src/video_pipeline/prompts.py","tests/test_pipeline.py","tests/test_cli.py","tests/integration/test_real_manim.py","examples/acceptance-scene.json","README.md",".gitignore",".claude/tmp/test-audit-ritl-cli.json"]` | `true` | `true` | `true` | `["tests/integration/test_real_manim.py","tests/test_cli.py","tests/test_pipeline.py"]` | `RITL_CLI_CONTRACT_MISSING` | `true` | `df-testing:test-author:ritl-cli-integration` | `{"test_author":"df-testing:test-author:ritl-cli-integration","implementer":"df-python:ritl-cli-integration","test_auditor":"df-testing:test-auditor:ritl-cli-integration"}` | `["src/video_pipeline/pipeline.py","src/video_pipeline/cli.py","src/video_pipeline/prompts.py"]` | `["docs/prd/PRD-render-in-the-loop-tracer.md","docs/adr/0001-manim-community-runtime.md"]` | `{"status":"pass","implementation_plan_paths_intersection":[]}` | `true` | `1` | `[{"mutation_id":"pipeline-requires-valid-mp4","path":"src/video_pipeline/pipeline.py","before":"if render_result.exit_code == 0 and validation.valid:","after":"if render_result.exit_code == 0:","behavior_command":"green"}]` | `{"allowed_globs":["src/video_pipeline/pipeline.py","src/video_pipeline/cli.py","src/video_pipeline/prompts.py","tests/test_pipeline.py","tests/test_cli.py","tests/integration/test_real_manim.py","examples/acceptance-scene.json","README.md",".gitignore"],"generated_globs":[".claude/tmp/test-audit-ritl-cli.json","artifacts/runs/**"],"optional_globs":[],"bundles":[{"id":"ritl-source","globs":["src/video_pipeline/pipeline.py","src/video_pipeline/cli.py","src/video_pipeline/prompts.py"]},{"id":"ritl-tests","globs":["tests/integration/test_real_manim.py","tests/test_cli.py","tests/test_pipeline.py"]},{"id":"ritl-docs","globs":["examples/acceptance-scene.json","README.md",".gitignore"]},{"id":"ritl-audit","globs":[".claude/tmp/test-audit-ritl-cli.json"]},{"id":"ritl-artifacts","globs":["artifacts/runs/**"]}]}` | `[]` | `["green"]` | `["green"]` | `["summary","coverage_manifest","mutation","context_delta"]` | `{"red":{"command_id":"red","kind":"red","argv":[".venv/bin/python","-m","pytest","-q","tests/test_pipeline.py","tests/test_cli.py","tests/integration/test_real_manim.py"],"timeout":240,"max_output_bytes":80000,"test_paths":["tests/integration/test_real_manim.py","tests/test_cli.py","tests/test_pipeline.py"],"red_failure_signature":"RITL_CLI_CONTRACT_MISSING"},"green":{"command_id":"green","kind":"green","argv":[".venv/bin/python","-m","pytest","-q","tests/test_pipeline.py","tests/test_cli.py","tests/integration/test_real_manim.py"],"timeout":240,"max_output_bytes":80000,"test_paths":["tests/integration/test_real_manim.py","tests/test_cli.py","tests/test_pipeline.py"]},"test-audit":{"command_id":"test-audit","kind":"test-audit","argv":[".venv/bin/python","-m","pytest","-q","tests/test_pipeline.py","tests/test_cli.py","tests/integration/test_real_manim.py"],"timeout":240,"max_output_bytes":80000,"test_paths":["tests/integration/test_real_manim.py","tests/test_cli.py","tests/test_pipeline.py"]}}` |
