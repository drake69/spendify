"""Spendify – Streamlit entrypoint (RF-08).

Four pages:
  📥 Import   – upload + pipeline processing
  📋 Ledger   – filterable transaction table + export
  📊 Analytics – interactive charts (Plotly)
  🔍 Review   – manual review of low-confidence items
"""
import os

from dotenv import load_dotenv
load_dotenv()

import streamlit as st

from db.models import create_tables, get_engine
from support.logging import setup_logging

logger = setup_logging()
logger.info("Starting Spendify")

# ── DB bootstrap ──────────────────────────────────────────────────────────────
DB_URL = os.getenv("SPENDIFY_DB", "sqlite:///ledger.db")
engine = create_tables(get_engine(DB_URL))

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Spendify",
    layout="wide",
    page_icon="🏦",
)

# ── Sidebar navigation ────────────────────────────────────────────────────────
from ui.sidebar import render_sidebar

page = render_sidebar()

# ── Route ─────────────────────────────────────────────────────────────────────
if page == "import":
    from ui.upload_page import render_upload_page
    render_upload_page(engine)

elif page == "ledger":
    from ui.registry_page import render_registry_page
    render_registry_page(engine)

elif page == "analytics":
    from ui.analysis_page import render_analysis_page
    render_analysis_page(engine)

elif page == "review":
    from ui.review_page import render_review_page
    render_review_page(engine)