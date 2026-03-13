"""Ledger page (RF-08): filterable transaction table + export."""
from __future__ import annotations

from decimal import Decimal

import pandas as pd
import streamlit as st

from db.models import Transaction, get_session
from db.repository import (
    bulk_set_giroconto_by_description,
    create_category_rule,
    get_all_user_settings,
    get_taxonomy_config,
    get_transactions,
    get_transactions_by_rule_pattern,
    toggle_transaction_giroconto,
    update_transaction_category,
)
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

        _taxonomy = get_taxonomy_config(session)
        _all_cats = ["tutte"] + _taxonomy.all_expense_categories + _taxonomy.all_income_categories

        fcol1, fcol2 = st.columns([3, 2])
        with fcol1:
            desc_filter = st.text_input("🔍 Descrizione", placeholder="cerca nel testo…", key="ledger_desc")
        with fcol2:
            cat_filter = st.selectbox("Categoria", _all_cats, key="ledger_cat")

        filters = {}
        if date_from:
            filters["date_from"] = date_from.isoformat()
        if date_to:
            filters["date_to"] = date_to.isoformat()
        if tx_type_filter != "tutti":
            filters["tx_type"] = tx_type_filter
        if review_only:
            filters["to_review"] = True
        if desc_filter.strip():
            filters["description"] = desc_filter.strip()
        if cat_filter != "tutte":
            filters["category"] = cat_filter

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
                "Entrata": float(tx.amount) if float(tx.amount) > 0 else None,
                "Uscita": abs(float(tx.amount)) if float(tx.amount) < 0 else None,
                "Valuta": tx.currency,
                "Tipo": ("🔄 " + tx.tx_type) if tx.tx_type in ("internal_out", "internal_in") else tx.tx_type,
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

        display_df = df.drop(columns=hide_cols)
        _num_fmt = f"%.2f"
        table_event = st.dataframe(
            display_df,
            use_container_width=True,
            height=500,
            on_select="rerun",
            selection_mode="single-row",
            column_config={
                "Entrata": st.column_config.NumberColumn("Entrata", format=_num_fmt),
                "Uscita": st.column_config.NumberColumn("Uscita", format=_num_fmt),
            },
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

        # ── Correction panel ──────────────────────────────────────────────────
        st.divider()
        taxonomy = get_taxonomy_config(session)
        all_categories = taxonomy.all_expense_categories + taxonomy.all_income_categories

    # ── Transaction selector ───────────────────────────────────────────────────
    st.subheader("Azioni transazione")
    tx_options = {
        f"{'⚠️ ' if tx.to_review else ''}{format_date_display(tx.date, _date_fmt)} | "
        f"{(tx.description or '')[:60]} | "
        f"{format_amount_display(abs(float(tx.amount)), _dec, _thou, symbol='')}": tx
        for tx in txs
    }

    _keys = list(tx_options.keys())

    # Sync selectbox when a table row is clicked
    _sel_rows = table_event.selection.rows if table_event and table_event.selection.rows else []
    if _sel_rows:
        _idx = _sel_rows[0]
        if _idx < len(_keys):
            st.session_state["ledger_correct_tx"] = _keys[_idx]

    # Reset stale session state value (e.g. after filter change or rerun)
    if st.session_state.get("ledger_correct_tx") not in tx_options:
        st.session_state["ledger_correct_tx"] = _keys[0] if _keys else None

    selected_label = st.selectbox("Seleziona transazione", _keys, key="ledger_correct_tx")
    selected_tx = tx_options[selected_label]

    is_giroconto = selected_tx.tx_type in ("internal_out", "internal_in")
    st.caption(
        f"Tipo: **{selected_tx.tx_type}** · "
        f"Categoria: **{selected_tx.category or '—'}** / "
        f"{selected_tx.subcategory or '—'} "
        f"(confidenza: {selected_tx.category_confidence or '—'})"
    )

    # ── Segna come giroconto ───────────────────────────────────────────────────
    _similar_count = 0
    if selected_tx.description:
        with get_session(engine) as _sc:
            _similar_count = _sc.query(Transaction).filter(
                Transaction.description == selected_tx.description,
                Transaction.id != selected_tx.id,
            ).count()

    apply_to_similar = False
    if _similar_count > 0:
        apply_to_similar = st.checkbox(
            f"Applica anche alle altre {_similar_count} transazioni con la stessa descrizione",
            value=True,
            key="ledger_giroconto_similar",
        )

    giroconto_label = "↩️ Rimuovi da giroconti" if is_giroconto else "🔄 Segna come giroconto"
    if st.button(giroconto_label, key="ledger_toggle_giroconto"):
        with get_session(engine) as ws:
            ok, new_type = toggle_transaction_giroconto(ws, selected_tx.id)
            n_extra = 0
            if ok and apply_to_similar and selected_tx.description:
                make_giroconto = new_type in ("internal_out", "internal_in")
                n_extra = bulk_set_giroconto_by_description(
                    ws, selected_tx.description, make_giroconto, exclude_id=selected_tx.id
                )
            ws.commit()
        if ok:
            action = "rimossa dai giroconti" if new_type in ("expense", "income") else "segnata come giroconto"
            extra_msg = f" · {n_extra} transazioni simili aggiornate." if n_extra else ""
            st.success(f"Transazione {action} (tipo: {new_type}).{extra_msg}")
            st.rerun()
        else:
            st.error("Transazione non trovata.")

    # ── Correzione categoria ───────────────────────────────────────────────────
    if not is_giroconto:
        with st.expander("✏️ Correggi categoria transazione", expanded=False):
            col1, col2 = st.columns(2)
            with col1:
                cat_idx = all_categories.index(selected_tx.category) \
                    if selected_tx.category in all_categories else 0
                new_cat = st.selectbox("Nuova categoria", all_categories, index=cat_idx,
                                       key="ledger_correct_cat")
            with col2:
                subs = taxonomy.valid_subcategories(new_cat)
                if subs:
                    sub_idx = subs.index(selected_tx.subcategory) \
                        if (selected_tx.subcategory in subs and selected_tx.category == new_cat) else 0
                    new_sub = st.selectbox("Nuova sottocategoria", subs, index=sub_idx,
                                           key=f"ledger_correct_sub__{new_cat}")
                else:
                    new_sub = st.text_input("Nuova sottocategoria",
                                            value=selected_tx.subcategory or "",
                                            key=f"ledger_correct_sub__{new_cat}")

            save_rule = st.checkbox(
                "Salva come regola deterministica (applica a tutte le transazioni simili)",
                value=False,
                key="ledger_correct_rule",
            )

            if st.button("💾 Applica correzione", type="primary", key="ledger_correct_save"):
                with get_session(engine) as ws:
                    ok = update_transaction_category(ws, selected_tx.id, new_cat, new_sub)
                    if ok:
                        rule_msg = ""
                        if save_rule and selected_tx.description:
                            _, created = create_category_rule(
                                session=ws,
                                pattern=selected_tx.description,
                                match_type="contains",
                                category=new_cat,
                                subcategory=new_sub,
                                priority=10,
                            )
                            similar = get_transactions_by_rule_pattern(
                                ws, selected_tx.description, "contains"
                            )
                            n_similar = 0
                            for stx in similar:
                                if stx.id != selected_tx.id:
                                    update_transaction_category(ws, stx.id, new_cat, new_sub)
                                    n_similar += 1
                            rule_tag = "creata" if created else "aggiornata"
                            rule_msg = f" · Regola {rule_tag}"
                            if n_similar:
                                rule_msg += f" · {n_similar} transazioni simili aggiornate."
                        ws.commit()
                        st.success(f"Categoria aggiornata: {new_cat} / {new_sub}{rule_msg}")
                        st.rerun()
                    else:
                        st.error("Transazione non trovata.")
