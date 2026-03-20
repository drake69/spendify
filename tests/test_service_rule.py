"""Tests for RuleService."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from db.models import Base, Transaction, get_session
from services.rule_service import RuleService


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def svc(engine):
    return RuleService(engine)


def _make_tx(session, *, tx_id: str, description: str, amount: float = -10.0,
             tx_type: str = "expense", category: str = "Altro",
             to_review: bool = False) -> Transaction:
    t = Transaction(
        id=tx_id,
        date="2025-01-01",
        description=description,
        amount=amount,
        currency="EUR",
        tx_type=tx_type,
        category=category,
        subcategory="",
        category_source="llm",
        category_confidence="medium",
        to_review=to_review,
        account_label="test",
    )
    session.add(t)
    session.commit()
    return t


# ── CategoryRule tests ────────────────────────────────────────────────────────

def test_get_rules_empty(svc):
    assert svc.get_rules() == []


def test_create_rule(svc):
    rule, created = svc.create_rule(
        pattern="netflix", match_type="contains",
        category="Intrattenimento", subcategory="Streaming",
    )
    assert created is True
    assert rule.pattern == "netflix"
    rules = svc.get_rules()
    assert len(rules) == 1
    assert rules[0].category == "Intrattenimento"


def test_create_rule_duplicate(svc):
    svc.create_rule(
        pattern="netflix", match_type="contains",
        category="Intrattenimento", subcategory="Streaming",
    )
    rule, created = svc.create_rule(
        pattern="netflix", match_type="contains",
        category="Svago", subcategory="Film",
    )
    assert created is False
    assert rule.category == "Svago"


def test_update_rule(svc):
    rule, _ = svc.create_rule(
        pattern="amazon", match_type="contains",
        category="Shopping", subcategory="Online",
    )
    ok = svc.update_rule(rule.id, category="E-commerce", priority=10)
    assert ok is True
    rules = svc.get_rules()
    assert rules[0].category == "E-commerce"
    assert rules[0].priority == 10


def test_delete_rule(svc):
    rule, _ = svc.create_rule(
        pattern="spotify", match_type="contains",
        category="Intrattenimento", subcategory="Musica",
    )
    ok = svc.delete_rule(rule.id)
    assert ok is True
    assert svc.get_rules() == []


def test_delete_rule_not_found(svc):
    ok = svc.delete_rule(999)
    assert ok is False


def test_apply_to_review(engine):
    svc = RuleService(engine)
    # Seed a to_review transaction
    with get_session(engine) as s:
        _make_tx(s, tx_id="t1", description="netflix abbonamento",
                 category="Altro", to_review=True)
    # Create a matching rule
    svc.create_rule(
        pattern="netflix", match_type="contains",
        category="Intrattenimento", subcategory="Streaming", priority=10,
    )
    n = svc.apply_to_review()
    assert n == 1
    # Verify the transaction was categorized
    with get_session(engine) as s:
        tx = s.get(Transaction, "t1")
        assert tx.category == "Intrattenimento"
        assert tx.to_review is False


def test_apply_to_all(engine):
    svc = RuleService(engine)
    with get_session(engine) as s:
        _make_tx(s, tx_id="t1", description="enel energia",
                 category="Altro", to_review=False)
        _make_tx(s, tx_id="t2", description="enel energia",
                 category="Altro", to_review=True)
    svc.create_rule(
        pattern="enel", match_type="contains",
        category="Utenze", subcategory="Elettricità", priority=10,
    )
    n_matched, n_cleared = svc.apply_to_all()
    assert n_matched == 2
    assert n_cleared == 1
    with get_session(engine) as s:
        t1 = s.get(Transaction, "t1")
        t2 = s.get(Transaction, "t2")
        assert t1.category == "Utenze"
        assert t2.category == "Utenze"
        assert t2.to_review is False


# ── DescriptionRule tests ─────────────────────────────────────────────────────

def test_get_description_rules_empty(svc):
    assert svc.get_description_rules() == []


def test_create_description_rule(svc):
    rule, created = svc.create_description_rule(
        raw_pattern="NETFLIX PAYMENT", match_type="exact",
        cleaned_description="Netflix",
    )
    assert created is True
    assert rule.cleaned_description == "Netflix"
    rules = svc.get_description_rules()
    assert len(rules) == 1


def test_create_description_rule_duplicate(svc):
    svc.create_description_rule(
        raw_pattern="AMAZON", match_type="contains",
        cleaned_description="Amazon",
    )
    rule, created = svc.create_description_rule(
        raw_pattern="AMAZON", match_type="contains",
        cleaned_description="Amazon Prime",
    )
    assert created is False
    assert rule.cleaned_description == "Amazon Prime"


def test_delete_description_rule(svc):
    rule, _ = svc.create_description_rule(
        raw_pattern="SPOTIFY", match_type="exact",
        cleaned_description="Spotify",
    )
    ok = svc.delete_description_rule(rule.id)
    assert ok is True
    assert svc.get_description_rules() == []


def test_delete_description_rule_not_found(svc):
    ok = svc.delete_description_rule(999)
    assert ok is False
