"""ImportService — service layer for file import pipeline.

Re-exports domain types (DocumentSchema, DocumentType, …) so UI modules can
import everything they need from services.* only, without touching core.* or db.*.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass

import pandas as pd
from sqlalchemy.orm import sessionmaker

from db import repository
from core import orchestrator
from core.models import Confidence, DocumentType, GirocontoMode, SignConvention  # noqa: F401 — re-exported for UI
from core.normalizer import (
    compute_columns_key,
    compute_header_sha256,
    detect_skip_rows as _detect_skip_rows,
    load_raw_head as _load_raw_head,
)
from core.history_engine import HistoryCache
from services.nsi_taxonomy_service import NsiTaxonomyService
from core.orchestrator import (
    ImportResult,
    ProcessingConfig,
    SkippedRow,  # noqa: F401 — re-exported for UI
    _normalize_df_with_schema,
    load_raw_dataframe,
    process_file as _process_file,
)
from core.sanitizer import SanitizationConfig
from core.schemas import DocumentSchema  # noqa: F401 — re-exported for UI


# ── Re-exported public types (UI can import these from services.import_service) ──
__all__ = [
    "ImportService",
    "FileAnalysis",
    # domain types re-exported as facade
    "Confidence",
    "DocumentType",
    "DocumentSchema",
    "GirocontoMode",
    "ProcessingConfig",
    "SignConvention",
]


@dataclass
class FileAnalysis:
    """Result of pre-import schema analysis for a single file."""
    n_rows: int
    known_schema: DocumentSchema | None
    header_sha256: str


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

    # ── Config ────────────────────────────────────────────────────────────────

    def build_config(
        self,
        test_mode: bool | None = None,
        giroconto_mode: str = "neutral",
    ) -> ProcessingConfig:
        """Build a ProcessingConfig from user settings stored in the DB.

        Args:
            test_mode: Override the import_test_mode setting. None = read from DB.
            giroconto_mode: GirocontoMode string (e.g. from session_state).
        """
        with self._session() as s:
            settings = repository.get_all_user_settings(s)

        use_owner_giroconto = settings.get("use_owner_names_giroconto", "false").lower() == "true"
        if test_mode is None:
            test_mode = settings.get("import_test_mode", "false").lower() == "true"
        owner_names = [n.strip() for n in settings.get("owner_names", "").split(",") if n.strip()]

        return ProcessingConfig(
            llm_backend=settings.get("llm_backend", "local_ollama"),
            giroconto_mode=GirocontoMode(giroconto_mode),
            use_owner_names_for_giroconto=use_owner_giroconto,
            sanitize_config=SanitizationConfig(
                owner_names=owner_names,
                description_language=settings.get("description_language", "it"),
            ),
            ollama_base_url=settings.get("ollama_base_url", "http://localhost:11434"),
            ollama_model=settings.get("ollama_model", "gemma3:12b"),
            openai_model=settings.get("openai_model", "gpt-4o-mini"),
            openai_api_key=settings.get("openai_api_key", ""),
            claude_model=settings.get("anthropic_model", "claude-3-5-haiku-20241022"),
            anthropic_api_key=settings.get("anthropic_api_key", ""),
            compat_base_url=settings.get("compat_base_url", ""),
            compat_api_key=settings.get("compat_api_key", ""),
            compat_model=settings.get("compat_model", ""),
            llama_cpp_model_path=settings.get("llama_cpp_model_path", ""),
            llama_cpp_n_gpu_layers=int(settings.get("llama_cpp_n_gpu_layers", "-1")),
            llama_cpp_n_ctx=int(settings.get("llama_cpp_n_ctx", "0")),
            description_language=settings.get("description_language", "it"),
            test_mode=test_mode,
            max_transaction_amount=float(settings.get("max_transaction_amount", "1000000")),
            force_schema_import=settings.get("force_schema_import", "false").lower() == "true",
        )

    def get_owner_names(self) -> str:
        """Return the raw 'owner_names' setting string (empty if not configured)."""
        with self._session() as s:
            return repository.get_all_user_settings(s).get("owner_names", "").strip()

    # ── File analysis (pre-import) ─────────────────────────────────────────────

    def detect_skip_rows(self, raw_bytes: bytes, filename: str) -> tuple[int, bool]:
        """Return (detected_skip_rows, is_certain) for a raw file."""
        skip, certain, _border = _detect_skip_rows(raw_bytes, filename)
        return skip, certain

    def get_raw_head(self, raw_bytes: bytes, filename: str, n: int = 10) -> pd.DataFrame:
        """Return the first *n* raw rows of a file without any pre-processing."""
        return _load_raw_head(raw_bytes, filename, n=n)

    def find_schema_by_header(
        self, raw_bytes: bytes, filename: str
    ) -> DocumentSchema | None:
        """Look up a cached schema by the file's header SHA-256 fingerprint."""
        sha256 = compute_header_sha256(raw_bytes, filename)
        with self._session() as s:
            return repository.find_schema_by_header_sha256(s, sha256)

    def analyze_file(self, raw_bytes: bytes, filename: str) -> FileAnalysis:
        """Check schema cache by header SHA-256 then by column key.

        Returns a FileAnalysis with the cached schema (if any) and the row count.
        Use the result to decide whether to show the skip-rows input widget.
        """
        h_sha256 = compute_header_sha256(raw_bytes, filename)
        with self._session() as s:
            cached = repository.find_schema_by_header_sha256(s, h_sha256)
            if cached:
                return FileAnalysis(n_rows=0, known_schema=cached, header_sha256=h_sha256)

            df_raw, _, preprocess_info = load_raw_dataframe(raw_bytes, filename)
            stable_cols = preprocess_info.columns_before_drop or list(df_raw.columns)
            cols_key = compute_columns_key(pd.DataFrame(columns=stable_cols))
            schema = repository.get_document_schema(s, cols_key)

        return FileAnalysis(
            n_rows=len(df_raw),
            known_schema=schema,
            header_sha256=h_sha256,
        )

    def get_normalized_preview(
        self,
        raw_bytes: bytes,
        filename: str,
        schema: DocumentSchema,
        n: int = 30,
        skip_rows_override: int | None = None,
    ) -> list[dict]:
        """Return up to *n* normalised transaction dicts for a schema-review preview."""
        df_raw, _, _ = load_raw_dataframe(
            raw_bytes, filename, skip_rows_override=skip_rows_override
        )
        txs, _, _ = _normalize_df_with_schema(df_raw.head(n), schema, filename)
        return txs

    # ── Single-file import ─────────────────────────────────────────────────────

    def process_file_single(
        self,
        raw_bytes: bytes,
        filename: str,
        config: ProcessingConfig,
        known_schema: DocumentSchema | None = None,
        progress_callback=None,
        account_label_override: str | None = None,
        skip_rows_override: int | None = None,
    ) -> ImportResult:
        """Run the full import pipeline for one file.

        Taxonomy and category rules are loaded from the DB internally.
        Duplicate detection uses a per-call session so no open session is needed
        from the caller.
        """
        nsi_svc = NsiTaxonomyService(self.engine)
        with self._session() as s:
            taxonomy = repository.get_taxonomy_config(s)
            user_rules = repository.get_category_rules(s)
            history_cache = HistoryCache(s)
            # C-08-cascade: load (or build) NSI taxonomy_map
            from core.orchestrator import _build_backend
            _backend_for_nsi = _build_backend(config)
            taxonomy_map = nsi_svc.get_or_build(s, taxonomy, _backend_for_nsi)

        def _existing_checker(tx_ids: list[str]) -> set[str]:
            with self._session() as s:
                return repository.get_existing_tx_ids(s, tx_ids)

        return _process_file(
            raw_bytes=raw_bytes,
            filename=filename,
            config=config,
            taxonomy=taxonomy,
            user_rules=user_rules,
            known_schema=known_schema,
            progress_callback=progress_callback,
            existing_tx_ids_checker=_existing_checker,
            account_label_override=account_label_override,
            skip_rows_override=skip_rows_override,
            history_cache=history_cache,
            taxonomy_map=taxonomy_map,
        )

    # ── Full-batch import (legacy) ─────────────────────────────────────────────

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

    # ── Persistence ───────────────────────────────────────────────────────────

    def persist_result(self, result: ImportResult) -> None:
        with self._session() as s:
            repository.persist_import_result(s, result)

    # ── Job management ────────────────────────────────────────────────────────

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

    # ── Import history & undo ──────────────────────────────────────────────────

    def get_import_history(self, limit: int = 100) -> list[dict]:
        """Return import batches with transaction counts, most recent first."""
        with self._session() as s:
            return repository.get_import_history(s, limit=limit)

    def cancel_import(self, batch_id: int) -> int:
        """Hard-delete all transactions for the given batch. Returns deleted count."""
        with self._session() as s:
            return repository.cancel_import_batch(s, batch_id)

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _config_from_settings(settings: dict) -> ProcessingConfig:
        """Minimal config used by process_files() legacy batch method."""
        return ProcessingConfig(
            llm_backend=settings.get("llm_backend", "local_ollama"),
            ollama_base_url=settings.get("ollama_base_url", "http://localhost:11434"),
            ollama_model=settings.get("ollama_model", "gemma3:12b"),
            openai_api_key=settings.get("openai_api_key", ""),
            openai_model=settings.get("openai_model", "gpt-4o-mini"),
            anthropic_api_key=settings.get("anthropic_api_key", ""),
            claude_model=settings.get("anthropic_model", "claude-3-haiku-20240307"),
            force_schema_import=settings.get("force_schema_import", "false").lower() == "true",
        )
