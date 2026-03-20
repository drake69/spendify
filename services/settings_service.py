"""SettingsService — thin service layer over UserSettings, Taxonomy, and Account repository functions."""
from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy.orm import sessionmaker

from db import repository
from core.categorizer import TaxonomyConfig


class SettingsService:
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

    # ── UserSettings ─────────────────────────────────────────────────────────

    def get_all(self) -> dict[str, str]:
        with self._session() as s:
            return repository.get_all_user_settings(s)

    def get(self, key: str, default: str | None = None) -> str | None:
        with self._session() as s:
            return repository.get_user_setting(s, key, default)

    def set(self, key: str, value: str) -> None:
        with self._session() as s:
            repository.set_user_setting(s, key, value)
            s.commit()

    # ── Taxonomy ──────────────────────────────────────────────────────────────

    def get_taxonomy(self) -> TaxonomyConfig:
        with self._session() as s:
            return repository.get_taxonomy_config(s)

    def get_categories(self, type_filter: str | None = None) -> list:
        with self._session() as s:
            return repository.get_taxonomy_categories(s, type_filter)

    def create_category(self, name: str, type_: str):
        with self._session() as s:
            result = repository.create_taxonomy_category(s, name, type_)
            s.commit()
            return result

    def update_category(self, cat_id: int, name: str) -> bool:
        with self._session() as s:
            result = repository.update_taxonomy_category(s, cat_id, name)
            s.commit()
            return result

    def delete_category(self, cat_id: int) -> bool:
        with self._session() as s:
            result = repository.delete_taxonomy_category(s, cat_id)
            s.commit()
            return result

    def create_subcategory(self, cat_id: int, name: str):
        with self._session() as s:
            result = repository.create_taxonomy_subcategory(s, cat_id, name)
            s.commit()
            return result

    def update_subcategory(self, sub_id: int, name: str) -> bool:
        with self._session() as s:
            result = repository.update_taxonomy_subcategory(s, sub_id, name)
            s.commit()
            return result

    def delete_subcategory(self, sub_id: int) -> bool:
        with self._session() as s:
            result = repository.delete_taxonomy_subcategory(s, sub_id)
            s.commit()
            return result

    # ── Account ───────────────────────────────────────────────────────────────

    def get_accounts(self) -> list:
        with self._session() as s:
            return repository.get_accounts(s)

    def create_account(self, name: str, bank_name: str):
        with self._session() as s:
            result = repository.create_account(s, name, bank_name)
            s.commit()
            return result

    def delete_account(self, account_id: int) -> bool:
        with self._session() as s:
            result = repository.delete_account(s, account_id)
            s.commit()
            return result

    # ── Bulk settings save ────────────────────────────────────────────────────

    def set_bulk(self, settings: dict[str, str]) -> None:
        """Persist multiple key/value settings in a single transaction."""
        with self._session() as s:
            for key, value in settings.items():
                repository.set_user_setting(s, key, value)
            s.commit()

    # ── Raw taxonomy queries (avoid DetachedInstanceError in UI) ──────────────

    def get_taxonomy_raw(self, type_key: str) -> tuple[list, list]:
        """Return (cat_rows, sub_rows) as plain SQLAlchemy Row tuples.

        cat_rows: (id, name, type, sort_order) for the given type_key
        sub_rows: (id, category_id, name, sort_order) for ALL subcategories
        """
        from sqlalchemy import text as _sql
        with self._session() as s:
            cat_rows = s.execute(
                _sql(
                    "SELECT id, name, type, sort_order "
                    "FROM taxonomy_category "
                    "WHERE type=:t ORDER BY name COLLATE NOCASE"
                ),
                {"t": type_key},
            ).fetchall()
            sub_rows = s.execute(
                _sql(
                    "SELECT id, category_id, name, sort_order "
                    "FROM taxonomy_subcategory ORDER BY name COLLATE NOCASE"
                ),
            ).fetchall()
        return cat_rows, sub_rows
