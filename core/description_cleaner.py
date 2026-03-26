"""Counterpart extraction from bank transaction descriptions (RF-02 pre-categorization).

Handles descriptions from banks in any country and language.

Sends two separate LLM batches — one for expenses, one for income — each with a
prompt tailored to extract the relevant counterpart:
  • Expenses (amount < 0): find the RECIPIENT / PAYEE  (destinatario del pagamento)
  • Income  (amount ≥ 0): find the SENDER   / PAYER    (mittente / ordinante)

Pipeline position:
    _normalize_df_with_schema()   → raw description stored in raw_description
    clean_descriptions_batch()    ← THIS MODULE (two directional passes)
    categorize_batch()            → sees cleaned counterpart name

transaction["description"]     = cleaned counterpart (used for categorization + display)
transaction["raw_description"] = original text      (used for SHA-256 dedup, never changed)
"""
from __future__ import annotations

import json
import unicodedata
from decimal import Decimal
from pathlib import Path

from core.llm_backends import LLMBackend, call_with_fallback
from core.sanitizer import SanitizationConfig, redact_pii, restore_owner_placeholders
from support.logging import setup_logging

logger = setup_logging()


def _strip_non_text(text: str) -> str:
    """Remove emoji and non-text symbols, keeping letters, digits, punctuation and spaces.

    Uses Unicode general categories:
      L* = letters (all scripts, accented chars included)
      N* = digits and numeric chars
      P* = punctuation (. , ; : ' " - etc.)
      Z* = separators / spaces
    Everything else (S* symbols including emoji ✅🏬€, C* control chars) is dropped.
    The result is collapsed to single spaces and stripped.
    """
    kept = [
        ch for ch in text
        if unicodedata.category(ch)[0] in ("L", "N", "P", "Z") or ch in " \t"
    ]
    return " ".join("".join(kept).split())


# ── Prompts (loaded from prompts/description_cleaner.json) ────────────────────

_PROMPTS_FILE = Path(__file__).parent.parent / "prompts" / "description_cleaner.json"


def _load_prompts() -> dict:
    with open(_PROMPTS_FILE, encoding="utf-8") as f:
        return json.load(f)


_PROMPTS = _load_prompts()

_SYSTEM_EXPENSE = _PROMPTS["system_expense"]
_SYSTEM_INCOME = _PROMPTS["system_income"]
_USER_TEMPLATE = _PROMPTS["user_template"]
_RESPONSE_SCHEMA = _PROMPTS["response_schema"]


# ── Public API ────────────────────────────────────────────────────────────────

def clean_descriptions_batch(
    transactions: list[dict],
    llm_backend: LLMBackend,
    fallback_backend: LLMBackend | None = None,
    batch_size: int = 30,
    source_name: str = "unknown",
    sanitize_config: SanitizationConfig | None = None,
) -> list[dict]:
    """Extract counterpart names from transaction descriptions using two directional passes.

    Pass 1 — expenses (amount < 0):  find RECIPIENT / PAYEE
    Pass 2 — income  (amount >= 0):  find SENDER   / PAYER

    Privacy — owner names:
        Before sending each description to the LLM, owner names are replaced with
        indexed placeholders (<OWNER_0>, <OWNER_1>, …) via redact_pii().  After the
        LLM returns the cleaned counterpart, restore_owner_placeholders() puts the
        real names back.  This means giroconto detection (which matches on owner names)
        works identically whether a local or remote (API) backend is used.

    Modifies transaction["description"] in place.
    transaction["raw_description"] is never touched.

    Args:
        transactions: list of transaction dicts (must have "description" and "amount").
        llm_backend: primary LLM backend.
        fallback_backend: optional local fallback.
        batch_size: number of descriptions per LLM call (default 30).
        source_name: used in log messages only.
        sanitize_config: if provided, owner names are redacted before the LLM call
            and restored afterwards.

    Returns:
        The same list with updated "description" fields.
    """
    if not transactions:
        return transactions

    txs = list(transactions)

    # Split by direction — keep original index for reassembly
    expense_indices: list[int] = []
    income_indices: list[int] = []

    for i, tx in enumerate(txs):
        amt = tx.get("amount")
        try:
            is_expense = Decimal(str(amt)) < 0 if amt is not None else False
        except Exception:
            is_expense = False
        if is_expense:
            expense_indices.append(i)
        else:
            income_indices.append(i)

    expense_cleaned = income_cleaned = 0

    # ── Pass 1: expenses → find recipient ────────────────────────────────────
    if expense_indices:
        expense_cleaned = _process_group(
            txs, expense_indices, _SYSTEM_EXPENSE,
            llm_backend, fallback_backend, batch_size, source_name, "expense",
            sanitize_config=sanitize_config,
        )

    # ── Pass 2: income → find sender ─────────────────────────────────────────
    if income_indices:
        income_cleaned = _process_group(
            txs, income_indices, _SYSTEM_INCOME,
            llm_backend, fallback_backend, batch_size, source_name, "income",
            sanitize_config=sanitize_config,
        )

    logger.info(
        f"clean_descriptions_batch [{source_name}]: "
        f"expenses={expense_cleaned}/{len(expense_indices)} cleaned, "
        f"income={income_cleaned}/{len(income_indices)} cleaned"
    )
    return txs


# ── Internal ──────────────────────────────────────────────────────────────────

def _process_group(
    txs: list[dict],
    indices: list[int],
    system_prompt: str,
    llm_backend: LLMBackend,
    fallback_backend: LLMBackend | None,
    batch_size: int,
    source_name: str,
    label: str,
    sanitize_config: SanitizationConfig | None = None,
) -> int:
    """Process a group of transactions (expense or income) in batches.
    Updates txs[i]["description"] in place. Returns count of cleaned descriptions.

    Privacy flow (when sanitize_config is provided):
        1. raw_description → redact_pii()     → <OWNER_N> placeholders replace names
        2. sanitized text  → LLM              → counterpart extracted (may contain <OWNER_N>)
        3. LLM result      → restore_owner_placeholders() → real name reinstated
    """
    cleaned_count = 0

    for batch_start in range(0, len(indices), batch_size):
        batch_indices = indices[batch_start: batch_start + batch_size]
        # Prefer raw_description: it already contains all description_cols merged
        # (done by _normalize_df_with_schema) and preserves original casing —
        # better for LLM pattern matching than the casefold'd "description".
        raw_descs = [
            txs[i].get("raw_description") or txs[i].get("description") or ""
            for i in batch_indices
        ]

        # Always redact before LLM (owner names + IBAN/PAN/fiscal code)
        # Then strip emoji and non-text symbols so the LLM sees clean text only
        llm_descs = [_strip_non_text(redact_pii(d, sanitize_config)) for d in raw_descs]

        cleaned = _call_llm_batch(
            llm_descs, system_prompt, llm_backend, fallback_backend,
            source_name, label,
        )

        for j, idx in enumerate(batch_indices):
            original = raw_descs[j]
            result = cleaned[j] if j < len(cleaned) else None
            if result:
                # Restore <OWNER_N> → real name before storing
                result = restore_owner_placeholders(result, sanitize_config)
                result = result.strip()
                # Discard known bad LLM outputs: "null", "none", "n/a", etc.
                if result.lower() in {"null", "none", "n/a", "na", "nan", "-", "—"}:
                    result = None
            if result and len(result) >= 2 and result != original:
                txs[idx]["description"] = result
                cleaned_count += 1

    return cleaned_count


def _call_llm_batch(
    descriptions: list[str],
    system_prompt: str,
    llm_backend: LLMBackend,
    fallback_backend: LLMBackend | None,
    source_name: str,
    label: str,
) -> list[str]:
    """Single LLM call for one directional batch. Returns same-length list.
    Falls back to original descriptions on any failure.
    """
    n = len(descriptions)
    descriptions_json = json.dumps(descriptions, ensure_ascii=False, indent=2)
    user_prompt = _USER_TEMPLATE.format(n=n, descriptions_json=descriptions_json)

    result, backend_used = call_with_fallback(
        primary=llm_backend,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        json_schema=_RESPONSE_SCHEMA,
        fallback=fallback_backend,
    )

    if result is None:
        logger.warning(
            f"clean_descriptions_batch [{source_name}] {label}: "
            f"LLM failed — keeping original descriptions"
        )
        return descriptions

    results = result.get("results")
    if not isinstance(results, list):
        logger.warning(
            f"clean_descriptions_batch [{source_name}] {label}: "
            f"unexpected response type {type(results)!r} — keeping originals"
        )
        return descriptions

    if len(results) != n:
        logger.warning(
            f"clean_descriptions_batch [{source_name}] {label}: "
            f"unexpected response shape (expected {n}, got {len(results)})"
            f" — using partial results, keeping originals for missing entries"
        )
        # Pad or truncate: use what we have, fall back to original for the rest
        results = list(results[:n]) + [None] * max(0, n - len(results))

    logger.debug(
        f"clean_descriptions_batch [{source_name}] {label}: "
        f"batch of {n} via {backend_used}"
    )
    mapped = [str(r) if r else descriptions[i] for i, r in enumerate(results)]
    for i, (inp, out) in enumerate(zip(descriptions, mapped)):
        logger.debug(f"  [{label}] #{i}: {inp!r} → {out!r}")
    return mapped
