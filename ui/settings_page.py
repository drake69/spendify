"""Settings page — persistent user preferences (locale, language, LLM)."""
from __future__ import annotations

import json

import streamlit as st

from services.settings_service import SettingsService
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
    "OpenAI-compatible (Groq, Together AI, Google AI Studio…)": "openai_compatible",
}


def _key_for(options: dict, value: str) -> str:
    """Return the display label whose value matches, or first key as fallback."""
    for label, v in options.items():
        if v == value:
            return label
    return next(iter(options))


def render_settings_page(engine):
    st.header("⚙️ Impostazioni")

    cfg_svc = SettingsService(engine)
    settings = cfg_svc.get_all()

    # Sync session_state immediately so other pages (upload, ledger) see current values
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

    _owner_list = [n.strip() for n in owner_names_raw.split(",") if n.strip()]
    use_owner_giroconto = st.toggle(
        "Usa nomi titolari per identificare giroconti",
        value=settings.get("use_owner_names_giroconto", "false").lower() == "true",
        disabled=not _owner_list,
        help=(
            "Se attivo, le transazioni la cui descrizione contiene un nome titolare "
            "vengono marcate automaticamente come giroconto."
        ),
    )

    st.divider()

    # ── Contesti di vita ───────────────────────────────────────────────────────
    st.subheader("🌍 Contesti di vita")
    st.caption(
        "Definisci i contesti entro cui classificare le transazioni "
        "(es. Quotidianità, Lavoro, Vacanza). "
        "Puoi assegnare un contesto a ogni transazione dal Registro."
    )

    try:
        _ctx_list: list[str] = json.loads(settings.get("contexts", '["Quotidianità", "Lavoro", "Vacanza"]'))
    except Exception:
        _ctx_list = ["Quotidianità", "Lavoro", "Vacanza"]

    if "settings_contexts" not in st.session_state:
        st.session_state["settings_contexts"] = list(_ctx_list)

    ctx_to_remove = None
    for i, ctx in enumerate(st.session_state["settings_contexts"]):
        cc1, cc2 = st.columns([5, 1])
        with cc1:
            new_val = st.text_input(
                f"Contesto {i + 1}", value=ctx, key=f"ctx_val_{i}", label_visibility="collapsed"
            )
            st.session_state["settings_contexts"][i] = new_val.strip()
        with cc2:
            if st.button("🗑️", key=f"ctx_del_{i}", help="Rimuovi contesto"):
                ctx_to_remove = i

    if ctx_to_remove is not None:
        st.session_state["settings_contexts"].pop(ctx_to_remove)
        st.rerun()

    with st.form("new_ctx_form", clear_on_submit=True):
        nc1, nc2 = st.columns([4, 1])
        new_ctx = nc1.text_input("Nuovo contesto", placeholder="es. Sport", label_visibility="collapsed")
        if nc2.form_submit_button("➕ Aggiungi"):
            val = new_ctx.strip()
            if val and val not in st.session_state["settings_contexts"]:
                st.session_state["settings_contexts"].append(val)
                st.rerun()

    st.divider()

    # ── Import ─────────────────────────────────────────────────────────────────
    st.subheader("📥 Importazione")

    import_test_mode = st.toggle(
        "Modalità test (solo prime 20 righe per file)",
        value=settings.get("import_test_mode", "false").lower() == "true",
        help="Importa solo le prime 20 righe di ogni file per verificare rapidamente la classificazione dello schema.",
    )
    if import_test_mode:
        st.caption("⚠️ Modalità test attiva — solo le prime 20 righe verranno elaborate.")

    max_tx_amount = st.number_input(
        "Importo massimo transazione (€)",
        min_value=1_000,
        max_value=100_000_000,
        value=int(settings.get("max_transaction_amount", "1000000")),
        step=10_000,
        help=(
            "Colonne con valore mediano superiore a questa soglia vengono scartate come "
            "colonne importo (es. colonne 'Riferimento' con ID numerici). "
            "Aumenta solo se hai transazioni reali sopra €1.000.000."
        ),
    )

    st.divider()

    # ── Conti bancari ──────────────────────────────────────────────────────────
    st.subheader("🏦 Conti bancari")
    st.caption(
        "Definisci i tuoi conti bancari. Il nome del conto viene usato come chiave "
        "stabile per il dedup delle transazioni (indipendente dal nome del file importato). "
        "Associa ogni file al conto corretto nella pagina Import."
    )

    _accounts = cfg_svc.get_accounts()

    with st.form("new_account_form", clear_on_submit=True):
        col_name, col_bank, col_btn = st.columns([2, 2, 1])
        new_acc_name = col_name.text_input("Nome conto", placeholder="Conto corrente POPSO")
        new_acc_bank = col_bank.text_input("Banca (opzionale)", placeholder="Banca Popolare di Sondrio")
        if col_btn.form_submit_button("➕ Aggiungi", width="stretch"):
            if new_acc_name.strip():
                try:
                    cfg_svc.create_account(new_acc_name, new_acc_bank or "")
                    st.success(f"Conto '{new_acc_name}' aggiunto.")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
            else:
                st.warning("Il nome del conto non può essere vuoto.")

    if _accounts:
        for acc in _accounts:
            c1, c2, c3 = st.columns([3, 3, 1])
            c1.markdown(f"**{acc.name}**")
            c2.caption(acc.bank_name or "—")
            if c3.button("🗑️", key=f"del_acc_{acc.id}", help="Elimina conto"):
                cfg_svc.delete_account(acc.id)
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
        compat_base_url = settings.get("compat_base_url", "")
        compat_api_key  = settings.get("compat_api_key", "")
        compat_model    = settings.get("compat_model", "")

    elif backend == "openai_compatible":
        st.caption("Compatibile con qualsiasi API che esponga `/v1/chat/completions` (Groq, Together AI, Google AI Studio, ecc.)")
        col_url, col_key, col_model = st.columns([2, 2, 1])
        with col_url:
            compat_base_url = st.text_input(
                "Base URL",
                value=settings.get("compat_base_url", ""),
                placeholder="https://api.groq.com/openai/v1",
            )
        with col_key:
            compat_api_key = st.text_input(
                "API Key",
                type="password",
                value=settings.get("compat_api_key", ""),
                placeholder="gsk_...",
            )
        with col_model:
            compat_model = st.text_input(
                "Modello",
                value=settings.get("compat_model", ""),
                placeholder="gemma3-12b-it",
            )
        ollama_url      = settings.get("ollama_base_url", "http://localhost:11434")
        ollama_model    = settings.get("ollama_model", "gemma3:12b")
        openai_key      = settings.get("openai_api_key", "")
        openai_model    = settings.get("openai_model", "gpt-4o-mini")
        anthropic_key   = settings.get("anthropic_api_key", "")
        anthropic_model = settings.get("anthropic_model", "claude-3-5-haiku-20241022")

    if backend != "openai_compatible":
        compat_base_url = settings.get("compat_base_url", "")
        compat_api_key  = settings.get("compat_api_key", "")
        compat_model    = settings.get("compat_model", "")

    st.divider()

    # ── Salva ──────────────────────────────────────────────────────────────────
    if st.button("💾 Salva impostazioni", type="primary"):
        _ctx_clean = [c for c in st.session_state.get("settings_contexts", _ctx_list) if c]
        cfg_svc.set_bulk({
            "date_display_format":    _DATE_FORMAT_OPTIONS[date_label],
            "amount_decimal_sep":     _DECIMAL_SEP_OPTIONS[dec_label],
            "amount_thousands_sep":   _THOUSANDS_SEP_OPTIONS[thou_label],
            "description_language":   _LANGUAGE_OPTIONS[lang_label],
            "giroconto_mode":         _GIROCONTO_OPTIONS[giroconto_label],
            "llm_backend":            backend,
            "ollama_base_url":        ollama_url,
            "ollama_model":           ollama_model,
            "openai_api_key":         openai_key,
            "openai_model":           openai_model,
            "anthropic_api_key":      anthropic_key,
            "anthropic_model":        anthropic_model,
            "compat_base_url":        compat_base_url,
            "compat_api_key":         compat_api_key,
            "compat_model":           compat_model,
            "owner_names":            owner_names_raw.strip(),
            "use_owner_names_giroconto": "true" if use_owner_giroconto else "false",
            "import_test_mode":       "true" if import_test_mode else "false",
            "max_transaction_amount": str(int(max_tx_amount)),
            "contexts":               json.dumps(_ctx_clean, ensure_ascii=False),
        })

        st.session_state["giroconto_mode"] = _GIROCONTO_OPTIONS[giroconto_label]
        st.session_state["llm_backend"] = backend
        st.session_state["ollama_base_url"] = ollama_url
        st.session_state.pop("settings_contexts", None)
        st.success("Impostazioni salvate.")
        logger.info(f"settings_page: saved backend={backend!r} ollama_url={ollama_url!r}")
        st.rerun()
