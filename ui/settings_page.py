"""Settings page — persistent user preferences (locale, language, LLM)."""
from __future__ import annotations

import streamlit as st

from db.models import get_session
from db.repository import get_all_user_settings, set_user_setting, get_accounts, create_account, delete_account
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

_GIROCONTO_OPTIONS = {
    "Mostra (neutral)": "neutral",
    "Escludi dal registro": "exclude",
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

    # Sync session_state immediately so other pages (upload, ledger) see current values
    # even if the user hasn't clicked Save yet.
    st.session_state.setdefault("giroconto_mode", settings.get("giroconto_mode", "neutral"))

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

    # ── Modalità Giroconti ─────────────────────────────────────────────────────
    st.subheader("🔄 Modalità Giroconti")
    st.caption(
        "Definisce come i giroconti (trasferimenti interni tra conti) compaiono nel registro "
        "e nelle analisi. *Mostra* li include come righe neutre; *Escludi* li nasconde."
    )

    giroconto_label = st.radio(
        "Giroconti nel registro",
        list(_GIROCONTO_OPTIONS.keys()),
        index=list(_GIROCONTO_OPTIONS.keys()).index(
            _key_for(_GIROCONTO_OPTIONS, settings.get("giroconto_mode", "neutral"))
        ),
        label_visibility="collapsed",
        horizontal=True,
    )

    st.divider()

    # ── Titolari del conto ─────────────────────────────────────────────────────
    st.subheader("👤 Titolari del conto")
    st.caption(
        "Nomi dei titolari dei conti (separati da virgola). "
        "Vengono rimossi automaticamente dalle descrizioni prima di inviarle a LLM remoti "
        "per proteggere la privacy."
    )
    owner_names_raw = st.text_input(
        "Nomi titolari (separati da virgola)",
        value=settings.get("owner_names", ""),
        placeholder="Mario Rossi, Anna Bianchi",
    )

    st.divider()

    # ── Conti bancari ──────────────────────────────────────────────────────────
    st.subheader("🏦 Conti bancari")
    st.caption(
        "Definisci i tuoi conti bancari. Il nome del conto viene usato come chiave "
        "stabile per il dedup delle transazioni (indipendente dal nome del file importato). "
        "Associa ogni file al conto corretto nella pagina Import."
    )

    with get_session(engine) as _acc_s:
        _accounts = get_accounts(_acc_s)

    # Add new account
    with st.form("new_account_form", clear_on_submit=True):
        col_name, col_bank, col_btn = st.columns([2, 2, 1])
        new_acc_name = col_name.text_input("Nome conto", placeholder="Conto corrente POPSO")
        new_acc_bank = col_bank.text_input("Banca (opzionale)", placeholder="Banca Popolare di Sondrio")
        if col_btn.form_submit_button("➕ Aggiungi", use_container_width=True):
            if new_acc_name.strip():
                try:
                    with get_session(engine) as _s:
                        create_account(_s, new_acc_name, new_acc_bank)
                        _s.commit()
                    st.success(f"Conto '{new_acc_name}' aggiunto.")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
            else:
                st.warning("Il nome del conto non può essere vuoto.")

    # List existing accounts
    if _accounts:
        for acc in _accounts:
            c1, c2, c3 = st.columns([3, 3, 1])
            c1.markdown(f"**{acc.name}**")
            c2.caption(acc.bank_name or "—")
            if c3.button("🗑️", key=f"del_acc_{acc.id}", help="Elimina conto"):
                with get_session(engine) as _s:
                    delete_account(_s, acc.id)
                    _s.commit()
                st.rerun()
    else:
        st.info("Nessun conto configurato. Aggiungine uno per associarlo ai file importati.")

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
                value=settings.get("ollama_base_url", "http://localhost:11434"),
            )
        with col_model:
            ollama_model = st.text_input(
                "Modello",
                value=settings.get("ollama_model", "gemma3:12b"),
            )
        openai_key = settings.get("openai_api_key", "")
        openai_model = settings.get("openai_model", "gpt-4o-mini")
        anthropic_key = settings.get("anthropic_api_key", "")
        anthropic_model = settings.get("anthropic_model", "claude-3-5-haiku-20241022")

    elif backend == "openai":
        col_key, col_model = st.columns([2, 1])
        with col_key:
            openai_key = st.text_input(
                "OpenAI API Key",
                type="password",
                value=settings.get("openai_api_key", ""),
                placeholder="sk-...",
            )
        with col_model:
            openai_model = st.text_input(
                "Modello",
                value=settings.get("openai_model", "gpt-4o-mini"),
            )
        ollama_url = settings.get("ollama_base_url", "http://localhost:11434")
        ollama_model = settings.get("ollama_model", "gemma3:12b")
        anthropic_key = settings.get("anthropic_api_key", "")
        anthropic_model = settings.get("anthropic_model", "claude-3-5-haiku-20241022")

    elif backend == "claude":
        col_key, col_model = st.columns([2, 1])
        with col_key:
            anthropic_key = st.text_input(
                "Anthropic API Key",
                type="password",
                value=settings.get("anthropic_api_key", ""),
                placeholder="sk-ant-...",
            )
        with col_model:
            anthropic_model = st.text_input(
                "Modello",
                value=settings.get("anthropic_model", "claude-3-5-haiku-20241022"),
            )
        ollama_url = settings.get("ollama_base_url", "http://localhost:11434")
        ollama_model = settings.get("ollama_model", "gemma3:12b")
        openai_key = settings.get("openai_api_key", "")
        openai_model = settings.get("openai_model", "gpt-4o-mini")

    st.divider()

    # ── Salva ──────────────────────────────────────────────────────────────────
    if st.button("💾 Salva impostazioni", type="primary"):
        with get_session(engine) as session2:
            set_user_setting(session2, "date_display_format", _DATE_FORMAT_OPTIONS[date_label])
            set_user_setting(session2, "amount_decimal_sep", _DECIMAL_SEP_OPTIONS[dec_label])
            set_user_setting(session2, "amount_thousands_sep", _THOUSANDS_SEP_OPTIONS[thou_label])
            set_user_setting(session2, "description_language", _LANGUAGE_OPTIONS[lang_label])
            set_user_setting(session2, "giroconto_mode", _GIROCONTO_OPTIONS[giroconto_label])
            set_user_setting(session2, "llm_backend", backend)
            set_user_setting(session2, "ollama_base_url", ollama_url)
            set_user_setting(session2, "ollama_model", ollama_model)
            set_user_setting(session2, "openai_api_key", openai_key)
            set_user_setting(session2, "openai_model", openai_model)
            set_user_setting(session2, "anthropic_api_key", anthropic_key)
            set_user_setting(session2, "anthropic_model", anthropic_model)
            set_user_setting(session2, "owner_names", owner_names_raw.strip())
            session2.commit()

        # Sync session_state so rest of app picks up changes immediately
        st.session_state["giroconto_mode"] = _GIROCONTO_OPTIONS[giroconto_label]
        st.session_state["llm_backend"] = backend
        st.session_state["ollama_base_url"] = ollama_url

        st.success("Impostazioni salvate.")
        logger.info(f"settings_page: saved backend={backend!r} ollama_url={ollama_url!r}")
        st.rerun()
