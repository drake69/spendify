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


def render_chat_page(engine) -> None:
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
                with st.expander(t_fn("chat.sources")):
                    for src in msg["sources"]:
                        st.markdown(f"- `{src}`")

    # chat input
    if user_input := st.chat_input(t_fn("chat.placeholder")):
        # show user message
        st.session_state["chat_history"].append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        # get bot response
        with st.chat_message("assistant"):
            with st.spinner(t_fn("chat.thinking")):
                response = bot.ask(user_input)
            st.markdown(response.text)
            if response.sources:
                with st.expander(t_fn("chat.sources")):
                    for src in response.sources:
                        st.markdown(f"- `{src}`")

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
