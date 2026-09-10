# v17 — INFELIZMENTE retake (fases 1–6)

Base: `final16.mp4`. Somente os takes `OLimiteDoDicionarioScene` (final) e `TokensERepresentacoesScene` foram re-renderizados; os demais oito takes são byte a byte idênticos. Áudio copiado sem recodificação; 22.167 frames; 12:18,9.

- Alinhamento por palavra (Vosk pt-BR) da narração nos dois takes.
- **Dicionário (fim):** PERGUNTA reposicionada — "PRECISA DECORAR" escreve-se em 52,6 s (fala "precisa decorar" em 54,2 s), "PALAVRA INTEIRA" em 54,2–55,5 s (fala 55,1–56,4 s); INFELIZMENTE entra grande somente após a pergunta completa (56,4 s), com contorno em tensão rosa.
- **Fase 1 (0,9 s):** palavra entra como unidade única, contorno sutil de agrupamento e push-in discreto da câmera (14,22 → 13,4).
- **Fase 2 (4,9 s "não precisa das palavras completas"):** inspeção — linha de varredura percorre a palavra duas vezes; tracking abre levemente (11,1 → 11,5).
- **Fase 2b (9,6 s "ela já é bem inteligente"):** agrupamentos candidatos (IN/FELIZ/MENTE em azul apagado) surgem e se dissolvem sem compromisso.
- **Fase 3 (10,9–13,2 s "pedaços pequenininhos que possam ser reutilizados"):** descoberta progressiva — IN (10,96), FELIZ (11,95), MENTE (13,22) ganham destaque um a um; o resto da palavra permanece intacto.
- **Fase 4 (14,6 s "técnicas de tokenização"):** payoff — cortes amarelos e as próprias letras se separam (sem FadeOut/FadeIn; glifos preservados).
- **Fase 5 (18,0–23,7 s "um token é… uma das unidades…"):** delimitadores se fecham ao redor dos pedaços, reorganizam-se como unidades e só então o rótulo "token" surge (21,6 s); verde confirma as unidades processadas (22,3 s).
- **Fase 6 (25,3 s "quando você faz aquele prompt bolado"):** tokens atuais encolhem e entregam o exemplo seguinte; "Crie o GTA 6" é escrito pela narração (28,9–30,4 s) e sofre a MESMA operação (30,9 s): Crie | o | GTA | 6.
- **Confirmação (35,1–48,2 s):** palavra comum mantém um único token; gíria GTA/6 ganha mais de um, com a mesma operação aprendida; "Isso resolve" assenta a comparação (44,6 s).
- **Cachorro/ID 742/vetores/plano:** código idêntico ao aprovado; diferença limite fora da janela ≤ 1,10 de luminância média (ruído de re-codificação, NCC 0,9945).

## Preservado

- Todos os takes fora dos dois retomados, byte a byte (verificação de todos os frames fora das janelas).
- Payload de áudio idêntico; hash SHA-256 do AAC confirmado.
- Dead frame de INFELIZMENTE estático (hold de 7,4 s) eliminado; holds de leitura intencionais mantidos e re-cronometrados com a fala.
- Nenhum elemento decorativo adicionado (sem wobble, partículas, floating ou zoom contínuo).