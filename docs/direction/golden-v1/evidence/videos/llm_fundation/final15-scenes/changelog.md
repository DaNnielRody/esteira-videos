# v15 — patch final consolidado

Baseline: **final14-estrutural.mp4**, evolução da **final14.mp4** indicada. Ambas intactas.

### Implementado

- **00:58–01:38 — criança/dicionário:** palavras alinhadas à posição e inclinação das linhas reais do livro; marcador percorre essas mesmas linhas. Olhos acompanham a consulta; reconhecimento precede nod. Ornitorrinco e cringe percorrem a página, falham, provocam reação e só depois viram UNK. Removido o UNK residual na pergunta.
- **02:44–02:46 — CACHORRO/máquina:** a palavra alcança fisicamente o slot; máquina e entrada rejeitam em rosa, a palavra recua e continua para token/ID sem reset.
- **03:29–03:41 — embedding:** aproximação cachorro/gato enfatizada antes de parafuso distante; seis vizinhos precedem a revelação de centenas de pontos aparentes. Sem eixos e sem restos da tabela/vetor.
- **03:49–03:51 — representação:** o enquadramento revela o mecanismo matemático durante uma espera que antes não evoluía; câmera retorna antes da pergunta seguinte.
- **04:35–04:45 — corpus:** crescimento em três ondas, 312 documentos simplificados com profundidade, extrapolando quatro bordas. Absorção em duas ondas, com primeira onda prolongada dentro do timing existente. Massa limpa antes de próximo token.
- **07:21–08:08 — MESMO MODELO:** as mesmas cinco camadas ganham projeção em perfil, close real, corte lateral e contexto wide. Pulso verde atravessa camadas; resultado só começa a nascer depois. Nós voltam ao amarelo em repouso. Instrução/persona preservada.
- **08:04–08:08 — payoff:** seis resultados já demonstrados voltam legíveis e periféricos; MESMO MODELO ganha destaque aqui. Revelação e leitura ocupam cerca de 3,4s; limpeza ocorre antes de modelo-base.
- **10:01–10:08 — FUNDAÇÃO:** letras entregam as camadas; estrutura se abre em profundidade e ganha presença. Demais capacidades, RX580 e API conservam a coreografia aprovada.

### Dead frames

- **01:26–01:38 — corrigido:** eliminada a espera inicial entre categorias; a palavra rara já entra e cringe passa a consultar o livro. O detector antigo agrupava também pequenas animações neste intervalo.
- **03:47–03:53 — corrigido:** o mecanismo recebe foco durante a explicação; removida a longa espera central.
- **04:41–04:43 — corrigido:** primeira absorção do corpus agora continua dentro da antiga espera.
- **Payoff, leitura de resultados, perguntas, títulos, NOVEMBRO 2022 e pausas aprovadas — preservados como INTENTIONAL_HOLD.**
- Todas as ocorrências do comando solicitado estão listadas, com timestamps, duração, motivo e evidência de movimento local, em [freeze-review.md](freeze-review.md). O detector usa um limiar global e também sinaliza sequências com olhos, texto e pulsos em movimento; não foram adicionadas derivas para zerá-lo.

### Preservado

- Tipografia/tokenização, lookup/ID 742 e sequência numérica do vetor.
- Probabilidades/próximo token; Attention; pergunta de escala e GPT scaling, incluindo count-ups e referências GPT-1/GPT-2 no payoff de GPT-3.
- Persona/instruction, incluindo Aja como um programador e Explique com clareza; RLHF; NOVEMBRO 2022.
- Capacidade emergindo da estrutura, API/request/processamento/response, RX580 narrada, escada, CTA e fio condutor do cachorro.
- Harness: estrutura de controle com portas/rotas/ferramentas, diferente da rede neural. Verificado e mantido.
- **Áudio AAC idêntico à baseline, sem recodificação. 22.167 frames, 1080p/30 fps, 12:18,9.**

### Não implementado deliberadamente

- RX580 → GPU: a narração menciona RX 580 explicitamente; preservado conforme a condição do patch.
- Redesign de Harness, escala, API, RLHF e capacidade emergente: verificações já atendidas, KEEP.
- Deriva global e eliminação artificial de todos os alertas de freezedetect: não implementadas.
- Não se alteraram valores/lookup para reproduzir literalmente os números ilustrativos do prompt: representação aprovada preservada.
- O intervalo amplo 01:40–02:50 contém a colisão solicitada no item 2. Essa colisão é a exceção explícita; a tipografia/tokenização permanece. A remoção de UNK residual na pergunta é a correção de ciclo de vida solicitada.

### Validação e limite

Render completo decodificado; áudio conferido por hash do payload; ordem causal das sete tarefas e identidade das cinco camadas verificadas em runtime; fontes e intervalos protegidos comparados; percursos alterados inspecionados em sequências contíguas a cada 0,1s, além de análise de todos os frames.

**Não houve assistência perceptual com áudio em reprodução normal:** a ferramenta disponível não oferece esse modo de observação. O aceite do item 16 permanece externo. [Player de revisão em 1×](review.html) aponta para o MP4 completo e marca as regiões alteradas; não se declara esse aceite como concluído.

Rodada encerrada após esta entrega. Nenhuma v16 iniciada.

### Correção pontual posterior — mesmo final15

- 01:42–01:45: removido o segundo FadeOut de UNK, que o fazia reaparecer. PALAVRA INTEIRA e INFELIZMENTE agora trocam sem sobreposição; moldura mantém margem suficiente.
- 03:01–03:05: rótulo 742 separado do grupo da tabela antes de desaparecer, evitando duplicação e reaparecimento sobre ID 742.
- 11:06–11:07: os rótulos adicionais da escada entram em fade escalonado de 0,8s, mantendo a câmera e os cues seguintes.
- 12:05–12:07: removido o segundo FadeOut do grupo COMO/ISSO/?, que o fazia reaparecer sobre o vetor. CTA preservado.
- Sete takes mantidos byte a byte; diferenças fora das janelas verificadas. Áudio e 12:18,9 preservados.
