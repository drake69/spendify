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

    try:
        doc_schema = DocumentSchema(**result)
        doc_schema.source_identifier = source_name
        return doc_schema
    except Exception as exc:
        logger.error(f"classify_document: schema validation failed: {exc}")
        return None
