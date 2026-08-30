# Mapa de contexto — Esteira de Vídeos

## Contextos

- [Fundação do projeto](../contexts/project/CONTEXT.md): identidade,
  timeline, estados, CLI, artifacts e golden.
- [Pipeline de renderização](../contexts/render-pipeline/CONTEXT.md): geração
  visual, tentativas, sensores, composição, inspect e aceitação.

## Relação

O CLI inicializa `PROJECT/project.json` com script, áudio e referências de
cenas. `timeline validate` torna explícito o que precisa de revisão; uma
timeline `candidate` exige `timeline confirm`. Só então `render` cria um run,
`inspect` permite revisão e `accept --run RUN_ID` promove o snapshot pronto.

## Contratos compartilhados

- Paths são relativos e seguros; IDs de projeto usam `YYYY_slug` e cenas têm
  ordem estável.
- Um run mantém candidatos e evidências em `PROJECT/artifacts/RUN_ID`; o render
  não escreve fontes permanentes.
- A promoção é uma transação lógica de payloads. Preflight valida todos os
  hashes, paths e fatos antes do primeiro replace; rollback preserva o golden
  anterior e os candidatos do run que falhou.
- O golden usa um envelope único `golden.manifest/1`, `version: 1`, profile
  explícito (`visual` ou `audiovisual`), status `accepted`, `project_id`,
  `title` e `capabilities`. Não há fallback implícito de profile.
- Discovery, leitura e validação são model-free e não executam provider,
  código visual, Manim, FFmpeg ou ffprobe.

## Política de engenharia

- Atualize consumidores quando uma interface canônica mudar; não crie aliases
  ou caminhos paralelos.
- Dependências novas exigem ADR.
- Prove cada comportamento em uma interface pública com RED/GREEN observado.
- Use fakes para provider, subprocessos, relógio, filesystem de publicação e
  sensores nos testes normais; não use rede, download ou modelo real.
- O sandbox é o gate local: testes `not integration`, Ruff em `.`, e mypy só
  nos módulos tipados declarados no script.
