"""Spendify – Streamlit entrypoint (RF-08).

Nine pages:
  📥 Import             – upload + pipeline processing
  📋 Ledger             – filterable transaction table + export
  ✏️ Modifiche massive  – bulk edits: category, context, deletion
  📊 Analytics          – interactive charts (Plotly)
  🔍 Review             – manual review of low-confidence items
  📏 Regole             – manage category rules (edit / delete / create)
  🗂️ Tassonomia         – manage categories and subcategories in taxonomy.yaml
  ⚙️ Impostazioni       – locale and language preferences
  ✅ Check List         – monthly tx presence per account (pivot table)
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

# ── Startup cleanup ───────────────────────────────────────────────────────────
# Reset any import jobs left in "running" state from a previous server process.
# Runs once per browser session (session_state is empty on first connection
# after a server restart, so the guard fires exactly once per new session).
if "stale_jobs_reset" not in st.session_state:
    st.session_state["stale_jobs_reset"] = True
    from db.models import get_session
    from db.repository import reset_stale_jobs
    with get_session(engine) as _startup_s:
        _n_stale = reset_stale_jobs(_startup_s)
    if _n_stale:
        logger.info(f"startup: reset {_n_stale} stale running job(s) to error")

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

elif page == "bulk_edit":
    from ui.bulk_edit_page import render_bulk_edit_page
    render_bulk_edit_page(engine)

elif page == "analytics":
    from ui.analysis_page import render_analysis_page
    render_analysis_page(engine)

elif page == "review":
    from ui.review_page import render_review_page
    render_review_page(engine)

elif page == "rules":
    from ui.rules_page import render_rules_page
    render_rules_page(engine)

elif page == "taxonomy":
    from ui.taxonomy_page import render_taxonomy_page
    render_taxonomy_page(engine)

elif page == "settings":
    from ui.settings_page import render_settings_page
    render_settings_page(engine)

elif page == "checklist":
    from ui.checklist_page import render_checklist_page
    render_checklist_page(engine)