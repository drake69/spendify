"""TransactionService — thin service layer over transaction repository functions."""
from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy.orm import sessionmaker

from db.models import Transaction
from db import repository


class TransactionService:
    def __init__(self, engine) -> None:
        self.engine = engine
        self._Session = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def _session(self):
        s = self._Session()
        try:
            yield s
        finally:
            s.close()

    def get_transactions(self, filters: dict | None = None, limit: int = 5000, offset: int = 0) -> list[Transaction]:
        with self._session() as s:
            return repository.get_transactions(s, filters or {}, limit, offset)

    def update_category(self, tx_id: str, category: str, subcategory: str) -> bool:
        with self._session() as s:
            result = repository.update_transaction_category(s, tx_id, category, subcategory)
            s.commit()
            return result

    def update_context(self, tx_id: str, context: str | None) -> bool:
        with self._session() as s:
            result = repository.update_transaction_context(s, tx_id, context)
            s.commit()
            return result

    def toggle_giroconto(self, tx_id: str) -> tuple[bool, str]:
        with self._session() as s:
            result = repository.toggle_transaction_giroconto(s, tx_id)
            s.commit()
            return result

    def get_similar(self, description: str, exclude_id: str, threshold: float = 0.8) -> list[Transaction]:
        with self._session() as s:
            return repository.get_similar_transactions(s, description, exclude_id, threshold)

    def bulk_set_giroconto_by_description(self, description: str, make_giroconto: bool, exclude_id: str) -> int:
        with self._session() as s:
            result = repository.bulk_set_giroconto_by_description(s, description, make_giroconto, exclude_id)
            s.commit()
            return result

    def delete_by_filter(self, filters: dict) -> int:
        with self._session() as s:
            result = repository.delete_transactions_by_filter(s, filters)
            s.commit()
            return result

    def get_cross_account_duplicates(self) -> list[list[Transaction]]:
        with self._session() as s:
            return repository.get_cross_account_duplicates(s)

    def validate(self, tx_id: str) -> bool:
        """Mark transaction as human-validated without changing category."""
        with self._session() as s:
            result = repository.validate_transaction(s, tx_id)
            s.commit()
            return result

    def unvalidate(self, tx_id: str) -> bool:
        """Remove human-validated flag from a transaction."""
        with self._session() as s:
            result = repository.unvalidate_transaction(s, tx_id)
            s.commit()
            return result

    def get_by_rule_pattern(self, pattern: str, match_type: str) -> list[Transaction]:
        with self._session() as s:
            return repository.get_transactions_by_rule_pattern(s, pattern, match_type)

    def get_by_raw_pattern(self, raw_pattern: str, match_type: str) -> list[Transaction]:
        with self._session() as s:
            return repository.get_transactions_by_raw_pattern(s, raw_pattern, match_type)

    # ── Distinct value helpers ─────────────────────────────────────────────────

    def get_distinct_account_labels(self) -> list[str]:
        with self._session() as s:
            rows = s.query(Transaction.account_label).distinct().all()
            return sorted({r[0] for r in rows if r[0]})

    def get_distinct_context_values(self) -> list[str]:
        with self._session() as s:
            rows = s.query(Transaction.context).distinct().all()
            return sorted({r[0] for r in rows if r[0]})

    def get_monthly_tx_counts(self) -> list:
        """Return (year_month, account_label, tx_count) named-tuple rows for the checklist pivot."""
        from sqlalchemy import func
        with self._session() as s:
            return (
                s.query(
                    func.strftime("%Y-%m", Transaction.date).label("year_month"),
                    Transaction.account_label,
                    func.count(Transaction.id).label("tx_count"),
                )
                .group_by(func.strftime("%Y-%m", Transaction.date), Transaction.account_label)
                .order_by(func.strftime("%Y-%m", Transaction.date).desc())
                .all()
            )

    # ── Export helpers ────────────────────────────────────────────────────────

    def export_csv(self, filters: dict) -> bytes:
        from reports.generator import generate_csv_export
        with self._session() as s:
            return generate_csv_export(s, filters=filters)

    def export_xlsx(self, filters: dict) -> bytes:
        from reports.generator import generate_xlsx_export
        with self._session() as s:
            return generate_xlsx_export(s, filters=filters)

    def export_html(self, date_from: str | None = None, date_to: str | None = None) -> str:
        from reports.generator import generate_html_report
        with self._session() as s:
            return generate_html_report(s, date_from=date_from, date_to=date_to)

    # ── Similarity / lookup helpers ───────────────────────────────────────────

    def count_by_description(self, description: str, exclude_id: str) -> int:
        with self._session() as s:
            return s.query(Transaction).filter(
                Transaction.description == description,
                Transaction.id != exclude_id,
            ).count()

    def count_by_raw_description(self, raw_description: str, exclude_id: str) -> int:
        with self._session() as s:
            return s.query(Transaction).filter(
                Transaction.raw_description == raw_description,
                Transaction.id != exclude_id,
            ).count()

    def get_by_description(self, description: str, exclude_id: str) -> list[Transaction]:
        with self._session() as s:
            return s.query(Transaction).filter(
                Transaction.description == description,
                Transaction.id != exclude_id,
            ).all()

    def get_by_raw_description_value(self, raw_description: str) -> list[Transaction]:
        with self._session() as s:
            return s.query(Transaction).filter(
                Transaction.raw_description == raw_description
            ).all()

    # ── Spending report (A-01) ────────────────────────────────────────────────

    def get_spending_aggregation(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
        account_ids: list[str] | None = None,
        exclude_internal: bool = True,
    ) -> list[dict]:
        """Aggregated spending by context/category/subcategory."""
        with self._session() as s:
            return repository.get_spending_aggregation(
                s, date_from=date_from, date_to=date_to,
                account_ids=account_ids, exclude_internal=exclude_internal,
            )

    def get_monthly_spending(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
        account_ids: list[str] | None = None,
        exclude_internal: bool = True,
    ) -> list[dict]:
        """Monthly totals by category for trend charts."""
        with self._session() as s:
            return repository.get_monthly_spending(
                s, date_from=date_from, date_to=date_to,
                account_ids=account_ids, exclude_internal=exclude_internal,
            )

    def get_transactions_for_export(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
        account_ids: list[str] | None = None,
        exclude_internal: bool = True,
    ) -> list[Transaction]:
        """Get transactions for detailed Excel export sheets."""
        filters: dict = {}
        if date_from:
            filters["date_from"] = date_from
        if date_to:
            filters["date_to"] = date_to
        if account_ids and len(account_ids) == 1:
            filters["account_label"] = account_ids[0]
        excluded = ["card_settlement", "aggregate_debit"]
        if exclude_internal:
            excluded += ["internal_in", "internal_out"]
        filters["exclude_tx_types"] = excluded
        with self._session() as s:
            txs = repository.get_transactions(s, filters, limit=50000)
            # Filter by multiple accounts if needed
            if account_ids and len(account_ids) > 1:
                txs = [t for t in txs if t.account_label in account_ids]
            return txs

    def get_to_review_batch(self, limit: int = 500) -> list[Transaction]:
        with self._session() as s:
            return s.query(Transaction).filter(Transaction.to_review.is_(True)).limit(limit).all()

    def get_without_category_batch(self, limit: int = 500) -> list[Transaction]:
        with self._session() as s:
            return s.query(Transaction).filter(Transaction.category.is_(None)).limit(limit).all()

    def get_by_id(self, tx_id: str) -> Transaction | None:
        with self._session() as s:
            return s.get(Transaction, tx_id)

    def get_by_ids(self, ids: list[str]) -> list[Transaction]:
        with self._session() as s:
            return s.query(Transaction).filter(Transaction.id.in_(ids)).all()

    # ── Bulk mutation helpers ─────────────────────────────────────────────────

    def update_context_bulk(self, ids: list[str], context: str | None) -> int:
        """Update context for a list of transaction IDs; returns count updated."""
        with self._session() as s:
            updated = 0
            for tx_id in ids:
                if repository.update_transaction_context(s, tx_id, context):
                    updated += 1
            s.commit()
            return updated

    def delete_duplicate_groups(self, groups: list[list]) -> int:
        """Delete all but the first transaction in each duplicate group.

        Cascades deletion of ReconciliationLink and InternalTransferLink rows.
        Returns the total number of transactions deleted.
        """
        from db.models import ReconciliationLink, InternalTransferLink
        deleted = 0
        with self._session() as s:
            for g in groups:
                for tx in g[1:]:
                    s.query(ReconciliationLink).filter(
                        (ReconciliationLink.settlement_id == tx.id) |
                        (ReconciliationLink.detail_id == tx.id)
                    ).delete(synchronize_session=False)
                    s.query(InternalTransferLink).filter(
                        (InternalTransferLink.out_id == tx.id) |
                        (InternalTransferLink.in_id == tx.id)
                    ).delete(synchronize_session=False)
                    tx_obj = s.get(Transaction, tx.id)
                    if tx_obj:
                        s.delete(tx_obj)
                        deleted += 1
            if deleted:
                s.commit()
        return deleted
