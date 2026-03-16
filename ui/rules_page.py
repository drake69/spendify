"""Rules management page — view, edit, delete and create category rules."""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from db.models import get_session
from db.repository import (
    apply_all_rules_to_all_transactions,
    create_category_rule,
    delete_category_rule,
    get_all_user_settings,
    get_category_rules,
    get_taxonomy_config,
    get_transactions_by_rule_pattern,
    update_category_rule,
    update_transaction_category,
    update_transaction_context,
)
from support.logging import setup_logging

logger = setup_logging()
_MATCH_TYPES = ["contains", "exact", "regex"]
_NO_CONTEXT   = "— nessuno —"   # valore UI per "non impostare il contesto"


def _subcategory_widget(taxonomy, category: str,
                        current: str, base_key: str) -> str:
    subs = taxonomy.valid_subcategories(category)
    # Embed category in key so Streamlit resets widget when category changes
    key = f"{base_key}__{category}"
    if not subs:
        return st.text_input("Sottocategoria", value=current, key=key)
    idx = subs.index(current) if current in subs else 0
    return st.selectbox("Sottocategoria", subs, index=idx, key=key)


def render_rules_page(engine):
    st.header("📏 Regole di Categorizzazione")
    st.caption(
        "Le regole vengono applicate prima dell'LLM. "
        "Modificando o eliminando una regola puoi anche aggiornare "
        "le transazioni già categorizzate da quella regola."
    )

    with get_session(engine) as session:
        taxonomy  = get_taxonomy_config(session)
        settings  = get_all_user_settings(session)
        all_categories = taxonomy.all_expense_categories + taxonomy.all_income_categories
        rules = get_category_rules(session)

    try:
        _contexts: list[str] = json.loads(
            settings.get("contexts", '["Quotidianità", "Lavoro", "Vacanza"]')
        )
    except Exception:
        _contexts = ["Quotidianità", "Lavoro", "Vacanza"]
    _ctx_options = [_NO_CONTEXT] + _contexts

    if not rules:
        st.info("Nessuna regola salvata. Crea le prime regole qui sotto o dalla pagina Review.")
    else:
        # ── Tabella regole ──────────────────────────────────────────────────
        st.subheader(f"Regole attive ({len(rules)})")
        table_data = [
            {
                "ID": r.id,
                "Pattern": r.pattern,
                "Tipo match": r.match_type,
                "Categoria": r.category,
                "Sottocategoria": r.subcategory or "",
                "Contesto": r.context or "",
                "Priorità": r.priority,
            }
            for r in sorted(rules, key=lambda x: x.pattern.casefold())
        ]
        df_rules = pd.DataFrame(table_data)

        # ── Paginazione ──────────────────────────────────────────────────
        _PAGE_SIZE = 20
        _n_pages   = max(1, (len(df_rules) + _PAGE_SIZE - 1) // _PAGE_SIZE)
        _pg_col, _ = st.columns([2, 5])
        with _pg_col:
            _page = st.number_input(
                f"Pagina (1–{_n_pages})", min_value=1, max_value=_n_pages,
                value=1, step=1, key="rules_page_num",
            )
        _start = (_page - 1) * _PAGE_SIZE
        _end   = _start + _PAGE_SIZE
        st.dataframe(
            df_rules.iloc[_start:_end].set_index("ID"),
            use_container_width=True,
        )
        st.caption(f"Righe {_start + 1}–{min(_end, len(df_rules))} di {len(df_rules)}")

        # ── Esegui tutte le regole ───────────────────────────────────────
        st.divider()
        st.subheader("▶️ Esegui tutte le regole")
        st.caption(
            "Applica tutte le regole attive a **tutte** le transazioni nel registro. "
            "Per ogni transazione la prima regola corrispondente (in ordine di priorità) vince. "
            "Le transazioni senza corrispondenza rimangono invariate."
        )
        rc1, rc2 = st.columns([3, 2])
        with rc1:
            _run_confirm = st.checkbox(
                "Confermo: voglio sovrascrivere le categorie di tutte le transazioni che corrispondono a una regola",
                key="run_all_rules_confirm",
            )
        with rc2:
            if st.button(
                "▶️ Esegui tutte le regole",
                type="primary",
                disabled=not _run_confirm,
                key="run_all_rules_btn",
                use_container_width=True,
            ):
                with get_session(engine) as _rs:
                    _n_matched, _n_cleared = apply_all_rules_to_all_transactions(
                        _rs, rules
                    )
                    _rs.commit()
                st.success(
                    f"✅ Completato — **{_n_matched}** transazioni aggiornate "
                    f"({_n_cleared} marcate come revisionate)."
                )
                logger.info(
                    f"rules_page: apply_all_rules matched={_n_matched} cleared={_n_cleared}"
                )
                st.rerun()

        st.divider()

        # ── Modifica / Elimina regola ────────────────────────────────────
        st.subheader("Modifica o elimina una regola")

        rule_opts = {f"[{r.id}] {r.pattern[:60]}  →  {r.category} / {r.subcategory or '—'}": r
                     for r in sorted(rules, key=lambda x: x.pattern.casefold())}
        selected_label = st.selectbox("Seleziona regola", list(rule_opts.keys()),
                                      key="rules_select")
        sel_rule = rule_opts[selected_label]

        col_edit, col_del = st.columns([3, 1])

        with col_edit:
            with st.expander("✏️ Modifica regola", expanded=False):
                # Keys embed sel_rule.id so widgets reset when a different rule is selected
                rid = sel_rule.id
                new_pattern = st.text_input("Pattern", value=sel_rule.pattern,
                                            key=f"rule_edit_pattern_{rid}")
                new_match = st.selectbox("Tipo match", _MATCH_TYPES,
                                         index=_MATCH_TYPES.index(sel_rule.match_type),
                                         key=f"rule_edit_match_{rid}")
                all_idx = all_categories.index(sel_rule.category) if sel_rule.category in all_categories else 0
                new_cat = st.selectbox("Categoria", all_categories, index=all_idx,
                                       key=f"rule_edit_cat_{rid}")
                new_sub = _subcategory_widget(taxonomy, new_cat,
                                              sel_rule.subcategory or "", f"rule_edit_sub_{rid}")
                _cur_ctx = sel_rule.context or _NO_CONTEXT
                _ctx_idx = _ctx_options.index(_cur_ctx) if _cur_ctx in _ctx_options else 0
                new_ctx_raw = st.selectbox(
                    "Contesto (opzionale)", _ctx_options, index=_ctx_idx,
                    key=f"rule_edit_ctx_{rid}",
                    help="Se impostato, viene assegnato alla transazione. Lascia '— nessuno —' per non modificarlo.",
                )
                new_ctx = None if new_ctx_raw == _NO_CONTEXT else new_ctx_raw
                new_prio = st.number_input("Priorità", value=int(sel_rule.priority or 0),
                                           min_value=0, max_value=100, step=1,
                                           key=f"rule_edit_prio_{rid}")

                # How many transactions would be affected
                with get_session(engine) as session:
                    affected = get_transactions_by_rule_pattern(
                        session, sel_rule.pattern, sel_rule.match_type)
                n_affected = len(affected)
                also_fix_txs = st.checkbox(
                    f"Aggiorna anche le {n_affected} transazioni già categorizzate da questa regola",
                    value=(n_affected > 0),
                    disabled=(n_affected == 0),
                    key=f"rule_edit_fix_txs_{rid}",
                )

                if st.button("💾 Salva modifiche", type="primary", key=f"rule_edit_save_{rid}"):
                    with get_session(engine) as session:
                        ok = update_category_rule(
                            session, sel_rule.id,
                            pattern=new_pattern,
                            match_type=new_match,
                            category=new_cat,
                            subcategory=new_sub,
                            context=new_ctx,
                            priority=new_prio,
                        )
                        if ok and also_fix_txs and n_affected > 0:
                            for tx in affected:
                                update_transaction_category(session, tx.id, new_cat, new_sub)
                                if new_ctx:
                                    update_transaction_context(session, tx.id, new_ctx)
                        session.commit()
                    if ok:
                        msg = "Regola aggiornata."
                        if also_fix_txs and n_affected > 0:
                            ctx_note = f" · contesto '{new_ctx}'" if new_ctx else ""
                            msg += f" {n_affected} transazioni ricalcolate{ctx_note}."
                        st.success(msg)
                        logger.info(f"rules_page: updated rule {sel_rule.id}")
                        st.rerun()
                    else:
                        st.error("Regola non trovata.")

        with col_del:
            st.write("")
            st.write("")

            # Count affected transactions before confirming delete
            with get_session(engine) as session:
                del_affected = get_transactions_by_rule_pattern(
                    session, sel_rule.pattern, sel_rule.match_type)
            n_del = len(del_affected)

            if n_del > 0:
                st.warning(f"⚠️ Questa regola ha categorizzato {n_del} transazioni.")

            confirm_del = st.checkbox("Conferma eliminazione", key=f"rule_del_confirm_{rid}")
            if st.button("🗑️ Elimina", type="secondary", key=f"rule_del_btn_{rid}",
                         disabled=not confirm_del):
                with get_session(engine) as session:
                    ok = delete_category_rule(session, sel_rule.id)
                    session.commit()
                if ok:
                    st.success("Regola eliminata.")
                    logger.info(f"rules_page: deleted rule {sel_rule.id}")
                    st.rerun()
                else:
                    st.error("Regola non trovata.")

    # ── Nuova regola manuale ─────────────────────────────────────────────────
    st.divider()
    st.subheader("➕ Nuova regola")

    nr_pattern = st.text_input("Pattern (testo da cercare nella descrizione)",
                               placeholder="es. ESSELUNGA, Netflix, stipendio…",
                               key="new_rule_pattern")
    nr_match = st.selectbox("Tipo match", _MATCH_TYPES, key="new_rule_match")
    nr_cat = st.selectbox("Categoria", all_categories, key="new_rule_cat")
    nr_subs = taxonomy.valid_subcategories(nr_cat)
    # Key includes nr_cat so subcategory widget resets when category changes
    if nr_subs:
        nr_sub = st.selectbox("Sottocategoria", nr_subs, key=f"new_rule_sub__{nr_cat}")
    else:
        nr_sub = st.text_input("Sottocategoria", key=f"new_rule_sub__{nr_cat}")
    nr_ctx_raw = st.selectbox(
        "Contesto (opzionale)", _ctx_options, index=0, key="new_rule_ctx",
        help="Se impostato, viene assegnato alla transazione. Lascia '— nessuno —' per non modificarlo.",
    )
    nr_ctx = None if nr_ctx_raw == _NO_CONTEXT else nr_ctx_raw
    nr_prio = st.number_input("Priorità", value=10, min_value=0, max_value=100, step=1,
                              key="new_rule_prio")

    if st.button("💾 Crea regola", type="primary", key="new_rule_submit"):
        if not nr_pattern.strip():
            st.error("Il pattern non può essere vuoto.")
        else:
            with get_session(engine) as session:
                _, created = create_category_rule(
                    session=session,
                    pattern=nr_pattern.strip(),
                    match_type=nr_match,
                    category=nr_cat,
                    subcategory=nr_sub,
                    context=nr_ctx,
                    priority=nr_prio,
                )
                session.commit()
            ctx_label = f" · contesto: {nr_ctx}" if nr_ctx else ""
            if created:
                st.success(f"Regola creata: '{nr_pattern}' → {nr_cat} / {nr_sub}{ctx_label}")
                logger.info(f"rules_page: created rule pattern={nr_pattern!r} cat={nr_cat!r} ctx={nr_ctx!r}")
            else:
                st.warning(f"Regola esistente aggiornata: '{nr_pattern}' → {nr_cat} / {nr_sub}{ctx_label}")
                logger.info(f"rules_page: updated existing rule pattern={nr_pattern!r} cat={nr_cat!r} ctx={nr_ctx!r}")
            st.rerun()
