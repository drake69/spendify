"""Main processing pipeline (RF-01 through RF-07).

Implements both Flow 1 (deterministic + known template) and
Flow 2 (LLM-first / schema-on-read).
"""
from __future__ import annotations

import io
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
from core.description_cleaner import clean_descriptions_batch
from core.classifier import classify_document
from core.llm_backends import LLMBackend, BackendFactory, OllamaBackend
from core.models import (
    Confidence,
    DocumentType,
    GirocontoMode,
    SignConvention,
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
    remove_card_balance_row,
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
    # When True: transactions whose description matches an owner name are marked
    # as internal transfer (giroconto), and the card balance row (if present) is
    # relabelled with the owner name instead of removed.
    use_owner_names_for_giroconto: bool = False
    llm_timeout_s: int = 120
    batch_size_llm: int = 1
    sanitize_config: SanitizationConfig = field(default_factory=SanitizationConfig)
    description_language: str = "it"  # language of transaction descriptions (ISO 639-1)

    # Test mode: limit rows for quick classifier verification
    test_mode: bool = False
    test_mode_rows: int = 20

    # Backend kwargs
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "gemma3:12b"
    openai_model: str = "gpt-4o-mini"
    openai_api_key: str = ""
    claude_model: str = "claude-3-5-haiku-20241022"
    anthropic_api_key: str = ""


@dataclass
class ImportResult:
    batch_sha256: str
    source_name: str
    transactions: list[dict]
    doc_schema: Optional[DocumentSchema]
    reconciliations: list[dict]
    transfer_links: list[dict]
    skipped_duplicate: bool = False
    skipped_count: int = 0   # number of transactions already in DB (skipped before LLM)
    errors: list[str] = field(default_factory=list)
    flow_used: str = "unknown"  # "flow1" or "flow2"


def _build_backend(config: ProcessingConfig) -> LLMBackend:
    kwargs = {"timeout": config.llm_timeout_s}
    if config.llm_backend == "local_ollama":
        kwargs["base_url"] = config.ollama_base_url
        kwargs["model"] = config.ollama_model
    elif config.llm_backend == "openai":
        kwargs["model"] = config.openai_model
        kwargs["api_key"] = config.openai_api_key
    elif config.llm_backend == "claude":
        kwargs["model"] = config.claude_model
        kwargs["api_key"] = config.anthropic_api_key
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
    skip_date_nan = skip_date_parse = skip_amount = 0

    for _, row in df.iterrows():
        # Parse date — Excel cells arrive as datetime/date objects, not strings
        raw_date = row.get(schema.date_col, "")
        if raw_date is None or (isinstance(raw_date, float) and pd.isna(raw_date)):
            skip_date_nan += 1
            continue
        if hasattr(raw_date, "date"):          # datetime → date
            tx_date = raw_date.date()
        elif isinstance(raw_date, date):       # already a date
            tx_date = raw_date
        elif raw_date:
            tx_date = parse_date_safe(str(raw_date), schema.date_format)
        else:
            tx_date = None
        if tx_date is None:
            skip_date_parse += 1
            continue  # skip rows with unparseable date

        # Parse accounting date
        raw_date_acc = row.get(schema.date_accounting_col, "") if schema.date_accounting_col else None
        tx_date_acc = parse_date_safe(str(raw_date_acc), schema.date_format) if raw_date_acc else None

        # Capture raw amount string(s) before parsing — used for the dedup hash
        if schema.sign_convention in (SignConvention.debit_positive, SignConvention.credit_negative):
            _d = str(row.get(schema.debit_col, "")) if schema.debit_col else ""
            _c = str(row.get(schema.credit_col, "")) if schema.credit_col else ""
            raw_amount_str = f"{_d}|{_c}"
        else:
            raw_amount_str = str(row.get(schema.amount_col, "")) if schema.amount_col else ""

        amount = apply_sign_convention(
            row.to_dict(),
            schema.amount_col,
            schema.debit_col,
            schema.credit_col,
            schema.sign_convention,
        )
        if amount is None:
            skip_amount += 1
            continue

        # Card files often store expenses as positive values.
        # invert_sign=True means "negate all amounts so expenses become negative".
        if getattr(schema, "invert_sign", False) and amount is not None:
            amount = -amount

        # Description — concatenate all descriptive columns when available
        _desc_cols = getattr(schema, "description_cols", None)
        if _desc_cols:
            parts = [
                str(row.get(c, "") or "").strip()
                for c in _desc_cols if c in row
            ]
            # Filter out empty strings and bare "nan" (pandas NaN → str)
            desc_raw = " ".join(p for p in parts if p and p.lower() != "nan")
        elif schema.description_col:
            desc_raw = str(row.get(schema.description_col, "") or "")
        else:
            desc_raw = ""
        description = normalize_description(desc_raw)

        # Currency
        currency = str(row.get(schema.currency_col, schema.default_currency)) if schema.currency_col else schema.default_currency

        # Idempotency key — hashed on PARSED/NORMALISED values so the same logical
        # transaction deduplicates across different file formats (CSV vs XLSX, Italian
        # date strings vs Excel datetime objects, comma vs dot decimals).
        # Uses account_label (stable per bank account) as the primary namespace.
        # Falls back to source_file when account_label is empty.
        _date_key = tx_date.isoformat()   # always "YYYY-MM-DD"
        _amount_key = str(amount.normalize()) if isinstance(amount, Decimal) else str(float(str(amount or 0)))
        _desc_key = desc_raw.strip()      # strip leading/trailing whitespace only
        tx_id = compute_transaction_id(
            source_name, _date_key, _amount_key, _desc_key,
            account_label=schema.account_label or "",
        )

        # Infer tx_type from doc_type
        tx_type = _infer_tx_type(amount, schema.doc_type, description, schema.internal_transfer_patterns)

        transactions.append({
            "id": tx_id,
            "date": tx_date,
            "date_accounting": tx_date_acc,
            "amount": amount,
            "raw_amount": raw_amount_str or None,
            "currency": currency,
            "description": description,
            "raw_description": desc_raw or None,
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

    total_skipped = skip_date_nan + skip_date_parse + skip_amount
    if total_skipped or not transactions:
        logger.warning(
            f"_normalize_df_with_schema [{source_name}]: "
            f"{len(transactions)} parsed, {total_skipped} skipped "
            f"(date_nan={skip_date_nan}, date_parse_fail={skip_date_parse}, amount_none={skip_amount})"
        )
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
    _card_types = {DocumentType.credit_card.value, DocumentType.debit_card.value, DocumentType.prepaid_card.value}
    if doc_str in _card_types:
        return TransactionType.card_tx
    if amount > 0:
        return TransactionType.income
    return TransactionType.expense


def _schema_is_usable(schema: "DocumentSchema") -> bool:
    """Return True if the schema has the minimum fields needed to parse transactions."""
    has_date = bool(schema.date_col)
    has_amount = bool(schema.amount_col) or (bool(schema.debit_col) and bool(schema.credit_col))
    return has_date and has_amount


def process_file(
    raw_bytes: bytes,
    filename: str,
    config: ProcessingConfig,
    taxonomy: TaxonomyConfig,
    user_rules: list[CategoryRule],
    known_schema: Optional[DocumentSchema] = None,
    progress_callback=None,  # Callable[[float], None] — 0.0..1.0 within this file
    existing_tx_ids_checker=None,  # Callable[[list[str]], set[str]] — returns already-imported tx ids
    account_label_override: Optional[str] = None,  # user-selected account name; overrides LLM-assigned label
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
    def _progress(p: float):
        if progress_callback:
            progress_callback(max(0.0, min(1.0, p)))

    batch_sha256 = compute_file_hash(raw_bytes)
    logger.info(f"process_file: loading {filename} ({len(raw_bytes)} bytes)")
    backend = _build_backend(config)
    fallback = _get_fallback_backend(config)

    # Load raw data
    _progress(0.0)
    df_raw, encoding = load_raw_dataframe(raw_bytes, filename)
    logger.info(
        f"process_file: loaded {filename} | rows={len(df_raw)} "
        f"ncols={len(df_raw.columns)} | known_schema={'yes' if known_schema else 'no'}"
    )
    _progress(0.05)

    # Test mode: truncate to first N rows for quick pipeline verification
    if config.test_mode and len(df_raw) > config.test_mode_rows:
        df_raw = df_raw.head(config.test_mode_rows).copy()
        logger.info(
            f"process_file: TEST MODE — truncated {filename} to first {config.test_mode_rows} rows"
        )

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

    # Validate cached schema has the critical fields; re-classify if not
    if doc_schema is not None and not _schema_is_usable(doc_schema):
        logger.warning(
            f"process_file: cached schema for {filename} is missing critical fields "
            f"(amount_col={doc_schema.amount_col!r}, date_col={doc_schema.date_col!r}) — re-classifying"
        )
        doc_schema = None

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
        _progress(0.25)
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
    else:
        _progress(0.10)

    # User-selected account overrides the LLM-assigned label — provides a
    # stable, human-readable dedup key independent of the filename.
    if account_label_override and account_label_override.strip():
        doc_schema.account_label = account_label_override.strip()
        logger.info(
            f"process_file: account_label overridden to '{doc_schema.account_label}' for {filename}"
        )

    # Apply schema → canonical transactions
    transactions = _normalize_df_with_schema(df_raw, doc_schema, filename)
    _progress(0.35)

    # Case 5: remove within-file card balance/totale summary row (double-counting guard)
    _card_doc_types = {DocumentType.credit_card.value, DocumentType.debit_card.value, DocumentType.prepaid_card.value}
    _doc_str = doc_schema.doc_type.value if hasattr(doc_schema.doc_type, 'value') else str(doc_schema.doc_type)
    if _doc_str in _card_doc_types and transactions:
        _owner_label: str | None = None
        if config.use_owner_names_for_giroconto and config.sanitize_config.owner_names:
            _owner_label = ", ".join(config.sanitize_config.owner_names)
        transactions, _balance_removed = remove_card_balance_row(
            transactions, epsilon=config.tolerance, owner_name_label=_owner_label
        )
        if _balance_removed:
            action = "relabelled" if _owner_label else "removed"
            logger.info(f"process_file: balance/totale row {action} from {filename}")

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

    # ── Description cleaning: extract counterpart name (pre-categorization) ──
    # Sends descriptions to LLM to strip payment-method boilerplate and keep
    # only the merchant / beneficiary / payer name.
    # raw_description is never modified — it stays as the SHA-256 dedup key.
    transactions = clean_descriptions_batch(
        transactions,
        llm_backend=backend,
        fallback_backend=fallback,
        source_name=filename,
        sanitize_config=config.sanitize_config,
    )
    _progress(0.38)

    # Per-transaction dedup: skip transactions already in DB before running LLM
    skipped_count = 0
    if existing_tx_ids_checker is not None:
        all_ids = [t["id"] for t in transactions]
        existing = existing_tx_ids_checker(all_ids)
        if existing:
            skipped_count = len(existing)
            transactions = [t for t in transactions if t["id"] not in existing]
            logger.info(
                f"process_file: {skipped_count} transactions already in DB, "
                f"{len(transactions)} new for {filename}"
            )
        if not transactions:
            logger.info(f"process_file: all transactions already imported for {filename}, skipping")
            return ImportResult(
                batch_sha256=batch_sha256,
                source_name=filename,
                transactions=[],
                doc_schema=doc_schema,
                reconciliations=[],
                transfer_links=[],
                skipped_count=skipped_count,
                flow_used=flow_used,
            )

    # Build DataFrame for transfer detection
    tx_df = pd.DataFrame(transactions)

    # Internal transfer detection (RF-04)
    keyword_patterns = doc_schema.internal_transfer_patterns or []
    _owner_names_giroconto = (
        config.sanitize_config.owner_names
        if config.use_owner_names_for_giroconto
        else None
    )
    tx_df = detect_internal_transfers(
        tx_df,
        epsilon=config.tolerance,
        delta_days=config.settlement_days,
        epsilon_strict=config.tolerance_strict,
        delta_days_strict=config.settlement_days_strict,
        keyword_patterns=keyword_patterns,
        require_keyword_confirmation=config.require_keyword_confirmation,
        owner_names=_owner_names_giroconto,
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

    def _cat_cb(frac: float):
        # Map categorization progress (0..1) → file progress (0.40..1.0)
        _progress(0.40 + 0.60 * frac)

    cat_results = categorize_batch(
        transactions=to_categorize,
        taxonomy=taxonomy,
        user_rules=user_rules,
        llm_backend=backend,
        sanitize_config=config.sanitize_config,
        fallback_backend=fallback,
        confidence_threshold=config.confidence_threshold,
        description_language=config.description_language,
        progress_callback=_cat_cb,
        source_name=filename,
    )
    cat_map = {tx["id"]: result for tx, result in zip(to_categorize, cat_results)}

    # Build tx_type / transfer map from tx_df so that changes made by
    # detect_internal_transfers (owner-name pass, keyword pass) are propagated
    # back to the original transactions list before DB persistence.
    tx_df_map = tx_df.set_index("id")[
        ["tx_type", "transfer_pair_id", "transfer_confidence"]
    ].to_dict("index")

    # Merge categorization + tx_type back
    for tx in transactions:
        # tx_type and transfer fields from detect_internal_transfers
        df_row = tx_df_map.get(tx["id"])
        if df_row:
            tx["tx_type"] = df_row["tx_type"]
            tx["transfer_pair_id"] = df_row["transfer_pair_id"]
            tx["transfer_confidence"] = df_row["transfer_confidence"]

        # Categorization (only for categorizable types)
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
        skipped_count=skipped_count,
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
