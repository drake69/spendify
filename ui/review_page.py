"""Review page (RF-08): manual review of low/medium confidence transactions."""
from __future__ import annotations

import streamlit as st
import pandas as pd

from db.models import Transaction, get_session
from db.repository import (
    apply_rules_to_review_transactions,
    bulk_set_giroconto_by_description,
    create_category_rule,
    get_all_user_settings,
    get_category_rules,
    get_taxonomy_config,
    get_transactions,
    get_transactions_by_rule_pattern,
    toggle_transaction_giroconto,
    update_transaction_category,
)
from support.formatting import format_amount_display, format_date_display, format_raw_amount_display
from support.logging import setup_logging

logger = setup_logging()


def render_review_page(engine):
    st.header("🔍 Review — Revisione Manuale")

    # Auto-apply rules to to_review transactions before rendering
    with get_session(engine) as _s:
        _rules = get_category_rules(_s)
        _auto_resolved = apply_rules_to_review_transactions(_s, _rules)
        if _auto_resolved:
            _s.commit()

    if _auto_resolved:
        st.success(f"✅ {_auto_resolved} transazioni risolte automaticamente dalle regole.")
        logger.info(f"review_page: auto-resolved {_auto_resolved} transactions via rules")

    session = get_session(engine)
    with session:
        taxonomy = get_taxonomy_config(session)
        all_categories = taxonomy.all_expense_categories + taxonomy.all_income_categories
        settings = get_all_user_settings(session)
        _date_fmt = settings.get("date_display_format", "%d/%m/%Y")
        _dec = settings.get("amount_decimal_sep", ",")
        _thou = settings.get("amount_thousands_sep", ".")

        only_review = st.toggle("Solo transazioni da rivedere", value=True, key="review_only_toggle")
        filters = {"to_review": True} if only_review else {}
        txs = get_transactions(session, filters=filters)

        if not txs:
            st.success("Nessuna transazione richiede revisione.")
            return

        if only_review:
            st.info(f"{len(txs)} transazioni in coda di revisione.")
        else:
            n_review = sum(1 for tx in txs if tx.to_review)
            st.info(f"{len(txs)} transazioni totali · {n_review} da rivedere ⚠️")

        show_raw = st.toggle("Mostra valori originali (raw)", value=False, key="review_show_raw")

        data = [
            {
                "id": tx.id,
                "Data": format_date_display(tx.date, _date_fmt),
                "Descrizione": (tx.description or "")[:100],
                "Entrata": float(tx.amount) if float(tx.amount) > 0 else None,
                "Uscita": abs(float(tx.amount)) if float(tx.amount) < 0 else None,
                "Tipo": tx.tx_type,
                "Categoria": tx.category or "",
                "Sottocategoria": tx.subcategory or "",
                "Confidenza": tx.category_confidence or "",
                "⚠️": "⚠️" if tx.to_review else "",
                "Desc. originale": (tx.raw_description or "")[:100],
                "Importo originale": format_raw_amount_display(tx.raw_amount),
            }
            for tx in txs
        ]
        df = pd.DataFrame(data)

        hide_cols = ["id"]
        if not show_raw:
            hide_cols += ["Desc. originale", "Importo originale"]

        display_df = df.drop(columns=hide_cols)
        st.dataframe(
            display_df,
            use_container_width=True,
            column_config={
                "Entrata": st.column_config.NumberColumn("Entrata", format="%.2f"),
                "Uscita": st.column_config.NumberColumn("Uscita", format="%.2f"),
            },
        )

    st.divider()
    st.subheader("Applica correzione")

    with get_session(engine) as session2:
        filters2 = {"to_review": True} if only_review else {}
        txs2 = get_transactions(session2, filters=filters2)

    tx_options = {
        f"{'⚠️ ' if tx.to_review else ''}{format_date_display(tx.date, _date_fmt)} | "
        f"{(tx.description or '')[:60]} | "
        f"{format_amount_display(abs(float(tx.amount)), _dec, _thou, symbol='')}": tx
        for tx in txs2
    }

    selected_label = st.selectbox("Seleziona transazione", list(tx_options.keys()),
                                  key="review_tx_select")
    selected_tx = tx_options[selected_label]

    is_giroconto = selected_tx.tx_type in ("internal_out", "internal_in")

    # Current assignment (shown as context)
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
            key="review_giroconto_similar",
        )

    giroconto_label = "↩️ Rimuovi da giroconti" if is_giroconto else "🔄 Segna come giroconto"
    if st.button(giroconto_label, key="review_toggle_giroconto"):
        with get_session(engine) as session_g:
            ok, new_type = toggle_transaction_giroconto(session_g, selected_tx.id)
            n_extra = 0
            if ok and apply_to_similar and selected_tx.description:
                make_giroconto = new_type in ("internal_out", "internal_in")
                n_extra = bulk_set_giroconto_by_description(
                    session_g, selected_tx.description, make_giroconto, exclude_id=selected_tx.id
                )
            session_g.commit()
        if ok:
            action = "rimossa dai giroconti" if new_type in ("expense", "income") else "segnata come giroconto"
            extra_msg = f" · {n_extra} transazioni simili aggiornate." if n_extra else ""
            st.success(f"Transazione {action} (tipo: {new_type}).{extra_msg}")
            st.rerun()
        else:
            st.error("Transazione non trovata.")

    # ── Correzione categoria (solo se non giroconto) ───────────────────────────
    if not is_giroconto:
        col1, col2 = st.columns(2)
        with col1:
            cat_idx = all_categories.index(selected_tx.category) if selected_tx.category in all_categories else 0
            new_cat = st.selectbox("Nuova categoria", all_categories, index=cat_idx,
                                   key="review_cat")
        with col2:
            subs = taxonomy.valid_subcategories(new_cat)
            if subs:
                sub_idx = subs.index(selected_tx.subcategory) \
                    if (selected_tx.subcategory in subs and selected_tx.category == new_cat) else 0
                new_sub = st.selectbox("Nuova sottocategoria", subs, index=sub_idx,
                                       key=f"review_sub_{new_cat}")
            else:
                new_sub = st.text_input("Nuova sottocategoria",
                                        value=selected_tx.subcategory or "",
                                        key=f"review_sub_{new_cat}")

        save_rule = st.checkbox(
            "Salva come regola deterministica (applica a tutte le transazioni simili)",
            value=False,
            key="review_save_rule",
        )

        if st.button("💾 Applica correzione", type="primary"):
            with get_session(engine) as session3:
                ok = update_transaction_category(session3, selected_tx.id, new_cat, new_sub)
                if ok:
                    rule_msg = ""
                    if save_rule and selected_tx.description:
                        _, created = create_category_rule(
                            session=session3,
                            pattern=selected_tx.description,
                            match_type="contains",
                            category=new_cat,
                            subcategory=new_sub,
                            priority=10,
                        )
                        similar = get_transactions_by_rule_pattern(
                            session3, selected_tx.description, "contains"
                        )
                        n_similar = 0
                        for stx in similar:
                            if stx.id != selected_tx.id:
                                update_transaction_category(session3, stx.id, new_cat, new_sub)
                                n_similar += 1
                        rule_tag = "creata" if created else "aggiornata"
                        rule_msg = f" · Regola {rule_tag}"
                        if n_similar:
                            rule_msg += f" · {n_similar} transazioni simili aggiornate."
                    session3.commit()
                    st.success(f"Categoria aggiornata: {new_cat} / {new_sub}{rule_msg}")
                    st.rerun()
                else:
                    st.error("Transazione non trovata.")
