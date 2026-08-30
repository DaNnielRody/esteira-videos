# Contrato do sandbox

O sandbox executa os gates determinísticos do projeto sem modelo, rede,
download, integração ou mídia real:

```bash
.venv/bin/python -m pytest -q -m "not integration"
.venv/bin/ruff check .
.venv/bin/mypy \
  --follow-imports=silent \
  src/video_pipeline/video.py \
  src/video_pipeline/pipeline.py \
  src/video_pipeline/prompts.py \
  src/video_pipeline/expectations.py \
  src/video_pipeline/spec.py \
  src/video_pipeline/theme.py \
  src/video_pipeline/scene_plan.py \
  src/video_pipeline/quality.py \
  src/video_pipeline/capabilities.py \
  src/video_pipeline/project.py \
  src/video_pipeline/timeline.py \
  src/video_pipeline/temporal.py \
  src/video_pipeline/validation.py
```

O comando requer o `.venv` local criado por `uv sync`. A lista de mypy contém
13 módulos com contratos tipados confirmados: orquestração (`video`,
`pipeline`), contratos visuais (`prompts`, `expectations`, `spec`, `theme`,
`scene_plan`, `quality`, `capabilities`) e contratos de projeto/timeline
(`project`, `timeline`, `temporal`, `validation`).

## Exclusões honestas

`golden.py` e `runtime.py`, além dos adaptadores de evidência de pixels e AST,
ficam fora do mypy estrito porque atravessam JSON cru, Manim, NumPy/OpenCV e
AST dinâmicos. `provider.py`, `rendering.py` e `cli.py` também ficam fora por
suas fronteiras dinâmicas de provider, subprocesso e argparse. Esses módulos
continuam sob Ruff e testes comportamentais determinísticos; a exclusão não é
uma alegação de tipagem estrita não verificada.

Os testes substituem provider, Manim, FFmpeg, ffprobe e sensores por fakes.
Testes de integração ficam marcados e não fazem parte do gate padrão.
