# Fundação do projeto

O produto transforma um projeto audiovisual canônico em um run revisável e,
depois de uma aceitação explícita, em um golden imutável. `project.json` é a
fonte de identidade e de estado; roteiros, áudio, timeline, cenas e runs são
referenciados por paths relativos seguros e hashes recalculados na aceitação.

## Fluxo público

```text
init PROJECT --title TITLE --script SCRIPT --audio AUDIO
    ↓
timeline validate PROJECT/project.json
    ↓ (se candidate, revisão manual)
timeline confirm PROJECT/project.json
    ↓
render PROJECT/project.json [--scene ID]
    ↓
inspect PROJECT/project.json
    ↓
accept PROJECT/project.json --run RUN_ID
```

`init` copia script e áudio para o projeto e cria `project.json`, timeline,
planos, briefs e expectations quando o roteiro permite. Uma timeline heurística
fica como `candidate` e exige confirmação; apenas `confirmed` pode entrar em
render. `render` preserva cada tentativa e deixa candidatos dentro de
`artifacts/<run-id>`. `inspect` expõe o estado e os paths do run atual.

## Formato determinístico do roteiro

O arquivo indicado por `--script` deve ser UTF-8 e é copiado, byte a byte, para
`script.md`. Headings Markdown `#`/`##` delimitam cenas; o corpo depois dos
metadados é a narração exata, sem reescrita. `@objective` é opcional. O par
completo `@start`/`@end` é obrigatório em todos os headings quando há qualquer
timestamp. Pares completos, contíguos, iniciando em zero e cobrindo toda a
duração do áudio formam a timeline confirmada pelo autor:

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

Quando nenhum heading contém timestamp, a timeline é `candidate`: uma detecção
de pausas fakeável fornece intervalos; alvos ponderados pela contagem de
palavras usam a pausa mais próxima de modo determinístico e recorrem a limites
proporcionais quando necessário. Se qualquer `@start` ou `@end` aparecer, o
par completo é obrigatório em todos os headings; metadados parciais ou mistos
são erro, não fallback. A correspondência candidate é aproximada, sempre
requer confirmação manual e não é ASR nem forced alignment. `.txt` ou texto
sem headings usa blocos separados por linhas em branco e o mesmo fallback.

## Inspect e interrupção

`inspect` é leitura pura. Ele resume áudio e duração, status/método/duração e
limitações da timeline, `current_scene` do projeto/run, progresso agregado,
estado/tentativas/erro/próxima ação de cada cena, correção temporal, ciclo de
composição e validação final. Evidência JSON ausente ou inválida vira um
status/erro legível; inspect não dispara provider, renderizador ou probing.

Uma interrupção deixa o mesmo run `rendering`, com `current_scene` escrito antes
da fronteira longa. O próximo render retoma esse run, verifica cenas prontas,
preserva evidências parciais e escolhe um caminho interno livre; ao terminar,
limpa `current_scene`. Somente `accept` promove candidatos para fontes/golden.

## Decisão de migração

`Project`/`Timeline` são canônicos e cada cena usa `SceneSpec`/`ScenePlan`; isso
substituiu o entrypoint obsoleto baseado em arquivo de especificação isolado.
Não existem `VideoSpec`, `load_video_spec`, `load_scene_spec`, `video-run.json`
ou `artifacts/videos` como aliases ou caminhos do produto.

## Estrutura canônica

```text
projects/YYYY_slug/
├── project.json
├── script.md
├── audio/<narration>
├── timeline.json
├── scenes/<scene>/
│   ├── plan.json
│   ├── brief.json
│   ├── expectations.json
│   ├── scene.py             # após accept
│   └── code-provenance.json # após accept
├── artifacts/<run-id>/
│   ├── run.json
│   ├── scenes/<scene>/... candidatos e evidências ...
│   ├── composition.json
│   └── final.mp4
└── golden/
    ├── manifest.json
    └── accepted/<run-id>/... snapshots do pacote ...
```

O candidato de código e sua proveniência pertencem ao run até o preflight de
`accept`. A promoção publica fontes e documentos permanentes em uma transação
lógica de payloads; falhas preservam a publicação anterior e não removem
candidatos. O manifest e os snapshots aceitos são validados sem provider,
modelo ou execução de mídia.

## Contratos

- `Project` usa estados explícitos de timeline, render e composição; run
  solicitado, status e hashes devem coincidir antes de qualquer publicação.
- A validação pública do golden usa o envelope `golden.manifest/1`, `version: 1`,
  `profile: visual|audiovisual`, `status: accepted`, `project_id`, `title` e
  `capabilities`. O dispatch por profile é explícito; profile ausente ou
  desconhecido é erro.
- A aceitação valida timeline, script, áudio, pacote de cena, candidatos,
  composição e fatos finais. A validação posterior recompõe os mesmos hashes e
  lê os snapshots imutáveis do golden.
- O Qwen é uma fronteira local opcional para gerar ou corrigir somente código
  visual. Timeline, áudio, aceitação e validação são contratos determinísticos.

## Segurança de desenvolvimento

Os testes normais usam fakes para provider, Manim, FFmpeg, ffprobe e sensores.
Não fazem rede, download, inferência ou mídia real. O sandbox executa testes
`not integration`, Ruff em todo o repositório e mypy somente nos módulos
tipados declarados; as fronteiras dinâmicas são cobertas por testes
comportamentais e Ruff.
