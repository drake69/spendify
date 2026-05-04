"""Report page (A-01): spending by context/category with pivot, trends, and Excel export."""
from __future__ import annotations

import io
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

from services.settings_service import SettingsService
from services.transaction_service import TransactionService
from support.formatting import format_amount_display
from support.logging import setup_logging
from ui.i18n import t

logger = setup_logging()


def _fmt_eur(val: float, dec: str = ",", thou: str = ".") -> str:
    """Format amount Italian style."""
    return format_amount_display(val, decimal_sep=dec, thousands_sep=thou)


def _fmt_pct(val: float) -> str:
    """Format percentage Italian style with 1 decimal."""
    return f"{val:.1f}%".replace(".", ",")


def _generate_xlsx(
    pivot_df: pd.DataFrame,
    monthly_df: pd.DataFrame,
    detail_txs: list,
    dec: str,
    thou: str,
) -> bytes:
    """Generate a multi-sheet Excel workbook.

    Sheets:
      - Riepilogo: the pivot table
      - One sheet per context with transaction details
      - Trend: monthly data
    """
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        # ── Sheet: Riepilogo ──────────────────────────────────────────────
        if not pivot_df.empty:
            export_pivot = pivot_df.copy()
            export_pivot.to_excel(writer, sheet_name="Riepilogo", index=False)

        # ── Sheet: Trend ──────────────────────────────────────────────────
        if not monthly_df.empty:
            monthly_df.to_excel(writer, sheet_name="Trend", index=False)

        # ── One sheet per context ─────────────────────────────────────────
        if detail_txs:
            detail_rows = []
            for tx in detail_txs:
                detail_rows.append({
                    "Data": tx.date,
                    "Conto": tx.account_label or "",
                    "Descrizione": tx.description or "",
                    "Importo": float(tx.amount),
                    "Categoria": tx.category or "",
                    "Sottocategoria": tx.subcategory or "",
                    "Contesto": tx.context or "—",
                    "Tipo": tx.tx_type or "",
                })
            df_detail = pd.DataFrame(detail_rows)
            contexts = sorted(df_detail["Contesto"].unique())
            for ctx in contexts:
                sheet_name = ctx[:31]  # Excel max sheet name length
                ctx_df = df_detail[df_detail["Contesto"] == ctx].copy()
                ctx_df = ctx_df.sort_values("Data", ascending=False)
                ctx_df.to_excel(writer, sheet_name=sheet_name, index=False)

    return output.getvalue()


def render_report_page(engine):
    st.header(t("report.title"))

    cfg_svc = SettingsService(engine)
    tx_svc = TransactionService(engine)

    settings = cfg_svc.get_all()
    _dec = settings.get("amount_decimal_sep", ",")
    _thou = settings.get("amount_thousands_sep", ".")

    _accounts = tx_svc.get_distinct_account_labels()
    today = date.today()

    # ── Session state defaults ────────────────────────────────────────────────
    if "report_from" not in st.session_state:
        st.session_state["report_from"] = today - timedelta(days=90)
    if "report_to" not in st.session_state:
        st.session_state["report_to"] = today

    # ── Date presets ──────────────────────────────────────────────────────────
    st.caption(f"**{t('report.quick_period')}**")
    pc1, pc2, pc3, pc4, pc5 = st.columns(5)
    _first_cur = today.replace(day=1)
    _cur_from = st.session_state.get("report_from", _first_cur)
    if not isinstance(_cur_from, date):
        _cur_from = _first_cur
    _rel_last_prev = _cur_from - timedelta(days=1)
    _rel_first_prev = _rel_last_prev.replace(day=1)

    if pc1.button(t("report.preset_current_month"), key="rpt_preset_cur", use_container_width=True):
        st.session_state["report_from"] = _first_cur
        st.session_state["report_to"] = today
        st.rerun()
    if pc2.button(t("report.preset_prev_month"), key="rpt_preset_prev", use_container_width=True):
        st.session_state["report_from"] = _rel_first_prev
        st.session_state["report_to"] = _rel_last_prev
        st.rerun()
    if pc3.button(t("report.preset_3months"), key="rpt_preset_3m", use_container_width=True):
        st.session_state["report_from"] = today - timedelta(days=90)
        st.session_state["report_to"] = today
        st.rerun()
    if pc4.button(t("report.preset_year"), key="rpt_preset_year", use_container_width=True):
        st.session_state["report_from"] = today.replace(month=1, day=1)
        st.session_state["report_to"] = today
        st.rerun()
    if pc5.button(t("report.preset_all"), key="rpt_preset_all", use_container_width=True):
        st.session_state["report_from"] = date(2000, 1, 1)
        st.session_state["report_to"] = today
        st.rerun()

    # ── Filters ───────────────────────────────────────────────────────────────
    fc1, fc2, fc3 = st.columns([2, 2, 2])
    with fc1:
        date_from = st.date_input(t("report.filter_from"), key="report_from")
    with fc2:
        date_to = st.date_input(t("report.filter_to"), key="report_to")
    with fc3:
        account_filter = st.multiselect(
            t("report.filter_accounts"), _accounts, default=[], key="rpt_accounts",
            placeholder=t("report.filter_accounts_placeholder"),
        )

    # Build filter params
    _date_from_str = date_from.isoformat() if date_from else None
    _date_to_str = date_to.isoformat() if date_to else None
    _account_ids = account_filter if account_filter else None

    # ── Fetch data ────────────────────────────────────────────────────────────
    agg_data = tx_svc.get_spending_aggregation(
        date_from=_date_from_str,
        date_to=_date_to_str,
        account_ids=_account_ids,
        exclude_internal=True,
    )

    if not agg_data:
        st.info(t("report.no_transactions"))
        return

    monthly_data = tx_svc.get_monthly_spending(
        date_from=_date_from_str,
        date_to=_date_to_str,
        account_ids=_account_ids,
        exclude_internal=True,
    )

    # ── Vista 1: Dove vanno i soldi ───────────────────────────────────────────
    st.subheader(t("report.where_money_goes"))
    st.caption(t("report.where_money_goes_caption"))

    df_agg = pd.DataFrame(agg_data)

    # Separate expenses (negative amounts) and income (positive)
    df_expenses = df_agg[df_agg["total_amount"] < 0].copy()
    df_income = df_agg[df_agg["total_amount"] > 0].copy()

    tab_exp, tab_inc = st.tabs([t("report.tab_expenses"), t("report.tab_income")])

    for tab, df_section, sign_label in [
        (tab_exp, df_expenses, t("report.tab_expenses")),
        (tab_inc, df_income, t("report.tab_income")),
    ]:
        with tab:
            if df_section.empty:
                st.info(t("report.no_section", label=sign_label.lower()))
                continue

            df_section = df_section.copy()
            df_section["abs_amount"] = df_section["total_amount"].abs()
            grand_total = df_section["abs_amount"].sum()

            # Build display rows with subtotals per context
            display_rows = []
            for ctx in sorted(df_section["context"].unique()):
                ctx_df = df_section[df_section["context"] == ctx].sort_values(
                    "abs_amount", ascending=False
                )
                ctx_total = ctx_df["abs_amount"].sum()

                for _, row in ctx_df.iterrows():
                    pct = (row["abs_amount"] / grand_total * 100) if grand_total > 0 else 0
                    display_rows.append({
                        "Contesto": row["context"],
                        "Categoria": row["category"],
                        "Sottocategoria": row["subcategory"],
                        "Importo": row["abs_amount"],
                        "% del totale": pct,
                        "N. transazioni": row["tx_count"],
                    })

                # Subtotal row
                ctx_pct = (ctx_total / grand_total * 100) if grand_total > 0 else 0
                display_rows.append({
                    "Contesto": f"TOTALE {ctx}",
                    "Categoria": "",
                    "Sottocategoria": "",
                    "Importo": ctx_total,
                    "% del totale": ctx_pct,
                    "N. transazioni": int(ctx_df["tx_count"].sum()),
                })

            # Grand total row
            display_rows.append({
                "Contesto": "TOTALE GENERALE",
                "Categoria": "",
                "Sottocategoria": "",
                "Importo": grand_total,
                "% del totale": 100.0,
                "N. transazioni": int(df_section["tx_count"].sum()),
            })

            df_display = pd.DataFrame(display_rows)
            st.dataframe(
                df_display,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Importo": st.column_config.NumberColumn(
                        format="%.2f",
                        help=t("report.col_amount_help"),
                    ),
                    "% del totale": st.column_config.NumberColumn(
                        format="%.1f %%",
                    ),
                    "N. transazioni": st.column_config.NumberColumn(format="%d"),
                },
            )

    # ── Vista 2: Trend temporale ──────────────────────────────────────────────
    st.subheader(t("report.trend_title"))

    if not monthly_data:
        st.info(t("report.no_monthly"))
    else:
        df_monthly = pd.DataFrame(monthly_data)

        tab_trend_exp, tab_trend_inc = st.tabs([t("report.tab_expenses"), t("report.tab_income")])

        with tab_trend_exp:
            df_m_exp = df_monthly[df_monthly["total_amount"] < 0].copy()
            if df_m_exp.empty:
                st.info(t("report.no_expenses_period"))
            else:
                df_m_exp["abs_amount"] = df_m_exp["total_amount"].abs()

                # Top 10 categories by total amount
                top_cats = (
                    df_m_exp.groupby("category")["abs_amount"]
                    .sum()
                    .nlargest(10)
                    .index.tolist()
                )
                df_m_top = df_m_exp[df_m_exp["category"].isin(top_cats)]

                # Line chart
                fig_line = px.line(
                    df_m_top, x="month", y="abs_amount", color="category",
                    title=t("report.chart_expense_trend"),
                    labels={"abs_amount": t("report.chart_amount"), "month": t("report.chart_month"), "category": t("report.chart_category")},
                    markers=True,
                )
                fig_line.update_layout(xaxis_title=t("report.chart_month"), yaxis_title="EUR")
                st.plotly_chart(fig_line, use_container_width=True)

                # Stacked bar chart
                fig_stack = px.bar(
                    df_m_top, x="month", y="abs_amount", color="category",
                    barmode="stack",
                    title=t("report.chart_expense_stack"),
                    labels={"abs_amount": t("report.chart_amount"), "month": t("report.chart_month"), "category": t("report.chart_category")},
                )
                st.plotly_chart(fig_stack, use_container_width=True)

        with tab_trend_inc:
            df_m_inc = df_monthly[df_monthly["total_amount"] > 0].copy()
            if df_m_inc.empty:
                st.info(t("report.no_income_period"))
            else:
                # Top 10 categories
                top_cats_inc = (
                    df_m_inc.groupby("category")["total_amount"]
                    .sum()
                    .nlargest(10)
                    .index.tolist()
                )
                df_m_top_inc = df_m_inc[df_m_inc["category"].isin(top_cats_inc)]

                fig_line_inc = px.line(
                    df_m_top_inc, x="month", y="total_amount", color="category",
                    title=t("report.chart_income_trend"),
                    labels={"total_amount": t("report.chart_amount"), "month": t("report.chart_month"), "category": t("report.chart_category")},
                    markers=True,
                )
                fig_line_inc.update_layout(xaxis_title=t("report.chart_month"), yaxis_title="EUR")
                st.plotly_chart(fig_line_inc, use_container_width=True)

                fig_stack_inc = px.bar(
                    df_m_top_inc, x="month", y="total_amount", color="category",
                    barmode="stack",
                    title=t("report.chart_income_stack"),
                    labels={"total_amount": t("report.chart_amount"), "month": t("report.chart_month"), "category": t("report.chart_category")},
                )
                st.plotly_chart(fig_stack_inc, use_container_width=True)

    # ── Vista 3: Export Excel ─────────────────────────────────────────────────
    st.divider()
    st.subheader(t("report.export_title"))

    # Build pivot for export
    pivot_rows = []
    for item in agg_data:
        abs_amt = abs(item["total_amount"])
        pivot_rows.append({
            "Contesto": item["context"],
            "Categoria": item["category"],
            "Sottocategoria": item["subcategory"],
            "Importo": item["total_amount"],
            "Importo assoluto": abs_amt,
            "N. transazioni": item["tx_count"],
            "Tipo": "Uscita" if item["total_amount"] < 0 else "Entrata",
        })
    pivot_export_df = pd.DataFrame(pivot_rows)

    # Build monthly for export
    monthly_export_df = pd.DataFrame(monthly_data) if monthly_data else pd.DataFrame()

    # Fetch detail transactions for per-context sheets
    detail_txs = tx_svc.get_transactions_for_export(
        date_from=_date_from_str,
        date_to=_date_to_str,
        account_ids=_account_ids,
        exclude_internal=True,
    )

    xlsx_bytes = _generate_xlsx(
        pivot_export_df, monthly_export_df, detail_txs, _dec, _thou
    )

    period_label = ""
    if date_from:
        period_label += date_from.strftime("%Y%m%d")
    period_label += "_"
    if date_to:
        period_label += date_to.strftime("%Y%m%d")

    st.download_button(
        t("report.export_btn"),
        xlsx_bytes,
        f"spendifai_report_{period_label}.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=False,
    )
