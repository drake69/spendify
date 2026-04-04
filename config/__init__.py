"""System settings and model registry loader.

Loads tuning parameters from config/system_settings.yaml (repo defaults)
and merges with ~/.spendifai/system_settings.yaml (user overrides) if present.

Also loads the model registry from config/models_registry.yaml.

Usage:
    from config import system_settings, get_recommended_model
    threshold = system_settings["history"]["auto_threshold"]
    model = get_recommended_model(ram_gb=16)
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from support.logging import setup_logging

logger = setup_logging()

_DEFAULTS_PATH = Path(__file__).parent / "system_settings.yaml"
_USER_OVERRIDE_PATH = Path(os.environ.get(
    "SPENDIFAI_SYSTEM_SETTINGS",
    Path.home() / ".spendifai" / "system_settings.yaml",
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


# ── Model Registry ──────────────────────────────────────────────────────────

_REGISTRY_PATH = Path(__file__).parent / "models_registry.yaml"


@dataclass
class ModelInfo:
    """A single model entry from the registry."""
    id: str
    name: str
    params: str
    quant: str
    filename: str
    repo: str
    size_mb: int
    ram_min_gb: int
    tier: str
    languages: list[str]


def _load_registry() -> dict[str, Any]:
    """Load model registry YAML."""
    with open(_REGISTRY_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_all_models() -> list[ModelInfo]:
    """Return all models from the registry."""
    reg = _load_registry()
    return [
        ModelInfo(**{k: v for k, v in m.items()})
        for m in reg.get("models", [])
    ]


def get_recommended_model(ram_gb: int) -> ModelInfo | None:
    """Pick the best model for the given RAM amount.

    Uses the ``default_tier_map`` in the registry: picks the entry
    whose RAM key is the largest that fits in ``ram_gb``.
    Returns None if no model fits.
    """
    reg = _load_registry()
    tier_map = reg.get("default_tier_map", {})
    models_by_id = {m["id"]: m for m in reg.get("models", [])}

    # Find best tier: largest key <= ram_gb
    best_id = None
    for threshold in sorted(int(k) for k in tier_map):
        if threshold <= ram_gb:
            best_id = tier_map[threshold]

    if best_id and best_id in models_by_id:
        m = models_by_id[best_id]
        return ModelInfo(**{k: v for k, v in m.items()})

    return None
