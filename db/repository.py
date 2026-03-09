"""Idempotent CRUD operations (RF-06, RF-07).

All write operations are upsert-style to guarantee idempotency.
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from core.categorizer import CategoryRule as CoreCategoryRule
from core.schemas import DocumentSchema
from db.models import (
    CategoryRule,
    DocumentSchemaModel,
    ImportBatch,
    InternalTransferLink,
    ReconciliationLink,
    Transaction,
)
from support.logging import setup_logging

logger = setup_logging()


# ── ImportBatch ───────────────────────────────────────────────────────────────

def get_import_batch(session: Session, sha256: str) -> Optional[ImportBatch]:
    return session.query(ImportBatch).filter_by(sha256=sha256).first()


def get_all_batch_hashes(session: Session) -> set[str]:
    return {row.sha256 for row in session.query(ImportBatch.sha256).all()}


def create_import_batch(
    session: Session,
    sha256: str,
    filename: str,
    flow_used: str = "unknown",
    n_transactions: int = 0,
    errors: Optional[str] = None,
) -> ImportBatch:
    batch = ImportBatch(
        sha256=sha256,
        filename=filename,
        flow_used=flow_used,
        n_transactions=n_transactions,
        errors=errors,
    )
    session.add(batch)
    session.flush()
    return batch


# ── DocumentSchema ────────────────────────────────────────────────────────────

def get_document_schema(session: Session, source_identifier: str) -> Optional[DocumentSchema]:
    row = session.query(DocumentSchemaModel).filter_by(source_identifier=source_identifier).first()
    if row is None:
        return None
    return _row_to_schema(row)


def upsert_document_schema(session: Session, schema: DocumentSchema) -> DocumentSchemaModel:
    row = session.query(DocumentSchemaModel).filter_by(
        source_identifier=schema.source_identifier
    ).first()

    if row is None:
        row = DocumentSchemaModel(source_identifier=schema.source_identifier)
        session.add(row)

    row.doc_type = schema.doc_type.value if hasattr(schema.doc_type, 'value') else schema.doc_type
    row.date_col = schema.date_col
    row.date_accounting_col = schema.date_accounting_col
    row.amount_col = schema.amount_col
    row.debit_col = schema.debit_col
    row.credit_col = schema.credit_col
    row.description_col = schema.description_col
    row.currency_col = schema.currency_col
    row.default_currency = schema.default_currency
    row.date_format = schema.date_format
    row.sign_convention = schema.sign_convention.value if hasattr(schema.sign_convention, 'value') else schema.sign_convention
    row.is_zero_sum = schema.is_zero_sum
    row.internal_transfer_patterns = json.dumps(schema.internal_transfer_patterns)
    row.account_label = schema.account_label
    row.encoding = schema.encoding
    row.sheet_name = schema.sheet_name
    row.skip_rows = schema.skip_rows
    row.delimiter = schema.delimiter
    row.confidence = schema.confidence.value if hasattr(schema.confidence, 'value') else schema.confidence

    session.flush()
    return row


def _row_to_schema(row: DocumentSchemaModel) -> DocumentSchema:
    from core.models import DocumentType, SignConvention, Confidence
    return DocumentSchema(
        doc_type=DocumentType(row.doc_type),
        date_col=row.date_col or "",
        date_accounting_col=row.date_accounting_col,
        amount_col=row.amount_col or "",
        debit_col=row.debit_col,
        credit_col=row.credit_col,
        description_col=row.description_col,
        currency_col=row.currency_col,
        default_currency=row.default_currency or "EUR",
        date_format=row.date_format or "%d/%m/%Y",
        sign_convention=SignConvention(row.sign_convention or "signed_single"),
        is_zero_sum=bool(row.is_zero_sum),
        internal_transfer_patterns=json.loads(row.internal_transfer_patterns or "[]"),
        account_label=row.account_label or "",
        encoding=row.encoding or "utf-8",
        sheet_name=row.sheet_name,
        skip_rows=row.skip_rows or 0,
        delimiter=row.delimiter,
        confidence=Confidence(row.confidence or "low"),
        source_identifier=row.source_identifier,
    )


# ── Transaction ───────────────────────────────────────────────────────────────

def upsert_transaction(session: Session, tx: dict, batch_id: Optional[int] = None) -> Transaction:
    """Insert or skip (idempotent) based on transaction id (SHA-256[:24])."""
    existing = session.get(Transaction, tx["id"])
    if existing is not None:
        return existing  # already imported, skip

    amount = tx["amount"]
    if isinstance(amount, Decimal):
        amount_val = float(amount)
    else:
        amount_val = float(str(amount))

    row = Transaction(
        id=tx["id"],
        batch_id=batch_id,
        date=tx["date"].isoformat() if hasattr(tx["date"], "isoformat") else str(tx["date"]),
        date_accounting=tx.get("date_accounting").isoformat() if tx.get("date_accounting") and hasattr(tx["date_accounting"], "isoformat") else tx.get("date_accounting"),
        amount=amount_val,
        currency=tx.get("currency", "EUR"),
        description=tx.get("description", ""),
        source_file=tx.get("source_file", ""),
        doc_type=tx.get("doc_type", ""),
        account_label=tx.get("account_label", ""),
        tx_type=tx.get("tx_type", "unknown"),
        category=tx.get("category"),
        subcategory=tx.get("subcategory"),
        category_confidence=tx.get("category_confidence"),
        category_source=tx.get("category_source"),
        reconciled=bool(tx.get("reconciled", False)),
        to_review=bool(tx.get("to_review", False)),
        transfer_pair_id=tx.get("transfer_pair_id"),
        transfer_confidence=tx.get("transfer_confidence"),
    )
    session.add(row)
    return row


def update_transaction_category(
    session: Session,
    tx_id: str,
    category: str,
    subcategory: str,
) -> bool:
    tx = session.get(Transaction, tx_id)
    if tx is None:
        return False
    tx.category = category
    tx.subcategory = subcategory
    tx.category_confidence = "high"
    tx.category_source = "manual"
    tx.to_review = False
    return True


def get_transactions(
    session: Session,
    filters: Optional[dict] = None,
    limit: Optional[int] = None,
    offset: int = 0,
) -> list[Transaction]:
    q = session.query(Transaction)
    if filters:
        if "tx_type" in filters:
            q = q.filter(Transaction.tx_type == filters["tx_type"])
        if "category" in filters:
            q = q.filter(Transaction.category == filters["category"])
        if "date_from" in filters:
            q = q.filter(Transaction.date >= filters["date_from"])
        if "date_to" in filters:
            q = q.filter(Transaction.date <= filters["date_to"])
        if "to_review" in filters:
            q = q.filter(Transaction.to_review == filters["to_review"])
        if "account_label" in filters:
            q = q.filter(Transaction.account_label == filters["account_label"])
    q = q.order_by(Transaction.date.desc())
    if offset:
        q = q.offset(offset)
    if limit:
        q = q.limit(limit)
    return q.all()


# ── ReconciliationLink ────────────────────────────────────────────────────────

def create_reconciliation_link(
    session: Session,
    settlement_id: str,
    detail_id: str,
    delta: float = 0.0,
    method: str = "",
) -> ReconciliationLink:
    existing = (
        session.query(ReconciliationLink)
        .filter_by(settlement_id=settlement_id, detail_id=detail_id)
        .first()
    )
    if existing:
        return existing
    link = ReconciliationLink(
        settlement_id=settlement_id,
        detail_id=detail_id,
        delta=delta,
        method=method,
    )
    session.add(link)
    return link


# ── InternalTransferLink ──────────────────────────────────────────────────────

def create_transfer_link(
    session: Session,
    out_id: str,
    in_id: str,
    confidence: str,
    keyword_matched: bool,
) -> InternalTransferLink:
    existing = (
        session.query(InternalTransferLink)
        .filter_by(out_id=out_id, in_id=in_id)
        .first()
    )
    if existing:
        return existing
    link = InternalTransferLink(
        out_id=out_id,
        in_id=in_id,
        confidence=confidence,
        keyword_matched=keyword_matched,
    )
    session.add(link)
    return link


# ── CategoryRule ──────────────────────────────────────────────────────────────

def get_category_rules(session: Session) -> list[CoreCategoryRule]:
    rows = session.query(CategoryRule).order_by(CategoryRule.priority.desc()).all()
    return [
        CoreCategoryRule(
            id=row.id,
            pattern=row.pattern,
            match_type=row.match_type,
            category=row.category,
            subcategory=row.subcategory,
            doc_type=row.doc_type,
            priority=row.priority or 0,
        )
        for row in rows
    ]


def create_category_rule(
    session: Session,
    pattern: str,
    match_type: str,
    category: str,
    subcategory: Optional[str] = None,
    doc_type: Optional[str] = None,
    priority: int = 0,
) -> CategoryRule:
    rule = CategoryRule(
        pattern=pattern,
        match_type=match_type,
        category=category,
        subcategory=subcategory,
        doc_type=doc_type,
        priority=priority,
    )
    session.add(rule)
    session.flush()
    return rule


# ── Persistence of ImportResult ───────────────────────────────────────────────

def persist_import_result(session: Session, result) -> None:
    """Persist a complete ImportResult to the database."""
    from core.orchestrator import ImportResult

    if result.skipped_duplicate:
        logger.info(f"persist_import_result: skipping duplicate {result.source_name}")
        return

    batch = create_import_batch(
        session=session,
        sha256=result.batch_sha256,
        filename=result.source_name,
        flow_used=result.flow_used,
        n_transactions=len(result.transactions),
        errors="; ".join(result.errors) if result.errors else None,
    )

    if result.doc_schema:
        upsert_document_schema(session, result.doc_schema)

    tx_id_map: dict[str, Transaction] = {}
    for tx in result.transactions:
        row = upsert_transaction(session, tx, batch_id=batch.id)
        tx_id_map[tx["id"]] = row

    for rec in result.reconciliations:
        for detail_id in rec["matched_ids"]:
            create_reconciliation_link(
                session=session,
                settlement_id=rec["settlement_id"],
                detail_id=detail_id,
                delta=float(rec.get("delta", 0)),
                method=rec.get("method", ""),
            )
            # Mark settlement and detail as reconciled
            if rec["settlement_id"] in tx_id_map:
                tx_id_map[rec["settlement_id"]].reconciled = True
            if detail_id in tx_id_map:
                tx_id_map[detail_id].reconciled = True

    for link in result.transfer_links:
        create_transfer_link(
            session=session,
            out_id=link["out_id"],
            in_id=link["in_id"],
            confidence=link.get("confidence", "medium"),
            keyword_matched=bool(link.get("keyword_matched", False)),
        )

    session.commit()
    logger.info(
        f"persist_import_result: committed batch {result.batch_sha256[:8]}… "
        f"({len(result.transactions)} transactions, "
        f"{len(result.reconciliations)} reconciliations, "
        f"{len(result.transfer_links)} transfer links)"
    )
