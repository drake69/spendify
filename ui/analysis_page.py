"""Analytics page (RF-08): interactive Plotly charts."""
from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from services.settings_service import SettingsService
from services.transaction_service import TransactionService
from support.formatting import format_amount_display
from support.logging import setup_logging

logger = setup_logging()

_ALWAYS_EXCLUDED = {"card_settlement", "aggregate_debit"}
_GIROCONTO_TYPES = {"internal_out", "internal_in"}


def render_analysis_page(engine):
    st.header("📊 Analytics — Analisi Finanziaria")

    cfg_svc = SettingsService(engine)
    tx_svc  = TransactionService(engine)

    settings = cfg_svc.get_all()
    taxonomy = cfg_svc.get_taxonomy()

    _dec          = settings.get("amount_decimal_sep", ",")
    _thou         = settings.get("amount_thousands_sep", ".")
    giroconto_mode = settings.get("giroconto_mode", "neutral")

    _expense_cats = sorted(taxonomy.all_expense_categories)
    _income_cats  = sorted(taxonomy.all_income_categories)
    _all_cats     = _expense_cats + _income_cats

    _accounts = tx_svc.get_distinct_account_labels()
    _contexts = tx_svc.get_distinct_context_values()
    today     = date.today()

    # ── Date preset session init ───────────────────────────────────────────────
    if "analytics_from" not in st.session_state:
        st.session_state["analytics_from"] = today.replace(day=1)
    if "analytics_to" not in st.session_state:
        st.session_state["analytics_to"] = today

    _first_cur = today.replace(day=1)
    _cur_from  = st.session_state.get("analytics_from", _first_cur)
    if not isinstance(_cur_from, date):
        _cur_from = _first_cur
    _rel_last_prev  = _cur_from - timedelta(days=1)
    _rel_first_prev = _rel_last_prev.replace(day=1)

    st.caption("**Periodo rapido**")
    pc1, pc2, pc3, pc4, pc5 = st.columns(5)
    if pc1.button("📅 Mese corrente",   key="an_preset_cur",  use_container_width=True):
        st.session_state["analytics_from"] = _first_cur
        st.session_state["analytics_to"]   = today
        st.rerun()
    if pc2.button("⏮ Mese precedente",  key="an_preset_prev", use_container_width=True):
        st.session_state["analytics_from"] = _rel_first_prev
        st.session_state["analytics_to"]   = _rel_last_prev
        st.rerun()
    if pc3.button("📆 Ultimi 3 mesi",   key="an_preset_3m",   use_container_width=True):
        st.session_state["analytics_from"] = today - timedelta(days=90)
        st.session_state["analytics_to"]   = today
        st.rerun()
    if pc4.button("🗓 Anno corrente",    key="an_preset_year", use_container_width=True):
        st.session_state["analytics_from"] = today.replace(month=1, day=1)
        st.session_state["analytics_to"]   = today
        st.rerun()
    if pc5.button("♾ Tutto",             key="an_preset_all",  use_container_width=True):
        st.session_state.pop("analytics_from", None)
        st.session_state.pop("analytics_to",   None)
        st.rerun()

    fc1, fc2, fc3 = st.columns([2, 2, 2])
    with fc1:
        date_from = st.date_input("Da", key="analytics_from")
    with fc2:
        date_to   = st.date_input("A",  key="analytics_to")
    with fc3:
        account_filter = st.selectbox(
            "Conto", ["tutti i conti"] + _accounts, key="an_account"
        )

    fc4, fc5, fc6 = st.columns([2, 2, 2])
    with fc4:
        cat_filter = st.selectbox(
            "Categoria", ["tutte"] + _all_cats, key="an_cat"
        )
    with fc5:
        if cat_filter != "tutte":
            _subs = sorted(taxonomy.valid_subcategories(cat_filter))
            sub_filter = st.selectbox(
                "Sottocategoria", ["tutte"] + _subs, key=f"an_sub_{cat_filter}"
            )
        else:
            sub_filter = st.selectbox(
                "Sottocategoria", ["tutte"], key="an_sub_all", disabled=True
            )
    with fc6:
        ctx_options = ["tutti i contesti"] + _contexts
        ctx_filter  = st.selectbox("Contesto", ctx_options, key="an_ctx")

    filters: dict = {}
    if date_from:
        filters["date_from"] = date_from.isoformat()
    if date_to:
        filters["date_to"] = date_to.isoformat()
    if account_filter != "tutti i conti":
        filters["account_label"] = account_filter
    if cat_filter != "tutte":
        filters["category"] = cat_filter
    if sub_filter != "tutte":
        filters["subcategory"] = sub_filter
    if ctx_filter != "tutti i contesti":
        filters["context"] = ctx_filter

    txs = tx_svc.get_transactions(filters=filters)

    if not txs:
        st.info("Nessuna transazione disponibile con i filtri selezionati.")
        return

    excluded = set(_ALWAYS_EXCLUDED)
    if giroconto_mode == "exclude":
        excluded |= _GIROCONTO_TYPES
        st.caption("ℹ️ Giroconti esclusi dall'analisi (modalità *Escludi giroconti*).")
    else:
        st.caption("ℹ️ Giroconti inclusi nell'analisi (modalità *Neutrale*). Puoi cambiarla in ⚙️ Impostazioni.")

    rows = []
    for tx in txs:
        if tx.tx_type in excluded:
            continue
        amt = float(tx.amount)
        rows.append({
            "date":          pd.to_datetime(tx.date),
            "amount":        amt,
            "category":      tx.category or "Altro",
            "subcategory":   tx.subcategory or "",
            "tx_type":       tx.tx_type,
            "account_label": tx.account_label or "",
            "description":   tx.description or "",
            "context":       tx.context or "—",
        })

    df = pd.DataFrame(rows)
    if df.empty:
        st.info("Nessuna transazione da visualizzare dopo i filtri.")
        return

    df["month"] = df["date"].dt.to_period("M").astype(str)
    df["year"]  = df["date"].dt.year

    # ── 1. Monthly income/expense grouped bar ──────────────────────────────────
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

    # ── 2. Cumulative balance ──────────────────────────────────────────────────
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

    # ── 3. Category pie + treemap — Uscite ────────────────────────────────────
    st.subheader("Distribuzione categorie — Uscite")
    expenses_df = df[df["amount"] < 0].copy()
    expenses_df["abs_amount"] = expenses_df["amount"].abs()
    if not expenses_df.empty:
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

        cat_sum = expenses_df.groupby(["category", "subcategory"])["abs_amount"].sum().reset_index()
        fig3 = px.treemap(
            cat_sum, path=["category", "subcategory"], values="abs_amount",
            title="Dettaglio uscite per categoria / sottocategoria",
        )
        fig3.update_traces(
            hovertemplate="<b>%{label}</b><br>%{value:.2f} €<br>%{percentRoot:.1%} del totale<extra></extra>"
        )
        st.plotly_chart(fig3, width="stretch")

    # ── 4. Category drill-down (interactive) ──────────────────────────────────
    st.subheader("Drill-down per categoria")
    all_cats_with_data = sorted(df["category"].unique().tolist())
    selected_cat = st.selectbox(
        "Seleziona categoria",
        ["— tutte le uscite —"] + all_cats_with_data,
        key="analytics_cat_filter",
    )

    if selected_cat == "— tutte le uscite —":
        drill_df    = expenses_df.copy() if not expenses_df.empty else pd.DataFrame()
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
            fig_drill.update_traces(hovertemplate="<b>%{y}</b><br>%{x:.2f} €<extra></extra>")
            st.plotly_chart(fig_drill, width="stretch")

            if selected_cat != "— tutte le uscite —":
                monthly_cat = drill_df.groupby("month")["abs_amount"].sum().reset_index()
                fig_trend = px.line(
                    monthly_cat, x="month", y="abs_amount",
                    title=f"Trend mensile: {selected_cat}",
                    labels={"abs_amount": "€", "month": "Mese"},
                    markers=True,
                )
                st.plotly_chart(fig_trend, width="stretch")
    else:
        st.info("Nessun dato per la selezione corrente.")

    # ── 5. Income breakdown ────────────────────────────────────────────────────
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

    # ── 6. Analisi per contesto ────────────────────────────────────────────────
    if len(df["context"].unique()) > 1:
        st.subheader("Distribuzione per contesto")
        ctx_data = (
            df.assign(abs_amount=df["amount"].abs())
              .groupby(["context", "month"])["abs_amount"]
              .sum()
              .reset_index()
        )
        if not ctx_data.empty:
            fig_ctx = px.bar(
                ctx_data, x="month", y="abs_amount", color="context",
                barmode="stack",
                labels={"abs_amount": "€", "month": "Mese", "context": "Contesto"},
                title="Importo mensile per contesto",
            )
            st.plotly_chart(fig_ctx, width="stretch")

        ctx_pie = (
            df.assign(abs_amount=df["amount"].abs())
              .groupby("context")["abs_amount"]
              .sum()
              .reset_index()
              .sort_values("abs_amount", ascending=False)
        )
        if not ctx_pie.empty:
            fig_ctx_pie = px.pie(
                ctx_pie, names="context", values="abs_amount",
                title="Ripartizione totale per contesto",
                hole=0.35,
            )
            fig_ctx_pie.update_traces(
                textposition="inside",
                textinfo="percent+label",
                hovertemplate="<b>%{label}</b><br>%{value:.2f} €<br>%{percent}<extra></extra>",
            )
            st.plotly_chart(fig_ctx_pie, width="stretch")

    # ── 7. Anomaly detection — family benchmark ────────────────────────────────
    st.subheader("🚨 Anomalie di spesa — Confronto famiglia di riferimento")
    st.caption(
        "Confronta la tua distribuzione di spesa con i benchmark ISTAT per una famiglia italiana. "
        "Valori molto superiori al benchmark possono indicare errori di categorizzazione "
        "(es. commissioni bancarie al 40% delle uscite)."
    )

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
    _PERCAPITA_CATS = {"Alimentari", "Abbigliamento", "Salute", "Istruzione", "Cura personale"}

    n_members = st.radio(
        "Componenti del nucleo familiare",
        [1, 2, 3, 4, 5],
        index=1,
        horizontal=True,
        key="anomaly_family_size",
    )

    if not expenses_df.empty:
        scale = n_members / 2.0
        benchmark: dict[str, float] = {
            cat: pct * scale if cat in _PERCAPITA_CATS else pct
            for cat, pct in _BENCHMARK_2P.items()
        }
        _total_b = sum(benchmark.values())
        benchmark = {c: v / _total_b * 100 for c, v in benchmark.items()}

        total_exp  = expenses_df["abs_amount"].sum()
        cat_totals = expenses_df.groupby("category")["abs_amount"].sum().to_dict()

        anomaly_rows = []
        for cat, bench_pct in sorted(benchmark.items(), key=lambda x: x[0]):
            user_amt  = float(cat_totals.get(cat, 0.0))
            user_pct  = (user_amt / total_exp * 100) if total_exp > 0 else 0.0
            dev       = (user_pct / bench_pct) if bench_pct > 0 else 0.0

            if dev > 1.5:
                status = "🔴 Alta"
            elif user_pct > 0 and bench_pct > 0 and dev < 0.5:
                status = "🔵 Bassa"
            elif user_pct == 0:
                status = "⚪ Assente"
            else:
                status = "🟢 Normale"

            anomaly_rows.append({
                "Categoria":    cat,
                "Speso (€)":    round(user_amt, 2),
                "Tua %":        round(user_pct, 1),
                "Benchmark %":  round(bench_pct, 1),
                "×Ref":         round(dev, 1),
                "Stato":        status,
            })

        df_anom = (
            pd.DataFrame(anomaly_rows)
            .sort_values("×Ref", ascending=False)
            .reset_index(drop=True)
        )
        st.dataframe(
            df_anom,
            width="stretch",
            column_config={
                "Speso (€)":    st.column_config.NumberColumn(format="%.2f"),
                "Tua %":        st.column_config.NumberColumn(format="%.1f %%"),
                "Benchmark %":  st.column_config.NumberColumn(format="%.1f %%"),
                "×Ref":         st.column_config.NumberColumn(
                    help="Rapporto tua%/benchmark. 🔴>1.5× troppo alta · 🔵<0.5× troppo bassa · 🟢 normale",
                    format="%.1f ×",
                ),
            },
            hide_index=True,
        )

        _chart_rows = []
        for row in anomaly_rows:
            if row["Speso (€)"] > 0 or row["Benchmark %"] > 0:
                _chart_rows.append({"Categoria": row["Categoria"], "Tipo": "Tua %",       "Percentuale": row["Tua %"]})
                _chart_rows.append({"Categoria": row["Categoria"], "Tipo": "Benchmark %", "Percentuale": row["Benchmark %"]})
        if _chart_rows:
            _df_chart = pd.DataFrame(_chart_rows)
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

        _high = df_anom[df_anom["Stato"] == "🔴 Alta"]
        _low  = df_anom[df_anom["Stato"] == "🔵 Bassa"]
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

    # ── 8. Top-N merchant ──────────────────────────────────────────────────────
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

    # ── 9. Account breakdown ───────────────────────────────────────────────────
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

    # ── HTML report download ───────────────────────────────────────────────────
    st.divider()
    html_str = tx_svc.export_html(
        date_from=filters.get("date_from"),
        date_to=filters.get("date_to"),
    )
    st.download_button(
        "📥 Scarica Report HTML",
        html_str.encode("utf-8"),
        "spendify_report.html",
        "text/html",
    )
