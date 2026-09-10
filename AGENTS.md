# Instruções do projeto

Leia e siga `/home/dan/.codex/RTK.md` para comandos de shell.

## Próximos vídeos e alterações de direção

Antes de planejar ou alterar um vídeo, leia
`docs/direction/visual-direction-v1.md`. A referência aprovada é o `master.mp4`
identificado em `docs/direction/reference-v1.json`; outro arquivo não a substitui
automaticamente.

Planeje intenção, ambiente, mudança de estado, ligação entre beats e destino
PERSIST / TRANSFORM / EXIT de cada unidade visual. Aplique os contratos
`ScenePlan.direction` de token, proporção e processamento quando pertinentes;
registre na revisão quando não forem aplicáveis. Use
`examples/direction-contract/plan.json` e `docs/direction/review-template.md`.

Ao mudar componentes, runtime ou validadores, execute
`rtk proxy .venv/bin/pytest tests/test_direction_contracts.py -q` e as regressões
afetadas. Não considere esses testes uma aprovação artística ou cobertura de
todos os frames. Revise sequências com narração e transições entre cenas.

Preserve a referência. Para conferir integridade, execute
`rtk proxy .venv/bin/python scripts/verify_direction_reference.py`. Alterar a
referência requer uma nova versão e aprovação explícita do vídeo correspondente;
nunca reescreva hashes apenas para fazer a verificação passar.
