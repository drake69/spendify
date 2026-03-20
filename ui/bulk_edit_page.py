"""Bulk edit page: apply category rules, giroconto and context to similar transactions."""
from __future__ import annotations

import json

import streamlit as st

from services.review_service import ReviewService
from services.rule_service import RuleService
from services.settings_service import SettingsService
from services.transaction_service import TransactionService
from support.formatting import format_amount_display, format_date_display
from support.logging import setup_logging

logger = setup_logging()

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

    review_svc = ReviewService(engine)
    rule_svc   = RuleService(engine)
    tx_svc     = TransactionService(engine)
    cfg_svc    = SettingsService(engine)

    settings = cfg_svc.get_all()
    taxonomy = cfg_svc.get_taxonomy()

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

    txs = tx_svc.get_transactions(filters=filters, limit=500)

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
    _same_desc  = tx_svc.count_by_description(sel.description or "", sel.id) if sel.description else 0
    _same_raw   = tx_svc.count_by_raw_description(sel.raw_description or "", sel.id) if sel.raw_description else 0
    _similar_txs = tx_svc.get_similar(sel.description or "", exclude_id=sel.id)
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
        run_cleaner    = st.checkbox("🔍 Estrai controparte", value=True, key="rerun_cleaner",
                                     help="Ri-estrae il nome commerciale/controparte dalla raw_description")
    with rrc2:
        run_categorizer = st.checkbox("🏷️ Ri-categorizza",  value=True, key="rerun_cat",
                                      help="Ri-applica regole deterministiche e LLM per categoria/sottocategoria")

    _rerun_disabled = not run_cleaner and not run_categorizer

    if st.button("🔄 Avvia ri-elaborazione", key="rerun_execute", type="primary",
                 disabled=_rerun_disabled, use_container_width=False):

        # ── 1. Determine which tx IDs to re-process ───────────────────────────
        if rr_scope == _scope_opts[0]:
            tx_ids = [sel.id]
        elif rr_scope == _scope_opts[1]:
            tx_ids = [tx.id for tx in tx_svc.get_by_raw_description_value(sel.raw_description or "")]
        elif rr_scope == _scope_opts[2]:
            _similar_ids = {tx.id for tx in _similar_txs}
            _similar_ids.add(sel.id)
            tx_ids = list(_similar_ids)
        elif rr_scope == _scope_opts[3]:
            tx_ids = [tx.id for tx in tx_svc.get_to_review_batch(500)]
        else:
            tx_ids = [tx.id for tx in tx_svc.get_without_category_batch(500)]

        if not tx_ids:
            st.warning("Nessuna transazione trovata per il criterio selezionato.")
        else:
            n_rerun = len(tx_ids)
            _progress_bar = st.progress(0.0)
            _status_text  = st.empty()

            if run_cleaner:
                _status_text.info(f"⏳ Estrazione controparte per {n_rerun} transazioni…")

            def _cat_progress_cb(p: float):
                base = 0.5 if run_cleaner else 0.0
                _progress_bar.progress(base + p * (1.0 - base))
                if run_categorizer:
                    _status_text.info(f"⏳ Categorizzazione {n_rerun} transazioni… {p*100:.0f}%")

            try:
                n_desc, n_cat, n_review = review_svc.rerun_pipeline_on_txs(
                    tx_ids, run_cleaner, run_categorizer,
                    categorizer_progress_callback=_cat_progress_cb if run_categorizer else None,
                )
            except Exception as _pe:
                st.error(f"Errore durante la ri-elaborazione: {_pe}")
                logger.exception("bulk_edit: rerun pipeline error")
                st.stop()

            _progress_bar.progress(1.0)
            _status_text.empty()

            _parts = []
            if run_cleaner:
                _parts.append(f"**{n_desc}** descrizioni aggiornate")
            if run_categorizer:
                _parts.append(f"**{n_cat}** tx categorizzate ({n_review} ancora da rivedere)")
            st.success(f"✅ Ri-elaborazione completata su {n_rerun} tx — " + " · ".join(_parts) + ".")
            logger.info(
                f"bulk_edit: rerun scope={rr_scope!r} n={n_rerun} "
                f"desc={n_desc} cat={n_cat} review={n_review}"
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
            ok, new_type = tx_svc.toggle_giroconto(sel.id)
            n_extra = 0
            if ok and apply_giro_similar and sel.description:
                make_giro = new_type in ("internal_out", "internal_in")
                n_extra = tx_svc.bulk_set_giroconto_by_description(
                    sel.description, make_giro, exclude_id=sel.id
                )
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
            tx_svc.update_context(sel.id, new_ctx_value)
            n_extra = 0
            if apply_ctx_same and sel.description:
                same_txs = tx_svc.get_by_description(sel.description, sel.id)
                n_extra += tx_svc.update_context_bulk([t.id for t in same_txs], new_ctx_value)
            if apply_ctx_similar:
                similar_ids = [t.id for t in _similar_txs if t.id != sel.id]
                n_extra += tx_svc.update_context_bulk(similar_ids, new_ctx_value)
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
            ok = tx_svc.update_category(sel.id, new_cat, new_sub)
            if ok:
                rule_msg = ""
                n_similar = 0

                if save_rule and sel.description:
                    _, created = rule_svc.create_rule(
                        pattern=sel.description,
                        match_type="contains",
                        category=new_cat,
                        subcategory=new_sub,
                        priority=10,
                    )
                    rule_tag = "creata" if created else "aggiornata"
                    rule_msg = f" · Regola {rule_tag}."

                if apply_to_similar_cat and sel.description:
                    similar = tx_svc.get_by_rule_pattern(sel.description, "contains")
                    for stx in similar:
                        if stx.id != sel.id:
                            tx_svc.update_category(stx.id, new_cat, new_sub)
                            n_similar += 1
                    if n_similar:
                        rule_msg += f" · {n_similar} tx simili aggiornate."

                st.success(f"Categoria aggiornata: **{new_cat}** / {new_sub}.{rule_msg}")
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

    _del_accounts = tx_svc.get_distinct_account_labels()

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

    _del_preview = tx_svc.get_transactions(filters=_del_filters, limit=10)
    _all_del     = tx_svc.get_transactions(filters=_del_filters)
    _del_count   = len(_all_del)

    if not _del_filters:
        st.warning("⚠️ Nessun filtro impostato — imposta almeno un criterio prima di procedere.")
    else:
        if _del_count == 0:
            st.info("Nessuna transazione corrisponde ai filtri selezionati.")
        else:
            st.error(
                f"🗑️ **{_del_count} transazioni** verranno eliminate in modo permanente."
            )

            with st.expander(f"👁 Anteprima prime {min(10, _del_count)} righe"):
                import pandas as _pd
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
                st.dataframe(_pd.DataFrame(_prev_rows), use_container_width=True, hide_index=True)

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
                    n_deleted = tx_svc.delete_by_filter(_del_filters)
                    st.success(f"✅ Eliminate **{n_deleted}** transazioni.")
                    logger.info(f"bulk_edit: deleted {n_deleted} tx filters={_del_filters}")
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

    _dup_groups = tx_svc.get_cross_account_duplicates()

    if not _dup_groups:
        st.success("Nessun duplicato tra conti trovato.")
    else:
        import pandas as _pd
        from itertools import combinations

        n_groups = len(_dup_groups)
        n_extra  = sum(len(g) - 1 for g in _dup_groups)
        st.warning(
            f"Trovati **{n_groups} gruppi** di duplicati — "
            f"**{n_extra} transazioni in eccesso** (una per gruppo è quella originale)."
        )

        _all_accounts_dup = sorted({
            tx.account_label or ""
            for g in _dup_groups for tx in g
        })
        if len(_all_accounts_dup) >= 2:
            _pair_counts: dict[tuple[str, str], int] = {}
            for g in _dup_groups:
                accts = sorted({tx.account_label or "" for tx in g})
                for a, b in combinations(accts, 2):
                    _pair_counts[(a, b)] = _pair_counts.get((a, b), 0) + 1

            _pivot = _pd.DataFrame("", index=_all_accounts_dup, columns=_all_accounts_dup)
            for (a, b), cnt in _pair_counts.items():
                _pivot.loc[b, a] = cnt

            for i, row in enumerate(_all_accounts_dup):
                for j, col in enumerate(_all_accounts_dup):
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

        _dup_rows = []
        for g in _dup_groups:
            ref = g[0]
            _dup_rows.append({
                "Data":     format_date_display(ref.date, _date_fmt),
                "Raw desc": (ref.raw_description or "")[:80],
                "Importo":  float(ref.amount),
                "Conti":    ", ".join(sorted({tx.account_label or "" for tx in g})),
                "N copie":  len(g),
                "IDs":      ", ".join(tx.id[:8] for tx in g),
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
                deleted = tx_svc.delete_duplicate_groups(_dup_groups)
                st.success(f"✅ Eliminati **{deleted}** duplicati.")
                logger.info(f"bulk_edit: deleted {deleted} cross-account duplicates")
                if "dup_confirm" in st.session_state:
                    del st.session_state["dup_confirm"]
                st.rerun()
