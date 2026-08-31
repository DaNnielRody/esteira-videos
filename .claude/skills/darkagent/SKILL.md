---
name: darkagent
description: Entrega mudanças desta esteira por especificação, DAG, TDD, gates, revisão e pull request.
---

# Pipeline Darkagent do projeto

Use status e prompts telegráficos; preserve detalhe integral para segurança,
irreversibilidade, especificações, código, commits e PRs. Leia
`/home/dan/workflow/darkagent/references/evidence-contract.md` antes de gerar ou
executar evidência.

## Bindings canônicos

Leia `.claude/BOOTSTRAP.md` antes do primeiro stage. Ele é o único cache local
dos bindings absolutos e branches observados; resolva novamente
`.claude/tmp/gates.env` quando essa evidência estiver stale.

## 1. Abrir contexto e resolver escopo

Injete regras com `memory.py rule inject`, abra
`.claude/tmp/contexts/<slug>/RUN.md` e seus módulos, então injete memória pelos
prefixos observados. Execute grilling apenas para decisões ainda abertas.
Concluído quando regras, módulos, objetivo, owners e reconciliação constam do run
e nenhuma decisão de produto executável permanece aberta.

## 2. Fixar especificação e evidência

Atualize `docs/prd/PRD-<slug>.md`; leia antipatterns aplicáveis e use os stages
`spec` e `evidence-path`. Cada claim deve conter todos os campos e comandos do
contrato de evidência, incluindo RED, GREEN, escopo, papéis, mutações e gates.
Concluído quando todos os ACs têm cobertura completa e sem gaps; um AC
compartilhado pode apontar para mais de uma linha, desde que cada relação seja
explícita e nenhuma evidência seja duplicada sem owner.

## 3. Registrar issues e DAG

Atualize `docs/prd/ISSUES-<slug>.md`, publique trackers quando autorizado e
registre o DAG no Ralph com payloads literais do PRD. Gates manuais usam
`human_gate`. Concluído quando documento, tracker e estado SQLite concordam.

## 4. Confirmar branch

Use o PR target observado em `.claude/BOOTSTRAP.md`, revalidando-o quando o
remote mudar. Concluído quando merge-base e branch de trabalho são registrados
antes de novos edits.

## 5. Executar TDD supervisionado

Antes de qualquer endpoint leia `watching-long-runs.md`. Em Case 1/2, assine
`GET /events` antes do dispatch e aceite somente os wakes
`CHECKPOINT_REACHED`, `AGENT_FAILED`, `USER_INPUT_REQUIRED` e `RUN_COMPLETED`;
sucesso terminal exige `next_action=respond_user`. Execute
test-author → test-audit → implementer → mutation/regression conforme o payload,
serializando writers sobrepostos. Concluído quando todo issue aceito tem RED
observado, GREEN fresco e toda auditoria/mutação exigida.

## 6. Qualidade, integração e reconciliação

Execute `df-quality`, integre o PR target de modo recuperável, reconcilie cada
run-module e chame `.claude/hooks/memory-reconcile.sh` em cada delta promovido.
Rode `.claude/scripts/sandbox.sh`. Concluído quando conflitos, deltas e gate
final têm veredito fresco e nenhum delta permanece pendente.

## 7. Gates de entrega

Registre sandbox, build/wheel, Firefox/geckodriver e design runtime pelos command
IDs declarados em `PRD-video-flow-ui.md`. Enquanto a regra
`mini-conclave-disabled` estiver ativa, preserve o command ID do conclave mas
registre-o como pendência não bloqueante no PR. RED volta ao owner por até três
tentativas; INCONCLUSIVE é gap explícito. Concluído quando cada gate atualmente
requerido tem veredito, toda correção tem nova evidência e o gate suspenso tem
instrução de retomada.

## 8. Dossiê e conclave

Gere `.claude/tmp/dossier-<slug>.md`, exporte o Ralph para
`.claude/tmp/ralph-state-<slug>.json` e materialize tracked mais untracked em
`.claude/tmp/observed.diff`. Use source root e base tree distintos no conclave;
inclua `LESSONS.md` somente se existir. Com mini-conclave desativado, pare nos
artefatos materializados e grave no PR o comando exato; não simule findings.
Concluído quando os artefatos existem e, conforme a regra ativa, achados foram
validados ou a retomada pendente foi documentada.

## 9. Aceitar e abrir PR

Aceite a delivery task somente com todos os gates atualmente requeridos GREEN;
um mini-conclave suspenso e documentado não bloqueia. Com autorização
explícita para publicar, crie commits convencionais focados e use
`PR-TEMPLATE.md`, com `Closes #...` para trackers publicados; sem autorização,
pare no changeset pronto. Concluído quando Ralph aceita delivery e o resultado
autorizado contém closures e matriz de gates.

## 10. Relatar

Relate issues, especialistas/modelos, command IDs, retries, evidências,
antipatterns, dissenso, paths não validados e URL do PR. Concluído quando cada
campo vem de estado observado e não de intenção.

Critério global: os dez passos passam em ordem ou o run para com task, tentativa
e violações exatas.
