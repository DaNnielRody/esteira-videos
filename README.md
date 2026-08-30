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

## Roteiro UTF-8 e timeline determinística

`--script` recebe um arquivo UTF-8. O conteúdo narrado depois dos metadados é
preservado como texto exato da cena (inclusive acentos e quebras internas), e
o arquivo de entrada é copiado para `script.md` no projeto. Headings Markdown
`#` ou `##` delimitam as cenas. Cada heading pode ter um `@objective` opcional
e, para uma timeline confirmada pelo autor, deve ter o par completo
`@start`/`@end`:

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
└── golden/
    ├── manifest.json
    └── accepted/<run-id>/... snapshots imutáveis ...
```

O render escreve candidatos dentro do próprio run. `accept` valida hashes,
paths, timeline, pacote de cena, composição e fatos finais antes de publicar
fontes e documentos permanentes em uma transação lógica. O manifest usa o
envelope comum `golden.manifest/1`, com `version: 1`, `profile: visual` ou
`profile: audiovisual`, `status: accepted`, identidade do projeto e
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
Integrações reais, modelos, rede e mídia ficam fora dos gates locais seguros.
