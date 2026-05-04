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


def _containment_score(output_name: str, input_desc: str) -> float:
    """Score how well output_name is 'contained' within input_desc.

    Uses token-level Jaccard on normalised lowercased words.
    A high score means every word in the output appears in the input —
    the classic signal that the LLM correctly extracted a substring.
    """
    def tokens(s: str) -> set[str]:
        s = s.lower()
        s = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in s)
        return set(s.split())

    out_tok = tokens(output_name)
    in_tok = tokens(input_desc)
    if not out_tok:
        return 0.0
    # Containment: |out ∩ in| / |out|  (how much of the output is in the input)
    return len(out_tok & in_tok) / len(out_tok)


def _reverse_match(
    unresolved: list[tuple[int, str]],
    unclaimed_names: list[str],
    source_name: str,
    label: str,
) -> dict[int, str]:
    """I-17: greedy containment-based reverse matching.

    For each (global_idx, input_description) in `unresolved`, find the best
    unclaimed output name by containment score.  Assignments are made greedily
    (highest score first) so each output name is used at most once.

    Returns a dict {global_idx: name} for assignments with score > 0.
    """
    # Build all (score, input_idx_in_list, name_idx_in_list) triples
    scores: list[tuple[float, int, int]] = []
    for i, (_, inp) in enumerate(unresolved):
        for j, name in enumerate(unclaimed_names):
            sc = _containment_score(name, inp)
            if sc > 0:
                scores.append((sc, i, j))

    scores.sort(reverse=True)

    assigned_inputs: set[int] = set()
    assigned_names: set[int] = set()
    result: dict[int, str] = {}

    for sc, i, j in scores:
        if i in assigned_inputs or j in assigned_names:
            continue
        global_idx, inp = unresolved[i]
        name = unclaimed_names[j]
        result[global_idx] = name
        assigned_inputs.add(i)
        assigned_names.add(j)
        logger.debug(
            f"I-17 reverse-match [{source_name}] {label}: "
            f"idx={global_idx} score={sc:.2f} {inp!r} → {name!r}"
        )

    return result


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
            caller="description_cleaner",
            step=f"batch_{label}",
            source_name=source_name,
            batch_size=n,
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

        # Map by idx (anti-shuffle, I-16)
        idx_to_name: dict[int, str] = {}
        for item in results:
            if isinstance(item, dict) and "idx" in item and "name" in item:
                idx_to_name[item["idx"]] = str(item["name"])
            elif isinstance(item, str):
                # Fallback: old-style flat array (backward compat)
                pass

        # Collect output names that were not claimed by any idx
        claimed_idx = set(idx_to_name.keys())
        unclaimed_names = [
            str(item["name"])
            for item in results
            if isinstance(item, dict) and "name" in item
            and item.get("idx") not in claimed_idx
        ]

        # I-17: reverse matching — recover positions where idx was missing/wrong.
        # For each unresolved input position, pick the unclaimed output name whose
        # normalised form is most contained within the input description.
        # Greedy assignment (best score first) avoids duplicate assignments.
        unresolved = [
            (batch_start + i, batch[i])
            for i in range(n)
            if (batch_start + i) not in claimed_idx
        ]
        if unresolved and unclaimed_names:
            assigned = _reverse_match(unresolved, unclaimed_names, source_name, label)
            idx_to_name.update(assigned)

        for i, d in enumerate(batch):
            global_idx = batch_start + i
            name = idx_to_name.get(global_idx)
            if name:
                all_results[global_idx] = name
            else:
                all_results[global_idx] = d  # keep original

        n_by_idx = len(claimed_idx & {batch_start + i for i in range(n)})
        n_by_rev = len(idx_to_name) - n_by_idx
        logger.debug(
            f"clean_descriptions_batch [{source_name}] {label}: "
            f"batch {batch_start}..{batch_start + n} via {backend_used}, "
            f"{n_by_idx}/{n} by idx, {n_by_rev}/{n} by reverse-match"
        )

    return [r if r else descriptions[i] for i, r in enumerate(all_results)]
