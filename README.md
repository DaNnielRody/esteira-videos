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
`attempt-02`, etc. O terminal imprime `SUCCESS`, `ATTEMPTS_EXHAUSTED` ou
`PROVIDER_ERROR`, além do caminho do run e do MP4 aceito.

Opções operacionais:

```bash
video-pipeline render scene.json \
  --model qwen2.5-coder:7b \
  --base-url http://localhost:11434 \
  --provider-timeout 120 \
  --render-timeout 120 \
  --max-attempts 3 \
  --output-root artifacts/runs
```

## O que decide uma tentativa

Uma tentativa só é aceita quando **todas** as condições valem:

1. o Manim sai com código zero;
2. o `ffprobe` observa um MP4 não vazio, com stream de vídeo, dimensões e
   duração positivas;
3. o `scene.py` gerado não anima um mobject que nunca entrou em cena.

A terceira condição existe porque o Manim sai com zero e grava um MP4 válido
para uma cena que anima um objeto invisível — o caso clássico é continuar
animando `b` depois de `self.play(Transform(a, b))`, quando quem está em cena
é `a`. Sem essa checagem o pipeline entregaria, em silêncio, um vídeo que não
corresponde à especificação.

Toda condição violada vira diagnóstico estruturado, volta para o provider junto
com o código anterior e a linha exata apontada pelo traceback do Manim, e a
próxima tentativa corrige. Nada disso depende do modelo acertar de primeira.

O exemplo de aceitação contém a descrição em português: “Mostre um círculo no centro. Depois transforme-o em um quadrado e mova-o para a direita.”

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
