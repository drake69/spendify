import streamlit as st

from ui.i18n import t

# Navigation entries: (i18n_key_suffix, page_key)
_NAV_KEYS = [
    ("import",          "import"),
    ("history",         "history"),
    ("ledger",          "ledger"),
    ("bulk_edit",       "bulk_edit"),
    ("analytics",       "analytics"),
    ("report",          "report"),
    ("budget",          "budget"),
    ("budget_vs_actual","budget_vs_actual"),
    ("review",          "review"),
    ("rules",           "rules"),
    ("taxonomy",        "taxonomy"),
    ("settings",        "settings"),
    ("checklist",       "checklist"),
    ("chat",            "chat"),
]


def render_sidebar() -> str:
    """Render the sidebar and return the selected page key."""
    st.sidebar.title(t("sidebar.title"))

    if "page" not in st.session_state:
        st.session_state["page"] = "import"

    llm_running = st.session_state.get("llm_in_progress", False)

    # Navigation guard: warn user when an LLM process is active
    if llm_running:
        st.sidebar.warning(t("sidebar.llm_warning"))

    # Confirmation gate: if user clicked a nav button while LLM was running,
    # show confirm/cancel instead of navigating immediately.
    pending_nav = st.session_state.pop("_pending_nav_target", None)
    if pending_nav and llm_running:
        st.sidebar.error(t("sidebar.nav_confirm_msg"))
        col1, col2 = st.sidebar.columns(2)
        with col1:
            if st.button(t("common.confirm"), key="nav_confirm_interrupt", type="primary"):
                st.session_state["llm_in_progress"] = False
                st.session_state["page"] = pending_nav
                st.rerun()
        with col2:
            if st.button(t("sidebar.nav_stay"), key="nav_cancel_interrupt"):
                st.rerun()
        # While confirmation is pending, still show nav but don't process clicks
        return st.session_state["page"]

    for nav_suffix, key in _NAV_KEYS:
        label = t(f"nav.{nav_suffix}")
        tooltip = t(f"nav.{nav_suffix}.desc")
        is_active = st.session_state["page"] == key
        btn_type = "primary" if is_active else "secondary"
        if st.sidebar.button(
            label, key=f"nav_{key}", width="stretch", type=btn_type, help=tooltip
        ):
            if llm_running and key != st.session_state["page"]:
                # Defer navigation — ask for confirmation on next rerun
                st.session_state["_pending_nav_target"] = key
                st.rerun()
            else:
                st.session_state["page"] = key
                st.rerun()

    return st.session_state["page"]
