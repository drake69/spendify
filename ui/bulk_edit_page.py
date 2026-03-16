"""Bulk edit page: apply category rules, giroconto and context to similar transactions."""
from __future__ import annotations

import json

import streamlit as st

from core.categorizer import categorize_batch
from core.description_cleaner import clean_descriptions_batch
from core.llm_backends import LLMBackend
from core.orchestrator import ProcessingConfig, _build_backend, _get_fallback_backend
from core.sanitizer import SanitizationConfig
from db.models import Transaction, get_session
from db.repository import (
    bulk_set_giroconto_by_description,
    create_category_rule,
    delete_transactions_by_filter,
    get_all_user_settings,
    get_category_rules,
    get_cross_account_duplicates,
    get_similar_transactions,
    get_taxonomy_config,
    get_transactions,
    get_transactions_by_rule_pattern,
    toggle_transaction_giroconto,
    update_transaction_category,
    update_transaction_context,
)
from support.formatting import format_amount_display, format_date_display
from support.logging import setup_logging

logger = setup_logging()


def _build_pipeline_backend(settings: dict) -> tuple[LLMBackend, LLMBackend | None, SanitizationConfig, str]:
    """Build LLM backend + sanitize_config from user settings dict.

    Returns (backend, fallback, sanitize_config, description_language).
    """
    owner_names = [n.strip() for n in settings.get("owner_names", "").split(",") if n.strip()]
    config = ProcessingConfig(
        llm_backend=settings.get("llm_backend", "local_ollama"),
        ollama_base_url=settings.get("ollama_base_url", "http://localhost:11434"),
        ollama_model=settings.get("ollama_model", "gemma3:12b"),
        openai_model=settings.get("openai_model", "gpt-4o-mini"),
        openai_api_key=settings.get("openai_api_key", ""),
        claude_model=settings.get("anthropic_model", "claude-3-5-haiku-20241022"),
        anthropic_api_key=settings.get("anthropic_api_key", ""),
        compat_base_url=settings.get("compat_base_url", ""),
        compat_api_key=settings.get("compat_api_key", ""),
        compat_model=settings.get("compat_model", ""),
        sanitize_config=SanitizationConfig(
            owner_names=owner_names,
            description_language=settings.get("description_language", "it"),
        ),
        description_language=settings.get("description_language", "it"),
    )
    backend  = _build_backend(config)
    fallback = _get_fallback_backend(config)
    return backend, fallback, config.sanitize_config, config.description_language


_ALL_TX_TYPES = [
    "tutti", "expense", "income", "card_tx",
    "internal_out", "internal_in", "card_settlement", "unknown",
]


def render_bulk_edit_page(engine):
    st.header("✏️ Modifiche massive")
    st.caption(
        "Seleziona una transazione e applica categoria, contesto o stato giroconto "
        "a tutte le transazioni simili. Puoi anche salvare una regola deterministica."
    )

    with get_session(engine) as s:
        settings  = get_all_user_settings(s)
        taxonomy  = get_taxonomy_config(s)

    _date_fmt = settings.get("date_display_format", "%d/%m/%Y")
    _dec      = settings.get("amount_decimal_sep",  ",")
    _thou     = settings.get("amount_thousands_sep", ".")

    try:
        _contexts: list[str] = json.loads(
            settings.get("contexts", '["Quotidianità", "Lavoro", "Vacanza"]')
        )
    except Exception:
        _contexts = ["Quotidianità", "Lavoro", "Vacanza"]

    _expense_cats = sorted(taxonomy.all_expense_categories)
    _income_cats  = sorted(taxonomy.all_income_categories)

    # ── Transaction selector ───────────────────────────────────────────────────
    st.subheader("1 · Scegli la transazione di riferimento")

    fc1, fc2, fc3 = st.columns([3, 2, 2])
    with fc1:
        desc_search = st.text_input("🔍 Cerca per descrizione", key="bulk_desc_search",
                                    placeholder="digita per filtrare…")
    with fc2:
        only_review = st.checkbox("Solo da rivedere ⚠️", key="bulk_only_review")
    with fc3:
        show_all = st.checkbox("Mostra tutte (no filtro da rivedere)", key="bulk_show_all",
                               value=False)

    filters: dict = {}
    if desc_search.strip():
        filters["description"] = desc_search.strip()
    if only_review:
        filters["to_review"] = True

    with get_session(engine) as s:
        txs = get_transactions(s, filters=filters, limit=500)

    if not txs:
        st.info("Nessuna transazione trovata.")
        return

    tx_options = {
        f"{'⚠️ ' if tx.to_review else ''}"
        f"{format_date_display(tx.date, _date_fmt)} | "
        f"{(tx.description or '')[:70]} | "
        f"{format_amount_display(abs(float(tx.amount)), _dec, _thou, symbol='')}": tx
        for tx in txs
    }

    selected_label = st.selectbox(
        "Transazione di riferimento",
        list(tx_options.keys()),
        key="bulk_tx_select",
    )
    sel = tx_options[selected_label]

    is_giroconto = sel.tx_type in ("internal_out", "internal_in")
    is_expense   = float(sel.amount) < 0

    st.caption(
        f"Tipo: **{sel.tx_type}** · "
        f"Categoria: **{sel.category or '—'}** / {sel.subcategory or '—'} "
        f"(conf: {sel.category_confidence or '—'}) · "
        f"Contesto: **{sel.context or '—'}**"
    )

    # ── Similarity counts ──────────────────────────────────────────────────────
    with get_session(engine) as s:
        # exact description match (cleaned field)
        _same_desc = s.query(Transaction).filter(
            Transaction.description == sel.description,
            Transaction.id != sel.id,
        ).count() if sel.description else 0

        # raw_description match — più affidabile: identifica la stessa "origine bancaria"
        # indipendentemente da come l'LLM ha estratto la controparte
        _same_raw = s.query(Transaction).filter(
            Transaction.raw_description == sel.raw_description,
            Transaction.id != sel.id,
        ).count() if sel.raw_description else 0

        _similar_txs = get_similar_transactions(s, sel.description or "", exclude_id=sel.id)
        _similar_count = len(_similar_txs)

    if _same_desc > 0 or _similar_count > 0:
        st.info(
            f"**{_same_desc}** tx con stessa descrizione · "
            f"**{_same_raw}** con stesso testo raw · "
            f"**{_similar_count}** simili (Jaccard ≥ 35%)"
        )

    st.divider()

    # ── SEZIONE 2: Ri-elabora con l'LLM ───────────────────────────────────────
    st.subheader("2 · 🔄 Ri-elabora con l'LLM")
    st.caption(
        "Esegui nuovamente estrazione controparte e/o categorizzazione su una selezione di transazioni. "
        "Il backend LLM configurato nelle Impostazioni verrà usato."
    )

    _scope_opts = [
        f"Solo la transazione selezionata (1)",
        f"Stessa raw description ({_same_raw + 1} tx)",
        f"Simili Jaccard ≥ 35% ({_similar_count + 1} tx)",
        "Tutte da rivedere ⚠️",
        "Tutte senza categoria",
    ]
    rr_scope = st.radio(
        "Transazioni da ri-elaborare",
        _scope_opts,
        key="rerun_scope",
        horizontal=True,
    )

    rrc1, rrc2 = st.columns(2)
    with rrc1:
        run_cleaner    = st.checkbox("🔍 Estrai controparte", value=True,  key="rerun_cleaner",
                                     help="Ri-estrae il nome commerciale/controparte dalla raw_description")
    with rrc2:
        run_categorizer = st.checkbox("🏷️ Ri-categorizza",   value=True,  key="rerun_cat",
                                      help="Ri-applica regole deterministiche e LLM per categoria/sottocategoria")

    _rerun_disabled = not run_cleaner and not run_categorizer

    if st.button("🔄 Avvia ri-elaborazione", key="rerun_execute", type="primary",
                 disabled=_rerun_disabled, use_container_width=False):

        # ── 1. Carica le tx da ri-elaborare ───────────────────────────────────
        with get_session(engine) as _s:
            if rr_scope == _scope_opts[0]:
                _rerun_txs = [_s.get(Transaction, sel.id)]
            elif rr_scope == _scope_opts[1]:
                _rerun_txs = (
                    _s.query(Transaction)
                    .filter(Transaction.raw_description == sel.raw_description)
                    .all()
                )
            elif rr_scope == _scope_opts[2]:
                # Jaccard ≥ 35%: la transazione selezionata + le simili già calcolate
                _similar_ids = {tx.id for tx in _similar_txs}
                _similar_ids.add(sel.id)
                _rerun_txs = (
                    _s.query(Transaction)
                    .filter(Transaction.id.in_(_similar_ids))
                    .all()
                )
            elif rr_scope == _scope_opts[3]:
                _rerun_txs = (
                    _s.query(Transaction)
                    .filter(Transaction.to_review.is_(True))
                    .limit(500).all()
                )
            else:  # senza categoria
                _rerun_txs = (
                    _s.query(Transaction)
                    .filter(Transaction.category.is_(None))
                    .limit(500).all()
                )
            _rerun_txs = [t for t in _rerun_txs if t is not None]

            # Converti in dicts (format atteso da cleaner e categorizer)
            _tx_dicts = [
                {
                    "id":              tx.id,
                    "description":     tx.description or "",
                    "raw_description": tx.raw_description or "",
                    "amount":          float(tx.amount or 0),
                    "doc_type":        tx.doc_type or "",
                }
                for tx in _rerun_txs
            ]

            _user_rules  = get_category_rules(_s)
            _taxonomy    = get_taxonomy_config(_s)

        if not _tx_dicts:
            st.warning("Nessuna transazione trovata per il criterio selezionato.")
        else:
            n_rerun = len(_tx_dicts)
            # ── 2. Backend LLM ────────────────────────────────────────────────
            try:
                _backend, _fallback, _san_cfg, _lang = _build_pipeline_backend(settings)
            except Exception as _be:
                st.error(f"Errore inizializzazione backend LLM: {_be}")
                st.stop()

            _progress_bar   = st.progress(0.0)
            _status_text    = st.empty()

            try:
                # ── 3a. Description cleaner ───────────────────────────────────
                if run_cleaner:
                    _status_text.info(f"⏳ Estrazione controparte per {n_rerun} transazioni…")
                    _tx_dicts = clean_descriptions_batch(
                        _tx_dicts, _backend, _fallback,
                        source_name="bulk_rerun",
                        sanitize_config=_san_cfg,
                    )
                    _progress_bar.progress(0.5 if run_categorizer else 1.0)

                # ── 3b. Categorizer ───────────────────────────────────────────
                _cat_results = None
                if run_categorizer:
                    _status_text.info(f"⏳ Categorizzazione {n_rerun} transazioni…")

                    def _prog_cb(p: float):
                        _progress_bar.progress(0.5 + p * 0.5 if run_cleaner else p)

                    _cat_results = categorize_batch(
                        _tx_dicts,
                        _taxonomy,
                        _user_rules,
                        _backend,
                        sanitize_config=_san_cfg,
                        fallback_backend=_fallback,
                        description_language=_lang,
                        source_name="bulk_rerun",
                        progress_callback=_prog_cb,
                    )

            except Exception as _pe:
                st.error(f"Errore durante la ri-elaborazione: {_pe}")
                logger.exception("bulk_edit: rerun pipeline error")
                st.stop()

            # ── 4. Applica risultati al DB ────────────────────────────────────
            _progress_bar.progress(1.0)
            _status_text.empty()

            _n_desc_updated = _n_cat_updated = _n_review = 0
            with get_session(engine) as ws:
                for i, td in enumerate(_tx_dicts):
                    tx = ws.get(Transaction, td["id"])
                    if tx is None:
                        continue
                    if run_cleaner:
                        new_desc = td.get("description", "")
                        if new_desc and new_desc != (tx.description or ""):
                            tx.description = new_desc
                            _n_desc_updated += 1
                    if run_categorizer and _cat_results:
                        r = _cat_results[i]
                        tx.category            = r.category
                        tx.subcategory         = r.subcategory
                        tx.category_confidence = r.confidence.value if hasattr(r.confidence, "value") else str(r.confidence)
                        tx.category_source     = r.source.value     if hasattr(r.source,     "value") else str(r.source)
                        tx.to_review           = r.to_review
                        _n_cat_updated += 1
                        if r.to_review:
                            _n_review += 1
                ws.commit()

            # ── 5. Riepilogo ──────────────────────────────────────────────────
            _parts = []
            if run_cleaner:
                _parts.append(f"**{_n_desc_updated}** descrizioni aggiornate")
            if run_categorizer:
                _parts.append(
                    f"**{_n_cat_updated}** tx categorizzate "
                    f"({_n_review} ancora da rivedere)"
                )
            st.success(f"✅ Ri-elaborazione completata su {n_rerun} tx — " + " · ".join(_parts) + ".")
            logger.info(
                f"bulk_edit: rerun scope={rr_scope!r} n={n_rerun} "
                f"desc={_n_desc_updated} cat={_n_cat_updated} review={_n_review}"
            )
            st.rerun()

    st.divider()

    # ── SEZIONE 3: Giroconto ───────────────────────────────────────────────────
    st.subheader("3a · Giroconto")

    giro_label = "↩️ Rimuovi da giroconti" if is_giroconto else "🔄 Segna come giroconto"

    g1, g2 = st.columns([2, 3])
    with g1:
        apply_giro_similar = st.checkbox(
            f"Applica anche alle {_same_desc} tx con stessa descrizione",
            value=(_same_desc > 0),
            disabled=(_same_desc == 0),
            key="bulk_giro_similar",
        )
    with g2:
        if st.button(giro_label, key="bulk_toggle_giro"):
            with get_session(engine) as ws:
                ok, new_type = toggle_transaction_giroconto(ws, sel.id)
                n_extra = 0
                if ok and apply_giro_similar and sel.description:
                    make_giro = new_type in ("internal_out", "internal_in")
                    n_extra = bulk_set_giroconto_by_description(
                        ws, sel.description, make_giro, exclude_id=sel.id
                    )
                ws.commit()
            if ok:
                action = "rimossa dai giroconti" if new_type in ("expense", "income") \
                         else "segnata come giroconto"
                extra = f" · {n_extra} simili aggiornate." if n_extra else ""
                st.success(f"Transazione {action} (tipo: {new_type}).{extra}")
                logger.info(f"bulk_edit: giro tx={sel.id} new_type={new_type} extra={n_extra}")
                st.rerun()
            else:
                st.error("Transazione non trovata.")

    st.divider()

    # ── SEZIONE 3b: Contesto ──────────────────────────────────────────────────
    st.subheader("3b · Contesto")

    _ctx_options = ["— nessuno —"] + _contexts
    _cur_ctx_idx = _ctx_options.index(sel.context) if sel.context in _ctx_options else 0

    cx1, cx2, cx3 = st.columns([2, 2, 2])
    with cx1:
        new_ctx_label = st.selectbox(
            "Contesto", _ctx_options, index=_cur_ctx_idx, key="bulk_ctx_select"
        )
        new_ctx_value = None if new_ctx_label == "— nessuno —" else new_ctx_label
    with cx2:
        apply_ctx_same = st.checkbox(
            f"Applica alle {_same_desc} tx con stessa descrizione",
            value=(_same_desc > 0),
            disabled=(_same_desc == 0),
            key="bulk_ctx_same",
        )
        apply_ctx_similar = st.checkbox(
            f"Applica anche alle {_similar_count} tx simili",
            value=False,
            disabled=(_similar_count == 0),
            key="bulk_ctx_similar",
        )
    with cx3:
        if st.button("💾 Applica contesto", key="bulk_ctx_save", use_container_width=True):
            with get_session(engine) as cs:
                update_transaction_context(cs, sel.id, new_ctx_value)
                n_extra = 0
                if apply_ctx_same and sel.description:
                    same_txs = cs.query(Transaction).filter(
                        Transaction.description == sel.description,
                        Transaction.id != sel.id,
                    ).all()
                    for stx in same_txs:
                        update_transaction_context(cs, stx.id, new_ctx_value)
                        n_extra += 1
                if apply_ctx_similar:
                    for stx in _similar_txs:
                        if stx.id != sel.id:
                            update_transaction_context(cs, stx.id, new_ctx_value)
                            n_extra += 1
                cs.commit()
            ctx_display = new_ctx_value or "nessuno"
            extra = f" · {n_extra} simili aggiornate." if n_extra else ""
            st.success(f"Contesto impostato: **{ctx_display}**.{extra}")
            logger.info(f"bulk_edit: ctx tx={sel.id} ctx={ctx_display} extra={n_extra}")
            st.rerun()

    st.divider()

    # ── SEZIONE 3c: Categoria ─────────────────────────────────────────────────
    if not is_giroconto:
        st.subheader("3c · Categoria")

        all_categories = _expense_cats if is_expense else _income_cats

        cat_idx = all_categories.index(sel.category) \
            if sel.category in all_categories else 0

        ca1, ca2 = st.columns(2)
        with ca1:
            new_cat = st.selectbox(
                "Nuova categoria", all_categories, index=cat_idx, key="bulk_cat_select"
            )
        with ca2:
            subs = taxonomy.valid_subcategories(new_cat)
            if subs:
                sub_idx = subs.index(sel.subcategory) \
                    if (sel.subcategory in subs and sel.category == new_cat) else 0
                new_sub = st.selectbox(
                    "Nuova sottocategoria", subs, index=sub_idx,
                    key=f"bulk_sub_select_{new_cat}"
                )
            else:
                new_sub = st.text_input(
                    "Nuova sottocategoria",
                    value=sel.subcategory or "",
                    key=f"bulk_sub_text_{new_cat}"
                )

        op1, op2 = st.columns(2)
        with op1:
            save_rule = st.checkbox(
                "💡 Salva come regola deterministica",
                value=True,
                key="bulk_save_rule",
                help="Crea una regola 'contains' che applica questa categoria "
                     "a tutte le transazioni con descrizione simile in futuro.",
            )
        with op2:
            apply_to_similar_cat = st.checkbox(
                f"Applica subito alle {_same_desc} tx con stessa descrizione",
                value=(_same_desc > 0),
                disabled=(_same_desc == 0),
                key="bulk_cat_similar",
            )

        if st.button("💾 Applica categoria", type="primary", key="bulk_cat_save"):
            with get_session(engine) as ws:
                ok = update_transaction_category(ws, sel.id, new_cat, new_sub)
                if ok:
                    rule_msg = ""
                    n_similar = 0

                    if save_rule and sel.description:
                        _, created = create_category_rule(
                            session=ws,
                            pattern=sel.description,
                            match_type="contains",
                            category=new_cat,
                            subcategory=new_sub,
                            priority=10,
                        )
                        rule_tag = "creata" if created else "aggiornata"
                        rule_msg = f" · Regola {rule_tag}."

                    if apply_to_similar_cat and sel.description:
                        similar = get_transactions_by_rule_pattern(
                            ws, sel.description, "contains"
                        )
                        for stx in similar:
                            if stx.id != sel.id:
                                update_transaction_category(ws, stx.id, new_cat, new_sub)
                                n_similar += 1
                        if n_similar:
                            rule_msg += f" · {n_similar} tx simili aggiornate."

                    ws.commit()
                    st.success(
                        f"Categoria aggiornata: **{new_cat}** / {new_sub}.{rule_msg}"
                    )
                    logger.info(
                        f"bulk_edit: cat tx={sel.id} cat={new_cat}/{new_sub} "
                        f"rule={save_rule} similar={n_similar}"
                    )
                    st.rerun()
                else:
                    st.error("Transazione non trovata.")
    else:
        st.info("Transazione marcata come giroconto — la correzione categoria non è applicabile.")

    st.divider()

    # ── SEZIONE 4: Eliminazione massiva da filtro ──────────────────────────────
    st.subheader("4 · Eliminazione massiva da filtro")
    st.caption(
        "Definisci i criteri di selezione, controlla quante transazioni verranno "
        "eliminate e conferma. **L'operazione è irreversibile.**"
    )

    with get_session(engine) as s:
        _del_accounts = sorted({
            r[0] for r in s.query(Transaction.account_label).distinct().all() if r[0]
        })

    # — Filtri eliminazione —
    de1, de2, de3, de4 = st.columns(4)
    with de1:
        del_date_from = st.date_input("Da", key="del_date_from", value=None)
    with de2:
        del_date_to   = st.date_input("A",  key="del_date_to",   value=None)
    with de3:
        del_account = st.selectbox(
            "Conto", ["tutti i conti"] + _del_accounts, key="del_account"
        )
    with de4:
        del_tx_type = st.selectbox("Tipo", _ALL_TX_TYPES, key="del_type")

    de5, de6 = st.columns([3, 2])
    with de5:
        del_desc = st.text_input(
            "🔍 Descrizione (contiene)", key="del_desc",
            placeholder="filtra per testo in descrizione o raw…"
        )
    with de6:
        del_cat = st.selectbox(
            "Categoria",
            ["tutte"] + sorted(taxonomy.all_expense_categories) + sorted(taxonomy.all_income_categories),
            key="del_cat",
        )

    # — Costruisci filtri e conta —
    _del_filters: dict = {}
    if del_date_from:
        _del_filters["date_from"] = del_date_from.isoformat()
    if del_date_to:
        _del_filters["date_to"] = del_date_to.isoformat()
    if del_account != "tutti i conti":
        _del_filters["account_label"] = del_account
    if del_tx_type != "tutti":
        _del_filters["tx_type"] = del_tx_type
    if del_desc.strip():
        _del_filters["description"] = del_desc.strip()
    if del_cat != "tutte":
        _del_filters["category"] = del_cat

    with get_session(engine) as s:
        _del_preview = get_transactions(s, filters=_del_filters, limit=10)
        _del_count   = len(get_transactions(s, filters=_del_filters))

    if not _del_filters:
        st.warning("⚠️ Nessun filtro impostato — imposta almeno un criterio prima di procedere.")
    else:
        if _del_count == 0:
            st.info("Nessuna transazione corrisponde ai filtri selezionati.")
        else:
            st.error(
                f"🗑️ **{_del_count} transazioni** verranno eliminate in modo permanente."
            )

            # Anteprima (max 10 righe)
            with st.expander(f"👁 Anteprima prime {min(10, _del_count)} righe"):
                _prev_rows = [
                    {
                        "Data":        format_date_display(tx.date, _date_fmt),
                        "Descrizione": (tx.description or "")[:70],
                        "Importo":     float(tx.amount),
                        "Conto":       tx.account_label or "",
                        "Tipo":        tx.tx_type or "",
                        "Categoria":   tx.category or "",
                    }
                    for tx in _del_preview
                ]
                import pandas as _pd
                st.dataframe(_pd.DataFrame(_prev_rows), use_container_width=True, hide_index=True)

            # — Conferma —
            st.markdown(
                "Per confermare, digita esattamente **`ELIMINA`** nel campo qui sotto "
                "e poi premi il pulsante."
            )
            cc1, cc2 = st.columns([2, 1])
            with cc1:
                confirm_text = st.text_input(
                    "Conferma eliminazione", key="del_confirm",
                    placeholder="digita ELIMINA per abilitare il pulsante"
                )
            with cc2:
                del_enabled = confirm_text.strip() == "ELIMINA"
                if st.button(
                    f"🗑️ Elimina {_del_count} transazioni",
                    type="primary",
                    disabled=not del_enabled,
                    key="del_execute",
                    use_container_width=True,
                ):
                    with get_session(engine) as ws:
                        n_deleted = delete_transactions_by_filter(ws, _del_filters)
                        ws.commit()
                    st.success(f"✅ Eliminate **{n_deleted}** transazioni.")
                    logger.info(f"bulk_edit: deleted {n_deleted} tx filters={_del_filters}")
                    # reset conferma e rerun
                    if "del_confirm" in st.session_state:
                        del st.session_state["del_confirm"]
                    st.rerun()

    st.divider()

    # ── SEZIONE 5: Duplicati tra conti ─────────────────────────────────────────
    st.subheader("5 · Duplicati tra conti")
    st.caption(
        "Transazioni con stessa **data**, **descrizione raw** e **importo** "
        "presenti su più conti diversi. Tipicamente causati da importazioni "
        "sovrapposte (es. estratto conto + file carta)."
    )

    with get_session(engine) as s:
        _dup_groups = get_cross_account_duplicates(s)

    if not _dup_groups:
        st.success("Nessun duplicato tra conti trovato.")
    else:
        n_groups = len(_dup_groups)
        n_extra  = sum(len(g) - 1 for g in _dup_groups)
        st.warning(
            f"Trovati **{n_groups} gruppi** di duplicati — "
            f"**{n_extra} transazioni in eccesso** (una per gruppo è quella originale)."
        )

        import pandas as _pd
        from itertools import combinations

        # ── Pivot: conti × conti (triangolo inferiore) ────────────────────────
        _all_accounts = sorted({
            tx.account_label or ""
            for g in _dup_groups for tx in g
        })
        if len(_all_accounts) >= 2:
            # Count duplicate groups per account pair (lower triangle only)
            _pair_counts: dict[tuple[str, str], int] = {}
            for g in _dup_groups:
                accts = sorted({tx.account_label or "" for tx in g})
                for a, b in combinations(accts, 2):
                    _pair_counts[(a, b)] = _pair_counts.get((a, b), 0) + 1

            # Build lower-triangle DataFrame
            _pivot = _pd.DataFrame("", index=_all_accounts, columns=_all_accounts)
            for (a, b), cnt in _pair_counts.items():
                # a < b (sorted), so b is the row and a is the column
                _pivot.loc[b, a] = cnt

            # Hide upper triangle and diagonal
            for i, row in enumerate(_all_accounts):
                for j, col in enumerate(_all_accounts):
                    if j >= i:
                        _pivot.loc[row, col] = ""

            st.dataframe(
                _pivot.style.map(
                    lambda v: "background-color: #ffeeba; font-weight: bold"
                    if isinstance(v, int) or (isinstance(v, str) and v.isdigit() and int(v) > 0)
                    else ""
                ),
                use_container_width=True,
            )

        # Tabella riassuntiva
        _dup_rows = []
        for g in _dup_groups:
            ref = g[0]
            _dup_rows.append({
                "Data":         format_date_display(ref.date, _date_fmt),
                "Raw desc":     (ref.raw_description or "")[:80],
                "Importo":      float(ref.amount),
                "Conti":        ", ".join(sorted({tx.account_label or "" for tx in g})),
                "N copie":      len(g),
                "IDs":          ", ".join(tx.id[:8] for tx in g),
            })
        st.dataframe(
            _pd.DataFrame(_dup_rows),
            use_container_width=True,
            hide_index=True,
        )

        st.markdown(
            "Il pulsante elimina **le copie in eccesso** mantenendo la prima "
            "transazione per ogni gruppo (in ordine di data importazione)."
        )
        dc1, dc2 = st.columns([2, 1])
        with dc1:
            dup_confirm = st.text_input(
                "Conferma eliminazione duplicati",
                key="dup_confirm",
                placeholder="digita ELIMINA per abilitare il pulsante",
            )
        with dc2:
            dup_enabled = dup_confirm.strip() == "ELIMINA"
            if st.button(
                f"🗑️ Elimina {n_extra} duplicati",
                type="primary",
                disabled=not dup_enabled,
                key="dup_execute",
                use_container_width=True,
            ):
                deleted = 0
                with get_session(engine) as ws:
                    for g in _dup_groups:
                        # Keep g[0], delete the rest
                        for tx in g[1:]:
                            from db.models import ReconciliationLink, InternalTransferLink
                            ws.query(ReconciliationLink).filter(
                                (ReconciliationLink.settlement_id == tx.id) |
                                (ReconciliationLink.detail_id == tx.id)
                            ).delete(synchronize_session=False)
                            ws.query(InternalTransferLink).filter(
                                (InternalTransferLink.out_id == tx.id) |
                                (InternalTransferLink.in_id == tx.id)
                            ).delete(synchronize_session=False)
                            ws.delete(tx)
                            deleted += 1
                    ws.commit()
                st.success(f"✅ Eliminati **{deleted}** duplicati.")
                logger.info(f"bulk_edit: deleted {deleted} cross-account duplicates")
                if "dup_confirm" in st.session_state:
                    del st.session_state["dup_confirm"]
                st.rerun()
