"""Unit tests for core/categorizer.py — user rules and cascade logic.

Note: Step 1 (static language rules, _it.json) has been deprecated in C-08-cascade.
  Brand matching is now handled by the NSI taxonomy_map bypass (Step 3b).
  TestStaticRules has been removed accordingly.
"""
from decimal import Decimal

import pytest

from core.categorizer import (
    CategoryRule,
    TaxonomyConfig,
    categorize_transaction,
)
from core.models import CategorySource, Confidence


@pytest.fixture
def minimal_taxonomy():
    return TaxonomyConfig(
        expenses={
            "Alimentari": ["Spesa supermercato", "Altro alimentari"],
            "Trasporti": ["Carburante", "Trasporto pubblico"],
            "Altro": ["Spese non classificate"],
        },
        income={
            "Lavoro dipendente": ["Stipendio"],
            "Altro entrate": ["Entrate non classificate"],
        },
    )


class TestCategoryRule:
    def test_contains_match(self):
        rule = CategoryRule(id=1, pattern="netflix", match_type="contains",
                            category="Comunicazioni", subcategory="Streaming / abbonamenti digitali",
                            doc_type=None)
        assert rule.matches("abbonamento netflix mensile")

    def test_contains_case_insensitive(self):
        rule = CategoryRule(id=1, pattern="AMAZON", match_type="contains",
                            category="Altro", subcategory=None, doc_type=None)
        assert rule.matches("amazon.it ordine 12345")

    def test_exact_match(self):
        rule = CategoryRule(id=1, pattern="stipendio", match_type="exact",
                            category="Lavoro dipendente", subcategory="Stipendio",
                            doc_type=None)
        assert rule.matches("stipendio")
        assert not rule.matches("pagamento stipendio")

    def test_regex_match(self):
        rule = CategoryRule(id=1, pattern=r"\btelepass\b", match_type="regex",
                            category="Trasporti", subcategory="Parcheggio / ZTL",
                            doc_type=None)
        assert rule.matches("pagamento telepass mensile")


class TestCategorizationCascade:
    def test_user_rule_wins(self, minimal_taxonomy):
        rule = CategoryRule(id=1, pattern="myshop", match_type="contains",
                            category="Alimentari", subcategory="Spesa supermercato",
                            doc_type=None, priority=10)
        result = categorize_transaction(
            description="pagamento myshop",
            amount=Decimal("-50.00"),
            doc_type="bank_account",
            taxonomy=minimal_taxonomy,
            user_rules=[rule],
            llm_backend=None,
        )
        assert result.category == "Alimentari"
        assert result.source == CategorySource.rule
        assert result.confidence == Confidence.high

    def test_no_rule_no_llm_falls_to_review(self, minimal_taxonomy):
        """Without user rules and without LLM, any transaction falls to Altro/to_review.

        Step 1 (static rules) was deprecated in C-08-cascade; brand matching is
        now handled by NSI taxonomy_map (Step 3b).  With no taxonomy_map and no LLM
        the cascade ends at the fallback.
        """
        result = categorize_transaction(
            description="Esselunga",
            amount=Decimal("-60.00"),
            doc_type="bank_account",
            taxonomy=minimal_taxonomy,
            user_rules=[],
            llm_backend=None,
        )
        assert result.category == "Altro"
        assert result.to_review is True

    def test_fallback_to_altro_no_llm(self, minimal_taxonomy):
        result = categorize_transaction(
            description="xyz irreconoscibile",
            amount=Decimal("-10.00"),
            doc_type="bank_account",
            taxonomy=minimal_taxonomy,
            user_rules=[],
            llm_backend=None,
        )
        assert result.category == "Altro"
        assert result.to_review is True
        assert result.confidence == Confidence.low
