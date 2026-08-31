# Video Flow UI — MVP design contract

## Intent

A operação deve responder, sem abrir arquivos: qual `PROJECT` está aberto, qual
é o estado da `Timeline`, qual run/revisão está selecionado, qual cena canônica
está em foco e qual MP4 real está sendo reproduzido. É uma superfície de
operação, não um novo editor ou renderer.

## Canonical model

- `PROJECT/project.json` e `PROJECT/timeline.json` são a identidade e a
  segmentação canônicas. A UI apenas expõe as cenas já produzidas por
  `initialize_project`; não cria outro `Project` nem outra segmentação.
- Cada cena da timeline aponta para um run em
  `PROJECT/artifacts/<run-id>/scenes/<scene>/`. O run preserva `run.json`,
  evidências e `composition.json`.
- Quando o run está `ready`, o canvas principal reproduz o
  `PROJECT/artifacts/<run-id>/final.mp4` validado. A rail de cenas reproduz os
  `normalized.mp4` reais quando necessário.
- Revisões da sessão são manifests create-once em
  `PROJECT/ui/revisions/vNNN.json`, com ponteiro em `PROJECT/ui/index.json`.
  Revisão é diferente de golden: checkout nunca altera
  `PROJECT/golden/manifest.json`; somente `accept` publica o snapshot editorial.

## Layout

- **Rail esquerda:** identidade do projeto, status candidate/confirmed da
  timeline e histórico de revisões `vNNN`, incluindo o indicador de golden
  aceito separado.
- **Canvas central:** estado e estágio do job, `action_next`, progresso,
  diagnóstico e o `final.mp4` validado (ou estado vazio enquanto o run ainda
  não tem composição).
- **Rail direita:** cenas canônicas na ordem da timeline, com seleção explícita,
  disponibilidade de mídia e o `normalized.mp4` real da cena em foco.
- **Largura estreita:** as três regiões se empilham, mantendo todas as ações
  alcançáveis sem scroll horizontal e preservando a proporção do vídeo.

## Interaction

- O formulário inicial envia título, roteiro e `audio_asset_id`; a resposta
  mostra o `PROJECT` e a timeline candidata. “Confirmar timeline” é uma ação
  explícita antes de qualquer render.
- “Gerar prévia” enfileira um job FIFO. A UI consulta o estado durável de
  `inspect_project`/`run.json` e anuncia as transições finas sem inventar uma
  fase que o run não registrou.
- Selecionar uma cena muda o MP4 e o contexto da correção. “Regenerar esta cena”
  exige instrução e cria um novo run a partir do run `ready`, revalidando e
  reutilizando os irmãos por hash; o run-base permanece imutável.
- Após resultado terminal, o serviço publica a próxima revisão create-once.
  Selecionar `v1`/`v2`/`v3` faz checkout apenas do ponteiro. Editar depois do
  checkout cria o próximo número monotônico com parent na revisão selecionada,
  sem apagar revisões posteriores.
- “Aceitar” é a única ação que chama `accept_project` e promove o run pronto ao
  golden. Render, regeneração e checkout nunca promovem candidatos.

## Status language

`Na fila → Gerando código → Liberando modelo → Renderizando → Validando MP4 →
Observando frames → Corrigindo → Concluído`, com terminais de falha derivados
do pipeline canônico. Há um único `role="log"` com `aria-live="polite"` para
mensagens de progresso, correção e diagnóstico.

## Visual language

- Chrome quase preto, canvas off-white quente e laranja contido para a ação
  primária, mantendo a paleta local de referência.
- Verde, vermelho e azul ficam reservados a estados. Cards, seleção e controles
  usam bordas, espaçamento e texto além da cor.
- Sans do sistema, metadados compactos, texto confortável para script/mensagens,
  foco de teclado visível e mudanças de estado instantâneas, sem animação decorativa.

## Accessibility and responsive baseline

- Botões e labels semânticos, foco visível, contraste suficiente, seleção
  exposta por ARIA e estados não comunicados somente por cor.
- A atualização atrasada de polling não pode substituir uma revisão/job mais
  novo; cada resposta é protegida por um token de job/revisão vigente.
- Firefox/geckodriver cobre criação, confirmação, polling, correção seletiva,
  playback do `final.mp4`, restore e stale guard.

## Implemented boundary

- `WebService.inspect` acrescenta a projeção `ui` somente para runs completos:
  histórico monotônico, revisão selecionada e IDs opacos do final/cenas. A
  leitura não cria nem altera arquivos.
- IDs de mídia incluem identidade de projeto, revisão, run e cena em um digest;
  o resolver revalida diretórios, arquivo regular, symlinks e containment antes
  de devolver qualquer candidato ao servidor HTTP.
- `/`, `/index.html`, `/app.css` e `/app.js` formam uma allowlist package-owned.
  Rotas `/api/` têm precedência, não há fallback para o diretório corrente e os
  estáticos não emitem CORS.
- Uma revisão de falha continua registrada, mas não desloca uma revisão pronta
  já selecionada. O diagnóstico aparece no único live log e o run anterior
  permanece reproduzível.
- O cliente protege a resposta terminal antes de atualizar a revisão do job:
  checkout ou job mais novo incrementa o token e torna a resposta anterior
  inerte.

## Observable states

- **Empty:** projeto/revisão ausente, ações dependentes desabilitadas e as duas
  superfícies de mídia sem `src`.
- **Loading:** fila e estágio corrente aparecem no badge, barra e live log.
- **Content:** revisão e cena selecionadas via `aria-pressed`, com final e
  normalized reais servidos por `/api/assets/<id>`.
- **Failure:** diagnóstico preservado, revisão pronta anterior ainda selecionada
  e aceite disponível apenas sobre um run pronto.

Em largura estreita as rails e o canvas empilham; `video` mantém `16 / 9`, o
documento não cria overflow horizontal e `prefers-reduced-motion: reduce`
remove animações e transições.
