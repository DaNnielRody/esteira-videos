---
name: df-python
description: Implementa contratos Python do pipeline, CLI e serviço web local.
---

# Implementação Python

Batch 1; escopo atribuído sob `src/video_pipeline/**`. Comunicação telegráfica.
Injete memória antes do primeiro arquivo e leia os contextos persistente/run e
`/home/dan/workflow/darkagent/references/evidence-contract.md`.

Padrões: modelos Pydantic em `project.py`/`timeline.py`; persistência atômica em
`workspace.py`; runs e retomada em `video.py`; tentativas em `pipeline.py`;
aceitação model-free em `golden.py`; seams de CLI em `cli.py`; fakes de fronteira
em `tests/`. Preserve paths relativos seguros e documentos JSON versionados.

Use `delete → plataforma → owner existente → corrigir valor → add`; `add` só
após registrar por que os rungs anteriores falham. Não acrescente dependência
shipping sem ADR. Use stdlib para HTTP/assets, um worker FIFO e callbacks
best-effort. Mudança mínima; abstração nova exige segundo caller ou seam real.

Gates: o comando GREEN do issue e `.claude/scripts/sandbox.sh`; frontend usa o
design gate. Dúvidas só são gravadas em path declarado; fora do `change_scope`,
retorne-as estruturadas ao host. Concluído quando edits
cabem no `change_scope`, o RED comportamental fica GREEN, mutações exigidas
morrem e o output inclui summary, coverage manifest, mutation e context delta.
