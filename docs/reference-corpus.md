# Corpus de referência local

`reference_catalog.py` contém padrões pequenos e autorizados de IA e matemática
adaptados para Manim Community 0.21.0. Não é um dump do repositório do 3Blue1Brown:
assets, helpers privados e APIs ManimGL foram removidos.

O catálogo cobre álgebra linear, cálculo, probabilidade, redes neurais,
transformers, convolução e Fourier/sinais. Cada entrada preserva commit, caminho,
classe original e URL de proveniência. Uma cena escolhe até três exemplos de
forma determinística:

```json
{
  "id": "attention",
  "scene_name": "AttentionScene",
  "description": "Mostre a atenção entre três tokens.",
  "topics": ["transformers", "linear_algebra"],
  "reference_examples": 2
}
```

`reference_examples: 0` desliga referências. Esse campo controla somente o
contexto local de geração da cena.

Os sensores são verificados contra os golden files em `tests/golden/`. O corpus
rotulado cobre formas, cores, região, movimento, LaTeX e texto fixo. Os testes
de integração confirmam que os exemplos selecionados renderizam no runtime
local, enquanto os testes normais usam fakes determinísticos.

As cenas Community são ports de técnicas didáticas, não cópias pixel a pixel de
vídeos completos. A revisão do MP4 final continua sendo a evidência visual do
produto.
