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
    from unittest.mock import MagicMock

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
        backend=MagicMock(),  # never called — rule matches first
    )
    assert isinstance(result, CategorizationResult)
    assert result.category == "Alimentari"
    assert result.subcategory == "Spesa supermercato"
    assert result.source == CategorySource.rule
    assert result.to_review is False


def test_categorize_single_user_rule(engine):
    """User rule takes priority (Step 0)."""
    from db import repository
    from unittest.mock import MagicMock

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
        backend=MagicMock(),  # never called — rule matches first
    )
    assert result.category == "Casa"
    assert result.subcategory == "Affitto"
    assert result.source == CategorySource.rule


def test_categorize_single_falls_back_when_no_match(engine):
    """Unknown description with stub backend → to_review fallback."""
    from unittest.mock import MagicMock

    svc = CategoryService(engine)

    # Mock backend that returns None (simulates LLM failure)
    mock_backend = MagicMock()
    mock_backend.complete_structured.return_value = None

    result = svc.categorize_single(
        description="xyzzy random unknown description 12345",
        amount=-5.0,
        doc_type="bank_account",
        backend=mock_backend,
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
    from unittest.mock import MagicMock

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
    results = svc.categorize_many(transactions, backend=MagicMock())
    assert len(results) == 2
    for r in results:
        assert r.category == "Alimentari"
        assert r.source == CategorySource.rule


class TestPrewarmNsiTaxonomyMap:
    """Cover CategoryService.prewarm_nsi_taxonomy_map() — the onboarding
    step 4 hook that fires the single LLM call mapping OSM tags to
    (category, subcategory). Build the backend stub so we never actually
    hit a real LLM."""

    def test_returns_true_when_nsi_build_succeeds(self, svc, monkeypatch):
        """Happy path: NsiTaxonomyService.build returns without error →
        prewarm reports success."""
        from services.nsi_taxonomy_service import NsiTaxonomyService

        # Pretend the orchestrator builds a usable backend (we don't care
        # what it is, NsiTaxonomyService.build is monkeypatched).
        from core import orchestrator
        monkeypatch.setattr(orchestrator, "_build_backend", lambda _cfg: object())
        monkeypatch.setattr(orchestrator, "_build_categorizer_backend", lambda _cfg: None)

        called = {"n": 0}
        def _fake_build(self, session, taxonomy, llm_backend=None):
            called["n"] += 1
            return {}
        monkeypatch.setattr(NsiTaxonomyService, "build", _fake_build)

        assert svc.prewarm_nsi_taxonomy_map() is True
        assert called["n"] == 1

    def test_returns_false_on_failure(self, svc, monkeypatch):
        """Any exception inside build → caller gets False, no crash.
        The import path still has the static fallback covering us."""
        from services.nsi_taxonomy_service import NsiTaxonomyService
        from core import orchestrator
        monkeypatch.setattr(orchestrator, "_build_backend", lambda _cfg: object())
        monkeypatch.setattr(orchestrator, "_build_categorizer_backend", lambda _cfg: None)

        def _boom(self, session, taxonomy, llm_backend=None):
            raise RuntimeError("synthetic LLM failure")
        monkeypatch.setattr(NsiTaxonomyService, "build", _boom)

        assert svc.prewarm_nsi_taxonomy_map() is False

    def test_returns_false_when_backend_unavailable(self, svc, monkeypatch):
        """If both backend builders return None (no LLM configured),
        prewarm should report False — never crash."""
        from core import orchestrator
        monkeypatch.setattr(orchestrator, "_build_backend", lambda _cfg: None)
        monkeypatch.setattr(orchestrator, "_build_categorizer_backend", lambda _cfg: None)

        # NsiTaxonomyService.build is called with llm_backend=None → the
        # service itself fails over to static-only mapping. Stub it to
        # raise to exercise the except branch deterministically.
        from services.nsi_taxonomy_service import NsiTaxonomyService
        monkeypatch.setattr(
            NsiTaxonomyService, "build",
            lambda self, s, t, llm_backend=None: (_ for _ in ()).throw(ValueError("no backend"))
        )
        assert svc.prewarm_nsi_taxonomy_map() is False
