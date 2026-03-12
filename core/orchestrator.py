"""Main processing pipeline (RF-01 through RF-07).

Implements both Flow 1 (deterministic + known template) and
Flow 2 (LLM-first / schema-on-read).
"""
from __future__ import annotations

import io
import os
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import chardet
import pandas as pd

from core.categorizer import (
    CategorizationResult,
    CategoryRule,
    TaxonomyConfig,
    categorize_batch,
)
from core.classifier import classify_document
from core.llm_backends import LLMBackend, BackendFactory, OllamaBackend
from core.models import (
    Confidence,
    DocumentType,
    GirocontoMode,
    TransactionType,
)
from core.normalizer import (
    apply_sign_convention,
    compute_file_hash,
    compute_transaction_id,
    detect_best_sheet,
    detect_delimiter,
    detect_encoding,
    detect_header_row,
    detect_internal_transfers,
    find_card_settlement_matches,
    normalize_description,
    parse_amount,
    parse_date_safe,
)
from core.sanitizer import SanitizationConfig, redact_pii
from core.schemas import DocumentSchema
from support.logging import setup_logging

logger = setup_logging()


@dataclass
class ProcessingConfig:
    llm_backend: str = "local_ollama"
    giroconto_mode: GirocontoMode = GirocontoMode.neutral
    window_days: int = 45
    max_gap_days: int = 5
    tolerance: Decimal = Decimal("0.01")
    tolerance_strict: Decimal = Decimal("0.005")
    settlement_days: int = 5
    settlement_days_strict: int = 1
    boundary_pre_post: int = 10
    confidence_threshold: float = 0.80
    require_keyword_confirmation: bool = True
    llm_timeout_s: int = 30
    batch_size_llm: int = 1
    sanitize_config: SanitizationConfig = field(default_factory=SanitizationConfig)

    # Backend kwargs
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "gemma3:12b"
    openai_model: str = "gpt-4o-mini"
    claude_model: str = "claude-3-5-haiku-20241022"


@dataclass
class ImportResult:
    batch_sha256: str
    source_name: str
    transactions: list[dict]
    doc_schema: Optional[DocumentSchema]
    reconciliations: list[dict]
    transfer_links: list[dict]
    skipped_duplicate: bool = False
    errors: list[str] = field(default_factory=list)
    flow_used: str = "unknown"  # "flow1" or "flow2"


def _build_backend(config: ProcessingConfig) -> LLMBackend:
    kwargs = {"timeout": config.llm_timeout_s}
    if config.llm_backend == "local_ollama":
        kwargs["base_url"] = config.ollama_base_url
        kwargs["model"] = config.ollama_model
    elif config.llm_backend == "openai":
        kwargs["model"] = config.openai_model
    elif config.llm_backend == "claude":
        kwargs["model"] = config.claude_model
    return BackendFactory.create(config.llm_backend, **kwargs)


def _get_fallback_backend(config: ProcessingConfig) -> Optional[OllamaBackend]:
    backend = OllamaBackend(
        base_url=config.ollama_base_url,
        model=config.ollama_model,
        timeout=config.llm_timeout_s,
    )
    if backend.is_available():
        return backend
    return None


def load_raw_dataframe(raw_bytes: bytes, filename: str) -> tuple[pd.DataFrame, str]:
    """
    Load a file into a raw DataFrame with pre-processing:
    encoding detection, sheet selection, header detection.

    Returns (df, encoding_used).
    """
    encoding = detect_encoding(raw_bytes)
    name_lower = filename.lower()

    if name_lower.endswith((".xlsx", ".xls")):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), read_only=True, data_only=True)
            sheet_name = detect_best_sheet(wb)
            wb.close()
        except Exception:
            sheet_name = 0  # fallback to first sheet

        df = pd.read_excel(io.BytesIO(raw_bytes), sheet_name=sheet_name)
        return df, encoding

    # CSV / text
    text = raw_bytes.decode(encoding, errors="replace")
    delimiter = detect_delimiter(text)
    lines = text.splitlines()
    skip_rows = detect_header_row(lines)

    df = pd.read_csv(
        io.StringIO(text),
        sep=delimiter,
        skiprows=skip_rows,
        engine="python",
        on_bad_lines="skip",
    )
    return df, encoding


def _normalize_df_with_schema(
    df: pd.DataFrame,
    schema: DocumentSchema,
    source_name: str,
) -> list[dict]:
    """
    Apply DocumentSchema to produce a list of canonical transaction dicts.
    """
    transactions = []

    for _, row in df.iterrows():
        # Parse date
        raw_date = row.get(schema.date_col, "")
        tx_date = parse_date_safe(str(raw_date), schema.date_format) if raw_date else None
        if tx_date is None:
            continue  # skip rows with unparseable date

        # Parse accounting date
        raw_date_acc = row.get(schema.date_accounting_col, "") if schema.date_accounting_col else None
        tx_date_acc = parse_date_safe(str(raw_date_acc), schema.date_format) if raw_date_acc else None

        # Parse amount
        amount = apply_sign_convention(
            row.to_dict(),
            schema.amount_col,
            schema.debit_col,
            schema.credit_col,
            schema.sign_convention,
        )
        if amount is None:
            continue

        # Description
        desc_raw = str(row.get(schema.description_col or "", "")) if schema.description_col else ""
        description = normalize_description(desc_raw)

        # Currency
        currency = str(row.get(schema.currency_col, schema.default_currency)) if schema.currency_col else schema.default_currency

        # Idempotency key
        tx_id = compute_transaction_id(source_name, tx_date, amount, description)

        # Infer tx_type from doc_type
        tx_type = _infer_tx_type(amount, schema.doc_type, description, schema.internal_transfer_patterns)

        transactions.append({
            "id": tx_id,
            "date": tx_date,
            "date_accounting": tx_date_acc,
            "amount": amount,
            "currency": currency,
            "description": description,
            "source_file": source_name,
            "doc_type": schema.doc_type.value if hasattr(schema.doc_type, 'value') else str(schema.doc_type),
            "account_label": schema.account_label,
            "tx_type": tx_type.value,
            "category": None,
            "subcategory": None,
            "category_confidence": None,
            "category_source": None,
            "reconciled": False,
            "to_review": False,
            "transfer_pair_id": None,
            "transfer_confidence": None,
        })

    return transactions


def _infer_tx_type(
    amount: Decimal,
    doc_type: DocumentType | str,
    description: str,
    transfer_patterns: list[str],
) -> TransactionType:
    """Infer the tx_type from basic heuristics (before transfer/reconciliation detection)."""
    import re
    if transfer_patterns:
        pattern = re.compile('|'.join(re.escape(p) for p in transfer_patterns), re.IGNORECASE)
        if pattern.search(description):
            return TransactionType.internal_out if amount < 0 else TransactionType.internal_in

    doc_str = doc_type.value if hasattr(doc_type, 'value') else str(doc_type)
    if doc_str == DocumentType.credit_card.value:
        return TransactionType.card_tx
    if amount > 0:
        return TransactionType.income
    return TransactionType.expense


def process_file(
    raw_bytes: bytes,
    filename: str,
    config: ProcessingConfig,
    taxonomy: TaxonomyConfig,
    user_rules: list[CategoryRule],
    known_schema: Optional[DocumentSchema] = None,
) -> ImportResult:
    """
    Process a single file through Flow 1 or Flow 2.

    Deduplication is handled at transaction level: each transaction has a
    deterministic SHA-256 ID; upsert_transaction silently skips any that
    already exist in the database.

    Args:
        raw_bytes: raw file content.
        filename: original filename (used in idempotency key).
        config: processing configuration.
        taxonomy: taxonomy configuration.
        user_rules: user-defined category rules from DB.
        known_schema: DocumentSchema from DB (if exists → Flow 1).

    Returns:
        ImportResult.
    """
    batch_sha256 = compute_file_hash(raw_bytes)
    backend = _build_backend(config)
    fallback = _get_fallback_backend(config)

    # Load raw data
    df_raw, encoding = load_raw_dataframe(raw_bytes, filename)
    if df_raw.empty:
        return ImportResult(
            batch_sha256=batch_sha256,
            source_name=filename,
            transactions=[],
            doc_schema=None,
            reconciliations=[],
            transfer_links=[],
            errors=["Empty file or no data found"],
        )

    flow_used = "flow1"
    doc_schema = known_schema

    # Flow 2: classify document if no known schema
    if doc_schema is None:
        flow_used = "flow2"
        logger.info(f"process_file: no known schema for {filename}, using Flow 2")
        doc_schema = classify_document(
            df_raw=df_raw,
            llm_backend=backend,
            source_name=filename,
            sanitize=True,
            sanitize_config=config.sanitize_config,
            fallback_backend=fallback,
        )
        if doc_schema is None or doc_schema.confidence == Confidence.low:
            logger.warning(f"process_file: classification failed or low confidence for {filename}")
            return ImportResult(
                batch_sha256=batch_sha256,
                source_name=filename,
                transactions=[],
                doc_schema=doc_schema,
                reconciliations=[],
                transfer_links=[],
                errors=["Document classification failed or low confidence; needs manual review"],
                flow_used=flow_used,
            )

    # Apply schema → canonical transactions
    transactions = _normalize_df_with_schema(df_raw, doc_schema, filename)
    if not transactions:
        return ImportResult(
            batch_sha256=batch_sha256,
            source_name=filename,
            transactions=[],
            doc_schema=doc_schema,
            reconciliations=[],
            transfer_links=[],
            errors=["No transactions could be parsed with the schema"],
            flow_used=flow_used,
        )

    # Build DataFrame for transfer detection
    tx_df = pd.DataFrame(transactions)

    # Internal transfer detection (RF-04)
    keyword_patterns = doc_schema.internal_transfer_patterns or []
    tx_df = detect_internal_transfers(
        tx_df,
        epsilon=config.tolerance,
        delta_days=config.settlement_days,
        epsilon_strict=config.tolerance_strict,
        delta_days_strict=config.settlement_days_strict,
        keyword_patterns=keyword_patterns,
        require_keyword_confirmation=config.require_keyword_confirmation,
    )

    # Card settlement reconciliation (RF-03)
    settlements = tx_df[tx_df["tx_type"] == TransactionType.card_settlement.value].to_dict("records")
    card_txs = tx_df[tx_df["tx_type"] == TransactionType.card_tx.value].to_dict("records")
    reconciliations = []
    if settlements and card_txs:
        reconciliations = find_card_settlement_matches(
            settlements=settlements,
            card_transactions=card_txs,
            epsilon=config.tolerance,
            window_days=config.window_days,
            max_gap_days=config.max_gap_days,
            boundary_k=config.boundary_pre_post,
        )

    # Extract transfer links
    transfer_links = []
    if "transfer_pair_id" in tx_df.columns:
        paired = tx_df[tx_df["transfer_pair_id"].notna()]
        processed_pairs: set = set()
        for _, row in paired.iterrows():
            pair_id = row["transfer_pair_id"]
            if pair_id in processed_pairs:
                continue
            processed_pairs.add(pair_id)
            pair_rows = paired[paired["transfer_pair_id"] == pair_id]
            if len(pair_rows) == 2:
                ids = pair_rows["id"].tolist()
                transfer_links.append({
                    "pair_id": pair_id,
                    "out_id": ids[0],
                    "in_id": ids[1],
                    "confidence": row.get("transfer_confidence", Confidence.medium.value),
                    "keyword_matched": row.get("tx_type") in (
                        TransactionType.internal_out.value,
                        TransactionType.internal_in.value,
                    ),
                })

    # Categorization cascade (skip internal/settlement rows)
    categorizable_types = {
        TransactionType.expense.value,
        TransactionType.income.value,
        TransactionType.card_tx.value,
        TransactionType.unknown.value,
    }
    to_categorize = tx_df[tx_df["tx_type"].isin(categorizable_types)].to_dict("records")
    cat_results = categorize_batch(
        transactions=to_categorize,
        taxonomy=taxonomy,
        user_rules=user_rules,
        llm_backend=backend,
        sanitize_config=config.sanitize_config,
        fallback_backend=fallback,
        confidence_threshold=config.confidence_threshold,
    )
    cat_map = {tx["id"]: result for tx, result in zip(to_categorize, cat_results)}

    # Merge categorization back
    for tx in transactions:
        result: Optional[CategorizationResult] = cat_map.get(tx["id"])
        if result:
            tx["category"] = result.category
            tx["subcategory"] = result.subcategory
            tx["category_confidence"] = result.confidence.value
            tx["category_source"] = result.source.value
            tx["to_review"] = result.to_review

    # Apply giroconto mode
    if config.giroconto_mode == GirocontoMode.exclude:
        excluded_types = {TransactionType.internal_out.value, TransactionType.internal_in.value}
        transactions = [tx for tx in transactions if tx["tx_type"] not in excluded_types]

    return ImportResult(
        batch_sha256=batch_sha256,
        source_name=filename,
        transactions=transactions,
        doc_schema=doc_schema,
        reconciliations=reconciliations,
        transfer_links=transfer_links,
        flow_used=flow_used,
    )


def process_files(
    files: list[tuple[bytes, str]],
    config: ProcessingConfig,
    taxonomy: TaxonomyConfig,
    user_rules: list[CategoryRule],
    known_schemas: Optional[dict[str, DocumentSchema]] = None,
) -> list[ImportResult]:
    """
    Process multiple files. Each (bytes, filename) tuple.
    known_schemas: mapping source_identifier → DocumentSchema.
    """
    results = []
    known_schemas = known_schemas or {}

    for raw_bytes, filename in files:
        schema = known_schemas.get(filename)
        try:
            result = process_file(
                raw_bytes=raw_bytes,
                filename=filename,
                config=config,
                taxonomy=taxonomy,
                user_rules=user_rules,
                known_schema=schema,
            )
            results.append(result)
        except Exception as exc:
            logger.error(f"process_files: unhandled error for {filename}: {exc}", exc_info=True)
            results.append(ImportResult(
                batch_sha256="",
                source_name=filename,
                transactions=[],
                doc_schema=None,
                reconciliations=[],
                transfer_links=[],
                errors=[str(exc)],
            ))

    return results
