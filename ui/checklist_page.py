"""Check List — presenza transazioni per mese e conto.

Mostra una tabella pivot mese × conto con il conteggio delle transazioni.
I mesi sono in ordine decrescente (corrente in cima); i conti senza
transazioni per quel mese mostrano un'icona di assenza.
"""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import streamlit as st

from services.settings_service import SettingsService
from services.transaction_service import TransactionService
from support.logging import setup_logging

logger = setup_logging()


# ── Costanti di visualizzazione ───────────────────────────────────────────────

_ICON_NO_TX    = "—"
_ICON_HAS_TX   = ""
_COLOR_NO_TX   = "#c8c8c8"
_COLOR_LOW     = "#9ecae1"
_COLOR_MED     = "#4292c6"
_COLOR_HIGH    = "#084594"
_COLOR_TEXT_DK = "#ffffff"


def _month_label(ym: str) -> str:
    """'2025-03' → 'Mar 2025'"""
    try:
        return datetime.strptime(ym, "%Y-%m").strftime("%b %Y")
    except ValueError:
        return ym


def _cell_color(val: int) -> str:
    if val == 0:
        return f"color: {_COLOR_NO_TX}; font-style: italic"
    if val < 5:
        return f"background-color: {_COLOR_LOW}; color: #003366"
    if val < 20:
        return f"background-color: {_COLOR_MED}; color: {_COLOR_TEXT_DK}"
    return f"background-color: {_COLOR_HIGH}; color: {_COLOR_TEXT_DK}"


def _cell_fmt(val: int) -> str:
    return _ICON_NO_TX if val == 0 else str(val)


def _all_months_range(oldest_ym: str, newest_ym: str) -> list[str]:
    """Return every 'YYYY-MM' string from newest_ym down to oldest_ym (inclusive)."""
    y_new, m_new = int(newest_ym[:4]), int(newest_ym[5:])
    y_old, m_old = int(oldest_ym[:4]), int(oldest_ym[5:])
    months = []
    y, m = y_new, m_new
    while (y, m) >= (y_old, m_old):
        months.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return months


def _build_pivot(
    rows: list,
    all_accounts: list[str],
    current_ym: str,
) -> pd.DataFrame:
    """Build a month × account DataFrame of tx counts."""
    data_months = [r.year_month for r in rows if r.year_month]
    oldest_ym = min(data_months) if data_months else current_ym

    months_sorted = _all_months_range(oldest_ym, current_ym)

    data: dict[str, dict[str, int]] = {ym: {} for ym in months_sorted}
    for r in rows:
        ym = r.year_month
        if not ym:
            continue
        acc = r.account_label or "(nessun conto)"
        if ym in data:
            data[ym][acc] = int(r.tx_count)

    records = []
    for ym in months_sorted:
        row: dict = {"Mese": _month_label(ym)}
        for acc in all_accounts:
            row[acc] = data[ym].get(acc, 0)
        records.append(row)

    df = pd.DataFrame(records).set_index("Mese")
    return df


def render_checklist_page(engine) -> None:
    st.header("✅ Check List — Presenza transazioni per mese e conto")
    st.caption(
        "Una riga per ogni mese (dal corrente in poi verso il passato), "
        "una colonna per ogni conto. Il valore è il numero di transazioni; "
        "**—** indica nessuna transazione per quel mese."
    )

    cfg_svc = SettingsService(engine)
    tx_svc  = TransactionService(engine)

    today = date.today()
    current_ym = today.strftime("%Y-%m")

    # ── Carica dati ───────────────────────────────────────────────────────────
    account_rows      = cfg_svc.get_accounts()
    defined_accounts  = [a.name for a in account_rows]
    tx_account_labels = tx_svc.get_distinct_account_labels()
    tx_account_set    = set(tx_account_labels)
    extra             = sorted(tx_account_set - set(defined_accounts))
    all_accounts      = defined_accounts + extra

    count_rows = tx_svc.get_monthly_tx_counts()

    # ── Stato vuoto ───────────────────────────────────────────────────────────
    if not all_accounts and not count_rows:
        st.info(
            "Nessun conto e nessuna transazione trovati. "
            "Aggiungi conti dalla pagina **Impostazioni** e importa gli estratti conto."
        )
        return

    if not all_accounts:
        all_accounts = sorted({r.account_label for r in count_rows if r.account_label})

    # ── Pivot table ───────────────────────────────────────────────────────────
    df = _build_pivot(count_rows, all_accounts, current_ym)

    # ── Metriche sommario ─────────────────────────────────────────────────────
    total_tx = sum(r.tx_count for r in count_rows)
    n_months_with_data = len({r.year_month for r in count_rows if r.year_month})
    col1, col2, col3 = st.columns(3)
    col1.metric("Transazioni totali", f"{total_tx:,}".replace(",", "."))
    col2.metric("Conti monitorati", len(all_accounts))
    col3.metric("Mesi con dati", n_months_with_data)

    st.divider()

    # ── Filtro opzionale ──────────────────────────────────────────────────────
    with st.expander("🔍 Filtri", expanded=False):
        filter_accounts = st.multiselect(
            "Mostra solo conti",
            options=all_accounts,
            default=[],
            help="Lascia vuoto per mostrare tutti i conti.",
        )
        col_a, col_b = st.columns(2)
        max_months = col_a.number_input(
            "Ultimi N mesi (0 = tutti)",
            min_value=0, max_value=120, value=0, step=1,
        )
        hide_empty_rows = col_b.checkbox(
            "Nascondi mesi senza transazioni",
            value=False,
            help="Non mostra i mesi in cui nessun conto ha transazioni.",
        )

    display_df = df.copy()
    if filter_accounts:
        cols_to_keep = [c for c in display_df.columns if c in filter_accounts]
        display_df = display_df[cols_to_keep]

    if hide_empty_rows:
        display_df = display_df[display_df.sum(axis=1) > 0]

    if max_months and max_months > 0:
        display_df = display_df.head(int(max_months))

    # ── Visualizzazione ───────────────────────────────────────────────────────
    if display_df.empty:
        st.warning("Nessun dato da mostrare con i filtri selezionati.")
        return

    styled = (
        display_df.style
        .applymap(_cell_color)
        .format(_cell_fmt)
        .set_properties(**{"text-align": "center", "min-width": "80px"})
        .set_table_styles([
            {"selector": "th", "props": [("text-align", "center"), ("font-size", "13px")]},
            {"selector": "td", "props": [("font-size", "14px"), ("padding", "6px 12px")]},
            {"selector": "th.row_heading", "props": [("text-align", "left"), ("min-width", "90px")]},
        ])
    )

    st.dataframe(styled, use_container_width=True)

    with st.expander("ℹ️ Legenda colori", expanded=False):
        st.markdown(
            f"""
| Colore | Significato |
|---|---|
| — (grigio chiaro) | Nessuna transazione |
| 🔵 Azzurro tenue | 1–4 transazioni |
| 🔵 Azzurro medio | 5–19 transazioni |
| 🔵 Azzurro scuro | ≥ 20 transazioni |
"""
        )

    csv = display_df.reset_index().to_csv(index=False).encode("utf-8")
    st.download_button(
        label="⬇️ Scarica CSV",
        data=csv,
        file_name=f"checklist_{today.strftime('%Y%m%d')}.csv",
        mime="text/csv",
    )
