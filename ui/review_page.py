"""Review page (RF-08): manual review of low/medium confidence transactions."""
from __future__ import annotations

import streamlit as st
import pandas as pd

from db.models import get_session
from db.repository import (
    get_all_user_settings,
    get_category_rules,
    get_taxonomy_config,
    get_transactions,
    update_transaction_category,
    create_category_rule,
)
from support.formatting import format_amount_display, format_date_display, format_raw_amount_display
from support.logging import setup_logging

logger = setup_logging()


def render_review_page(engine):
    st.header("🔍 Review — Revisione Manuale")

    session = get_session(engine)
    with session:
        taxonomy = get_taxonomy_config(session)
        all_categories = taxonomy.all_expense_categories + taxonomy.all_income_categories
        settings = get_all_user_settings(session)
        _date_fmt = settings.get("date_display_format", "%d/%m/%Y")
        _dec = settings.get("amount_decimal_sep", ",")
        _thou = settings.get("amount_thousands_sep", ".")

        txs = get_transactions(session, filters={"to_review": True})

        if not txs:
            st.success("Nessuna transazione richiede revisione.")
            return

        st.info(f"{len(txs)} transazioni in coda di revisione.")

        show_raw = st.toggle("Mostra valori originali (raw)", value=False, key="review_show_raw")

        data = [
            {
                "id": tx.id,
                "Data": format_date_display(tx.date, _date_fmt),
                "Descrizione": (tx.description or "")[:100],
                "Entrata": format_amount_display(float(tx.amount), _dec, _thou, symbol="")
                           if float(tx.amount) > 0 else "",
                "Uscita": format_amount_display(abs(float(tx.amount)), _dec, _thou, symbol="")
                          if float(tx.amount) < 0 else "",
                "Tipo": tx.tx_type,
                "Categoria attuale": tx.category or "",
                "Sottocategoria attuale": tx.subcategory or "",
                "Confidenza": tx.category_confidence or "",
                "Desc. originale": (tx.raw_description or "")[:100],
                "Importo originale": format_raw_amount_display(tx.raw_amount),
            }
            for tx in txs
        ]
        df = pd.DataFrame(data)

        hide_cols = ["id"]
        if not show_raw:
            hide_cols += ["Desc. originale", "Importo originale"]

        st.dataframe(df.drop(columns=hide_cols), use_container_width=True)

        st.divider()
        st.subheader("Applica correzione")

        tx_options = {
            f"{d['Data']} | {d['Descrizione'][:60]} | "
            f"{d['Uscita'] or d['Entrata']}": d["id"]
            for d in data
        }

        selected_label = st.selectbox("Seleziona transazione", list(tx_options.keys()))
        selected_id = tx_options[selected_label]

        col1, col2 = st.columns(2)
        with col1:
            new_cat = st.selectbox("Nuova categoria", all_categories)
        with col2:
            subs = taxonomy.valid_subcategories(new_cat)
            new_sub = st.selectbox("Nuova sottocategoria", subs) if subs else st.text_input("Nuova sottocategoria")

        save_rule = st.checkbox(
            "Salva come regola deterministica (applica a future transazioni simili)",
            value=False,
        )

        if st.button("💾 Applica correzione", type="primary"):
            ok = update_transaction_category(session, selected_id, new_cat, new_sub)
            if ok:
                if save_rule:
                    # Find description for the rule pattern
                    tx_row = next((tx for tx in txs if tx.id == selected_id), None)
                    if tx_row and tx_row.description:
                        create_category_rule(
                            session=session,
                            pattern=tx_row.description,
                            match_type="contains",
                            category=new_cat,
                            subcategory=new_sub,
                            priority=10,
                        )
                session.commit()
                st.success(f"Categoria aggiornata: {new_cat} / {new_sub}")
                st.rerun()
            else:
                st.error("Transazione non trovata.")
