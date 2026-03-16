"""Import page (RF-08): upload files, run pipeline, show summary."""
from __future__ import annotations

import time
from datetime import datetime, timezone

import streamlit as st

from core.models import GirocontoMode
from core.normalizer import compute_columns_key
from core.orchestrator import ProcessingConfig, load_raw_dataframe, process_files
from core.sanitizer import SanitizationConfig
from db.models import get_session
from db.repository import get_accounts
from db.repository import (
    create_import_job,
    get_all_user_settings,
    get_category_rules,
    get_document_schema,
    get_existing_tx_ids,
    get_latest_import_job,
    get_taxonomy_config,
    persist_import_result,
    update_import_job,
)
from support.logging import setup_logging

logger = setup_logging()

# Minimum seconds between DB progress writes (throttle for high-frequency callbacks)
_DB_WRITE_INTERVAL = 1.5


def _build_config(engine, test_mode: bool | None = None) -> ProcessingConfig:
    mode_str = st.session_state.get("giroconto_mode", "neutral")
    with get_session(engine) as _s:
        s = get_all_user_settings(_s)

    use_owner_giroconto = s.get("use_owner_names_giroconto", "false").lower() == "true"
    if test_mode is None:
        test_mode = s.get("import_test_mode", "false").lower() == "true"
    owner_names = [n.strip() for n in s.get("owner_names", "").split(",") if n.strip()]

    return ProcessingConfig(
        llm_backend=s.get("llm_backend", "local_ollama"),
        giroconto_mode=GirocontoMode(mode_str),
        use_owner_names_for_giroconto=use_owner_giroconto,
        sanitize_config=SanitizationConfig(
            owner_names=owner_names,
            description_language=s.get("description_language", "it"),
        ),
        ollama_base_url=s.get("ollama_base_url", "http://localhost:11434"),
        ollama_model=s.get("ollama_model", "gemma3:12b"),
        openai_model=s.get("openai_model", "gpt-4o-mini"),
        openai_api_key=s.get("openai_api_key", ""),
        claude_model=s.get("anthropic_model", "claude-3-5-haiku-20241022"),
        anthropic_api_key=s.get("anthropic_api_key", ""),
        description_language=s.get("description_language", "it"),
        test_mode=test_mode,
    )


def _render_job_status(engine, job=None):
    """Render import job state. Reads from DB if job not provided.

    When a job is *running*, auto-refreshes every 2 s so any connected
    session (including other browsers) sees live progress.
    """
    if job is None:
        with get_session(engine) as s:
            job = get_latest_import_job(s)
    if job is None:
        return

    if job.status == "running":
        pct = float(job.progress or 0)
        msg = job.status_message or "Elaborazione in corso…"
        st.info(f"⏳ {msg}")
        st.progress(pct)
        st.caption(f"Avanzamento: {int(pct * 100)}% · aggiornamento automatico ogni 2 s")
        time.sleep(2)
        st.rerun()

    elif job.status == "completed":
        st.success(job.status_message or "✅ Completato")
        st.progress(1.0)
        if job.detail_message:
            st.caption(job.detail_message)

    elif job.status == "error":
        st.error(job.status_message or "❌ Errore durante l'importazione")


def _render_last_import_summary():
    results = st.session_state.get("last_import_results")
    if not results:
        return
    st.divider()
    st.subheader("Riepilogo ultima elaborazione")
    for result in results:
        with st.expander(f"📄 {result.source_name}", expanded=True):
            if result.errors:
                st.error("Errori: " + "; ".join(result.errors))
            else:
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Nuove transazioni", len(result.transactions))
                col2.metric("Già importate (saltate)", result.skipped_count)
                col3.metric("Riconciliazioni", len(result.reconciliations))
                col4.metric("Flusso", result.flow_used.upper() if result.flow_used != "unknown" else "—")

                if result.skipped_count and not result.transactions:
                    st.info("⏭️ Tutte le transazioni erano già presenti nel database.")
                    continue

                to_review = sum(1 for tx in result.transactions if tx.get("to_review"))
                if to_review:
                    st.warning(f"{to_review} transazioni richiedono revisione manuale → pagina Review")

                if result.doc_schema:
                    st.caption(
                        f"Schema: {result.doc_schema.doc_type} | "
                        f"Account: {result.doc_schema.account_label} | "
                        f"Confidence: {result.doc_schema.confidence}"
                    )


def render_upload_page(engine):
    st.header("📥 Import — Caricamento Estratti Conto")

    # Guard: owner names must be configured before any import
    with get_session(engine) as _s:
        _owner_names = get_all_user_settings(_s).get("owner_names", "").strip()
    if not _owner_names:
        st.error(
            "⚠️ **Nomi titolari non configurati.** "
            "Vai in ⚙️ Impostazioni e compila il campo *Nomi titolari* prima di importare."
        )
        return

    # Check for an active or recently-completed job *before* rendering the form.
    # If a job is running: show only the progress view (block the form).
    # If completed/error: fall through and show form + summary below.
    with get_session(engine) as _js:
        _active_job = get_latest_import_job(_js)

    if _active_job and _active_job.status == "running":
        st.info("⏳ Importazione in corso — attendere il completamento prima di caricare nuovi file.")
        _render_job_status(engine, job=_active_job)
        return

    uploaded_files = st.file_uploader(
        "Carica uno o più file (CSV, XLSX, PDF)",
        type=["csv", "xls", "xlsx"],
        accept_multiple_files=True,
    )

    if not uploaded_files:
        st.info("Carica uno o più file per avviare l'elaborazione.")
        # Show job status for any connected session (including other browsers)
        # that didn't originate the import — reads live state from DB.
        with get_session(engine) as _idle_s:
            _idle_job = get_latest_import_job(_idle_s)
        _render_job_status(engine, job=_idle_job)
        _render_last_import_summary()
        # If no running job is visible, poll every 5 s to detect a job started
        # from another browser session (Streamlit has no push mechanism).
        if _idle_job is None or _idle_job.status != "running":
            time.sleep(5)
            st.rerun()
        return

    # Per-file account selector
    with get_session(engine) as _acc_s:
        _accounts = get_accounts(_acc_s)
    _account_names = [a.name for a in _accounts]
    _account_options = ["— rilevamento automatico —"] + _account_names

    if not _account_names:
        st.warning(
            "⚠️ Nessun conto configurato. Vai in ⚙️ Impostazioni → *Conti bancari* per aggiungere "
            "i tuoi conti. Puoi importare comunque ma il dedup potrebbe non essere stabile."
        )

    st.markdown("**Associa ogni file a un conto:**")
    _file_account_map: dict[str, str | None] = {}
    for uf in uploaded_files:
        c1, c2 = st.columns([3, 2])
        c1.caption(f"📄 {uf.name}")
        sel = c2.selectbox(
            "Conto",
            options=_account_options,
            key=f"file_account_{uf.name}",
            label_visibility="collapsed",
        )
        _file_account_map[uf.name] = sel if sel != "— rilevamento automatico —" else None

    if st.button("▶️ Elabora file", type="primary"):
        config = _build_config(engine)

        # Prepare file list and load known schemas
        files = []
        known_schemas = {}
        with get_session(engine) as session:
            taxonomy = get_taxonomy_config(session)
            user_rules = get_category_rules(session)
            for uf in uploaded_files:
                raw_bytes = uf.read()
                df_raw, _ = load_raw_dataframe(raw_bytes, uf.name)
                cols_key = compute_columns_key(df_raw)
                schema = get_document_schema(session, cols_key)
                if schema:
                    known_schemas[uf.name] = schema
                files.append((raw_bytes, uf.name, len(df_raw)))

        total_files = len(files)

        # Create job record in DB
        with get_session(engine) as s:
            job = create_import_job(s, n_files=total_files)
            job_id = job.id

        # Live widgets for the originating session
        _progress_bar = st.progress(0.0)
        _status_text = st.empty()
        _counter_text = st.empty()

        results = []
        with get_session(engine) as session2:
            for i, (raw_bytes, filename, n_rows) in enumerate(files):
                file_start = i / total_files
                file_end = (i + 1) / total_files

                _status_text.text(f"File {i + 1}/{total_files} — {filename}")
                _counter_text.caption("Avvio elaborazione...")

                # Write file-start to DB
                with get_session(engine) as s:
                    update_import_job(s, job_id,
                                      progress=file_start,
                                      status_message=f"File {i + 1}/{total_files} — {filename}")

                # Throttle state for DB writes inside the callback
                _last_db_write = [0.0]

                def _make_cb(start: float, end: float, fname: str,
                              fidx: int, ftot: int, jid: int,
                              _last: list):
                    def _cb(p: float):
                        pct = start + (end - start) * p
                        # Update live widgets in originating session
                        _progress_bar.progress(min(pct, 1.0))
                        _status_text.text(f"File {fidx + 1}/{ftot} — {fname}")
                        _counter_text.caption(f"Avanzamento file: {int(p * 100)}%")
                        # Throttled DB write so other sessions see live progress
                        now = time.time()
                        if now - _last[0] >= _DB_WRITE_INTERVAL:
                            _last[0] = now
                            with get_session(engine) as s:
                                update_import_job(
                                    s, jid,
                                    progress=round(pct, 4),
                                    status_message=f"File {fidx + 1}/{ftot} — {fname} ({int(p * 100)}%)",
                                )
                    return _cb

                def _make_existing_checker(eng):
                    def _checker(tx_ids: list[str]) -> set[str]:
                        with get_session(eng) as s:
                            return get_existing_tx_ids(s, tx_ids)
                    return _checker

                from core.orchestrator import process_file
                result = process_file(
                    raw_bytes=raw_bytes,
                    filename=filename,
                    config=config,
                    taxonomy=taxonomy,
                    user_rules=user_rules,
                    known_schema=known_schemas.get(filename),
                    progress_callback=_make_cb(
                        file_start, file_end, filename, i, total_files,
                        job_id, _last_db_write,
                    ),
                    existing_tx_ids_checker=_make_existing_checker(engine),
                    account_label_override=_file_account_map.get(filename),
                )
                persist_import_result(session2, result)
                results.append(result)
                _progress_bar.progress(file_end)

                # Write file-end to DB
                with get_session(engine) as s:
                    update_import_job(s, job_id, progress=file_end)

        n_tx = sum(len(r.transactions) for r in results)
        n_skipped = sum(r.skipped_count for r in results)
        final_msg = f"✅ Completato — {n_tx:,} nuove transazioni"
        if n_skipped:
            final_msg += f" · {n_skipped:,} già presenti (saltate)"
        final_detail = (
            f"{total_files} file elaborati · "
            f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC"
        )

        # Mark job completed in DB
        with get_session(engine) as s:
            update_import_job(s, job_id,
                              status="completed",
                              progress=1.0,
                              status_message=final_msg,
                              detail_message=final_detail,
                              n_transactions=n_tx,
                              completed_at=datetime.now(timezone.utc))

        st.session_state["last_import_results"] = results

    # Always rendered: reads job state from DB + summary from session_state
    _render_job_status(engine)
    _render_last_import_summary()
