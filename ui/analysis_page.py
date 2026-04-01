"""Analytics page (RF-08): interactive Plotly charts."""
from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from core.history_engine import get_description_profiles
from db.models import get_session
from services.settings_service import SettingsService
from services.transaction_service import TransactionService
from support.formatting import format_amount_display
from support.logging import setup_logging
from ui.i18n import t
from ui.widgets.tree_filter import render_tree_filter, build_full_tree_data

logger = setup_logging()

_ALWAYS_EXCLUDED = {"card_settlement", "aggregate_debit"}
_GIROCONTO_TYPES = {"internal_out", "internal_in"}


def render_analysis_page(engine):
    st.header(t("analytics.title"))

    cfg_svc = SettingsService(engine)
    tx_svc  = TransactionService(engine)

    settings = cfg_svc.get_all()

    _dec          = settings.get("amount_decimal_sep", ",")
    _thou         = settings.get("amount_thousands_sep", ".")
    giroconto_mode = settings.get("giroconto_mode", "neutral")

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

    st.caption(t("analytics.quick_period"))
    pc1, pc2, pc3, pc4, pc5 = st.columns(5)
    if pc1.button(t("analytics.preset.current_month"),  key="an_preset_cur",  use_container_width=True):
        st.session_state["analytics_from"] = _first_cur
        st.session_state["analytics_to"]   = today
        st.rerun()
    if pc2.button(t("analytics.preset.prev_month"),    key="an_preset_prev", use_container_width=True):
        st.session_state["analytics_from"] = _rel_first_prev
        st.session_state["analytics_to"]   = _rel_last_prev
        st.rerun()
    if pc3.button(t("analytics.preset.last_3_months"), key="an_preset_3m",   use_container_width=True):
        st.session_state["analytics_from"] = today - timedelta(days=90)
        st.session_state["analytics_to"]   = today
        st.rerun()
    if pc4.button(t("analytics.preset.current_year"),  key="an_preset_year", use_container_width=True):
        st.session_state["analytics_from"] = today.replace(month=1, day=1)
        st.session_state["analytics_to"]   = today
        st.rerun()
    if pc5.button(t("analytics.preset.all"),           key="an_preset_all",  use_container_width=True):
        st.session_state.pop("analytics_from", None)
        st.session_state.pop("analytics_to",   None)
        st.rerun()

    fc1, fc2, fc3 = st.columns([2, 2, 2])
    with fc1:
        date_from = st.date_input(t("ledger.filter.from"), key="analytics_from")
    with fc2:
        date_to   = st.date_input(t("ledger.filter.to"),  key="analytics_to")
    with fc3:
        account_filter = st.selectbox(
            t("ledger.filter.account"), [t("ledger.filter.account_all")] + _accounts, key="an_account"
        )

    # ── Tree filter for categories / subcategories / contexts ────────────
    _tree_cats = build_full_tree_data(cfg_svc)
    with st.expander(t("analytics.filter_categories"), expanded=False):
        tree_sel = render_tree_filter(
            categories=_tree_cats,
            contexts=_contexts,
            key_prefix="an_tree",
            show_contexts=True,
        )

    filters: dict = {}
    if date_from:
        filters["date_from"] = date_from.isoformat()
    if date_to:
        filters["date_to"] = date_to.isoformat()
    if account_filter != t("ledger.filter.account_all"):
        filters["account_label"] = account_filter

    # Apply tree filter selections — only restrict when user deselected something
    _sel_cats = set(tree_sel["selected_categories"])
    _sel_subs = set(tree_sel["selected_subcategories"])
    _sel_ctxs = set(tree_sel["selected_contexts"])
    _all_cat_names = {c["name"] for c in _tree_cats}
    _all_sub_names = {
        s["name"] if isinstance(s, dict) else s
        for c in _tree_cats for s in c.get("subcategories", [])
    }

    if _sel_cats and _sel_cats != _all_cat_names:
        filters["categories"] = sorted(_sel_cats)
    if _sel_ctxs and _sel_ctxs != set(_contexts):
        filters["contexts"] = sorted(_sel_ctxs)

    txs = tx_svc.get_transactions(filters=filters)

    if not txs:
        st.info(t("analytics.no_transactions"))
        return

    excluded = set(_ALWAYS_EXCLUDED)
    if giroconto_mode == "exclude":
        excluded |= _GIROCONTO_TYPES
        st.caption(t("analytics.giro_excluded"))
    else:
        st.caption(t("analytics.giro_included"))

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
        st.info(t("analytics.no_transactions_after_filter"))
        return

    df["month"] = df["date"].dt.to_period("M").astype(str)
    df["year"]  = df["date"].dt.year

    # ── 1. Monthly income/expense grouped bar ──────────────────────────────────
    st.subheader(t("analytics.monthly_income_expense"))
    _lbl_income  = t("ledger.metric.income")
    _lbl_expense = t("ledger.metric.expenses")
    monthly = (
        df.assign(sign=df["amount"].apply(lambda x: _lbl_income if x > 0 else _lbl_expense),
                  abs_amount=df["amount"].abs())
          .groupby(["month", "sign"])["abs_amount"]
          .sum()
          .reset_index()
    )
    if not monthly.empty:
        fig1 = px.bar(monthly, x="month", y="abs_amount", color="sign",
                      barmode="group", labels={"abs_amount": "€", "month": t("analytics.label.month")},
                      color_discrete_map={_lbl_income: "#27ae60", _lbl_expense: "#c0392b"})
        st.plotly_chart(fig1, width="stretch")

    # ── 2. Cumulative balance ──────────────────────────────────────────────────
    st.subheader(t("analytics.cumulative_balance"))
    df_sorted = df.sort_values("date")
    df_sorted["cumulative"] = df_sorted["amount"].cumsum()
    fig2 = go.Figure()
    fig2.add_trace(go.Scatter(
        x=df_sorted["date"], y=df_sorted["cumulative"],
        mode="lines", name=t("analytics.cumulative_balance"), line=dict(color="#2980b9"),
    ))
    fig2.add_hline(y=0, line_dash="dash", line_color="gray")
    fig2.update_layout(xaxis_title=t("ledger.col.date"), yaxis_title="€")
    st.plotly_chart(fig2, width="stretch")

    # ── 3. Category pie + treemap — Uscite ────────────────────────────────────
    st.subheader(t("analytics.category_dist_expenses"))
    expenses_df = df[df["amount"] < 0].copy()
    expenses_df["abs_amount"] = expenses_df["amount"].abs()
    if not expenses_df.empty:
        pie_data = expenses_df.groupby("category")["abs_amount"].sum().reset_index()
        pie_data = pie_data.sort_values("abs_amount", ascending=False)
        total_expenses = pie_data["abs_amount"].sum()
        fig_pie = px.pie(
            pie_data, names="category", values="abs_amount",
            title=t("analytics.pie_expenses_title", total=format_amount_display(total_expenses, _dec, _thou)),
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
            title=t("analytics.treemap_expenses_title"),
        )
        fig3.update_traces(
            hovertemplate="<b>%{label}</b><br>%{value:.2f} €<br>%{percentRoot:.1%} del totale<extra></extra>"
        )
        st.plotly_chart(fig3, width="stretch")

    # ── 4. Category drill-down (interactive) ──────────────────────────────────
    st.subheader(t("analytics.drilldown_title"))
    all_cats_with_data = sorted(df["category"].unique().tolist())
    selected_cat = st.selectbox(
        t("analytics.select_category"),
        [t("analytics.all_expenses")] + all_cats_with_data,
        key="analytics_cat_filter",
    )

    if selected_cat == t("analytics.all_expenses"):
        drill_df    = expenses_df.copy() if not expenses_df.empty else pd.DataFrame()
        drill_title = t("analytics.drilldown_all_title")
    else:
        drill_df = df[(df["category"] == selected_cat)].copy()
        drill_df["abs_amount"] = drill_df["amount"].abs()
        drill_title = t("analytics.drilldown_cat_title", cat=selected_cat)

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
                labels={"abs_amount": "€", "subcategory": t("ledger.col.subcategory")},
                color="abs_amount",
                color_continuous_scale="Blues",
            )
            fig_drill.update_layout(coloraxis_showscale=False, showlegend=False)
            fig_drill.update_traces(hovertemplate="<b>%{y}</b><br>%{x:.2f} €<extra></extra>")
            st.plotly_chart(fig_drill, width="stretch")

            if selected_cat != t("analytics.all_expenses"):
                monthly_cat = drill_df.groupby("month")["abs_amount"].sum().reset_index()
                fig_trend = px.line(
                    monthly_cat, x="month", y="abs_amount",
                    title=t("analytics.monthly_trend", cat=selected_cat),
                    labels={"abs_amount": "€", "month": t("analytics.label.month")},
                    markers=True,
                )
                st.plotly_chart(fig_trend, width="stretch")
    else:
        st.info(t("analytics.no_data_selection"))

    # ── 5. Income breakdown ────────────────────────────────────────────────────
    st.subheader(t("analytics.category_dist_income"))
    income_df = df[df["amount"] > 0].copy()
    income_df["abs_amount"] = income_df["amount"]
    if not income_df.empty:
        inc_pie = income_df.groupby("category")["abs_amount"].sum().reset_index()
        inc_pie = inc_pie.sort_values("abs_amount", ascending=False)
        total_income = inc_pie["abs_amount"].sum()
        fig_inc_pie = px.pie(
            inc_pie, names="category", values="abs_amount",
            title=t("analytics.pie_income_title", total=format_amount_display(total_income, _dec, _thou)),
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
                title=t("analytics.treemap_income_title"),
                color_discrete_sequence=px.colors.sequential.Greens_r,
            )
            st.plotly_chart(fig_inc_tree, width="stretch")

    # ── 6. Analisi per contesto ────────────────────────────────────────────────
    if len(df["context"].unique()) > 1:
        st.subheader(t("analytics.context_distribution"))
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
                labels={"abs_amount": "€", "month": t("analytics.label.month"), "context": t("ledger.col.context")},
                title=t("analytics.monthly_by_context"),
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
                title=t("analytics.pie_context_title"),
                hole=0.35,
            )
            fig_ctx_pie.update_traces(
                textposition="inside",
                textinfo="percent+label",
                hovertemplate="<b>%{label}</b><br>%{value:.2f} €<br>%{percent}<extra></extra>",
            )
            st.plotly_chart(fig_ctx_pie, width="stretch")

    # ── 7. Anomaly detection — family benchmark ────────────────────────────────
    st.subheader(t("analytics.anomaly_title"))
    st.caption(t("analytics.anomaly_caption"))

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
        t("analytics.family_members"),
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
                status = t("analytics.status.high")
            elif user_pct > 0 and bench_pct > 0 and dev < 0.5:
                status = t("analytics.status.low")
            elif user_pct == 0:
                status = t("analytics.status.absent")
            else:
                status = t("analytics.status.normal")

            _c_cat   = t("ledger.col.category")
            _c_spent = t("analytics.col.spent")
            _c_yours = t("analytics.col.yours_pct")
            _c_bench = t("analytics.col.benchmark_pct")
            _c_ref   = "×Ref"
            _c_stato = t("analytics.col.status")
            anomaly_rows.append({
                _c_cat:    cat,
                _c_spent:  round(user_amt, 2),
                _c_yours:  round(user_pct, 1),
                _c_bench:  round(bench_pct, 1),
                _c_ref:    round(dev, 1),
                _c_stato:  status,
            })

        _c_cat   = t("ledger.col.category")
        _c_spent = t("analytics.col.spent")
        _c_yours = t("analytics.col.yours_pct")
        _c_bench = t("analytics.col.benchmark_pct")
        _c_ref   = "×Ref"
        _c_stato = t("analytics.col.status")

        df_anom = (
            pd.DataFrame(anomaly_rows)
            .sort_values(_c_ref, ascending=False)
            .reset_index(drop=True)
        )
        st.dataframe(
            df_anom,
            width="stretch",
            column_config={
                _c_spent:  st.column_config.NumberColumn(format="%.2f"),
                _c_yours:  st.column_config.NumberColumn(format="%.1f %%"),
                _c_bench:  st.column_config.NumberColumn(format="%.1f %%"),
                _c_ref:    st.column_config.NumberColumn(
                    help=t("analytics.ref_help"),
                    format="%.1f ×",
                ),
            },
            hide_index=True,
        )

        _chart_rows = []
        for row in anomaly_rows:
            if row[_c_spent] > 0 or row[_c_bench] > 0:
                _chart_rows.append({_c_cat: row[_c_cat], "Tipo": _c_yours,  t("analytics.col.percentage"): row[_c_yours]})
                _chart_rows.append({_c_cat: row[_c_cat], "Tipo": _c_bench, t("analytics.col.percentage"): row[_c_bench]})
        if _chart_rows:
            _df_chart = pd.DataFrame(_chart_rows)
            _cat_order = df_anom[_c_cat].tolist()
            _df_chart[_c_cat] = pd.Categorical(_df_chart[_c_cat], categories=_cat_order, ordered=True)
            _df_chart = _df_chart.sort_values(_c_cat)
            fig_bench = px.bar(
                _df_chart, x=t("analytics.col.percentage"), y=_c_cat, color="Tipo",
                barmode="group", orientation="h",
                color_discrete_map={_c_yours: "#e74c3c", _c_bench: "#95a5a6"},
                labels={t("analytics.col.percentage"): t("analytics.label.pct_total_expenses"), _c_cat: ""},
                title=t("analytics.benchmark_chart_title", n=n_members),
            )
            fig_bench.update_layout(legend_title_text="", height=500)
            st.plotly_chart(fig_bench, width="stretch")

        _high = df_anom[df_anom[_c_stato].str.contains("🔴")]
        _low  = df_anom[df_anom[_c_stato].str.contains("🔵")]
        if not _high.empty:
            st.error(
                t("analytics.anomaly_high_header") + "\n"
                + "\n".join(
                    f"- **{r[_c_cat]}**: {r[_c_yours]:.1f}% vs {r[_c_bench]:.1f}% "
                    f"({r[_c_ref]:.1f}×) — {format_amount_display(r[_c_spent], _dec, _thou)}"
                    for _, r in _high.iterrows()
                )
            )
        if not _low.empty:
            st.warning(
                t("analytics.anomaly_low_header") + "\n"
                + "\n".join(
                    f"- **{r[_c_cat]}**: {r[_c_yours]:.1f}% vs {r[_c_bench]:.1f}% "
                    f"({r[_c_ref]:.1f}×)"
                    for _, r in _low.iterrows()
                )
            )
        if _high.empty and _low.empty:
            st.success(t("analytics.no_anomalies"))
    else:
        st.info(t("analytics.no_expenses_period"))

    # ── 8. Top-N merchant ──────────────────────────────────────────────────────
    st.subheader(t("analytics.top10_title"))
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
    st.subheader(t("analytics.account_breakdown"))
    acc_monthly = (
        df.assign(abs_amount=df["amount"].abs())
          .groupby(["month", "account_label"])["abs_amount"]
          .sum()
          .reset_index()
    )
    if not acc_monthly.empty:
        fig5 = px.bar(acc_monthly, x="month", y="abs_amount", color="account_label",
                      barmode="stack", labels={"abs_amount": "€", "month": t("analytics.label.month")})
        st.plotly_chart(fig5, width="stretch")

    # ── 10. Associazioni descrizione → categoria ───────────────────────────────
    st.subheader(t("analytics.associations_title"))
    st.caption(t("analytics.associations_caption"))

    with get_session(engine) as _hist_session:
        _profiles = get_description_profiles(_hist_session)

    # Filter: only descriptions with at least 3 validated transactions
    _profiles = [p for p in _profiles if p.total_validated >= 3]

    if _profiles:
        # Sort by count descending
        _profiles.sort(key=lambda p: p.total_validated, reverse=True)

        _col_desc  = t("analytics.assoc.col.description")
        _col_mcat  = t("analytics.assoc.col.main_category")
        _col_sub   = t("analytics.assoc.col.subcategory")
        _col_val   = t("analytics.assoc.col.validations")
        _col_homo  = t("analytics.assoc.col.homogeneity")
        _col_conf  = t("analytics.assoc.col.confidence")
        _col_stat  = t("analytics.assoc.col.status")

        _assoc_rows = []
        for p in _profiles:
            if p.homogeneity >= 0.90:
                badge = t("analytics.assoc.badge.auto")
            elif p.homogeneity >= 0.50:
                badge = t("analytics.assoc.badge.mixed")
            else:
                badge = t("analytics.assoc.badge.heterogeneous")

            _assoc_rows.append({
                _col_desc:  p.description or "",
                _col_mcat:  p.top_category,
                _col_sub:   p.top_subcategory or "",
                _col_val:   p.total_validated,
                _col_homo:  round(p.homogeneity, 2),
                _col_conf:  round(p.confidence, 2),
                _col_stat:  badge,
            })

        df_assoc = pd.DataFrame(_assoc_rows)
        st.dataframe(
            df_assoc,
            width="stretch",
            column_config={
                _col_homo: st.column_config.ProgressColumn(
                    min_value=0.0, max_value=1.0, format="%.2f",
                ),
                _col_conf: st.column_config.ProgressColumn(
                    min_value=0.0, max_value=1.0, format="%.2f",
                ),
            },
            hide_index=True,
        )

        _n_auto = sum(1 for p in _profiles if p.homogeneity >= 0.90)
        _n_mixed = sum(1 for p in _profiles if 0.50 <= p.homogeneity < 0.90)
        _n_hetero = sum(1 for p in _profiles if p.homogeneity < 0.50)
        st.caption(
            t("analytics.assoc.summary",
              n_auto=_n_auto, n_mixed=_n_mixed,
              n_hetero=_n_hetero, n_total=len(_profiles))
        )
    else:
        st.info(t("analytics.assoc.no_data"))

    # ── HTML report download ───────────────────────────────────────────────────
    st.divider()
    html_str = tx_svc.export_html(
        date_from=filters.get("date_from"),
        date_to=filters.get("date_to"),
    )
    st.download_button(
        t("analytics.download_html_report"),
        html_str.encode("utf-8"),
        "spendify_report.html",
        "text/html",
    )
