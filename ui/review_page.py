"""Review page (RF-08): manual review of low/medium confidence transactions."""
from __future__ import annotations

import streamlit as st
import pandas as pd

from db.models import Transaction, get_session
from db.repository import (
    apply_rules_to_review_transactions,
    bulk_set_giroconto_by_description,
    create_category_rule,
    create_description_rule,
    delete_description_rule,
    get_all_transfer_keyword_patterns,
    get_all_user_settings,
    get_category_rules,
    get_description_rules,
    get_taxonomy_config,
    get_transactions,
    get_transactions_by_raw_pattern,
    get_transactions_by_rule_pattern,
    toggle_transaction_giroconto,
    update_transaction_category,
)
from support.formatting import format_amount_display, format_date_display, format_raw_amount_display
from support.logging import setup_logging

logger = setup_logging()


def _rerun_llm_on_review(engine) -> tuple[int, int]:
    """Re-run description cleaning + categorization on all to_review=True transactions.

    - Always cleans description (if raw_description differs case-insensitively).
    - Re-categorizes unless category_source is 'manual' or 'rule'.

    Returns (n_cleaned, n_categorized).
    """
    from core.description_cleaner import clean_descriptions_batch
    from core.categorizer import categorize_batch
    from core.orchestrator import ProcessingConfig, _build_backend, _get_fallback_backend
    from core.sanitizer import SanitizationConfig

    with get_session(engine) as _s:
        s = get_all_user_settings(_s)

    owner_names = [n.strip() for n in s.get("owner_names", "").split(",") if n.strip()]
    config = ProcessingConfig(
        llm_backend=s.get("llm_backend", "local_ollama"),
        sanitize_config=SanitizationConfig(
            owner_names=owner_names,
            description_language=s.get("description_language", "it"),
        ),
        ollama_base_url=s.get("ollama_base_url", "http://localhost:11434"),
        ollama_model=s.get("ollama_model", "gemma3:12b"),
        openai_model=s.get("openai_model", "gpt-4o-mini"),
        openai_api_key=s.get("openai_api_key", ""),
        claude_model=s.get("anthropic_model", "claude-3-5-haiku-20241022"),
        anthropic_api_key=s.get("anthropic_api_key", ""),
        compat_base_url=s.get("compat_base_url", ""),
        compat_api_key=s.get("compat_api_key", ""),
        compat_model=s.get("compat_model", ""),
        description_language=s.get("description_language", "it"),
    )
    backend = _build_backend(config)
    fallback = _get_fallback_backend(config)

    # Load all non-giroconto transactions that still need review
    _categorizable_types = ("expense", "income", "card_tx", "unknown")
    with get_session(engine) as _s:
        uncleaned = _s.query(Transaction).filter(
            Transaction.to_review == True,  # noqa: E712
            Transaction.tx_type.in_(_categorizable_types),
        ).all()
        tx_dicts = [
            {
                "id": tx.id,
                "description": tx.description or "",
                "raw_description": tx.raw_description or "",
                "tx_type": tx.tx_type or "unknown",
                "amount": float(tx.amount or 0),
                "date": tx.date,
                "source_file": tx.source_file or "",
                "category_source": tx.category_source or "",
            }
            for tx in uncleaned
        ]

    if not tx_dicts:
        return 0, 0

    # Re-run description cleaner
    tx_dicts = clean_descriptions_batch(
        tx_dicts,
        llm_backend=backend,
        fallback_backend=fallback,
        source_name="review_rerun",
        sanitize_config=config.sanitize_config,
    )
    n_cleaned = sum(
        1 for tx in tx_dicts if tx["description"] != tx["raw_description"]
    )

    # Re-run categorizer on categorizable types — skip manual/rule-categorized
    _categorizable = {"expense", "income", "card_tx", "unknown"}
    _protected = {"manual", "rule"}
    to_categorize = [
        t for t in tx_dicts
        if t["tx_type"] in _categorizable and t.get("category_source") not in _protected
    ]

    cat_map = {}
    if to_categorize:
        with get_session(engine) as _s:
            taxonomy = get_taxonomy_config(_s)
            user_rules = get_category_rules(_s)
        cat_results = categorize_batch(
            transactions=to_categorize,
            taxonomy=taxonomy,
            user_rules=user_rules,
            llm_backend=backend,
            sanitize_config=config.sanitize_config,
            fallback_backend=fallback,
            confidence_threshold=config.confidence_threshold,
            description_language=config.description_language,
            source_name="review_rerun",
        )
        cat_map = {tx["id"]: result for tx, result in zip(to_categorize, cat_results)}

    # Write results back to DB
    n_categorized = 0
    with get_session(engine) as _s:
        for tx_dict in tx_dicts:
            tx = _s.get(Transaction, tx_dict["id"])
            if tx is None:
                continue
            tx.description = tx_dict["description"]
            result = cat_map.get(tx_dict["id"])
            if result and result.category:
                tx.category = result.category
                tx.subcategory = result.subcategory
                tx.category_confidence = result.confidence.value
                tx.category_source = result.source.value
                tx.to_review = result.to_review
                n_categorized += 1
        _s.commit()

    return n_cleaned, n_categorized


def _rerun_transfer_detection(engine) -> int:
    """Re-run detect_internal_transfers on ALL non-giroconto transactions in the DB.

    Finds cross-account pairs that couldn't be matched at import time because the
    counterpart file hadn't been imported yet.

    Returns count of transactions whose tx_type was updated.
    """
    from decimal import Decimal as _Decimal
    import pandas as pd
    from core.normalizer import detect_internal_transfers
    from core.orchestrator import ProcessingConfig

    with get_session(engine) as _s:
        s = get_all_user_settings(_s)
        keyword_patterns = get_all_transfer_keyword_patterns(_s)
        # Load all non-giroconto transactions
        _non_internal = {"expense", "income", "card_tx", "unknown"}
        txs = _s.query(Transaction).filter(
            Transaction.tx_type.in_(_non_internal)
        ).all()
        rows = [
            {
                "id": tx.id,
                "date": tx.date,
                "amount": _Decimal(str(tx.amount or 0)),
                "description": tx.description or "",
                "account_label": tx.account_label or "",
                "tx_type": tx.tx_type or "unknown",
                "transfer_pair_id": tx.transfer_pair_id,
                "transfer_confidence": tx.transfer_confidence,
            }
            for tx in txs
        ]

    if not rows:
        return 0

    owner_names = [n.strip() for n in s.get("owner_names", "").split(",") if n.strip()]
    use_owner = s.get("use_owner_names_giroconto", "false").lower() == "true"

    df = pd.DataFrame(rows)
    # Convert date strings to date objects for detect_internal_transfers
    from datetime import date as _date
    import pandas as _pd
    df["date"] = _pd.to_datetime(df["date"]).dt.date

    df_result = detect_internal_transfers(
        df,
        keyword_patterns=keyword_patterns,
        owner_names=owner_names if use_owner else None,
    )

    # Find rows where tx_type changed
    changed = df_result[df_result["tx_type"] != df["tx_type"]]
    if changed.empty:
        return 0

    with get_session(engine) as _s:
        updated = 0
        for _, row in changed.iterrows():
            tx = _s.get(Transaction, row["id"])
            if tx is None:
                continue
            tx.tx_type = row["tx_type"]
            tx.transfer_pair_id = row.get("transfer_pair_id") or tx.transfer_pair_id
            tx.transfer_confidence = row.get("transfer_confidence") or tx.transfer_confidence
            updated += 1
        if updated:
            _s.commit()
    return updated


def _apply_description_rule_bulk(
    engine,
    raw_pattern: str,
    match_type: str,
    cleaned_description: str,
) -> tuple[int, int]:
    """Update description for all matching transactions, then re-categorize with LLM.

    Returns (n_updated, n_categorized).
    """
    from core.categorizer import categorize_batch
    from core.orchestrator import ProcessingConfig, _build_backend, _get_fallback_backend
    from core.sanitizer import SanitizationConfig

    with get_session(engine) as _s:
        s = get_all_user_settings(_s)
        matching = get_transactions_by_raw_pattern(_s, raw_pattern, match_type)
        matching_ids = [tx.id for tx in matching]
        tx_dicts = [
            {
                "id": tx.id,
                "description": cleaned_description,
                "raw_description": tx.raw_description or "",
                "tx_type": tx.tx_type or "unknown",
                "amount": float(tx.amount or 0),
                "date": tx.date,
                "source_file": tx.source_file or "",
            }
            for tx in matching
        ]
        for tx in matching:
            tx.description = cleaned_description
        _s.commit()

    n_updated = len(matching_ids)
    if n_updated == 0:
        return 0, 0

    owner_names = [n.strip() for n in s.get("owner_names", "").split(",") if n.strip()]
    config = ProcessingConfig(
        llm_backend=s.get("llm_backend", "local_ollama"),
        sanitize_config=SanitizationConfig(
            owner_names=owner_names,
            description_language=s.get("description_language", "it"),
        ),
        ollama_base_url=s.get("ollama_base_url", "http://localhost:11434"),
        ollama_model=s.get("ollama_model", "gemma3:12b"),
        openai_model=s.get("openai_model", "gpt-4o-mini"),
        openai_api_key=s.get("openai_api_key", ""),
        claude_model=s.get("anthropic_model", "claude-3-5-haiku-20241022"),
        anthropic_api_key=s.get("anthropic_api_key", ""),
        compat_base_url=s.get("compat_base_url", ""),
        compat_api_key=s.get("compat_api_key", ""),
        compat_model=s.get("compat_model", ""),
        description_language=s.get("description_language", "it"),
    )
    backend = _build_backend(config)
    fallback = _get_fallback_backend(config)

    _categorizable = {"expense", "income", "card_tx", "unknown"}
    to_categorize = [t for t in tx_dicts if t["tx_type"] in _categorizable]

    n_categorized = 0
    if to_categorize:
        with get_session(engine) as _s:
            taxonomy = get_taxonomy_config(_s)
            user_rules = get_category_rules(_s)
        cat_results = categorize_batch(
            transactions=to_categorize,
            taxonomy=taxonomy,
            user_rules=user_rules,
            llm_backend=backend,
            sanitize_config=config.sanitize_config,
            fallback_backend=fallback,
            confidence_threshold=config.confidence_threshold,
            description_language=config.description_language,
            source_name="desc_rule_bulk",
        )
        cat_map = {tx["id"]: result for tx, result in zip(to_categorize, cat_results)}

        with get_session(engine) as _s:
            for tx_id in matching_ids:
                tx = _s.get(Transaction, tx_id)
                if tx is None:
                    continue
                result = cat_map.get(tx_id)
                if result and result.category:
                    tx.category = result.category
                    tx.subcategory = result.subcategory
                    tx.category_confidence = result.confidence.value
                    tx.category_source = result.source.value
                    tx.to_review = result.to_review
                    n_categorized += 1
            _s.commit()

    return n_updated, n_categorized


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

    # ── Rielabora transazioni in coda di revisione ─────────────────────────────
    _llm_types = ("expense", "income", "card_tx", "unknown")
    with get_session(engine) as _chk_s:
        _n_to_review = _chk_s.query(Transaction).filter(
            Transaction.to_review == True,  # noqa: E712
            Transaction.tx_type.in_(_llm_types),
        ).count()

    _rerun_label = (
        f"🔄 Rielabora con LLM ({_n_to_review} da rivedere)"
        if _n_to_review > 0
        else "🔄 Rielabora con LLM (nessuna transazione in coda di revisione)"
    )
    if st.button(_rerun_label, key="review_rerun_llm", disabled=(_n_to_review == 0)):
        with st.spinner("Rielaborazione in corso…"):
            n_cleaned, n_cat = _rerun_llm_on_review(engine)
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
            n_updated = _rerun_transfer_detection(engine)
        if n_updated:
            st.success(f"Trovate e marcate **{n_updated}** nuove transazioni come giroconto.")
        else:
            st.info("Nessuna nuova coppia giroconto trovata.")
        logger.info(f"review_page: rerun_transfers: updated={n_updated}")
        if n_updated:
            st.rerun()

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
            width="stretch",
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
            with get_session(engine) as _ps:
                _preview_matches = get_transactions_by_raw_pattern(
                    _ps, bulk_pattern, bulk_match_type
                )
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
                    with get_session(engine) as _rs:
                        _, _rule_created = create_description_rule(
                            _rs, bulk_pattern, bulk_match_type, bulk_new_desc
                        )
                        _rs.commit()

                with st.spinner("Aggiornamento descrizioni e ri-categorizzazione in corso…"):
                    n_upd, n_cat = _apply_description_rule_bulk(
                        engine, bulk_pattern, bulk_match_type, bulk_new_desc
                    )

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
    with get_session(engine) as _rs:
        _desc_rules = get_description_rules(_rs)

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
                            with st.spinner("Riapplicazione in corso…"):
                                n_upd, n_cat = _apply_description_rule_bulk(
                                    engine, _rule.raw_pattern, _rule.match_type, _rule.cleaned_description
                                )
                            st.success(f"{n_upd} aggiornate · {n_cat} ri-categorizzate.")
                            st.rerun()
                    with _btn_col2:
                        if st.button("🗑", key=f"desc_rule_del_{_rule.id}", help="Elimina regola"):
                            with get_session(engine) as _del_s:
                                delete_description_rule(_del_s, _rule.id)
                                _del_s.commit()
                            st.rerun()
