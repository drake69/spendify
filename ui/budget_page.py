"""Budget page (A-02): define % budget targets per expense category."""
from __future__ import annotations

import streamlit as st

from services.budget_service import BudgetService
from services.settings_service import SettingsService
from support.logging import setup_logging
from ui.i18n import t

logger = setup_logging()


def render_budget_page(engine):
    st.header(t("budget.title"))
    st.caption(t("budget.caption"))

    svc = BudgetService(engine)
    settings_svc = SettingsService(engine)

    # Load expense categories from taxonomy
    expense_cats = settings_svc.get_categories(type_filter="expense")
    cat_names = [c.name for c in expense_cats if not c.is_fallback]
    fallback_names = [c.name for c in expense_cats if c.is_fallback]
    cat_names.extend(fallback_names)

    if not cat_names:
        st.warning(t("budget.no_categories"))
        return

    # Load existing targets
    existing_targets = {tgt["category"]: tgt["target_pct"] for tgt in svc.get_targets()}

    # ── Table with inputs ─────────────────────────────────────────────────────
    st.subheader(t("budget.targets_header"))

    if "budget_targets_draft" not in st.session_state:
        st.session_state["budget_targets_draft"] = {
            cat: existing_targets.get(cat, 0.0) for cat in cat_names
        }

    draft = st.session_state["budget_targets_draft"]

    col_cat, col_pct, col_bar = st.columns([3, 2, 5])
    with col_cat:
        st.markdown(f"**{t('budget.col.category')}**")
    with col_pct:
        st.markdown(f"**{t('budget.col.target_pct')}**")
    with col_bar:
        st.markdown(f"**{t('budget.col.allocation')}**")

    st.divider()

    new_values: dict[str, float] = {}
    for cat in cat_names:
        col_cat, col_pct, col_bar = st.columns([3, 2, 5])
        with col_cat:
            st.markdown(f"&nbsp;\n\n{cat}")
        with col_pct:
            val = st.number_input(
                f"% {cat}",
                min_value=0.0,
                max_value=100.0,
                value=float(draft.get(cat, 0.0)),
                step=1.0,
                format="%.1f",
                key=f"budget_pct_{cat}",
                label_visibility="collapsed",
            )
            new_values[cat] = val
        with col_bar:
            if val > 0:
                st.progress(min(val / 100.0, 1.0))
            else:
                st.markdown(f"&nbsp;\n\n*{t('budget.no_target')}*")

    # ── Summary bar ───────────────────────────────────────────────────────────
    st.divider()

    total_allocated = sum(new_values.values())
    remaining = 100.0 - total_allocated

    col_alloc, col_remain, col_status = st.columns(3)
    with col_alloc:
        st.metric(t("budget.total_allocated"), f"{total_allocated:.1f}%")
    with col_remain:
        label = t("budget.remaining") if remaining >= 0 else t("budget.excess")
        st.metric(label, f"{abs(remaining):.1f}%", delta=None)
    with col_status:
        if total_allocated > 100:
            st.error(t("budget.over_100", excess=f"{total_allocated - 100:.1f}"))
        elif total_allocated == 100:
            st.success(t("budget.fully_allocated"))
        elif total_allocated > 0:
            st.info(t("budget.available", pct=f"{remaining:.1f}"))
        else:
            st.info(t("budget.no_targets_set"))

    # ── Save button ───────────────────────────────────────────────────────────
    st.divider()

    col_save, col_reset = st.columns([1, 1])
    with col_save:
        if st.button(t("budget.save"), type="primary", use_container_width=True):
            targets_to_save = [
                {"category": cat, "target_pct": pct}
                for cat, pct in new_values.items()
            ]
            svc.save_targets(targets_to_save)
            st.session_state["budget_targets_draft"] = dict(new_values)
            st.success(t("budget.saved"))
            logger.info(f"Budget targets saved: {sum(1 for v in new_values.values() if v > 0)} categories, total {total_allocated:.1f}%")

    with col_reset:
        if st.button(t("budget.restore"), use_container_width=True):
            refreshed = {tgt["category"]: tgt["target_pct"] for tgt in svc.get_targets()}
            st.session_state["budget_targets_draft"] = {
                cat: refreshed.get(cat, 0.0) for cat in cat_names
            }
            st.rerun()
