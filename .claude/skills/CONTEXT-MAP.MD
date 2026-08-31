# Mapa de contexto — Esteira de Vídeos

## Protocolo

O ciclo de vida deste mapa é definido por
`/home/dan/workflow/darkagent/skills/context-map/SKILL.md`. A reconciliação de
um run promove somente fatos confirmados e chama
`.claude/hooks/memory-reconcile.sh` para cada delta marcado `promoted`.

Este mapa usa o RTL canônico de `origin/feat/render-in-the-loop-tracer@2fcc38f`
como baseline observado e adiciona o contexto planejado da Web UI nesta branch.
`adc0e42` é apenas o HEAD/merge-base de planejamento. O domínio existente
continua sendo a autoridade; a UI não cria outro `Project`, `Timeline`,
segmentação ou renderer.

## Contextos

- [Fundação do projeto](../contexts/project/CONTEXT.md): identidade, runtime,
  CLI, dependências, artefatos e golden.
- [Pipeline de renderização](../contexts/render-pipeline/CONTEXT.md): geração
  visual, tentativas, sensores, composição, inspect, retomada e aceitação.
- [Web UI](../contexts/web-ui/CONTEXT.md): operador local, jobs, seleção de
  cenas canônicas, revisões da sessão e adaptação HTTP/estática.

## Relações

- **Fundação do projeto → Pipeline de renderização/Web UI:** isolamento do
  subprocesso Manim, execução local sequencial, paths relativos seguros e
  restrições de artefatos imutáveis valem para ambas as superfícies.
- **Web UI → Pipeline de renderização:** um run externo de `VideoPipeline`
  cobre o `PROJECT`/`Timeline`; cada cena canônica abre um run interno de
  `RenderPipeline`. A regeneração seletiva cria um novo run externo com os
  irmãos verificados reutilizados. A UI lê `PROJECT/artifacts/<outer-run-id>/run.json`
  e MP4s preservados, sem duplicar o estado do renderer.
- **Web UI → Fundação do projeto:** criação/confirmar/render/inspect/accept
  chamam as APIs canônicas e armazenam apenas manifests transitórios ou de
  revisão em `PROJECT/ui`.

## Fluxo canônico compartilhado

```text
init PROJECT --title TITLE --script SCRIPT --audio AUDIO
    ↓
timeline validate PROJECT/project.json
    ↓ (se candidate, revisão manual)
timeline confirm PROJECT/project.json
    ↓
render PROJECT/project.json [--scene ID]
    ↓
inspect PROJECT/project.json
    ↓
accept PROJECT/project.json --run RUN_ID
```

`init` cria `PROJECT/project.json`, script, áudio, timeline, planos, briefs e
expectations quando o roteiro permite. Headings/blocos, timeline explícita,
pause-aligned ou proporcional são decisões do RTL. Timeline `candidate` exige
`confirm`; apenas `confirmed` pode renderizar. `inspect` expõe
`current_scene`, `action_next`, progresso e diagnósticos persistidos. Uma
interrupção ou falha retoma o mesmo run e reutiliza cenas `ready` verificadas.
Um run completo compõe `PROJECT/artifacts/<run-id>/final.mp4`; somente `accept`
publica `PROJECT/golden/`.

## Contratos compartilhados

- Paths são relativos ao `PROJECT` e passam por validação segura; IDs de projeto
  usam `YYYY_slug` e cenas têm ordem estável.
- Cada run mantém candidatos e evidências em
  `PROJECT/artifacts/<run-id>`, incluindo `run.json`, cenas, composição e
  `final.mp4`. Não há root global de artefatos.
- Uma regeneração seletiva começa de um run `ready` do mesmo projeto, valida
  hashes, cria um novo run, reutiliza atomicamente irmãos verificados e renderiza
  somente a cena selecionada.
- Revisões da UI são manifests create-once em
  `PROJECT/ui/revisions/vNNN.json`; `PROJECT/ui/index.json` é o único ponteiro
  mutável e `PROJECT/ui/working/<job-id>.json` é transitório. Revisão da UI é
  distinta de `PROJECT/golden/manifest.json` e não o promove.
- O golden usa o envelope `golden.manifest/1`, `version: 1`, profile explícito
  (`visual` ou `audiovisual`), status `accepted`, identidade, capacidades,
  hashes, composição e snapshots. Não há fallback implícito de profile.
- Discovery, leitura e validação são model-free e não executam provider,
  código visual, Manim, FFmpeg ou ffprobe.

## Política de engenharia

- Atualize consumidores quando uma interface canônica mudar; não crie aliases,
  um segundo `Project` ou caminhos paralelos de segmentação.
- Dependências novas exigem ADR.
- Prove cada comportamento em uma interface pública com RED/GREEN observado.
- Use fakes para provider, subprocessos, relógio, filesystem de publicação e
  sensores nos testes normais; não use rede, download ou modelo real.
- O sandbox é o gate local: testes `not integration`, Ruff em `.`, e mypy só
  nos módulos tipados declarados no script.
