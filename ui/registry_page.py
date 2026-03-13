"""Ledger page (RF-08): filterable transaction table + export."""
from __future__ import annotations

from decimal import Decimal

import pandas as pd
import streamlit as st

from db.models import get_session
from db.repository import get_all_user_settings, get_transactions
from reports.generator import generate_csv_export, generate_xlsx_export
from support.formatting import format_amount_display, format_date_display, format_raw_amount_display
from support.logging import setup_logging

logger = setup_logging()

EXCLUDED_FROM_BALANCE = {"internal_out", "internal_in", "card_settlement", "aggregate_debit"}


def render_registry_page(engine):
    st.header("📋 Ledger — Registro Transazioni")

    session = get_session(engine)
    with session:
        settings = get_all_user_settings(session)

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
        _dec = settings.get("amount_decimal_sep", ",")
        _thou = settings.get("amount_thousands_sep", ".")

        m1.metric("Transazioni", len(txs))
        m2.metric("Saldo Netto", format_amount_display(float(net), _dec, _thou))
        m3.metric("Entrate", format_amount_display(float(income), _dec, _thou))
        m4.metric("Uscite", format_amount_display(float(expenses), _dec, _thou))

        # ── Table ─────────────────────────────────────────────────────────────
        show_raw = st.toggle("Mostra valori originali (raw)", value=False, key="ledger_show_raw")

        _date_fmt = settings.get("date_display_format", "%d/%m/%Y")

        data = [
            {
                "Data": format_date_display(tx.date, _date_fmt),
                "Descrizione": (tx.description or "")[:80],
                "Entrata": format_amount_display(float(tx.amount), _dec, _thou, symbol="")
                           if float(tx.amount) > 0 else "",
                "Uscita": format_amount_display(abs(float(tx.amount)), _dec, _thou, symbol="")
                          if float(tx.amount) < 0 else "",
                "Valuta": tx.currency,
                "Tipo": tx.tx_type,
                "Categoria": tx.category or "",
                "Sottocategoria": tx.subcategory or "",
                "Conto": tx.account_label or "",
                "Conf.": tx.category_confidence or "",
                "Da rivedere": "⚠️" if tx.to_review else "",
                "Desc. originale": (tx.raw_description or "")[:80],
                "Importo originale": format_raw_amount_display(tx.raw_amount),
                "id": tx.id,
            }
            for tx in txs
        ]
        df = pd.DataFrame(data)

        hide_cols = ["id"]
        if not show_raw:
            hide_cols += ["Desc. originale", "Importo originale"]

        st.dataframe(
            df.drop(columns=hide_cols),
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
