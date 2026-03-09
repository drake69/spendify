"""Analytics page (RF-08): interactive Plotly charts."""
from __future__ import annotations

from decimal import Decimal

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from db.models import get_session
from db.repository import get_transactions
from reports.generator import generate_html_report
from support.logging import setup_logging

logger = setup_logging()

EXCLUDED = {"internal_out", "internal_in", "card_settlement", "aggregate_debit"}


def render_analysis_page(engine):
    st.header("📊 Analytics — Analisi Finanziaria")

    session = get_session(engine)
    with session:
        # ── Date filter ───────────────────────────────────────────────────────
        col1, col2 = st.columns(2)
        with col1:
            date_from = st.date_input("Da", value=None, key="analytics_from")
        with col2:
            date_to = st.date_input("A", value=None, key="analytics_to")

        filters: dict = {}
        if date_from:
            filters["date_from"] = date_from.isoformat()
        if date_to:
            filters["date_to"] = date_to.isoformat()

        txs = get_transactions(session, filters=filters)

        if not txs:
            st.info("Nessuna transazione disponibile.")
            return

        # Build DataFrames
        rows = []
        for tx in txs:
            if tx.tx_type in EXCLUDED:
                continue
            amt = float(tx.amount)
            rows.append({
                "date": pd.to_datetime(tx.date),
                "amount": amt,
                "category": tx.category or "Altro",
                "subcategory": tx.subcategory or "",
                "tx_type": tx.tx_type,
                "account_label": tx.account_label or "",
                "description": tx.description or "",
            })

        df = pd.DataFrame(rows)
        if df.empty:
            st.info("Nessuna transazione da visualizzare dopo i filtri.")
            return

        df["month"] = df["date"].dt.to_period("M").astype(str)
        df["year"] = df["date"].dt.year

        # ── 1. Monthly income/expense grouped bar ─────────────────────────────
        st.subheader("Entrate / Uscite mensili")
        monthly = (
            df.assign(sign=df["amount"].apply(lambda x: "Entrate" if x > 0 else "Uscite"),
                      abs_amount=df["amount"].abs())
              .groupby(["month", "sign"])["abs_amount"]
              .sum()
              .reset_index()
        )
        if not monthly.empty:
            fig1 = px.bar(monthly, x="month", y="abs_amount", color="sign",
                          barmode="group", labels={"abs_amount": "€", "month": "Mese"},
                          color_discrete_map={"Entrate": "#27ae60", "Uscite": "#c0392b"})
            st.plotly_chart(fig1, use_container_width=True)

        # ── 2. Cumulative balance ─────────────────────────────────────────────
        st.subheader("Saldo cumulativo")
        df_sorted = df.sort_values("date")
        df_sorted["cumulative"] = df_sorted["amount"].cumsum()
        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(
            x=df_sorted["date"], y=df_sorted["cumulative"],
            mode="lines", name="Saldo cumulativo", line=dict(color="#2980b9"),
        ))
        fig2.add_hline(y=0, line_dash="dash", line_color="gray")
        fig2.update_layout(xaxis_title="Data", yaxis_title="€")
        st.plotly_chart(fig2, use_container_width=True)

        # ── 3. Category treemap ───────────────────────────────────────────────
        st.subheader("Distribuzione categorie — Uscite")
        expenses_df = df[df["amount"] < 0].copy()
        expenses_df["abs_amount"] = expenses_df["amount"].abs()
        if not expenses_df.empty:
            cat_sum = expenses_df.groupby(["category", "subcategory"])["abs_amount"].sum().reset_index()
            fig3 = px.treemap(
                cat_sum, path=["category", "subcategory"], values="abs_amount",
                title="Uscite per categoria",
            )
            st.plotly_chart(fig3, use_container_width=True)

        # ── 4. Top-N merchant ─────────────────────────────────────────────────
        st.subheader("Top 10 descrizioni per importo assoluto")
        top_n = (
            df.assign(abs_amount=df["amount"].abs())
              .groupby("description")["abs_amount"]
              .sum()
              .nlargest(10)
              .reset_index()
        )
        if not top_n.empty:
            fig4 = px.bar(top_n, y="description", x="abs_amount", orientation="h",
                          labels={"abs_amount": "€", "description": ""})
            st.plotly_chart(fig4, use_container_width=True)

        # ── 5. Account breakdown ──────────────────────────────────────────────
        st.subheader("Composizione per conto")
        acc_monthly = (
            df.assign(abs_amount=df["amount"].abs())
              .groupby(["month", "account_label"])["abs_amount"]
              .sum()
              .reset_index()
        )
        if not acc_monthly.empty:
            fig5 = px.bar(acc_monthly, x="month", y="abs_amount", color="account_label",
                          barmode="stack", labels={"abs_amount": "€", "month": "Mese"})
            st.plotly_chart(fig5, use_container_width=True)

        # ── HTML report download ───────────────────────────────────────────────
        st.divider()
        html_str = generate_html_report(
            session,
            date_from=filters.get("date_from"),
            date_to=filters.get("date_to"),
        )
        st.download_button(
            "📥 Scarica Report HTML",
            html_str.encode("utf-8"),
            "spendify_report.html",
            "text/html",
        )
