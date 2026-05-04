"""Chat page — Spendif.ai support chatbot."""
from __future__ import annotations

import streamlit as st

from chat_bot.engine import ChatBotEngine, ChatMode
from services.settings_service import SettingsService
from ui.i18n import t as t_fn

_MODE_LABELS = {
    ChatMode.RAG_CLOUD: "RAG Cloud",
    ChatMode.RAG_LOCAL: "RAG Local",
    ChatMode.FAQ_MATCH: "FAQ",
}

# Maps page_ref keys → human-readable nav label (same keys as sidebar _NAV_KEYS)
_PAGE_LABELS = {
    "import":           "📥 Import",
    "history":          "📜 Storico import",
    "ledger":           "📋 Ledger",
    "bulk_edit":        "✏️ Modifiche massive",
    "analytics":        "📊 Analytics",
    "report":           "📋 Report",
    "budget":           "💰 Budget",
    "budget_vs_actual": "📊 Budget vs Actual",
    "review":           "🔍 Review",
    "rules":            "📏 Regole",
    "taxonomy":         "🗂️ Tassonomia",
    "settings":         "⚙️ Impostazioni",
    "checklist":        "✅ Check List",
}


_CHAT_CSS = """
<style>
/* ── WhatsApp-style chat bubbles ─────────────────────────────────── */

/* User messages: right-aligned bubble */
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
    flex-direction: row-reverse;
    align-items: flex-start;
    background: #dcf8c6;
    border-radius: 16px 4px 16px 16px;
    width: fit-content;
    max-width: 72%;
    height: auto !important;
    min-height: 0 !important;
    margin-left: auto;
    margin-right: 0;
    padding: 6px 10px 4px 10px;
    margin-bottom: 4px;
    box-shadow: 0 1px 1px rgba(0,0,0,.08);
}

/* Assistant messages: left-aligned bubble */
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) {
    align-items: flex-start;
    background: #f0f0f0;
    border-radius: 4px 16px 16px 16px;
    width: fit-content;
    max-width: 78%;
    height: auto !important;
    min-height: 0 !important;
    margin-left: 0;
    margin-right: auto;
    padding: 6px 10px 4px 10px;
    margin-bottom: 4px;
    box-shadow: 0 1px 1px rgba(0,0,0,.08);
}

/* Force black text inside all bubbles */
[data-testid="stChatMessage"] p,
[data-testid="stChatMessage"] li,
[data-testid="stChatMessage"] span,
[data-testid="stChatMessage"] div {
    color: #000000 !important;
}

/* Compact: tighten paragraph spacing inside bubbles */
[data-testid="stChatMessage"] p {
    margin-bottom: 4px !important;
}
[data-testid="stChatMessage"] .stCaptionContainer {
    margin-top: 2px !important;
}
</style>
"""


def render_chat_page(engine) -> None:
    st.markdown(_CHAT_CSS, unsafe_allow_html=True)
    st.header(t_fn("chat.title"))

    settings = SettingsService(engine)
    lang = settings.get_all().get("ui_language", "it")

    # initialise chatbot once per session
    if "chatbot" not in st.session_state:
        st.session_state["chatbot"] = ChatBotEngine(db_engine=engine, lang=lang)
    if "chat_history" not in st.session_state:
        st.session_state["chat_history"] = []

    bot: ChatBotEngine = st.session_state["chatbot"]

    # mode badge
    mode_label = _MODE_LABELS.get(bot.mode, bot.mode.value)
    st.caption(t_fn("chat.mode_label").format(mode=mode_label))

    # suggested questions (only when history is empty)
    if not st.session_state["chat_history"]:
        st.markdown(f"*{t_fn('chat.welcome')}*")
        _render_suggestions(bot, lang)

    # render conversation history
    for msg in st.session_state["chat_history"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("sources"):
                _render_sources_caption(msg["sources"])

    # chat input
    if user_input := st.chat_input(t_fn("chat.placeholder")):
        # show user message
        st.session_state["chat_history"].append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        # get bot response (pass history excluding the just-added user message)
        history_so_far = st.session_state["chat_history"][:-1]
        with st.chat_message("assistant"):
            with st.spinner(t_fn("chat.thinking")):
                response = bot.ask(user_input, history=history_so_far or None)
            st.markdown(response.text)
            if response.sources:
                _render_sources_caption(response.sources)

        st.session_state["chat_history"].append({
            "role": "assistant",
            "content": response.text,
            "sources": response.sources,
        })

    # clear chat button
    if st.session_state["chat_history"]:
        if st.button(t_fn("chat.clear"), type="secondary"):
            st.session_state["chat_history"] = []
            st.rerun()


def _render_suggestions(bot: ChatBotEngine, lang: str) -> None:
    """Show clickable suggested questions."""
    suggestions = _get_suggestions(lang)
    if not suggestions:
        return
    cols = st.columns(len(suggestions))
    for col, suggestion in zip(cols, suggestions):
        with col:
            if st.button(suggestion, key=f"suggest_{suggestion[:20]}", use_container_width=True):
                st.session_state["chat_history"].append({"role": "user", "content": suggestion})
                response = bot.ask(suggestion)
                st.session_state["chat_history"].append({
                    "role": "assistant",
                    "content": response.text,
                    "sources": response.sources,
                })
                st.rerun()


def _get_suggestions(lang: str) -> list[str]:
    """Return suggested questions based on language."""
    _SUGGESTIONS = {
        "it": [
            "Come importo un file?",
            "Quali formati sono supportati?",
            "Come cambio una categoria?",
        ],
        "en": [
            "How do I import a file?",
            "What formats are supported?",
            "How do I change a category?",
        ],
    }
    return _SUGGESTIONS.get(lang, _SUGGESTIONS.get("en", []))


def _render_sources_caption(sources: list[str]) -> None:
    """Render sources as a subtle single-line caption below a message."""
    labels = [_format_source(s) for s in sources if _format_source(s)]
    if labels:
        st.caption("  ·  ".join(labels))


def _format_source(src: str) -> str:
    """Format a source reference for display.

    - Known page_ref keys  → "→ 📥 Import" (navigable page label)
    - Doc filenames (.md)  → "📄 guida_utente.md"
    - Anything else        → suppressed (return empty string)
    """
    if not src:
        return ""
    if src in _PAGE_LABELS:
        return f"→ {_PAGE_LABELS[src]}"
    if src.endswith((".md", ".txt", ".pdf")):
        return f"📄 {src}"
    return ""
