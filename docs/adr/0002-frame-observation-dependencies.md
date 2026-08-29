# ADR 0002 — Observação de frames com NumPy, Pillow e SciPy

- Status: aceito
- Data: 2026-08-29
- Contexto: `docs/prd/PRD-render-in-the-loop-tracer.md`, limitação registrada em
  C3 ("render success does not prove semantic visual fidelity")

## Contexto

O gate de aceitação anterior era `exit code == 0` mais validação de contêiner
por `ffprobe`. Foi observado empiricamente que os dois passam para uma cena
semanticamente errada: a execução `3e9f37ce08a243db9aa5b6b75f1a307d` gravou um
MP4 h264 válido, com duração e dimensões positivas, mostrando **dois**
quadrados quando a especificação pede um único objeto que se move. Nem o
processo nem o contêiner podem decidir fidelidade semântica.

Para decidir isso é preciso ler os pixels do vídeo já renderizado.

## Decisão

Declarar `numpy`, `pillow` e `scipy` como dependências diretas de
`video-pipeline`.

As três já eram dependências declaradas de `manim==0.21.0`
(`numpy>=2.1`, `pillow>=11.0`, `scipy>=1.15` em Python 3.13), portanto a
decisão **não instala nada novo**: ela torna intencional um acoplamento que já
era transitivo, para que `src/video_pipeline/observation.py` não dependa de um
detalhe de implementação do Manim.

Usos exatos:

- `pillow` — decodificar cada frame PNG amostrado.
- `numpy` — máscara de luminância, caixa delimitadora e centroide.
- `scipy.ndimage` — `label` (componentes conexos, isto é, contagem de formas) e
  `binary_fill_holes` (preencher contornos, já que o Manim desenha shapes só
  com traço).

A extração dos frames continua sendo `ffmpeg`, já exigido pelo `ffprobe`.

## Alternativas descartadas

- **OpenCV**: instalação nova e pesada para o que se resume a componentes
  conexos e preenchimento de buracos.
- **Modelo de visão local (VLM)**: nenhum modelo de visão está disponível no
  Ollama local (apenas `qwen2.5-coder:7b` e `ornith-1.5:9b`), e um juiz
  probabilístico contradiz a tese do RITL, em que a observação determinística
  do artefato real decide.
- **Implementar rotulagem à mão com NumPy**: evitaria declarar `scipy`, mas
  reescreve código já testado por uma dependência que de qualquer forma está
  instalada.

## Consequências

- O classificador é estreito por decisão: separa círculo de quadrado alinhado
  aos eixos e conta formas visíveis. Não reconhece texto, cor ou geometria
  arbitrária. Formas intermediárias durante um `Transform` são reportadas como
  `polygon`/`other`, e o casamento de beats por subsequência tolera isso.
- `observation.py` não satisfaz `disallow_any_expr` do mypy, porque os tipos
  públicos do NumPy e do SciPy carregam `Any`. Fica na mesma classe de dívida
  já registrada em `.claude/tmp/doubts-render-in-the-loop-tracer.md`.
- A verificação semântica é opt-in: um Scene Spec sem `expect` mantém o
  contrato anterior, apenas de renderabilidade.
