"""Test bootstrap for Jade Helper.

Puts the repo root on sys.path so ``import helper...`` works from a bare
``pytest`` run, and installs a minimal psutil stub when the real package
isn't available (the worker/registration modules only use cpu_percent
and virtual_memory).
"""

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import psutil  # noqa: F401
except ImportError:  # pragma: no cover - only on bare dev machines
    _stub = types.ModuleType("psutil")

    class _VM:
        percent = 1.0
        total = 8 * (1024 ** 3)

    _stub.cpu_percent = lambda interval=None: 1.0
    _stub.virtual_memory = lambda: _VM()
    sys.modules["psutil"] = _stub
