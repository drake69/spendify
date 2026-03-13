"""Rules management page — view, edit, delete and create category rules."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from db.models import get_session
from db.repository import (
    create_category_rule,
    delete_category_rule,
    get_category_rules,
    get_taxonomy_config,
    get_transactions_by_rule_pattern,
    update_category_rule,
    update_transaction_category,
)
from support.logging import setup_logging

logger = setup_logging()
_MATCH_TYPES = ["contains", "exact", "regex"]


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
        taxonomy = get_taxonomy_config(session)
        all_categories = taxonomy.all_expense_categories + taxonomy.all_income_categories
        rules = get_category_rules(session)

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
                "Priorità": r.priority,
            }
            for r in sorted(rules, key=lambda x: -x.priority)
        ]
        df_rules = pd.DataFrame(table_data)
        st.dataframe(df_rules.set_index("ID"), use_container_width=True)

        st.divider()

        # ── Modifica / Elimina regola ────────────────────────────────────
        st.subheader("Modifica o elimina una regola")

        rule_opts = {f"[{r.id}] {r.pattern[:60]}  →  {r.category} / {r.subcategory or '—'}": r
                     for r in sorted(rules, key=lambda x: -x.priority)}
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
                            priority=new_prio,
                        )
                        if ok and also_fix_txs and n_affected > 0:
                            for tx in affected:
                                update_transaction_category(session, tx.id, new_cat, new_sub)
                        session.commit()
                    if ok:
                        msg = f"Regola aggiornata."
                        if also_fix_txs and n_affected > 0:
                            msg += f" {n_affected} transazioni ricalcolate."
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
                    priority=nr_prio,
                )
                session.commit()
            if created:
                st.success(f"Regola creata: '{nr_pattern}' → {nr_cat} / {nr_sub}")
                logger.info(f"rules_page: created rule pattern={nr_pattern!r} cat={nr_cat!r}")
            else:
                st.warning(f"Regola esistente aggiornata: '{nr_pattern}' → {nr_cat} / {nr_sub}")
                logger.info(f"rules_page: updated existing rule pattern={nr_pattern!r} cat={nr_cat!r}")
            st.rerun()
