"""Import page (RF-08): upload files, run pipeline, show summary."""
from __future__ import annotations

import time
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from services.import_service import (
    Confidence,
    DocumentSchema,
    DocumentType,
    FileAnalysis,
    ImportService,
    ProcessingConfig,
    SignConvention,
)
from services.settings_service import SettingsService
from support.logging import setup_logging

logger = setup_logging()

# Minimum seconds between DB progress writes (throttle for high-frequency callbacks)
_DB_WRITE_INTERVAL = 1.5


@st.fragment(run_every="2s")
def _render_job_status_poll(import_svc: ImportService) -> None:
    """Auto-refreshing job-status fragment (polls DB every 2 s).

    Renders progress/result widgets without blocking the main script thread,
    so no ghost content from the previous page ever appears.

    Uses session_state to detect state transitions:
    - not running → running : full app rerun to lock the file-uploader form.
    - running → completed/error : full app rerun to unlock the form.
    """
    job = import_svc.get_latest_job()
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
            st.rerun()

    elif job.status == "completed":
        st.success(job.status_message or "✅ Completato")
        st.progress(1.0)
        if job.detail_message:
            st.caption(job.detail_message)
        if was_running:
            st.session_state["_upload_job_was_running"] = False
            st.rerun()

    elif job.status == "error":
        st.error(job.status_message or "❌ Errore durante l'importazione")
        if was_running:
            st.session_state["_upload_job_was_running"] = False
            st.rerun()


_SKIP_REASON_LABELS: dict[str, str] = {
    "date_nan": "Data mancante (NaN)",
    "date_parse": "Data non parsabile",
    "amount_none": "Importo non parsabile",
    "amount_none_dc": "Importo: entrambe le colonne Dare/Avere vuote",
    "balance_row": "Riga saldo carta rimossa",
    "merged": "Duplicato intra-file (aggregato)",
}


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
                # ── Row-level breakdown ──
                n_new = len(result.transactions)
                n_dedup = result.skipped_count
                n_skipped = len(result.skipped_rows) if result.skipped_rows else 0
                n_merged = result.merged_count
                n_header = result.header_rows_skipped
                n_total = result.total_file_rows

                n_giro = getattr(result, "internal_transfer_count", 0)

                cols = st.columns(7)
                cols[0].metric("Righe E/C", n_total + n_header,
                               help="Righe totali nel file (intestazione + dati)")
                cols[1].metric("Intestazione", n_header)
                cols[2].metric("Importate", n_new)
                cols[3].metric("Già presenti", n_dedup)
                cols[4].metric("Scartate", n_skipped)
                cols[5].metric("Aggregate", n_merged,
                               help="Righe duplicate nel file sommate in un'unica transazione")
                cols[6].metric("Giroconti", n_giro,
                               help="Trasferimenti interni tra conti propri (salvati nel ledger, esclusi dai report)")

                # Sanity check: if numbers don't add up, show a warning
                accounted = n_new + n_dedup + n_skipped + n_merged
                if n_total and accounted < n_total:
                    st.caption(
                        f"⚠️ {n_total - accounted} righe non contabilizzate "
                        f"(righe dati={n_total}, importate={n_new}, già presenti={n_dedup}, "
                        f"scartate={n_skipped}, aggregate={n_merged})"
                    )

                if result.skipped_count and not result.transactions:
                    st.info("⏭️ Tutte le transazioni erano già presenti nel database.")
                    continue

                to_review = sum(1 for tx in result.transactions if tx.get("to_review"))
                if to_review:
                    st.warning(f"{to_review} transazioni richiedono revisione manuale → pagina Review")

                # ── Skipped rows detail ──
                if result.skipped_rows:
                    # Group by reason
                    from collections import Counter
                    reason_counts = Counter(s.reason for s in result.skipped_rows)
                    reason_summary = ", ".join(
                        f"{_SKIP_REASON_LABELS.get(r, r)}: {c}"
                        for r, c in reason_counts.most_common()
                    )
                    st.warning(f"⚠️ {n_skipped} righe scartate — {reason_summary}")

                    with st.expander(f"🔍 Dettaglio {n_skipped} righe scartate", expanded=False):
                        skip_rows_data = [
                            {
                                "Riga": s.row_index + 1,
                                "Motivo": _SKIP_REASON_LABELS.get(s.reason, s.reason),
                                **{k: v for k, v in s.raw_values.items()},
                            }
                            for s in result.skipped_rows
                        ]
                        st.dataframe(
                            pd.DataFrame(skip_rows_data),
                            use_container_width=True,
                            hide_index=True,
                        )

                if result.doc_schema:
                    _conf_score = getattr(result.doc_schema, 'confidence_score', 0.0)
                    st.caption(
                        f"Schema: {result.doc_schema.doc_type} | "
                        f"Account: {result.doc_schema.account_label} | "
                        f"Confidence: {_conf_score:.2f} ({result.doc_schema.confidence})"
                    )


def _render_schema_review(import_svc: ImportService, config: ProcessingConfig) -> bool:
    """Show editable schema form for files with medium/low confidence.
    Returns True if the user confirmed and re-import was triggered."""
    pending = st.session_state.get("_pending_schema_reviews", [])
    if not pending:
        return False

    # Deduplica per filename
    seen: set[str] = set()
    pending = [e for e in pending if not (e["filename"] in seen or seen.add(e["filename"]))]  # type: ignore[func-returns-value]

    st.warning(
        f"⚠️ **Revisione schema richiesta** — {len(pending)} file con classificazione incerta. "
        "Verifica i campi rilevati e conferma prima di importare."
    )

    doc_type_options = [d.value for d in DocumentType]
    sign_options = [s.value for s in SignConvention]
    none_option = "— nessuna —"

    confirmed_schemas: dict[str, DocumentSchema] = {}

    for _idx, entry in enumerate(pending):
        filename = entry["filename"]
        _key = f"{_idx}_{filename}"
        result = entry["result"]
        schema = result.doc_schema
        cols = result.available_columns
        col_options = [none_option] + cols

        evidence = schema.semantic_evidence if schema else []
        _conf_score = getattr(schema, 'confidence_score', 0.0) if schema else 0.0
        confidence_label = schema.confidence.value if schema else "unknown"

        with st.expander(f"📄 {filename}  [confidence: {_conf_score:.2f} ({confidence_label})]", expanded=True):
            if evidence:
                st.caption("Ragionamento LLM: " + " · ".join(evidence))

            # Raw file preview — only when no rows to skip (otherwise it shows garbage pre-header data)
            if not (schema and schema.skip_rows and schema.skip_rows > 0):
                st.markdown("**Struttura raw del file (prime 10 righe, senza pre-elaborazione):**")
                try:
                    df_raw_preview = import_svc.get_raw_head(entry["raw_bytes"], filename, n=10)
                    st.dataframe(df_raw_preview, use_container_width=True, hide_index=False)
                except Exception as e:
                    st.caption(f"Anteprima raw non disponibile: {e}")

            c1, c2 = st.columns(2)
            doc_type_val = schema.doc_type.value if schema else doc_type_options[0]
            doc_type_sel = c1.selectbox(
                "Tipo documento", doc_type_options,
                index=doc_type_options.index(doc_type_val) if doc_type_val in doc_type_options else 0,
                key=f"rev_doc_type_{_key}",
            )
            account_sel = c2.text_input(
                "Account label", value=schema.account_label if schema else "",
                key=f"rev_account_{_key}",
            )

            c3, c4, c5 = st.columns(3)

            def _col_idx(val):
                return col_options.index(val) if val in col_options else 0

            amount_sel = c3.selectbox(
                "Colonna importo", col_options,
                index=_col_idx(schema.amount_col if schema else none_option),
                key=f"rev_amount_{_key}",
            )
            date_sel = c4.selectbox(
                "Colonna data", col_options,
                index=_col_idx(schema.date_col if schema else none_option),
                key=f"rev_date_{_key}",
            )
            sign_val = schema.sign_convention.value if schema else sign_options[0]
            sign_sel = c5.selectbox(
                "Convenzione segno", sign_options,
                index=sign_options.index(sign_val) if sign_val in sign_options else 0,
                key=f"rev_sign_{_key}",
            )

            c6, c7, c8, c9 = st.columns(4)
            description_sel = c6.selectbox(
                "Colonna descrizione", col_options,
                index=_col_idx(schema.description_col if schema and schema.description_col else none_option),
                key=f"rev_description_{_key}",
            )
            debit_sel = c7.selectbox(
                "Colonna addebiti (opt.)", col_options,
                index=_col_idx(schema.debit_col if schema and schema.debit_col else none_option),
                key=f"rev_debit_{_key}",
            )
            credit_sel = c8.selectbox(
                "Colonna accrediti (opt.)", col_options,
                index=_col_idx(schema.credit_col if schema and schema.credit_col else none_option),
                key=f"rev_credit_{_key}",
            )
            invert = c9.checkbox(
                "Inverti segno", value=schema.invert_sign if schema else False,
                key=f"rev_invert_{_key}",
            )

            # Build confirmed schema from user selections
            base = schema.model_copy() if schema else DocumentSchema(
                doc_type=DocumentType(doc_type_options[0]),
                date_col="", amount_col="", sign_convention=SignConvention(sign_options[0]),
                date_format="%d/%m/%Y", account_label="", confidence=Confidence.high,
            )
            confirmed_schemas[filename] = base.model_copy(update={
                "doc_type":         DocumentType(doc_type_sel),
                "account_label":    account_sel,
                "amount_col":       "" if amount_sel == none_option else amount_sel,
                "date_col":         "" if date_sel == none_option else date_sel,
                "description_col":  None if description_sel == none_option else description_sel,
                "sign_convention":  SignConvention(sign_sel),
                "debit_col":        None if debit_sel == none_option else debit_sel,
                "credit_col":       None if credit_sel == none_option else credit_sel,
                "invert_sign":      invert,
                "confidence":       Confidence.high,
                "confidence_score": 1.0,  # user-confirmed schema is fully trusted
                "skip_rows":        schema.skip_rows if schema else 0,
                "header_sha256":    entry.get("header_sha256", ""),
            })

            # Normalised preview
            st.markdown("**Anteprima normalizzata** — così appariranno nel ledger:")
            try:
                _skip_override = (schema.skip_rows or None) if schema else None
                preview_txs = import_svc.get_normalized_preview(
                    entry["raw_bytes"], filename,
                    confirmed_schemas[filename],
                    n=30,
                    skip_rows_override=_skip_override,
                )
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
        results = []
        prog = st.progress(0.0)
        for i, entry in enumerate(pending):
            raw_bytes = entry["raw_bytes"]
            filename = entry["filename"]
            confirmed = confirmed_schemas[filename]
            prog.progress((i + 1) / len(pending))

            result = import_svc.process_file_single(
                raw_bytes=raw_bytes,
                filename=filename,
                config=config,
                known_schema=confirmed,
                account_label_override=entry.get("account_label_override"),
            )
            import_svc.persist_result(result)
            results.append(result)

        st.session_state["_pending_schema_reviews"] = []
        existing = st.session_state.get("last_import_results", [])
        st.session_state["last_import_results"] = existing + results

        # Update job status so the banner shows ✅ instead of ⏸️
        _pending_job_id = st.session_state.pop("_pending_schema_job_id", None)
        if _pending_job_id:
            n_confirmed = sum(len(r.transactions) for r in results)
            import_svc.update_job(
                _pending_job_id,
                status_message=f"✅ Completato — {n_confirmed:,} nuove transazioni",
            )

        st.rerun()

    return True


def render_upload_page(engine):
    st.header("📥 Import — Caricamento Estratti Conto")

    import_svc = ImportService(engine)
    cfg_svc    = SettingsService(engine)

    # Guard: owner names must be configured before any import
    if not import_svc.get_owner_names():
        st.error(
            "⚠️ **Nomi titolari non configurati.** "
            "Vai in ⚙️ Impostazioni e compila il campo *Nomi titolari* prima di importare."
        )
        return

    # Check for an active running job before rendering the form.
    active_job = import_svc.get_latest_job()
    if active_job and active_job.status == "running":
        st.info("⏳ Importazione in corso — attendere il completamento prima di caricare nuovi file.")
        _render_job_status_poll(import_svc)
        return

    uploaded_files = st.file_uploader(
        "Carica uno o più file (CSV, XLSX, PDF)",
        type=["csv", "xls", "xlsx"],
        accept_multiple_files=True,
    )

    if not uploaded_files:
        st.info("Carica uno o più file per avviare l'elaborazione.")
        _render_job_status_poll(import_svc)
        _render_last_import_summary()
        return

    # Per-file account selector
    _accounts     = cfg_svc.get_accounts()
    _account_names  = [a.name for a in _accounts]
    _account_options = ["— rilevamento automatico —"] + _account_names

    if not _account_names:
        st.warning(
            "⚠️ Nessun conto configurato. Vai in ⚙️ Impostazioni → *Conti bancari* per aggiungere "
            "i tuoi conti. Puoi importare comunque ma il dedup potrebbe non essere stabile."
        )

    st.markdown("**Associa ogni file a un conto:**")
    _file_account_map: dict[str, str | None] = {}
    _file_skip_map: dict[str, int] = {}

    for uf in uploaded_files:
        raw_preview = uf.getvalue()
        _detected_skip, _skip_certain = import_svc.detect_skip_rows(raw_preview, uf.name)
        _sha256_hit = import_svc.find_schema_by_header(raw_preview, uf.name)

        if _sha256_hit or _skip_certain:
            c1, c2 = st.columns([3, 2])
            c1.caption(f"📄 {uf.name}")
            sel = c2.selectbox(
                "Conto",
                options=_account_options,
                key=f"file_account_{uf.name}",
                label_visibility="collapsed",
            )
            _file_skip_map[uf.name] = _sha256_hit.skip_rows if _sha256_hit else _detected_skip
        else:
            c1, c2, c3 = st.columns([3, 2, 2])
            c1.caption(f"📄 {uf.name}")
            sel = c2.selectbox(
                "Conto",
                options=_account_options,
                key=f"file_account_{uf.name}",
                label_visibility="collapsed",
            )
            _file_skip_map[uf.name] = c3.number_input(
                "Righe da saltare",
                min_value=0,
                max_value=20,
                value=0,
                key=f"file_skip_{uf.name}",
                help="Non riesco a rilevare automaticamente quante righe saltare prima dell'intestazione. Controlla il file e inserisci il numero corretto.",
            )
        _file_account_map[uf.name] = sel if sel != "— rilevamento automatico —" else None

    if st.button("▶️ Elabora file", type="primary"):
        giroconto_mode = st.session_state.get("giroconto_mode", "neutral")
        config = import_svc.build_config(giroconto_mode=giroconto_mode)

        # Analyse each file (schema cache lookup)
        files: list[tuple[bytes, str, int]] = []
        known_schemas: dict[str, object] = {}
        file_header_sha256: dict[str, str] = {}

        for uf in uploaded_files:
            raw_bytes = uf.read()
            analysis: FileAnalysis = import_svc.analyze_file(raw_bytes, uf.name)
            file_header_sha256[uf.name] = analysis.header_sha256
            if analysis.known_schema:
                logger.info(f"upload_page: schema cache hit for {uf.name}")
                known_schemas[uf.name] = analysis.known_schema
            files.append((raw_bytes, uf.name, analysis.n_rows))

        total_files = len(files)

        # Create job record
        job = import_svc.create_job(n_files=total_files)
        job_id = job.id

        # Live progress widgets for this session
        _progress_bar = st.progress(0.0)
        _status_text  = st.empty()
        _counter_text = st.empty()

        results = []
        for i, (raw_bytes, filename, _n_rows) in enumerate(files):
            file_start = i / total_files
            file_end   = (i + 1) / total_files

            _status_text.text(f"File {i + 1}/{total_files} — {filename}")
            _counter_text.caption("Avvio elaborazione...")

            import_svc.update_job(
                job_id,
                progress=file_start,
                status_message=f"File {i + 1}/{total_files} — {filename}",
            )

            _last_db_write = [0.0]

            def _make_cb(start: float, end: float, fname: str,
                         fidx: int, ftot: int, jid: int, _last: list):
                def _cb(p: float):
                    pct = start + (end - start) * p
                    _progress_bar.progress(min(pct, 1.0))
                    _status_text.text(f"File {fidx + 1}/{ftot} — {fname}")
                    _counter_text.caption(f"Avanzamento file: {int(p * 100)}%")
                    now = time.time()
                    if now - _last[0] >= _DB_WRITE_INTERVAL:
                        _last[0] = now
                        import_svc.update_job(
                            jid,
                            progress=round(pct, 4),
                            status_message=f"File {fidx + 1}/{ftot} — {fname} ({int(p * 100)}%)",
                        )
                return _cb

            result = import_svc.process_file_single(
                raw_bytes=raw_bytes,
                filename=filename,
                config=config,
                known_schema=known_schemas.get(filename),
                progress_callback=_make_cb(
                    file_start, file_end, filename, i, total_files,
                    job_id, _last_db_write,
                ),
                account_label_override=_file_account_map.get(filename),
                skip_rows_override=_file_skip_map.get(filename),
            )

            if result.needs_schema_review:
                pending = st.session_state.get("_pending_schema_reviews", [])
                pending.append({
                    "raw_bytes":              raw_bytes,
                    "filename":               filename,
                    "result":                 result,
                    "account_label_override": _file_account_map.get(filename),
                    "header_sha256":          file_header_sha256.get(filename, ""),
                })
                st.session_state["_pending_schema_reviews"] = pending
                st.session_state["_pending_schema_job_id"]  = job_id
            else:
                import_svc.persist_result(result)
                # Show auto-import success for high-confidence Flow 2 files
                if result.flow_used == "flow2" and result.doc_schema:
                    _auto_score = getattr(result.doc_schema, 'confidence_score', 0.0)
                    if _auto_score >= 0.80:
                        st.success(
                            f"✅ {filename} — auto-importato con confidence {_auto_score:.2f} "
                            f"({result.doc_schema.confidence})"
                        )

            results.append(result)
            _progress_bar.progress(file_end)
            import_svc.update_job(job_id, progress=file_end)

        n_pending = len(st.session_state.get("_pending_schema_reviews", []))
        n_tx      = sum(len(r.transactions) for r in results if not r.needs_schema_review)
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

        import_svc.update_job(
            job_id,
            status="completed",
            progress=1.0,
            status_message=final_msg,
            detail_message=final_detail,
            n_transactions=n_tx,
            completed_at=datetime.now(timezone.utc),
        )

        st.session_state["last_import_results"] = [r for r in results if not r.needs_schema_review]

    # Schema review gate
    if st.session_state.get("_pending_schema_reviews"):
        giroconto_mode = st.session_state.get("giroconto_mode", "neutral")
        config = import_svc.build_config(giroconto_mode=giroconto_mode)
        _render_schema_review(import_svc, config)
        return

    _render_job_status_poll(import_svc)
    _render_last_import_summary()
