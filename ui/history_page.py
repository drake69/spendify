"""Storico Import — cronologia delle importazioni con possibilita di annullamento."""
from __future__ import annotations

import streamlit as st

from services.import_service import ImportService
from support.logging import setup_logging
from ui.i18n import t

logger = setup_logging()


def render_history_page(engine) -> None:
    st.header(t("history.title"))

    svc = ImportService(engine)
    history = svc.get_import_history(limit=200)

    if not history:
        st.info(t("history.no_imports"))
        return

    # ── Confirmation dialog state ────────────────────────────────────────────
    pending_cancel = st.session_state.get("_history_pending_cancel")

    if pending_cancel is not None:
        batch = pending_cancel
        st.warning(
            t("history.confirm_cancel",
              filename=batch['filename'],
              n=batch['n_transactions'])
        )
        col_confirm, col_abort = st.columns([1, 1])
        with col_confirm:
            if st.button(t("history.confirm_btn"), key="confirm_cancel", type="primary"):
                deleted = svc.cancel_import(batch["id"])
                st.session_state.pop("_history_pending_cancel", None)
                st.success(t("history.cancelled", n=deleted))
                logger.info(
                    f"history_page: cancelled batch {batch['id']} "
                    f"({batch['filename']}), {deleted} transactions deleted"
                )
                st.rerun()
        with col_abort:
            if st.button(t("common.cancel"), key="abort_cancel"):
                st.session_state.pop("_history_pending_cancel", None)
                st.rerun()
        return

    # ── Table header ─────────────────────────────────────────────────────────
    _STATUS_LABELS = {
        "completed": t("history.status_completed"),
        "cancelled": t("history.status_cancelled"),
    }

    _COL_WIDTHS = [2, 3, 2, 1.5, 1.5, 1.5]

    hdr = st.columns(_COL_WIDTHS)
    hdr[0].markdown(f"**{t('history.col.datetime')}**")
    hdr[1].markdown(f"**{t('history.col.file')}**")
    hdr[2].markdown(f"**{t('history.col.account')}**")
    hdr[3].markdown(f"**{t('history.col.n_transactions')}**")
    hdr[4].markdown(f"**{t('history.col.status')}**")
    hdr[5].markdown(f"**{t('history.col.actions')}**")

    st.divider()

    # ── Table rows ───────────────────────────────────────────────────────────
    for batch in history:
        imported_at = batch["imported_at"]
        if imported_at:
            dt_str = imported_at.strftime("%d/%m/%Y %H:%M")
        else:
            dt_str = "\u2014"

        status_label = _STATUS_LABELS.get(batch["status"], batch["status"])
        is_cancelled = batch["status"] == "cancelled"

        cols = st.columns(_COL_WIDTHS)
        cols[0].write(dt_str)
        cols[1].write(batch["filename"])
        cols[2].write(batch["account_label"] or "\u2014")
        cols[3].write(str(batch["n_transactions"]))
        cols[4].write(status_label)

        with cols[5]:
            if not is_cancelled and batch["n_transactions"] > 0:
                if st.button(
                    t("history.cancel_import"),
                    key=f"cancel_{batch['id']}",
                    type="secondary",
                ):
                    st.session_state["_history_pending_cancel"] = batch
                    st.rerun()
            else:
                st.write("\u2014")
