# Contrato do sandbox

O sandbox executa os gates determinísticos do projeto sem modelo, rede ou
download durante os testes. A calibração usa renders locais pequenos de
Manim/FFmpeg/LaTeX mesmo fora da marca `integration`; essas ferramentas devem
estar instaladas antes da execução. No Ubuntu, LaTeX requer
`texlive-latex-extra`, `texlive-fonts-recommended` e `dvisvgm`, além das
dependências Cairo/Pango e FFmpeg do renderer.

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

Os testes de orquestração substituem provider, renderer e sensores por fakes;
os testes de calibração conferem fixtures locais com o renderer real. Testes
marcados como integração não fazem parte do gate padrão.
