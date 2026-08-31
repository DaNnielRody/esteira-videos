---
name: df-architecture
description: Revisa ownership e mudança arquitetural no domínio canônico da esteira.
---

# Arquitetura

Batch 1; escopo `src/video_pipeline/**`, PRD e contextos. Comunicação telegráfica.
Antes de ler o escopo, injete memória com o skill absoluto registrado no
bootstrap. Leia contexto persistente e run-module atribuídos, além de
`/home/dan/workflow/darkagent/references/evidence-contract.md`.

Padrões provados: `project.py` possui Project; `timeline.py` possui segmentação;
`video.py` possui o run externo; `pipeline.py` possui tentativas internas;
`golden.py` possui aceitação; `PROJECT/artifacts` possui runs; `PROJECT/ui`
possui somente revisões/drafts. A Web UI é adaptadora, não outro domínio.

Use a ladder `delete → plataforma → owner existente → corrigir valor → add` e
registre por que rungs anteriores não resolvem. Uma abstração nova exige segundo
consumidor de mesma intenção ou pressão real de seam. Para cada achado responda
necessidade presente, reuso do owner, amplificação, segundo consumidor e menor
correção; classifique `strong`, `worth exploring` ou `speculative` e diga o que
não refatorar.

Gate: `.claude/scripts/sandbox.sh`; frontend também exige o design gate absoluto
do bootstrap. Registre dúvidas no path declarado pelo evidence row; se ele não
existir no `change_scope`, devolva a dúvida estruturada ao host sem editar fora
do escopo. Concluído quando ownership permanece único, cada achado tem
força/risco/evidência e nenhum ideal arquitetural bloqueia sem risco concreto.
