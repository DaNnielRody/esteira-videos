# v16 — QA final

Base exclusiva: `final15.mp4` corrigido. Duração 12:18,9; áudio AAC copiado sem recodificação.

## Implementado

- **04:08–04:23 — trabalho manual:** mão com estados aberto, pressão e soltura; documento reage e recebe rótulo/marca. Quatro ciclos aceleram enquanto a fila cresce. Documentos têm variações de posição, rotação e tamanho; processados recebem contorno verde e fundo distinto.
- **05:20–05:35 — frase / modelo / Depois:** removida a miniatura textual sobreposta; propriedade dos grupos de texto limpa antes da redução. Entrada percorre o caminho, processamento precede a escrita da continuação.
- **07:27–07:35 — primeiro close:** perfil com nós circulares, camadas reconhecíveis e câmera acompanhando processamento. Resultado separado e legível.
- **07:48–07:55 — segundo enquadramento:** pilha frontal deslocada, sem repetir o perfil/close; POSITIVO ocupa espaço compatível com seu papel de resultado.
- **08:18–08:28 — Texto / Parâmetros / GPU:** contorno superior separado do card Texto. Eliminada cópia transitória dos três rótulos; estrutura emerge da conexão existente.
- **10:03–10:15 — Foundation:** corrigida deformação equivalente dos nós, margem inferior da estrutura e margem superior do título. Conceito e restante da cena preservados.
- **11:22–11:35 — topo da escada:** criança no último degrau, olhar para trás/baixo, recuo que revela sete degraus, VOCÊ ESTÁ AQUI com marcador. Hold de 3,4 s; olhar à frente e reenquadramento entregam a pergunta sem vazio.

## Preservado

- Abertura/Harness, criança/dicionário e correções anteriores de UNK/palavra inteira.
- Tokenização, ID 742/lookup e embedding.
- Corpus 04:36–04:44, probabilidades/próximo token até 05:20, Attention e GPT scaling.
- Estrutura e demais tarefas de MESMO MODELO, persona/instruction e payoff de capacidades.
- RLHF, NOVEMBRO 2022, API e CTA.
- Escada antes do topo e depois da entrega da pergunta, incluindo a suavização anterior dos títulos.
- Seis takes reaproveitados byte a byte. Nos cinco takes renderizados, todos os frames fora das janelas de patch foram comparados com a v15 em luminância reduzida, sem diferença material além da compressão.

## QA

- Medições dos nós em 07:30 e 07:51 registradas em `node-pixel-measurements.json`; círculos inteiros dentro de 1,00 ± 0,10.
- A medição por cor exclui componentes cortados pela borda nos closes deliberados. Oclusão parcial pelo pulso pode fragmentar uma máscara, sem representar deformação geométrica.
- Frames intermediários das regiões editadas verificados em sequências de 10 fps. Demais aparições auditadas por fonte e amostragem visual.
- Ocorrências do detector documentadas individualmente em `freeze-review.md`. Holds intencionais preservados; nenhuma deriva global adicionada.
- Vídeo completo decodificado, 22.167 frames a 30 fps; payload do áudio idêntico à v15.
- Limite da validação: não houve reprodução audiovisual integral em velocidade normal neste ambiente.

## Deliberadamente preservado

- Crops de crescimento em GPT scaling e cortes laterais de closes intencionais.
- Pausas de leitura, títulos e payoffs; não foram tratados como falhas só por dispararem o detector.
- Nenhuma nova estética, metáfora ou rodada de redesign.


# QA pontual — mesmo final16

Base: final16 anterior, preservado em `artifacts/llm-fundation-final16-qa/final16-before.mp4`.

- **04:06–04:15:** câmera aproxima mão/documento antes de pegar, pressionar, marcar e comparar palavra/rótulo. Depois abre para a fila e para os ciclos acelerados. Restante do bloco preservado.
- **07:42–07:49 e 07:55–08:01:** persona/contexto com painel maior, fundo opaco e borda reforçada, ligado ao conteúdo. Depois da chegada do input, a conexão passa ao modelo; saída continua posterior ao processamento.
- **08:07–08:12:** capacidades convergem para a pilha; suas mesmas camadas abrem em profundidade e se reorganizam para entregar modelo-base. Sem outro asset e sem longo estabelecimento imóvel.
- **11:31–11:33:** VOCÊ ESTÁ AQUI sai antes da escrita de Como isso é possível?. Escada, criança, recuo e hold aprovados preservados.
- **11:35–11:48:** callback compacto cachorro → vetor → modelo → próximo token, seguido do retorno ao ciclo e às capacidades já existentes. Sem nova explicação.

Áudio copiado sem recodificação. Duração 12:18,9. Oito takes reutilizados byte a byte; comparação de todos os frames fora das janelas editadas nos outros três takes.

Validação visual por sequências de frames; não houve reprodução audiovisual integral em velocidade normal neste ambiente.
