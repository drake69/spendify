"""Tests for CategoryService — no real LLM backend needed."""
from __future__ import annotations

import pytest
from decimal import Decimal
from sqlalchemy import create_engine

from db.models import Base, get_session
from core.categorizer import CategorySource, CategorizationResult
from core.models import Confidence
from services.category_service import CategoryService


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def svc(engine):
    return CategoryService(engine)


class _NoLLMBackend:
    """Stub LLM backend that never gets called in deterministic tests."""
    def complete(self, *args, **kwargs):
        raise AssertionError("LLM backend should not be called for deterministic categorization")


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_categorize_single_deterministic(svc):
    """'ESSELUNGA SPA' matches the static rule → no LLM needed."""
    result = svc.categorize_single(
        description="ESSELUNGA SPA",
        amount=-25.0,
        doc_type="bank_account",
        backend=_NoLLMBackend(),
    )
    assert isinstance(result, CategorizationResult)
    assert result.category == "Alimentari"
    assert result.subcategory == "Spesa supermercato"
    assert result.source == CategorySource.rule
    assert result.to_review is False


def test_categorize_single_user_rule(engine):
    """User rule takes priority over static rules."""
    from db.models import get_session
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
        backend=_NoLLMBackend(),
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
    """categorize_many with static-matchable transactions returns results."""
    svc = CategoryService(engine)
    transactions = [
        {"description": "ESSELUNGA SPA", "amount": -25.0, "doc_type": "bank_account"},
        {"description": "LIDL SUPERMERCATI", "amount": -15.0, "doc_type": "bank_account"},
    ]
    results = svc.categorize_many(transactions, backend=_NoLLMBackend())
    assert len(results) == 2
    for r in results:
        assert r.category == "Alimentari"
