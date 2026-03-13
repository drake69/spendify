"""Settings page — persistent user preferences (locale, language, LLM)."""
from __future__ import annotations

import os

import streamlit as st

from db.models import get_session
from db.repository import get_all_user_settings, set_user_setting
from support.logging import setup_logging

logger = setup_logging()

_DATE_FORMAT_OPTIONS = {
    "dd/mm/yyyy  (es. 31/12/2025)": "%d/%m/%Y",
    "yyyy-mm-dd  (ISO 8601)": "%Y-%m-%d",
    "mm/dd/yyyy  (US)": "%m/%d/%Y",
}

_DECIMAL_SEP_OPTIONS = {
    "Virgola  ,  (italiano/europeo)": ",",
    "Punto    .  (inglese/US)": ".",
}

_THOUSANDS_SEP_OPTIONS = {
    "Punto    .  (italiano/europeo)": ".",
    "Virgola  ,  (inglese/US)": ",",
    "Spazio      (francese)": " ",
    "Nessuno": "",
}

_LANGUAGE_OPTIONS = {
    "Italiano": "it",
    "English": "en",
    "Français": "fr",
    "Deutsch": "de",
}

_BACKEND_OPTIONS = {
    "Ollama (locale, privacy-first)": "local_ollama",
    "OpenAI": "openai",
    "Claude (Anthropic)": "claude",
}


def _key_for(options: dict, value: str) -> str:
    """Return the display label whose value matches, or first key as fallback."""
    for label, v in options.items():
        if v == value:
            return label
    return next(iter(options))


def render_settings_page(engine):
    st.header("⚙️ Impostazioni")

    with get_session(engine) as session:
        settings = get_all_user_settings(session)

    # ── Formato visualizzazione ────────────────────────────────────────────────
    st.subheader("Formato visualizzazione")

    date_label = st.selectbox(
        "Formato data",
        list(_DATE_FORMAT_OPTIONS.keys()),
        index=list(_DATE_FORMAT_OPTIONS.keys()).index(
            _key_for(_DATE_FORMAT_OPTIONS, settings.get("date_display_format", "%d/%m/%Y"))
        ),
    )

    col1, col2 = st.columns(2)
    with col1:
        dec_label = st.selectbox(
            "Separatore decimali",
            list(_DECIMAL_SEP_OPTIONS.keys()),
            index=list(_DECIMAL_SEP_OPTIONS.keys()).index(
                _key_for(_DECIMAL_SEP_OPTIONS, settings.get("amount_decimal_sep", ","))
            ),
        )
    with col2:
        thou_label = st.selectbox(
            "Separatore migliaia",
            list(_THOUSANDS_SEP_OPTIONS.keys()),
            index=list(_THOUSANDS_SEP_OPTIONS.keys()).index(
                _key_for(_THOUSANDS_SEP_OPTIONS, settings.get("amount_thousands_sep", "."))
            ),
        )

    # Preview
    from support.formatting import format_amount_display, format_date_display
    preview_date = format_date_display("2025-12-31", _DATE_FORMAT_OPTIONS[date_label])
    preview_amount = format_amount_display(
        1234.56,
        decimal_sep=_DECIMAL_SEP_OPTIONS[dec_label],
        thousands_sep=_THOUSANDS_SEP_OPTIONS[thou_label],
    )
    st.info(f"Anteprima: data → **{preview_date}** · importo → **{preview_amount}**")

    st.divider()

    # ── Lingua descrizioni ─────────────────────────────────────────────────────
    st.subheader("Lingua delle descrizioni")
    st.caption(
        "La lingua in cui sono scritte le descrizioni nelle rendicontazioni bancarie. "
        "Viene usata dal categorizzatore LLM per interpretare correttamente le transazioni."
    )

    lang_label = st.selectbox(
        "Lingua descrizioni",
        list(_LANGUAGE_OPTIONS.keys()),
        index=list(_LANGUAGE_OPTIONS.keys()).index(
            _key_for(_LANGUAGE_OPTIONS, settings.get("description_language", "it"))
        ),
    )

    st.divider()

    # ── Configurazione LLM ─────────────────────────────────────────────────────
    st.subheader("🤖 Configurazione LLM")

    backend_label = st.selectbox(
        "Backend LLM",
        list(_BACKEND_OPTIONS.keys()),
        index=list(_BACKEND_OPTIONS.keys()).index(
            _key_for(_BACKEND_OPTIONS, settings.get("llm_backend", "local_ollama"))
        ),
    )
    backend = _BACKEND_OPTIONS[backend_label]

    if backend == "local_ollama":
        col_url, col_model = st.columns([2, 1])
        with col_url:
            ollama_url = st.text_input(
                "URL server Ollama",
                value=settings.get("ollama_base_url", os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")),
            )
        with col_model:
            ollama_model = st.text_input(
                "Modello",
                value=settings.get("ollama_model", os.getenv("OLLAMA_MODEL", "gemma3:12b")),
            )
        openai_key = settings.get("openai_api_key", "")
        openai_model = settings.get("openai_model", os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
        anthropic_key = settings.get("anthropic_api_key", "")
        anthropic_model = settings.get("anthropic_model", os.getenv("CLAUDE_MODEL", "claude-3-5-haiku-20241022"))

    elif backend == "openai":
        col_key, col_model = st.columns([2, 1])
        with col_key:
            openai_key = st.text_input(
                "OpenAI API Key",
                type="password",
                value=settings.get("openai_api_key", os.getenv("OPENAI_API_KEY", "")),
                placeholder="sk-...",
            )
        with col_model:
            openai_model = st.text_input(
                "Modello",
                value=settings.get("openai_model", os.getenv("OPENAI_MODEL", "gpt-4o-mini")),
            )
        ollama_url = settings.get("ollama_base_url", os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))
        ollama_model = settings.get("ollama_model", os.getenv("OLLAMA_MODEL", "gemma3:12b"))
        anthropic_key = settings.get("anthropic_api_key", "")
        anthropic_model = settings.get("anthropic_model", os.getenv("CLAUDE_MODEL", "claude-3-5-haiku-20241022"))

    elif backend == "claude":
        col_key, col_model = st.columns([2, 1])
        with col_key:
            anthropic_key = st.text_input(
                "Anthropic API Key",
                type="password",
                value=settings.get("anthropic_api_key", os.getenv("ANTHROPIC_API_KEY", "")),
                placeholder="sk-ant-...",
            )
        with col_model:
            anthropic_model = st.text_input(
                "Modello",
                value=settings.get("anthropic_model", os.getenv("CLAUDE_MODEL", "claude-3-5-haiku-20241022")),
            )
        ollama_url = settings.get("ollama_base_url", os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))
        ollama_model = settings.get("ollama_model", os.getenv("OLLAMA_MODEL", "gemma3:12b"))
        openai_key = settings.get("openai_api_key", "")
        openai_model = settings.get("openai_model", os.getenv("OPENAI_MODEL", "gpt-4o-mini"))

    st.divider()

    # ── Salva ──────────────────────────────────────────────────────────────────
    if st.button("💾 Salva impostazioni", type="primary"):
        with get_session(engine) as session2:
            set_user_setting(session2, "date_display_format", _DATE_FORMAT_OPTIONS[date_label])
            set_user_setting(session2, "amount_decimal_sep", _DECIMAL_SEP_OPTIONS[dec_label])
            set_user_setting(session2, "amount_thousands_sep", _THOUSANDS_SEP_OPTIONS[thou_label])
            set_user_setting(session2, "description_language", _LANGUAGE_OPTIONS[lang_label])
            set_user_setting(session2, "llm_backend", backend)
            set_user_setting(session2, "ollama_base_url", ollama_url)
            set_user_setting(session2, "ollama_model", ollama_model)
            set_user_setting(session2, "openai_api_key", openai_key)
            set_user_setting(session2, "openai_model", openai_model)
            set_user_setting(session2, "anthropic_api_key", anthropic_key)
            set_user_setting(session2, "anthropic_model", anthropic_model)
            session2.commit()

        # Sync session_state so rest of app picks up changes immediately
        st.session_state["llm_backend"] = backend
        st.session_state["ollama_base_url"] = ollama_url

        st.success("Impostazioni salvate.")
        logger.info(f"settings_page: saved backend={backend!r} ollama_url={ollama_url!r}")
        st.rerun()
