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
            st.plotly_chart(fig1, width="stretch")

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
        st.plotly_chart(fig2, width="stretch")

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
            st.plotly_chart(fig_pie, width="stretch")

            # Treemap with category → subcategory drill-down
            cat_sum = expenses_df.groupby(["category", "subcategory"])["abs_amount"].sum().reset_index()
            fig3 = px.treemap(
                cat_sum, path=["category", "subcategory"], values="abs_amount",
                title="Dettaglio uscite per categoria / sottocategoria",
            )
            fig3.update_traces(
                hovertemplate="<b>%{label}</b><br>%{value:.2f} €<br>%{percentRoot:.1%} del totale<extra></extra>"
            )
            st.plotly_chart(fig3, width="stretch")

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
                st.plotly_chart(fig_drill, width="stretch")

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
                    st.plotly_chart(fig_trend, width="stretch")
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
            st.plotly_chart(fig_inc_pie, width="stretch")

            inc_tree = income_df.groupby(["category", "subcategory"])["abs_amount"].sum().reset_index()
            if not inc_tree.empty and len(inc_tree) > 1:
                fig_inc_tree = px.treemap(
                    inc_tree, path=["category", "subcategory"], values="abs_amount",
                    title="Dettaglio entrate per categoria / sottocategoria",
                    color_discrete_sequence=px.colors.sequential.Greens_r,
                )
                st.plotly_chart(fig_inc_tree, width="stretch")

        # ── 6. Anomaly detection — family benchmark ───────────────────────────
        st.subheader("🚨 Anomalie di spesa — Confronto famiglia di riferimento")
        st.caption(
            "Confronta la tua distribuzione di spesa con i benchmark ISTAT per una famiglia italiana. "
            "Valori molto superiori al benchmark possono indicare errori di categorizzazione "
            "(es. commissioni bancarie al 40% delle uscite)."
        )

        # ISTAT benchmark % of total expenses — 2 adulti, Italia (fonte: Indagine sui consumi 2022)
        # Le percentuali sono calibrate su 2 persone; scala linearmente per famiglie più grandi
        # per le categorie "pro-capite" (alimentari, abbigliamento, salute, istruzione).
        _BENCHMARK_2P: dict[str, float] = {
            "Casa":                    26.0,
            "Alimentari":              17.0,
            "Ristorazione":             6.0,
            "Trasporti":               12.0,
            "Salute":                   7.0,
            "Istruzione":               1.0,
            "Abbigliamento":            5.0,
            "Comunicazioni":            2.0,
            "Svago e tempo libero":     8.0,
            "Animali domestici":        1.0,
            "Finanza e assicurazioni":  6.0,
            "Cura personale":           2.0,
            "Tasse e tributi":          4.0,
            "Regali e donazioni":       2.0,
            "Altro":                    1.0,
        }
        # Categories that scale with number of persons (pro-capite)
        _PERCAPITA_CATS = {"Alimentari", "Abbigliamento", "Salute", "Istruzione", "Cura personale"}

        n_members = st.radio(
            "Componenti del nucleo familiare",
            [1, 2, 3, 4, 5],
            index=1,
            horizontal=True,
            key="anomaly_family_size",
        )

        if not expenses_df.empty:
            # Adjust benchmark for family size: pro-capite categories scale by n/2
            scale = n_members / 2.0
            benchmark: dict[str, float] = {}
            for cat, pct in _BENCHMARK_2P.items():
                if cat in _PERCAPITA_CATS:
                    benchmark[cat] = pct * scale
                else:
                    benchmark[cat] = pct
            # Re-normalize to 100%
            _total_b = sum(benchmark.values())
            benchmark = {c: v / _total_b * 100 for c, v in benchmark.items()}

            total_exp = expenses_df["abs_amount"].sum()
            cat_totals = expenses_df.groupby("category")["abs_amount"].sum().to_dict()

            anomaly_rows = []
            for cat, bench_pct in sorted(benchmark.items(), key=lambda x: x[0]):
                user_amt = float(cat_totals.get(cat, 0.0))
                user_pct = (user_amt / total_exp * 100) if total_exp > 0 else 0.0
                dev = (user_pct / bench_pct) if bench_pct > 0 else 0.0

                if dev > 1.5:
                    status = "🔴 Alta"
                elif user_pct > 0 and bench_pct > 0 and dev < 0.5:
                    status = "🔵 Bassa"
                elif user_pct == 0:
                    status = "⚪ Assente"
                else:
                    status = "🟢 Normale"

                anomaly_rows.append({
                    "Categoria": cat,
                    "Speso (€)": round(user_amt, 2),
                    "Tua %": round(user_pct, 1),
                    "Benchmark %": round(bench_pct, 1),
                    "×Ref": round(dev, 1),
                    "Stato": status,
                })

            df_anom = (
                pd.DataFrame(anomaly_rows)
                .sort_values("×Ref", ascending=False)
                .reset_index(drop=True)
            )
            # Show all categories (even zero) so user sees what's missing vs reference
            st.dataframe(
                df_anom,
                width="stretch",
                column_config={
                    "Speso (€)": st.column_config.NumberColumn(format="%.2f"),
                    "Tua %":     st.column_config.NumberColumn(format="%.1f %%"),
                    "Benchmark %": st.column_config.NumberColumn(format="%.1f %%"),
                    "×Ref":      st.column_config.NumberColumn(
                        help="Rapporto tua%/benchmark. 🔴>1.5× troppo alta · 🔵<0.5× troppo bassa · 🟢 normale",
                        format="%.1f ×",
                    ),
                },
                hide_index=True,
            )

            # Bar chart: tua % vs benchmark
            _chart_rows = []
            for row in anomaly_rows:
                if row["Speso (€)"] > 0 or row["Benchmark %"] > 0:
                    _chart_rows.append({"Categoria": row["Categoria"], "Tipo": "Tua %",       "Percentuale": row["Tua %"]})
                    _chart_rows.append({"Categoria": row["Categoria"], "Tipo": "Benchmark %", "Percentuale": row["Benchmark %"]})
            if _chart_rows:
                _df_chart = pd.DataFrame(_chart_rows)
                # Order categories by user deviation desc
                _cat_order = df_anom["Categoria"].tolist()
                _df_chart["Categoria"] = pd.Categorical(_df_chart["Categoria"], categories=_cat_order, ordered=True)
                _df_chart = _df_chart.sort_values("Categoria")
                fig_bench = px.bar(
                    _df_chart, x="Percentuale", y="Categoria", color="Tipo",
                    barmode="group", orientation="h",
                    color_discrete_map={"Tua %": "#e74c3c", "Benchmark %": "#95a5a6"},
                    labels={"Percentuale": "% del totale uscite", "Categoria": ""},
                    title=f"Distribuzione uscite: reale vs benchmark ISTAT ({n_members} {'persona' if n_members == 1 else 'persone'})",
                )
                fig_bench.update_layout(legend_title_text="", height=500)
                st.plotly_chart(fig_bench, width="stretch")

            # Summary: anomalies high and low
            _high  = df_anom[df_anom["Stato"] == "🔴 Alta"]
            _low   = df_anom[df_anom["Stato"] == "🔵 Bassa"]
            if not _high.empty:
                st.error(
                    "**Spesa anomalmente alta** (>1.5× il benchmark) — possibili errori di categorizzazione:\n"
                    + "\n".join(
                        f"- **{r['Categoria']}**: {r['Tua %']:.1f}% vs {r['Benchmark %']:.1f}% di riferimento "
                        f"({r['×Ref']:.1f}×) — {format_amount_display(r['Speso (€)'], _dec, _thou)}"
                        for _, r in _high.iterrows()
                    )
                )
            if not _low.empty:
                st.warning(
                    "**Spesa anomalmente bassa** (<50% del benchmark) — categoria poco usata o mancante:\n"
                    + "\n".join(
                        f"- **{r['Categoria']}**: {r['Tua %']:.1f}% vs {r['Benchmark %']:.1f}% di riferimento "
                        f"({r['×Ref']:.1f}×)"
                        for _, r in _low.iterrows()
                    )
                )
            if _high.empty and _low.empty:
                st.success("✅ Nessuna anomalia macroscopica rilevata rispetto al benchmark di riferimento.")
        else:
            st.info("Nessuna uscita nel periodo selezionato.")

        # ── 7. Top-N merchant ──────────────────────────────────────────────────
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
            st.plotly_chart(fig4, width="stretch")

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
            st.plotly_chart(fig5, width="stretch")

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
