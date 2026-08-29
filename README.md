# Esteira de Vídeos

Pipeline Python que transforma uma especificação de uma cena em um vídeo MP4
verificado pelo Manim Community e pelo `ffprobe`. O conteúdo/roteiro entra já
escrito; este primeiro tracer bullet atende uma cena por execução.

## Operação exata

No ambiente local, instale o pacote e execute:

```bash
uv sync
video-pipeline render examples/acceptance-scene.json
```

O comando carrega a especificação estrita, pede Python Manim ao Ollama,
descarrega explicitamente o modelo antes do render, executa o Manim de forma
limitada, valida o MP4 com `ffprobe`, confere a semântica de cena do código
gerado e, quando necessário, envia o código e o diagnóstico completo para uma
nova tentativa. Cada execução fica em
`artifacts/runs/<run-id>/`, com `run.json` e diretórios `attempt-01`,
`attempt-02`, etc. O terminal imprime `SUCCESS`, `ATTEMPTS_EXHAUSTED`,
`PROVIDER_ERROR` ou `SENSOR_ERROR`, além do caminho do run e do MP4 aceito.

Opções operacionais:

```bash
video-pipeline render scene.json \
  --model qwen2.5-coder:7b \
  --base-url http://localhost:11434 \
  --provider-timeout 120 \
  --render-timeout 120 \
  --max-attempts 3 \
  --temperature 0.0 \
  --seed 42 \
  --output-root artifacts/runs
```

`temperature` e `seed` são enviados explicitamente nas `options` do Ollama e
preservados em cada `request.json`. Isso permite comparar corpus e temperatura
sem depender de defaults ocultos do modelo.

Para cenas de IA/matemática, `topics` seleciona um conjunto pequeno de padrões
adaptados do código autorizado do 3Blue1Brown e já validado em Manim Community.
`reference_examples: 0` desliga o few-shot; o padrão é 2. Consulte
`docs/reference-corpus.md` para o catálogo, o estudo pareado e a proveniência.

## O que decide uma tentativa

Uma tentativa só é aceita quando **todas** as condições valem:

1. o Manim sai com código zero;
2. o `ffprobe` observa um MP4 não vazio, com stream de vídeo, dimensões e
   duração positivas;
3. os frames do vídeo renderizado satisfazem os `expect` da especificação;
4. o `scene.py` gerado não anima um mobject que nunca entrou em cena.

As condições 3 e 4 existem porque **o Manim sai com zero e grava um MP4 válido
para uma cena errada**. O caso clássico é continuar animando `b` depois de
`self.play(Transform(a, b))`, quando quem está em cena é `a`: o resultado é um
vídeo perfeitamente decodificável mostrando dois objetos onde a especificação
pede um. Sem 3 e 4, o pipeline entregaria isso em silêncio.

As duas checagens são independentes de propósito. A condição 4 lê o código; a
condição 3 lê os pixels do vídeo que de fato saiu, então também pega erros que
nenhuma análise estática enxergaria.

Toda rejeição da cena vira diagnóstico estruturado, volta para o provider junto
com o código anterior e a linha exata apontada pelo traceback do Manim, e a
próxima tentativa corrige. Uma falha do próprio sensor termina separadamente e
não culpa o código nem gasta outra chamada ao modelo.

## Verificação semântica (`expect`)

O bloco `expect` é opcional. Sem ele o contrato é só de renderabilidade; com
ele o vídeo é lido de volta frame a frame e conferido:

```json
{
  "schema_version": "1.0",
  "scene_name": "AcceptanceScene",
  "description": "Mostre um círculo no centro. Depois transforme-o em um quadrado e mova-o para a direita.",
  "expect": {
    "max_shapes": 1,
    "beats": [
      {"shape": "circle", "region": "center"},
      {"shape": "square", "region": "center"},
      {"shape": "square", "moved": "right"}
    ]
  }
}
```

- `max_shapes` — quantas formas podem estar visíveis ao mesmo tempo. É o que
  pega o objeto duplicado.
- `beats` — o que precisa aparecer, **na ordem escrita**. Cada beat casa como
  subsequência dos frames amostrados, então restringe o quê e em que ordem,
  não o instante exato.
- `shape` — `circle`, `square`, `polygon` ou `any`.
- `color` — cor observada da forma: `red`, `orange`, `yellow`, `green`, `teal`,
  `blue`, `purple`, `pink`, `white`, `grey`, `black`. Opcional por beat; sem
  ele qualquer cor serve. Uma mudança de cor se escreve como dois beats.
- `region` — posição absoluta em terços do frame: `left`/`center`/`right`,
  `top`/`middle`/`bottom`.
- `moved` — direção do deslocamento **relativa ao beat anterior**
  (`left`/`right`/`up`/`down`), útil porque a descrição pede uma direção, não
  uma posição final.
- `latex` — fórmula `MathTex` com `tex`, `font_size`, `color`, `x`, `y` e
  `min_iou` fixados. O pipeline renderiza uma referência e compara máscara e
  cor com o vídeo real; sem tipografia fixada não há aceitação automática de
  conteúdo textual.
- `text` — conteúdo exato renderizado com `Text` ou `Tex`. Ambos exigem
  `renderer`, `content`, `font_size`, `color`, `x`, `y` e `min_iou`; `Text`
  exige ainda uma `font` Pango explícita. Conteúdo multilinha é aceito. Presença
  e posição são verificadas pelo mesmo match visual fixado.

Exemplo de texto determinístico:

```json
{
  "renderer": "text",
  "content": "Camada de entrada\nCamada de saída",
  "font": "DejaVu Sans",
  "font_size": 36,
  "color": "white",
  "x": 0.0,
  "y": -2.5,
  "min_iou": 0.95
}
```

Os frames amostrados e o veredito ficam em
`artifacts/runs/<run>/attempt-NN/observation/` e `observation.json`, de modo que
dá para olhar exatamente o que o verificador olhou.

Os frames trafegam e são gravados no **formato de control data do Manim** —
`(n_frames, height, width, 4)` uint8 RGBA sob a chave `frame_data` num `.npz` —
de modo que o `frames.npz` de cada tentativa é legível pelas ferramentas do
próprio Manim. O leitor é validado contra o control data oficial do Manim,
vendorizado em `tests/golden/` (ver o README de lá).

`video-pipeline calibrate` transforma esse golden set em um relatório de
TP/FP/FN/TN por eixo. O observador de frames e o sensor de texto retornam o
mesmo contrato `SensorResult`: evidência ou falha explícita, nunca ambos. O
matcher semântico só recebe evidência bem-sucedida. Falha de extração ou de
referência textual termina como `SENSOR_ERROR`, separada de uma rejeição
semântica e sem gastar outra chamada ao Qwen.

Medição por contornos do OpenCV: todo descritor vem de `cv2.minAreaRect`, a
caixa mínima **rotacionada**, então um quadrado é um quadrado em qualquer
ângulo.

A cor é nomeada em HSV sobre os pixels efetivamente desenhados da forma —
traço mais preenchimento, excluindo o fundo que ela cerca. O antisserrilhamento
mistura o traço com o fundo e derruba a mediana (um traço branco do Manim lê
mediana 0,60 de valor, que nomearia cinza), então a decisão usa o **percentil
90** de saturação e de valor: os pixels mais fortes são o traço, os fracos são
a mistura. Os limites de matiz ficam nos pontos médios entre as constantes
medidas da paleta do Manim, e `tests/test_observation.py` verifica a paleta
inteira, em traço e em preenchimento.

**Limites, por decisão:** o vocabulário de formas é `circle`, `square`,
`polygon` mais as onze cores acima. Não usa OCR nem reconhece texto de
tipografia livre; `MathTex`, `Tex` e `Text` só são verificados quando conteúdo,
tipografia, cor e posição estão fixados na spec. Duas formas exatamente
sobrepostas leem como uma — por isso a checagem estática do código continua
como segunda camada.
Comparação exata de frame não julga cena gerada: dois renders ambos corretos da
mesma spec divergem em 2,72% dos pixels, 2.719x acima da tolerância do Manim.
Ver `docs/adr/0002-frame-observation-dependencies.md`.

O exemplo de aceitação contém a descrição em português: “Mostre um círculo no centro. Depois transforme-o em um quadrado e mova-o para a direita.”

`examples/colored-scene.json` exercita o vocabulário de cor: círculo azul no
centro, vira quadrado vermelho, move para a direita. Rodada real com
`qwen2.5-coder:7b`, convergiu na tentativa 3 — as duas primeiras foram
rejeitadas por “frame 10 mostra 2 formas (red square, red square)”.

## Aviso de confiança

O Python gerado é executado localmente como código confiável no MVP. Não use
prompts ou endpoints remotos não confiáveis sem isolamento de sistema/container;
essa proteção está fora do escopo desta primeira versão.

Generated Python remains trusted local execution in this MVP; do not run
untrusted remote prompts without OS/container isolation.

## Stack

- Python 3.13
- Manim Community 0.21.0
- Pydantic 2.12.4
- Ollama (modelo padrão `qwen2.5-coder:7b`)
- FFmpeg/`ffprobe`
- OpenCV (headless), NumPy e Pillow (leitura dos frames renderizados)
