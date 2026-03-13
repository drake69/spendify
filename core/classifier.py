"""Document classification (Flow 2 / RF-01).

Given raw tabular data from an unknown source, uses LLM structured output to
produce a DocumentSchema that can be persisted as a template for Flow 1.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from core.llm_backends import LLMBackend, SanitizationRequiredError, call_with_fallback
from core.normalizer import compute_columns_key
from core.sanitizer import SanitizationConfig, sanitize_dataframe_descriptions
from core.schemas import DocumentSchema
from support.logging import setup_logging

logger = setup_logging()

_PROMPTS_FILE = Path(__file__).parent.parent / "prompts" / "classifier.json"

def _load_prompts() -> dict:
    with open(_PROMPTS_FILE, encoding="utf-8") as f:
        return json.load(f)

_PROMPTS = _load_prompts()


def classify_document(
    df_raw: pd.DataFrame,
    llm_backend: LLMBackend,
    source_name: str = "unknown",
    sanitize: bool = True,
    sanitize_config: SanitizationConfig | None = None,
    fallback_backend: LLMBackend | None = None,
) -> DocumentSchema | None:
    """
    Flow 2: classify a raw DataFrame and return a DocumentSchema.

    Args:
        df_raw: raw DataFrame as loaded from CSV/Excel (no normalization yet).
        llm_backend: the LLM backend to use.
        source_name: name of the source file (for logging).
        sanitize: whether to sanitize descriptions before sending to LLM.
        sanitize_config: PII sanitization configuration.
        fallback_backend: fallback LLM backend (must be local).

    Returns:
        DocumentSchema or None if classification failed.
    """
    if llm_backend.is_remote and not sanitize:
        raise SanitizationRequiredError(
            "Sanitization is mandatory for remote LLM backends (RF-10)."
        )

    # Build a compact sample for the prompt (max 20 rows)
    sample = df_raw.head(20).copy()

    if sanitize:
        # Sanitize all string columns
        for col in sample.select_dtypes(include="object").columns:
            sample[col] = sanitize_dataframe_descriptions(
                sample[col].astype(str).tolist(), sanitize_config
            )

    sample_json = sample.to_json(orient="records", force_ascii=False)
    columns_list = df_raw.columns.tolist()

    user_prompt = _PROMPTS["user_template"].format(
        source_name=source_name,
        columns_list=columns_list,
        sample_json=sample_json,
    )

    schema = DocumentSchema(
        doc_type="unknown",
        date_col="",
        amount_col="",
        sign_convention="signed_single",
        date_format="%d/%m/%Y",
        account_label=source_name,
        confidence="low",
    )
    json_schema = schema.llm_json_schema()

    result, backend_used = call_with_fallback(
        primary=llm_backend,
        system_prompt=_PROMPTS["system"],
        user_prompt=user_prompt,
        json_schema=json_schema,
        fallback=fallback_backend,
    )

    if result is None:
        logger.warning(f"classify_document: all backends failed for {source_name}")
        return None

    logger.info(f"classify_document: classified via {backend_used} (confidence={result.get('confidence')})")

    # Validate that column names returned by the LLM actually exist in the DataFrame.
    # Tries case-insensitive match before nullifying.
    result = _coerce_column_names(result, list(df_raw.columns), source_name)

    # Step 0: deterministic override of invert_sign based on column name semantics.
    # This runs AFTER the LLM so it cannot be ignored by weaker local models.
    result = _apply_step0_invert_sign(result, source_name)

    try:
        doc_schema = DocumentSchema(**result)
        # Use columns fingerprint as cache key, not the filename, so the same
        # bank layout is recognised across differently-named export files.
        doc_schema.source_identifier = compute_columns_key(df_raw)
        return doc_schema
    except Exception as exc:
        logger.error(f"classify_document: schema validation failed: {exc}")
        return None


# ── Helpers ───────────────────────────────────────────────────────────────────

_COLUMN_FIELDS = (
    "date_col", "date_accounting_col",
    "amount_col", "debit_col", "credit_col",
    "description_col", "currency_col",
)


# ── Step 0: deterministic invert_sign override ────────────────────────────────

# Partial, case-insensitive matches. A column name is "outflow" if any of these
# strings appear in it (e.g. "Addebiti carta" matches "addebito").
_OUTFLOW_SYNONYMS: frozenset[str] = frozenset({
    "uscita", "uscite",
    "addebito", "addebiti",
    "pagamento", "pagamenti",
    "importo addebitato",
    "spesa", "spese",
    "dare",
})
_INFLOW_SYNONYMS: frozenset[str] = frozenset({
    "entrata", "entrate",
    "accredito", "accrediti",
    "importo accreditato",
    "avere",
    "credito",
})
_BANK_DOC_TYPES: frozenset[str] = frozenset({"bank_account", "savings"})


def _apply_step0_invert_sign(result: dict, source_name: str) -> dict:
    """Deterministic Step 0: override invert_sign based on amount column name.

    Called after LLM output so it prevails over any model misclassification.
    Only applies when sign_convention == signed_single (debit/credit columns
    already encode directionality; invert_sign is irrelevant for them).

    Outflow synonyms  (Uscita, Addebito, Dare …) → invert_sign = True
        (expenses stored as positive absolute values → must be negated)
        Exception: bank_account / savings always keep invert_sign = False.
    Inflow synonyms   (Entrata, Accredito, Avere …) → invert_sign = False
        (incomes stored as positive values → no negation needed)
    Neutral names     (Importo, Amount …) → keep LLM decision unchanged.
    """
    out = dict(result)

    # Step 0 only applies to signed_single (one amount column)
    convention = str(out.get("sign_convention", "")).lower()
    if convention not in ("signed_single", ""):
        return out

    doc_type = str(out.get("doc_type", "")).lower()
    amount_col = str(out.get("amount_col") or "").strip().lower()

    is_outflow = any(syn in amount_col for syn in _OUTFLOW_SYNONYMS)
    is_inflow = any(syn in amount_col for syn in _INFLOW_SYNONYMS)

    if is_outflow and doc_type not in _BANK_DOC_TYPES:
        if not out.get("invert_sign"):
            logger.info(
                f"classify_document [{source_name}]: Step 0 override — "
                f"amount_col='{out.get('amount_col')}' is an outflow synonym "
                f"→ invert_sign=True"
            )
            out["invert_sign"] = True
    elif is_inflow:
        if out.get("invert_sign"):
            logger.info(
                f"classify_document [{source_name}]: Step 0 override — "
                f"amount_col='{out.get('amount_col')}' is an inflow synonym "
                f"→ invert_sign=False"
            )
            out["invert_sign"] = False

    return out


def _coerce_column_names(result: dict, available: list[str], source_name: str) -> dict:
    """
    For every column-mapping field in result, ensure the value is an actual column
    in `available`. Tries case-insensitive match first; nullifies on no match.
    Logs a warning for each correction so debugging is easy.
    """
    lower_map = {c.lower(): c for c in available}
    out = dict(result)
    for field in _COLUMN_FIELDS:
        val = out.get(field)
        if not val:
            continue
        if val in available:
            continue  # exact match, keep as-is
        # try case-insensitive
        canonical = lower_map.get(val.lower())
        if canonical:
            logger.info(
                f"classify_document [{source_name}]: coerced {field} "
                f"'{val}' → '{canonical}' (case-insensitive match)"
            )
            out[field] = canonical
        else:
            logger.warning(
                f"classify_document [{source_name}]: {field}='{val}' not found in "
                f"columns {available!r} — setting to null"
            )
            out[field] = None
    return out
