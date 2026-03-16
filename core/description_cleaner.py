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
from decimal import Decimal

from core.llm_backends import LLMBackend, call_with_fallback
from core.sanitizer import SanitizationConfig, redact_pii, restore_owner_placeholders
from support.logging import setup_logging

logger = setup_logging()

# ── Prompts ───────────────────────────────────────────────────────────────────

_SYSTEM_EXPENSE = """\
You are a financial transaction description parser. Descriptions may come from banks
in any country and language.

These are EXPENSE transactions (money going OUT).
Task: extract ONLY the RECIPIENT — the merchant, business, or person that received the payment.
The counterpart name can appear anywhere in the string; scan the full description.

WHAT TO STRIP (language-independent noise):
  • Payment-type labels (e.g. "POS", "Bonifico", "Virement", "Lastschrift", "wire transfer")
  • Beneficiary markers (strip the label, keep what follows):
    "Fv.", "F.V.", "Beg.", "Begünstigter", "Pour", "For the benefit of"
  • Amounts: "352,00 EUR", "9.798,76 EUR"
  • Dates: "23.12.2025", "2025-12-29", "29/10.41"
  • Card numbers (13–19 consecutive digits) and masked card refs ("CARTA ****0178")
  • Auth/transaction codes: "CAU 98105", "NDS 824402523"
  • Reference codes and SEPA fields: "RIF:", "CRO:", "/INV/", "/SEPASCT/", "/SEPADD/", BIC
  • "ORD." and any identifier tokens that follow it (unless the counterpart name follows)
  • Country codes: "(ITA)", "(IRL)", "(FRA)"
  • City names that appear after the business name
  • Repeated phrases — some banks duplicate text within a single field; keep only one
    occurrence (e.g. "Rimborso spese rimborso spese" → "Rimborso spese",
    "Luigi Rossi luigi rossi" → "Luigi Rossi")
  • The literal string "nan"

BANK-ORIGINATED EXPENSES (no external recipient — the bank itself charges):
  If the description is a bank interest charge, account fee, commission, or credit-card
  balance settlement with no identifiable external payee, return a short descriptive label
  in the same language as the description (e.g. "Interessi bancari", "Frais bancaires",
  "Bankgebühren", "Bank fees", "Saldo carta").

FALLBACK: if no name can be identified, return a short label describing the payment type
in the same language as the description. Never return "null", "none", "n/a", or empty string.

Examples (description → result):
  "pagam. pos - pagamento pos 352,00 eur del 23.12.2025 a (ita) NOTORIOUS CINEMAS carta..."
      → "Notorious Cinemas"
  "vietgnam srl pagamento con carta 5179090003789315 vietgnam srl milan"
      → "Vietgnam SRL"
  "Bonifico eseguito Carlo Brambilla Marta Pellegrino carlo brambilla marta pellegrino"
      → "Carlo Brambilla Marta Pellegrino"
  "Bonifico eseguito Rimborso spese rimborso spese"
      → "Bonifico"
  "VOSTRA DISPOSIZIONE Disposizione bonifico SCT Fv. ARCA FONDI SGR SPA [CF] 00000000031914"
      → "Arca Fondi SGR SPA"
  "Disposizione bonifico SCT Fv. MARIO ROSSI [CF] RIF:0012345"
      → "Mario Rossi"
  "Disposizione bonifico SCT Rapporto 06 77 Codice cliente 12345"
      → "Bonifico"
  "Virement AMAZON EU SARL 14,95 EUR 2025-11-03"
      → "Amazon EU SARL"
  "Lastschrift Netflix International 15,99 EUR 2025-10-15"
      → "Netflix International"
  "Liquidazione interessi debitori trim. 1,75% a fronte del saldo"
      → "Interessi bancari"
  "Saldo carta INARCASSACARD RIF:8834729"
      → "Saldo carta"
  "SOTTOSCRIZIONI FONDI E SICAV SOTTOSCRIZIONE ETICA AZIONARIO R DEP.TITOLI 081/663905/000"
      → "Etica Azionario R"
  "Subscription funds SICAV SUBSCRIPTION GLOBAL EQUITY FUND DEP.TITOLI 002/123456/000"
      → "Global Equity Fund"
  "RID ENEL ENERGIA SPA 00001234567 UTENZA GAS 987654"
      → "Enel Energia SPA"
  "Addebito diretto SDD TELECOM ITALIA SPA CID IT12345 RIF:20251201"
      → "Telecom Italia SPA"
  "SDD SEPA Direct Debit SPOTIFY AB mandate 0987654321"
      → "Spotify AB"
  "ADDEBITO DIRETTO SDD 00001234 /SEPADD/"
      → "Addebito diretto"
  "PRELIEVO CONTANTI SPORTELLO 23/12/2025 BANCA XYZ VIA ROMA MILANO"
      → "Prelievo contanti"
  "ATM WITHDRAWAL 29/10/2025 BANK OF IRELAND O CONNELL ST DUBLIN"
      → "Prelievo contanti"
  "PAGAMENTO F24 IRPEF ACCONTO II RATA 2025 [CF]"
      → "F24 IRPEF"
  "F24 IMU COMUNE DI MILANO CODICE TRIBUTO 3912"
      → "F24 IMU"
  "PAGAMENTO BOLLETTINO POSTALE 123456789 COMUNE DI MILANO TASSA RIFIUTI"
      → "Comune di Milano"
  "BOLLETTINO POSTALE 123456789 REF 20251201"
      → "Bollettino postale"

Output: a JSON object {"results": ["recipient1", "recipient2", ...]} — same order as input.
"""

_SYSTEM_INCOME = """\
You are a financial transaction description parser. Descriptions may come from banks
in any country and language.

These are INCOME transactions (money coming IN).
Task: extract ONLY the SENDER — the person, business, or institution that sent the payment.
The counterpart name can appear anywhere in the string; scan the full description.
It often follows labels like "ORD.", "FROM:", "DA:", "DE:" but not always.

WHAT TO STRIP (language-independent noise):
  • Payment-type labels (e.g. "Bonifico", "Accredito", "Virement", "Gutschrift", "wire transfer")
  • Amounts: "300,00 EUR", "9.798,76 EUR"
  • Interest rates: "1,75%", "0,50% annuo"
  • Period/condition qualifiers: "trim.", "semestre", "annuo", "a fronte del saldo"
  • Dates: "23.12.2025", "2025-12-29"
  • Card numbers (13–19 consecutive digits)
  • Reference codes and SEPA fields: "RIF:", "CRO:", "/INV/", "/SEPASCT/", BIC
  • "ORD." and any reference identifier tokens before the name
  • City names that appear after the sender name
  • Repeated phrases — some banks duplicate text within a single field; keep only one
    occurrence (e.g. "Mario Rossi mario rossi" → "Mario Rossi")
  • The literal string "nan"

BANK-ORIGINATED INCOME (no external sender — the bank itself is the source):
  If the description is an interest credit, fee reversal, or capitalisation with no
  identifiable external sender, return a short descriptive label in the same language
  as the description (e.g. "Interessi bancari", "Intérêts bancaires", "Bankzinsen",
  "Bank interest", "Liquidazione bancaria"). Strip all rate and period information.

FALLBACK: if no name can be identified, return a short label describing the transaction
type in the same language as the description. Never return "null", "none", "n/a", or empty string.

Examples (description → result):
  "bonif. v/fav. - rif:209403494ord. CENTRO DIAGNOSTICO ITALIANO SPA /inv/24-2025-FE"
      → "Centro Diagnostico Italiano SPA"
  "Bonifico ricevuto Corsaro luigi gerotti elena CORSARO LUIGI GEROTTI ELENA"
      → "Corsaro Luigi Gerotti Elena"
  "Virement reçu MARTIN DUPONT 500,00 EUR 2025-11-15"
      → "Martin Dupont"
  "Gutschrift MUSTER GMBH Überweisung 1.250,00 EUR"
      → "Muster GmbH"
  "Liquidazione interessi attivi a fronte del saldo trim. 0,50% annuo"
      → "Interessi bancari"
  "Liquidazione interessi-commissioni-spese semestre"
      → "Liquidazione bancaria"
  "ACCREDITO STIPENDIO ORD. ACME SRL [CF] CRO:12345678"
      → "Acme SRL"
  "Salary payment JOHNSON & JOHNSON LTD ref 2025-DEC"
      → "Johnson & Johnson Ltd"
  "RIMBORSO RID ENEL ENERGIA SPA 00001234567"
      → "Enel Energia SPA"
  "Rimborso spese trasferta ORD. MARIO ROSSI"
      → "Mario Rossi"
  "ACCREDITO PENSIONE INPS CRO:987654321 [CF]"
      → "INPS"
  "Pension payment SOCIAL SECURITY ADMINISTRATION ref 2025-12"
      → "Social Security Administration"
  "Rimborso fiscale Agenzia delle Entrate CRO:123456789"
      → "Agenzia delle Entrate"
  "Bonifico ricevuto <CARD_ID>   20250923"
      → "Bonifico da carta"
  "Bonifico ricevuto <CARD_ID>   <CARD_ID> 017   20241216"
      → "Bonifico da carta"

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

        # Always redact before LLM (owner names + IBAN/PAN/fiscal code)
        llm_descs = [redact_pii(d, sanitize_config) for d in raw_descs]

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
