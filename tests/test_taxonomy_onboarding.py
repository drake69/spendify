"""Tests for multi-language taxonomy defaults and onboarding service methods."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from db.models import create_tables
from db.taxonomy_defaults import TAXONOMY_DEFAULTS, SUPPORTED_LANGUAGES
from services.settings_service import SettingsService


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def engine():
    """Fresh in-memory DB with full migration chain (including taxonomy seeding)."""
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    create_tables(eng)
    return eng


@pytest.fixture
def svc(engine):
    return SettingsService(engine)


# ── TAXONOMY_DEFAULTS structure ───────────────────────────────────────────────

class TestTaxonomyDefaultsData:
    def test_all_required_languages_present(self):
        for lang in ("it", "en", "fr", "de", "es"):
            assert lang in TAXONOMY_DEFAULTS, f"Missing language: {lang}"

    def test_each_language_has_label(self):
        for code, data in TAXONOMY_DEFAULTS.items():
            assert "label" in data, f"{code}: missing 'label'"
            assert data["label"], f"{code}: empty 'label'"

    def test_each_language_has_expenses_and_income(self):
        for code, data in TAXONOMY_DEFAULTS.items():
            assert "expenses" in data, f"{code}: missing 'expenses'"
            assert "income"   in data, f"{code}: missing 'income'"
            assert len(data["expenses"]) >= 5, f"{code}: too few expense categories"
            assert len(data["income"])   >= 3, f"{code}: too few income categories"

    def test_each_category_has_subcategories(self):
        for code, data in TAXONOMY_DEFAULTS.items():
            for entry in data["expenses"] + data["income"]:
                assert "category" in entry, f"{code}: entry missing 'category'"
                subs = entry.get("subcategories", [])
                assert len(subs) >= 1, f"{code} / {entry['category']}: no subcategories"

    def test_no_duplicate_categories_per_language(self):
        for code, data in TAXONOMY_DEFAULTS.items():
            for type_key, entries in (("expense", data["expenses"]), ("income", data["income"])):
                names = [e["category"] for e in entries]
                assert len(names) == len(set(names)), \
                    f"{code}/{type_key}: duplicate category names: {names}"

    def test_supported_languages_matches_defaults(self):
        codes_in_supported = {code for code, _ in SUPPORTED_LANGUAGES}
        assert codes_in_supported == set(TAXONOMY_DEFAULTS.keys())


# ── DB migration: taxonomy_default table ──────────────────────────────────────

class TestTaxonomyDefaultMigration:
    def test_taxonomy_default_table_exists(self, engine):
        with engine.connect() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) FROM taxonomy_default")
            ).scalar()
        assert count > 0, "taxonomy_default should be seeded after create_tables()"

    def test_all_languages_seeded_in_db(self, engine):
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT language FROM taxonomy_default GROUP BY language ORDER BY MIN(id)")
            ).fetchall()
        seeded = {r[0] for r in rows}
        assert set(TAXONOMY_DEFAULTS.keys()) <= seeded

    def test_each_language_has_expenses_and_income_in_db(self, engine):
        with engine.connect() as conn:
            for lang in TAXONOMY_DEFAULTS:
                for type_key in ("expense", "income"):
                    count = conn.execute(
                        text("SELECT COUNT(*) FROM taxonomy_default "
                             "WHERE language=:l AND type=:t AND subcategory IS NOT NULL"),
                        {"l": lang, "t": type_key},
                    ).scalar()
                    assert count > 0, f"{lang}/{type_key}: no rows in taxonomy_default"

    def test_migration_is_idempotent(self, engine):
        """Running _migrate_add_taxonomy_default twice must not create duplicates."""
        from db.models import _migrate_add_taxonomy_default
        _migrate_add_taxonomy_default(engine)

        with engine.connect() as conn:
            before = conn.execute(
                text("SELECT COUNT(*) FROM taxonomy_default")
            ).scalar()
        _migrate_add_taxonomy_default(engine)
        with engine.connect() as conn:
            after = conn.execute(
                text("SELECT COUNT(*) FROM taxonomy_default")
            ).scalar()
        assert before == after, "Second run should not insert new rows"


# ── SettingsService: default taxonomy languages ───────────────────────────────

class TestGetDefaultTaxonomyLanguages:
    def test_returns_list_of_tuples(self, svc):
        langs = svc.get_default_taxonomy_languages()
        assert isinstance(langs, list)
        assert len(langs) >= 4
        for item in langs:
            assert len(item) == 2
            code, label = item
            assert isinstance(code, str) and len(code) <= 8
            assert isinstance(label, str) and label

    def test_includes_it_and_en(self, svc):
        codes = [code for code, _ in svc.get_default_taxonomy_languages()]
        assert "it" in codes
        assert "en" in codes

    def test_labels_match_taxonomy_defaults(self, svc):
        for code, label in svc.get_default_taxonomy_languages():
            expected = TAXONOMY_DEFAULTS[code]["label"]
            assert label == expected


# ── SettingsService: taxonomy preview ─────────────────────────────────────────

class TestGetDefaultTaxonomyPreview:
    def test_returns_expenses_and_income(self, svc):
        preview = svc.get_default_taxonomy_preview("it")
        assert "expenses" in preview
        assert "income"   in preview

    def test_expenses_are_strings(self, svc):
        preview = svc.get_default_taxonomy_preview("en")
        assert all(isinstance(c, str) for c in preview["expenses"])
        assert len(preview["expenses"]) >= 5

    def test_unknown_language_returns_empty(self, svc):
        preview = svc.get_default_taxonomy_preview("xx")
        assert preview["expenses"] == []
        assert preview["income"]   == []


# ── SettingsService: apply_default_taxonomy ───────────────────────────────────

class TestApplyDefaultTaxonomy:
    def test_populates_user_taxonomy(self, engine, svc):
        # New DB has 'it' taxonomy seeded by _migrate_add_taxonomy
        # Apply 'en' and verify user taxonomy changes
        n = svc.apply_default_taxonomy("en")
        assert n > 0, "Should return number of categories applied"

        with engine.connect() as conn:
            cats = conn.execute(
                text("SELECT name FROM taxonomy_category WHERE type='expense'")
            ).fetchall()
        names = {r[0] for r in cats}
        # English category names expected
        assert "Housing" in names
        assert "Groceries" in names

    def test_sets_description_language(self, svc):
        svc.apply_default_taxonomy("de")
        settings = svc.get_all()
        assert settings.get("description_language") == "de"

    def test_replaces_previous_taxonomy(self, engine, svc):
        svc.apply_default_taxonomy("it")
        svc.apply_default_taxonomy("fr")

        with engine.connect() as conn:
            # Italian category should not exist anymore
            it_count = conn.execute(
                text("SELECT COUNT(*) FROM taxonomy_category WHERE name='Casa'")
            ).scalar()
            fr_count = conn.execute(
                text("SELECT COUNT(*) FROM taxonomy_category WHERE name='Logement'")
            ).scalar()
        assert it_count == 0, "Italian taxonomy should have been replaced"
        assert fr_count == 1, "French taxonomy should be present"

    def test_subcategories_applied(self, engine, svc):
        svc.apply_default_taxonomy("en")
        with engine.connect() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) FROM taxonomy_subcategory")
            ).scalar()
        assert count >= 30, "Should have many subcategories after applying 'en'"

    def test_fallback_to_it_for_unknown_language(self, engine, svc):
        n = svc.apply_default_taxonomy("xx")
        assert n > 0  # falls back to 'it', still applies something
        with engine.connect() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) FROM taxonomy_category")
            ).scalar()
        assert count > 0


# ── SettingsService: onboarding flag ─────────────────────────────────────────

class TestOnboardingFlag:
    def test_is_onboarding_done_false_on_new_db_without_taxonomy(self):
        """A completely fresh DB (no taxonomy rows) should have onboarding not done."""
        from sqlalchemy import create_engine as _ce
        from db.models import Base
        # Use only Base.metadata — no create_tables() so migration skips auto-set
        eng = _ce("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(eng)
        svc = SettingsService(eng)
        assert svc.is_onboarding_done() is False

    def test_set_onboarding_done(self, svc):
        svc.set_onboarding_done()
        assert svc.is_onboarding_done() is True

    def test_set_onboarding_done_is_idempotent(self, svc):
        svc.set_onboarding_done()
        svc.set_onboarding_done()
        assert svc.is_onboarding_done() is True


# ── Apply taxonomy overrides (wizard step 3 — rename + disable) ───────────────

class TestApplyTaxonomyOverrides:
    """SettingsService.apply_taxonomy_overrides is used by the onboarding
    Taxonomy step to let the user tweak the default template without
    rebuilding the full taxonomy editor inside the wizard."""

    def _count_named(self, engine, name: str) -> int:
        with engine.connect() as conn:
            return conn.execute(text(
                "SELECT COUNT(*) FROM taxonomy_category WHERE name = :n"
            ), {"n": name}).scalar() or 0

    def _total(self, engine) -> int:
        with engine.connect() as conn:
            return conn.execute(text("SELECT COUNT(*) FROM taxonomy_category")).scalar() or 0

    def test_no_overrides_is_a_noop(self, engine, svc):
        svc.apply_default_taxonomy("it")
        n_before = self._total(engine)
        svc.apply_taxonomy_overrides()
        assert self._total(engine) == n_before

    def test_deletion_removes_category(self, engine, svc):
        svc.apply_default_taxonomy("it")
        assert self._count_named(engine, "Trasporti") == 1
        n_before = self._total(engine)
        svc.apply_taxonomy_overrides(deletions=["Trasporti"])
        assert self._count_named(engine, "Trasporti") == 0
        assert self._total(engine) == n_before - 1

    def test_rename_changes_name(self, engine, svc):
        svc.apply_default_taxonomy("it")
        svc.apply_taxonomy_overrides(renames={"Casa": "Abitazione"})
        assert self._count_named(engine, "Casa") == 0
        assert self._count_named(engine, "Abitazione") == 1

    def test_deletion_wins_over_rename_on_same_category(self, engine, svc):
        svc.apply_default_taxonomy("it")
        svc.apply_taxonomy_overrides(
            renames={"Casa": "Abitazione"},
            deletions=["Casa"],
        )
        assert self._count_named(engine, "Casa") == 0
        assert self._count_named(engine, "Abitazione") == 0

    def test_rename_to_empty_or_same_is_ignored(self, engine, svc):
        svc.apply_default_taxonomy("it")
        svc.apply_taxonomy_overrides(renames={"Casa": "", "Trasporti": "Trasporti"})
        assert self._count_named(engine, "Casa") == 1
        assert self._count_named(engine, "Trasporti") == 1


# ── Auto-skip migration for existing users ────────────────────────────────────

class TestAutoSkipMigration:
    """Migration auto-skip detects 'real existing user' via the 4 signals the
    onboarding wizard would have set: ui_language, owner_names, llm_backend,
    and at least one account row.
    """

    @staticmethod
    def _seed_user_settings(conn, **overrides):
        """Insert the four required signals; tests override one to assert each guard."""
        defaults = {
            "ui_language": "it",
            "owner_names": "Mario Rossi",
            "llm_backend": "local_llama_cpp",
        }
        defaults.update(overrides)
        for k, v in defaults.items():
            conn.execute(text(
                "INSERT OR REPLACE INTO user_settings (key, value) VALUES (:k, :v)"
            ), {"k": k, "v": v})

    @staticmethod
    def _seed_account(conn):
        conn.execute(text(
            "INSERT INTO account (name, bank_name, account_type) "
            "VALUES ('main', 'Bank', 'bank_account')"
        ))

    def _new_engine_with_migrations(self):
        from sqlalchemy import create_engine as _ce
        from db.models import (
            Base,
            _migrate_add_user_settings,
            _migrate_add_taxonomy_default,
            _migrate_add_taxonomy,
        )
        eng = _ce("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(eng)
        _migrate_add_user_settings(eng)
        _migrate_add_taxonomy_default(eng)
        _migrate_add_taxonomy(eng)
        return eng

    def test_returning_user_with_all_signals_skips_wizard(self):
        """All 4 prerequisites present → migration marks onboarding_done."""
        from db.models import _migrate_set_onboarding_done_for_existing_users
        eng = self._new_engine_with_migrations()
        with eng.connect() as conn:
            self._seed_user_settings(conn)
            self._seed_account(conn)
            conn.commit()
        _migrate_set_onboarding_done_for_existing_users(eng)

        svc = SettingsService(eng)
        assert svc.is_onboarding_done() is True

    def test_fresh_db_with_only_seeded_taxonomy_does_not_skip_onboarding(self):
        """Regression for #AI-58: default taxonomy seed alone must not trigger skip."""
        from db.models import _migrate_set_onboarding_done_for_existing_users
        eng = self._new_engine_with_migrations()
        # No user_settings overrides → owner_names/ui_language/llm_backend missing
        _migrate_set_onboarding_done_for_existing_users(eng)

        svc = SettingsService(eng)
        assert svc.is_onboarding_done() is False

    def test_missing_owner_names_blocks_skip(self):
        from db.models import _migrate_set_onboarding_done_for_existing_users
        eng = self._new_engine_with_migrations()
        with eng.connect() as conn:
            self._seed_user_settings(conn, owner_names="")
            self._seed_account(conn)
            conn.commit()
        _migrate_set_onboarding_done_for_existing_users(eng)

        svc = SettingsService(eng)
        assert svc.is_onboarding_done() is False

    def test_missing_ui_language_blocks_skip(self):
        from db.models import _migrate_set_onboarding_done_for_existing_users
        eng = self._new_engine_with_migrations()
        with eng.connect() as conn:
            self._seed_user_settings(conn, ui_language="")
            self._seed_account(conn)
            conn.commit()
        _migrate_set_onboarding_done_for_existing_users(eng)

        svc = SettingsService(eng)
        assert svc.is_onboarding_done() is False

    def test_missing_llm_backend_blocks_skip(self):
        from db.models import _migrate_set_onboarding_done_for_existing_users
        eng = self._new_engine_with_migrations()
        with eng.connect() as conn:
            self._seed_user_settings(conn, llm_backend="")
            self._seed_account(conn)
            conn.commit()
        _migrate_set_onboarding_done_for_existing_users(eng)

        svc = SettingsService(eng)
        assert svc.is_onboarding_done() is False

    def test_missing_account_blocks_skip(self):
        from db.models import _migrate_set_onboarding_done_for_existing_users
        eng = self._new_engine_with_migrations()
        with eng.connect() as conn:
            self._seed_user_settings(conn)
            conn.commit()
        # No account inserted
        _migrate_set_onboarding_done_for_existing_users(eng)

        svc = SettingsService(eng)
        assert svc.is_onboarding_done() is False

    def test_migration_does_not_overwrite_existing_flag(self):
        """If onboarding_done is already set, migration must not touch it."""
        from sqlalchemy import create_engine as _ce
        from db.models import (
            Base,
            _migrate_add_user_settings,
            _migrate_add_taxonomy_default,
            _migrate_add_taxonomy,
            _migrate_set_onboarding_done_for_existing_users,
        )
        eng = _ce("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(eng)
        _migrate_add_user_settings(eng)
        _migrate_add_taxonomy_default(eng)
        _migrate_add_taxonomy(eng)

        # Manually set flag to false BEFORE migration runs
        with eng.connect() as conn:
            conn.execute(text(
                "INSERT OR REPLACE INTO user_settings (key, value) "
                "VALUES ('onboarding_done', 'false')"
            ))
            conn.commit()

        _migrate_set_onboarding_done_for_existing_users(eng)

        # Migration must NOT have overwritten the explicit 'false'
        svc = SettingsService(eng)
        assert svc.is_onboarding_done() is False
