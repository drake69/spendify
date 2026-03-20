"""Tests for SettingsService."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from db.models import Base, DEFAULT_USER_SETTINGS
from core.categorizer import TaxonomyConfig
from services.settings_service import SettingsService


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def svc(engine):
    return SettingsService(engine)


# ── UserSettings tests ────────────────────────────────────────────────────────

def test_get_all_returns_defaults(svc):
    settings = svc.get_all()
    # get_all_user_settings returns a dict starting from DEFAULT_USER_SETTINGS
    for key in DEFAULT_USER_SETTINGS:
        assert key in settings


def test_set_and_get(svc):
    svc.set("description_language", "en")
    value = svc.get("description_language")
    assert value == "en"


def test_get_missing_key_returns_default(svc):
    value = svc.get("nonexistent_key", default="fallback")
    assert value == "fallback"


# ── Taxonomy tests ────────────────────────────────────────────────────────────

def test_get_taxonomy(svc):
    taxonomy = svc.get_taxonomy()
    assert isinstance(taxonomy, TaxonomyConfig)
    # Should have fallback categories when no taxonomy data seeded
    assert len(taxonomy.expenses) > 0 or len(taxonomy.income) > 0


def test_create_and_delete_category(svc):
    cat = svc.create_category("Test Category", "expense")
    assert cat.id is not None
    assert cat.name == "Test Category"

    ok = svc.delete_category(cat.id)
    assert ok is True

    cats = svc.get_categories(type_filter="expense")
    names = [c.name for c in cats]
    assert "Test Category" not in names


def test_create_subcategory(svc):
    cat = svc.create_category("Alimentari", "expense")
    sub = svc.create_subcategory(cat.id, "Supermercato")
    assert sub.id is not None
    assert sub.name == "Supermercato"
    assert sub.category_id == cat.id


def test_update_category(svc):
    cat = svc.create_category("Old Name", "expense")
    ok = svc.update_category(cat.id, "New Name")
    assert ok is True
    cats = svc.get_categories(type_filter="expense")
    names = [c.name for c in cats]
    assert "New Name" in names
    assert "Old Name" not in names


def test_update_subcategory(svc):
    cat = svc.create_category("Cat", "expense")
    sub = svc.create_subcategory(cat.id, "OldSub")
    ok = svc.update_subcategory(sub.id, "NewSub")
    assert ok is True


def test_delete_subcategory(svc):
    cat = svc.create_category("Cat2", "expense")
    sub = svc.create_subcategory(cat.id, "SubToDelete")
    ok = svc.delete_subcategory(sub.id)
    assert ok is True


# ── Account tests ─────────────────────────────────────────────────────────────

def test_create_and_delete_account(svc):
    acc = svc.create_account("Conto POPSO", "Banca Popolare di Sondrio")
    assert acc.id is not None
    assert acc.name == "Conto POPSO"

    accounts = svc.get_accounts()
    names = [a.name for a in accounts]
    assert "Conto POPSO" in names

    ok = svc.delete_account(acc.id)
    assert ok is True

    accounts_after = svc.get_accounts()
    names_after = [a.name for a in accounts_after]
    assert "Conto POPSO" not in names_after


def test_delete_account_not_found(svc):
    ok = svc.delete_account(9999)
    assert ok is False
