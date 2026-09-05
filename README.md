# Esteira de Vídeos

Pipeline local para transformar um projeto audiovisual editável em cenas Manim
aceitas e em um MP4 final. O projeto é a fonte de identidade; cada execução
preserva seus candidatos, fatos e decisões para revisão.

## Fluxo canônico

Inicialize um diretório de projeto com roteiro e narração:

```bash
video-pipeline init projects/2026_vetores \
  --title "Vetores" \
  --script roteiro.md \
  --audio narracao.wav
```

Valide a timeline criada a partir do roteiro:

```bash
video-pipeline timeline validate projects/2026_vetores/project.json
```

Se o resultado for `TIMELINE: CANDIDATE`, a revisão manual é obrigatória antes
de confirmar:

```bash
video-pipeline timeline confirm projects/2026_vetores/project.json
```

Depois da confirmação, renderize, inspecione o run e aceite somente um run
pronto:

```bash
video-pipeline render projects/2026_vetores/project.json --max-attempts 3
video-pipeline inspect projects/2026_vetores/project.json
video-pipeline accept projects/2026_vetores/project.json --run run-001
```

`render --scene ID` pode ser usado para revisar uma cena, mas não substitui a
timeline confirmada nem promove candidatos automaticamente.

## Interface local de operação

A mesma sequência canônica está disponível em uma UI local, sem upload e sem
servidor externo:

```bash
video-pipeline web --projects-root projects --audio-root audio --port 8000
```

Abra `http://127.0.0.1:8000/`. A aplicação cria o projeto, confirma a timeline,
enfileira render e regeneração seletiva, restaura revisões e aceita o run pronto.
Ela só serve arquivos estáticos empacotados e MP4s referenciados por IDs opacos;
paths do host não entram no HTML nem no JSON público. Para aceitar um golden,
declare ao menos uma `@capabilities` suportada em cada cena do roteiro.

O catálogo de áudio é uma dependência operacional: se estiver ausente ou vazio,
`GET /api/audio` e a UI informam que é preciso adicionar pelo menos um arquivo
de narração antes de criar ou renderizar um vídeo real. A aplicação não fabrica
áudio nem usa um placeholder silencioso.

O estado da sessão web persiste dentro de cada projeto em `ui/`: manifests
imutáveis `revisions/vNNN.json`, um ponteiro mutável em `index.json` e drafts
recuperáveis em `working/`. Um render sempre cria/preserva um run em
`artifacts/<run-id>/` e publica uma revisão quando termina; uma interrupção é
recuperada como `interrupted` e só continua após um retry explícito. `checkout`
altera apenas o ponteiro da revisão selecionada, sem apagar histórico, runs ou
golden. `render` e `regenerate` produzem candidatos revisáveis e nunca publicam
golden; `accept` é a única operação que chama a aceitação canônica e promove o
run pronto para `golden/`.

## Roteiro UTF-8 e timeline determinística

`--script` recebe um arquivo UTF-8. O conteúdo narrado depois dos metadados é
preservado como texto exato da cena (inclusive acentos e quebras internas), e
o arquivo de entrada é copiado para `script.md` no projeto. Headings Markdown
`#` ou `##` delimitam as cenas. Cada heading pode ter um `@objective` opcional
e, para uma timeline confirmada pelo autor, deve ter o par completo
`@start`/`@end`:

```markdown
# Abertura
@capabilities: basic_geometry, typography
@objective: Apresente a ideia.
@start: 0
@end: 4
A origem é mostrada exatamente nesta cena.

## Soma
@capabilities: equations
@start: 4
@end: 10
Agora a soma é explicada passo a passo.
```

`@capabilities` é opcional e aceita IDs separados por vírgulas na ordem
autoral. Cada ID precisa existir no registry de capacidades e estar marcado como
`supported`; IDs desconhecidos ou ainda não suportados são erro. Se uma cena
declarar capabilities, todas as cenas do roteiro devem declarar uma lista
(não há default global nem herança entre cenas). Roteiros sem nenhuma
declaração continuam válidos e persistem `capabilities: []` em cada plano.

Quando todos os pares estão completos, os intervalos precisam começar em zero,
ser contíguos e terminar na duração do áudio; esse documento é uma timeline
confirmada. Somente um roteiro sem qualquer timestamp segue o caminho
`candidate`: usa a detecção local de pausas (uma fronteira fakeável), alvos
proporcionais ao peso de palavras, a pausa mais próxima de cada alvo e fallback
proporcional quando não há pausa elegível. Qualquer uso de `@start` ou `@end`
exige o par completo em todos os headings; metadados parciais ou mistos são
erro, não fallback. O resultado candidate é sempre aproximado e exige revisão
manual; não é ASR nem forced alignment.

Um arquivo `.txt`, ou qualquer roteiro sem headings, usa blocos de texto
separados por linhas em branco como cenas e segue o mesmo caminho de candidate,
com fallback proporcional quando necessário.

## Estrutura persistida

Um projeto audiovisual canônico contém:

```text
projects/2026_vetores/
├── project.json
├── script.md
├── audio/narration.wav
├── timeline.json
├── scenes/<scene>/
│   ├── plan.json
│   ├── brief.json
│   ├── expectations.json
│   ├── scene.py             # após accept
│   └── code-provenance.json # após accept
├── artifacts/<run-id>/
│   ├── run.json
│   ├── composition.json
│   ├── final.mp4
│   └── scenes/<scene>/... evidências e candidatos ...
├── ui/
│   ├── index.json            # ponteiro de checkout
│   ├── revisions/vNNN.json   # histórico create-once
│   └── working/<job-id>.json # jobs recuperáveis
└── golden/
    ├── manifest.json
    └── accepted/<run-id>/... snapshots imutáveis ...
```

O render escreve candidatos dentro do próprio run. `accept` valida hashes,
paths, timeline, pacote de cena, composição e fatos finais antes de publicar
fontes e documentos permanentes em uma transação lógica. O manifest usa o
envelope `golden.manifest/1`, com `version: 1`, `profile: audiovisual`,
`status: accepted`, identidade do projeto e
capacidades. A validação do golden é model-free: lê snapshots e arquivos,
recalcula hashes e não executa provider, cena ou mídia.

## Inspect e retomada

`inspect` é somente leitura e não executa provider, Manim, FFmpeg ou ffprobe.
Seu resumo mostra fatos e duração do áudio; status, método, duração e
limitações da timeline; `current_scene` do projeto e do run; progresso agregado;
estado, tentativas, erro e próxima ação de cada cena; correção temporal; e o
ciclo de composição com saída e resumo da validação final. JSON ausente ou
malformado aparece como status/erro no resumo, sem fazer probing. O run também
expõe `action_next` para orientar a operação seguinte.

Uma interrupção durante a geração deixa o mesmo run em `rendering`, com
`current_scene` persistido antes da fronteira do provider. Um novo `render`
retoma esse run, verifica e reutiliza cenas já prontas, preserva candidatos e
evidências parciais e escolhe um caminho interno livre para a tentativa
interrompida. Só `accept` publica fontes e golden; renderizar não promove uma
aceitação.

## Migração de especificação

`Project` e `Timeline` são os contratos canônicos; cada cena é descrita no
nível de cena por `SceneSpec`/`ScenePlan`. Essa arquitetura substituiu o
entrypoint obsoleto de um arquivo de especificação independente. Não há
`VideoSpec`, `load_video_spec`, `load_scene_spec`, `video-run.json` nem
`artifacts/videos` no fluxo atual.

## Contratos visuais

Planos explícitos (`ScenePlan`) carregam `VideoTheme`, capacidades, objetos com
IDs, beats, regiões, expectativas e continuidade. A heurística pode sugerir
um plano, mas um timeline candidato nunca é tratado como confirmado.
`VisualScene` registra estados, animações e evidências no runtime; críticos
determinísticos cobrem safe area, clipping, overlap, contraste, legibilidade,
ritmo e continuidade declarada.

O Qwen, quando configurado no ambiente local, é usado somente para gerar ou
corrigir código visual. Narração, timeline, aceitação e validação têm contratos
determinísticos próprios. Os testes normais substituem provider, Manim, FFmpeg,
ffprobe e sensores por fakes; não fazem inferência, rede ou download.

## Desenvolvimento seguro

```bash
.claude/scripts/sandbox.sh
```

O sandbox executa apenas testes não-integração, Ruff e mypy no núcleo tipado.
O comando bare `rtk .venv/bin/mypy` usa exatamente os mesmos 13 módulos
mantidos pelo sandbox, definidos em `pyproject.toml`; os demais módulos e testes
atravessam JSON/mídia, subprocessos ou adapters dinâmicos e são cobertos por
Ruff e testes comportamentais. Integrações reais, modelos, rede e mídia ficam
fora dos gates locais seguros.

Os testes locais da UI usam fakes e cobrem assets, HTTP, fila, recuperação e
revisões; não executam rede, modelo, Manim ou FFmpeg reais. A evidência de
navegador real é opt-in e roda exatamente com:

```bash
rtk .venv/bin/pytest -q tests/integration/test_web_e2e.py
```

Esse teste inicia o geckodriver real em `/snap/bin/geckodriver` e usa
`_firefox_binary()` para verificar `/usr/bin/firefox`: se esse launcher for um
ELF, ele é usado diretamente; se for um wrapper Snap, o teste passa o ELF
`/snap/firefox/current/usr/lib/firefox/firefox` em `moz:firefoxOptions.binary`.
Ele usa `WebService` e `ThreadingHTTPServer` reais. O único fake é a fronteira
de `VideoPipeline`, que grava MP4s determinísticos para provar criação,
confirmação, polling, URLs de playback, regeneração seletiva, histórico/checkout,
stale polling guard, reload/restart e retry de draft interrompido sem Ollama,
Manim, FFmpeg ou ffprobe. O teste é pulado somente quando `/usr/bin/firefox`
ou `/snap/bin/geckodriver` não existem ou não são executáveis; falhas de setup
depois dessa verificação fazem o teste falhar.
