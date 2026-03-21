"""Tests for TransactionService."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from db.models import Base, Transaction, get_session
from services.transaction_service import TransactionService


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def svc(engine):
    return TransactionService(engine)


def _make_tx(session, *, tx_id: str, description: str, amount: float = -10.0,
             tx_type: str = "expense", category: str = "Altro",
             subcategory: str = "", account_label: str = "test",
             to_review: bool = False, raw_description: str | None = None) -> Transaction:
    t = Transaction(
        id=tx_id,
        date="2025-01-01",
        description=description,
        amount=amount,
        currency="EUR",
        tx_type=tx_type,
        category=category,
        subcategory=subcategory,
        category_source="llm",
        category_confidence="medium",
        to_review=to_review,
        account_label=account_label,
        raw_description=raw_description,
    )
    session.add(t)
    session.commit()
    return t


@pytest.fixture
def seeded_engine(engine):
    """Engine with 3 transactions pre-seeded."""
    with get_session(engine) as s:
        _make_tx(s, tx_id="t1", description="pagamento netflix", amount=-12.0)
        _make_tx(s, tx_id="t2", description="stipendio mensile", amount=2000.0,
                 tx_type="income", category="Reddito")
        _make_tx(s, tx_id="t3", description="pagamento amazon", amount=-50.0)
    return engine


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_get_transactions_empty(svc):
    result = svc.get_transactions()
    assert result == []


def test_get_transactions_returns_seeded(seeded_engine):
    svc = TransactionService(seeded_engine)
    result = svc.get_transactions()
    assert len(result) == 3


def test_update_category(seeded_engine):
    svc = TransactionService(seeded_engine)
    ok = svc.update_category("t1", "Intrattenimento", "Streaming")
    assert ok is True
    with get_session(seeded_engine) as s:
        tx = s.get(Transaction, "t1")
        assert tx.category == "Intrattenimento"
        assert tx.subcategory == "Streaming"
        assert tx.category_source == "manual"


def test_update_context(seeded_engine):
    svc = TransactionService(seeded_engine)
    ok = svc.update_context("t1", "Vacanza")
    assert ok is True
    with get_session(seeded_engine) as s:
        tx = s.get(Transaction, "t1")
        assert tx.context == "Vacanza"


def test_update_context_missing_tx(svc):
    ok = svc.update_context("nonexistent", "Vacanza")
    assert ok is False


def test_toggle_giroconto(seeded_engine):
    svc = TransactionService(seeded_engine)
    ok, new_type = svc.toggle_giroconto("t1")
    assert ok is True
    assert new_type == "internal_out"  # t1 is negative amount
    with get_session(seeded_engine) as s:
        tx = s.get(Transaction, "t1")
        assert tx.tx_type == "internal_out"


def test_get_similar_transactions(seeded_engine):
    svc = TransactionService(seeded_engine)
    # "pagamento netflix" and "pagamento amazon" share the word "pagamento"
    result = svc.get_similar("pagamento netflix", exclude_id="t1", threshold=0.3)
    ids = {tx.id for tx in result}
    # t3 has "pagamento amazon" — shares "pagamento" word → Jaccard >= 0.3
    assert "t3" in ids
    # t1 is excluded
    assert "t1" not in ids


def test_bulk_set_giroconto_by_description(seeded_engine):
    svc = TransactionService(seeded_engine)
    # Add two transactions with the same description
    with get_session(seeded_engine) as s:
        _make_tx(s, tx_id="g1", description="giroconto banca", amount=-100.0)
        _make_tx(s, tx_id="g2", description="giroconto banca", amount=-100.0)
    n = svc.bulk_set_giroconto_by_description("giroconto banca", make_giroconto=True, exclude_id="")
    assert n == 2
    with get_session(seeded_engine) as s:
        assert s.get(Transaction, "g1").tx_type == "internal_out"
        assert s.get(Transaction, "g2").tx_type == "internal_out"


def test_delete_by_filter(seeded_engine):
    svc = TransactionService(seeded_engine)
    # Delete t1 by account_label
    n = svc.delete_by_filter({"account_label": "test", "tx_type": "expense"})
    assert n == 2  # t1 and t3 are both expense with account_label=test
    result = svc.get_transactions()
    ids = {tx.id for tx in result}
    assert "t1" not in ids
    assert "t3" not in ids
    assert "t2" in ids


def test_get_cross_account_duplicates(seeded_engine):
    svc = TransactionService(seeded_engine)
    # No cross-account duplicates in the seeded data
    result = svc.get_cross_account_duplicates()
    assert result == []


def test_get_by_rule_pattern(seeded_engine):
    svc = TransactionService(seeded_engine)
    result = svc.get_by_rule_pattern("netflix", "contains")
    assert len(result) == 1
    assert result[0].id == "t1"


def test_get_by_raw_pattern(seeded_engine):
    svc = TransactionService(seeded_engine)
    # Add a transaction with raw_description
    with get_session(seeded_engine) as s:
        _make_tx(s, tx_id="r1", description="cleaned desc",
                 raw_description="RAW NETFLIX PAYMENT", amount=-15.0)
    result = svc.get_by_raw_pattern("netflix", "contains")
    assert len(result) == 1
    assert result[0].id == "r1"
