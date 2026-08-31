# PRD — Video Flow UI MVP

## Baseline reconciliado

Esta especificação reconcilia `origin/feat/render-in-the-loop-tracer@2fcc38f`
com a UI em `adc0e42` (HEAD e merge-base observados). O RTL é a autoridade do
domínio: já entrega `Project`, `Timeline`, `VideoPipeline`, composição
audiovisual, inspeção, retomada e aceitação. A Web UI deve adaptá-los, não
substituí-los nem criar um segundo domínio.

| Contrato | Evidência no RTL | Classificação para a UI |
|---|---|---|
| Projeto com script, áudio, cenas e estados | `project.py:Project`, `initialize_project` | já entregue; reutilizar |
| Cenas e segmentação do roteiro | headings ou blocos; timeline explícita/pause-aligned/proporcional | já entregue; não recriar `Project`, `Timeline` ou segmentação |
| Timeline candidata e confirmação | `Timeline.status`, `confirm_project_timeline` | já entregue; expor revisão/confirmar |
| Progresso durável | `current_scene`, `action_next`, `run.json`, `inspect_project` | já entregue em nível projeto/cena |
| Retomada | `VideoPipeline.render` reabre o run atual e verifica/reutiliza cenas `ready` | já entregue para falha/interrupção |
| MP4 final audiovisual | `PROJECT/artifacts/<run-id>/composition.json` e `final.mp4` | já entregue; prévia principal |
| Snapshot aceito | `PROJECT/golden/accepted/<run-id>`, `golden.manifest/1` | já entregue; distinto de revisão da UI |
| Estágio fino de tentativa | não há `PipelineEvent`/`on_progress` | falta |
| Corrigir cena de run pronto | `--scene` não herda irmãos num run novo | falta |
| Revisões/branching da conversa | sem índice de revisões da UI | falta |
| Fila, HTTP seguro e UI | sem pacote `video_pipeline.web` | falta |

Baseline observado num archive de `2fcc38f`: `396 passed, 9 skipped,
16 deselected`, Ruff green e mypy green nos 13 módulos tipados do sandbox.

## Problem

O pipeline produz projetos audiovisuais completos, mas o operador ainda depende
do CLI bloqueante e de arquivos. Falta uma superfície navegável para criar/abrir
um `PROJECT` canônico, confirmar sua timeline, acompanhar geração,
pré-visualizar o `final.mp4` e as cenas, registrar uma correção dirigida,
comparar revisões e restaurar uma revisão anterior.

## Outcome

`video-pipeline web` inicia uma aplicação loopback-only. Ela adapta
`initialize_project`, `confirm_project_timeline`, `inspect_project`,
`VideoPipeline.render` e `accept_project`. O operador fornece título, roteiro e
um áudio identificado no catálogo local, vê as cenas canônicas, acompanha jobs
serializados, pré-visualiza o `final.mp4` e cada MP4 normalizado, regenera uma
cena canônica em novo run reutilizando irmãos verificados, navega por revisões
imutáveis da sessão e aceita explicitamente um run. O serviço não cria outro
`Project` nem recalcula a segmentação da timeline.

## Scenarios

### S1 — Projeto canônico

`POST /api/projects` recebe `title`, `script` e `audio_asset_id`. O serviço
resolve o áudio por ID sob raiz configurada, grava o roteiro UTF-8 em staging e
chama `initialize_project` para o `PROJECT` canônico. A resposta projeta
`inspect_project`, nunca paths do host. Headings/blocos, planos, briefs,
expectations, cenas e timeline continuam sob o domínio existente. Timeline
`candidate` aparece como revisão obrigatória e só uma ação explícita chama
`confirm_project_timeline`; render não a contorna.

### S2 — Progresso verdadeiro e fila serial

`run.json` e `inspect_project` permanecem a fonte durável. Um callback opcional
e tipado em `RenderPipeline`, propagado por `VideoPipeline`, informa apenas os
estágios finos atravessados: generating, unloading, rendering, validating,
observing/correcting e terminal. `queued` pertence ao job web. Uma fila FIFO
global com um worker impede sobreposição. Falha de callback não altera resultado
nem evidência.

### S3 — Regeneração seletiva de cena

Uma correção não edita o run-base. O serviço inicia novo run com `base_run_id`,
valida hashes das cenas prontas, reutiliza/copia atomicamente pacotes dos irmãos,
renderiza só a cena canônica selecionada com a correção registrada e
recompõe/valida novo `PROJECT/artifacts/<new-run-id>/final.mp4`. Irmãos
preservam hashes de código, mídia, normalização e evidência; o run anterior
permanece imutável. Planos/expectations continuam autoritativos: instrução
incompatível falha visivelmente, não apaga gates semânticos nem cria
segmentação paralela.

### S4 — Revisões da UI não são goldens

Após job inicial/regeneração terminal, sucesso ou falha, o serviço publica em
`PROJECT/ui/revisions/vNNN.json` uma revisão create-once com parent, base package
hashes, run ID, correção, mensagens e asset IDs. `current_revision_id` fica em
`PROJECT/ui/index.json`, atualizado atomicamente. Checkout move só esse ponteiro;
versões posteriores e `PROJECT/golden/accepted` ficam intocados. Editar após
restaurar `v1` cria o próximo número monotônico com parent `v1`.
`PROJECT/ui/working/<job-id>.json` não terminal achado após restart vira
`interrupted` e exige retry. Revisão da UI nunca é golden: somente `accept` pode
publicar o snapshot editorial em `PROJECT/golden/`.

### S5 — Prévia real e segura

O canvas principal usa `PROJECT/artifacts/<run-id>/final.mp4` validado quando o
run está `ready`. A rail de cenas oferece os `normalized.mp4` reais das cenas
canônicas e pode tocá-los em ordem enquanto o final não existe, rotulado “prévia
das cenas”. Assets são encontrados por IDs presentes na revisão/run e servidos
somente após `resolve(strict=True)` + `relative_to(resolved_root)`, inclusive
contra symlink escape. Single-range HTTP retorna 206.

### S6 — Operação acessível

Desktop: rail de revisões/projeto, canvas de status/vídeo/conversa e rail de
cenas canônicas da timeline. Em telas estreitas as regiões empilham sem scroll horizontal. Há um único
`role="log"`/`aria-live="polite"`, labels, foco visível, seleção ARIA e estado
não comunicado apenas por cor. Polling atrasado nunca substitui revisão/job novo.

### S7 — Falhas e aceitação

Falhas de provider, sensor, tentativas, normalização, composição e interrupção
mostram estado/diagnóstico preservado, sem destruir runs/revisões anteriores. A
UI não inventa cancelamento/cleanup. Só uma ação editorial explícita sobre run
`ready` chama `accept_project`; render/regeneração nunca promove candidatos.

## Contracts

### Domínio e progresso

- `Project`/`Timeline`/`ScenePlan`/`VideoPipeline` são canônicos; não criar outro
  `Project`, `projects.py`, segmentação, catálogo de cenas ou renderer.
- O serviço usa `video_pipeline.golden.accept_project`; não presume export top-level nem cria alias.
- O projeto e a timeline vivem em `PROJECT/project.json` e
  `PROJECT/timeline.json`; planos, briefs e expectations vivem em
  `PROJECT/scenes/<scene>/`. Runs e evidências vivem somente em
  `PROJECT/artifacts/<run-id>/`.
- `SceneSpec` usa `id`, `scene_name`, `description`, `plan`, `expect`, `topics`
  e `reference_examples`; não há `schema_version="1.0"` no contrato atual.
- `PipelineEvent` inclui run interno, tentativa one-based, estágio e estado;
  `ProjectPipelineEvent` acrescenta project run e scene ID.
- Callbacks são opcionais, ordenados e best-effort. Observation é
  `not_applicable` quando não executada.
- Regeneração de cena exige timeline confirmed, run-base `ready` do mesmo projeto e
  input/package hashes idênticos. Só a cena selecionada chama provider/Manim;
  irmãos são revalidados antes da reutilização.

### Revisões da UI

- IDs de projeto, revisão, cena, job, áudio e asset são validados. Documentos
  públicos usam IDs, nunca paths de runtime.
- Revisões: `PROJECT/ui/revisions/vNNN.json`, create-once. Índice mutável:
  `PROJECT/ui/index.json`. Drafts: `PROJECT/ui/working/<job-id>.json`.
- Uma revisão referencia `Project`, timeline, cenas e run preservados; não copia,
  substitui ou promove `PROJECT/project.json`, `PROJECT/timeline.json` ou
  `PROJECT/golden/manifest.json`.
- `PROJECT/ui` é o histórico da sessão e `PROJECT/golden` é o snapshot editorial;
  revision != golden, mesmo quando apontam para o mesmo run.
- Jobs são FIFO, transitórios, limitados; apenas um invoca `VideoPipeline`.

### HTTP e segurança

- Bind fixo `127.0.0.1`; estáticos package-owned; JSON em `/api/`; sem CORS.
- `GET /api/session` entrega CSRF process-scoped. Mutação exige Host loopback,
  Origin same-origin, JSON e header CSRF.
- `GET /api/audio` retorna IDs sob raiz configurada, sem paths.
- Mutations: criar projeto, confirmar timeline, enfileirar render, regenerar
  cena/revisão, checkout e aceitar run. Render/regenerate retornam 202 + job ID.
- Body até 1 MiB; script 50.000 chars; correção 5.000; projeto 20 cenas; fila 32.
  Áudio não trafega no JSON: usa `audio_asset_id` do catálogo seguro.
- MP4 aceita uma faixa (`206`, `Content-Range`, `Accept-Ranges`). Multi-range ou
  range malformado é rejeitado.
- Erros não contêm traceback, token, path arbitrário ou conteúdo de arquivo.

## Implementation decisions

- Rebasear UI sobre `2fcc38f`, preservando estes docs e mesclando contextos
  `.claude` conscientemente; não copiar código do checkout paralelo. A estratégia
  recuperável (stash/snapshot, fast-forward-only, resolução, validação e stash
  mantido até a verificação) está registrada nas Issues e não deve ser executada
  durante esta especificação.
- Adicionar só a observabilidade necessária a `pipeline.py`/`video.py`.
- Revisões são manifests da UI sobre runs canônicos; golden continua sendo o
  snapshot editorial do domínio.
- Estender `VideoPipeline` com base-run explícito; não criar renderer alternativo.
- Persistir dentro do `PROJECT`: usar `PROJECT/artifacts` para runs e
  `PROJECT/ui` para drafts/revisions/index. Não usar roots fora de `PROJECT`,
  diretórios globais ou um segundo root.
- Servidor stdlib e assets vanilla; nenhuma dependência shipping nova.

## Test seams

- Fakes de `RenderPipeline`/`VideoPipeline` provam evento fino, ordem, best-effort
  e compatibilidade sem substituir `run.json`.
- Fakes de `VideoPipeline` provam base-run, uma chamada de provider, reutilização
  por hash, composição nova e rejeição de base incompatível.
- Store em `tmp_path` prova create-once, checkout/branch, interrupção/atomicidade.
- Serviço/router real prova create → confirm → render → regenerate one canonical
  scene → checkout → accept, FIFO e não sobreposição.
- Firefox/geckodriver prova DOM, fetch, polling, correção, restore, playback e
  proteção stale.
- Sandbox RTL continua o gate de regressão; integração Manim não é duplicada.

## Acceptance criteria

- **AC1:** criação chama domínio canônico e impede render candidato até confirm.
- **AC2:** callbacks finos não substituem `run.json`; fila nunca sobrepõe renders.
- **AC3:** regenerar cena de run `ready` cria novo run em `PROJECT/artifacts`,
  recompõe novo `final.mp4`, chama provider/Manim
  só para ela e preserva hashes verificados dos irmãos.
- **AC4:** revisões são imutáveis; checkout preserva posteriores e branch parent.
- **AC5:** assets só por ID; traversal, symlink, Host/Origin/CSRF/content/range e
  inputs hostis são rejeitados.
- **AC6:** UI identifica projeto, timeline, run, revisão, cena, estágio,
  diagnóstico e MP4 em desktop/mobile acessível.
- **AC7:** browser real prova criação, confirmação, polling, correção seletiva,
  playback, restore e stale guard.
- **AC8:** falhas/interrupções são inspecionáveis; só accept publica golden.
- **AC9:** regressão RTL, novos testes, Ruff, mypy, sandbox, build/wheel, design
  gate e conclave ficam green ou com blocker explícito.

## Exclusions and risks

- Sem nova edição/segmentação de timeline, upload remoto, auth, colaboração, database,
  cancelamento, cleanup, frame-range patch ou deploy remoto.
- Catálogo de áudio é configurado no host; a UI nunca recebe path.
- Correção textual não reescreve plano/expectations; conflitos podem falhar.
- Jobs em memória não retomam; projetos, runs e revisões terminais sobrevivem.
- Python gerado preserva o risco local confiável existente.

## Active antipattern coverage

O query anterior não encontrou antipatterns ativos. Após o rebase, repeti-lo
para `src/video_pipeline/**` e `src/video_pipeline/web/**` antes do DAG.

## Evidence plan

Cada payload abaixo deve ser copiado literalmente para sua issue.

### D0 / AC3, AC5 — accept-contracts

Este pré-requisito registra as correções do backend real descobertas antes do
DAG da UI: rollback atômico de aceite, lineage seletiva estrita e capability
canônica declarada no init. Esses arquivos não são mudanças órfãs do C2.

```json
{"requires_tdd":true,"planned_paths":["src/video_pipeline/golden.py","src/video_pipeline/project.py","src/video_pipeline/timeline.py","tests/test_audiovisual_composition.py","tests/test_project_accept.py","tests/test_project_resume.py","tests/test_timeline_init.py",".claude/tmp/test-audit-accept-contracts.json"],"expected_outputs":["src/video_pipeline/golden.py","src/video_pipeline/project.py","src/video_pipeline/timeline.py","tests/test_audiovisual_composition.py","tests/test_project_accept.py","tests/test_project_resume.py","tests/test_timeline_init.py",".claude/tmp/test-audit-accept-contracts.json"],"expects_changes":true,"requires_diff_coverage":true,"test_paths":["tests/test_audiovisual_composition.py","tests/test_project_accept.py","tests/test_project_resume.py","tests/test_timeline_init.py"],"red_failure_signature":"ACCEPT_CONTRACTS_MISSING","blind_test_authorship":false,"test_author":"df-testing:test-author:accept-contracts","blind_roles":{"test_author":"df-testing:test-author:accept-contracts","implementer":"gpt-5.6-luna:max:accept-contracts","test_auditor":"sol-high:test-auditor:accept-contracts"},"implementation_plan_paths":[],"permitted_context_paths":["docs/prd/PRD-video-flow-ui.md","src/video_pipeline/golden.py","src/video_pipeline/project.py","src/video_pipeline/timeline.py"],"blind_attestation":{"status":"not-applicable","implementation_plan_paths_intersection":[]},"requires_test_audit":true,"required_mutation_kills":0,"mutation_probes":[],"change_scope":{"allowed_globs":["src/video_pipeline/golden.py","src/video_pipeline/project.py","src/video_pipeline/timeline.py","tests/test_audiovisual_composition.py","tests/test_project_accept.py","tests/test_project_resume.py","tests/test_timeline_init.py"],"generated_globs":[".claude/tmp/test-audit-accept-contracts.json"],"optional_globs":[],"bundles":[{"id":"accept-source","globs":["src/video_pipeline/golden.py","src/video_pipeline/project.py","src/video_pipeline/timeline.py"]},{"id":"accept-tests","globs":["tests/test_audiovisual_composition.py","tests/test_project_accept.py","tests/test_project_resume.py","tests/test_timeline_init.py"]},{"id":"accept-audit","globs":[".claude/tmp/test-audit-accept-contracts.json"]}]},"active_antipattern_ids":[],"evidence_commands":{"red":{"command_id":"accept-red","kind":"red","argv":[".venv/bin/python","-m","pytest","-q","tests/test_audiovisual_composition.py","tests/test_project_accept.py","tests/test_project_resume.py","tests/test_timeline_init.py"],"timeout":180,"max_output_bytes":80000,"test_paths":["tests/test_audiovisual_composition.py","tests/test_project_accept.py","tests/test_project_resume.py","tests/test_timeline_init.py"],"red_failure_signature":"ACCEPT_CONTRACTS_MISSING"},"test-audit":{"command_id":"accept-test-audit","kind":"test-audit","argv":[".venv/bin/python","-m","pytest","-q","tests/test_audiovisual_composition.py","tests/test_project_accept.py","tests/test_project_resume.py","tests/test_timeline_init.py"],"timeout":180,"max_output_bytes":80000,"test_paths":["tests/test_audiovisual_composition.py","tests/test_project_accept.py","tests/test_project_resume.py","tests/test_timeline_init.py"]},"green":{"command_id":"accept-green","kind":"green","argv":[".venv/bin/python","-m","pytest","-q","tests/test_audiovisual_composition.py","tests/test_project_accept.py","tests/test_project_resume.py","tests/test_timeline_init.py"],"timeout":180,"max_output_bytes":80000,"test_paths":["tests/test_audiovisual_composition.py","tests/test_project_accept.py","tests/test_project_resume.py","tests/test_timeline_init.py"]}},"required_gates":["green"],"required_gate_evidence_ids":["accept-green"],"required_output_fields":["summary","coverage_manifest","context_delta"]}
```

### C1 / AC2 — progress-events

```json
{"requires_tdd":true,"planned_paths":["src/video_pipeline/pipeline.py","src/video_pipeline/video.py","src/video_pipeline/__init__.py","tests/test_pipeline_progress.py",".claude/tmp/test-audit-pipeline-progress.json"],"expected_outputs":["src/video_pipeline/pipeline.py","src/video_pipeline/video.py","src/video_pipeline/__init__.py","tests/test_pipeline_progress.py",".claude/tmp/test-audit-pipeline-progress.json"],"expects_changes":true,"requires_diff_coverage":true,"test_paths":["tests/test_pipeline_progress.py"],"red_failure_signature":"PIPELINE_PROGRESS_CONTRACT_MISSING","blind_test_authorship":false,"test_author":"df-testing:test-author:progress-events","blind_roles":{"test_author":"df-testing:test-author:progress-events","implementer":"gpt-5.6-luna:max:progress-events","test_auditor":"sol-high:test-auditor:progress-events"},"implementation_plan_paths":[],"permitted_context_paths":["docs/prd/PRD-video-flow-ui.md","tests/test_pipeline.py","tests/test_project_lifecycle.py"],"blind_attestation":{"status":"not-applicable","implementation_plan_paths_intersection":[]},"requires_test_audit":true,"required_mutation_kills":1,"mutation_probes":[{"mutation_id":"progress-render-stage","path":"src/video_pipeline/pipeline.py","before":"PipelineStage.RENDERING","after":"PipelineStage.GENERATING","behavior_command":"progress-green"}],"change_scope":{"allowed_globs":["src/video_pipeline/pipeline.py","src/video_pipeline/video.py","src/video_pipeline/__init__.py","tests/test_pipeline_progress.py"],"generated_globs":[".claude/tmp/test-audit-pipeline-progress.json"],"optional_globs":[],"bundles":[{"id":"progress-source","globs":["src/video_pipeline/pipeline.py","src/video_pipeline/video.py","src/video_pipeline/__init__.py"]},{"id":"progress-tests","globs":["tests/test_pipeline_progress.py"]},{"id":"progress-audit","globs":[".claude/tmp/test-audit-pipeline-progress.json"]}]},"active_antipattern_ids":[],"evidence_commands":{"red":{"command_id":"progress-red","kind":"red","argv":[".venv/bin/python","-m","pytest","-q","tests/test_pipeline_progress.py"],"timeout":90,"max_output_bytes":40000,"test_paths":["tests/test_pipeline_progress.py"],"red_failure_signature":"PIPELINE_PROGRESS_CONTRACT_MISSING"},"test-audit":{"command_id":"progress-test-audit","kind":"test-audit","argv":[".venv/bin/python","-m","pytest","-q","tests/test_pipeline_progress.py"],"timeout":90,"max_output_bytes":40000,"test_paths":["tests/test_pipeline_progress.py"]},"green":{"command_id":"progress-green","kind":"green","argv":[".venv/bin/python","-m","pytest","-q","tests/test_pipeline_progress.py"],"timeout":90,"max_output_bytes":40000,"test_paths":["tests/test_pipeline_progress.py"]}},"required_gates":["green"],"required_gate_evidence_ids":["progress-green"],"required_output_fields":["summary","coverage_manifest","mutation","context_delta"]}
```

### C2 / AC3–AC4 — selective-revisions

```json
{"requires_tdd":true,"planned_paths":["src/video_pipeline/video.py","src/video_pipeline/revisions.py","src/video_pipeline/__init__.py","tests/test_selective_regeneration.py","tests/test_project_revisions.py",".claude/tmp/test-audit-selective-revisions.json"],"expected_outputs":["src/video_pipeline/video.py","src/video_pipeline/revisions.py","src/video_pipeline/__init__.py","tests/test_selective_regeneration.py","tests/test_project_revisions.py",".claude/tmp/test-audit-selective-revisions.json"],"expects_changes":true,"requires_diff_coverage":true,"test_paths":["tests/test_selective_regeneration.py","tests/test_project_revisions.py"],"red_failure_signature":"SELECTIVE_REVISION_CONTRACT_MISSING","blind_test_authorship":false,"test_author":"df-testing:test-author:selective-revisions","blind_roles":{"test_author":"df-testing:test-author:selective-revisions","implementer":"gpt-5.6-luna:max:selective-revisions","test_auditor":"sol-high:test-auditor:selective-revisions"},"implementation_plan_paths":[],"permitted_context_paths":["docs/prd/PRD-video-flow-ui.md","src/video_pipeline/video.py","tests/test_project_lifecycle.py","tests/test_project_accept.py"],"blind_attestation":{"status":"not-applicable","implementation_plan_paths_intersection":[]},"requires_test_audit":true,"required_mutation_kills":2,"mutation_probes":[{"mutation_id":"base-run-project-check","path":"src/video_pipeline/video.py","before":"run_document.get(\"project_id\") != project.id","after":"False","behavior_command":"selective-green"},{"mutation_id":"revision-parent","path":"src/video_pipeline/revisions.py","before":"parent_revision_id=base.revision_id","after":"parent_revision_id=None","behavior_command":"selective-green"}],"change_scope":{"allowed_globs":["src/video_pipeline/video.py","src/video_pipeline/revisions.py","src/video_pipeline/__init__.py","tests/test_selective_regeneration.py","tests/test_project_revisions.py"],"generated_globs":[".claude/tmp/test-audit-selective-revisions.json"],"optional_globs":[],"bundles":[{"id":"selective-source","globs":["src/video_pipeline/video.py","src/video_pipeline/revisions.py","src/video_pipeline/__init__.py"]},{"id":"selective-tests","globs":["tests/test_selective_regeneration.py","tests/test_project_revisions.py"]},{"id":"selective-audit","globs":[".claude/tmp/test-audit-selective-revisions.json"]}]},"active_antipattern_ids":[],"evidence_commands":{"red":{"command_id":"selective-red","kind":"red","argv":[".venv/bin/python","-m","pytest","-q","tests/test_selective_regeneration.py","tests/test_project_revisions.py"],"timeout":120,"max_output_bytes":60000,"test_paths":["tests/test_selective_regeneration.py","tests/test_project_revisions.py"],"red_failure_signature":"SELECTIVE_REVISION_CONTRACT_MISSING"},"test-audit":{"command_id":"selective-test-audit","kind":"test-audit","argv":[".venv/bin/python","-m","pytest","-q","tests/test_selective_regeneration.py","tests/test_project_revisions.py"],"timeout":120,"max_output_bytes":60000,"test_paths":["tests/test_selective_regeneration.py","tests/test_project_revisions.py"]},"green":{"command_id":"selective-green","kind":"green","argv":[".venv/bin/python","-m","pytest","-q","tests/test_selective_regeneration.py","tests/test_project_revisions.py"],"timeout":120,"max_output_bytes":60000,"test_paths":["tests/test_selective_regeneration.py","tests/test_project_revisions.py"]}},"required_gates":["green"],"required_gate_evidence_ids":["selective-green"],"required_output_fields":["summary","coverage_manifest","mutation","context_delta"]}
```

### C3 / AC1, AC5, AC8 — web-service-api

```json
{"requires_tdd":true,"planned_paths":["src/video_pipeline/web/__init__.py","src/video_pipeline/web/service.py","src/video_pipeline/web/server.py","src/video_pipeline/web/limits.py","src/video_pipeline/cli.py","tests/test_web_service.py","tests/test_web_api.py","tests/integration/test_web_flow.py",".claude/tmp/test-audit-web-service.json"],"expected_outputs":["src/video_pipeline/web/__init__.py","src/video_pipeline/web/service.py","src/video_pipeline/web/server.py","src/video_pipeline/web/limits.py","src/video_pipeline/cli.py","tests/test_web_service.py","tests/test_web_api.py","tests/integration/test_web_flow.py",".claude/tmp/test-audit-web-service.json"],"expects_changes":true,"requires_diff_coverage":true,"test_paths":["tests/test_web_service.py","tests/test_web_api.py","tests/integration/test_web_flow.py"],"red_failure_signature":"WEB_CANONICAL_SERVICE_CONTRACT_MISSING","blind_test_authorship":false,"test_author":"df-testing:test-author:web-service-api","blind_roles":{"test_author":"df-testing:test-author:web-service-api","implementer":"gpt-5.6-luna:max:web-service-api","test_auditor":"sol-high:test-auditor:web-service-api"},"implementation_plan_paths":[],"permitted_context_paths":["docs/prd/PRD-video-flow-ui.md","src/video_pipeline/project.py","src/video_pipeline/video.py","src/video_pipeline/revisions.py"],"blind_attestation":{"status":"not-applicable","implementation_plan_paths_intersection":[]},"requires_test_audit":true,"required_mutation_kills":2,"mutation_probes":[{"mutation_id":"single-worker","path":"src/video_pipeline/web/service.py","before":"max_workers=1","after":"max_workers=2","behavior_command":"web-green"},{"mutation_id":"asset-relative-root","path":"src/video_pipeline/web/server.py","before":"candidate.relative_to(root)","after":"candidate","behavior_command":"web-green"}],"change_scope":{"allowed_globs":["src/video_pipeline/web/__init__.py","src/video_pipeline/web/service.py","src/video_pipeline/web/server.py","src/video_pipeline/web/limits.py","src/video_pipeline/cli.py","tests/test_web_service.py","tests/test_web_api.py","tests/integration/test_web_flow.py"],"generated_globs":[".claude/tmp/test-audit-web-service.json"],"optional_globs":[],"bundles":[{"id":"web-source","globs":["src/video_pipeline/web/__init__.py","src/video_pipeline/web/service.py","src/video_pipeline/web/server.py","src/video_pipeline/web/limits.py","src/video_pipeline/cli.py"]},{"id":"web-tests","globs":["tests/test_web_service.py","tests/test_web_api.py","tests/integration/test_web_flow.py"]},{"id":"web-audit","globs":[".claude/tmp/test-audit-web-service.json"]}]},"active_antipattern_ids":[],"evidence_commands":{"red":{"command_id":"web-red","kind":"red","argv":[".venv/bin/python","-m","pytest","-q","tests/test_web_service.py","tests/test_web_api.py","tests/integration/test_web_flow.py"],"timeout":180,"max_output_bytes":80000,"test_paths":["tests/test_web_service.py","tests/test_web_api.py","tests/integration/test_web_flow.py"],"red_failure_signature":"WEB_CANONICAL_SERVICE_CONTRACT_MISSING"},"test-audit":{"command_id":"web-test-audit","kind":"test-audit","argv":[".venv/bin/python","-m","pytest","-q","tests/test_web_service.py","tests/test_web_api.py","tests/integration/test_web_flow.py"],"timeout":180,"max_output_bytes":80000,"test_paths":["tests/test_web_service.py","tests/test_web_api.py","tests/integration/test_web_flow.py"]},"green":{"command_id":"web-green","kind":"green","argv":[".venv/bin/python","-m","pytest","-q","tests/test_web_service.py","tests/test_web_api.py","tests/integration/test_web_flow.py"],"timeout":180,"max_output_bytes":80000,"test_paths":["tests/test_web_service.py","tests/test_web_api.py","tests/integration/test_web_flow.py"]}},"required_gates":["green"],"required_gate_evidence_ids":["web-green"],"required_output_fields":["summary","coverage_manifest","mutation","context_delta"]}
```

### C4 / AC6–AC7 — operator-ui

```json
{"requires_tdd":true,"planned_paths":["src/video_pipeline/web/service.py","src/video_pipeline/web/server.py","src/video_pipeline/revisions.py","src/video_pipeline/web/static/index.html","src/video_pipeline/web/static/app.css","src/video_pipeline/web/static/app.js","tests/test_web_ui.py","tests/integration/test_web_e2e.py","docs/DESIGN.md","README.md",".claude/tmp/test-audit-operator-ui.json"],"expected_outputs":["src/video_pipeline/web/service.py","src/video_pipeline/web/server.py","src/video_pipeline/revisions.py","src/video_pipeline/web/static/index.html","src/video_pipeline/web/static/app.css","src/video_pipeline/web/static/app.js","tests/test_web_ui.py","tests/integration/test_web_e2e.py","docs/DESIGN.md","README.md",".claude/tmp/test-audit-operator-ui.json"],"expects_changes":true,"requires_diff_coverage":true,"test_paths":["tests/test_web_ui.py","tests/integration/test_web_e2e.py"],"red_failure_signature":"OPERATOR_UI_CONTRACT_MISSING","blind_test_authorship":false,"test_author":"df-testing:test-author:operator-ui","blind_roles":{"test_author":"df-testing:test-author:operator-ui","implementer":"gpt-5.6-luna:max:operator-ui","test_auditor":"sol-high:test-auditor:operator-ui"},"implementation_plan_paths":[],"permitted_context_paths":["docs/prd/PRD-video-flow-ui.md","docs/prd/ISSUES-video-flow-ui.md","docs/DESIGN.md",".claude/contexts/web-ui/CONTEXT.md"],"blind_attestation":{"status":"not-applicable","implementation_plan_paths_intersection":[]},"requires_test_audit":true,"required_mutation_kills":1,"mutation_probes":[{"mutation_id":"stale-job-guard","path":"src/video_pipeline/web/static/app.js","before":"if (token !== state.pollToken) return;","after":"if (false) return;","behavior_command":"ui-green"}],"change_scope":{"allowed_globs":["src/video_pipeline/web/service.py","src/video_pipeline/web/server.py","src/video_pipeline/revisions.py","src/video_pipeline/web/static/index.html","src/video_pipeline/web/static/app.css","src/video_pipeline/web/static/app.js","tests/test_web_ui.py","tests/integration/test_web_e2e.py","docs/DESIGN.md","README.md"],"generated_globs":[".claude/tmp/test-audit-operator-ui.json"],"optional_globs":["src/video_pipeline/web/__init__.py"],"bundles":[{"id":"ui-adapters","globs":["src/video_pipeline/web/service.py","src/video_pipeline/web/server.py","src/video_pipeline/revisions.py"]},{"id":"ui-assets","globs":["src/video_pipeline/web/static/index.html","src/video_pipeline/web/static/app.css","src/video_pipeline/web/static/app.js"]},{"id":"ui-tests","globs":["tests/test_web_ui.py","tests/integration/test_web_e2e.py"]},{"id":"ui-docs","globs":["docs/DESIGN.md","README.md"]},{"id":"ui-audit","globs":[".claude/tmp/test-audit-operator-ui.json"]}]},"active_antipattern_ids":[],"evidence_commands":{"red":{"command_id":"ui-red","kind":"red","argv":[".venv/bin/python","-m","pytest","-q","tests/test_web_ui.py","tests/integration/test_web_e2e.py"],"timeout":180,"max_output_bytes":80000,"test_paths":["tests/test_web_ui.py","tests/integration/test_web_e2e.py"],"red_failure_signature":"OPERATOR_UI_CONTRACT_MISSING"},"test-audit":{"command_id":"ui-test-audit","kind":"test-audit","argv":[".venv/bin/python","-m","pytest","-q","tests/test_web_ui.py","tests/integration/test_web_e2e.py"],"timeout":180,"max_output_bytes":80000,"test_paths":["tests/test_web_ui.py","tests/integration/test_web_e2e.py"]},"green":{"command_id":"ui-green","kind":"green","argv":[".venv/bin/python","-m","pytest","-q","tests/test_web_ui.py","tests/integration/test_web_e2e.py"],"timeout":180,"max_output_bytes":80000,"test_paths":["tests/test_web_ui.py","tests/integration/test_web_e2e.py"]},"browser-preflight":{"command_id":"ui-browser-preflight","kind":"green","argv":["bash","-lc","firefox --version && geckodriver --version"],"timeout":30,"max_output_bytes":20000,"test_paths":[]},"design-selftest":{"command_id":"ui-design-selftest","kind":"green","argv":["bash","-lc","set -euo pipefail; source .claude/tmp/gates.env; bash \"$DF_SKILL_DESIGN/scripts/selftest.sh\""],"timeout":180,"max_output_bytes":60000,"test_paths":[]},"design-runtime":{"command_id":"ui-design-runtime","kind":"green","argv":["bash","-lc","set -euo pipefail; source .claude/tmp/gates.env; .venv/bin/video-pipeline web --port 8766 & server_pid=$!; trap 'kill $server_pid' EXIT; for n in {1..30}; do curl -fsS http://127.0.0.1:8766/ >/dev/null && break; sleep 0.2; done; \"$DF_GATE_DESIGN\" --url http://127.0.0.1:8766/ --src src/video_pipeline/web/static --tokens src/video_pipeline/web/static/app.css --mode refinement --states-complete"],"timeout":240,"max_output_bytes":100000,"test_paths":[]}},"required_gates":["green","browser-preflight","design-selftest","design-runtime"],"required_gate_evidence_ids":["ui-green","ui-browser-preflight","ui-design-selftest","ui-design-runtime"],"required_output_fields":["summary","coverage_manifest","mutation","gate_matrix","context_delta"]}
```

### C5 / AC9 — delivery

```json
{"requires_tdd":false,"planned_paths":["pyproject.toml","uv.lock",".claude/scripts/sandbox.sh",".claude/BOOTSTRAP.md",".claude/contexts/render-pipeline/CONTEXT.md",".claude/contexts/web-ui/CONTEXT.md",".claude/skills/CONTEXT-MAP.MD",".claude/skills/darkagent/PR-TEMPLATE.md",".claude/skills/darkagent/SKILL.md",".claude/skills/df-architecture/SKILL.md",".claude/skills/df-frontend/SKILL.md",".claude/skills/df-python/SKILL.md",".claude/skills/df-quality/SKILL.md",".claude/skills/df-testing/SKILL.md","docs/prd/PRD-video-flow-ui.md","docs/prd/ISSUES-video-flow-ui.md","docs/DESIGN.md",".claude/tmp/dossier-video-flow-ui.md",".claude/tmp/ralph-state-video-flow-ui.json",".claude/tmp/observed.diff"],"expected_outputs":[".claude/BOOTSTRAP.md",".claude/contexts/render-pipeline/CONTEXT.md",".claude/contexts/web-ui/CONTEXT.md",".claude/skills/CONTEXT-MAP.MD",".claude/skills/darkagent/PR-TEMPLATE.md",".claude/skills/darkagent/SKILL.md",".claude/skills/df-architecture/SKILL.md",".claude/skills/df-frontend/SKILL.md",".claude/skills/df-python/SKILL.md",".claude/skills/df-quality/SKILL.md",".claude/skills/df-testing/SKILL.md","docs/prd/PRD-video-flow-ui.md","docs/prd/ISSUES-video-flow-ui.md","dist/video_pipeline-0.1.0-py3-none-any.whl",".claude/tmp/dossier-video-flow-ui.md",".claude/tmp/ralph-state-video-flow-ui.json",".claude/tmp/observed.diff"],"expects_changes":false,"requires_diff_coverage":false,"test_paths":["tests"],"red_failure_signature":"not-applicable","blind_test_authorship":false,"test_author":"none","blind_roles":{"test_author":"none","implementer":"df-quality:delivery-video-flow-ui","test_auditor":"none"},"implementation_plan_paths":[],"permitted_context_paths":["docs/prd/PRD-video-flow-ui.md","docs/prd/ISSUES-video-flow-ui.md"],"blind_attestation":{"status":"not-applicable","implementation_plan_paths_intersection":[]},"requires_test_audit":false,"required_mutation_kills":0,"mutation_probes":[],"change_scope":{"allowed_globs":[".claude/BOOTSTRAP.md",".claude/contexts/render-pipeline/CONTEXT.md",".claude/contexts/web-ui/CONTEXT.md",".claude/skills/CONTEXT-MAP.MD",".claude/skills/darkagent/PR-TEMPLATE.md",".claude/skills/darkagent/SKILL.md",".claude/skills/df-architecture/SKILL.md",".claude/skills/df-frontend/SKILL.md",".claude/skills/df-python/SKILL.md",".claude/skills/df-quality/SKILL.md",".claude/skills/df-testing/SKILL.md","docs/prd/PRD-video-flow-ui.md","docs/prd/ISSUES-video-flow-ui.md"],"generated_globs":["dist/video_pipeline-0.1.0-py3-none-any.whl",".claude/tmp/dossier-video-flow-ui.md",".claude/tmp/ralph-state-video-flow-ui.json",".claude/tmp/observed.diff"],"optional_globs":[],"bundles":[{"id":"delivery-workflow","globs":[".claude/BOOTSTRAP.md",".claude/contexts/render-pipeline/CONTEXT.md",".claude/contexts/web-ui/CONTEXT.md",".claude/skills/CONTEXT-MAP.MD",".claude/skills/darkagent/PR-TEMPLATE.md",".claude/skills/darkagent/SKILL.md",".claude/skills/df-architecture/SKILL.md",".claude/skills/df-frontend/SKILL.md",".claude/skills/df-python/SKILL.md",".claude/skills/df-quality/SKILL.md",".claude/skills/df-testing/SKILL.md","docs/prd/PRD-video-flow-ui.md","docs/prd/ISSUES-video-flow-ui.md"]},{"id":"delivery-build","globs":["dist/video_pipeline-0.1.0-py3-none-any.whl"]},{"id":"delivery-review","globs":[".claude/tmp/dossier-video-flow-ui.md",".claude/tmp/ralph-state-video-flow-ui.json",".claude/tmp/observed.diff"]}]},"active_antipattern_ids":[],"evidence_commands":{"green":{"command_id":"delivery-green","kind":"green","argv":["bash",".claude/scripts/sandbox.sh"],"timeout":300,"max_output_bytes":120000,"test_paths":["tests"]},"browser-preflight":{"command_id":"browser-preflight","kind":"green","argv":["bash","-lc","firefox --version && geckodriver --version"],"timeout":30,"max_output_bytes":20000,"test_paths":[]},"build":{"command_id":"build","kind":"green","argv":["uv","build","--wheel"],"timeout":180,"max_output_bytes":50000,"test_paths":[]},"wheel-smoke":{"command_id":"wheel-smoke","kind":"green","argv":["bash","-lc","set -euo pipefail; env_dir=$(mktemp -d); python3 -m venv $env_dir; $env_dir/bin/pip install dist/video_pipeline-0.1.0-py3-none-any.whl; test -f $env_dir/lib/python3.13/site-packages/video_pipeline/web/static/index.html; $env_dir/bin/video-pipeline web --help"],"timeout":180,"max_output_bytes":60000,"test_paths":[]},"design-selftest":{"command_id":"design-selftest","kind":"green","argv":["bash","-lc","set -euo pipefail; source .claude/tmp/gates.env; bash \"$DF_SKILL_DESIGN/scripts/selftest.sh\""],"timeout":180,"max_output_bytes":60000,"test_paths":[]},"design-runtime":{"command_id":"design-runtime","kind":"green","argv":["bash","-lc","set -euo pipefail; source .claude/tmp/gates.env; .venv/bin/video-pipeline web --port 8766 & server_pid=$!; trap 'kill $server_pid' EXIT; for n in {1..30}; do curl -fsS http://127.0.0.1:8766/ >/dev/null && break; sleep 0.2; done; \"$DF_GATE_DESIGN\" --url http://127.0.0.1:8766/ --src src/video_pipeline/web/static --tokens src/video_pipeline/web/static/app.css"],"timeout":240,"max_output_bytes":100000,"test_paths":[]},"conclave":{"command_id":"conclave","kind":"green","argv":["bash","-lc","set -euo pipefail; source .claude/tmp/gates.env; \"$DF_GATE_CONCLAVE\" --job pr-local-video-flow-ui --dossier .claude/tmp/dossier-video-flow-ui.md --prd docs/prd/PRD-video-flow-ui.md --ralph-state .claude/tmp/ralph-state-video-flow-ui.json --diff .claude/tmp/observed.diff --source-root . --base-root .claude/tmp/base-tree-video-flow-ui --require-findings"],"timeout":1800,"max_output_bytes":120000,"test_paths":[]},"gate-resolve":{"command_id":"gate-resolve","kind":"green","argv":["bash","-lc","set -euo pipefail; source .claude/tmp/gates.env; test -x \"$DF_SKILL_DESIGN\"; test -x \"$DF_GATE_DESIGN\"; test -x \"$DF_GATE_CONCLAVE\""],"timeout":30,"max_output_bytes":20000,"test_paths":[]}},"required_gates":["green","browser-preflight","gate-resolve","build","wheel-smoke","design-selftest","design-runtime"],"required_gate_evidence_ids":["delivery-green","browser-preflight","gate-resolve","build","wheel-smoke","design-selftest","design-runtime"],"required_output_fields":["summary","gate_matrix","pending_gates","unvalidated_paths"]}
```
