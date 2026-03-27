"""Budget vs Actual page (A-02): compare actual spending % vs budget targets."""
from __future__ import annotations

from calendar import monthrange
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from services.budget_service import BudgetService
from support.formatting import format_amount_display
from support.logging import setup_logging

logger = setup_logging()

_MONTH_NAMES_IT = [
    "", "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno",
    "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre",
]

_STATUS_ICON = {
    "green": "\U0001F7E2",   # green circle
    "yellow": "\U0001F7E1",  # yellow circle
    "red": "\U0001F534",     # red circle
    "none": "\u26AA",        # white circle
}


def _fmt_eur(val: float, dec: str = ",", thou: str = ".") -> str:
    return format_amount_display(val, decimal_sep=dec, thousands_sep=thou)


def _fmt_pct(val: float | None) -> str:
    if val is None:
        return "—"
    return f"{val:.1f}%".replace(".", ",")


def _period_bounds(period_type: str, ref_date: date) -> tuple[str, str, str]:
    """Return (date_from, date_to, label) for the selected period around ref_date."""
    if period_type == "month":
        first = ref_date.replace(day=1)
        last_day = monthrange(ref_date.year, ref_date.month)[1]
        last = ref_date.replace(day=last_day)
        label = f"{_MONTH_NAMES_IT[ref_date.month]} {ref_date.year}"
        return first.isoformat(), last.isoformat(), label

    if period_type == "quarter":
        q = (ref_date.month - 1) // 3
        first_month = q * 3 + 1
        last_month = first_month + 2
        first = date(ref_date.year, first_month, 1)
        last_day = monthrange(ref_date.year, last_month)[1]
        last = date(ref_date.year, last_month, last_day)
        label = f"Q{q + 1} {ref_date.year}"
        return first.isoformat(), last.isoformat(), label

    if period_type == "year":
        first = date(ref_date.year, 1, 1)
        last = date(ref_date.year, 12, 31)
        label = str(ref_date.year)
        return first.isoformat(), last.isoformat(), label

    # custom — handled separately
    return ref_date.isoformat(), ref_date.isoformat(), ""


def _navigate(period_type: str, ref_date: date, direction: int) -> date:
    """Move ref_date forward/backward by one period unit."""
    if period_type == "month":
        m = ref_date.month + direction
        y = ref_date.year
        while m < 1:
            m += 12
            y -= 1
        while m > 12:
            m -= 12
            y += 1
        day = min(ref_date.day, monthrange(y, m)[1])
        return date(y, m, day)

    if period_type == "quarter":
        m = ref_date.month + direction * 3
        y = ref_date.year
        while m < 1:
            m += 12
            y -= 1
        while m > 12:
            m -= 12
            y += 1
        return date(y, m, 1)

    if period_type == "year":
        return date(ref_date.year + direction, ref_date.month, ref_date.day)

    return ref_date


def render_budget_vs_actual_page(engine):
    st.header("📊 Budget vs Actual")

    svc = BudgetService(engine)

    # ── Period selector ───────────────────────────────────────────────────────
    col_type, col_nav = st.columns([2, 4])

    with col_type:
        period_options = {
            "month": "Mese",
            "quarter": "Trimestre",
            "year": "Anno",
            "custom": "Personalizzato",
        }
        period_type = st.selectbox(
            "Periodo",
            options=list(period_options.keys()),
            format_func=lambda k: period_options[k],
            key="bva_period_type",
        )

    # Reference date in session state
    if "bva_ref_date" not in st.session_state:
        st.session_state["bva_ref_date"] = date.today()

    ref_date = st.session_state["bva_ref_date"]

    if period_type == "custom":
        col_from, col_to = st.columns(2)
        with col_from:
            custom_from = st.date_input("Da", value=ref_date.replace(day=1), key="bva_custom_from")
        with col_to:
            custom_to = st.date_input("A", value=ref_date, key="bva_custom_to")
        date_from = custom_from.isoformat()
        date_to = custom_to.isoformat()
        period_label = f"{custom_from.strftime('%d/%m/%Y')} — {custom_to.strftime('%d/%m/%Y')}"
    else:
        date_from, date_to, period_label = _period_bounds(period_type, ref_date)

        with col_nav:
            st.markdown("&nbsp;")  # spacing
            nav_left, nav_label, nav_right = st.columns([1, 3, 1])
            with nav_left:
                if st.button("← Precedente", key="bva_prev", use_container_width=True):
                    st.session_state["bva_ref_date"] = _navigate(period_type, ref_date, -1)
                    st.rerun()
            with nav_label:
                st.markdown(f"### {period_label}")
            with nav_right:
                if st.button("Successivo →", key="bva_next", use_container_width=True):
                    st.session_state["bva_ref_date"] = _navigate(period_type, ref_date, 1)
                    st.rerun()

    # ── Fetch data ────────────────────────────────────────────────────────────
    data = svc.get_actual_vs_budget(date_from, date_to)

    # ── Summary metrics ───────────────────────────────────────────────────────
    st.divider()
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.metric("Totale entrate", _fmt_eur(data["total_income"]))
    with m2:
        st.metric("Totale uscite", _fmt_eur(data["total_expenses"]))
    with m3:
        st.metric("Liquidità residua", _fmt_eur(data["liquidity"]))
    with m4:
        liq_pct = data["liquidity_actual_pct"]
        liq_target = data["liquidity_target_pct"]
        delta_str = None
        if liq_target > 0:
            diff = liq_pct - liq_target
            delta_str = f"{diff:+.1f}%".replace(".", ",")
        st.metric(
            "% Liquidità",
            _fmt_pct(liq_pct),
            delta=delta_str,
        )
        if liq_target > 0 and liq_pct < liq_target:
            st.error(f"Liquidità sotto obiettivo ({_fmt_pct(liq_target)})")

    # ── Budget table ──────────────────────────────────────────────────────────
    st.divider()
    st.subheader("Dettaglio per Categoria")

    rows = data["rows"]
    if not rows:
        st.info("Nessun dato disponibile per il periodo selezionato.")
        return

    # Build display table
    table_data = []
    for r in rows:
        table_data.append({
            "Categoria": r["category"],
            "Obiettivo %": _fmt_pct(r["target_pct"]) if r["target_pct"] is not None else "—",
            "Attuale %": _fmt_pct(r["actual_pct"]),
            "Attuale €": _fmt_eur(r["actual_amount"]),
            "Scostamento": (f"{r['deviation']:+.1f}%".replace(".", ",") if r["deviation"] is not None else "—"),
            "Stato": _STATUS_ICON.get(r["status"], ""),
        })

    df_table = pd.DataFrame(table_data)
    st.dataframe(
        df_table,
        use_container_width=True,
        hide_index=True,
        height=min(len(table_data) * 40 + 50, 600),
    )

    # ── Charts ────────────────────────────────────────────────────────────────
    st.divider()

    tab_bar, tab_donut = st.tabs(["📊 Confronto Budget", "🍩 Distribuzione Attuale"])

    with tab_bar:
        _render_comparison_chart(rows)

    with tab_donut:
        _render_donut_chart(rows)


def _render_comparison_chart(rows: list[dict]):
    """Horizontal bar chart: target % vs actual % side by side."""
    # Filter to categories that have either target or actual > 0
    chart_rows = [r for r in rows if (r["target_pct"] or 0) > 0 or r["actual_pct"] > 0]
    if not chart_rows:
        st.info("Nessun dato da visualizzare.")
        return

    categories = [r["category"] for r in chart_rows]
    targets = [r["target_pct"] or 0 for r in chart_rows]
    actuals = [r["actual_pct"] for r in chart_rows]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=categories,
        x=targets,
        name="Obiettivo %",
        orientation="h",
        marker_color="#636EFA",
        text=[f"{v:.1f}%" for v in targets],
        textposition="auto",
    ))
    fig.add_trace(go.Bar(
        y=categories,
        x=actuals,
        name="Attuale %",
        orientation="h",
        marker_color="#EF553B",
        text=[f"{v:.1f}%" for v in actuals],
        textposition="auto",
    ))
    fig.update_layout(
        barmode="group",
        title="Obiettivo vs Attuale per Categoria",
        xaxis_title="% sul totale spese",
        yaxis=dict(autorange="reversed"),
        height=max(300, len(chart_rows) * 50),
        margin=dict(l=10, r=10, t=40, b=30),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_donut_chart(rows: list[dict]):
    """Donut chart showing actual expense distribution."""
    chart_rows = [r for r in rows if r["actual_pct"] > 0]
    if not chart_rows:
        st.info("Nessun dato da visualizzare.")
        return

    labels = [r["category"] for r in chart_rows]
    values = [r["actual_amount"] for r in chart_rows]

    fig = go.Figure(data=[go.Pie(
        labels=labels,
        values=values,
        hole=0.4,
        textinfo="label+percent",
        textposition="outside",
    )])
    fig.update_layout(
        title="Distribuzione Spese Effettive",
        height=500,
        margin=dict(l=10, r=10, t=40, b=30),
        showlegend=True,
    )
    st.plotly_chart(fig, use_container_width=True)
