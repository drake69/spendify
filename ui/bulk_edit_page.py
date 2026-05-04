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
from ui.i18n import t

logger = setup_logging()

_ALL_TX_TYPES = [
    "expense", "income", "card_tx",
    "internal_out", "internal_in", "card_settlement", "unknown",
]


def render_bulk_edit_page(engine):
    st.header(t("bulk_edit.title"))
    st.caption(t("bulk_edit.caption"))

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
    st.subheader(t("bulk_edit.section1_title"))

    fc1, fc2, fc3 = st.columns([3, 2, 2])
    with fc1:
        desc_search = st.text_input(t("bulk_edit.search_desc"), key="bulk_desc_search",
                                    placeholder=t("bulk_edit.search_placeholder"))
    with fc2:
        only_review = st.checkbox(t("bulk_edit.only_review"), key="bulk_only_review")
    with fc3:
        show_all = st.checkbox(t("bulk_edit.show_all"), key="bulk_show_all",
                               value=False)

    filters: dict = {}
    if desc_search.strip():
        filters["description"] = desc_search.strip()
    if only_review:
        filters["to_review"] = True

    txs = tx_svc.get_transactions(filters=filters, limit=500)

    if not txs:
        st.info(t("bulk_edit.no_transactions"))
        return

    tx_options = {
        f"{'⚠️ ' if tx.to_review else ''}"
        f"{format_date_display(tx.date, _date_fmt)} | "
        f"{(tx.description or '')[:70]} | "
        f"{format_amount_display(abs(float(tx.amount)), _dec, _thou, symbol='')}": tx
        for tx in txs
    }

    selected_label = st.selectbox(
        t("bulk_edit.ref_transaction"),
        list(tx_options.keys()),
        key="bulk_tx_select",
    )
    sel = tx_options[selected_label]

    is_giroconto = sel.tx_type in ("internal_out", "internal_in")
    is_expense   = float(sel.amount) < 0

    st.caption(
        t("bulk_edit.tx_info",
          tx_type=sel.tx_type,
          category=sel.category or "—",
          subcategory=sel.subcategory or "—",
          confidence=sel.category_confidence or "—",
          context=sel.context or "—")
    )

    # ── Similarity counts ──────────────────────────────────────────────────────
    _same_desc  = tx_svc.count_by_description(sel.description or "", sel.id) if sel.description else 0
    _same_raw   = tx_svc.count_by_raw_description(sel.raw_description or "", sel.id) if sel.raw_description else 0
    _similar_txs = tx_svc.get_similar(sel.description or "", exclude_id=sel.id)
    _similar_count = len(_similar_txs)

    if _same_desc > 0 or _similar_count > 0:
        st.info(
            t("bulk_edit.same_desc_info",
              same_desc=_same_desc, same_raw=_same_raw,
              similar=_similar_count)
        )

    st.divider()

    # ── SEZIONE 2: Ri-elabora con l'LLM ───────────────────────────────────────
    st.subheader(t("bulk_edit.section2_title"))
    st.caption(t("bulk_edit.section2_caption"))

    _scope_opts = [
        t("bulk_edit.scope.selected_only"),
        t("bulk_edit.scope.same_raw", n=_same_raw + 1),
        t("bulk_edit.scope.similar_jaccard", n=_similar_count + 1),
        t("bulk_edit.scope.all_review"),
        t("bulk_edit.scope.all_no_category"),
    ]
    rr_scope = st.radio(
        t("bulk_edit.scope_label"),
        _scope_opts,
        key="rerun_scope",
        horizontal=True,
    )

    rrc1, rrc2 = st.columns(2)
    with rrc1:
        run_cleaner    = st.checkbox(t("bulk_edit.extract_counterpart"), value=True, key="rerun_cleaner",
                                     help=t("bulk_edit.extract_counterpart_help"))
    with rrc2:
        run_categorizer = st.checkbox(t("bulk_edit.recategorize"),  value=True, key="rerun_cat",
                                      help=t("bulk_edit.recategorize_help"))

    _rerun_disabled = not run_cleaner and not run_categorizer

    if st.button(t("bulk_edit.start_rerun"), key="rerun_execute", type="primary",
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
            st.warning(t("bulk_edit.no_tx_found_criteria"))
        else:
            n_rerun = len(tx_ids)
            _progress_bar = st.progress(0.0)
            _status_text  = st.empty()

            if run_cleaner:
                _status_text.info(t("bulk_edit.rerun_extracting", n=n_rerun))

            def _cat_progress_cb(p: float):
                base = 0.5 if run_cleaner else 0.0
                _progress_bar.progress(base + p * (1.0 - base))
                if run_categorizer:
                    _status_text.info(t("bulk_edit.rerun_categorizing", n=n_rerun, pct=p * 100))

            try:
                n_desc, n_cat, n_review = review_svc.rerun_pipeline_on_txs(
                    tx_ids, run_cleaner, run_categorizer,
                    categorizer_progress_callback=_cat_progress_cb if run_categorizer else None,
                )
            except Exception as _pe:
                st.error(t("bulk_edit.rerun_error", error=_pe))
                logger.exception("bulk_edit: rerun pipeline error")
                st.stop()

            _progress_bar.progress(1.0)
            _status_text.empty()

            _parts = []
            if run_cleaner:
                _parts.append(t("bulk_edit.rerun_desc_updated", n=n_desc))
            if run_categorizer:
                _parts.append(t("bulk_edit.rerun_cat_updated", n=n_cat, review=n_review))
            st.success(t("bulk_edit.rerun_completed", n=n_rerun, details=" · ".join(_parts)))
            logger.info(
                f"bulk_edit: rerun scope={rr_scope!r} n={n_rerun} "
                f"desc={n_desc} cat={n_cat} review={n_review}"
            )
            st.rerun()

    st.divider()

    # ── SEZIONE 3: Giroconto ───────────────────────────────────────────────────
    st.subheader(t("bulk_edit.section3a_title"))

    giro_label = t("bulk_edit.giro_remove") if is_giroconto else t("bulk_edit.giro_mark")

    g1, g2 = st.columns([2, 3])
    with g1:
        apply_giro_similar = st.checkbox(
            t("bulk_edit.apply_same_desc_giro", n=_same_desc),
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
                action = t("bulk_edit.giro_removed") if new_type in ("expense", "income") \
                         else t("bulk_edit.giro_marked")
                extra = t("bulk_edit.similar_updated", n=n_extra) if n_extra else ""
                st.success(t("bulk_edit.giro_action_done", action=action, type=new_type, extra=extra))
                logger.info(f"bulk_edit: giro tx={sel.id} new_type={new_type} extra={n_extra}")
                st.rerun()
            else:
                st.error(t("bulk_edit.tx_not_found"))

    st.divider()

    # ── SEZIONE 3b: Contesto ──────────────────────────────────────────────────
    st.subheader(t("bulk_edit.section3b_title"))

    _ctx_none = t("bulk_edit.context_none")
    _ctx_options = [_ctx_none] + _contexts
    _cur_ctx_idx = _ctx_options.index(sel.context) if sel.context in _ctx_options else 0

    cx1, cx2, cx3 = st.columns([2, 2, 2])
    with cx1:
        new_ctx_label = st.selectbox(
            t("ledger.col.context"), _ctx_options, index=_cur_ctx_idx, key="bulk_ctx_select"
        )
        new_ctx_value = None if new_ctx_label == _ctx_none else new_ctx_label
    with cx2:
        apply_ctx_same = st.checkbox(
            t("bulk_edit.apply_same_desc_ctx", n=_same_desc),
            value=(_same_desc > 0),
            disabled=(_same_desc == 0),
            key="bulk_ctx_same",
        )
        apply_ctx_similar = st.checkbox(
            t("bulk_edit.apply_similar_ctx", n=_similar_count),
            value=False,
            disabled=(_similar_count == 0),
            key="bulk_ctx_similar",
        )
    with cx3:
        if st.button(t("bulk_edit.apply_context_btn"), key="bulk_ctx_save", use_container_width=True):
            tx_svc.update_context(sel.id, new_ctx_value)
            n_extra = 0
            if apply_ctx_same and sel.description:
                same_txs = tx_svc.get_by_description(sel.description, sel.id)
                n_extra += tx_svc.update_context_bulk([stx.id for stx in same_txs], new_ctx_value)
            if apply_ctx_similar:
                similar_ids = [stx.id for stx in _similar_txs if stx.id != sel.id]
                n_extra += tx_svc.update_context_bulk(similar_ids, new_ctx_value)
            ctx_display = new_ctx_value or t("bulk_edit.context_none_label")
            extra = t("bulk_edit.similar_updated", n=n_extra) if n_extra else ""
            st.success(t("bulk_edit.context_set", context=ctx_display, extra=extra))
            logger.info(f"bulk_edit: ctx tx={sel.id} ctx={ctx_display} extra={n_extra}")
            st.rerun()

    st.divider()

    # ── SEZIONE 3c: Categoria ─────────────────────────────────────────────────
    if not is_giroconto:
        st.subheader(t("bulk_edit.section3c_title"))

        all_categories = _expense_cats if is_expense else _income_cats

        cat_idx = all_categories.index(sel.category) \
            if sel.category in all_categories else 0

        ca1, ca2 = st.columns(2)
        with ca1:
            new_cat = st.selectbox(
                t("bulk_edit.new_category"), all_categories, index=cat_idx, key="bulk_cat_select"
            )
        with ca2:
            subs = taxonomy.valid_subcategories(new_cat)
            if subs:
                sub_idx = subs.index(sel.subcategory) \
                    if (sel.subcategory in subs and sel.category == new_cat) else 0
                new_sub = st.selectbox(
                    t("bulk_edit.new_subcategory"), subs, index=sub_idx,
                    key=f"bulk_sub_select_{new_cat}"
                )
            else:
                new_sub = st.text_input(
                    t("bulk_edit.new_subcategory"),
                    value=sel.subcategory or "",
                    key=f"bulk_sub_text_{new_cat}"
                )

        op1, op2 = st.columns(2)
        with op1:
            save_rule = st.checkbox(
                t("bulk_edit.save_as_rule"),
                value=True,
                key="bulk_save_rule",
                help=t("bulk_edit.save_as_rule_help"),
            )
        with op2:
            apply_to_similar_cat = st.checkbox(
                t("bulk_edit.apply_same_desc_cat", n=_same_desc),
                value=(_same_desc > 0),
                disabled=(_same_desc == 0),
                key="bulk_cat_similar",
            )

        if st.button(t("bulk_edit.apply_category_btn"), type="primary", key="bulk_cat_save"):
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
                    rule_msg = t("bulk_edit.rule_created") if created else t("bulk_edit.rule_updated")

                if apply_to_similar_cat and sel.description:
                    similar = tx_svc.get_by_rule_pattern(sel.description, "contains")
                    for stx in similar:
                        if stx.id != sel.id:
                            tx_svc.update_category(stx.id, new_cat, new_sub)
                            n_similar += 1
                    if n_similar:
                        rule_msg += t("bulk_edit.similar_tx_updated", n=n_similar)

                st.success(t("bulk_edit.cat_updated", cat=new_cat, sub=new_sub, extra=rule_msg))
                logger.info(
                    f"bulk_edit: cat tx={sel.id} cat={new_cat}/{new_sub} "
                    f"rule={save_rule} similar={n_similar}"
                )
                st.rerun()
            else:
                st.error(t("bulk_edit.tx_not_found"))
    else:
        st.info(t("bulk_edit.giro_no_category"))

    st.divider()

    # ── SEZIONE 4: Eliminazione massiva da filtro ──────────────────────────────
    st.subheader(t("bulk_edit.section4_title"))
    st.caption(t("bulk_edit.section4_caption"))

    _del_accounts = tx_svc.get_distinct_account_labels()

    _all_label = t("bulk_edit.filter.account_all")
    _type_all  = t("bulk_edit.filter.type_all")
    _cat_all   = t("bulk_edit.filter.category_all")

    de1, de2, de3, de4 = st.columns(4)
    with de1:
        del_date_from = st.date_input(t("bulk_edit.filter.from"), key="del_date_from", value=None)
    with de2:
        del_date_to   = st.date_input(t("bulk_edit.filter.to"),  key="del_date_to",   value=None)
    with de3:
        del_account = st.selectbox(
            t("bulk_edit.filter.account"), [_all_label] + _del_accounts, key="del_account"
        )
    with de4:
        del_tx_type = st.selectbox(t("bulk_edit.filter.type"), [_type_all] + _ALL_TX_TYPES, key="del_type")

    de5, de6 = st.columns([3, 2])
    with de5:
        del_desc = st.text_input(
            t("bulk_edit.filter.desc"), key="del_desc",
            placeholder=t("bulk_edit.filter.desc_placeholder")
        )
    with de6:
        del_cat = st.selectbox(
            t("bulk_edit.filter.category"),
            [_cat_all] + sorted(taxonomy.all_expense_categories) + sorted(taxonomy.all_income_categories),
            key="del_cat",
        )

    _del_filters: dict = {}
    if del_date_from:
        _del_filters["date_from"] = del_date_from.isoformat()
    if del_date_to:
        _del_filters["date_to"] = del_date_to.isoformat()
    if del_account != _all_label:
        _del_filters["account_label"] = del_account
    if del_tx_type != _type_all:
        _del_filters["tx_type"] = del_tx_type
    if del_desc.strip():
        _del_filters["description"] = del_desc.strip()
    if del_cat != _cat_all:
        _del_filters["category"] = del_cat

    _del_preview = tx_svc.get_transactions(filters=_del_filters, limit=10)
    _all_del     = tx_svc.get_transactions(filters=_del_filters)
    _del_count   = len(_all_del)

    _confirm_kw = t("bulk_edit.confirm_keyword")

    if not _del_filters:
        st.warning(t("bulk_edit.no_filter_warning"))
    else:
        if _del_count == 0:
            st.info(t("bulk_edit.no_match_filter"))
        else:
            st.error(t("bulk_edit.delete_warning", n=_del_count))

            with st.expander(t("bulk_edit.preview_title", n=min(10, _del_count))):
                import pandas as _pd
                _prev_rows = [
                    {
                        t("bulk_edit.col.date"):        format_date_display(tx.date, _date_fmt),
                        t("bulk_edit.col.description"): (tx.description or "")[:70],
                        t("bulk_edit.col.amount"):      float(tx.amount),
                        t("bulk_edit.col.account"):     tx.account_label or "",
                        t("bulk_edit.col.type"):        tx.tx_type or "",
                        t("bulk_edit.col.category"):    tx.category or "",
                    }
                    for tx in _del_preview
                ]
                st.dataframe(_pd.DataFrame(_prev_rows), use_container_width=True, hide_index=True)

            st.markdown(t("bulk_edit.confirm_delete_instruction"))
            cc1, cc2 = st.columns([2, 1])
            with cc1:
                confirm_text = st.text_input(
                    t("bulk_edit.confirm_delete_label"), key="del_confirm",
                    placeholder=t("bulk_edit.confirm_delete_placeholder")
                )
            with cc2:
                del_enabled = confirm_text.strip() == _confirm_kw
                if st.button(
                    t("bulk_edit.delete_btn", n=_del_count),
                    type="primary",
                    disabled=not del_enabled,
                    key="del_execute",
                    use_container_width=True,
                ):
                    n_deleted = tx_svc.delete_by_filter(_del_filters)
                    st.success(t("bulk_edit.deleted_success", n=n_deleted))
                    logger.info(f"bulk_edit: deleted {n_deleted} tx filters={_del_filters}")
                    if "del_confirm" in st.session_state:
                        del st.session_state["del_confirm"]
                    st.rerun()

    st.divider()

    # ── SEZIONE 5: Duplicati tra conti ─────────────────────────────────────────
    st.subheader(t("bulk_edit.section5_title"))
    st.caption(t("bulk_edit.section5_caption"))

    _dup_groups = tx_svc.get_cross_account_duplicates()

    if not _dup_groups:
        st.success(t("bulk_edit.no_duplicates"))
    else:
        import pandas as _pd
        from itertools import combinations

        n_groups = len(_dup_groups)
        n_extra  = sum(len(g) - 1 for g in _dup_groups)
        st.warning(t("bulk_edit.dup_groups_found", n_groups=n_groups, n_extra=n_extra))

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
                t("bulk_edit.col.date"):     format_date_display(ref.date, _date_fmt),
                t("bulk_edit.col.raw_desc"): (ref.raw_description or "")[:80],
                t("bulk_edit.col.amount"):   float(ref.amount),
                t("bulk_edit.col.accounts"): ", ".join(sorted({tx.account_label or "" for tx in g})),
                t("bulk_edit.col.copies"):   len(g),
                "IDs":                       ", ".join(tx.id[:8] for tx in g),
            })
        st.dataframe(
            _pd.DataFrame(_dup_rows),
            use_container_width=True,
            hide_index=True,
        )

        st.markdown(t("bulk_edit.dup_delete_info"))
        dc1, dc2 = st.columns([2, 1])
        with dc1:
            dup_confirm = st.text_input(
                t("bulk_edit.confirm_dup_label"),
                key="dup_confirm",
                placeholder=t("bulk_edit.confirm_dup_placeholder"),
            )
        with dc2:
            dup_enabled = dup_confirm.strip() == _confirm_kw
            if st.button(
                t("bulk_edit.delete_dup_btn", n=n_extra),
                type="primary",
                disabled=not dup_enabled,
                key="dup_execute",
                use_container_width=True,
            ):
                deleted = tx_svc.delete_duplicate_groups(_dup_groups)
                st.success(t("bulk_edit.dup_deleted", n=deleted))
                logger.info(f"bulk_edit: deleted {deleted} cross-account duplicates")
                if "dup_confirm" in st.session_state:
                    del st.session_state["dup_confirm"]
                st.rerun()
