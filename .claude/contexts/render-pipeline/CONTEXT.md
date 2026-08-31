# Pipeline de renderização

Este contexto usa o RTL canônico de `origin/feat/render-in-the-loop-tracer@2fcc38f`.
É dono do fluxo confirmado de cenas visuais até um run pronto, inclusive
tentativas, observação, composição, retomada e aceitação. A Web UI adapta esse
fluxo; não cria outro `Project`, `Timeline`, segmentação ou renderer.

## Contrato operacional

- O CLI recebe `PROJECT/project.json`; a timeline precisa estar `confirmed`
  antes da geração ou correção de código, render e composição.
- `render` percorre as cenas canônicas na ordem da timeline e preserva request,
  resposta, código candidato, render, validação, observação, diagnóstico e
  qualidade em `PROJECT/artifacts/<run-id>`.
- Cada cena só fica pronta após código de render válido, validações
  independentes, expectations e gates visuais determinísticos. Candidatos não
  são escritos em paths permanentes durante o render.
- O provider é descarregado antes da fronteira de render. O Qwen/Ollama local só
  gera ou corrige código visual; os testes substituem provider e subprocessos
  por fakes determinísticos.
- A composição recebe apenas cenas aceitas e a narração copiada no projeto. O
  MP4 final `PROJECT/artifacts/<run-id>/final.mp4` é validado novamente antes de
  o run chegar a `ready`.
- `inspect` lê exclusivamente documentos persistidos do `PROJECT` e do run;
  não dispara provider, Manim, FFmpeg ou ffprobe.
- `accept --run RUN_ID` exige o run atual pronto, hashes e fatos finais íntegros.
  Uma transação lógica promove candidatos, `project.json` e manifest; falha em
  qualquer replace restaura os destinos anteriores e mantém candidatos do run.

## Entrada de roteiro e timeline

O arquivo de `init` é UTF-8 determinístico. Headings Markdown `#`/`##`
delimitam cenas, o corpo narrado é preservado exatamente, `@objective` é
opcional e um timestamp autoral só é aceito como par completo `@start`/`@end`
em todas as cenas. Intervalos que começam em zero, são contíguos e cobrem a
duração do áudio produzem timeline confirmada:

```markdown
# Abertura
@objective: Apresente a ideia.
@start: 0
@end: 4
A origem é mostrada exatamente nesta cena.

## Soma
@start: 4
@end: 10
Agora a soma é explicada passo a passo.
```

Somente sem qualquer timestamp o pipeline produz `candidate` para revisão
manual. A detecção local de pausas é uma fronteira fakeável; cada alvo de
fronteira é ponderado por palavras, escolhe a pausa mais próxima de forma
determinística e usa fallback proporcional quando não houver pausa elegível.
Se qualquer `@start` ou `@end` aparecer, o par completo é obrigatório em todos
os headings; metadados parciais ou mistos são erro, não fallback. Isso não é
ASR nem forced alignment. `.txt`/roteiro sem headings usa blocos separados por
linhas em branco e segue o mesmo caminho candidato.

## Inspect e retomada

`inspect` é somente leitura: não executa provider, Manim, FFmpeg ou ffprobe.
O contrato de saída resume fatos/duração do áudio, status/método/duração e
limitações da timeline, `current_scene` do projeto e do run, contagem de
progresso, estado/tentativas/ação/erro por cena, correção temporal e composição
(lifecycle, saída e validação final compacta). Evidência JSON ausente ou
malformada é reportada como status/erro, não executada; `run.action_next` indica
a próxima operação.

Antes de cada fronteira longa, `render` persiste projeto e run juntos. Uma
interrupção mantém o run `rendering` e o `current_scene`; a chamada seguinte
retoma o mesmo run, reutiliza e verifica cenas `ready`, preserva evidências
parciais e aloca caminho interno livre para a tentativa interrompida. Um run
pronto limpa `current_scene`; apenas `accept` publica candidatos e golden.

Uma regeneração solicitada pela Web UI é uma operação adicional sobre esse
contrato: exige timeline confirmada e run-base `ready` do mesmo `PROJECT`, cria
um novo run em `PROJECT/artifacts`, valida hashes dos irmãos e renderiza só a
cena escolhida. O run-base, seus irmãos e suas evidências permanecem
imutáveis.

## Migração canônica

O fluxo usa `Project`/`Timeline` e `SceneSpec`/`ScenePlan` por cena, no lugar do
entrypoint obsoleto de arquivo de especificação independente. Não há aliases ou
caminhos `VideoSpec`, `load_video_spec`, `load_scene_spec`, `video-run.json` ou
roots globais de artefatos.

## Evidência e caminhos

```text
PROJECT/
├── project.json
├── script.md
├── audio/<narration>
├── timeline.json
├── scenes/<scene>/
│   ├── plan.json
│   ├── brief.json
│   └── expectations.json
├── artifacts/<run-id>/
│   ├── run.json
│   ├── scenes/<scene>/
│   │   ├── scene.py
│   │   ├── code-provenance.json
│   │   └── ... evidências por tentativa ...
│   ├── composition.json
│   └── final.mp4
├── ui/                         # manifests da Web UI; contexto web-ui
│   ├── index.json
│   ├── revisions/vNNN.json
│   └── working/<job-id>.json
└── golden/
    ├── manifest.json
    └── accepted/<run-id>/... snapshots imutáveis ...
```

O manifest aceito usa o envelope comum `golden.manifest/1` com `version: 1`,
profile explícito (`visual` ou `audiovisual`), status `accepted`, identidade,
capacidades, hashes, composição, fatos finais e snapshots do pacote.
Discovery, leitura e validação aplicam o mesmo dispatch por profile e falham
para profile ausente ou desconhecido.

## Módulos sob responsabilidade

- Núcleo do fluxo: `video.py`, `pipeline.py`, `project.py`, `timeline.py` e
  `golden.py`.
- Contratos visuais e expectativas: `spec.py`, `scene_plan.py`, `theme.py`,
  `expectations.py`, `capabilities.py`.
- Evidência e sensores: `rendering.py`, `validation.py`, `observation.py`,
  `latex_validation.py`, `quality.py`, `critics.py`, `runtime.py`,
  `continuity.py` e `multimodal.py`.
- CLI público: `init`, `timeline validate`, `timeline confirm`, `render`,
  `inspect` e `accept`.
- Web UI: callbacks finos e adaptação de serviço são consumidores deste
  contexto; jobs/revisões/HTTP pertencem ao contexto Web UI.

## Gates seguros

Fakes cobrem provider, subprocessos, filesystem de publicação, Manim, FFmpeg,
ffprobe e sensores nos testes normais. Não usar rede, download, modelos ou
mídia real nos gates locais. O sandbox mantém mypy apenas para os módulos com
contratos tipados; golden/runtime, pixel/AST e as fronteiras provider,
rendering e CLI são validados por Ruff e testes comportamentais por suas APIs
dinâmicas.
