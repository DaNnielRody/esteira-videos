# Narração com ElevenLabs

`video_pipeline.narration.NarrationEnhancer` recebe áudio, `voice_id` e,
opcionalmente, a `Timeline` e os `ScenePlan` canônicos. Publica um candidato
separado com `narration.wav`, trechos brutos da API e `report.json`. O futuro
fluxo texto → áudio pode chamar esse serviço antes de `initialize_project`.
Esta entrega não adiciona TTS ou rota HTTP à UI.

`video_pipeline.elevenlabs.ElevenLabsVoiceChanger` usa stdlib e HTTPS oficial;
o serviço recebe um `VoiceConversionProvider` injetável. Não há dependência nova.

## Configuração e comandos

Configure `ELEVENLABS_API_KEY` ou `ELEVEN_LABS_API_KEY`, já usado neste projeto.
Variáveis de ambiente têm precedência. A CLI lê `.env` apenas com
`--env-file .env`, sem executar seu conteúdo. A chave não entra nos relatórios.

Planeje offline, sem chave e sem consumir créditos:

```sh
rtk proxy .venv/bin/video-pipeline narration plan \
  projects/2026_llm-fundation/audio/narration-clean.wav \
  --project projects/2026_llm-fundation/project.json
```

Clone explicitamente a voz usando 90 segundos do MP3 fornecido:

```sh
rtk proxy .venv/bin/video-pipeline narration clone \
  /home/dan/Downloads/video-narracao-1.mp3 \
  --name 'Dan - Narração LLM Foundation' --start 20 --duration 90 \
  --output artifacts/elevenlabs/voice.json --env-file .env
```

Clonagem exige uma conta com Instant Voice Cloning habilitado. O erro
`can_not_use_instant_voice_cloning` não cria um clone fictício. Se a API exigir
verificação, conclua-a na ElevenLabs e atualize os metadados antes de converter.
Clonagem nunca acontece como efeito colateral de abrir a UI ou renderizar.

Converta a narração canônica quando o clone estiver disponível:

```sh
rtk proxy .venv/bin/video-pipeline narration enhance \
  projects/2026_llm-fundation/audio/narration-clean.wav \
  artifacts/elevenlabs/enthusiastic-v1 \
  --project projects/2026_llm-fundation/project.json \
  --voice-file artifacts/elevenlabs/voice.json --env-file .env
```

O destino pode vir de `--voice-id`, `--voice-file` ou `ELEVENLABS_VOICE_ID`.
Cada execução exige um diretório novo. WAV e MP3 são aceitos. FFmpeg decodifica
para mono a 48 kHz; somente os trechos de fala são enviados, em WAV PCM16.
A saída também é PCM16, não uma cópia bit a bit do original em PCM24.

Para áudio novo sem projeto, omita `--project`. `--timeline` aceita uma
timeline isolada, sem carregar beats; os dois flags são exclusivos.
`--stability`, `--similarity` e `--style` permitem comparar interpretações.
Comece com um trecho curto antes de converter a narração inteira.

## Timing e expressividade

Janelas de energia de 10 ms localizam fala; pausas de aproximadamente 350 ms
separam requisições, com margens de 60 ms. A detecção é heurística: pode incluir
respirações ou falas baixas. Pausas menores ficam dentro do trecho convertido.
Intervalos menores que 300 ms ficam preservados no áudio original, sem requisição
paga; não são descartados nem classificados automaticamente como respiração.
Fala contínua acima de nove minutos é rejeitada, sem cortar frases arbitrariamente.

A fala convertida volta ao mesmo intervalo de amostras. Diferenças pequenas
de duração são ajustadas com `atempo`, preservando pitch. Drift maior que 5%,
resposta vazia, não finita ou silenciosa e normalização que exigiria aparar
mais de 10 ms bloqueiam a publicação. Um déficit de até 60 ms do normalizador
é completado com silêncio, sem aparar conteúdo. Padding e trim residuais são
registrados no relatório. O áudio original, projeto, timeline,
runs anteriores e vídeo aprovado permanecem preservados.

Com `--project`, cada trecho registra cenas e beats temporizados sobrepostos.
Beats `reveal`, `transform`, `connect`, `emphasize` e `payoff` recebem `style`
acrescido de 0,15 e `stability` reduzida em 0,05, limitados a [0,1]. Os padrões
são 0,5 e 0,3, respectivamente; similaridade é 0,85. É uma hipótese para ouvir,
não uma medição de entusiasmo. Sacadas sem beats temporizados não são inferidas
do texto ou do objetivo. Cortes visuais não cortam frases faladas.

Voice Changer preserva a entrega emocional da entrada. Aumentar `style` pode
acentuar características da voz, mas não garante tornar uma fala neutra animada.
Duração e pausas preservadas não comprovam alinhamento de palavras, conteúdo,
identidade vocal ou aprovação artística. O relatório exige revisão com narração
e transições. Na narração atual, o plano identifica 222 intervalos, dos quais
219 são convertidos, e cerca de 616,38 segundos enviados; confira o volume
offline antes de consumir créditos.

Não há retry automático de operações pagas. Falhas não publicam candidatos e
descartam temporários; uma nova execução começa do zero. Trechos brutos ficam
preservados no diretório de uma execução bem-sucedida.

## Direção e validação

Nenhuma unidade visual ou `ScenePlan.direction` é modificada. Contratos de token,
proporção e processamento e decisões PERSIST / TRANSFORM / EXIT não se aplicam
ao serviço de áudio. DV15 orienta a revisão audiovisual. O controle positivo
`intentional-hold` reforça preservar pausas, sem exigir movimento. O master
e seus hashes continuam sendo a referência aprovada.

Testes usam fakes de HTTP/processos e não consomem créditos:

```sh
rtk proxy .venv/bin/pytest tests/test_narration_cli.py tests/test_direction_contracts.py -q
rtk proxy .venv/bin/python scripts/verify_direction_reference.py
```

Referências oficiais:
[Voice Changer](https://elevenlabs.io/docs/api-reference/speech-to-speech/convert),
[Instant Voice Cloning](https://elevenlabs.io/docs/api-reference/voices/ivc/create),
[controles de voz](https://elevenlabs.io/docs/api-reference/voices/settings/get).
