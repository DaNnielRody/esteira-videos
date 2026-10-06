# LLM Foundation — referência aprovada

O arquivo aprovado pelo usuário é [master.mp4](master.mp4), SHA-256
`7d52455e20cecb8c958bafceceb250558640e85f581b804d94bc524b8c409f33`.
Ele tem 738,9 segundos, 1920×1080 e 30 fps; o áudio está incorporado no MP4.

- [Fontes das 11 cenas e suporte congelado](master-scenes/)
- [Validação existente](master-validation.json)
- [Changelog do master](master-changelog.md)
- [Inventário de integridade](../../docs/direction/reference-v1.json)
- [Princípios e contratos de direção](../../docs/direction/visual-direction-v1.md)

## Recuperar em outro checkout

Instale Git LFS na máquina, clone o repositório e execute:

```sh
git lfs install --local
git lfs pull
python3 scripts/verify_direction_reference.py
```

O script usa apenas a biblioteca padrão do Python (3.11 ou mais recente).
Ele verifica os 211 arquivos do pacote, incluindo o master e suas fontes;
ponteiros LFS sem conteúdo hidratado falham na verificação. O conteúdo vive no
Git LFS deste mesmo repositório. Nenhum download automático reaprova um hash.

## Escopo da referência

As fontes e relatórios são preservados byte a byte. Caminhos absolutos antigos
em relatórios documentam a execução original; não são requisitos para conferir
a integridade. Os assets e fontes tipográficas incluídos ficam em `_assets` e
`_fonts`; o suporte original está em `_support`.

O pacote preserva o resultado e as fontes identificadas, mas não promete um
rerender bit a bit em qualquer ambiente. O inventário de ambiente no manifesto
foi observado posteriormente. O workflow de CI verifica a recuperação do pacote,
a integridade e os testes; não substitui reprodução audiovisual humana.

As versões anteriores, renders intermediários e projetos de trabalho ficam fora
deste commit. Permanecem no workspace original; não foram apagados. O histórico
editorial local anterior está em `README-local-history.md`. Construir um golden
set de regressões a partir dessas versões exige alinhar os trechos e rotular os
pares defeito/correção — não foi feito nesta entrega.
