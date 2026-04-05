"""Compatibility shim — actual implementation lives in benchmark/hw_monitor.py.

All benchmark scripts import HWMonitor as ``from tests.hw_monitor import HWMonitor``.
The real module is at ``benchmark/hw_monitor.py`` (not a package, so a direct
importlib load is used to avoid requiring a benchmark/__init__.py).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "benchmark" / "hw_monitor.py"
import sys as _sys

_spec = importlib.util.spec_from_file_location("_benchmark_hw_monitor", _SRC)
assert _spec is not None and _spec.loader is not None, f"Cannot load {_SRC}"
_mod = importlib.util.module_from_spec(_spec)
# Register before exec so @dataclass can look up sys.modules[cls.__module__]
_sys.modules["_benchmark_hw_monitor"] = _mod
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

HWMonitor = _mod.HWMonitor  # noqa: N816
HWStats = _mod.HWStats       # noqa: N816

__all__ = ["HWMonitor", "HWStats"]
