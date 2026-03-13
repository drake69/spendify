import streamlit as st


_NAV = [
    ("📥 Import", "import"),
    ("📋 Ledger", "ledger"),
    ("📊 Analytics", "analytics"),
    ("🔍 Review", "review"),
    ("📏 Regole", "rules"),
    ("🗂️ Tassonomia", "taxonomy"),
    ("⚙️ Impostazioni", "settings"),
]


def render_sidebar() -> str:
    """Render the sidebar and return the selected page key."""
    st.sidebar.title("🏦 Spendify")

    if "page" not in st.session_state:
        st.session_state["page"] = "import"

    for label, key in _NAV:
        is_active = st.session_state["page"] == key
        # Highlight active page with a filled button, others secondary
        btn_type = "primary" if is_active else "secondary"
        if st.sidebar.button(label, key=f"nav_{key}", use_container_width=True, type=btn_type):
            st.session_state["page"] = key
            st.rerun()

    st.sidebar.divider()
    st.sidebar.subheader("🔄 Modalità Giroconti")
    mode = st.sidebar.radio("Giroconti nel registro", ["neutral", "exclude"], index=0,
                            label_visibility="collapsed")
    st.session_state["giroconto_mode"] = mode

    return st.session_state["page"]
