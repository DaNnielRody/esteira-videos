---
name: df-testing
description: Produz e audita evidência comportamental TDD para a esteira.
---

# Testes

Batch 1; escopo de testes declarado no evidence row. Comunicação telegráfica.
Injete memória, leia contextos atribuídos e
`/home/dan/workflow/darkagent/references/evidence-contract.md` antes de produzir
evidência.

Padrões: pytest em `tests/`; fakes de provider/subprocess em
`test_project_lifecycle.py`; aceitação em `test_project_accept.py`; retomada em
`test_project_resume.py`; composição em `test_audiovisual_composition.py`;
integração real marcada em `tests/integration/`. Não use rede/modelo/mídia real
no gate normal.

Siga o evidence row literal. RED precisa exercer todos os `test_paths` e conter
a assinatura declarada. O audit liga o RED ID e tem exatamente os cinco checks
canônicos. Mocks só atravessam fronteiras públicas. Mutações restauram hashes
exatos; ambiente/import/syntax não conta como kill. Use a menor fixture realista.

Gate: argv/timeout/output cap declarados pelo host. Dúvidas só vão para um doubt
log declarado no `change_scope`; caso contrário, retornam estruturadas ao host.
Concluído quando RED, audit, GREEN e mutation/skip têm envelopes compatíveis,
papéis independentes e paths observados.
