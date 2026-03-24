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

    for label, key, tooltip in _NAV:
        is_active = st.session_state["page"] == key
        btn_type = "primary" if is_active else "secondary"
        if st.sidebar.button(
            label, key=f"nav_{key}", width="stretch", type=btn_type, help=tooltip
        ):
            st.session_state["page"] = key
            st.rerun()

    return st.session_state["page"]
