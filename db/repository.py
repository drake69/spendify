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
    DEFAULT_USER_SETTINGS,
    DescriptionRule,
    DocumentSchemaModel,
    ImportBatch,
    ImportJob,
    InternalTransferLink,
    ReconciliationLink,
    Transaction,
    UserSettings,
)
from support.logging import setup_logging

logger = setup_logging()

# Sentinel per distinguere "non passato" da None in update_category_rule.context
_SENTINEL = object()


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
    existing = session.query(ImportBatch).filter_by(sha256=sha256).first()
    if existing:
        return existing
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

def get_all_transfer_keyword_patterns(session: Session) -> list[str]:
    """Return the union of all internal_transfer_patterns across every known schema."""
    rows = session.query(DocumentSchemaModel).all()
    patterns: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for p in json.loads(row.internal_transfer_patterns or "[]"):
            if p and p not in seen:
                patterns.append(p)
                seen.add(p)
    return patterns


def get_document_schema(session: Session, source_identifier: str) -> Optional[DocumentSchema]:
    row = session.query(DocumentSchemaModel).filter_by(source_identifier=source_identifier).first()
    if row is None:
        return None
    return _row_to_schema(row)


def find_schema_by_header_sha256(session: Session, header_sha256: str) -> Optional[DocumentSchema]:
    """Look up a saved schema by the SHA256 of the file's first rows.
    Returns the most recently updated schema if multiple match (shouldn't happen in practice)."""
    row = (
        session.query(DocumentSchemaModel)
        .filter_by(header_sha256=header_sha256)
        .order_by(DocumentSchemaModel.updated_at.desc().nullslast())
        .first()
    )
    if row is None:
        return None
    return _row_to_schema(row)


def delete_all_schemas(session: Session) -> int:
    """Delete all cached document schemas. Returns the number of rows deleted."""
    count = session.query(DocumentSchemaModel).delete()
    session.flush()
    return count


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
    row.description_cols = json.dumps(schema.description_cols) if schema.description_cols else "[]"
    row.currency_col = schema.currency_col
    row.default_currency = schema.default_currency
    row.date_format = schema.date_format
    row.sign_convention = schema.sign_convention.value if hasattr(schema.sign_convention, 'value') else schema.sign_convention
    row.is_zero_sum = schema.is_zero_sum
    row.invert_sign = schema.invert_sign
    row.internal_transfer_patterns = json.dumps(schema.internal_transfer_patterns)
    row.account_label = schema.account_label
    row.encoding = schema.encoding
    row.sheet_name = schema.sheet_name
    row.skip_rows = schema.skip_rows
    row.delimiter = schema.delimiter
    row.confidence = schema.confidence.value if hasattr(schema.confidence, 'value') else schema.confidence
    row.confidence_score = getattr(schema, 'confidence_score', None)
    if schema.header_sha256:
        row.header_sha256 = schema.header_sha256

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
        description_cols=json.loads(getattr(row, "description_cols", None) or "[]"),
        currency_col=row.currency_col,
        default_currency=row.default_currency or "EUR",
        date_format=row.date_format or "%d/%m/%Y",
        sign_convention=SignConvention(row.sign_convention or "signed_single"),
        is_zero_sum=bool(row.is_zero_sum),
        invert_sign=bool(row.invert_sign) if row.invert_sign is not None else False,
        internal_transfer_patterns=json.loads(row.internal_transfer_patterns or "[]"),
        account_label=row.account_label or "",
        encoding=row.encoding or "utf-8",
        sheet_name=row.sheet_name,
        skip_rows=row.skip_rows or 0,
        delimiter=row.delimiter,
        confidence=Confidence(row.confidence or "low"),
        confidence_score=getattr(row, 'confidence_score', None) or 0.0,
        source_identifier=row.source_identifier,
        header_sha256=getattr(row, 'header_sha256', None),
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
        raw_description=tx.get("raw_description"),
        raw_amount=tx.get("raw_amount"),
        human_validated=bool(tx.get("human_validated", False)),
    )
    session.add(row)
    return row


def get_existing_tx_ids(session: Session, tx_ids: list[str]) -> set[str]:
    """Return the subset of tx_ids that already exist in the DB."""
    if not tx_ids:
        return set()
    rows = session.query(Transaction.id).filter(Transaction.id.in_(tx_ids)).all()
    return {row.id for row in rows}


def update_transaction_category(
    session: Session,
    tx_id: str,
    category: str,
    subcategory: str,
) -> bool:
    from datetime import datetime, timezone
    tx = session.get(Transaction, tx_id)
    if tx is None:
        return False
    tx.category = category
    tx.subcategory = subcategory
    tx.category_confidence = "high"
    tx.category_source = "manual"
    tx.to_review = False
    tx.human_validated = True
    tx.validated_at = datetime.now(timezone.utc)
    return True


def toggle_transaction_giroconto(session: Session, tx_id: str) -> tuple[bool, str]:
    """Toggle a transaction's tx_type between giroconto and expense/income.

    If currently internal_out / internal_in → revert to expense or income based on sign.
    Otherwise → mark as internal_out (negative amount) or internal_in (positive amount).

    Returns (ok, new_tx_type).
    """
    tx = session.get(Transaction, tx_id)
    if tx is None:
        return False, ""
    internal = {"internal_out", "internal_in"}
    if tx.tx_type in internal:
        # Revert to normal
        new_type = "income" if float(tx.amount or 0) >= 0 else "expense"
    else:
        # Mark as giroconto
        new_type = "internal_in" if float(tx.amount or 0) >= 0 else "internal_out"
    tx.tx_type = new_type
    return True, new_type


def update_transaction_context(session: Session, tx_id: str, context: str | None) -> bool:
    """Set or clear the context of a transaction. Returns True if found."""
    from datetime import datetime, timezone
    tx = session.get(Transaction, tx_id)
    if tx is None:
        return False
    tx.context = context or None
    tx.human_validated = True
    tx.validated_at = datetime.now(timezone.utc)
    session.flush()
    return True


def get_similar_transactions(
    session: Session, description: str, exclude_id: str = "", threshold: float = 0.35
) -> list[Transaction]:
    """Return transactions whose description is similar to the given one.

    Uses Jaccard similarity on word tokens (case-insensitive).
    Only transactions with a non-empty description are considered.
    """
    if not description:
        return []
    ref_tokens = set(description.lower().split())
    if not ref_tokens:
        return []
    txs = session.query(Transaction).filter(
        Transaction.description.isnot(None),
        Transaction.description != "",
        Transaction.id != exclude_id,
    ).all()
    result = []
    for tx in txs:
        tokens = set((tx.description or "").lower().split())
        if not tokens:
            continue
        similarity = len(ref_tokens & tokens) / len(ref_tokens | tokens)
        if similarity >= threshold:
            result.append(tx)
    return result


def bulk_set_giroconto_by_description(
    session: Session, description: str, make_giroconto: bool, exclude_id: str = ""
) -> int:
    """Set giroconto status for all transactions matching description.

    make_giroconto=True  → internal_in / internal_out based on sign
    make_giroconto=False → income / expense based on sign

    Returns count of transactions actually changed.
    """
    txs = session.query(Transaction).filter(Transaction.description == description).all()
    internal = {"internal_out", "internal_in"}
    updated = 0
    for tx in txs:
        if tx.id == exclude_id:
            continue
        is_internal = tx.tx_type in internal
        if make_giroconto and not is_internal:
            tx.tx_type = "internal_in" if float(tx.amount or 0) >= 0 else "internal_out"
            updated += 1
        elif not make_giroconto and is_internal:
            tx.tx_type = "income" if float(tx.amount or 0) >= 0 else "expense"
            updated += 1
    if updated:
        session.flush()
    return updated


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
        if "description" in filters:
            _term = f"%{filters['description']}%"
            from sqlalchemy import or_
            q = q.filter(
                or_(
                    Transaction.description.ilike(_term),
                    Transaction.raw_description.ilike(_term),
                )
            )
        if "subcategory" in filters:
            q = q.filter(Transaction.subcategory == filters["subcategory"])
        if "context" in filters:
            if filters["context"] is None:
                q = q.filter(Transaction.context.is_(None))
            else:
                q = q.filter(Transaction.context == filters["context"])
        if "exclude_tx_types" in filters:
            q = q.filter(Transaction.tx_type.notin_(filters["exclude_tx_types"]))
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
            context=row.context or None,
            doc_type=row.doc_type,
            priority=row.priority or 0,
        )
        for row in rows
    ]


def apply_rules_to_review_transactions(
    session: Session,
    user_rules: list[CoreCategoryRule],
) -> int:
    """Apply category rules (highest priority first) to all to_review=True transactions.

    For each transaction, the first matching rule wins.
    Matched transactions get their category/subcategory updated and to_review cleared.

    Returns the number of transactions updated.
    """
    if not user_rules:
        return 0

    txs = session.query(Transaction).filter(Transaction.to_review.is_(True)).all()
    if not txs:
        return 0

    rules = sorted(user_rules, key=lambda r: -(r.priority or 0))
    updated = 0
    for tx in txs:
        desc = tx.description or ""
        for rule in rules:
            if rule.matches(desc, tx.doc_type):
                tx.category = rule.category
                tx.subcategory = rule.subcategory
                if rule.context:
                    tx.context = rule.context
                tx.category_source = "rule"
                tx.category_confidence = "high"
                tx.to_review = False
                # human_validated NOT reset: means "user saw this tx", not "user approves category"
                updated += 1
                break
    if updated:
        session.flush()
    return updated


def apply_all_rules_to_all_transactions(
    session: Session,
    user_rules: list[CoreCategoryRule],
) -> tuple[int, int]:
    """Apply category rules to ALL transactions (not just to_review).

    Rules are evaluated in descending priority order; first match wins.
    Transactions with no matching rule are left unchanged.

    Returns (n_matched, n_cleared_review) where:
      - n_matched        = transactions whose category was set/changed by a rule
      - n_cleared_review = subset of those that also had to_review cleared
    """
    if not user_rules:
        return 0, 0

    rules = sorted(user_rules, key=lambda r: -(r.priority or 0))
    txs = session.query(Transaction).all()

    n_matched = 0
    n_cleared = 0
    for tx in txs:
        desc = tx.description or ""
        for rule in rules:
            if rule.matches(desc, tx.doc_type):
                tx.category            = rule.category
                tx.subcategory         = rule.subcategory
                if rule.context:
                    tx.context = rule.context
                tx.category_source     = "rule"
                tx.category_confidence = "high"
                # human_validated NOT reset: means "user saw this tx", not "user approves category"
                if tx.to_review:
                    tx.to_review = False
                    n_cleared += 1
                n_matched += 1
                break

    if n_matched:
        session.flush()
    return n_matched, n_cleared


def create_category_rule(
    session: Session,
    pattern: str,
    match_type: str,
    category: str,
    subcategory: Optional[str] = None,
    context: Optional[str] = None,
    doc_type: Optional[str] = None,
    priority: int = 0,
) -> tuple[CategoryRule, bool]:
    """Create or update a category rule.

    If a rule with the same pattern + match_type already exists it is updated
    in-place (upsert) to avoid duplicates.

    Returns (rule, created) where created=False means an existing rule was updated.
    """
    existing = (
        session.query(CategoryRule)
        .filter(CategoryRule.pattern == pattern, CategoryRule.match_type == match_type)
        .first()
    )
    if existing is not None:
        existing.category = category
        existing.subcategory = subcategory
        existing.context = context or None
        existing.priority = priority
        session.flush()
        return existing, False

    rule = CategoryRule(
        pattern=pattern,
        match_type=match_type,
        category=category,
        subcategory=subcategory,
        context=context or None,
        doc_type=doc_type,
        priority=priority,
    )
    session.add(rule)
    session.flush()
    return rule, True


def update_category_rule(
    session: Session,
    rule_id: int,
    pattern: Optional[str] = None,
    match_type: Optional[str] = None,
    category: Optional[str] = None,
    subcategory: Optional[str] = None,
    context: Optional[str] = _SENTINEL,   # type: ignore[assignment]
    priority: Optional[int] = None,
) -> bool:
    rule = session.get(CategoryRule, rule_id)
    if rule is None:
        return False
    if pattern is not None:
        rule.pattern = pattern
    if match_type is not None:
        rule.match_type = match_type
    if category is not None:
        rule.category = category
    if subcategory is not None:
        rule.subcategory = subcategory
    if context is not _SENTINEL:
        # None means "clear context"; any string sets it
        rule.context = context or None
    if priority is not None:
        rule.priority = priority
    session.flush()
    return True


def delete_category_rule(session: Session, rule_id: int) -> bool:
    rule = session.get(CategoryRule, rule_id)
    if rule is None:
        return False
    session.delete(rule)
    session.flush()
    return True


def get_transactions_by_rule_pattern(
    session: Session,
    pattern: str,
    match_type: str,
) -> list[Transaction]:
    """Return transactions whose description matches the rule pattern."""
    import re
    txs = session.query(Transaction).all()
    result = []
    pat_lower = pattern.lower()
    for tx in txs:
        desc = (tx.description or "").lower()
        if match_type == "exact" and desc == pat_lower:
            result.append(tx)
        elif match_type == "contains" and pat_lower in desc:
            result.append(tx)
        elif match_type == "regex":
            try:
                if re.search(pattern, tx.description or "", re.IGNORECASE):
                    result.append(tx)
            except re.error:
                pass
    return result


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


# ── ImportJob ─────────────────────────────────────────────────────────────────

def reset_stale_jobs(session: Session) -> int:
    """Mark any 'running' ImportJob as 'error' (interrupted by app restart).

    Call once at app startup so stale jobs from a previous process do not block
    the upload page or show a phantom progress bar.
    Returns the number of jobs reset.
    """
    from datetime import datetime, timezone
    stale = session.query(ImportJob).filter(ImportJob.status == "running").all()
    for job in stale:
        job.status = "error"
        job.status_message = "⚠️ Importazione interrotta (app riavviata)"
        job.completed_at = datetime.now(timezone.utc)
    session.commit()
    return len(stale)


def create_import_job(session: Session, n_files: int) -> ImportJob:
    from datetime import datetime, timezone
    job = ImportJob(status="running", progress=0.0, n_files=n_files,
                    started_at=datetime.now(timezone.utc))
    session.add(job)
    session.flush()
    session.commit()  # commit immediately so other sessions/threads can see it
    return job


def update_import_job(session: Session, job_id: int, **kwargs) -> None:
    job = session.get(ImportJob, job_id)
    if job is None:
        return
    for k, v in kwargs.items():
        setattr(job, k, v)
    session.flush()
    session.commit()


def get_latest_import_job(session: Session) -> Optional[ImportJob]:
    return session.query(ImportJob).order_by(ImportJob.id.desc()).first()


# ── UserSettings ──────────────────────────────────────────────────────────────

def get_user_setting(session: Session, key: str, default: Optional[str] = None) -> Optional[str]:
    row = session.get(UserSettings, key)
    if row is None:
        return default or DEFAULT_USER_SETTINGS.get(key)
    return row.value


def set_user_setting(session: Session, key: str, value: str) -> None:
    row = session.get(UserSettings, key)
    if row is None:
        row = UserSettings(key=key, value=value)
        session.add(row)
    else:
        row.value = value
    session.flush()


def get_all_user_settings(session: Session) -> dict[str, str]:
    rows = session.query(UserSettings).all()
    result = dict(DEFAULT_USER_SETTINGS)  # start with defaults
    result.update({r.key: r.value for r in rows if r.value is not None})
    return result


# ── Taxonomy ───────────────────────────────────────────────────────────────────

def get_taxonomy_config(session: Session):
    """Build a TaxonomyConfig from the DB taxonomy tables."""
    from core.categorizer import TaxonomyConfig
    from db.models import TaxonomyCategory

    cats = (
        session.query(TaxonomyCategory)
        .order_by(TaxonomyCategory.type, TaxonomyCategory.sort_order)
        .all()
    )
    expenses: dict[str, list[str]] = {}
    income: dict[str, list[str]] = {}
    for cat in cats:
        subs = [s.name for s in sorted(cat.subcategories, key=lambda x: x.sort_order)]
        if cat.type == "expense":
            expenses[cat.name] = subs
        else:
            income[cat.name] = subs

    # Ensure fallback categories always exist
    if not expenses:
        expenses = {"Altro": ["Spese non classificate"]}
    if not income:
        income = {"Altro entrate": ["Entrate non classificate"]}
    return TaxonomyConfig(expenses=expenses, income=income)


def get_taxonomy_categories(session: Session, type_filter: Optional[str] = None):
    """Return TaxonomyCategory rows, optionally filtered by type ('expense'/'income').

    Subcategories are eagerly loaded so they remain accessible after the session closes.
    """
    from db.models import TaxonomyCategory
    from sqlalchemy.orm import joinedload
    q = session.query(TaxonomyCategory).options(joinedload(TaxonomyCategory.subcategories))
    if type_filter:
        q = q.filter_by(type=type_filter)
    return q.order_by(TaxonomyCategory.sort_order).all()


def create_taxonomy_category(session: Session, name: str, type_: str) -> "TaxonomyCategory":
    from db.models import TaxonomyCategory
    from sqlalchemy import func
    max_order = session.query(func.max(TaxonomyCategory.sort_order)).filter_by(type=type_).scalar() or 0
    cat = TaxonomyCategory(name=name.strip(), type=type_, sort_order=max_order + 1)
    session.add(cat)
    session.flush()
    return cat


def update_taxonomy_category(session: Session, cat_id: int, name: str) -> bool:
    from db.models import TaxonomyCategory
    cat = session.get(TaxonomyCategory, cat_id)
    if cat is None:
        return False
    cat.name = name.strip()
    session.flush()
    return True


def delete_taxonomy_category(session: Session, cat_id: int) -> bool:
    from db.models import TaxonomyCategory
    cat = session.get(TaxonomyCategory, cat_id)
    if cat is None:
        return False
    session.delete(cat)
    session.flush()
    return True


def create_taxonomy_subcategory(session: Session, cat_id: int, name: str) -> "TaxonomySubcategory":
    from db.models import TaxonomySubcategory
    from sqlalchemy import func
    max_order = session.query(func.max(TaxonomySubcategory.sort_order)).filter_by(category_id=cat_id).scalar() or 0
    sub = TaxonomySubcategory(category_id=cat_id, name=name.strip(), sort_order=max_order + 1)
    session.add(sub)
    session.flush()
    return sub


def update_taxonomy_subcategory(session: Session, sub_id: int, name: str) -> bool:
    from db.models import TaxonomySubcategory
    sub = session.get(TaxonomySubcategory, sub_id)
    if sub is None:
        return False
    sub.name = name.strip()
    session.flush()
    return True


def delete_taxonomy_subcategory(session: Session, sub_id: int) -> bool:
    from db.models import TaxonomySubcategory
    sub = session.get(TaxonomySubcategory, sub_id)
    if sub is None:
        return False
    session.delete(sub)
    session.flush()
    return True


# ── Classification tracking ──────────────────────────────────────────────────

def validate_transaction(session: Session, tx_id: str) -> bool:
    """Mark a transaction as human-validated and clear to_review flag."""
    from datetime import datetime, timezone
    tx = session.get(Transaction, tx_id)
    if tx is None:
        return False
    tx.human_validated = True
    tx.validated_at = datetime.now(timezone.utc)
    tx.to_review = False
    session.flush()
    return True


def unvalidate_transaction(session: Session, tx_id: str) -> bool:
    """Remove human-validated flag from a transaction."""
    tx = session.get(Transaction, tx_id)
    if tx is None:
        return False
    tx.human_validated = False
    tx.validated_at = None
    session.flush()
    return True


def get_fallback_categories(session: Session) -> dict[str, tuple[str, str]]:
    """Return fallback category names for expense and income, read from taxonomy.

    Returns dict like {"expense": ("Altro", "Spese non classificate"), "income": ("Altro entrate", "Entrate non classificate")}
    """
    from db.models import TaxonomyCategory, TaxonomySubcategory
    fallbacks: dict[str, tuple[str, str]] = {}
    for cat in session.query(TaxonomyCategory).filter(TaxonomyCategory.is_fallback == True).all():  # noqa: E712
        subs = session.query(TaxonomySubcategory).filter(TaxonomySubcategory.category_id == cat.id).first()
        sub_name = subs.name if subs else ""
        fallbacks[cat.type] = (cat.name, sub_name)
    # Hardcoded fallback if DB has no fallback categories (fresh install before seed)
    if "expense" not in fallbacks:
        fallbacks["expense"] = ("Altro", "Spese non classificate")
    if "income" not in fallbacks:
        fallbacks["income"] = ("Altro entrate", "Entrate non classificate")
    return fallbacks


# ── Account CRUD ──────────────────────────────────────────────────────────────

def get_accounts(session: Session) -> list:
    from db.models import Account
    return session.query(Account).order_by(Account.name).all()


def create_account(session: Session, name: str, bank_name: str = "", account_type: str | None = None) -> object:
    from db.models import Account
    from sqlalchemy.exc import IntegrityError
    acc = Account(
        name=name.strip(),
        bank_name=bank_name.strip() or None,
        account_type=account_type,
    )
    session.add(acc)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise ValueError(f"Conto '{name}' già esistente")
    return acc


def delete_account(session: Session, account_id: int) -> bool:
    from db.models import Account
    acc = session.get(Account, account_id)
    if acc is None:
        return False
    session.delete(acc)
    session.flush()
    return True


def rename_account(
    session: Session,
    account_id: int,
    new_name: str,
    new_bank_name: str | None = None,
    new_account_type: str | None = _SENTINEL,
) -> int:
    """Rename an account, recalculate all transaction IDs, and cascade to related tables.

    When the account_label changes, every transaction ID must be recomputed because
    the ID hash includes the account_label.  All FK references in reconciliation_link
    and internal_transfer_link are updated atomically.

    Returns the number of transactions updated, or -1 if account not found.
    Raises ValueError if the new name collides with an existing account.
    """
    from db.models import Account, InternalTransferLink, ReconciliationLink, Transaction
    from sqlalchemy.exc import IntegrityError
    from core.normalizer import compute_transaction_id

    acc = session.get(Account, account_id)
    if acc is None:
        return -1
    old_name = acc.name
    stripped_new = new_name.strip()
    name_changed = old_name != stripped_new
    if not name_changed and new_bank_name is None and new_account_type is _SENTINEL:
        return 0  # nothing to do
    acc.name = stripped_new
    if new_bank_name is not None:
        acc.bank_name = new_bank_name.strip() or None
    if new_account_type is not _SENTINEL:
        acc.account_type = new_account_type
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise ValueError(f"Conto '{new_name}' già esistente")

    if not name_changed:
        # Only bank_name changed — no tx_id recalculation needed
        session.flush()
        return 0

    # Fetch all transactions for the old account_label
    txs = (
        session.query(Transaction)
        .filter(Transaction.account_label == old_name)
        .all()
    )
    if not txs:
        # Update document schemas even if no transactions exist
        _update_schemas_account_label(session, old_name, acc.name)
        session.flush()
        return 0

    # Snapshot the fields needed for id recomputation before any mutations
    tx_data = []
    for tx in txs:
        tx_data.append({
            "old_id": tx.id,
            "source_file": tx.source_file or "",
            "date": tx.date.isoformat() if hasattr(tx.date, 'isoformat') else str(tx.date or ""),
            "amount_key": str(Decimal(str(tx.amount or 0)).normalize()),
            "desc_key": (tx.raw_description or tx.description or "").strip(),
        })

    # Build old_id → new_id mapping
    id_mapping: dict[str, str] = {}
    for td in tx_data:
        new_id = compute_transaction_id(
            td["source_file"], td["date"], td["amount_key"], td["desc_key"],
            account_label=acc.name,
        )
        id_mapping[td["old_id"]] = new_id

    # Evict all affected Transaction objects from the identity map so raw SQL
    # updates don't conflict with stale ORM state.
    for tx in txs:
        session.expunge(tx)

    # Update FK references in related tables BEFORE changing PKs
    from sqlalchemy import text as _text
    for old_id, new_id in id_mapping.items():
        if old_id == new_id:
            continue
        session.execute(
            _text('UPDATE reconciliation_link SET settlement_id = :new WHERE settlement_id = :old'),
            {"new": new_id, "old": old_id},
        )
        session.execute(
            _text('UPDATE reconciliation_link SET detail_id = :new WHERE detail_id = :old'),
            {"new": new_id, "old": old_id},
        )
        session.execute(
            _text('UPDATE internal_transfer_link SET out_id = :new WHERE out_id = :old'),
            {"new": new_id, "old": old_id},
        )
        session.execute(
            _text('UPDATE internal_transfer_link SET in_id = :new WHERE in_id = :old'),
            {"new": new_id, "old": old_id},
        )

    # Update transaction PKs and account_label using raw SQL (can't update PK via ORM)
    for old_id, new_id in id_mapping.items():
        session.execute(
            _text('UPDATE "transaction" SET id = :new_id, account_label = :label WHERE id = :old_id'),
            {"new_id": new_id, "old_id": old_id, "label": acc.name},
        )

    # Update document schemas that reference the old account_label
    _update_schemas_account_label(session, old_name, acc.name)

    session.flush()
    return len(tx_data)


def _update_schemas_account_label(session: Session, old_label: str, new_label: str) -> int:
    """Update all DocumentSchemaModel rows that reference the old account_label."""
    return (
        session.query(DocumentSchemaModel)
        .filter(DocumentSchemaModel.account_label == old_label)
        .update({DocumentSchemaModel.account_label: new_label})
    )


# ── DescriptionRule ───────────────────────────────────────────────────────────

def get_description_rules(session: Session) -> list[DescriptionRule]:
    return session.query(DescriptionRule).order_by(DescriptionRule.id).all()


def create_description_rule(
    session: Session,
    raw_pattern: str,
    match_type: str,
    cleaned_description: str,
) -> tuple[DescriptionRule, bool]:
    """Upsert a description rule on (raw_pattern, match_type).

    Returns (rule, created) where created=False means an existing rule was updated.
    """
    existing = (
        session.query(DescriptionRule)
        .filter(
            DescriptionRule.raw_pattern == raw_pattern,
            DescriptionRule.match_type == match_type,
        )
        .first()
    )
    if existing is not None:
        existing.cleaned_description = cleaned_description
        session.flush()
        return existing, False

    rule = DescriptionRule(
        raw_pattern=raw_pattern,
        match_type=match_type,
        cleaned_description=cleaned_description,
    )
    session.add(rule)
    session.flush()
    return rule, True


def delete_description_rule(session: Session, rule_id: int) -> bool:
    rule = session.get(DescriptionRule, rule_id)
    if rule is None:
        return False
    session.delete(rule)
    session.flush()
    return True


def delete_transactions_by_filter(
    session: Session,
    filters: dict,
) -> int:
    """Delete transactions matching *filters* and return the count of deleted rows.

    Uses the same filter keys as ``get_transactions()``.
    Cascades: also removes linked ReconciliationLink and InternalTransferLink rows
    to avoid dangling foreign-key references.
    """
    from sqlalchemy import or_

    q = session.query(Transaction)
    if "tx_type" in filters:
        q = q.filter(Transaction.tx_type == filters["tx_type"])
    if "category" in filters:
        q = q.filter(Transaction.category == filters["category"])
    if "date_from" in filters:
        q = q.filter(Transaction.date >= filters["date_from"])
    if "date_to" in filters:
        q = q.filter(Transaction.date <= filters["date_to"])
    if "account_label" in filters:
        q = q.filter(Transaction.account_label == filters["account_label"])
    if "description" in filters:
        _term = f"%{filters['description']}%"
        q = q.filter(
            or_(
                Transaction.description.ilike(_term),
                Transaction.raw_description.ilike(_term),
            )
        )
    if "subcategory" in filters:
        q = q.filter(Transaction.subcategory == filters["subcategory"])
    if "context" in filters:
        q = q.filter(Transaction.context == filters["context"])
    if "exclude_tx_types" in filters:
        q = q.filter(Transaction.tx_type.notin_(filters["exclude_tx_types"]))

    txs = q.all()
    if not txs:
        return 0

    ids = [tx.id for tx in txs]

    # Remove cascade links first (avoid FK violations)
    session.query(ReconciliationLink).filter(
        (ReconciliationLink.settlement_id.in_(ids)) |
        (ReconciliationLink.detail_id.in_(ids))
    ).delete(synchronize_session=False)
    session.query(InternalTransferLink).filter(
        (InternalTransferLink.out_id.in_(ids)) |
        (InternalTransferLink.in_id.in_(ids))
    ).delete(synchronize_session=False)

    for tx in txs:
        session.delete(tx)

    return len(txs)


def get_cross_account_duplicates(session: Session) -> list[list[Transaction]]:
    """Return groups of transactions that share (date, raw_description, amount)
    but belong to different account_labels.

    Each element of the returned list is a group of ≥ 2 transactions that are
    likely the same real-world movement imported from multiple bank exports.
    Groups are sorted by date descending.
    """
    from sqlalchemy import func, tuple_

    # Find (date, raw_description, amount) keys that appear in > 1 account
    subq = (
        session.query(
            Transaction.date,
            Transaction.raw_description,
            Transaction.amount,
        )
        .filter(Transaction.raw_description.isnot(None))
        .group_by(Transaction.date, Transaction.raw_description, Transaction.amount)
        .having(func.count(Transaction.account_label.distinct()) > 1)
        .subquery()
    )

    # Fetch all transactions matching those keys
    duplicates = (
        session.query(Transaction)
        .filter(
            tuple_(Transaction.date, Transaction.raw_description, Transaction.amount)
            .in_(session.query(subq.c.date, subq.c.raw_description, subq.c.amount))
        )
        .order_by(Transaction.date.desc(), Transaction.raw_description, Transaction.amount)
        .all()
    )

    # Group by key
    from collections import defaultdict
    groups: dict[tuple, list[Transaction]] = defaultdict(list)
    for tx in duplicates:
        groups[(tx.date, tx.raw_description, str(tx.amount))].append(tx)

    return [g for g in groups.values() if len(g) >= 2]


def get_transactions_by_raw_pattern(
    session: Session,
    raw_pattern: str,
    match_type: str,
) -> list[Transaction]:
    """Return transactions whose raw_description matches the given pattern."""
    import re
    txs = session.query(Transaction).filter(
        Transaction.raw_description.isnot(None),
    ).all()
    result = []
    pat_lower = raw_pattern.lower()
    for tx in txs:
        raw = (tx.raw_description or "").lower()
        if match_type == "exact" and raw == pat_lower:
            result.append(tx)
        elif match_type == "contains" and pat_lower in raw:
            result.append(tx)
        elif match_type == "regex":
            try:
                if re.search(raw_pattern, tx.raw_description or "", re.IGNORECASE):
                    result.append(tx)
            except re.error:
                pass
    return result


# ── TaxonomyDefault ───────────────────────────────────────────────────────────

def get_default_taxonomy_languages(session: Session) -> list[str]:
    """Return the list of language codes present in taxonomy_default, in insertion order."""
    from sqlalchemy import text as _text
    rows = session.execute(
        _text("SELECT language FROM taxonomy_default GROUP BY language ORDER BY MIN(id)")
    ).fetchall()
    return [r[0] for r in rows]


def seed_user_taxonomy_from_default(session: Session, language: str) -> int:
    """Replace the user taxonomy with the built-in template for *language*.

    Clears taxonomy_category (cascades to taxonomy_subcategory) then copies
    rows from taxonomy_default.  Returns the number of categories inserted.
    """
    from sqlalchemy import text as _text
    from db.models import TaxonomyCategory, TaxonomySubcategory

    # Clear existing user taxonomy
    session.query(TaxonomySubcategory).delete(synchronize_session=False)
    session.query(TaxonomyCategory).delete(synchronize_session=False)
    session.flush()

    rows = session.execute(_text(
        'SELECT type, category, subcategory, sort_order_cat, sort_order_sub '
        'FROM taxonomy_default WHERE language = :lang AND subcategory IS NOT NULL '
        'ORDER BY sort_order_cat, sort_order_sub'
    ), {"lang": language}).fetchall()

    if not rows:
        # Language not found — fall back to 'it'
        rows = session.execute(_text(
            'SELECT type, category, subcategory, sort_order_cat, sort_order_sub '
            'FROM taxonomy_default WHERE language = :lang AND subcategory IS NOT NULL '
            'ORDER BY sort_order_cat, sort_order_sub'
        ), {"lang": "it"}).fetchall()

    cat_map: dict[tuple[str, str], TaxonomyCategory] = {}
    for row in rows:
        type_key, cat_name, sub_name, sort_cat, sort_sub = row
        key = (type_key, cat_name)
        if key not in cat_map:
            cat = TaxonomyCategory(name=cat_name, type=type_key, sort_order=sort_cat)
            session.add(cat)
            session.flush()  # get cat.id
            cat_map[key] = cat
        sub = TaxonomySubcategory(
            category_id=cat_map[key].id,
            name=sub_name,
            sort_order=sort_sub,
        )
        session.add(sub)

    session.commit()
    return len(cat_map)
