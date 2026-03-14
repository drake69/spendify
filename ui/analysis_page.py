"""Analytics page (RF-08): interactive Plotly charts."""
from __future__ import annotations

from decimal import Decimal

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from db.models import get_session
from db.repository import get_all_user_settings, get_transactions
from reports.generator import generate_html_report
from support.formatting import format_amount_display
from support.logging import setup_logging

logger = setup_logging()

# Always excluded from analytics (technical/settlement types, never meaningful)
_ALWAYS_EXCLUDED = {"card_settlement", "aggregate_debit"}
# Excluded only when giroconto_mode == "exclude"
_GIROCONTO_TYPES = {"internal_out", "internal_in"}


def render_analysis_page(engine):
    st.header("📊 Analytics — Analisi Finanziaria")

    session = get_session(engine)
    with session:
        settings = get_all_user_settings(session)
        _dec = settings.get("amount_decimal_sep", ",")
        _thou = settings.get("amount_thousands_sep", ".")
        giroconto_mode = settings.get("giroconto_mode", "neutral")

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

        # Determine which tx_types to exclude based on giroconto_mode setting
        excluded = set(_ALWAYS_EXCLUDED)
        if giroconto_mode == "exclude":
            excluded |= _GIROCONTO_TYPES

        if giroconto_mode == "exclude":
            st.caption("ℹ️ Giroconti esclusi dall'analisi (modalità *Escludi giroconti*).")
        elif giroconto_mode == "neutral":
            st.caption("ℹ️ Giroconti inclusi nell'analisi (modalità *Neutrale*). Puoi cambiarla in ⚙️ Impostazioni.")

        # Build DataFrames
        rows = []
        for tx in txs:
            if tx.tx_type in excluded:
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

        # ── 3. Category pie + treemap ─────────────────────────────────────────
        st.subheader("Distribuzione categorie — Uscite")
        expenses_df = df[df["amount"] < 0].copy()
        expenses_df["abs_amount"] = expenses_df["amount"].abs()
        if not expenses_df.empty:
            # Pie chart by top-level category
            pie_data = expenses_df.groupby("category")["abs_amount"].sum().reset_index()
            pie_data = pie_data.sort_values("abs_amount", ascending=False)
            total_expenses = pie_data["abs_amount"].sum()
            fig_pie = px.pie(
                pie_data, names="category", values="abs_amount",
                title=f"Uscite per categoria — totale {format_amount_display(total_expenses, _dec, _thou)}",
                hole=0.35,
            )
            fig_pie.update_traces(
                textposition="inside",
                textinfo="percent+label",
                hovertemplate="<b>%{label}</b><br>%{value:.2f} €<br>%{percent}<extra></extra>",
            )
            fig_pie.update_layout(showlegend=True, legend=dict(orientation="v"))
            st.plotly_chart(fig_pie, use_container_width=True)

            # Treemap with category → subcategory drill-down
            cat_sum = expenses_df.groupby(["category", "subcategory"])["abs_amount"].sum().reset_index()
            fig3 = px.treemap(
                cat_sum, path=["category", "subcategory"], values="abs_amount",
                title="Dettaglio uscite per categoria / sottocategoria",
            )
            fig3.update_traces(
                hovertemplate="<b>%{label}</b><br>%{value:.2f} €<br>%{percentRoot:.1%} del totale<extra></extra>"
            )
            st.plotly_chart(fig3, use_container_width=True)

        # ── 4. Category drill-down (interactive) ─────────────────────────────
        st.subheader("Drill-down per categoria")

        all_cats_with_data = sorted(df["category"].unique().tolist())
        selected_cat = st.selectbox(
            "Seleziona categoria",
            ["— tutte le uscite —"] + all_cats_with_data,
            key="analytics_cat_filter",
        )

        if selected_cat == "— tutte le uscite —":
            drill_df = expenses_df.copy() if not expenses_df.empty else pd.DataFrame()
            drill_title = "Uscite per sottocategoria — tutte le categorie"
        else:
            drill_df = df[(df["category"] == selected_cat)].copy()
            drill_df["abs_amount"] = drill_df["amount"].abs()
            drill_title = f"Dettaglio: {selected_cat}"

        if not drill_df.empty and "abs_amount" in drill_df.columns:
            sub_data = (
                drill_df.groupby("subcategory")["abs_amount"]
                .sum()
                .reset_index()
                .sort_values("abs_amount", ascending=True)
            )
            if not sub_data.empty:
                fig_drill = px.bar(
                    sub_data, x="abs_amount", y="subcategory", orientation="h",
                    title=drill_title,
                    labels={"abs_amount": "€", "subcategory": "Sottocategoria"},
                    color="abs_amount",
                    color_continuous_scale="Blues",
                )
                fig_drill.update_layout(coloraxis_showscale=False, showlegend=False)
                fig_drill.update_traces(
                    hovertemplate="<b>%{y}</b><br>%{x:.2f} €<extra></extra>"
                )
                st.plotly_chart(fig_drill, use_container_width=True)

                # Monthly trend for selected category
                if selected_cat != "— tutte le uscite —":
                    monthly_cat = (
                        drill_df.groupby("month")["abs_amount"]
                        .sum()
                        .reset_index()
                    )
                    fig_trend = px.line(
                        monthly_cat, x="month", y="abs_amount",
                        title=f"Trend mensile: {selected_cat}",
                        labels={"abs_amount": "€", "month": "Mese"},
                        markers=True,
                    )
                    st.plotly_chart(fig_trend, use_container_width=True)
        else:
            st.info("Nessun dato per la selezione corrente.")

        # ── 5. Income breakdown ───────────────────────────────────────────────
        st.subheader("Distribuzione categorie — Entrate")
        income_df = df[df["amount"] > 0].copy()
        income_df["abs_amount"] = income_df["amount"]
        if not income_df.empty:
            inc_pie = income_df.groupby("category")["abs_amount"].sum().reset_index()
            inc_pie = inc_pie.sort_values("abs_amount", ascending=False)
            total_income = inc_pie["abs_amount"].sum()
            fig_inc_pie = px.pie(
                inc_pie, names="category", values="abs_amount",
                title=f"Entrate per categoria — totale {format_amount_display(total_income, _dec, _thou)}",
                hole=0.35,
                color_discrete_sequence=px.colors.sequential.Greens_r,
            )
            fig_inc_pie.update_traces(
                textposition="inside",
                textinfo="percent+label",
                hovertemplate="<b>%{label}</b><br>%{value:.2f} €<br>%{percent}<extra></extra>",
            )
            st.plotly_chart(fig_inc_pie, use_container_width=True)

            inc_tree = income_df.groupby(["category", "subcategory"])["abs_amount"].sum().reset_index()
            if not inc_tree.empty and len(inc_tree) > 1:
                fig_inc_tree = px.treemap(
                    inc_tree, path=["category", "subcategory"], values="abs_amount",
                    title="Dettaglio entrate per categoria / sottocategoria",
                    color_discrete_sequence=px.colors.sequential.Greens_r,
                )
                st.plotly_chart(fig_inc_tree, use_container_width=True)

        # ── 6. Top-N merchant ─────────────────────────────────────────────────
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

        # ── 7. Account breakdown ──────────────────────────────────────────────
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
