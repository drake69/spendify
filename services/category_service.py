"""CategoryService — service layer for categorization operations."""
from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy.orm import sessionmaker

from db import repository
from core.categorizer import (
    TaxonomyConfig,
    CategorizationResult,
    categorize_batch,
    categorize_transaction,
)


class CategoryService:
    def __init__(self, engine) -> None:
        self.engine = engine
        self._Session = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def _session(self):
        s = self._Session()
        try:
            yield s
        finally:
            s.close()

    def categorize_single(
        self,
        description: str,
        amount: float,
        doc_type: str,
        backend=None,
    ) -> CategorizationResult:
        """Categorize one transaction using rules → static → LLM cascade."""
        from core.orchestrator import ProcessingConfig, _build_backend
        with self._session() as s:
            taxonomy = repository.get_taxonomy_config(s)
            user_rules = repository.get_category_rules(s)
            settings = repository.get_all_user_settings(s)
        if backend is None:
            config = self._config_from_settings(settings)
            backend = _build_backend(config)
        return categorize_transaction(
            description=description,
            amount=amount,
            doc_type=doc_type,
            taxonomy=taxonomy,
            user_rules=user_rules,
            llm_backend=backend,
            sanitize_config=None,
            fallback_backend=None,
            confidence_threshold=0.6,
            description_language=settings.get("description_language", "it"),
        )

    def categorize_many(
        self,
        transactions: list[dict],
        backend=None,
        progress_callback=None,
    ) -> list[CategorizationResult]:
        """Categorize a batch of transactions."""
        from core.orchestrator import ProcessingConfig, _build_backend
        from services.nsi_taxonomy_service import NsiTaxonomyService
        with self._session() as s:
            taxonomy = repository.get_taxonomy_config(s)
            user_rules = repository.get_category_rules(s)
            settings = repository.get_all_user_settings(s)
        if backend is None:
            config = self._config_from_settings(settings)
            backend = _build_backend(config)
        # C-08-cascade: load (or build) NSI taxonomy_map
        nsi_svc = NsiTaxonomyService(self.engine)
        with self._session() as s:
            taxonomy_map = nsi_svc.get_or_build(s, taxonomy, backend)
        return categorize_batch(
            transactions=transactions,
            taxonomy=taxonomy,
            user_rules=user_rules,
            llm_backend=backend,
            sanitize_config=None,
            fallback_backend=None,
            description_language=settings.get("description_language", "it"),
            user_country=settings.get("country", ""),
            progress_callback=progress_callback,
            taxonomy_map=taxonomy_map,
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _config_from_settings(settings: dict):
        from core.orchestrator import ProcessingConfig
        return ProcessingConfig(
            llm_backend=settings.get("llm_backend", "local_ollama"),
            ollama_base_url=settings.get("ollama_base_url", "http://localhost:11434"),
            ollama_model=settings.get("ollama_model", "gemma3:12b"),
            openai_api_key=settings.get("openai_api_key", ""),
            openai_model=settings.get("openai_model", "gpt-4o-mini"),
            anthropic_api_key=settings.get("anthropic_api_key", ""),
            claude_model=settings.get("anthropic_model", "claude-3-haiku-20240307"),
            user_country=settings.get("country", ""),
        )
