"""Review page (RF-08): manual review of low/medium confidence transactions."""
from __future__ import annotations

import json

import streamlit as st
import pandas as pd

from services.review_service import ReviewService
from services.rule_service import RuleService
from services.settings_service import SettingsService
from services.transaction_service import TransactionService
from support.formatting import format_amount_display, format_date_display, format_raw_amount_display
from support.logging import setup_logging

logger = setup_logging()


def render_review_page(engine):
    st.header("🔍 Review — Revisione Manuale")

    review_svc = ReviewService(engine)
    rule_svc   = RuleService(engine)
    tx_svc     = TransactionService(engine)
    cfg_svc    = SettingsService(engine)

    # Auto-apply rules to to_review transactions before rendering
    _auto_resolved = rule_svc.apply_to_review()
    if _auto_resolved:
        st.success(f"✅ {_auto_resolved} transazioni risolte automaticamente dalle regole.")
        logger.info(f"review_page: auto-resolved {_auto_resolved} transactions via rules")

    # ── Rielabora transazioni in coda di revisione ─────────────────────────────
    _n_to_review = review_svc.count_to_review()

    _rerun_label = (
        f"🔄 Rielabora con LLM ({_n_to_review} da rivedere)"
        if _n_to_review > 0
        else "🔄 Rielabora con LLM (nessuna transazione in coda di revisione)"
    )
    if st.button(_rerun_label, key="review_rerun_llm", disabled=(_n_to_review == 0)):
        st.session_state["llm_in_progress"] = True
        with st.spinner("Rielaborazione in corso…"):
            n_cleaned, n_cat = review_svc.rerun_llm_on_review()
        st.session_state["llm_in_progress"] = False
        st.success(
            f"Completato: {n_cleaned} descrizioni pulite · {n_cat} ri-categorizzate."
        )
        logger.info(f"review_page: rerun_llm: cleaned={n_cleaned} categorized={n_cat}")
        st.rerun()

    # ── Riesegui rilevamento giroconti ─────────────────────────────────────────
    if st.button(
        "🔁 Riesegui rilevamento giroconti (cross-account)",
        key="review_rerun_transfers",
        help=(
            "Riesegue il matching importo+data su TUTTE le transazioni nel DB. "
            "Utile dopo aver importato più file di conti diversi."
        ),
    ):
        with st.spinner("Rilevamento in corso…"):
            n_updated = review_svc.rerun_transfer_detection()
        if n_updated:
            st.success(f"Trovate e marcate **{n_updated}** nuove transazioni come giroconto.")
        else:
            st.info("Nessuna nuova coppia giroconto trovata.")
        logger.info(f"review_page: rerun_transfers: updated={n_updated}")
        if n_updated:
            st.rerun()

    # ── Load shared data ───────────────────────────────────────────────────────
    taxonomy = cfg_svc.get_taxonomy()
    all_categories = taxonomy.all_expense_categories + taxonomy.all_income_categories
    settings = cfg_svc.get_all()
    _date_fmt = settings.get("date_display_format", "%d/%m/%Y")
    _dec = settings.get("amount_decimal_sep", ",")
    _thou = settings.get("amount_thousands_sep", ".")

    try:
        _contexts: list[str] = json.loads(
            settings.get("contexts", '["Quotidianità", "Lavoro", "Vacanza"]')
        )
    except Exception:
        _contexts = ["Quotidianità", "Lavoro", "Vacanza"]

    only_review = st.toggle("Solo transazioni da rivedere", value=True, key="review_only_toggle")
    filters = {"to_review": True} if only_review else {}
    txs = tx_svc.get_transactions(filters=filters)

    if not txs:
        st.success("Nessuna transazione richiede revisione.")
        return

    if only_review:
        st.info(f"{len(txs)} transazioni in coda di revisione.")
    else:
        n_review = sum(1 for tx in txs if tx.to_review)
        st.info(f"{len(txs)} transazioni totali · {n_review} da rivedere ⚠️")

    show_raw = st.toggle("Mostra valori originali (raw)", value=False, key="review_show_raw")

    _SOURCE_BADGE = {
        "llm": "🧠 AI",
        "rule": "📏 Regola",
        "manual": "👤 Manuale",
        "history": "📚 Storico",
    }

    data = [
        {
            "id": tx.id,
            "sel": False,
            "Data": format_date_display(tx.date, _date_fmt),
            "Descrizione": (tx.description or "")[:100],
            "Entrata": float(tx.amount) if float(tx.amount) > 0 else None,
            "Uscita": abs(float(tx.amount)) if float(tx.amount) < 0 else None,
            "Tipo": tx.tx_type,
            "Categoria": tx.category or "",
            "Sottocategoria": tx.subcategory or "",
            "Contesto": tx.context or "",
            "Confidenza": tx.category_confidence or "",
            "Fonte": _SOURCE_BADGE.get(tx.category_source, "—"),
            "⚠️": "⚠️" if tx.to_review else "·",
            "✅": "✅" if tx.human_validated else "·",
            "Validato": bool(tx.human_validated),
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
    edited_review = st.data_editor(
        display_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "sel": st.column_config.CheckboxColumn("✔", width=40),
            "Entrata": st.column_config.NumberColumn("Entrata", format="%.2f"),
            "Uscita": st.column_config.NumberColumn("Uscita", format="%.2f"),
            "Contesto": st.column_config.SelectboxColumn(
                "Contesto", options=[""] + _contexts, required=False, width="small",
            ),
            "Fonte": st.column_config.TextColumn("Fonte", disabled=True, width=100),
            "⚠️": st.column_config.TextColumn("⚠️", disabled=True, width=40),
            "✅": st.column_config.TextColumn("✅", disabled=True, width=40),
            "Validato": st.column_config.CheckboxColumn("Validato", width=60),
        },
        key="review_grid",
    )

    # ── Detect inline edits (Validato, Contesto) ───────────────────────────
    n_val_changed = 0
    n_ctx_changed = 0
    for i in range(len(edited_review)):
        tx_id = df.iloc[i]["id"]

        new_val = bool(edited_review.iloc[i].get("Validato", False))
        old_val = bool(display_df.iloc[i].get("Validato", False))
        if new_val != old_val:
            if new_val:
                tx_svc.validate(tx_id)
            else:
                tx_svc.unvalidate(tx_id)
            n_val_changed += 1

        new_ctx = str(edited_review.iloc[i].get("Contesto", ""))
        old_ctx = str(display_df.iloc[i].get("Contesto", ""))
        if new_ctx != old_ctx:
            tx_svc.update_context(tx_id, new_ctx or None)
            n_ctx_changed += 1

    _need_rerun = False
    if n_val_changed:
        st.success(f"✅ {n_val_changed} transazioni aggiornate.")
        logger.info(f"review_page: toggled validation on {n_val_changed} via checkbox")
        _need_rerun = True
    if n_ctx_changed:
        st.success(f"✅ {n_ctx_changed} contesti aggiornati.")
        logger.info(f"review_page: updated context on {n_ctx_changed} transactions")
        _need_rerun = True
    if _need_rerun:
        st.rerun()

    selected_ids = [
        df.iloc[i]["id"]
        for i in range(len(edited_review))
        if edited_review.iloc[i].get("sel", False)
    ]
    if st.button("✅ Valida selezionate", disabled=len(selected_ids) == 0, key="review_validate_bulk"):
        n_ok = 0
        for _tid in selected_ids:
            if tx_svc.validate(_tid):
                n_ok += 1
        st.success(f"✅ {n_ok} transazioni validate.")
        logger.info(f"review_page: validated {n_ok} transactions")
        st.rerun()

    st.divider()
    st.subheader("Applica correzione")

    txs2 = tx_svc.get_transactions(filters=filters)

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
        _similar_count = review_svc.count_similar_by_description(
            selected_tx.description, selected_tx.id
        )

    apply_to_similar = False
    if _similar_count > 0:
        apply_to_similar = st.checkbox(
            f"Applica anche alle altre {_similar_count} transazioni con la stessa descrizione",
            value=True,
            key="review_giroconto_similar",
        )

    giroconto_label = "↩️ Rimuovi da giroconti" if is_giroconto else "🔄 Segna come giroconto"
    if st.button(giroconto_label, key="review_toggle_giroconto"):
        ok, new_type = tx_svc.toggle_giroconto(selected_tx.id)
        n_extra = 0
        if ok and apply_to_similar and selected_tx.description:
            make_giroconto = new_type in ("internal_out", "internal_in")
            n_extra = tx_svc.bulk_set_giroconto_by_description(
                selected_tx.description, make_giroconto, exclude_id=selected_tx.id
            )
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

        # Preview and retroactive options (shown when save_rule is checked)
        if save_rule and selected_tx.description:
            _preview_pattern = selected_tx.description
            _preview_matching = tx_svc.get_by_rule_pattern(_preview_pattern, "contains")
            st.info(f"Matcherà {len(_preview_matching)} transazioni")
            review_retroactive = st.checkbox(
                "Applica retroattivamente a tutte le transazioni",
                value=True,
                key="review_retroactive",
            )
        else:
            review_retroactive = False

        if st.button("💾 Applica correzione", type="primary"):
            ok = tx_svc.update_category(selected_tx.id, new_cat, new_sub)
            if ok:
                rule_msg = ""
                if save_rule and selected_tx.description:
                    _, created = rule_svc.create_rule(
                        pattern=selected_tx.description,
                        match_type="contains",
                        category=new_cat,
                        subcategory=new_sub,
                        priority=10,
                    )
                    rule_tag = "creata" if created else "aggiornata"
                    rule_msg = f" · Regola {rule_tag}"
                    if review_retroactive:
                        n_matched, _ = rule_svc.apply_to_all()
                        rule_msg += f" · {n_matched} transazioni aggiornate retroattivamente."
                    else:
                        similar = tx_svc.get_by_rule_pattern(selected_tx.description, "contains")
                        n_similar = 0
                        for stx in similar:
                            if stx.id != selected_tx.id:
                                tx_svc.update_category(stx.id, new_cat, new_sub)
                                n_similar += 1
                        if n_similar:
                            rule_msg += f" · {n_similar} transazioni simili aggiornate."
                st.success(f"Categoria aggiornata: {new_cat} / {new_sub}{rule_msg}")
                st.rerun()
            else:
                st.error("Transazione non trovata.")

    # ── Correggi descrizione in blocco ─────────────────────────────────────────
    st.divider()
    with st.expander("✏️ Correggi descrizione in blocco", expanded=False):
        st.caption(
            "Sostituisce la descrizione di tutte le transazioni la cui **raw description** "
            "corrisponde al pattern, poi ri-categorizza con l'LLM."
        )
        _match_types = {"exact": "Esatto", "contains": "Contiene", "regex": "Regex"}
        _raw_default = selected_tx.raw_description or selected_tx.description or ""

        bulk_pattern = st.text_input(
            "Pattern da cercare nella raw description",
            value=_raw_default,
            key="bulk_desc_pattern",
        )
        bulk_match_type = st.selectbox(
            "Tipo di match",
            options=list(_match_types.keys()),
            format_func=lambda k: _match_types[k],
            index=list(_match_types.keys()).index("contains"),
            key="bulk_desc_match_type",
        )
        bulk_new_desc = st.text_input(
            "Nuova descrizione pulita",
            value="",
            key="bulk_desc_new",
            placeholder="Inserisci la descrizione che sostituirà quella originale",
        )

        # Live preview: count matching transactions
        if bulk_pattern:
            _preview_matches = tx_svc.get_by_raw_pattern(bulk_pattern, bulk_match_type)
            st.caption(f"Transazioni corrispondenti: **{len(_preview_matches)}**")

        bulk_save_rule = st.checkbox(
            "Salva come regola (riapplicabile in futuro)",
            value=True,
            key="bulk_desc_save_rule",
        )

        if st.button("✏️ Applica in blocco + ri-categorizza", key="bulk_desc_apply", type="primary"):
            if not bulk_pattern.strip():
                st.warning("Inserisci un pattern.")
            elif not bulk_new_desc.strip():
                st.warning("Inserisci la nuova descrizione.")
            else:
                if bulk_save_rule:
                    rule_svc.create_description_rule(bulk_pattern, bulk_match_type, bulk_new_desc)

                st.session_state["llm_in_progress"] = True
                with st.spinner("Aggiornamento descrizioni e ri-categorizzazione in corso…"):
                    n_upd, n_cat = review_svc.apply_description_rule_bulk(
                        bulk_pattern, bulk_match_type, bulk_new_desc
                    )
                st.session_state["llm_in_progress"] = False

                if n_upd == 0:
                    st.warning("Nessuna transazione corrisponde al pattern.")
                else:
                    rule_note = " · Regola salvata." if bulk_save_rule else ""
                    st.success(
                        f"✅ {n_upd} descrizioni aggiornate · {n_cat} ri-categorizzate.{rule_note}"
                    )
                    logger.info(
                        f"review_page: bulk_desc: pattern={bulk_pattern!r} "
                        f"match={bulk_match_type} updated={n_upd} categorized={n_cat}"
                    )
                    st.rerun()

    # ── Regole descrizione salvate ─────────────────────────────────────────────
    _desc_rules = rule_svc.get_description_rules()

    if _desc_rules:
        with st.expander(f"📋 Regole descrizione salvate ({len(_desc_rules)})", expanded=False):
            _match_labels = {"exact": "Esatto", "contains": "Contiene", "regex": "Regex"}
            for _rule in _desc_rules:
                rcol1, rcol2, rcol3, rcol4 = st.columns([3, 1, 3, 1])
                with rcol1:
                    st.text(_rule.raw_pattern[:60] + ("…" if len(_rule.raw_pattern) > 60 else ""))
                with rcol2:
                    st.caption(_match_labels.get(_rule.match_type, _rule.match_type))
                with rcol3:
                    st.text(_rule.cleaned_description[:60] + ("…" if len(_rule.cleaned_description) > 60 else ""))
                with rcol4:
                    _btn_col1, _btn_col2 = st.columns(2)
                    with _btn_col1:
                        if st.button("▶", key=f"desc_rule_apply_{_rule.id}", help="Riapplica regola"):
                            st.session_state["llm_in_progress"] = True
                            with st.spinner("Riapplicazione in corso…"):
                                n_upd, n_cat = review_svc.apply_description_rule_bulk(
                                    _rule.raw_pattern, _rule.match_type, _rule.cleaned_description
                                )
                            st.session_state["llm_in_progress"] = False
                            st.success(f"{n_upd} aggiornate · {n_cat} ri-categorizzate.")
                            st.rerun()
                    with _btn_col2:
                        if st.button("🗑", key=f"desc_rule_del_{_rule.id}", help="Elimina regola"):
                            rule_svc.delete_description_rule(_rule.id)
                            st.rerun()
