"""RuleService — thin service layer over CategoryRule and DescriptionRule repository functions."""
from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy.orm import sessionmaker

from db import repository
from core.categorizer import CategoryRule as CoreCategoryRule


class RuleService:
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

    # ── CategoryRule ──────────────────────────────────────────────────────────

    def get_rules(self) -> list[CoreCategoryRule]:
        with self._session() as s:
            return repository.get_category_rules(s)

    def create_rule(
        self,
        pattern: str,
        match_type: str,
        category: str,
        subcategory: str,
        context: str | None = None,
        doc_type: str | None = None,
        priority: int = 0,
    ) -> tuple:
        with self._session() as s:
            result = repository.create_category_rule(
                s, pattern, match_type, category, subcategory, context, doc_type, priority
            )
            s.commit()
            return result

    def update_rule(self, rule_id: int, **kwargs) -> bool:
        with self._session() as s:
            result = repository.update_category_rule(s, rule_id, **kwargs)
            s.commit()
            return result

    def delete_rule(self, rule_id: int) -> bool:
        with self._session() as s:
            result = repository.delete_category_rule(s, rule_id)
            s.commit()
            return result

    def apply_to_review(self) -> int:
        with self._session() as s:
            rules = repository.get_category_rules(s)
            result = repository.apply_rules_to_review_transactions(s, rules)
            s.commit()
            return result

    def apply_to_all(self) -> tuple[int, int]:
        with self._session() as s:
            rules = repository.get_category_rules(s)
            result = repository.apply_all_rules_to_all_transactions(s, rules)
            s.commit()
            return result

    # ── DescriptionRule ───────────────────────────────────────────────────────

    def get_description_rules(self) -> list:
        with self._session() as s:
            return repository.get_description_rules(s)

    def create_description_rule(
        self, raw_pattern: str, match_type: str, cleaned_description: str
    ) -> tuple:
        with self._session() as s:
            result = repository.create_description_rule(s, raw_pattern, match_type, cleaned_description)
            s.commit()
            return result

    def delete_description_rule(self, rule_id: int) -> bool:
        with self._session() as s:
            result = repository.delete_description_rule(s, rule_id)
            s.commit()
            return result
