"""Tests for C-06: Fan-out comportamentale.

Covers:
  - find_similar_uncategorized returns correct transactions
  - apply_fan_out copies category/subcategory/context
  - apply_fan_out sets category_source='history'
  - Already-validated transactions are not included in fan-out
  - Manual categorizations are not overwritten
  - Rule-based categorizations are not included in fan-out
  - Exclude_tx_id works correctly
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event

from core.history_engine import apply_fan_out, find_similar_uncategorized
from core.normalizer import compute_transaction_id
from db.models import Base, Transaction, get_session


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _set_sqlite_pragma(dbapi_conn, _rec):
        dbapi_conn.execute("PRAGMA foreign_keys=OFF")

    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine):
    with get_session(engine) as s:
        yield s


# ── Helpers ───────────────────────────────────────────────────────────────────

_SEQ = 0


def _make_tx(
    session,
    *,
    description: str,
    category: str | None = None,
    subcategory: str | None = None,
    context: str | None = None,
    category_source: str | None = None,
    human_validated: bool = False,
    amount: float = -10.0,
    date: str = "2025-01-15",
    account_label: str = "TestAccount",
    source_file: str = "f.csv",
) -> Transaction:
    """Insert a transaction with given properties."""
    global _SEQ
    _SEQ += 1
    amount_key = str(Decimal(str(amount + _SEQ * 0.001)).normalize())
    desc_key = description.strip()
    tx_id = compute_transaction_id(source_file, date, amount_key, desc_key, account_label=account_label)
    t = Transaction(
        id=tx_id,
        date=date,
        amount=Decimal(str(amount)),
        currency="EUR",
        description=description,
        raw_description=description,
        raw_amount=str(amount),
        source_file=source_file,
        account_label=account_label,
        tx_type="expense",
        category=category,
        subcategory=subcategory,
        context=context,
        category_source=category_source,
        human_validated=human_validated,
        validated_at=datetime.now(timezone.utc) if human_validated else None,
    )
    session.add(t)
    session.flush()
    return t


# ── Tests: find_similar_uncategorized ────────────────────────────────────────

class TestFindSimilarUncategorized:

    def test_finds_llm_categorized(self, session):
        """Transactions with category_source='llm' should be found."""
        source = _make_tx(
            session, description="esselunga", category="Alimentari",
            subcategory="Spesa", category_source="manual", human_validated=True,
        )
        target = _make_tx(
            session, description="esselunga", category="Altro",
            subcategory="Generico", category_source="llm",
        )
        results = find_similar_uncategorized(session, "esselunga", source.id)
        assert len(results) == 1
        assert results[0].id == target.id

    def test_finds_uncategorized(self, session):
        """Transactions with category_source=None should be found."""
        source = _make_tx(
            session, description="lidl", category="Alimentari",
            category_source="manual", human_validated=True,
        )
        target = _make_tx(
            session, description="lidl", category=None,
            category_source=None,
        )
        results = find_similar_uncategorized(session, "lidl", source.id)
        assert len(results) == 1
        assert results[0].id == target.id

    def test_excludes_validated(self, session):
        """Already-validated transactions should NOT be returned."""
        _make_tx(
            session, description="conad", category="Alimentari",
            category_source="manual", human_validated=True,
        )
        _make_tx(
            session, description="conad", category="Alimentari",
            category_source="llm", human_validated=True,
        )
        results = find_similar_uncategorized(session, "conad")
        assert len(results) == 0

    def test_excludes_rule_based(self, session):
        """Transactions with category_source='rule' should NOT be returned."""
        _make_tx(
            session, description="netflix", category="Intrattenimento",
            category_source="rule",
        )
        results = find_similar_uncategorized(session, "netflix")
        assert len(results) == 0

    def test_excludes_manual(self, session):
        """Transactions with category_source='manual' should NOT be returned."""
        _make_tx(
            session, description="amazon", category="Tecnologia",
            category_source="manual",
        )
        results = find_similar_uncategorized(session, "amazon")
        assert len(results) == 0

    def test_excludes_different_description(self, session):
        """Transactions with a different description should NOT be returned."""
        _make_tx(
            session, description="esselunga", category="Altro",
            category_source="llm",
        )
        results = find_similar_uncategorized(session, "conad")
        assert len(results) == 0

    def test_exclude_tx_id(self, session):
        """The exclude_tx_id parameter should exclude the source transaction."""
        tx = _make_tx(
            session, description="lidl", category="Altro",
            category_source="llm",
        )
        results = find_similar_uncategorized(session, "lidl", tx.id)
        assert len(results) == 0

    def test_no_exclude(self, session):
        """Without exclude_tx_id, all matching are returned."""
        tx = _make_tx(
            session, description="lidl", category="Altro",
            category_source="llm",
        )
        results = find_similar_uncategorized(session, "lidl")
        assert len(results) == 1
        assert results[0].id == tx.id


# ── Tests: apply_fan_out ─────────────────────────────────────────────────────

class TestApplyFanOut:

    def test_copies_category_subcategory_context(self, session):
        """Fan-out should copy category, subcategory, and context from source."""
        source = _make_tx(
            session, description="esselunga", category="Alimentari",
            subcategory="Spesa supermercato", context="Quotidianità",
            category_source="manual", human_validated=True,
        )
        target = _make_tx(
            session, description="esselunga", category="Altro",
            category_source="llm",
        )
        n = apply_fan_out(session, source.id, [target.id])
        assert n == 1
        assert target.category == "Alimentari"
        assert target.subcategory == "Spesa supermercato"
        assert target.context == "Quotidianità"

    def test_sets_category_source_history(self, session):
        """Fan-out should set category_source='history' on targets."""
        source = _make_tx(
            session, description="conad", category="Alimentari",
            category_source="manual", human_validated=True,
        )
        target = _make_tx(
            session, description="conad", category_source="llm",
        )
        apply_fan_out(session, source.id, [target.id])
        assert target.category_source == "history"

    def test_sets_confidence_high(self, session):
        """Fan-out should set category_confidence='high'."""
        source = _make_tx(
            session, description="conad", category="Alimentari",
            category_source="manual", human_validated=True,
        )
        target = _make_tx(
            session, description="conad", category_source="llm",
        )
        apply_fan_out(session, source.id, [target.id])
        assert target.category_confidence == "high"

    def test_clears_to_review(self, session):
        """Fan-out should clear to_review flag."""
        source = _make_tx(
            session, description="conad", category="Alimentari",
            category_source="manual", human_validated=True,
        )
        target = _make_tx(
            session, description="conad", category_source="llm",
        )
        target.to_review = True
        session.flush()
        apply_fan_out(session, source.id, [target.id])
        assert target.to_review is False

    def test_does_not_overwrite_validated(self, session):
        """Fan-out should NOT overwrite already-validated transactions."""
        source = _make_tx(
            session, description="lidl", category="Alimentari",
            subcategory="Spesa", category_source="manual", human_validated=True,
        )
        target = _make_tx(
            session, description="lidl", category="Ristorazione",
            subcategory="Ristorante", category_source="llm",
            human_validated=True,
        )
        n = apply_fan_out(session, source.id, [target.id])
        assert n == 0
        assert target.category == "Ristorazione"
        assert target.subcategory == "Ristorante"

    def test_multiple_targets(self, session):
        """Fan-out should handle multiple targets at once."""
        source = _make_tx(
            session, description="coop", category="Alimentari",
            subcategory="Spesa", category_source="manual", human_validated=True,
        )
        targets = [
            _make_tx(session, description="coop", category_source="llm")
            for _ in range(5)
        ]
        n = apply_fan_out(session, source.id, [t.id for t in targets])
        assert n == 5
        for t in targets:
            assert t.category == "Alimentari"
            assert t.subcategory == "Spesa"
            assert t.category_source == "history"

    def test_source_not_found(self, session):
        """Fan-out returns 0 if source transaction doesn't exist."""
        target = _make_tx(
            session, description="test", category_source="llm",
        )
        n = apply_fan_out(session, "nonexistent_id_12345678", [target.id])
        assert n == 0

    def test_does_not_overwrite_context_when_source_has_none(self, session):
        """If source has no context, target's existing context is preserved."""
        source = _make_tx(
            session, description="coop", category="Alimentari",
            context=None, category_source="manual", human_validated=True,
        )
        target = _make_tx(
            session, description="coop", context="Vacanza",
            category_source="llm",
        )
        apply_fan_out(session, source.id, [target.id])
        assert target.category == "Alimentari"
        assert target.context == "Vacanza"  # preserved, not cleared

    def test_mixed_targets_validated_and_not(self, session):
        """Fan-out skips validated targets but updates non-validated ones."""
        source = _make_tx(
            session, description="lidl", category="Alimentari",
            category_source="manual", human_validated=True,
        )
        validated = _make_tx(
            session, description="lidl", category="Ristorazione",
            category_source="llm", human_validated=True,
        )
        not_validated = _make_tx(
            session, description="lidl", category="Altro",
            category_source="llm",
        )
        n = apply_fan_out(session, source.id, [validated.id, not_validated.id])
        assert n == 1
        assert validated.category == "Ristorazione"  # unchanged
        assert not_validated.category == "Alimentari"  # updated
