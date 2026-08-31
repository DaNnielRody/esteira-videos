# Darkagent bootstrap — Video Flow UI

## Política de comunicação

Status, relatórios e prompts de agentes são telegráficos e claros. Segurança,
ações irreversíveis, especificações, código, commits e pull requests preservam
detalhe integral. Esta política é entrada obrigatória dos especialistas e do
pipeline local.

## Descoberta observada

- Grafo: `.codegraph` sincronizado em 2026-08-30; o pacote e seus testes foram
  indexados.
- Codemap: rota `skipped-with-reason`. O checkout tem um único pacote Python
  sob `src/video_pipeline`, e o atlas de módulos já existe em
  `.claude/skills/CONTEXT-MAP.MD`; mapas por pasta duplicariam esse limite.
- Backend: Python 3.13, pacote `video_pipeline`, CLI em
  `src/video_pipeline/cli.py` e contratos canônicos em `project.py`,
  `timeline.py`, `video.py` e `golden.py`.
- Frontend: superfície vanilla planejada em `src/video_pipeline/web/static`;
  ainda ausente no bootstrap e, por isso, nenhum build frontend pode alegar
  GREEN antes de sua implementação.
- Persistência do produto: JSON e mídia dentro de `PROJECT`; não há banco de
  dados do produto. `.claude/memory.db` pertence apenas ao workflow.
- Integrações: Ollama loopback, Manim, FFmpeg/ffprobe e filesystem local. Os
  testes normais usam fakes e não chamam modelo, rede nem mídia real.
- Testes/configuração: pytest, Ruff e mypy configurados em `pyproject.toml`;
  gate nativo em `.claude/scripts/sandbox.sh`.
- Branch padrão observada: `origin/main@6792962`. Alvo deste PR observado:
  `origin/feat/render-in-the-loop-tracer@2fcc38f`.

## Bindings

- Contexto: `/home/dan/workflow/darkagent/skills/context-map/SKILL.md`
- Memória: `/home/dan/workflow/darkagent/skills/memory/SKILL.md`
- Grilling/spec/evidence/issues/TDD: `/home/dan/workflow/darkagent/skills/`
- Ralph: `/home/dan/workflow/darkagent/scripts/orchestrator.py`
- Conclave: `/home/dan/workflow/gates/mini-conclave/scripts/conclave-gate.sh`
- Design: `/home/dan/workflow/gates/darkdesign/scripts/design-gate.sh`

## Estado dos gates no bootstrap

- Resolução de bindings: GREEN; `.claude/tmp/gates.env` contém todos os paths.
- Contextos: GREEN; projeto, render pipeline e Web UI têm um contexto alcançável.
- Memória: GREEN; DB e hook foram criados e ficam ignorados pelo Git.
- Sandbox nativo: execução observada avançou sem falha, mas o resultado final
  será renovado após o código de produto estabilizar.
- Design/runtime frontend: pendente da implementação da rota e assets.
- Conclave: temporariamente desativado por decisão explícita do usuário; o
  changeset final ainda materializa dossiê/diff/estado e o PR registra o comando
  pendente para retomar as lentes quando o gate voltar.

## Aprendizado

O checkout não continha `.claude/skills/darkagent/SKILL.md`; o bootstrap passou
a possuir esse gap no pipeline local. Não há lição apenas narrativa: o probe de
fase zero agora seleciona este pipeline nos próximos runs.
