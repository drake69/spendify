"""Storico Import — cronologia delle importazioni con possibilita di annullamento.

Mostra una tabella con tutti i batch importati (data/ora, file, conto,
numero transazioni, stato) e permette di annullare un'importazione
eliminando le transazioni associate.
"""
from __future__ import annotations

import streamlit as st

from services.import_service import ImportService
from support.logging import setup_logging

logger = setup_logging()


def render_history_page(engine) -> None:
    st.header("Storico import")

    svc = ImportService(engine)
    history = svc.get_import_history(limit=200)

    if not history:
        st.info("Nessuna importazione trovata.")
        return

    # ── Confirmation dialog state ────────────────────────────────────────────
    pending_cancel = st.session_state.get("_history_pending_cancel")

    if pending_cancel is not None:
        batch = pending_cancel
        st.warning(
            f"Confermi l'annullamento dell'importazione **{batch['filename']}** "
            f"({batch['n_transactions']} transazioni)? "
            f"Le transazioni verranno eliminate definitivamente."
        )
        col_confirm, col_abort = st.columns([1, 1])
        with col_confirm:
            if st.button("Conferma annullamento", key="confirm_cancel", type="primary"):
                deleted = svc.cancel_import(batch["id"])
                st.session_state.pop("_history_pending_cancel", None)
                st.success(f"Importazione annullata: {deleted} transazioni eliminate.")
                logger.info(
                    f"history_page: cancelled batch {batch['id']} "
                    f"({batch['filename']}), {deleted} transactions deleted"
                )
                st.rerun()
        with col_abort:
            if st.button("Annulla", key="abort_cancel"):
                st.session_state.pop("_history_pending_cancel", None)
                st.rerun()
        return  # Don't show the table while confirmation is pending

    # ── Table header ─────────────────────────────────────────────────────────
    _STATUS_LABELS = {
        "completed": "Completato",
        "cancelled": "Annullato",
    }

    _COL_WIDTHS = [2, 3, 2, 1.5, 1.5, 1.5]

    hdr = st.columns(_COL_WIDTHS)
    hdr[0].markdown("**Data/Ora**")
    hdr[1].markdown("**File**")
    hdr[2].markdown("**Conto**")
    hdr[3].markdown("**N. Transazioni**")
    hdr[4].markdown("**Stato**")
    hdr[5].markdown("**Azioni**")

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
                    "Annulla import",
                    key=f"cancel_{batch['id']}",
                    type="secondary",
                ):
                    st.session_state["_history_pending_cancel"] = batch
                    st.rerun()
            else:
                st.write("\u2014")
