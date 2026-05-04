"""Minimal i18n — JSON-based translation with fallback to Italian."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_I18N_DIR = Path(__file__).parent
_CACHE: dict[str, dict[str, str]] = {}
_FALLBACK_LANG = "it"
_active_lang: str = "it"


def _load(lang: str) -> dict[str, str]:
    """Load a flat key→string dict from {lang}.json. Cached."""
    if lang not in _CACHE:
        path = _I18N_DIR / f"{lang}.json"
        if path.exists():
            with open(path, encoding="utf-8") as f:
                _CACHE[lang] = json.load(f)
        else:
            _CACHE[lang] = {}
    return _CACHE[lang]


def set_language(lang: str) -> None:
    """Set the active UI language."""
    global _active_lang
    _active_lang = lang
    # Pre-load to validate
    _load(lang)


def get_language() -> str:
    """Return current active language code."""
    return _active_lang


def t(key: str, **kwargs: Any) -> str:
    """Translate a key. Supports {placeholder} substitution.

    Lookup order: active language → Italian fallback → key itself.
    """
    strings = _load(_active_lang)
    text = strings.get(key)
    if text is None and _active_lang != _FALLBACK_LANG:
        text = _load(_FALLBACK_LANG).get(key)
    if text is None:
        return key  # last resort: show the key
    if kwargs:
        text = text.format(**kwargs)
    return text


def available_languages() -> list[tuple[str, str]]:
    """Return list of (code, label) for available languages."""
    result = []
    for path in sorted(_I18N_DIR.glob("*.json")):
        lang = path.stem
        data = _load(lang)
        label = data.get("_language_name", lang.upper())
        result.append((lang, label))
    return result
