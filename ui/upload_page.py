"""Import page (RF-08): upload files, run pipeline, show summary."""
from __future__ import annotations

import time
from datetime import datetime, timezone

import streamlit as st

from core.models import Confidence, DocumentType, GirocontoMode, SignConvention
from core.normalizer import compute_columns_key
from core.orchestrator import ProcessingConfig, load_raw_dataframe, process_files
from core.sanitizer import SanitizationConfig
from core.schemas import DocumentSchema
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
        compat_base_url=s.get("compat_base_url", ""),
        compat_api_key=s.get("compat_api_key", ""),
        compat_model=s.get("compat_model", ""),
        description_language=s.get("description_language", "it"),
        test_mode=test_mode,
        max_transaction_amount=float(s.get("max_transaction_amount", "1000000")),
    )


@st.fragment(run_every="2s")
def _render_job_status_poll(engine) -> None:
    """Auto-refreshing job-status fragment (polls DB every 2 s).

    Renders progress/result widgets without blocking the main script thread,
    so no ghost content from the previous page ever appears.

    Uses session_state to detect state transitions:
    - not running → running : full app rerun to lock the file-uploader form.
    - running → completed/error : full app rerun to unlock the form.
    """
    with get_session(engine) as s:
        job = get_latest_import_job(s)

    was_running: bool = st.session_state.get("_upload_job_was_running", False)

    if job is None:
        st.session_state["_upload_job_was_running"] = False
        return

    if job.status == "running":
        pct = float(job.progress or 0)
        msg = job.status_message or "Elaborazione in corso…"
        st.info(f"⏳ {msg}")
        st.progress(pct)
        st.caption(f"Avanzamento: {int(pct * 100)}% · aggiornamento automatico ogni 2 s")
        st.session_state["_upload_job_was_running"] = True
        if not was_running:
            # Newly detected running job (started from another browser session).
            # Full rerun so main script enters the "job running" block and hides the form.
            st.rerun()

    elif job.status == "completed":
        st.success(job.status_message or "✅ Completato")
        st.progress(1.0)
        if job.detail_message:
            st.caption(job.detail_message)
        if was_running:
            # Transition: running → completed. Full rerun to unlock the form.
            st.session_state["_upload_job_was_running"] = False
            st.rerun()

    elif job.status == "error":
        st.error(job.status_message or "❌ Errore durante l'importazione")
        if was_running:
            st.session_state["_upload_job_was_running"] = False
            st.rerun()


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


def _render_schema_review(engine, config, taxonomy, user_rules) -> bool:
    """Show editable schema form for files with medium/low confidence.
    Returns True if the user confirmed and re-import was triggered."""
    pending = st.session_state.get("_pending_schema_reviews", [])
    if not pending:
        return False

    st.warning(
        f"⚠️ **Revisione schema richiesta** — {len(pending)} file con classificazione incerta. "
        "Verifica i campi rilevati e conferma prima di importare."
    )

    doc_type_options = [d.value for d in DocumentType]
    sign_options = [s.value for s in SignConvention]
    none_option = "— nessuna —"

    confirmed_schemas: dict[str, DocumentSchema] = {}

    for entry in pending:
        filename = entry["filename"]
        result = entry["result"]
        schema = result.doc_schema
        cols = result.available_columns
        col_options = [none_option] + cols

        evidence = schema.semantic_evidence if schema else []
        confidence_label = schema.confidence.value if schema else "unknown"

        with st.expander(f"📄 {filename}  [confidence: {confidence_label}]", expanded=True):
            if evidence:
                st.caption("Ragionamento LLM: " + " · ".join(evidence))

            c1, c2 = st.columns(2)
            doc_type_val = schema.doc_type.value if schema else doc_type_options[0]
            doc_type_sel = c1.selectbox(
                "Tipo documento", doc_type_options,
                index=doc_type_options.index(doc_type_val) if doc_type_val in doc_type_options else 0,
                key=f"rev_doc_type_{filename}",
            )
            account_sel = c2.text_input(
                "Account label", value=schema.account_label if schema else "",
                key=f"rev_account_{filename}",
            )

            c3, c4, c5 = st.columns(3)
            def _col_idx(val):
                return col_options.index(val) if val in col_options else 0

            amount_sel = c3.selectbox(
                "Colonna importo", col_options,
                index=_col_idx(schema.amount_col if schema else none_option),
                key=f"rev_amount_{filename}",
            )
            date_sel = c4.selectbox(
                "Colonna data", col_options,
                index=_col_idx(schema.date_col if schema else none_option),
                key=f"rev_date_{filename}",
            )
            sign_val = schema.sign_convention.value if schema else sign_options[0]
            sign_sel = c5.selectbox(
                "Convenzione segno", sign_options,
                index=sign_options.index(sign_val) if sign_val in sign_options else 0,
                key=f"rev_sign_{filename}",
            )

            c6, c7, c8 = st.columns(3)
            debit_sel = c6.selectbox(
                "Colonna addebiti (opt.)", col_options,
                index=_col_idx(schema.debit_col if schema and schema.debit_col else none_option),
                key=f"rev_debit_{filename}",
            )
            credit_sel = c7.selectbox(
                "Colonna accrediti (opt.)", col_options,
                index=_col_idx(schema.credit_col if schema and schema.credit_col else none_option),
                key=f"rev_credit_{filename}",
            )
            invert = c8.checkbox(
                "Inverti segno", value=schema.invert_sign if schema else False,
                key=f"rev_invert_{filename}",
            )

            # Build confirmed schema from user selections
            base = schema.model_copy() if schema else DocumentSchema(
                doc_type=DocumentType(doc_type_options[0]),
                date_col="", amount_col="", sign_convention=SignConvention(sign_options[0]),
                date_format="%d/%m/%Y", account_label="", confidence=Confidence.high,
            )
            confirmed_schemas[filename] = base.model_copy(update={
                "doc_type": DocumentType(doc_type_sel),
                "account_label": account_sel,
                "amount_col": "" if amount_sel == none_option else amount_sel,
                "date_col": "" if date_sel == none_option else date_sel,
                "sign_convention": SignConvention(sign_sel),
                "debit_col": None if debit_sel == none_option else debit_sel,
                "credit_col": None if credit_sel == none_option else credit_sel,
                "invert_sign": invert,
                "confidence": Confidence.high,  # user confirmed → treat as high
            })

            # Preview: apply schema and show normalised result — same layout as ledger
            st.markdown("**Anteprima normalizzata** — così appariranno nel ledger:")
            try:
                from core.orchestrator import _normalize_df_with_schema, load_raw_dataframe
                import pandas as pd
                df_raw_prev, _, _ = load_raw_dataframe(entry["raw_bytes"], filename)
                preview_txs = _normalize_df_with_schema(df_raw_prev.head(30), confirmed_schemas[filename], filename)
                if preview_txs:
                    preview_rows = [
                        {
                            "Data":              str(t.get("date", "")),
                            "Descrizione":       (t.get("description") or "")[:80],
                            "Entrata":           float(t["amount"]) if t.get("amount") is not None and float(t["amount"]) > 0 else None,
                            "Uscita":            abs(float(t["amount"])) if t.get("amount") is not None and float(t["amount"]) < 0 else None,
                            "Valuta":            t.get("currency", "EUR"),
                            "Desc. originale":   (t.get("raw_description") or "")[:80],
                            "Importo originale": str(t.get("raw_amount", "")),
                        }
                        for t in preview_txs[:8]
                    ]
                    st.dataframe(
                        pd.DataFrame(preview_rows),
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "Entrata": st.column_config.NumberColumn("Entrata", format="%.2f"),
                            "Uscita":  st.column_config.NumberColumn("Uscita",  format="%.2f"),
                        },
                    )
                else:
                    st.warning("⚠️ Nessuna riga parsata — verifica le colonne selezionate.")
            except Exception as e:
                st.caption(f"Anteprima non disponibile: {e}")

    if st.button("✅ Conferma schemi e importa", type="primary"):
        from core.orchestrator import process_file
        results = []
        prog = st.progress(0.0)
        with get_session(engine) as session:
            taxonomy_c = taxonomy
            user_rules_c = user_rules
            for i, entry in enumerate(pending):
                raw_bytes = entry["raw_bytes"]
                filename = entry["filename"]
                confirmed = confirmed_schemas[filename]
                prog.progress((i + 1) / len(pending))

                def _make_existing_checker(eng):
                    def _checker(tx_ids):
                        with get_session(eng) as s:
                            return get_existing_tx_ids(s, tx_ids)
                    return _checker

                result = process_file(
                    raw_bytes=raw_bytes,
                    filename=filename,
                    config=config,
                    taxonomy=taxonomy_c,
                    user_rules=user_rules_c,
                    known_schema=confirmed,
                    existing_tx_ids_checker=_make_existing_checker(engine),
                    account_label_override=entry.get("account_label_override"),
                )
                with get_session(engine) as s2:
                    persist_import_result(s2, result)
                results.append(result)

        st.session_state["_pending_schema_reviews"] = []
        existing = st.session_state.get("last_import_results", [])
        st.session_state["last_import_results"] = existing + results

        # Update job status message so the banner shows ✅ instead of ⏸️
        _pending_job_id = st.session_state.pop("_pending_schema_job_id", None)
        if _pending_job_id:
            n_confirmed = sum(len(r.transactions) for r in results)
            with get_session(engine) as _sj:
                update_import_job(_sj, _pending_job_id,
                                  status_message=f"✅ Completato — {n_confirmed:,} nuove transazioni")

        st.rerun()

    return True


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

    # Check for an active running job *before* rendering the form.
    # If a job is running: show only the progress view (block the form).
    # If completed/error: fall through and show form + summary below.
    with get_session(engine) as _js:
        _active_job = get_latest_import_job(_js)

    if _active_job and _active_job.status == "running":
        st.info("⏳ Importazione in corso — attendere il completamento prima di caricare nuovi file.")
        # Fragment polls every 2 s; calls st.rerun() when job completes to unlock the form.
        _render_job_status_poll(engine)
        return

    uploaded_files = st.file_uploader(
        "Carica uno o più file (CSV, XLSX, PDF)",
        type=["csv", "xls", "xlsx"],
        accept_multiple_files=True,
    )

    if not uploaded_files:
        st.info("Carica uno o più file per avviare l'elaborazione.")
        # Fragment polls every 2 s and detects jobs started from other browser sessions.
        _render_job_status_poll(engine)
        _render_last_import_summary()
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
                df_raw, _, _preprocess_info = load_raw_dataframe(raw_bytes, uf.name)
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
                if result.needs_schema_review:
                    # Do NOT persist — wait for user confirmation
                    pending = st.session_state.get("_pending_schema_reviews", [])
                    pending.append({
                        "raw_bytes": raw_bytes,
                        "filename": filename,
                        "result": result,
                        "account_label_override": _file_account_map.get(filename),
                    })
                    st.session_state["_pending_schema_reviews"] = pending
                    st.session_state["_pending_schema_job_id"] = job_id
                else:
                    persist_import_result(session2, result)
                results.append(result)
                _progress_bar.progress(file_end)

                # Write file-end to DB
                with get_session(engine) as s:
                    update_import_job(s, job_id, progress=file_end)

        n_pending = len(st.session_state.get("_pending_schema_reviews", []))
        n_tx = sum(len(r.transactions) for r in results if not r.needs_schema_review)
        n_skipped = sum(r.skipped_count for r in results)
        if n_pending:
            final_msg = f"⏸️ {n_pending} file in attesa di revisione schema"
        else:
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

        st.session_state["last_import_results"] = [r for r in results if not r.needs_schema_review]

    # Schema review gate: shown when a file has medium/low confidence
    if st.session_state.get("_pending_schema_reviews"):
        with get_session(engine) as _rs:
            _config = _build_config(engine)
            _taxonomy = get_taxonomy_config(_rs)
            _user_rules = get_category_rules(_rs)
        _render_schema_review(engine, _config, _taxonomy, _user_rules)
        return

    # Always rendered: reads job state from DB + summary from session_state.
    # Fragment auto-refreshes every 2 s — no time.sleep() needed here.
    _render_job_status_poll(engine)
    _render_last_import_summary()
