import streamlit as st


_NAV = [
    ("📥 Import", "import", "Importa file CSV/XLSX dai tuoi conti bancari"),
    ("📋 Ledger", "ledger", "Consulta e filtra tutte le transazioni importate"),
    ("✏️ Modifiche massive", "bulk_edit", "Modifica categoria, contesto o giroconto su gruppi di transazioni"),
    ("📊 Analytics", "analytics", "Grafici interattivi su entrate, uscite e andamento"),
    ("🔍 Review", "review", "Rivedi le transazioni segnalate per verifica"),
    ("📏 Regole", "rules", "Gestisci le regole automatiche di categorizzazione"),
    ("🗂️ Tassonomia", "taxonomy", "Configura categorie e sottocategorie di spesa/entrata"),
    ("⚙️ Impostazioni", "settings", "Conti bancari, backend LLM e preferenze generali"),
    ("✅ Check List", "checklist", "Verifica completezza e qualità dei dati importati"),
]


def render_sidebar() -> str:
    """Render the sidebar and return the selected page key."""
    st.sidebar.title("🏦 Spendify")

    if "page" not in st.session_state:
        st.session_state["page"] = "import"

    llm_running = st.session_state.get("llm_in_progress", False)

    # Navigation guard: warn user when an LLM process is active
    if llm_running:
        st.sidebar.warning(
            "⚠️ Elaborazione AI in corso. "
            "Cambiare pagina interromperà il processo."
        )

    # Confirmation gate: if user clicked a nav button while LLM was running,
    # show confirm/cancel instead of navigating immediately.
    pending_nav = st.session_state.pop("_pending_nav_target", None)
    if pending_nav and llm_running:
        st.sidebar.error(
            f"Stai per abbandonare la pagina corrente. "
            f"L'elaborazione AI verrà interrotta."
        )
        col1, col2 = st.sidebar.columns(2)
        with col1:
            if st.button("Conferma", key="nav_confirm_interrupt", type="primary"):
                st.session_state["llm_in_progress"] = False
                st.session_state["page"] = pending_nav
                st.rerun()
        with col2:
            if st.button("Resta", key="nav_cancel_interrupt"):
                st.rerun()
        # While confirmation is pending, still show nav but don't process clicks
        return st.session_state["page"]

    for label, key, tooltip in _NAV:
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
