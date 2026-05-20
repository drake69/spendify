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


# ── Account rename + bulk settings + raw taxonomy + default with subs ────────

def test_rename_account_cascades(svc):
    """rename_account updates the row and reports the cascade count."""
    acc = svc.create_account("Vecchio", "BancaY", account_type="bank_account")
    count = svc.rename_account(acc.id, "Nuovo", "BancaZ", new_account_type="credit_card")
    # Returns the number of transactions touched by the cascade (0 here, no tx)
    assert count == 0
    # The account row itself is now renamed
    accounts = svc.get_accounts()
    by_id = {a.id: a for a in accounts}
    assert by_id[acc.id].name == "Nuovo"
    assert by_id[acc.id].bank_name == "BancaZ"
    assert by_id[acc.id].account_type == "credit_card"


def test_set_bulk_writes_multiple_keys(svc):
    """set_bulk persists a dict of key/value pairs in one transaction."""
    svc.set_bulk({
        "k_alpha": "1",
        "k_beta":  "due",
        "k_gamma": "3.14",
    })
    assert svc.get("k_alpha") == "1"
    assert svc.get("k_beta") == "due"
    assert svc.get("k_gamma") == "3.14"


def test_get_taxonomy_raw_returns_row_tuples(svc):
    """get_taxonomy_raw returns plain SQLAlchemy Row tuples for one type
    plus the full subcategory list."""
    # Seed: one expense cat + 2 subs
    cat = svc.create_category("Spese Test", type_="expense")
    svc.create_subcategory(cat.id, "Sub A")
    svc.create_subcategory(cat.id, "Sub B")

    cat_rows, sub_rows = svc.get_taxonomy_raw("expense")
    # cat_rows scoped to "expense"
    cat_names = [r[1] for r in cat_rows]  # row[1] = name
    assert "Spese Test" in cat_names
    # sub_rows is global → both subs are present
    sub_names = [r[2] for r in sub_rows]
    assert "Sub A" in sub_names
    assert "Sub B" in sub_names


def test_get_taxonomy_raw_other_type_is_empty(svc):
    """get_taxonomy_raw('income') only sees the income categories."""
    svc.create_category("Spese Test", type_="expense")
    cat_rows, _ = svc.get_taxonomy_raw("income")
    # The expense category must NOT be in the income result
    cat_names = [r[1] for r in cat_rows]
    assert "Spese Test" not in cat_names


def test_get_default_taxonomy_preview_for_known_language(svc):
    """get_default_taxonomy_preview returns {expenses, income} category
    lists for a supported language."""
    out = svc.get_default_taxonomy_preview("it")
    assert "expenses" in out
    assert "income" in out
    assert isinstance(out["expenses"], list)
    assert isinstance(out["income"], list)


def test_get_default_taxonomy_full_preview_includes_subcategories(svc):
    """get_default_taxonomy_full_preview returns nested dicts with the
    'subcategories' list per category."""
    out = svc.get_default_taxonomy_full_preview("it")
    assert isinstance(out["expenses"], list)
    if out["expenses"]:
        e0 = out["expenses"][0]
        assert "category" in e0
        assert "subcategories" in e0
        assert isinstance(e0["subcategories"], list)
