"""Counterpart extraction from verbose Italian bank descriptions (RF-02 pre-categorization).

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
from decimal import Decimal

from core.llm_backends import LLMBackend, call_with_fallback
from core.sanitizer import SanitizationConfig, redact_pii, restore_owner_placeholders
from support.logging import setup_logging

logger = setup_logging()

# ── Prompts ───────────────────────────────────────────────────────────────────

_SYSTEM_EXPENSE = """\
You are a financial transaction description parser specialized in Italian bank exports.

These descriptions are EXPENSE transactions (money going OUT).
Your task: extract ONLY the RECIPIENT / PAYEE — the merchant, business, or person
that received the payment.

IMPORTANT: the counterpart name can appear ANYWHERE in the description — before the
payment-type keyword, after it, or repeated on both sides. Always scan the full string.

SEMANTIC GUIDE — payment types and where to look:
  • "POS" / "Pagam. POS" / "PAGAMENTO POS {amount} EUR DEL {date} A ({country})"
      → POS card payment; counterpart = merchant name (often after country code, but
        may also appear before the keyword)
      → e.g. "pagam. pos - pagamento pos 352,00 eur del 23.12.2025 a (ita) NOTORIOUS CINEMAS carta..."
              → "Notorious Cinemas"
  • "pagamento con carta {card_number}" / "pagamento con carta"
      → card payment; counterpart = business/merchant name appearing before or after
        the keyword (ignore the card number)
      → e.g. "vietgnam srl pagamento con carta 5179090003789315 vietgnam srl milan"
              → "Vietgnam SRL"
  • "Bonif. v/terzi" / "Disposizione" / "Vostra disposizione" / "Disposizione bonifico SCT"
      → outgoing wire transfer; counterpart = beneficiary name that follows "ORD." or the prefix
  • "Addebito diretto SDD" / "RID"
      → direct debit; counterpart = creditor/company name that follows
  • "Delega Unica" / "F24 web" / "F24"
      → tax payment; counterpart = "Agenzia delle Entrate" (unless a specific payee is named)
  • "Pagamento utenza" / "Bollettino"
      → utility/bill payment; counterpart = utility company name
  • "Canone" / "Commissione" / "Spese"
      → bank fee; counterpart = bank name or fee type (keep concise)

STRIP completely from the result:
  • All payment-type keywords listed above
  • Amounts embedded in text: "352,00 EUR", "9.798,76 EUR", "del 5,95 EUR"
  • Dates: "DEL 23.12.2025", "del 23.12.2025", "2025-12-29/10.41"
  • Card numbers (any sequence of 13–19 digits)
  • Card / auth codes: "CARTA ****0178", "CAU 98105", "NDS 824402523"
  • Reference codes: "RIF:209403494", "/INV/24-2025-FE", "/SEPASCT/"
  • The word "ORD." and anything after it that is not the counterpart
  • Country codes: "(ITA)", "(IRL)", "A (ITA)"
  • City names that appear after the business name
  • SEPA identifiers, CRO codes, BIC codes, routing references
  • Duplicate occurrences of the same name (keep only one)
  • The literal string "nan" (artifact from empty spreadsheet cells)

KEEP: only the merchant name, business name, or beneficiary person (first + last name).
If the description is already clean (e.g. "Netflix", "Esselunga"), return it as-is.

FALLBACK: if nothing meaningful can be extracted, return the original string unchanged.
Never return an empty string.

Output: a JSON object {"results": ["recipient1", "recipient2", ...]} — same order as input.
"""

_SYSTEM_INCOME = """\
You are a financial transaction description parser specialized in Italian bank exports.

These descriptions are INCOME transactions (money coming IN).
Your task: extract ONLY the SENDER / PAYER — the person, business, or institution
that sent the payment.

IMPORTANT: the counterpart name can appear ANYWHERE in the description — before the
payment-type keyword, after it, or repeated on both sides. Always scan the full string.

SEMANTIC GUIDE — payment types and where to look:
  • "Bonif. v/fav." (= bonifico a vostro favore, received BY you)
      → incoming wire transfer; counterpart = sender name that follows "ORD." or the prefix
      → e.g. "bonif. v/fav. - rif:209403494ord. CENTRO DIAGNOSTICO ITALIANO SPA /inv/..."
              → "Centro Diagnostico Italiano SPA"
  • "Accredito bonifico" / "Accredito da"
      → generic incoming transfer; counterpart = sender name
  • "Accredito stipendio" / "Stipendio"
      → salary credit; counterpart = employer name
  • "Girofondo" / "Giro interno" / "Giro conto"
      → internal transfer; counterpart = source account label or bank name
  • "Rimborso"
      → refund; counterpart = company issuing the refund
  • "Incasso" / "Accredito SDD"
      → direct credit collection; counterpart = payer name

BANK-ORIGINATED TRANSACTIONS — no external sender exists; return a short, clean Italian label:
  • "Liquidazione interessi" / "Maturazione interessi" / "Accredito interessi"
      → bank crediting accrued interest → return "Interessi bancari"
  • "Liquidazione commissioni" / "Liquidazione spese" / "Liquidazione interessi-commissioni-spese"
      → bank settling fees/commissions → return "Liquidazione bancaria"
  • "Competenze" / "Capitalizzazione"
      → bank charges/capitalisation → return "Competenze bancarie"
  • "Canone" / "Spese tenuta conto"
      → account maintenance fee (but this is an expense, classify as bank fee if income it's a reversal)
      → return "Rimborso spese bancarie"
  These are self-contained bank entries; strip all rate info ("1,75%"), period references
  ("a fronte del saldo", "trim.", "semestre"), and return only the label above.

STRIP completely from the result:
  • All payment-type keywords listed above
  • Amounts embedded in text: "300,00 EUR", "9.798,76 EUR"
  • Interest rates: "1,75%", "0,50% annuo"
  • Period/condition qualifiers: "a fronte del saldo", "trim.", "semestre", "annuo"
  • Dates: "DEL 23.12.2025", "del 23.12.2025", "2025-12-29"
  • Card numbers (any sequence of 13–19 digits)
  • Reference codes: "RIF:", "CRO:", "/INV/", "/SEPASCT/"
  • The word "ORD." and reference identifiers before the name
  • City names that appear after the sender name
  • SEPA identifiers, BIC codes, routing references
  • Duplicate occurrences of the same name (keep only one)
  • The literal string "nan" (artifact from empty spreadsheet cells)

KEEP: only the sender's name — person (first + last), company name, or institution name.
If the description is already clean (e.g. "Giovanni Bianchi", "Azienda SRL"), return it as-is.

FALLBACK: if nothing meaningful can be extracted, return the original string unchanged.
Never return an empty string.

Output: a JSON object {"results": ["sender1", "sender2", ...]} — same order as input.
"""

_USER_TEMPLATE = """\
Extract the counterpart from each of these {n} transaction descriptions.
Return exactly {n} results in the same order.

{descriptions_json}
"""

_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Extracted counterpart names, one per input description",
        }
    },
    "required": ["results"],
}


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

        # Redact owner names before sending to LLM
        if sanitize_config and sanitize_config.owner_names:
            llm_descs = [redact_pii(d, sanitize_config) for d in raw_descs]
        else:
            llm_descs = raw_descs

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
    if not isinstance(results, list) or len(results) != n:
        logger.warning(
            f"clean_descriptions_batch [{source_name}] {label}: "
            f"unexpected response shape "
            f"(expected {n}, got {len(results) if isinstance(results, list) else type(results)!r})"
            f" — keeping originals"
        )
        return descriptions

    logger.debug(
        f"clean_descriptions_batch [{source_name}] {label}: "
        f"batch of {n} via {backend_used}"
    )
    return [str(r) if r else descriptions[i] for i, r in enumerate(results)]
