# Corpus 3Blue1Brown para o Qwen

O pipeline usa um corpus curado de técnicas de IA e matemática, não um dump do
repositório `3b1b/videos`. O código original depende de ManimGL,
`manim_imports_ext`, `InteractiveScene`, assets e helpers privados. Cada cartão
em `reference_catalog.py` foi adaptado para uma cena pequena e isolada em Manim
Community 0.21.0 e é renderizado de verdade pela suíte de integração.

## Escopo e proveniência

O catálogo cobre álgebra linear, cálculo, probabilidade, redes neurais, machine
learning, transformers, convolução e Fourier/sinais. Cada cartão registra o
commit `674b966fbb6cf0307590d27744d186165e8b6a76`, caminho, classe original e
link imutável. O proprietário deste projeto confirmou em 2026-08-29 que possui
autorização para reutilizar e adaptar o código do autor; essa autorização é a
premissa de distribuição deste corpus.

Uma Scene Spec seleciona no máximo três cartões, em ordem determinística:

```json
{
  "schema_version": "1.0",
  "scene_name": "AttentionScene",
  "description": "Mostre a atenção entre três tokens.",
  "topics": ["transformers", "linear_algebra"],
  "reference_examples": 2
}
```

`reference_examples: 0` desliga o few-shot e forma a condição de controle.

## Texto e LaTeX determinísticos

Conteúdo LaTeX só entra no gate automático quando a spec fixa expressão,
tamanho, cor e posição:

```json
{
  "expect": {
    "max_shapes": 200,
    "latex": [{
      "tex": "A\\mathbf{v}=\\lambda\\mathbf{v}",
      "font_size": 48,
      "color": "yellow",
      "x": 0.0,
      "y": -2.8,
      "min_iou": 0.95
    }]
  }
}
```

Depois que o modelo é descarregado, o pipeline renderiza essa `MathTex` como
referência, isola no candidato a cor declarada e compara as máscaras com
tolerância de um pixel para antialiasing e compressão. O melhor score permanece
no campo histórico `best_iou` de `latex-validation.json`. Fórmula, tamanho,
posição ou cor divergentes são rejeição semântica e podem voltar ao Qwen. Falha
ao renderizar ou decodificar a referência é `SENSOR_ERROR` e não consome outra
tentativa do modelo. OCR não participa do critério de aceitação.

O mesmo sensor aceita itens `text` com `renderer: "text"` ou `renderer: "tex"`.
`Text` exige fonte Pango explícita; `Tex` usa o ambiente LaTeX fixado pelo
Manim Community. Conteúdo, tamanho, cor e posição são obrigatórios nos dois
casos, inclusive para texto multilinha.

## Calibração e estudo pareado

Antes de ampliar o vocabulário do observador:

```bash
video-pipeline calibrate \
  --golden-root tests/golden \
  --output artifacts/sensor-calibration.json
```

O comando grava TP/FP/FN/TN e taxas por eixo (`shape_count`, `shape`, `color`,
`region`, `motion`, `latex`, `text`) e falha se algum FP, FN ou erro de sensor
aparecer. O corpus rotulado tem 15 casos; texto cobre conteúdo divergente,
posição, tamanho, cor, multilinha e os renderizadores `Text`/`Tex`. Candidato e
referência são renderizados de verdade durante a calibração.

O catálogo de dez cenas em `examples/reference-study.json` materializa N=10
por condição, com specs idênticas exceto pelo número de exemplos. Cada caso
declara o arquivo e a classe 3b1b de origem no mesmo commit imutável usado pelo
catálogo:

```bash
video-pipeline prepare-study examples/reference-study.json \
  --output-root artifacts/reference-study
```

O diretório de saída deve estar ausente ou vazio; o comando recusa resultados
antigos para impedir mistura silenciosa entre execuções.

- `control/`: `reference_examples = 0`;
- `treatment/`: `reference_examples = 2`.

Execute os vinte arquivos pelo comando `render` nas mesmas condições de modelo,
`temperature`, `seed`, limite de tentativas e máquina. Compare sucesso, tentativas até
aceitação, classes de diagnóstico e tempo total; não conclua ganho do corpus a
partir de exemplos isolados.

## Limite de equivalência

As cenas Community são ports pequenos dos padrões didáticos e de animação das
classes citadas — não cópias pixel a pixel dos vídeos completos. A suíte prova
proveniência, seleção determinística e renderização em Manim Community 0.21.0.
Assets, helpers privados e a composição integral em ManimGL continuam fora do
contrato, portanto fidelidade visual completa exige uma validação separada com
os vídeos finais autorizados.
