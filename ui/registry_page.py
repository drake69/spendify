"""Ledger page (RF-08): filterable transaction table + export."""
from __future__ import annotations

from decimal import Decimal

import pandas as pd
import streamlit as st

from db.models import get_session
from db.repository import get_transactions
from reports.generator import generate_csv_export, generate_xlsx_export
from support.logging import setup_logging

logger = setup_logging()

EXCLUDED_FROM_BALANCE = {"internal_out", "internal_in", "card_settlement", "aggregate_debit"}


def render_registry_page(engine):
    st.header("📋 Ledger — Registro Transazioni")

    session = get_session(engine)
    with session:
        # ── Filters ──────────────────────────────────────────────────────────
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            date_from = st.date_input("Da", value=None, key="ledger_from")
        with col2:
            date_to = st.date_input("A", value=None, key="ledger_to")
        with col3:
            tx_type_filter = st.selectbox(
                "Tipo transazione",
                ["tutti", "expense", "income", "card_tx", "internal_out", "internal_in",
                 "card_settlement", "unknown"],
            )
        with col4:
            review_only = st.checkbox("Solo da rivedere")

        filters = {}
        if date_from:
            filters["date_from"] = date_from.isoformat()
        if date_to:
            filters["date_to"] = date_to.isoformat()
        if tx_type_filter != "tutti":
            filters["tx_type"] = tx_type_filter
        if review_only:
            filters["to_review"] = True

        txs = get_transactions(session, filters=filters)

        if not txs:
            st.info("Nessuna transazione trovata.")
            return

        # ── Metrics ──────────────────────────────────────────────────────────
        net = sum(
            Decimal(str(tx.amount)) for tx in txs
            if tx.tx_type not in EXCLUDED_FROM_BALANCE
        )
        income = sum(
            Decimal(str(tx.amount)) for tx in txs
            if tx.tx_type not in EXCLUDED_FROM_BALANCE and Decimal(str(tx.amount)) > 0
        )
        expenses = sum(
            Decimal(str(tx.amount)) for tx in txs
            if tx.tx_type not in EXCLUDED_FROM_BALANCE and Decimal(str(tx.amount)) < 0
        )

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Transazioni", len(txs))
        m2.metric("Saldo Netto", f"{net:.2f} €")
        m3.metric("Entrate", f"{income:.2f} €")
        m4.metric("Uscite", f"{expenses:.2f} €")

        # ── Table ─────────────────────────────────────────────────────────────
        data = [
            {
                "Data": tx.date,
                "Descrizione": (tx.description or "")[:80],
                "Importo": float(tx.amount),
                "Valuta": tx.currency,
                "Tipo": tx.tx_type,
                "Categoria": tx.category or "",
                "Sottocategoria": tx.subcategory or "",
                "Conto": tx.account_label or "",
                "Conf.": tx.category_confidence or "",
                "Da rivedere": "⚠️" if tx.to_review else "",
                "id": tx.id,
            }
            for tx in txs
        ]
        df = pd.DataFrame(data)

        st.dataframe(
            df.drop(columns=["id"]),
            use_container_width=True,
            height=500,
        )

        # ── Export ────────────────────────────────────────────────────────────
        st.divider()
        ec1, ec2 = st.columns(2)
        with ec1:
            csv_bytes = generate_csv_export(session, filters=filters)
            st.download_button("📥 Esporta CSV", csv_bytes, "spendify_export.csv", "text/csv")
        with ec2:
            xlsx_bytes = generate_xlsx_export(session, filters=filters)
            st.download_button(
                "📥 Esporta XLSX", xlsx_bytes, "spendify_export.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
