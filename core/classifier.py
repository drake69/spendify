"""Document classification (Flow 2 / RF-01).

Given raw tabular data from an unknown source, uses LLM structured output to
produce a DocumentSchema that can be persisted as a template for Flow 1.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from core.llm_backends import LLMBackend, SanitizationRequiredError, call_with_fallback
from core.sanitizer import SanitizationConfig, sanitize_dataframe_descriptions
from core.schemas import DocumentSchema
from support.logging import setup_logging

logger = setup_logging()

_SYSTEM_PROMPT = """You are an expert at classifying bank statement files.

Given a sample of raw tabular data (CSV/Excel) from a bank statement, you must:
1. Identify the document type (bank_account, credit_card, debit_card, prepaid_card, savings, unknown).
2. Map column names to their semantic roles (date, amount, description, etc.).
3. Detect the date format, sign convention, encoding, and any internal-transfer keywords.
4. Return a complete DocumentSchema as JSON using the provided schema.

Rules:
- Use ONLY the column names present in the sample.
- sign_convention must be one of: signed_single, debit_positive, credit_negative.
- date_format must be a valid Python strftime string (e.g. "%d/%m/%Y").
- confidence must reflect how certain you are (high/medium/low).
- If you cannot determine a field, set it to null.
- Do NOT expose any PII in your response.
"""


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

    user_prompt = f"""Classify this bank statement sample.

Source file: {source_name}
Columns: {columns_list}
Sample (first 20 rows):
{sample_json}

Return the DocumentSchema JSON using the provided tool.
"""

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
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        json_schema=json_schema,
        fallback=fallback_backend,
    )

    if result is None:
        logger.warning(f"classify_document: all backends failed for {source_name}")
        return None

    logger.info(f"classify_document: classified via {backend_used} (confidence={result.get('confidence')})")

    try:
        doc_schema = DocumentSchema(**result)
        doc_schema.source_identifier = source_name
        return doc_schema
    except Exception as exc:
        logger.error(f"classify_document: schema validation failed: {exc}")
        return None
