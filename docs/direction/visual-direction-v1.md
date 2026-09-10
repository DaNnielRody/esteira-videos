# Direção visual v1 — referência LLM Foundation

Status: primeira camada implementada. Referência aprovada pelo usuário:
`videos/llm_fundation/master.mp4`, identificada em `reference-v1.json`.
A aprovação deste vídeo não implica aprovação automática de outro render.

## Princípios e critérios de revisão

| ID | Princípio | Evidência exigida na revisão |
|---|---|---|
| DV01 | Animar relações e verbos | A ação altera o conceito; mover um substantivo não basta. |
| DV02 | Mesmo objeto pode ter outro enquadramento | Identidade reconhecível e composição adequada ao beat; comparar sequências. |
| DV03 | Raciocínio contínuo compartilha ambiente | Beats relacionados transformam estado/foco dentro do mesmo mundo. |
| DV04 | Cena anterior entrega a próxima | Objeto, estado ou pergunta estabelece a ligação; corte pode ser intencional. |
| DV05 | Assets causam consequências | Ação do personagem/livro/mão muda o sistema e recebe resposta. |
| DV06 | Exemplo recorrente acumula significado | Callback recupera aprendizado; não exigir cachorro em outros assuntos. |
| DV07 | Densidade comunica quantidade | Documentos/pontos/estruturas numerosos têm função no conceito. |
| DV08 | Limpar o que perdeu função | Identificar o destino de cada unidade visual; não confundir limpeza com vazio. |
| DV09 | Câmera revela algo | Movimento responde a escala, mecanismo, foco ou continuidade espacial. |
| DV10 | Progressão semântica mantém a cena viva | Narrativa muda interpretação/estado; freeze físico não prova dead frame. |
| DV11 | Pergunta gera visualmente sua resposta | Inspeção/ação seguinte resolve o problema apresentado. |
| DV12 | A vira B por transformação | Continuidade visível da transformação; seta só quando expressa a relação. |
| DV13 | Unidade visual compartilha lifecycle | PERSIST, TRANSFORM ou EXIT explícito; sem container órfão ou ghost text. |
| DV14 | Componentes robustos | Bbox + padding, proporções, legibilidade e enquadramento intencional. |
| DV15 | Verificar o caminho | Revisar estados intermediários, transições e sequência com narração. |

## Contratos executáveis nesta versão

`ScenePlan.direction` aceita `visual.direction/1`. O gate existente
`evaluate_visual_quality` executa estes contratos e retorna falhas `DIRECTION_*`.
O JSON do plano também chega ao prompt de geração/correção, com instruções de
instrumentação. Planos anteriores sem `direction` mantêm o comportamento anterior.

- `tokens`: `text_id`, `container_id`, `start_seconds`, `end_seconds`, `padding`.
  Ambos visíveis em `[start,end)`; bbox do texto contida com padding em cada eixo
  normalizado. Ambos ausentes a partir de `end`, inclusive nos checkpoints seguintes.
  IDs são exclusivos daquela vida do token. Este contrato v1 modela **EXIT**;
  PERSIST e TRANSFORM gerais ainda são critérios de planejamento/revisão.
- `aspects`: `object_id`, início/fim, `expected_ratio`, `relative_tolerance`.
  Compara largura/altura do bbox, corrigindo a normalização pelos tamanhos de câmera.
  Não impor proporção circular a projeções em perspectiva, objetos rotacionados ou
  morphs: definir a janela estável relevante e a razão esperada. Não mede perspectiva
  3D nem pixels; é uma garantia sobre geometria lógica axis-aligned registrada.
- `processing`: `model_id`, `output_id`, `animation`, início/fim,
  `output_by_seconds`. Requer AnimationFact com nome, objeto e intervalo iguais;
  modelo visível durante processamento e novo output observado após conclusão.
  Output presente antes da conclusão reprova. Usar ID novo em cada resposta.
  Nesta versão, usar um `play` dedicado ao processamento, sem AnimationGroup ou
  várias animações simultâneas: o runtime agrega nomes/IDs nessas situações.

As janelas precisam pertencer à duração da cena, os IDs precisam existir no plano,
e os limites/tolerâncias devem ser finitos. Contratos inválidos são rejeitados.
`examples/direction-contract/plan.json` é um plano válido para começar.

## Evidência e limites

Exigir checkpoints nos limites de cada contrato, no início da cena e no prazo de
output. Registrar também estados intermediários relevantes. Checkpoints no mesmo
instante representam mutações sem duração; vale o último estado, com resolução de
microssegundo para absorver erro de ponto flutuante do Manim.

`DIRECTION_EVIDENCE_MISSING` bloqueia: a ausência de dados não aprova um contrato.
Isso não equivale a cobertura de todos os frames. Os testes exercitam fatos
controlados e geometria/eventos do Manim real, em dry-run; não são uma revisão
pixel a pixel de um vídeo. Um AnimationFact prova a animação registrada, não que
ela explica semanticamente o processamento. Essa avaliação continua na revisão.

Nenhuma regra automática limita fades, exige câmera em deriva, define zero
freezes ou penaliza densidade de forma universal. Não comparar pixels do vídeo
inteiro com novos temas. Comparação visual exata cabe em componentes/trechos
reproduzidos no mesmo ambiente, com tolerâncias calibradas.

## Uso nos próximos vídeos

1. Antes do código, preencher intenção, ambiente, antes/depois, ação, ligação com o
   próximo beat e lifecycle. Usar `review-template.md` para registrar o raciocínio.
2. Adicionar `direction` aos planos em que as três garantias se aplicam. Justificar
   casos não aplicáveis na revisão. Esta versão não infere contratos automaticamente
   nem obriga cobertura de todos os planos; esse controle é de autoria/revisão.
3. Registrar objetos reais via `register_visual`; texto/container separados para
   medição, reunidos em VGroup para manipulação. Checkpoints via `self.checkpoint`.
4. Executar o gate normal e corrigir falhas. Rodar as regressões ao mudar helpers,
   runtime ou validadores.
5. Revisar vídeo com narração, sequências intermediárias e fronteiras entre cenas.
   Classificar cada achado: direção, layout, lifecycle ou timing; incluir intervalo,
   observação, correção esperada e evidência da nova verificação.
6. Aprovar o hash do render revisado. Mudanças invalidam a evidência dos trechos
   afetados e das transições vizinhas. Aprovação não pode ser copiada por nome.

Critério de parada: nenhuma falha objetiva ou problema de direção confirmado
pendente; evidência suficiente; sugestões restantes são preferências, registradas
sem nova rodada obrigatória. Não existe nota agregada que compense um defeito grave.

## Comandos

```sh
rtk proxy .venv/bin/pytest tests/test_direction_contracts.py -q
rtk proxy .venv/bin/python scripts/verify_direction_reference.py
```

A referência inclui hashes de vídeo, fontes, assets, fontes tipográficas e
relatórios disponíveis. O ambiente atual é inventariado como **ambiente observado**,
não como prova retroativa do ambiente do render aprovado. O verificador detecta
alterações/ausências; não reconstrói arquivos e não reaprova novas versões.

O pacote é publicado neste repositório. Instalar Git LFS e executar `git lfs pull`
recupera o master, as fontes tipográficas, imagens e assets de referência maiores.
Fontes de cenas e relatórios ficam no Git comum. O CI baixa o LFS e executa a
verificação de integridade; um checkout só com ponteiros não satisfaz a verificação.
Versões intermediárias e projetos de execução permanecem locais, preservados,
aguardando a catalogação dos pares defeito/correção.

## Evidência TDD desta entrega

Ciclos observados: 6 falhas de contrato token ausente → 6 passaram; 3 falhas de
contrato de proporção ausente → 9 passaram; 5 falhas de contrato de processamento
ausente → 14 passaram. A integração com Manim expôs os limites de tempo e estado
inicial; após correção, 18 testes passaram. Casos negativos incluem bbox/padding
quebrados, container órfão, deformação intermediária, output antecipado e fatos
faltantes ou de outro objeto. São variantes de regressão defeituosas, não uma
alegação de execução de ferramenta de mutation testing.

Verificação final: 31 testes novos passaram (contratos, integração Manim,
validação de entradas e integridade com arquivo alterado/ausente). A seleção de
regressões de câmera, continuidade, planos, críticos, runtime, pipeline, prompts,
goldens, aprovação e renderização passou com 246 testes, incluindo os 28 testes de
contratos. Ruff passou nos arquivos envolvidos e mypy passou nos três módulos
`direction.py`, `direction_contracts.py` e `scene_plan.py`. O mypy geral apontou
quatro erros nos trechos preexistentes de `pipeline.py` (linhas 976, 979 e 998),
que não foram alterados nesta entrega. A integridade dos 211 arquivos foi verificada.

Na preparação do PR completo, esses erros de `pipeline.py` foram corrigidos com
validação dos dados dos checkpoints e uma regressão pelo pipeline público para
amostragem de texto. Ruff e mypy geral passaram. A suíte sem integração registrou
658 testes passando e 9 skips previstos; o teste adicional de amostragem passou
com os 14 testes existentes do gate semântico. As integrações passaram com 35
testes. O workflow `Quality and approved reference` verifica o estado publicado.
