"""Runtime prompt integrity verification (S-01).

Called at app startup to detect unauthorized prompt modifications.
If prompt_hashes.json is missing, the check is silently skipped (dev mode).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from support.logging import setup_logging

logger = setup_logging()

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
_HASHES_FILE = _PROMPTS_DIR / "prompt_hashes.json"


def verify_prompt_integrity() -> list[str]:
    """Verify all prompt files against stored SHA256 hashes.

    Returns a list of error messages (empty if all OK).
    Logs critical warnings for any mismatches.
    """
    if not _HASHES_FILE.exists():
        logger.debug("prompt_guard: prompt_hashes.json not found — skipping integrity check")
        return []

    try:
        with open(_HASHES_FILE, encoding="utf-8") as f:
            stored = json.load(f)
    except Exception as exc:
        logger.warning(f"prompt_guard: failed to load prompt_hashes.json: {exc}")
        return []

    errors: list[str] = []

    # Verify known prompts
    for name, expected_hash in stored.items():
        filepath = _PROMPTS_DIR / name
        if not filepath.exists():
            msg = f"Prompt file mancante: {name}"
            errors.append(msg)
            logger.critical(f"prompt_guard: {msg}")
            continue

        actual_hash = hashlib.sha256(filepath.read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            msg = (
                f"Prompt modificato: {name} — "
                f"hash atteso {expected_hash[:16]}..., "
                f"trovato {actual_hash[:16]}..."
            )
            errors.append(msg)
            logger.critical(f"prompt_guard: {msg}")

    # Check for unauthorized new prompts
    for filepath in sorted(_PROMPTS_DIR.glob("*.json")):
        if filepath.name == "prompt_hashes.json":
            continue
        if filepath.name not in stored:
            msg = f"Prompt non autorizzato: {filepath.name} — non presente in prompt_hashes.json"
            errors.append(msg)
            logger.critical(f"prompt_guard: {msg}")

    if errors:
        logger.critical(
            f"prompt_guard: {len(errors)} problemi rilevati! "
            "Possibile manomissione dei prompt LLM. "
            "Eseguire: python tools/compute_prompt_hashes.py"
        )
    else:
        logger.info(f"prompt_guard: {len(stored)} prompt files verified OK")

    return errors
