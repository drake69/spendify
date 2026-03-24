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

    def rename_account(
        self, account_id: int, new_name: str, new_bank_name: str | None = None
    ) -> int:
        """Rename account and cascade to transactions. Returns count updated."""
        with self._session() as s:
            result = repository.rename_account(s, account_id, new_name, new_bank_name)
            s.commit()
            return result

    def delete_all_schemas(self) -> int:
        """Delete all cached document schemas. Returns count deleted."""
        with self._session() as s:
            count = repository.delete_all_schemas(s)
            s.commit()
            return count

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

    # ── Default taxonomy & onboarding ─────────────────────────────────────────

    def get_default_taxonomy_languages(self) -> list[tuple[str, str]]:
        """Return list of (code, label) for all languages in taxonomy_default."""
        from db.taxonomy_defaults import TAXONOMY_DEFAULTS
        with self._session() as s:
            codes = repository.get_default_taxonomy_languages(s)
        return [(code, TAXONOMY_DEFAULTS[code]["label"]) for code in codes if code in TAXONOMY_DEFAULTS]

    def apply_default_taxonomy(self, language: str) -> int:
        """Replace user taxonomy with the built-in template for *language*.

        Also persists description_language = language in user_settings.
        Returns the number of categories applied.
        """
        with self._session() as s:
            n = repository.seed_user_taxonomy_from_default(s, language)
        self.set_bulk({"description_language": language})
        return n

    def is_onboarding_done(self) -> bool:
        """Return True if the user has completed onboarding."""
        with self._session() as s:
            val = repository.get_user_setting(s, "onboarding_done", "false")
        return (val or "false").lower() == "true"

    def set_onboarding_done(self) -> None:
        with self._session() as s:
            repository.set_user_setting(s, "onboarding_done", "true")
            s.commit()

    def get_default_taxonomy_preview(self, language: str) -> dict:
        """Return {'expenses': [category_name, ...], 'income': [category_name, ...]} for *language*."""
        from db.taxonomy_defaults import TAXONOMY_DEFAULTS
        data = TAXONOMY_DEFAULTS.get(language, {})
        return {
            "expenses": [e["category"] for e in data.get("expenses", [])],
            "income":   [e["category"] for e in data.get("income", [])],
        }
