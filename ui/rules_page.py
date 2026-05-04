"""Rules management page — view, edit, delete and create category rules."""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from services.rule_service import RuleService
from services.settings_service import SettingsService
from services.transaction_service import TransactionService
from support.logging import setup_logging
from ui.i18n import t

logger = setup_logging()


def _match_labels() -> dict[str, str]:
    return {
        t("rules.match_contains"): "contains",
        t("rules.match_exact"): "exact",
        t("rules.match_regex"): "regex",
    }


def _label_from_type() -> dict[str, str]:
    return {v: k for k, v in _match_labels().items()}


_MATCH_TYPES = ["contains", "exact", "regex"]
_NO_CONTEXT   = "— nessuno —"


def _subcategory_widget(taxonomy, category: str, current: str, base_key: str) -> str:
    subs = taxonomy.valid_subcategories(category)
    key = f"{base_key}__{category}"
    if not subs:
        return st.text_input(t("rules.subcategory"), value=current, key=key)
    idx = subs.index(current) if current in subs else 0
    return st.selectbox(t("rules.subcategory"), subs, index=idx, key=key)


def render_rules_page(engine):
    st.header(t("rules.title"))
    st.caption(t("rules.caption"))

    rule_svc = RuleService(engine)
    cfg_svc  = SettingsService(engine)
    tx_svc   = TransactionService(engine)

    taxonomy       = cfg_svc.get_taxonomy()
    settings       = cfg_svc.get_all()
    all_categories = taxonomy.all_expense_categories + taxonomy.all_income_categories
    rules          = rule_svc.get_rules()

    try:
        _contexts: list[str] = json.loads(
            settings.get("contexts", '["Quotidianità", "Lavoro", "Vacanza"]')
        )
    except Exception:
        _contexts = ["Quotidianità", "Lavoro", "Vacanza"]
    _ctx_options = [_NO_CONTEXT] + _contexts

    match_labels = _match_labels()
    label_from_type = _label_from_type()

    if not rules:
        st.info(t("rules.no_rules"))
    else:
        # ── Tabella regole ──────────────────────────────────────────────────
        st.subheader(t("rules.active_rules", n=len(rules)))
        table_data = [
            {
                "ID": r.id,
                "Pattern": r.pattern,
                t("rules.col_type"): label_from_type.get(r.match_type, r.match_type),
                t("rules.col_category"): r.category,
                t("rules.col_subcategory"): r.subcategory or "",
                t("rules.col_context"): r.context or "",
                t("rules.col_priority"): r.priority,
            }
            for r in sorted(rules, key=lambda x: x.pattern.casefold())
        ]
        df_rules = pd.DataFrame(table_data)

        # ── Ordinamento esplicito ────────────────────────────────────
        _sort_cols = ["Pattern", t("rules.col_category"), t("rules.col_priority"),
                      t("rules.col_context"), t("rules.col_subcategory")]
        _sc1, _sc2, _ = st.columns([2, 1, 4])
        with _sc1:
            _sort_col = st.selectbox(t("rules.sort_by"), _sort_cols, key="rules_sort_col")
        with _sc2:
            _sort_asc = st.toggle(t("rules.sort_asc"), value=True, key="rules_sort_asc")
        df_rules = df_rules.sort_values(
            _sort_col, ascending=_sort_asc,
            key=lambda s: s.str.casefold() if s.dtype == object else s,
        ).reset_index(drop=True)

        # ── Paginazione ──────────────────────────────────────────────
        _PAGE_SIZE = 20
        _n_pages   = max(1, (len(df_rules) + _PAGE_SIZE - 1) // _PAGE_SIZE)
        _pg_col, _ = st.columns([2, 5])
        with _pg_col:
            _page = st.number_input(
                t("rules.page_label", max=_n_pages), min_value=1, max_value=_n_pages,
                value=1, step=1, key="rules_page_num",
            )
        _start = (_page - 1) * _PAGE_SIZE
        _end   = _start + _PAGE_SIZE
        st.dataframe(
            df_rules.iloc[_start:_end].set_index("ID"),
            use_container_width=True,
        )
        st.caption(t("rules.rows_info", start=_start + 1, end=min(_end, len(df_rules)), total=len(df_rules)))

        # ── Esegui tutte le regole ───────────────────────────────────────
        st.divider()
        st.subheader(t("rules.run_all_title"))
        st.caption(t("rules.run_all_caption"))
        rc1, rc2 = st.columns([3, 2])
        with rc1:
            _run_confirm = st.checkbox(
                t("rules.run_all_confirm"),
                key="run_all_rules_confirm",
            )
        with rc2:
            if st.button(
                t("rules.run_all_btn"),
                type="primary",
                disabled=not _run_confirm,
                key="run_all_rules_btn",
                use_container_width=True,
            ):
                _n_matched, _n_cleared = rule_svc.apply_to_all()
                st.success(t("rules.run_all_done", matched=_n_matched, cleared=_n_cleared))
                logger.info(
                    f"rules_page: apply_all_rules matched={_n_matched} cleared={_n_cleared}"
                )
                st.rerun()

        st.divider()

        # ── Modifica / Elimina regola ────────────────────────────────────
        st.subheader(t("rules.edit_delete_title"))

        rule_opts = {f"[{r.id}] {r.pattern[:60]}  →  {r.category} / {r.subcategory or '—'}": r
                     for r in sorted(rules, key=lambda x: x.pattern.casefold())}
        selected_label = st.selectbox(t("rules.select_rule"), list(rule_opts.keys()),
                                      key="rules_select")
        sel_rule = rule_opts[selected_label]

        col_edit, col_del = st.columns([3, 1])

        with col_edit:
            with st.expander(t("rules.edit_expander"), expanded=False):
                rid = sel_rule.id
                new_pattern = st.text_input("Pattern", value=sel_rule.pattern,
                                            key=f"rule_edit_pattern_{rid}")
                _edit_label = label_from_type.get(sel_rule.match_type, list(match_labels.keys())[0])
                _edit_labels = list(match_labels.keys())
                new_match_label = st.selectbox(t("rules.match_type"), _edit_labels,
                                         index=_edit_labels.index(_edit_label),
                                         key=f"rule_edit_match_{rid}")
                new_match = match_labels[new_match_label]
                all_idx = all_categories.index(sel_rule.category) if sel_rule.category in all_categories else 0
                new_cat = st.selectbox(t("rules.category"), all_categories, index=all_idx,
                                       key=f"rule_edit_cat_{rid}")
                new_sub = _subcategory_widget(taxonomy, new_cat,
                                              sel_rule.subcategory or "", f"rule_edit_sub_{rid}")
                _cur_ctx = sel_rule.context or _NO_CONTEXT
                _ctx_idx = _ctx_options.index(_cur_ctx) if _cur_ctx in _ctx_options else 0
                new_ctx_raw = st.selectbox(
                    t("rules.context_optional"), _ctx_options, index=_ctx_idx,
                    key=f"rule_edit_ctx_{rid}",
                    help=t("rules.context_help"),
                )
                new_ctx = None if new_ctx_raw == _NO_CONTEXT else new_ctx_raw
                new_prio = st.number_input(t("rules.priority"), value=int(sel_rule.priority or 0),
                                           min_value=0, max_value=100, step=1,
                                           key=f"rule_edit_prio_{rid}")

                affected = tx_svc.get_by_rule_pattern(sel_rule.pattern, sel_rule.match_type)
                n_affected = len(affected)
                also_fix_txs = st.checkbox(
                    t("rules.also_update_txs", n=n_affected),
                    value=(n_affected > 0),
                    disabled=(n_affected == 0),
                    key=f"rule_edit_fix_txs_{rid}",
                )

                if st.button(t("rules.save_changes"), type="primary", key=f"rule_edit_save_{rid}"):
                    ok = rule_svc.update_rule(
                        sel_rule.id,
                        pattern=new_pattern,
                        match_type=new_match,
                        category=new_cat,
                        subcategory=new_sub,
                        context=new_ctx,
                        priority=new_prio,
                    )
                    if ok and also_fix_txs and n_affected > 0:
                        for tx in affected:
                            tx_svc.update_category(tx.id, new_cat, new_sub)
                            if new_ctx:
                                tx_svc.update_context(tx.id, new_ctx)
                    if ok:
                        msg = t("rules.rule_updated")
                        if also_fix_txs and n_affected > 0:
                            ctx_note = f" · {t('rules.col_context').lower()} '{new_ctx}'" if new_ctx else ""
                            msg += " " + t("rules.txs_recalculated", n=n_affected) + ctx_note + "."
                        st.success(msg)
                        logger.info(f"rules_page: updated rule {sel_rule.id}")
                        st.rerun()
                    else:
                        st.error(t("rules.rule_not_found"))

        with col_del:
            st.write("")
            st.write("")
            del_affected = tx_svc.get_by_rule_pattern(sel_rule.pattern, sel_rule.match_type)
            n_del = len(del_affected)

            if n_del > 0:
                st.warning(t("rules.del_warning", n=n_del))

            confirm_del = st.checkbox(t("rules.confirm_delete"), key=f"rule_del_confirm_{rid}")
            if st.button(t("rules.delete_btn"), type="secondary", key=f"rule_del_btn_{rid}",
                         disabled=not confirm_del):
                ok = rule_svc.delete_rule(sel_rule.id)
                if ok:
                    st.success(t("rules.rule_deleted"))
                    logger.info(f"rules_page: deleted rule {sel_rule.id}")
                    st.rerun()
                else:
                    st.error(t("rules.rule_not_found"))

    # ── Nuova regola manuale ─────────────────────────────────────────────────
    st.divider()
    st.subheader(t("rules.new_rule_title"))

    nr_pattern = st.text_input(t("rules.pattern_label"),
                               placeholder=t("rules.pattern_placeholder"),
                               key="new_rule_pattern")
    _nr_match_label = st.selectbox(t("rules.match_type"), list(match_labels.keys()), key="new_rule_match")
    nr_match = match_labels[_nr_match_label]
    nr_cat = st.selectbox(t("rules.category"), all_categories, key="new_rule_cat")
    nr_subs = taxonomy.valid_subcategories(nr_cat)
    if nr_subs:
        nr_sub = st.selectbox(t("rules.subcategory"), nr_subs, key=f"new_rule_sub__{nr_cat}")
    else:
        nr_sub = st.text_input(t("rules.subcategory"), key=f"new_rule_sub__{nr_cat}")
    nr_ctx_raw = st.selectbox(
        t("rules.context_optional"), _ctx_options, index=0, key="new_rule_ctx",
        help=t("rules.context_help"),
    )
    nr_ctx = None if nr_ctx_raw == _NO_CONTEXT else nr_ctx_raw
    nr_prio = st.number_input(t("rules.priority"), value=10, min_value=0, max_value=100, step=1,
                              key="new_rule_prio")

    # ── Preview: count matching transactions ─────────────────────────────────
    _nr_preview_txs: list = []
    _nr_preview_count = 0
    if nr_pattern.strip():
        try:
            _nr_preview_txs = tx_svc.get_by_rule_pattern(nr_pattern.strip(), nr_match)
            _nr_preview_count = len(_nr_preview_txs)
            if _nr_preview_count > 0:
                st.info(t("rules.preview_match", n=_nr_preview_count))
            else:
                st.caption(t("rules.preview_no_match"))
        except Exception:
            st.warning(t("rules.preview_invalid_regex"))
            _nr_preview_txs = []

    nr_also_apply = False
    if _nr_preview_count > 0:
        nr_also_apply = st.checkbox(
            t("rules.new_rule_also_apply", n=_nr_preview_count),
            value=True,
            key="new_rule_also_apply",
        )

    if st.button(t("rules.create_btn"), type="primary", key="new_rule_submit"):
        if not nr_pattern.strip():
            st.error(t("rules.pattern_empty"))
        else:
            _, created = rule_svc.create_rule(
                pattern=nr_pattern.strip(),
                match_type=nr_match,
                category=nr_cat,
                subcategory=nr_sub,
                context=nr_ctx,
                priority=nr_prio,
            )
            ctx_label = f" · {t('rules.col_context').lower()}: {nr_ctx}" if nr_ctx else ""
            if created:
                st.success(t("rules.created", pattern=nr_pattern, cat=nr_cat, sub=nr_sub) + ctx_label)
                logger.info(f"rules_page: created rule pattern={nr_pattern!r} cat={nr_cat!r} ctx={nr_ctx!r}")
            else:
                st.warning(t("rules.existing_updated", pattern=nr_pattern, cat=nr_cat, sub=nr_sub) + ctx_label)
                logger.info(f"rules_page: updated existing rule pattern={nr_pattern!r} cat={nr_cat!r} ctx={nr_ctx!r}")
            if nr_also_apply and _nr_preview_txs:
                for _tx in _nr_preview_txs:
                    tx_svc.update_category(_tx.id, nr_cat, nr_sub)
                    if nr_ctx:
                        tx_svc.update_context(_tx.id, nr_ctx)
                logger.info(
                    f"rules_page: applied new rule to {len(_nr_preview_txs)} existing transactions"
                    f" (pattern={nr_pattern!r})"
                )
            st.rerun()
