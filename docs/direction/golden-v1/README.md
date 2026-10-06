# Golden de direção v1

Abra [o comparador](index.html) no navegador. Os recortes estão no Git LFS:
execute `git lfs pull` após clonar. Também funciona sem servidor, abrindo o HTML
local. Cada par contém áudio original reencodado, frames amostrados e o registro
da correção. O botão inicia os dois lados; controles individuais permitem ouvir
e inspecionar cada versão. O navegador não garante sincronismo exato entre players.

## O que foi catalogado

- Inventário dos 22 arquivos históricos encontrados na pasta do vídeo:
  21 conteúdos únicos; `final.mp4` e `final1.mp4` são idênticos por SHA-256.
- Um snapshot adicional, `hotfix-baseline`, para reproduzir os problemas de tokens
  e a mudança da soma. Total do inventário: 23 arquivos, 22 conteúdos únicos.
- Seis pares: progressão da palavra, padding, lifecycle dos tokens, proporção dos
  nós, payoff da escada e continuidade da soma.
- Um controle positivo: a pausa em NOVEMBRO 2022. A versão anterior também é válida
  para esse critério; não é um exemplo negativo.

O `master.mp4` aprovado continua sendo a única referência aprovada pelo usuário.
Os pares foram selecionados por inspeção de frames e changelogs locais, **sem uma
nova revisão audiovisual integral nem aprovação humana específica de cada par**.
São exemplos de problemas localizados; um vídeo histórico não recebe o rótulo
global de ruim. Numeração e nomes como `master-2` não provam aprovação ou sequência.
O inventário não equivale a catalogar cada cena de todas as versões.

## Uso nos próximos vídeos

1. Durante o planejamento, selecione os casos pertinentes e registre seus IDs na
   revisão. Aplique o princípio ao novo conteúdo; não copie a composição inteira.
2. Para tokens, proporções e processamento, instrumente os contratos existentes
   `ScenePlan.direction`. Eles verificam fatos registrados; não leem estes MP4s.
3. Revise o novo trecho com narração, estados intermediários e cenas vizinhas.
   Registre intervalo, problema observado, critério do catálogo e nova evidência.
4. Preserve pausas e densidade intencionais. Não substitua revisão por contagem de
   fades, zero freezes ou comparação pixel a pixel entre assuntos diferentes.
5. Acrescente novos pares em uma nova versão quando aparecerem decisões úteis.
   Não é preciso esperar outro vídeo; o histórico atual ainda contém candidatos.

## Verificação e reprodução

`catalog.json` registra SHA-256 dos originais, recortes e evidências, além de
intervalos de frames `[início,fim)`, a 30 fps. Cada par usa a mesma janela da linha
do tempo original; os beats dentro dela podem ocorrer em instantes diferentes.
Os vídeos completos antigos continuam locais, fora do Git. Os recortes e registros
necessários à consulta estão versionados e não dependem desses originais.

```sh
rtk proxy .venv/bin/python scripts/verify_direction_catalog.py --probe
rtk proxy .venv/bin/pytest tests/test_direction_catalog.py -q
rtk proxy .venv/bin/python scripts/verify_direction_reference.py
```

A primeira verificação confere hashes, referências, janelas, quantidade de frames,
resolução e presença de áudio. **Não aprova direção, sincronismo semântico ou todos
os frames.** Os testes de corrupção impedem aceitar evidências adulteradas ou
intervalos inválidos. A CI executa essa verificação com os arquivos do LFS.

Para reextrair, use o original com o hash indicado, `-ss início/30`,
`-t (fim-início)/30`, vídeo H.264 CRF18 preset fast yuv420p, áudio AAC160k e
`-movflags +faststart`. Os recortes mantêm 1920×1080 e 30 fps. Versões diferentes
do FFmpeg podem gerar outros bytes; reextração não autoriza reescrever hashes.
As folhas de contato contêm amostras reduzidas; para medir geometria, use o vídeo
em resolução original ou fatos instrumentados, não a miniatura.

Changelogs arquivados são evidências históricas e podem mencionar caminhos locais,
testes antigos ou planos futuros. Essas menções não são instruções atuais nem
prova de que todos esses testes foram executados nesta catalogação.
