"""Spendif.ai – Streamlit entrypoint (RF-08).

Pages:
  📥 Import             – upload + pipeline processing
  📜 Import History     – import timeline with undo
  📋 Ledger             – filterable transaction table + export
  ✏️ Bulk Edit          – bulk edits: category, context, deletion, duplicates
  📊 Analytics          – interactive charts (Plotly)
  📋 Report             – spending by context/category with pivot, trends, Excel export
  💰 Budget             – define % budget targets per category
  📊 Budget vs Actual   – compare actual spending vs budget targets
  🔍 Review             – manual review of low-confidence items
  📏 Rules              – manage category rules (edit / delete / create)
  🗂️ Taxonomy           – manage categories and subcategories
  ⚙️ Settings           – locale, language, LLM backend preferences
  ✅ Checklist          – monthly tx presence per account (pivot table)
  💬 Chat              – adaptive support chatbot (RAG cloud/local or FAQ match)
"""
import os

from dotenv import load_dotenv
load_dotenv()

import streamlit as st

from db.models import create_tables, get_engine
from support.logging import setup_logging

logger = setup_logging()
logger.info("Starting Spendif.ai")

# ── S-01: Prompt integrity check ─────────────────────────────────────────────
from core.prompt_guard import verify_prompt_integrity

_prompt_errors = verify_prompt_integrity()
if _prompt_errors:
    # Shown before i18n is loaded — hardcoded bilingual warning
    st.error(
        "**LLM prompts modified from certified version / Prompt LLM modificati rispetto alla versione certificata.**\n\n"
        + "\n".join(f"- {e}" for e in _prompt_errors)
        + "\n\nRun / Eseguire: `python tools/compute_prompt_hashes.py`"
    )

# ── DB bootstrap ──────────────────────────────────────────────────────────────
DB_URL = os.getenv("SPENDIFAI_DB", "sqlite:///ledger.db")
engine = create_tables(get_engine(DB_URL))

# ── Startup cleanup ───────────────────────────────────────────────────────────
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
    page_title="Spendif.ai",
    layout="wide",
    page_icon="🏦",
)

# ── Compact layout: reduce top margin and padding ────────────────────────
st.markdown("""
<style>
    .block-container { padding-top: 1rem !important; padding-bottom: 0.5rem !important; }
    header[data-testid="stHeader"] { height: 2rem !important; }
    .stMainBlockContainer { padding-top: 0.5rem !important; }
    h1, h2, h3 { margin-top: 0.3rem !important; margin-bottom: 0.3rem !important; }
    .stDataFrame, .stDataEditor { margin-top: 0.2rem !important; }
    div[data-testid="stExpander"] { margin-top: 0.3rem !important; }
</style>
""", unsafe_allow_html=True)

# ── Onboarding gate ───────────────────────────────────────────────────────────
# Show the onboarding wizard on first run (when onboarding_done != 'true').
# This also re-shows if taxonomy_category was somehow wiped.
from services.settings_service import SettingsService as _SvcCheck
_cfg_check = _SvcCheck(engine)
if not _cfg_check.is_onboarding_done():
    from ui.onboarding_page import render_onboarding_page
    render_onboarding_page(engine)
    st.stop()

# ── i18n: set UI language from user settings ─────────────────────────────────
from ui.i18n import set_language as _set_lang
_set_lang(_cfg_check.get_all().get("ui_language", "it"))

# ── Sidebar navigation ────────────────────────────────────────────────────────
from ui.sidebar import render_sidebar

page = render_sidebar()

# ── Route ─────────────────────────────────────────────────────────────────────
if page == "import":
    from ui.upload_page import render_upload_page
    render_upload_page(engine)

elif page == "history":
    from ui.history_page import render_history_page
    render_history_page(engine)

elif page == "ledger":
    from ui.registry_page import render_registry_page
    render_registry_page(engine)

elif page == "bulk_edit":
    from ui.bulk_edit_page import render_bulk_edit_page
    render_bulk_edit_page(engine)

elif page == "analytics":
    from ui.analysis_page import render_analysis_page
    render_analysis_page(engine)

elif page == "report":
    from ui.report_page import render_report_page
    render_report_page(engine)

elif page == "budget":
    from ui.budget_page import render_budget_page
    render_budget_page(engine)

elif page == "budget_vs_actual":
    from ui.budget_vs_actual_page import render_budget_vs_actual_page
    render_budget_vs_actual_page(engine)

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

elif page == "chat":
    from ui.chat_page import render_chat_page
    render_chat_page(engine)
