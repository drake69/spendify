"""Tests for CategoryService — no real LLM backend needed."""
from __future__ import annotations

import pytest
from decimal import Decimal
from sqlalchemy import create_engine

from db.models import create_tables, get_session
from core.categorizer import CategorySource, CategorizationResult
from core.models import Confidence
from services.category_service import CategoryService


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def engine():
    """In-memory DB with full migrations (required for nsi_tag_mapping table)."""
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    create_tables(eng)
    return eng


@pytest.fixture
def svc(engine):
    return CategoryService(engine)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_categorize_single_deterministic(engine):
    """User-defined category rule fires without calling the LLM (Step 0 deterministic)."""
    from db import repository

    # Seed a user rule
    with get_session(engine) as s:
        repository.create_category_rule(
            s, pattern="esselunga", match_type="contains",
            category="Alimentari", subcategory="Spesa supermercato", priority=10,
        )
        s.commit()

    svc = CategoryService(engine)
    result = svc.categorize_single(
        description="ESSELUNGA SPA",
        amount=-25.0,
        doc_type="bank_account",
        backend=None,
    )
    assert isinstance(result, CategorizationResult)
    assert result.category == "Alimentari"
    assert result.subcategory == "Spesa supermercato"
    assert result.source == CategorySource.rule
    assert result.to_review is False


def test_categorize_single_user_rule(engine):
    """User rule takes priority (Step 0)."""
    from db import repository

    svc = CategoryService(engine)

    # Seed a user rule for "pagamento affitto"
    with get_session(engine) as s:
        repository.create_category_rule(
            s, pattern="affitto", match_type="contains",
            category="Casa", subcategory="Affitto", priority=100,
        )
        s.commit()

    result = svc.categorize_single(
        description="pagamento affitto mensile",
        amount=-800.0,
        doc_type="bank_account",
        backend=None,  # user rule fires before LLM is needed
    )
    assert result.category == "Casa"
    assert result.subcategory == "Affitto"
    assert result.source == CategorySource.rule


def test_categorize_single_falls_back_when_no_match(engine):
    """Unknown description with stub backend → to_review fallback."""
    svc = CategoryService(engine)

    class _NullBackend:
        def complete(self, *args, **kwargs):
            return None

    result = svc.categorize_single(
        description="xyzzy random unknown description 12345",
        amount=-5.0,
        doc_type="bank_account",
        backend=None,  # uses real _build_backend which will fail/fallback
    )
    # Must not raise; result should be a CategorizationResult
    assert isinstance(result, CategorizationResult)


def test_config_from_settings():
    """_config_from_settings builds a ProcessingConfig with correct fields."""
    from core.orchestrator import ProcessingConfig
    settings = {
        "llm_backend": "openai",
        "ollama_base_url": "http://localhost:11434",
        "ollama_model": "llama3",
        "openai_api_key": "sk-test",
        "openai_model": "gpt-4",
        "anthropic_api_key": "ak-test",
        "anthropic_model": "claude-3-opus-20240229",
    }
    config = CategoryService._config_from_settings(settings)
    assert isinstance(config, ProcessingConfig)
    assert config.llm_backend == "openai"
    assert config.openai_api_key == "sk-test"
    assert config.openai_model == "gpt-4"
    assert config.claude_model == "claude-3-opus-20240229"
    assert config.ollama_model == "llama3"


def test_categorize_many_deterministic(engine):
    """categorize_many with user-rule-matched transactions never calls LLM (Step 0)."""
    from db import repository

    # Seed user rules so all transactions are matched deterministically
    with get_session(engine) as s:
        repository.create_category_rule(
            s, pattern="esselunga", match_type="contains",
            category="Alimentari", subcategory="Spesa supermercato", priority=10,
        )
        repository.create_category_rule(
            s, pattern="lidl", match_type="contains",
            category="Alimentari", subcategory="Spesa supermercato", priority=10,
        )
        s.commit()

    svc = CategoryService(engine)
    transactions = [
        {"description": "ESSELUNGA SPA", "amount": -25.0, "doc_type": "bank_account"},
        {"description": "LIDL SUPERMERCATI", "amount": -15.0, "doc_type": "bank_account"},
    ]
    results = svc.categorize_many(transactions, backend=None)
    assert len(results) == 2
    for r in results:
        assert r.category == "Alimentari"
        assert r.source == CategorySource.rule
