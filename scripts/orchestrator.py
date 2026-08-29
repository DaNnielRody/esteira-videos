#!/usr/bin/env python3
"""Project entrypoint for the canonical Darkagent Ralph control plane."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_CANONICAL = Path("/home/dan/workflow/darkagent/scripts/orchestrator.py")
_SPEC = importlib.util.spec_from_file_location("darkagent_canonical_orchestrator", _CANONICAL)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load canonical orchestrator: {_CANONICAL}")
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

for _name in dir(_MODULE):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_MODULE, _name)

if __name__ == "__main__":
    raise SystemExit(_MODULE.main())

