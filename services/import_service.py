"""ImportService — service layer for file import pipeline."""
from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy.orm import sessionmaker

from db import repository
from core import orchestrator
from core.orchestrator import ImportResult, ProcessingConfig


class ImportService:
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

    def process_files(
        self,
        files: list[tuple[bytes, str]],
        config: ProcessingConfig | None = None,
    ) -> list[ImportResult]:
        """Run the full import pipeline for a list of (raw_bytes, filename) tuples."""
        with self._session() as s:
            settings = repository.get_all_user_settings(s)
            taxonomy = repository.get_taxonomy_config(s)
            user_rules = repository.get_category_rules(s)
        if config is None:
            config = self._config_from_settings(settings)
        return orchestrator.process_files(files, config, taxonomy, user_rules, {})

    def persist_result(self, result: ImportResult) -> None:
        with self._session() as s:
            repository.persist_import_result(s, result)

    def create_job(self, n_files: int):
        with self._session() as s:
            return repository.create_import_job(s, n_files)

    def update_job(self, job_id: int, **kwargs) -> None:
        with self._session() as s:
            repository.update_import_job(s, job_id, **kwargs)

    def get_latest_job(self):
        with self._session() as s:
            return repository.get_latest_import_job(s)

    def reset_stale_jobs(self) -> int:
        with self._session() as s:
            return repository.reset_stale_jobs(s)

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _config_from_settings(settings: dict) -> ProcessingConfig:
        return ProcessingConfig(
            llm_backend=settings.get("llm_backend", "local_ollama"),
            ollama_base_url=settings.get("ollama_base_url", "http://localhost:11434"),
            ollama_model=settings.get("ollama_model", "gemma3:12b"),
            openai_api_key=settings.get("openai_api_key", ""),
            openai_model=settings.get("openai_model", "gpt-4o-mini"),
            anthropic_api_key=settings.get("anthropic_api_key", ""),
            claude_model=settings.get("anthropic_model", "claude-3-haiku-20240307"),
        )
