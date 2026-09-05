# Web UI

Este contexto é dono da superfície local, single-user, que adapta o
`Project`/`Timeline` canônico e os runs preservados do RTL. Ele cobre roteiro,
estado/progresso, cenas, MP4, correções, jobs e revisões da sessão; não cria um
segundo `Project`, uma nova segmentação ou um renderer.

## Linguagem

**Project:** o diretório canônico recebido pelo domínio existente, com
`project.json`, `timeline.json`, cenas, áudio, runs e golden.

**Cena:** uma cena/segmento já definido na `Timeline` e materializado pelo
`SceneSpec`/`ScenePlan` do RTL. A Web UI não inventa cenas nem altera a
segmentação.

**Run:** uma sequência de tentativas do `VideoPipeline` para as cenas do projeto,
preservada em `PROJECT/artifacts/<run-id>/` com `run.json`, evidências,
composição e MP4.

**Revisão:** um manifest imutável da sessão, em
`PROJECT/ui/revisions/vNNN.json`, que referencia projeto, cenas, run, hashes,
correções, mensagens e assets. Revisão não é golden.

**Working draft:** estado transitório de um job em
`PROJECT/ui/working/<job-id>.json`; nunca substitui fontes canônicas.

## Contratos

- Criar projeto chama `initialize_project` com título, script UTF-8 e
  `audio_asset_id`. `Timeline.status == candidate` exige ação explícita de
  `confirm_project_timeline` antes de enfileirar render.
- O catálogo de áudio vem da raiz configurada sem expor paths. Se estiver
  ausente ou vazio, `/api/audio` retorna `assets: []` com diagnóstico acionável,
  a UI bloqueia a criação e orienta adicionar ao menos um arquivo de narração
  antes de criar ou renderizar vídeo real; áudio nunca é fabricado.
- A UI consulta `inspect_project`/`run.json` para `current_scene`,
  `action_next`, progresso, tentativas, diagnóstico e estado. Callbacks finos
  são informativos e best-effort; não substituem a evidência persistida.
- Jobs são FIFO, limitados e têm um único worker. Apenas um job por vez invoca
  `VideoPipeline`; `queued` pertence ao job, não ao run canônico.
- Uma regeneração de cena exige run-base `ready` do mesmo `PROJECT`, timeline
  confirmada e hashes compatíveis. Cria novo run sob
  `PROJECT/artifacts/<new-run-id>/`, revalida/reutiliza atomicamente irmãos
  `ready` e renderiza somente a cena escolhida. O run-base e os irmãos ficam
  imutáveis; o novo run recompõe e valida `final.mp4`.
- Ao terminar (sucesso ou falha), o job publica create-once a próxima revisão.
  `PROJECT/ui/index.json` contém apenas o ponteiro mutável
  `current_revision_id`; checkout muda esse ponteiro e não apaga revisões
  posteriores. Editar depois de checkout cria o próximo número monotônico com
  parent na revisão selecionada.
- `PROJECT/golden/manifest.json` e `PROJECT/golden/accepted/` são o snapshot
  editorial do domínio. Apenas `accept_project` sobre run `ready` publica
  golden; revisão, checkout, render e regeneração nunca o promovem.
- Jobs/drafts não terminais encontrados após restart viram `interrupted` e
  exigem retry explícito. Falhas preservam runs, evidências e revisões
  anteriores.

## Relações

- **Render pipeline:** um run externo de `VideoPipeline` cobre o
  `PROJECT`/`Timeline`; cada cena canônica é executada em um run interno de
  `RenderPipeline`. Regeneração seletiva abre um novo run externo, reutiliza os
  irmãos verificados e devolve a referência em `PROJECT/artifacts`; a UI não
  duplica o estado de `run.json` nem o fluxo de validação.
- **Fundação do projeto:** fornece `init`, confirmação de timeline, `inspect`,
  `accept`, isolamento local, validação de paths e invariantes do golden.

## Operacional e segurança

- Estado da Web UI vive somente abaixo do `PROJECT/ui`:
  `index.json`, `revisions/vNNN.json` e `working/<job-id>.json`.
- Runs e assets de render vivem somente abaixo do `PROJECT/artifacts`; URLs
  públicas carregam IDs, nunca paths de host ou globais.
- O servidor escuta apenas `127.0.0.1`, serve estáticos package-owned, não usa
  CORS e exige Host loopback, Origin same-origin, JSON e CSRF process-scoped em
  toda mutação.
- O catálogo de áudio e assets resolve IDs sob a raiz configurada. Antes de
  abrir qualquer arquivo, exige `resolve(strict=True)` e
  `relative_to(resolved_root)`, rejeitando traversal e symlink escape.
- MP4 aceita uma única faixa HTTP (`206`, `Content-Range`,
  `Accept-Ranges`); multi-range e ranges malformados são rejeitados. Body,
  script, correção, cenas e fila obedecem aos limites do PRD; erros não vazam
  traceback, token, path ou conteúdo de arquivo.

## Proven patterns and tests

- `src/video_pipeline/workspace.py` fornece diretórios locais atômicos e IDs de
  run nunca sobrescritos; `src/video_pipeline/cli.py` é a fronteira injetável.
- Fakes cobrem provider, subprocessos, relógio, filesystem e sensores.
  Testes de serviço provam create → confirm → render → regenerar uma cena →
  checkout → accept. A prova de browser real roda com `rtk .venv/bin/pytest -q
  tests/integration/test_web_e2e.py`: ela inicia `/snap/bin/geckodriver`, abre
  Firefox headless; `_firefox_binary()` verifica se `/usr/bin/firefox` é um
  ELF e, quando é wrapper Snap, passa
  `/snap/firefox/current/usr/lib/firefox/firefox` em
  `moz:firefoxOptions.binary`. O DOM é dirigido pelo protocolo WebDriver W3C
  usando apenas a stdlib, com `WebService` e `ThreadingHTTPServer` reais e um
  fake somente na fronteira de `VideoPipeline`. O fluxo observado é criação,
  confirmação, render/polling, URLs de playback final e de cena, regeneração
  seletiva, histórico e restore/checkout, stale polling guard, reload/restart
  com descoberta e retry de draft interrompido; não roda Ollama, Manim, FFmpeg
  ou ffprobe. O teste só faz skip se `/usr/bin/firefox` ou
  `/snap/bin/geckodriver` não existirem/não forem executáveis; falhas de setup
  posteriores não são skip.
- A composição canônica do RTL é o `PROJECT/artifacts/<run-id>/final.mp4`.
  Enquanto não houver final pronto, a UI pode tocar `normalized.mp4` reais das
  cenas, claramente rotulados como prévia das cenas.
