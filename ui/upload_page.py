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
from ui.i18n import t as t_fn

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
        msg = job.status_message or t_fn("upload.processing")
        st.info(f"⏳ {msg}")
        st.progress(pct)
        st.caption(t_fn("upload.progress_auto", pct=int(pct * 100)))
        st.session_state["_upload_job_was_running"] = True
        if not was_running:
            st.rerun()

    elif job.status == "completed":
        st.success(job.status_message or t_fn("upload.completed"))
        st.progress(1.0)
        if job.detail_message:
            st.caption(job.detail_message)
        if was_running:
            st.session_state["_upload_job_was_running"] = False
            st.rerun()

    elif job.status == "error":
        st.error(job.status_message or t_fn("upload.error_import"))
        if was_running:
            st.session_state["_upload_job_was_running"] = False
            st.rerun()


def _skip_reason_labels() -> dict[str, str]:
    return {
        "date_nan": t_fn("upload.skip.date_nan"),
        "date_parse": t_fn("upload.skip.date_parse"),
        "amount_none": t_fn("upload.skip.amount_none"),
        "amount_none_dc": t_fn("upload.skip.amount_none_dc"),
        "balance_row": t_fn("upload.skip.balance_row"),
        "merged": t_fn("upload.skip.merged"),
    }


def _render_last_import_summary():
    results = st.session_state.get("last_import_results")
    if not results:
        return
    st.divider()
    st.subheader(t_fn("upload.summary_title"))
    for result in results:
        with st.expander(f"📄 {result.source_name}", expanded=True):
            if result.errors:
                st.error(t_fn("upload.errors") + ": " + "; ".join(result.errors))
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
                cols[0].metric(t_fn("upload.metric.file_rows"), n_total + n_header,
                               help=t_fn("upload.metric.file_rows_help"))
                cols[1].metric(t_fn("upload.metric.header"), n_header)
                cols[2].metric(t_fn("upload.metric.imported"), n_new)
                cols[3].metric(t_fn("upload.metric.already_present"), n_dedup)
                cols[4].metric(t_fn("upload.metric.skipped"), n_skipped)
                cols[5].metric(t_fn("upload.metric.aggregated"), n_merged,
                               help=t_fn("upload.metric.aggregated_help"))
                cols[6].metric(t_fn("upload.metric.transfers"), n_giro,
                               help=t_fn("upload.metric.transfers_help"))

                # Sanity check: if numbers don't add up, show a warning
                accounted = n_new + n_dedup + n_skipped + n_merged
                if n_total and accounted < n_total:
                    st.caption(
                        t_fn("upload.unaccounted_rows",
                          unaccounted=n_total - accounted,
                          total=n_total, imported=n_new,
                          dedup=n_dedup, skipped=n_skipped,
                          merged=n_merged)
                    )

                if result.skipped_count and not result.transactions:
                    st.info(t_fn("upload.all_already_present"))
                    continue

                to_review = sum(1 for tx in result.transactions if tx.get("to_review"))
                if to_review:
                    st.warning(t_fn("upload.needs_review", n=to_review))

                # ── Skipped rows detail ──
                if result.skipped_rows:
                    # Group by reason
                    from collections import Counter
                    reason_counts = Counter(s.reason for s in result.skipped_rows)
                    reason_summary = ", ".join(
                        f"{_skip_reason_labels().get(r, r)}: {c}"
                        for r, c in reason_counts.most_common()
                    )
                    st.warning(t_fn("upload.rows_skipped", n=n_skipped, reasons=reason_summary))

                    with st.expander(t_fn("upload.skipped_detail", n=n_skipped), expanded=False):
                        skip_rows_data = [
                            {
                                t_fn("upload.col.row"): s.row_index + 1,
                                t_fn("upload.col.reason"): _skip_reason_labels().get(s.reason, s.reason),
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

    st.warning(t_fn("upload.schema_review_warning", n=len(pending)))

    doc_type_options = [d.value for d in DocumentType]
    sign_options = [s.value for s in SignConvention]
    none_option = t_fn("upload.schema.none_option")

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
                st.caption(t_fn("upload.schema.llm_reasoning") + " " + " · ".join(evidence))

            # Raw file preview — preheader (top) and footer (bottom) in collapsible sections
            try:
                _skip = schema.skip_rows if schema else 0
                _n_preview = max(30, _skip + 15)
                df_raw_preview = import_svc.get_raw_head(entry["raw_bytes"], filename, n=_n_preview)
                if _skip > 0 and len(df_raw_preview) > 0:
                    _preheader_df = df_raw_preview.iloc[:_skip]
                    if not _preheader_df.empty:
                        with st.expander(t_fn("upload.schema.preheader", n=_skip), expanded=False):
                            st.dataframe(_preheader_df, use_container_width=True, hide_index=False)
                # Show data portion (first 10 rows after header)
                st.markdown(t_fn("upload.schema.data_preview"))
                _data_start = _skip
                _data_end = min(_data_start + 10, len(df_raw_preview))
                st.dataframe(
                    df_raw_preview.iloc[_data_start:_data_end],
                    use_container_width=True, hide_index=False,
                )
                # Footer preview: show last 5 rows of the raw file
                _total_raw = import_svc.get_raw_head(entry["raw_bytes"], filename, n=500)
                if len(_total_raw) > 10:
                    _footer_df = _total_raw.tail(5)
                    with st.expander(t_fn("upload.schema.footer_preview"), expanded=False):
                        st.dataframe(_footer_df, use_container_width=True, hide_index=False)
            except Exception as e:
                st.caption(t_fn("upload.schema.preview_unavailable", error=e))

            c1, c2 = st.columns(2)
            doc_type_val = schema.doc_type.value if schema else doc_type_options[0]
            doc_type_sel = c1.selectbox(
                t_fn("upload.schema.doc_type"), doc_type_options,
                index=doc_type_options.index(doc_type_val) if doc_type_val in doc_type_options else 0,
                key=f"rev_doc_type_{_key}",
            )
            account_sel = c2.text_input(
                t_fn("upload.schema.account_label"), value=schema.account_label if schema else "",
                key=f"rev_account_{_key}",
            )

            c3, c4, c5 = st.columns(3)

            def _col_idx(val):
                return col_options.index(val) if val in col_options else 0

            amount_sel = c3.selectbox(
                t_fn("upload.schema.amount_col"), col_options,
                index=_col_idx(schema.amount_col if schema else none_option),
                key=f"rev_amount_{_key}",
            )
            date_sel = c4.selectbox(
                t_fn("upload.schema.date_col"), col_options,
                index=_col_idx(schema.date_col if schema else none_option),
                key=f"rev_date_{_key}",
            )
            sign_val = schema.sign_convention.value if schema else sign_options[0]
            sign_sel = c5.selectbox(
                t_fn("upload.schema.sign_convention"), sign_options,
                index=sign_options.index(sign_val) if sign_val in sign_options else 0,
                key=f"rev_sign_{_key}",
            )

            c6, c7, c8, c9 = st.columns(4)
            description_sel = c6.selectbox(
                t_fn("upload.schema.description_col"), col_options,
                index=_col_idx(schema.description_col if schema and schema.description_col else none_option),
                key=f"rev_description_{_key}",
            )
            debit_sel = c7.selectbox(
                t_fn("upload.schema.debit_col"), col_options,
                index=_col_idx(schema.debit_col if schema and schema.debit_col else none_option),
                key=f"rev_debit_{_key}",
            )
            credit_sel = c8.selectbox(
                t_fn("upload.schema.credit_col"), col_options,
                index=_col_idx(schema.credit_col if schema and schema.credit_col else none_option),
                key=f"rev_credit_{_key}",
            )
            invert = c9.checkbox(
                t_fn("upload.schema.invert_sign"), value=schema.invert_sign if schema else False,
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
            st.markdown(t_fn("upload.schema.normalized_preview"))
            try:
                _skip_override = (schema.skip_rows or None) if schema else None
                preview_txs = import_svc.get_normalized_preview(
                    entry["raw_bytes"], filename,
                    confirmed_schemas[filename],
                    n=30,
                    skip_rows_override=_skip_override,
                )
                if preview_txs:
                    _col_date = t_fn("ledger.col.date")
                    _col_desc = t_fn("ledger.col.description")
                    _col_in   = t_fn("ledger.col.income")
                    _col_out  = t_fn("ledger.col.expense")
                    _col_cur  = t_fn("upload.col.currency")
                    _col_raw_desc = t_fn("upload.col.raw_description")
                    _col_raw_amt  = t_fn("upload.col.raw_amount")
                    preview_rows = [
                        {
                            _col_date:     str(tx.get("date", "")),
                            _col_desc:     (tx.get("description") or "")[:80],
                            _col_in:       float(tx["amount"]) if tx.get("amount") is not None and float(tx["amount"]) > 0 else None,
                            _col_out:      abs(float(tx["amount"])) if tx.get("amount") is not None and float(tx["amount"]) < 0 else None,
                            _col_cur:      tx.get("currency", "EUR"),
                            _col_raw_desc: (tx.get("raw_description") or "")[:80],
                            _col_raw_amt:  str(tx.get("raw_amount", "")),
                        }
                        for tx in preview_txs[:8]
                    ]
                    st.dataframe(
                        pd.DataFrame(preview_rows),
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            _col_in:  st.column_config.NumberColumn(_col_in,  format="%.2f"),
                            _col_out: st.column_config.NumberColumn(_col_out, format="%.2f"),
                        },
                    )
                else:
                    st.warning(t_fn("upload.schema.no_rows_parsed"))
            except Exception as e:
                st.caption(t_fn("upload.schema.preview_unavailable", error=e))

    if st.button(t_fn("upload.schema.confirm_btn"), type="primary"):
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
                status_message=t_fn("upload.completed_tx", n=f"{n_confirmed:,}"),
            )

        st.rerun()

    return True


def render_upload_page(engine):
    st.header(t_fn("upload.title"))

    import_svc = ImportService(engine)
    cfg_svc    = SettingsService(engine)

    # Guard: owner names must be configured before any import
    if not import_svc.get_owner_names():
        st.error(t_fn("upload.owner_names_missing"))
        return

    # Check for an active running job before rendering the form.
    active_job = import_svc.get_latest_job()
    if active_job and active_job.status == "running":
        st.info(t_fn("upload.job_running"))
        _render_job_status_poll(import_svc)
        return

    uploaded_files = st.file_uploader(
        t_fn("upload.file_uploader_label"),
        type=["csv", "xls", "xlsx"],
        accept_multiple_files=True,
    )

    if not uploaded_files:
        st.info(t_fn("upload.no_files_hint"))
        _render_job_status_poll(import_svc)
        _render_last_import_summary()
        return

    # Per-file account selector
    _accounts     = cfg_svc.get_accounts()
    _account_names  = [a.name for a in _accounts]
    _account_options = [t_fn("upload.auto_detect")] + _account_names

    if not _account_names:
        st.warning(t_fn("upload.no_accounts_warning"))

    st.markdown(t_fn("upload.associate_files"))
    _file_account_map: dict[str, str | None] = {}
    _file_skip_map: dict[str, int] = {}

    for uf in uploaded_files:
        raw_preview = uf.getvalue()
        _detected_skip, _skip_certain = import_svc.detect_skip_rows(raw_preview, uf.name)
        _sha256_hit = import_svc.find_schema_by_header(raw_preview, uf.name)

        # ── I-02: auto-select account from cached schema ─────────────
        _auto_account_idx = 0  # default: "— rilevamento automatico —"
        if _sha256_hit and getattr(_sha256_hit, "account_label", None):
            _cached_label = _sha256_hit.account_label
            if _cached_label in _account_names:
                _auto_account_idx = _account_names.index(_cached_label) + 1  # +1 for the "auto" option

        if _sha256_hit or _skip_certain:
            c1, c2 = st.columns([3, 2])
            c1.caption(f"📄 {uf.name}")
            sel = c2.selectbox(
                t_fn("ledger.col.account"),
                options=_account_options,
                index=_auto_account_idx,
                key=f"file_account_{uf.name}",
                label_visibility="collapsed",
            )
            _file_skip_map[uf.name] = _sha256_hit.skip_rows if _sha256_hit else _detected_skip
        else:
            # ── I-01: warning primo caricamento ──────────────────────
            _row_count = raw_preview.count(b"\n") if isinstance(raw_preview, bytes) else 50
            if _row_count < 50:
                st.warning(t_fn("upload.first_upload_warning", filename=uf.name, rows=_row_count))
            c1, c2, c3 = st.columns([3, 2, 2])
            c1.caption(f"📄 {uf.name}")
            sel = c2.selectbox(
                t_fn("ledger.col.account"),
                options=_account_options,
                index=_auto_account_idx,
                key=f"file_account_{uf.name}",
                label_visibility="collapsed",
            )
            _file_skip_map[uf.name] = c3.number_input(
                t_fn("upload.skip_rows_label"),
                min_value=0,
                max_value=20,
                value=0,
                key=f"file_skip_{uf.name}",
                help=t_fn("upload.skip_rows_help"),
            )
        _file_account_map[uf.name] = sel if sel != t_fn("upload.auto_detect") else None

    if st.button(t_fn("upload.process_btn"), type="primary"):
        st.session_state["llm_in_progress"] = True
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
            _counter_text.caption(t_fn("upload.starting"))

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
                    _counter_text.caption(t_fn("upload.file_progress", pct=int(p * 100)))
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
                            t_fn("upload.auto_imported", filename=filename,
                                 score=f"{_auto_score:.2f}",
                                 confidence=result.doc_schema.confidence)
                        )

            results.append(result)
            _progress_bar.progress(file_end)
            import_svc.update_job(job_id, progress=file_end)

        n_pending = len(st.session_state.get("_pending_schema_reviews", []))
        n_tx      = sum(len(r.transactions) for r in results if not r.needs_schema_review)
        n_skipped = sum(r.skipped_count for r in results)

        if n_pending:
            final_msg = t_fn("upload.pending_review", n=n_pending)
        else:
            final_msg = t_fn("upload.completed_tx", n=n_tx)
        if n_skipped:
            final_msg += t_fn("upload.already_present_skipped", n=n_skipped)

        final_detail = t_fn("upload.final_detail",
                            n_files=total_files,
                            time=datetime.now(timezone.utc).strftime('%H:%M:%S'))

        import_svc.update_job(
            job_id,
            status="completed",
            progress=1.0,
            status_message=final_msg,
            detail_message=final_detail,
            n_transactions=n_tx,
            completed_at=datetime.now(timezone.utc),
        )

        st.session_state["llm_in_progress"] = False
        st.session_state["last_import_results"] = [r for r in results if not r.needs_schema_review]

    # Schema review gate
    if st.session_state.get("_pending_schema_reviews"):
        giroconto_mode = st.session_state.get("giroconto_mode", "neutral")
        config = import_svc.build_config(giroconto_mode=giroconto_mode)
        _render_schema_review(import_svc, config)
        return

    _render_job_status_poll(import_svc)
    _render_last_import_summary()
