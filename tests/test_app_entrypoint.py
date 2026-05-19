"""Tests for app.py — Streamlit entrypoint.

Cannot run app.py directly (it calls st.set_page_config at import time),
so we test the individual bootstrap steps that are exercised at startup.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine

from db.models import create_tables, get_engine


# ── DB bootstrap ─────────────────────────────────────────────────────────────

class TestDBBootstrap:
    """Verify that the DB bootstrap sequence works (lines 44-46 of app.py)."""

    def test_get_engine_creates_sqlite(self, tmp_path):
        db_path = tmp_path / "test.db"
        url = f"sqlite:///{db_path}"
        eng = get_engine(url)
        assert eng is not None

    def test_create_tables_idempotent(self, tmp_path):
        db_path = tmp_path / "test.db"
        url = f"sqlite:///{db_path}"
        eng = get_engine(url)
        # First call: creates all tables
        result1 = create_tables(eng)
        assert result1 is eng
        # Second call: no error (idempotent)
        result2 = create_tables(eng)
        assert result2 is eng

    def test_default_db_url(self):
        """Without SPENDIFAI_DB env var, default is sqlite:///ledger.db."""
        url = os.getenv("SPENDIFAI_DB", "sqlite:///ledger.db")
        assert url.startswith("sqlite:///")

    def test_orphan_schema_hash_does_not_skip_create(self, tmp_path):
        """Regression: if the DB file is deleted but `.schema_hash` lingers,
        the fast-path must NOT skip create_all (would leave tables missing
        and raise `no such table` at first query)."""
        db_path = tmp_path / "test.db"
        url = f"sqlite:///{db_path}"

        # 1st run: create schema → .schema_hash written
        create_tables(get_engine(url))
        hash_file = tmp_path / ".schema_hash"
        assert hash_file.is_file()

        # Simulate user wiping the DB while .schema_hash survives
        db_path.unlink()

        # 2nd run on a fresh engine must rebuild the schema
        eng2 = get_engine(url)
        create_tables(eng2)
        from sqlalchemy import inspect as _inspect
        assert "import_job" in _inspect(eng2).get_table_names()


# ── Prompt integrity ─────────────────────────────────────────────────────────

class TestPromptIntegrity:
    """Verify prompt integrity check (lines 33-42 of app.py)."""

    def test_verify_prompt_integrity_returns_list(self):
        from core.prompt_guard import verify_prompt_integrity
        errors = verify_prompt_integrity()
        assert isinstance(errors, list)
        # In a clean repo, no errors expected
        assert len(errors) == 0


# ── Stale job reset ──────────────────────────────────────────────────────────

class TestStaleJobReset:
    """Verify startup cleanup of stale import jobs (lines 49-56)."""

    def test_reset_stale_jobs_on_empty_db(self, tmp_path):
        from db.models import get_session
        from db.repository import reset_stale_jobs

        db_path = tmp_path / "test.db"
        eng = get_engine(f"sqlite:///{db_path}")
        create_tables(eng)

        with get_session(eng) as s:
            n = reset_stale_jobs(s)
        assert n == 0  # no jobs → no resets


# ── Onboarding gate ──────────────────────────────────────────────────────────

class TestOnboardingGate:
    """Verify onboarding detection (lines 80-85)."""

    def test_fresh_db_onboarding_NOT_auto_set(self, tmp_path):
        """Regression for #AI-58: a brand-new DB must NOT be flagged as
        already-onboarded just because the migration chain seeded the
        default taxonomy. The auto-skip migration now needs all four
        wizard prerequisites (ui_language, owner_names, llm_backend,
        ≥1 account) — none are set on a fresh DB."""
        from services.settings_service import SettingsService

        db_path = tmp_path / "test.db"
        eng = get_engine(f"sqlite:///{db_path}")
        create_tables(eng)

        svc = SettingsService(eng)
        assert svc.is_onboarding_done() is False

    def test_after_onboarding_done(self, tmp_path):
        from services.settings_service import SettingsService

        db_path = tmp_path / "test.db"
        eng = get_engine(f"sqlite:///{db_path}")
        create_tables(eng)

        svc = SettingsService(eng)
        svc.set("onboarding_done", "true")
        assert svc.is_onboarding_done() is True


# ── Page routing ─────────────────────────────────────────────────────────────

class TestPageRouting:
    """Verify all page routes import without errors (lines 97-151)."""

    PAGES = [
        ("import", "ui.upload_page", "render_upload_page"),
        ("history", "ui.history_page", "render_history_page"),
        ("ledger", "ui.registry_page", "render_registry_page"),
        ("bulk_edit", "ui.bulk_edit_page", "render_bulk_edit_page"),
        ("analytics", "ui.analysis_page", "render_analysis_page"),
        ("report", "ui.report_page", "render_report_page"),
        ("budget", "ui.budget_page", "render_budget_page"),
        ("budget_vs_actual", "ui.budget_vs_actual_page", "render_budget_vs_actual_page"),
        ("review", "ui.review_page", "render_review_page"),
        ("rules", "ui.rules_page", "render_rules_page"),
        ("taxonomy", "ui.taxonomy_page", "render_taxonomy_page"),
        ("settings", "ui.settings_page", "render_settings_page"),
        ("checklist", "ui.checklist_page", "render_checklist_page"),
        ("chat", "ui.chat_page", "render_chat_page"),
    ]

    @pytest.mark.parametrize("page,module,func", PAGES)
    def test_page_module_importable(self, page, module, func):
        """Each page module can be imported and has the expected render function."""
        import importlib
        mod = importlib.import_module(module)
        assert hasattr(mod, func), f"{module} missing {func}"
        assert callable(getattr(mod, func))
