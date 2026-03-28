"""System settings loader.

Loads tuning parameters from config/system_settings.yaml (repo defaults)
and merges with ~/.spendify/system_settings.yaml (user overrides) if present.

Usage:
    from config import system_settings
    threshold = system_settings["history"]["auto_threshold"]
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from support.logging import setup_logging

logger = setup_logging()

_DEFAULTS_PATH = Path(__file__).parent / "system_settings.yaml"
_USER_OVERRIDE_PATH = Path(os.environ.get(
    "SPENDIFY_SYSTEM_SETTINGS",
    Path.home() / ".spendify" / "system_settings.yaml",
))


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base (override wins)."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def _load() -> dict[str, Any]:
    """Load system settings with optional user overrides."""
    # Load defaults
    with open(_DEFAULTS_PATH, encoding="utf-8") as f:
        settings = yaml.safe_load(f) or {}

    # Merge user overrides if present
    if _USER_OVERRIDE_PATH.exists():
        try:
            with open(_USER_OVERRIDE_PATH, encoding="utf-8") as f:
                overrides = yaml.safe_load(f) or {}
            settings = _deep_merge(settings, overrides)
            logger.info(f"system_settings: merged overrides from {_USER_OVERRIDE_PATH}")
        except Exception as exc:
            logger.warning(f"system_settings: failed to load {_USER_OVERRIDE_PATH}: {exc}")

    return settings


# Module-level singleton — loaded once at import time
system_settings: dict[str, Any] = _load()
