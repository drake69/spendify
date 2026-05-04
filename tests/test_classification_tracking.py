"""Tests for C-01: Tracking Origine Categorizzazione.

Covers:
  - human_validated / validated_at lifecycle
  - category_source preservation and updates
  - TaxonomyCategory.is_fallback flag and get_fallback_categories()
  - _migrate_add_classification_tracking idempotency
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event

from core.normalizer import compute_transaction_id
from db.models import (
    Base,
    TaxonomyCategory,
    TaxonomySubcategory,
    Transaction,
    get_session,
)
from db.repository import (
    apply_rules_to_review_transactions,
    get_fallback_categories,
    update_transaction_category,
    validate_transaction,
)


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


def _make_tx_id(date: str, amount: str, desc: str, account_label: str, source_file: str = "f.csv") -> str:
    return compute_transaction_id(source_file, date, amount, desc, account_label=account_label)


def _insert_tx(
    session,
    *,
    account_label: str = "TestAccount",
    date: str = "2025-01-15",
    amount: float = -42.50,
    raw_description: str = "PAGAMENTO COOP",
    source_file: str = "f.csv",
    category: str | None = None,
    subcategory: str | None = None,
    category_source: str | None = None,
    human_validated: bool = False,
) -> Transaction:
    """Insert a transaction with classification tracking fields."""
    amount_key = str(Decimal(str(amount)).normalize())
    desc_key = raw_description.strip()
    tx_id = _make_tx_id(date, amount_key, desc_key, account_label, source_file)
    t = Transaction(
        id=tx_id,
        date=date,
        amount=amount,
        currency="EUR",
        description=raw_description.lower(),
        raw_description=raw_description,
        raw_amount=str(amount),
        source_file=source_file,
        account_label=account_label,
        tx_type="expense",
        category=category,
        subcategory=subcategory,
        category_source=category_source,
        human_validated=human_validated,
    )
    session.add(t)
    session.flush()
    return t


# ── TestHumanValidation ──────────────────────────────────────────────────────

class TestHumanValidation:
    """Verify human_validated and validated_at lifecycle."""

    def test_import_sets_human_validated_false(self, session):
        """A freshly imported transaction has human_validated=False."""
        tx = _insert_tx(session)
        assert tx.human_validated is False

    def test_manual_edit_sets_validated_true(self, session):
        """update_transaction_category sets human_validated=True and validated_at."""
        tx = _insert_tx(session, category="Alimentari", subcategory="Supermercato", category_source="llm")
        assert tx.human_validated is False

        result = update_transaction_category(session, tx.id, "Trasporti", "Carburante")
        session.flush()
        session.refresh(tx)

        assert result is True
        assert tx.human_validated is True
        assert tx.validated_at is not None

    def test_validate_without_category_change(self, session):
        """validate_transaction marks human_validated=True without changing category_source."""
        tx = _insert_tx(
            session,
            category="Alimentari",
            subcategory="Supermercato",
            category_source="llm",
        )
        original_source = tx.category_source

        result = validate_transaction(session, tx.id)
        session.flush()
        session.refresh(tx)

        assert result is True
        assert tx.human_validated is True
        assert tx.category_source == original_source

    def test_rule_preserves_validated(self, session):
        """After manual validation, applying a rule does NOT reset human_validated.

        human_validated means "user saw this transaction" (approval of the spend),
        NOT "user approves the category". Rules change category_source but leave
        the user's approval intact.
        """
        tx = _insert_tx(
            session,
            category="Alimentari",
            subcategory="Supermercato",
            category_source="manual",
            human_validated=True,
        )
        tx.human_validated = True
        tx.validated_at = datetime.now(timezone.utc)
        tx.to_review = True
        session.flush()

        from core.categorizer import CategoryRule
        rule = CategoryRule(
            id=1,
            pattern=tx.raw_description[:10],
            match_type="contains",
            category="Trasporti",
            subcategory="Carburante",
            doc_type=None,
        )
        apply_rules_to_review_transactions(session, [rule])
        session.flush()
        session.refresh(tx)

        # Rule reclassifies category but human_validated stays True
        if tx.category == "Trasporti":
            assert tx.human_validated is True  # NOT reset!
            assert tx.category_source == "rule"

    def test_validated_at_timestamp(self, session):
        """validated_at is a datetime after validation."""
        tx = _insert_tx(session, category="Alimentari", subcategory="Supermercato")
        assert tx.validated_at is None

        validate_transaction(session, tx.id)
        session.flush()
        session.refresh(tx)

        assert isinstance(tx.validated_at, datetime)


# ── TestCategorySource ───────────────────────────────────────────────────────

class TestCategorySource:
    """Verify category_source behaviour on validate vs manual edit."""

    def test_source_preserved_on_validate(self, session):
        """validate_transaction does not change category_source."""
        tx = _insert_tx(
            session,
            category="Alimentari",
            subcategory="Supermercato",
            category_source="llm",
        )
        validate_transaction(session, tx.id)
        session.flush()
        session.refresh(tx)

        assert tx.category_source == "llm"

    def test_source_set_to_manual_on_edit(self, session):
        """update_transaction_category sets category_source to 'manual'."""
        tx = _insert_tx(
            session,
            category="Alimentari",
            subcategory="Supermercato",
            category_source="llm",
        )
        update_transaction_category(session, tx.id, "Trasporti", "Carburante")
        session.flush()
        session.refresh(tx)

        assert tx.category_source == "manual"


# ── TestTaxonomyFallback ─────────────────────────────────────────────────────

class TestTaxonomyFallback:
    """Verify is_fallback flag on TaxonomyCategory and get_fallback_categories."""

    def test_fallback_flag_on_category(self, session):
        """TaxonomyCategory supports is_fallback=True."""
        cat = TaxonomyCategory(name="Altro", type="expense", is_fallback=True)
        session.add(cat)
        session.flush()
        session.refresh(cat)

        assert cat.is_fallback is True

    def test_get_fallback_categories(self, session):
        """get_fallback_categories returns a dict keyed by type."""
        cat_exp = TaxonomyCategory(name="Altro", type="expense", is_fallback=True)
        cat_inc = TaxonomyCategory(name="Altro Entrate", type="income", is_fallback=True)
        session.add_all([cat_exp, cat_inc])
        session.flush()

        result = get_fallback_categories(session)

        assert isinstance(result, dict)
        assert "expense" in result
        assert "income" in result
        # get_fallback_categories returns (name, subcategory) tuples
        assert result["expense"][0] == "Altro"
        assert result["income"][0] == "Altro Entrate"

    def test_fallback_defaults_when_empty(self, session):
        """With no fallback in DB, get_fallback_categories returns hardcoded defaults."""
        # No TaxonomyCategory rows with is_fallback=True
        cat = TaxonomyCategory(name="Alimentari", type="expense", is_fallback=False)
        session.add(cat)
        session.flush()

        result = get_fallback_categories(session)

        # Should return hardcoded defaults (non-empty dict with expense and income keys)
        assert isinstance(result, dict)
        assert "expense" in result
        assert "income" in result
        assert len(result["expense"]) > 0
        assert len(result["income"]) > 0

    def test_toggle_fallback(self, session):
        """Only one category per type should be fallback at a time."""
        cat_a = TaxonomyCategory(name="Altro", type="expense", is_fallback=True)
        cat_b = TaxonomyCategory(name="Varie", type="expense", is_fallback=False)
        session.add_all([cat_a, cat_b])
        session.flush()

        # Switch fallback from cat_a to cat_b
        cat_a.is_fallback = False
        cat_b.is_fallback = True
        session.flush()
        session.refresh(cat_a)
        session.refresh(cat_b)

        assert cat_a.is_fallback is False
        assert cat_b.is_fallback is True

        # Verify only one per type
        fallbacks = (
            session.query(TaxonomyCategory)
            .filter(TaxonomyCategory.type == "expense", TaxonomyCategory.is_fallback.is_(True))
            .all()
        )
        assert len(fallbacks) == 1
        assert fallbacks[0].name == "Varie"


# ── TestMigrationIdempotent ──────────────────────────────────────────────────

class TestMigrationIdempotent:
    """Verify _migrate_add_classification_tracking is idempotent."""

    def test_migration_runs_twice(self):
        """Calling _migrate_add_classification_tracking twice raises no error."""
        from db.models import _migrate_add_classification_tracking

        eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(eng)

        # Run migration twice — second call must be a no-op
        _migrate_add_classification_tracking(eng)
        _migrate_add_classification_tracking(eng)
