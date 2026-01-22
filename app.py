import streamlit as st
from support.logging import setup_logging
from support.state import init_state
from services.history_service import load_history
from ui.sidebar import render_sidebar
from ui.upload_page import render_upload_page
from ui.review_page import render_review_page
from ui.analysis_page import render_analysis_page
from ui.reconciliation_page import render_reconciliation_page
from ui.registry_page import render_registry_page

logger = setup_logging()
logger.info("Starting AI Finance Manager application")

st.set_page_config(
    page_title="AI Finance Manager",
    layout="wide",
    page_icon="🏦"
)

# Inizializza session_state
logger.debug("Initializing session state")
init_state()

# Carica storico transazioni
if "history_df" not in st.session_state or st.session_state.history_df is None:
    logger.info("Loading transaction history")
    st.session_state.history_df = load_history()
    logger.debug(f"Loaded {len(st.session_state.history_df)} transactions")
else:
    logger.debug("Transaction history already in session state")

# Sidebar
logger.debug("Rendering sidebar")
menu, ai_mode, api_key, budgets = render_sidebar()
logger.info(f"User selected menu: {menu}, AI mode: {ai_mode}")

# ------------------------
# MENU LOGIC
# ------------------------
if menu == "📥 Caricamento":
    logger.info("Rendering upload page")
    render_upload_page()

elif menu == "📝 Revisione":
    logger.info("Rendering review page")
    render_review_page(st.session_state.history_df)

elif menu == "📊 Analisi & Budget":
    logger.info("Rendering analysis page")
    render_analysis_page(st.session_state.history_df, budgets)

elif menu == "🔍 Riconciliazione Ricevute":
    logger.info("Rendering reconciliation page")
    render_reconciliation_page(st.session_state.history_df)

elif menu == "📋 Registro Caricamenti":
    logger.info("Rendering registry page")
    render_registry_page(st.session_state.history_df)

logger.debug("Page rendering complete")