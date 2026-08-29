# ADR 0002 — Observação de frames no formato do Manim, com OpenCV

- Status: aceito
- Data: 2026-08-29
- Substitui: a primeira versão desta ADR, que escolhia NumPy/Pillow/SciPy com
  classificador próprio
- Contexto: `docs/prd/PRD-render-in-the-loop-tracer.md`, limitação registrada em
  C3 ("render success does not prove semantic visual fidelity")

## Contexto

O gate de aceitação era `exit code == 0` mais validação de contêiner por
`ffprobe`. Foi observado empiricamente que os dois passam para uma cena
semanticamente errada: a execução `3e9f37ce08a243db9aa5b6b75f1a307d` gravou um
MP4 h264 válido, com duração e dimensões positivas, mostrando **dois**
quadrados quando a especificação pede um único objeto que se move.

A primeira tentativa de fechar isso usou um classificador escrito à mão sobre
`scipy.ndimage`. Ele foi medido e tinha dois defeitos reais:

1. **Rotação.** Os descritores vinham da caixa delimitadora alinhada aos eixos,
   que não é invariante a rotação. Um quadrado girado 10° era classificado como
   `polygon`, então um `expect` pedindo `square` **rejeitaria um render
   correto**. Não é hipotético: o modelo gerou `square.rotate(PI/4)` na execução
   `a3a79800`.
2. **Conectividade.** `ndimage.label` usa 4-conectividade por padrão, e um
   contorno diagonal de 1px se estilhaça — 132 componentes a 20°, 220 no
   losango. Todos caíam abaixo do filtro de área e **a forma sumia** do
   storyboard. Latente com o traço padrão de 4px do Manim, mas real.

## Decisão

### Formato: o do Manim, sem alteração

Frames trafegam e são persistidos como `(n_frames, height, width, 4)` uint8
RGBA sob a chave `frame_data` num `.npz` comprimido — exatamente o formato de
`manim.utils.testing._frames_testers`. As tolerâncias de comparação exata,
onde ela se aplica, são as constantes do Manim
(`FRAME_ABSOLUTE_TOLERANCE = 1.01`, `FRAME_MISMATCH_RATIO_TOLERANCE = 1e-5`),
reusadas e não reinventadas.

Consequência prática: o `frames.npz` que cada tentativa preserva é legível
pelas ferramentas do próprio Manim, e o control data do Manim é legível por nós.

### Ground truth: control data do Manim

`tests/golden/` vendoriza sete arquivos de
`ManimCommunity/manim@cafc63919eb0`, `tests/test_graphical_units/control_data`
(MIT, atribuição em `tests/golden/LICENSE`). São renders validados pelo próprio
projeto Manim, **rotulados pelo nome da cena**: `geometry/Circle.npz` é um
círculo, `transform/Transform.npz` é uma transformação real em 7 frames.

Não vêm no wheel do PyPI — `frames_comparison` aponta para
`manim/control_data/graphical_units_data`, que não existe numa instalação — por
isso são vendorizados.

`expected.json` declara o que cada cena é, escrito a partir da cena e conferido
visualmente e por medição de centroide, nunca a partir da saída do observador.

### Medição: `opencv-python-headless`

Todo descritor vem de `cv2.minAreaRect`, a caixa mínima **rotacionada**, o que
torna proporção e preenchimento invariantes a rotação numa chamada. Contornos
(`findContours`) são inerentemente 8-conectados, o que elimina o defeito de
conectividade por construção. `approxPolyDP` conta vértices,
também invariante a rotação.

Comparação real considerada:

| | scikit-image 0.26 | opencv-python-headless 5.0 | opencv-python 5.0 |
|---|---|---|---|
| wheel x86_64 | 13.7 MB | 56.6 MB | 71.1 MB |
| novos pacotes na árvore | 4 | **0** (só numpy, já presente) | 0 |
| GUI GTK/Qt | não | **não** | sim (+14.5 MB) |
| primitiva de rotação | composição (`approximate_polygon` + `regionprops`) | **`minAreaRect` direto** | idem |

Escolhido `opencv-python-headless`: zero nomes novos na árvore de dependências,
e a primitiva direta para o defeito que motivou a troca. A variante *headless*
existe justamente para não arrastar GUI em servidor; o vídeo continua sendo
decodificado pelo `ffmpeg` de linha de comando, não pelo `VideoCapture`.

`pillow` permanece declarado (usado pelos testes para gerar frames sintéticos);
`scipy` deixa de ser dependência direta.

## Alternativas descartadas

- **scikit-image**: 4x menor, mas arrasta 4 pacotes novos (imageio, tifffile,
  lazy-loader, networkx) e não tem equivalente direto a `minAreaRect`.
- **Classificador próprio sobre scipy**: dependência zero, mas foi o que
  falhou nos dois defeitos medidos acima.
- **Modelo de visão local (VLM)**: nenhum modelo de visão disponível no Ollama
  local (só `qwen2.5-coder:7b` e `ornith-1.5:9b`), e um juiz probabilístico
  contradiz a tese do RITL, em que a observação determinística decide.
- **`frames_comparison` do Manim para julgar cena gerada**: impossível, e a
  razão está medida abaixo.

## Consequências

- **Comparação exata de frame não julga cena gerada por LLM.** Dois renders
  ambos semanticamente corretos da mesma especificação foram medidos divergindo
  em **2,72% dos pixels — 2.719x acima da tolerância do Manim** — porque o
  modelo escolhe raio, cor, traço e timing diferentes a cada tentativa. O
  golden set valida o **observador**; a fidelidade de uma cena nova é decidida
  por `expectations.py`. Comparação exata continua aplicável onde o render é
  determinístico, como o teste de integração com provider fixo.
- O vocabulário é estreito por decisão: `circle`, `square`, `polygon`. Não
  reconhece texto, cor nem geometria arbitrária.
- Duas formas que se sobreponham **exatamente** ainda leem como uma região. A
  análise estática do `scene.py` continua como segunda camada por isso.
- `observation.py` não satisfaz `disallow_any_expr` do mypy, porque os tipos
  públicos do OpenCV e do NumPy carregam `Any`. Mesma classe de dívida já
  registrada em `.claude/tmp/doubts-render-in-the-loop-tracer.md`.
