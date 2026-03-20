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

    def get_by_rule_pattern(self, pattern: str, match_type: str) -> list[Transaction]:
        with self._session() as s:
            return repository.get_transactions_by_rule_pattern(s, pattern, match_type)

    def get_by_raw_pattern(self, raw_pattern: str, match_type: str) -> list[Transaction]:
        with self._session() as s:
            return repository.get_transactions_by_raw_pattern(s, raw_pattern, match_type)
