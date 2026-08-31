---
name: df-quality
description: Consolida regressão, build, gates e dossiê de entrega da esteira.
---

# Qualidade

Batch 2; somente após implementações GREEN. Comunicação telegráfica. Injete
memória, leia todos os contextos do run, o evidence contract e, antes de
supervisão delegada, `/home/dan/workflow/darkagent/references/watching-long-runs.md`.

Padrões: sandbox em `.claude/scripts/sandbox.sh`; config em `pyproject.toml`;
evidência por issue no PRD; diff materializado inclui untracked; golden valida
sem modelo; integração real fica marcada; wheel deve conter assets web; gates
design/conclave usam bindings absolutos do bootstrap.

Execute apenas command IDs declarados, com limites. Verifique cobertura do diff,
context reconciliation, build/wheel smoke, Firefox/geckodriver e design. Se a
regra `mini-conclave-disabled` estiver ativa, materialize seus inputs e registre
o command ID como pendente no PR; quando ausente, execute o conclave normalmente.
Em Case 1/2, Watcher/Ralph possui a próxima OODA; trabalho inline usa
`watching-inline-runs.md`. Não transforme INCONCLUSIVE em GREEN.

Dúvidas só são gravadas em path declarado; fora do `change_scope`, retornam ao
host. Concluído quando a matriz cobre todo required gate,
o changeset observado está materializado, não há delta pendente e o dossiê
explicita dissenso e paths não validados.
