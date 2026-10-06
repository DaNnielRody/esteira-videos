# Lifecycle — hotfix final consolidado

| Beat | PERSIST | TRANSFORM | EXIT |
|---|---|---|---|
| VAMOS LÁ → livro | — | eixo tipográfico → lombada → livro aberto | demais letras |
| Emenda da analogia | livro aberto + criança, mesma posição e câmera | — | eixo temporário |
| CASA | livro + criança | letras vindas da cabeça → CASA → entrada correspondente | palavra móvel após incorporação; updater dos olhos |
| Outros exemplos | livro + criança; entradas encontradas | próximo estímulo → linha correspondente | estímulo móvel e updater a cada busca |
| Busca sem correspondência | livro + criança | palavra → UNK | scanner, dúvida, indicação de falha e label ao fim do beat |
| Pergunta → INFELIZMENTE | glifos de INFELIZMENTE | pergunta → palavra protagonista | criança, livro, lead, interrogação; contorno sai ANTES da palavra |
| Inspeção | mesmos glifos | destaque tipográfico sequencial | nenhum container existe |
| Fragmentação → tokens | mesmos glifos | partes separadas → TokenBox(texto, contorno) | — |
| Tokens → prompt | linguagem visual | grupos se deslocam/escalam como unidade | três TokenBoxes + label token enquanto o prompt entra |
| GTA 6 | quatro grupos de tokens | deslocamento de grupos; cor do próprio contorno | quatro grupos completos, sem duplicatas |
| CACHORRO + computador | somente palavra e receptor | palavra → representação, conforme take anterior | zero objetos dos exemplos anteriores |
| Capacidades finais | modelo funcional no palco | tarefa → somar(a,b) → 2 + 2 → 2 + 2 → 4 | resumo breve, pergunta e pulsos transitórios |
| Emenda da conclusão | expressão final + modelo na mesma geometria | — | saída original na conclusão preservada |

Checagens:
- Repro executa a cena real em modo sem raster: antes, três contornos órfãos; depois, zero.
- Checagem geométrica percorre frames animados a 30 fps: padding positivo e elementos dentro do frame nos intervalos auditados.
- Contornos são medidos a partir dos glifos finais; nenhum tamanho fixo por palavra.
- Inspeção visual densa a 10 fps nos movimentos alterados; screenshots do MP4 montado nas emendas e em CACHORRO + computador.
- Emendas das demais cenas inspecionadas sem redesenho.
- Código após a entrada de CACHORRO permanece igual; a retirada dos órfãos corrige tecnicamente seu fundo.
