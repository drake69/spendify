"""Tests for rule upsert, apply, giroconto toggle and bulk operations in db/repository.py."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from db.models import Base, Transaction, get_session
from db.repository import (
    apply_all_rules_to_all_transactions,
    apply_rules_to_review_transactions,
    bulk_set_giroconto_by_description,
    create_category_rule,
    get_category_rules,
    get_transactions_by_rule_pattern,
    toggle_transaction_giroconto,
    update_transaction_category,
)
from core.categorizer import CategoryRule as CoreCategoryRule


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine):
    with get_session(engine) as s:
        yield s


def _tx(session, *, tx_id: str, description: str, amount: float = -10.0,
        tx_type: str = "expense", category: str = "Altro",
        subcategory: str = "", category_source: str = "llm",
        to_review: bool = False) -> Transaction:
    """Helper to insert a minimal Transaction."""
    t = Transaction(
        id=tx_id,
        date="2025-01-01",
        description=description,
        amount=amount,
        currency="EUR",
        tx_type=tx_type,
        category=category,
        subcategory=subcategory,
        category_source=category_source,
        category_confidence="medium",
        to_review=to_review,
        account_label="test",
    )
    session.add(t)
    session.flush()
    return t


# ── Rule upsert ───────────────────────────────────────────────────────────────

class TestCreateCategoryRuleUpsert:
    def test_creates_new_rule(self, session):
        rule, created = create_category_rule(
            session, pattern="netflix", match_type="contains",
            category="Intrattenimento", subcategory="Streaming",
        )
        assert created is True
        assert rule.id is not None
        assert rule.pattern == "netflix"

    def test_upsert_same_pattern_and_match_type(self, session):
        create_category_rule(
            session, pattern="netflix", match_type="contains",
            category="Intrattenimento", subcategory="Streaming", priority=5,
        )
        rule, created = create_category_rule(
            session, pattern="netflix", match_type="contains",
            category="Svago", subcategory="Film", priority=20,
        )
        assert created is False
        assert rule.category == "Svago"
        assert rule.subcategory == "Film"
        assert rule.priority == 20

    def test_different_match_type_creates_new(self, session):
        _, c1 = create_category_rule(
            session, pattern="netflix", match_type="contains",
            category="Cat1", subcategory="",
        )
        _, c2 = create_category_rule(
            session, pattern="netflix", match_type="exact",
            category="Cat2", subcategory="",
        )
        assert c1 is True
        assert c2 is True

    def test_priority_updated_on_upsert(self, session):
        create_category_rule(session, pattern="x", match_type="contains",
                             category="A", subcategory="", priority=5)
        rule, _ = create_category_rule(session, pattern="x", match_type="contains",
                                       category="A", subcategory="", priority=99)
        assert rule.priority == 99


# ── get_transactions_by_rule_pattern ─────────────────────────────────────────

class TestGetTransactionsByRulePattern:
    def test_contains_match(self, session):
        _tx(session, tx_id="t1", description="pagamento liquigas srl")
        _tx(session, tx_id="t2", description="liquigas mensile")
        _tx(session, tx_id="t3", description="esselunga spa")

        result = get_transactions_by_rule_pattern(session, "liquigas", "contains")
        ids = {t.id for t in result}
        assert "t1" in ids
        assert "t2" in ids
        assert "t3" not in ids

    def test_contains_case_insensitive(self, session):
        _tx(session, tx_id="t1", description="NETFLIX ABBONAMENTO")
        result = get_transactions_by_rule_pattern(session, "netflix", "contains")
        assert any(t.id == "t1" for t in result)

    def test_exact_match(self, session):
        _tx(session, tx_id="t1", description="stipendio")
        _tx(session, tx_id="t2", description="bonifico stipendio")
        result = get_transactions_by_rule_pattern(session, "stipendio", "exact")
        ids = {t.id for t in result}
        assert "t1" in ids
        assert "t2" not in ids

    def test_regex_match(self, session):
        _tx(session, tx_id="t1", description="telepass autostrade")
        _tx(session, tx_id="t2", description="parcheggio telepass")
        _tx(session, tx_id="t3", description="benzina eni")
        result = get_transactions_by_rule_pattern(session, r"\btelepass\b", "regex")
        ids = {t.id for t in result}
        assert "t1" in ids
        assert "t2" in ids
        assert "t3" not in ids

    def test_matches_llm_sourced_transactions(self, session):
        """Regression: used to skip category_source=llm transactions."""
        _tx(session, tx_id="t1", description="liquigas srl", category_source="llm")
        _tx(session, tx_id="t2", description="liquigas srl", category_source="rule")
        _tx(session, tx_id="t3", description="liquigas srl", category_source="manual")
        result = get_transactions_by_rule_pattern(session, "liquigas", "contains")
        ids = {t.id for t in result}
        assert ids == {"t1", "t2", "t3"}


# ── apply_rules_to_review_transactions ───────────────────────────────────────

class TestApplyRulesToReviewTransactions:
    def _make_rule(self, pattern, category, subcategory="", match_type="contains",
                   priority=10, doc_type=None) -> CoreCategoryRule:
        return CoreCategoryRule(
            id=1, pattern=pattern, match_type=match_type,
            category=category, subcategory=subcategory,
            doc_type=doc_type, priority=priority,
        )

    def test_applies_to_review_transactions(self, session):
        _tx(session, tx_id="t1", description="netflix abbonamento",
            category="Altro", to_review=True)
        rule = self._make_rule("netflix", "Intrattenimento", "Streaming")
        n = apply_rules_to_review_transactions(session, [rule])
        assert n == 1
        tx = session.get(Transaction, "t1")
        assert tx.category == "Intrattenimento"
        assert tx.to_review is False
        assert tx.category_source == "rule"

    def test_skips_non_review_transactions(self, session):
        _tx(session, tx_id="t1", description="netflix abbonamento",
            category="Altro", to_review=False)
        rule = self._make_rule("netflix", "Intrattenimento")
        n = apply_rules_to_review_transactions(session, [rule])
        assert n == 0

    def test_highest_priority_rule_wins(self, session):
        _tx(session, tx_id="t1", description="amazon prime video", to_review=True)
        low = self._make_rule("amazon", "Shopping", priority=5)
        high = self._make_rule("prime video", "Intrattenimento", "Streaming", priority=20)
        n = apply_rules_to_review_transactions(session, [low, high])
        assert n == 1
        tx = session.get(Transaction, "t1")
        assert tx.category == "Intrattenimento"

    def test_no_rules_returns_zero(self, session):
        _tx(session, tx_id="t1", description="something", to_review=True)
        assert apply_rules_to_review_transactions(session, []) == 0

    def test_no_review_transactions_returns_zero(self, session):
        rule = self._make_rule("netflix", "Intrattenimento")
        assert apply_rules_to_review_transactions(session, [rule]) == 0


# ── toggle_transaction_giroconto ──────────────────────────────────────────────

class TestToggleTransactionGiroconto:
    def test_marks_expense_as_internal_out(self, session):
        _tx(session, tx_id="t1", description="bonifico a mario",
            amount=-100.0, tx_type="expense")
        ok, new_type = toggle_transaction_giroconto(session, "t1")
        assert ok is True
        assert new_type == "internal_out"
        assert session.get(Transaction, "t1").tx_type == "internal_out"

    def test_marks_income_as_internal_in(self, session):
        _tx(session, tx_id="t1", description="bonifico da mario",
            amount=100.0, tx_type="income")
        ok, new_type = toggle_transaction_giroconto(session, "t1")
        assert ok is True
        assert new_type == "internal_in"

    def test_reverts_internal_out_to_expense(self, session):
        _tx(session, tx_id="t1", description="giroconto",
            amount=-50.0, tx_type="internal_out")
        ok, new_type = toggle_transaction_giroconto(session, "t1")
        assert ok is True
        assert new_type == "expense"

    def test_reverts_internal_in_to_income(self, session):
        _tx(session, tx_id="t1", description="giroconto entrata",
            amount=50.0, tx_type="internal_in")
        ok, new_type = toggle_transaction_giroconto(session, "t1")
        assert ok is True
        assert new_type == "income"

    def test_not_found_returns_false(self, session):
        ok, new_type = toggle_transaction_giroconto(session, "nonexistent")
        assert ok is False
        assert new_type == ""


# ── bulk_set_giroconto_by_description ────────────────────────────────────────

class TestBulkSetGirocontoByDescription:
    def test_marks_all_matching_as_giroconto(self, session):
        _tx(session, tx_id="t1", description="romantica srl", amount=-2.5, tx_type="card_tx")
        _tx(session, tx_id="t2", description="romantica srl", amount=-2.5, tx_type="card_tx")
        _tx(session, tx_id="t3", description="altro merchant", amount=-5.0, tx_type="expense")

        n = bulk_set_giroconto_by_description(session, "romantica srl", make_giroconto=True)
        assert n == 2
        assert session.get(Transaction, "t1").tx_type == "internal_out"
        assert session.get(Transaction, "t2").tx_type == "internal_out"
        assert session.get(Transaction, "t3").tx_type == "expense"  # unchanged

    def test_reverts_all_giroconti_by_description(self, session):
        _tx(session, tx_id="t1", description="giroconto banca",
            amount=-100.0, tx_type="internal_out")
        _tx(session, tx_id="t2", description="giroconto banca",
            amount=-100.0, tx_type="internal_out")

        n = bulk_set_giroconto_by_description(session, "giroconto banca", make_giroconto=False)
        assert n == 2
        assert session.get(Transaction, "t1").tx_type == "expense"
        assert session.get(Transaction, "t2").tx_type == "expense"

    def test_excludes_given_id(self, session):
        _tx(session, tx_id="t1", description="romantica", amount=-2.5, tx_type="expense")
        _tx(session, tx_id="t2", description="romantica", amount=-2.5, tx_type="expense")

        n = bulk_set_giroconto_by_description(
            session, "romantica", make_giroconto=True, exclude_id="t1"
        )
        assert n == 1
        assert session.get(Transaction, "t1").tx_type == "expense"   # excluded
        assert session.get(Transaction, "t2").tx_type == "internal_out"

    def test_skips_already_correct_type(self, session):
        _tx(session, tx_id="t1", description="test",
            amount=-10.0, tx_type="internal_out")  # already giroconto

        n = bulk_set_giroconto_by_description(session, "test", make_giroconto=True)
        assert n == 0

    def test_returns_zero_when_no_match(self, session):
        n = bulk_set_giroconto_by_description(session, "nessuna desc", make_giroconto=True)
        assert n == 0


# ── apply_all_rules_to_all_transactions ──────────────────────────────────────

class TestApplyAllRulesToAllTransactions:
    def _rule(self, pattern, category, subcategory="", match_type="contains",
              priority=10, doc_type=None) -> CoreCategoryRule:
        return CoreCategoryRule(
            id=1, pattern=pattern, match_type=match_type,
            category=category, subcategory=subcategory,
            doc_type=doc_type, priority=priority,
        )

    def test_applies_to_all_including_non_review(self, session):
        _tx(session, tx_id="t1", description="netflix abbonamento",
            category="Altro", to_review=False)
        _tx(session, tx_id="t2", description="netflix abbonamento",
            category="Altro", to_review=True)
        rule = self._rule("netflix", "Intrattenimento", "Streaming")
        n_matched, n_cleared = apply_all_rules_to_all_transactions(session, [rule])
        assert n_matched == 2
        assert n_cleared == 1   # only the to_review=True one is "cleared"
        t1 = session.get(Transaction, "t1")
        t2 = session.get(Transaction, "t2")
        assert t1.category == "Intrattenimento"
        assert t2.category == "Intrattenimento"
        assert t2.to_review is False
        assert t1.to_review is False   # was already False, stays False

    def test_returns_zero_zero_when_no_rules(self, session):
        _tx(session, tx_id="t1", description="something", to_review=True)
        assert apply_all_rules_to_all_transactions(session, []) == (0, 0)

    def test_returns_zero_zero_when_no_transactions(self, session):
        rule = self._rule("netflix", "Intrattenimento")
        assert apply_all_rules_to_all_transactions(session, [rule]) == (0, 0)

    def test_first_match_wins_highest_priority(self, session):
        _tx(session, tx_id="t1", description="amazon prime video", to_review=False)
        low  = self._rule("amazon",      "Shopping",        priority=5)
        high = self._rule("prime video", "Intrattenimento", priority=20)
        n_matched, _ = apply_all_rules_to_all_transactions(session, [low, high])
        assert n_matched == 1
        tx = session.get(Transaction, "t1")
        assert tx.category == "Intrattenimento"   # higher priority wins

    def test_unmatched_transactions_left_unchanged(self, session):
        _tx(session, tx_id="t1", description="amazon prime video", category="Shopping")
        _tx(session, tx_id="t2", description="esselunga supermercato", category="Altro")
        rule = self._rule("prime video", "Intrattenimento")
        n_matched, _ = apply_all_rules_to_all_transactions(session, [rule])
        assert n_matched == 1
        assert session.get(Transaction, "t2").category == "Altro"

    def test_category_source_set_to_rule(self, session):
        _tx(session, tx_id="t1", description="enel energia", category_source="llm")
        rule = self._rule("enel", "Utenze", "Elettricità")
        apply_all_rules_to_all_transactions(session, [rule])
        tx = session.get(Transaction, "t1")
        assert tx.category_source == "rule"
        assert tx.category_confidence == "high"

    def test_n_cleared_counts_only_previously_to_review(self, session):
        _tx(session, tx_id="t1", description="enel energia", to_review=True)
        _tx(session, tx_id="t2", description="enel energia", to_review=False)
        _tx(session, tx_id="t3", description="enel energia", to_review=True)
        rule = self._rule("enel", "Utenze")
        n_matched, n_cleared = apply_all_rules_to_all_transactions(session, [rule])
        assert n_matched == 3
        assert n_cleared == 2

    def test_empty_rules_list_no_db_flush(self, session):
        """No flush should occur (and no error) when rules list is empty."""
        _tx(session, tx_id="t1", description="test", to_review=True)
        result = apply_all_rules_to_all_transactions(session, [])
        assert result == (0, 0)
        # transaction unchanged
        assert session.get(Transaction, "t1").category == "Altro"
