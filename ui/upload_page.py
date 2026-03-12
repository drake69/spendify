"""Import page (RF-08): upload files, run pipeline, show summary."""
from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from core.categorizer import TaxonomyConfig
from core.models import GirocontoMode
from core.normalizer import compute_columns_key
from core.orchestrator import ProcessingConfig, load_raw_dataframe, process_files
from core.sanitizer import SanitizationConfig
from db.models import get_session
from db.repository import (
    get_category_rules,
    get_document_schema,
    persist_import_result,
)
from support.logging import setup_logging

logger = setup_logging()

TAXONOMY_PATH = os.getenv("TAXONOMY_PATH", "taxonomy.yaml")


def _load_taxonomy() -> TaxonomyConfig:
    if Path(TAXONOMY_PATH).exists():
        return TaxonomyConfig.from_yaml(TAXONOMY_PATH)
    # Fallback minimal taxonomy
    return TaxonomyConfig(
        expenses={"Altro": ["Spese non classificate"]},
        income={"Altro entrate": ["Entrate non classificate"]},
    )


def _build_config(engine) -> ProcessingConfig:
    backend = st.session_state.get("llm_backend", "local_ollama")
    mode_str = st.session_state.get("giroconto_mode", "neutral")

    # Owner names from env for PII sanitization
    owner_names = [n.strip() for n in os.getenv("OWNER_NAMES", "").split(",") if n.strip()]

    return ProcessingConfig(
        llm_backend=backend,
        giroconto_mode=GirocontoMode(mode_str),
        sanitize_config=SanitizationConfig(owner_names=owner_names),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        claude_model=os.getenv("CLAUDE_MODEL", "claude-3-5-haiku-20241022"),
        ollama_model=os.getenv("OLLAMA_MODEL", "gemma3:12b"),
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
    )


def render_upload_page(engine):
    st.header("📥 Import — Caricamento Estratti Conto")

    uploaded_files = st.file_uploader(
        "Carica uno o più file (CSV, XLSX, PDF)",
        type=["csv", "xls", "xlsx"],
        accept_multiple_files=True,
    )

    if not uploaded_files:
        st.info("Carica uno o più file per avviare l'elaborazione.")
        return

    if st.button("▶️ Elabora file", type="primary"):
        session = get_session(engine)
        taxonomy = _load_taxonomy()
        config = _build_config(engine)

        with session:
            user_rules = get_category_rules(session)

            # Read bytes once, derive columns key, look up known schema by that key
            # (not by filename, so CARTA_2025.xlsx and CARTA_2026.xlsx share a schema)
            known_schemas = {}
            files = []
            for uf in uploaded_files:
                raw_bytes = uf.read()
                df_raw, _ = load_raw_dataframe(raw_bytes, uf.name)
                cols_key = compute_columns_key(df_raw)
                schema = get_document_schema(session, cols_key)
                if schema:
                    known_schemas[uf.name] = schema
                files.append((raw_bytes, uf.name))

        progress = st.progress(0)
        status = st.empty()

        results = []
        with get_session(engine) as session2:
            for i, (raw_bytes, filename) in enumerate(files):
                status.text(f"Elaborazione: {filename}…")
                schema = known_schemas.get(filename)
                from core.orchestrator import process_file
                result = process_file(
                    raw_bytes=raw_bytes,
                    filename=filename,
                    config=config,
                    taxonomy=taxonomy,
                    user_rules=user_rules,
                    known_schema=schema,
                )
                persist_import_result(session2, result)
                results.append(result)
                progress.progress((i + 1) / len(files))

        status.text("Elaborazione completata!")

        # Summary
        st.divider()
        st.subheader("Riepilogo elaborazione")
        for result in results:
            with st.expander(f"📄 {result.source_name}", expanded=True):
                if result.errors:
                    st.error("Errori: " + "; ".join(result.errors))
                else:
                    col1, col2, col3, col4 = st.columns(4)
                    col1.metric("Transazioni", len(result.transactions))
                    col2.metric("Riconciliazioni", len(result.reconciliations))
                    col3.metric("Giroconti", len(result.transfer_links))
                    col4.metric("Flusso", result.flow_used.upper())

                    to_review = sum(1 for tx in result.transactions if tx.get("to_review"))
                    if to_review:
                        st.warning(f"{to_review} transazioni richiedono revisione manuale → pagina Review")

                    if result.doc_schema:
                        st.caption(
                            f"Schema: {result.doc_schema.doc_type} | "
                            f"Account: {result.doc_schema.account_label} | "
                            f"Confidence: {result.doc_schema.confidence}"
                        )
