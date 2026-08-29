# Golden set — control data do Manim Community

Renders validados pelo próprio projeto Manim, usados aqui como **ground truth
rotulada** para o leitor de frames (`src/video_pipeline/observation.py`).

## Procedência

- Origem: [ManimCommunity/manim](https://github.com/ManimCommunity/manim),
  `tests/test_graphical_units/control_data/<módulo>/<Cena>.npz`
- Commit de referência: `cafc63919eb0` (2026-08-26)
- Licença: MIT (© ManimCommunity). Ver `LICENSE` neste diretório.
- Versão do Manim usada no projeto: 0.21.0

Estes arquivos **não** vêm no wheel do PyPI; `manim.utils.testing.frames_comparison`
aponta para `manim/control_data/graphical_units_data`, que não existe numa
instalação. Só existem no repositório, por isso são vendorizados aqui.

## Formato

O formato é o do Manim, sem alteração — é o ponto de usá-lo:

- `.npz` comprimido (`np.savez_compressed`)
- chave única `frame_data`
- shape `(n_frames, height, width, 4)`, RGBA
- dtype `uint8`

Cenas estáticas trazem 1 frame a 480x854; animações trazem 7 frames a 240x427.

Tolerâncias de comparação exata, quando aplicável, são as do Manim
(`manim.utils.testing._frames_testers`): `FRAME_ABSOLUTE_TOLERANCE = 1.01` e
`FRAME_MISMATCH_RATIO_TOLERANCE = 1e-5`.

## O que cada arquivo prova

`expected.json` declara o que o observador deve ler em cada frame. O nome da
cena é o rótulo: `geometry/Circle.npz` é um círculo, `transform/Transform.npz`
é uma transformação real capturada em 7 frames.

`latex/expected.json` e `text/expected.json` declaram pares positivos e
negativos que o harness renderiza em Manim Community durante a calibração.
Eles cobrem conteúdo, posição, tamanho e cor fixos para `MathTex`, `Tex` e
`Text`; não usam OCR nem imagens autorrotuladas pelo próprio sensor.

## Limite deliberado

Comparação exata de frame **não** serve para julgar cena gerada por LLM. Dois
renders ambos semanticamente corretos da mesma especificação foram medidos
divergindo em 2,72% dos pixels — 2.719x acima da tolerância do Manim — porque o
modelo escolhe raio, cor, traço e timing diferentes a cada tentativa. Por isso
este golden set valida o **observador**, e a fidelidade semântica de uma cena
nova é decidida por `expectations.py`.
