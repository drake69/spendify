"""Tests for C-03/C-04/C-05: History-based auto-learning engine.

Covers:
  - Empty history → no associations
  - Single-category homogeneous description
  - Mixed-category description
  - Minimum validated scaling
  - Lookup auto (confidence >= 0.90)
  - Lookup suggest (0.50 <= confidence < 0.90)
  - Lookup no match
  - Entropy for single category (= 0.0)
  - Entropy for uniform distribution (= 1.0)
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event

from core.history_engine import (
    HISTORY_AUTO_THRESHOLD,
    HISTORY_MIN_VALIDATED,
    HISTORY_SUGGEST_THRESHOLD,
    DescriptionAssociation,
    DescriptionProfile,
    HistoryCache,
    _shannon_entropy,
    get_associations,
    get_description_profiles,
    lookup_history,
)
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


def _insert_validated_tx(
    session,
    *,
    description: str,
    category: str,
    subcategory: str | None = None,
    amount: float = -10.0,
    date: str = "2025-01-15",
    account_label: str = "TestAccount",
    source_file: str = "f.csv",
):
    """Insert a validated transaction for history engine tests."""
    global _SEQ
    _SEQ += 1
    # Use a unique amount to generate unique tx IDs
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
        human_validated=True,
        validated_at=datetime.now(timezone.utc),
    )
    session.add(t)
    session.flush()
    return t


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestEmptyHistory:
    def test_empty_history(self, session):
        """No validated transactions → no associations."""
        assocs = get_associations(session)
        assert assocs == []

        profiles = get_description_profiles(session)
        assert profiles == []


class TestSingleCategoryHomogeneous:
    def test_single_category_homogeneous(self, session):
        """5 validated ESSELUNGA→Alimentari → homogeneity=1.0, confidence=1.0."""
        for _ in range(5):
            _insert_validated_tx(
                session,
                description="esselunga",
                category="Alimentari",
                subcategory="Spesa supermercato",
            )

        profiles = get_description_profiles(session)
        assert len(profiles) == 1
        p = profiles[0]
        assert p.description == "esselunga"
        assert p.top_category == "Alimentari"
        assert p.top_subcategory == "Spesa supermercato"
        assert p.total_validated == 5
        assert p.homogeneity == 1.0
        assert p.confidence == 1.0


class TestMixedCategories:
    def test_mixed_categories(self, session):
        """AMAZON with 5 Tecnologia + 3 Alimentari → homogeneity < 0.5 is not guaranteed,
        but entropy > 0 and homogeneity < 1.0."""
        for _ in range(5):
            _insert_validated_tx(
                session,
                description="amazon",
                category="Tecnologia",
                subcategory="Elettronica",
            )
        for _ in range(3):
            _insert_validated_tx(
                session,
                description="amazon",
                category="Alimentari",
                subcategory="Spesa supermercato",
            )

        profiles = get_description_profiles(session)
        assert len(profiles) == 1
        p = profiles[0]
        assert p.description == "amazon"
        assert p.top_category == "Tecnologia"  # 5 > 3
        assert p.total_validated == 8
        assert 0.0 < p.homogeneity < 1.0
        # With 5:3 ratio, entropy is not 0 and not 1
        assert p.confidence < 1.0


class TestMinValidatedScaling:
    def test_min_validated_scaling(self, session):
        """2 validated → confidence scaled down by factor 2/HISTORY_MIN_VALIDATED."""
        for _ in range(2):
            _insert_validated_tx(
                session,
                description="lidl",
                category="Alimentari",
                subcategory="Spesa supermercato",
            )

        profiles = get_description_profiles(session)
        assert len(profiles) == 1
        p = profiles[0]
        assert p.total_validated == 2
        assert p.homogeneity == 1.0  # single category → perfect homogeneity
        expected_confidence = 1.0 * min(1.0, 2 / HISTORY_MIN_VALIDATED)
        assert abs(p.confidence - expected_confidence) < 1e-9
        assert p.confidence < 1.0  # scaled down because < HISTORY_MIN_VALIDATED


class TestLookupAuto:
    def test_lookup_auto(self, session):
        """confidence >= 0.90 → returns category."""
        for _ in range(HISTORY_MIN_VALIDATED):
            _insert_validated_tx(
                session,
                description="conad",
                category="Alimentari",
                subcategory="Spesa supermercato",
            )

        cat, subcat, conf = lookup_history(session, "conad")
        assert cat == "Alimentari"
        assert subcat == "Spesa supermercato"
        assert conf >= HISTORY_AUTO_THRESHOLD


class TestLookupSuggest:
    def test_lookup_suggest(self, session):
        """0.50 <= confidence < 0.90 → returns with to_review semantics."""
        # 3 in one category, 1 in another → some entropy
        for _ in range(3):
            _insert_validated_tx(
                session,
                description="mercato misto",
                category="Alimentari",
                subcategory="Spesa supermercato",
            )
        _insert_validated_tx(
            session,
            description="mercato misto",
            category="Ristorazione",
            subcategory="Ristorante",
        )

        cat, subcat, conf = lookup_history(session, "mercato misto")
        assert cat == "Alimentari"  # top category
        # Confidence should be moderate (scaled by 4/5 and reduced by entropy)
        assert conf > 0.0
        # With 4 total (< HISTORY_MIN_VALIDATED), confidence is further scaled down


class TestLookupNoMatch:
    def test_lookup_no_match(self, session):
        """Unknown description → returns None."""
        cat, subcat, conf = lookup_history(session, "descrizione_inesistente_xyz")
        assert cat is None
        assert subcat is None
        assert conf == 0.0


class TestEntropySingleCategory:
    def test_entropy_single_category(self, session):
        """Entropy = 0 for a single category (uniform = one bucket)."""
        entropy = _shannon_entropy([10])
        assert entropy == 0.0

    def test_entropy_multiple_same(self):
        """Entropy = 0 when only one category has all counts."""
        entropy = _shannon_entropy([100])
        assert entropy == 0.0


class TestEntropyUniform:
    def test_entropy_uniform(self):
        """Entropy = 1.0 for perfectly uniform distribution."""
        # 4 categories each with 25 occurrences
        entropy = _shannon_entropy([25, 25, 25, 25])
        assert abs(entropy - 1.0) < 1e-9

    def test_entropy_two_equal(self):
        """Entropy = 1.0 for two categories equally distributed."""
        entropy = _shannon_entropy([50, 50])
        assert abs(entropy - 1.0) < 1e-9


class TestHistoryCache:
    def test_cache_matches_lookup(self, session):
        """HistoryCache returns same results as lookup_history."""
        for _ in range(HISTORY_MIN_VALIDATED):
            _insert_validated_tx(
                session,
                description="coop",
                category="Alimentari",
                subcategory="Spesa supermercato",
            )

        cache = HistoryCache(session)
        cat_c, sub_c, conf_c = cache.lookup("coop")
        cat_l, sub_l, conf_l = lookup_history(session, "coop")

        assert cat_c == cat_l
        assert sub_c == sub_l
        assert abs(conf_c - conf_l) < 1e-9

    def test_cache_no_match(self, session):
        """Cache returns (None, None, 0.0) for unknown descriptions."""
        cache = HistoryCache(session)
        cat, subcat, conf = cache.lookup("nonexistent")
        assert cat is None
        assert subcat is None
        assert conf == 0.0
