"""Core comum de navegação usado pela app desktop.

A implementação real está em `app movel/navigation_core.py` porque essa pasta é
o diretório de build do APK. Este wrapper carrega exatamente esse módulo e muda
apenas os caminhos dos dados para a raiz do repositório.
"""

from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
SHARED_CORE_PATH = ROOT_DIR / "app movel" / "navigation_core.py"

_spec = spec_from_file_location("_utad_shared_navigation_core", SHARED_CORE_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Não foi possível carregar o core comum em {SHARED_CORE_PATH}")

_shared_core = module_from_spec(_spec)
sys.modules[_spec.name] = _shared_core
_spec.loader.exec_module(_shared_core)
_shared_core.configure_paths(ROOT_DIR)

for _name in dir(_shared_core):
    if not _name.startswith("_"):
        globals()[_name] = getattr(_shared_core, _name)

__all__ = [_name for _name in globals() if not _name.startswith("_")]
