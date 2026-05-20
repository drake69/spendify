"""Tests for BudgetService — targets CRUD and actual-vs-budget comparison.

The module had zero coverage; these cases exercise both halves of the
service through the real BudgetTarget table and a synthesised period.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from db.models import Transaction, create_tables, get_session
from services.budget_service import BudgetService


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    create_tables(eng)
    return eng


@pytest.fixture
def svc(engine):
    return BudgetService(engine)


def _make_tx(tx_id, date, amount, tx_type="expense", category=None):
    return Transaction(
        id=tx_id, date=date, amount=Decimal(str(amount)),
        currency="EUR", description="x", source_file="f.csv",
        doc_type="bank_statement", account_label="BancaX",
        tx_type=tx_type, category=category, subcategory=None,
        reconciled=False, to_review=False,
    )


# ── Targets CRUD ──────────────────────────────────────────────────────────────

class TestBudgetTargets:

    def test_empty_db_returns_no_targets(self, svc):
        assert svc.get_targets() == []

    def test_save_then_get_roundtrip(self, svc):
        svc.save_targets([
            {"category": "Alimentari", "target_pct": 25.0},
            {"category": "Trasporti", "target_pct": 10.5},
        ])
        targets = svc.get_targets()
        assert len(targets) == 2
        by_cat = {t["category"]: t["target_pct"] for t in targets}
        assert by_cat["Alimentari"] == 25.0
        assert by_cat["Trasporti"] == 10.5

    def test_save_target_zero_deletes_existing(self, svc):
        svc.save_targets([{"category": "Casa", "target_pct": 30.0}])
        assert {t["category"] for t in svc.get_targets()} == {"Casa"}
        # Zero → delete
        svc.save_targets([{"category": "Casa", "target_pct": 0}])
        assert svc.get_targets() == []

    def test_save_target_none_deletes_existing(self, svc):
        svc.save_targets([{"category": "Casa", "target_pct": 30.0}])
        svc.save_targets([{"category": "Casa", "target_pct": None}])
        assert svc.get_targets() == []

    def test_save_drops_categories_omitted_from_list(self, svc):
        """Saving a fresh list without a previously-known category must
        remove it from the table (full-replace semantics)."""
        svc.save_targets([
            {"category": "Alimentari", "target_pct": 25.0},
            {"category": "Trasporti", "target_pct": 10.0},
        ])
        # Now save only one of them → Trasporti must disappear
        svc.save_targets([{"category": "Alimentari", "target_pct": 28.0}])
        rem = svc.get_targets()
        assert len(rem) == 1
        assert rem[0]["category"] == "Alimentari"
        assert rem[0]["target_pct"] == 28.0


# ── get_actual_vs_budget ──────────────────────────────────────────────────────

class TestActualVsBudget:

    def test_empty_db_returns_zero_totals(self, svc):
        out = svc.get_actual_vs_budget("2026-01-01", "2026-01-31")
        assert out["total_income"] == 0
        assert out["total_expenses"] == 0
        assert out["liquidity"] == 0
        assert out["rows"] == []
        # When income==0 the actual pct floor is 0 → liquidity_actual_pct stays at 100
        assert out["liquidity_actual_pct"] == 100.0
        # Without targets the budgeted liquidity is also 100
        assert out["liquidity_target_pct"] == 100.0

    def test_status_green_yellow_red_thresholds(self, engine, svc):
        """Deviation |a-b| ≤ 5 → green, ≤ 10 → yellow, > 10 → red."""
        # Income 1000, expenses split: Alim 200 (20%), Trasp 100 (10%), Casa 700 (70%)
        with get_session(engine) as s:
            for tx in [
                _make_tx("a"*24, "2026-01-15", 1000, tx_type="income", category="Stipendio"),
                _make_tx("b"*24, "2026-01-16", -200, tx_type="expense", category="Alimentari"),
                _make_tx("c"*24, "2026-01-17", -100, tx_type="expense", category="Trasporti"),
                _make_tx("d"*24, "2026-01-18", -700, tx_type="expense", category="Casa"),
            ]:
                s.add(tx)
            s.commit()
        # Targets: Alim 22 (≈green), Trasp 18 (yellow, |10-18|=8), Casa 50 (red, |70-50|=20)
        svc.save_targets([
            {"category": "Alimentari", "target_pct": 22.0},
            {"category": "Trasporti", "target_pct": 18.0},
            {"category": "Casa", "target_pct": 50.0},
        ])
        out = svc.get_actual_vs_budget("2026-01-01", "2026-01-31")
        by_cat = {r["category"]: r for r in out["rows"]}
        assert by_cat["Alimentari"]["status"] == "green"
        assert by_cat["Trasporti"]["status"] == "yellow"
        assert by_cat["Casa"]["status"] == "red"

    def test_categories_with_actuals_but_no_target_get_status_none(self, engine, svc):
        with get_session(engine) as s:
            for tx in [
                _make_tx("a"*24, "2026-01-01", 500, tx_type="income", category="Stipendio"),
                _make_tx("b"*24, "2026-01-02", -100, tx_type="expense", category="Imprevisto"),
            ]:
                s.add(tx)
            s.commit()
        out = svc.get_actual_vs_budget("2026-01-01", "2026-01-31")
        row = next(r for r in out["rows"] if r["category"] == "Imprevisto")
        assert row["target_pct"] is None
        assert row["deviation"] is None
        assert row["status"] == "none"

    def test_liquidity_metrics(self, engine, svc):
        """liquidity_target_pct = 100 - Σtarget_pct;
        liquidity_actual_pct = 100 - (total_expenses/total_income*100)."""
        with get_session(engine) as s:
            for tx in [
                _make_tx("a"*24, "2026-01-01", 1000, tx_type="income", category="Stipendio"),
                _make_tx("b"*24, "2026-01-02", -300, tx_type="expense", category="Alimentari"),
            ]:
                s.add(tx)
            s.commit()
        svc.save_targets([{"category": "Alimentari", "target_pct": 25.0}])
        out = svc.get_actual_vs_budget("2026-01-01", "2026-01-31")
        assert out["total_income"] == 1000
        assert out["total_expenses"] == 300
        assert out["liquidity"] == 700
        assert out["liquidity_target_pct"] == 75.0
        assert out["liquidity_actual_pct"] == 70.0
