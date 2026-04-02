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

    Optimization: deduplicates descriptions before sending to LLM.
    300 transactions with 40 unique descriptions → only 40 LLM calls.
    Results are remapped to all transactions sharing the same description.

    Privacy flow (when sanitize_config is provided):
        1. raw_description → redact_pii()     → <OWNER_N> placeholders replace names
        2. sanitized text  → LLM              → counterpart extracted (may contain <OWNER_N>)
        3. LLM result      → restore_owner_placeholders() → real name reinstated
    """
    cleaned_count = 0

    # ── Step 1: Dedup descriptions ────────────────────────────────────────
    # Group indices by unique raw_description → send each unique desc once
    desc_to_indices: dict[str, list[int]] = {}
    for i in indices:
        raw = txs[i].get("raw_description") or txs[i].get("description") or ""
        desc_to_indices.setdefault(raw, []).append(i)

    unique_descs = list(desc_to_indices.keys())
    n_total = len(indices)
    n_unique = len(unique_descs)
    if n_unique < n_total:
        logger.info(
            f"clean_descriptions_batch [{source_name}] {label}: "
            f"dedup {n_total} → {n_unique} unique descriptions"
        )

    # ── Step 2: Clean unique descriptions via LLM (indexed batch) ────────
    # Sanitize + strip non-text
    llm_descs = [_strip_non_text(redact_pii(d, sanitize_config)) for d in unique_descs]

    cleaned = _call_llm_batch(
        llm_descs, system_prompt, llm_backend, fallback_backend,
        batch_size, source_name, label,
    )

    # ── Step 3: Remap results to all transactions ────────────────────────
    for j, raw_desc in enumerate(unique_descs):
        result = cleaned[j] if j < len(cleaned) else None
        if result:
            result = restore_owner_placeholders(result, sanitize_config)
            result = result.strip()
            if result.lower() in {"null", "none", "n/a", "na", "nan", "-", "—"}:
                result = None
        if result and len(result) >= 2 and result != raw_desc:
            # Apply to ALL transactions with this raw_description
            for idx in desc_to_indices[raw_desc]:
                txs[idx]["description"] = result
                cleaned_count += 1

    return cleaned_count


# ── Indexed LLM schema ────────────────────────────────────────────────────

_INDEXED_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "idx": {"type": "integer"},
                    "name": {"type": "string"},
                },
                "required": ["idx", "name"],
            },
            "description": "Extracted counterpart names with index matching input order",
        }
    },
    "required": ["results"],
}

_INDEXED_USER_TEMPLATE = (
    "Extract the counterpart from each of these {n} transaction descriptions.\n"
    "Return exactly {n} results. Each result must include the idx from the input.\n\n"
    "{descriptions_json}"
)


def _call_llm_batch(
    descriptions: list[str],
    system_prompt: str,
    llm_backend: LLMBackend,
    fallback_backend: LLMBackend | None,
    batch_size: int,
    source_name: str,
    label: str,
) -> list[str]:
    """Send descriptions to LLM in indexed batches. Returns same-length list.
    Uses indexed input/output to prevent shuffle on small models.
    Falls back to original descriptions on any failure.
    """
    all_results: list[str | None] = [None] * len(descriptions)

    for batch_start in range(0, len(descriptions), batch_size):
        batch = descriptions[batch_start: batch_start + batch_size]
        n = len(batch)

        # Build indexed input
        indexed_input = [{"idx": batch_start + i, "name": d} for i, d in enumerate(batch)]
        descriptions_json = json.dumps(indexed_input, ensure_ascii=False, indent=2)
        user_prompt = _INDEXED_USER_TEMPLATE.format(n=n, descriptions_json=descriptions_json)

        result, backend_used = call_with_fallback(
            primary=llm_backend,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            json_schema=_INDEXED_RESPONSE_SCHEMA,
            fallback=fallback_backend,
        )

        if result is None:
            logger.warning(
                f"clean_descriptions_batch [{source_name}] {label}: "
                f"LLM failed on batch {batch_start}..{batch_start + n} — keeping originals"
            )
            for i, d in enumerate(batch):
                all_results[batch_start + i] = d
            continue

        results = result.get("results")
        if not isinstance(results, list):
            logger.warning(
                f"clean_descriptions_batch [{source_name}] {label}: "
                f"unexpected response type {type(results)!r} — keeping originals"
            )
            for i, d in enumerate(batch):
                all_results[batch_start + i] = d
            continue

        # Map by idx (anti-shuffle)
        idx_to_name: dict[int, str] = {}
        for item in results:
            if isinstance(item, dict) and "idx" in item and "name" in item:
                idx_to_name[item["idx"]] = str(item["name"])
            elif isinstance(item, str):
                # Fallback: old-style flat array (backward compat)
                pass

        for i, d in enumerate(batch):
            global_idx = batch_start + i
            name = idx_to_name.get(global_idx)
            if name:
                all_results[global_idx] = name
            else:
                all_results[global_idx] = d  # keep original

        logger.debug(
            f"clean_descriptions_batch [{source_name}] {label}: "
            f"batch {batch_start}..{batch_start + n} via {backend_used}, "
            f"{len(idx_to_name)}/{n} mapped by idx"
        )

    return [r if r else descriptions[i] for i, r in enumerate(all_results)]
