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

_ACCOUNT_TYPES = {
    "Conto corrente": "bank_account",
    "Carta di credito": "credit_card",
    "Carta di debito": "debit_card",
    "Carta prepagata": "prepaid_card",
    "Conto risparmio": "savings_account",
    "Contanti": "cash",
}

_ACCOUNT_TYPE_LABELS = {v: k for k, v in _ACCOUNT_TYPES.items()}

_GIROCONTO_OPTIONS = {
    "Mostra (neutral)": "neutral",
    "Escludi dal registro": "exclude",
}

_BACKEND_OPTIONS = {
    "llama.cpp (locale, zero-config)": "local_llama_cpp",
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


def _do_ollama_pull(base_url: str, model: str) -> None:
    """Pull (download/update) an Ollama model with streaming progress."""
    import requests

    base_url = base_url.rstrip("/")
    try:
        with st.spinner(f"Download modello **{model}**…"):
            resp = requests.post(
                f"{base_url}/api/pull",
                json={"name": model, "stream": True},
                stream=True,
                timeout=600,
            )
            resp.raise_for_status()

            progress_bar = st.progress(0.0)
            status_text = st.empty()

            for line in resp.iter_lines():
                if not line:
                    continue
                import json as _json
                data = _json.loads(line)
                status = data.get("status", "")
                total = data.get("total", 0)
                completed = data.get("completed", 0)

                if total > 0:
                    pct = completed / total
                    progress_bar.progress(min(pct, 1.0))
                    size_mb = total / (1024 * 1024)
                    done_mb = completed / (1024 * 1024)
                    status_text.caption(f"{status} — {done_mb:.0f}/{size_mb:.0f} MB")
                else:
                    status_text.caption(status)

            progress_bar.progress(1.0)
            st.success(f"✅ Modello **{model}** pronto.")
    except requests.ConnectionError:
        st.error(f"❌ Impossibile connettersi a Ollama su {base_url}. Verifica che il server sia avviato.")
    except requests.HTTPError as exc:
        st.error(f"❌ Errore dal server Ollama: {exc}")
    except Exception as exc:
        st.error(f"❌ Errore durante il pull: {exc}")


def _do_llm_test(
    backend: str,
    base_url: str = "",
    api_key: str = "",
    model: str = "",
    **extra_kwargs,
) -> None:
    """Send a minimal test prompt to the configured LLM backend."""
    from core.llm_backends import BackendFactory, LLMValidationError

    try:
        kwargs: dict = {"timeout": 15}
        if backend == "local_llama_cpp":
            kwargs.pop("timeout", None)
            kwargs.update(extra_kwargs)
        elif backend == "local_ollama":
            kwargs["base_url"] = base_url
            kwargs["model"] = model
        elif backend == "openai":
            kwargs["api_key"] = api_key
            kwargs["model"] = model
        elif backend == "claude":
            kwargs["api_key"] = api_key
            kwargs["model"] = model
        elif backend == "openai_compatible":
            kwargs["base_url"] = base_url
            kwargs["api_key"] = api_key
            kwargs["model"] = model

        llm = BackendFactory.create(backend, **kwargs)

        with st.spinner("Invio prompt di test…"):
            result = llm.complete_structured(
                system_prompt="Rispondi in JSON.",
                user_prompt='Classifica questa transazione: "PAGAMENTO POS FARMACIA". Rispondi con category e confidence.',
                json_schema={
                    "type": "object",
                    "properties": {
                        "category": {"type": "string"},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    },
                    "required": ["category", "confidence"],
                },
            )
            cat = result.get("category", "?")
            conf = result.get("confidence", "?")
            st.success(f'✅ LLM risponde! Test: "FARMACIA" → **{cat}** (confidence: {conf})')

    except LLMValidationError as exc:
        st.error(f"❌ LLM ha risposto ma con errore di validazione: {exc}")
    except Exception as exc:
        error_msg = str(exc)
        if "Connection" in error_msg or "refused" in error_msg:
            st.error(f"❌ Impossibile connettersi al backend. Verifica URL e che il server sia avviato.\n\n`{error_msg}`")
        elif "401" in error_msg or "auth" in error_msg.lower():
            st.error(f"❌ Errore di autenticazione. Verifica la API key.\n\n`{error_msg}`")
        else:
            st.error(f"❌ Errore: {error_msg}")


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

    # ── Lingua interfaccia (i18n) ──────────────────────────────────────────────
    st.subheader("🌐 Lingua interfaccia")
    st.caption(
        "La lingua in cui vengono mostrati menu, etichette e filtri nell'app. "
        "/ The language used for menus, labels and filters in the app."
    )
    from ui.i18n import available_languages
    _ui_langs = available_languages()  # [(code, label), ...]
    _ui_lang_labels = [label for _, label in _ui_langs]
    _ui_lang_codes = [code for code, _ in _ui_langs]
    _current_ui_lang = settings.get("ui_language", "it")
    _ui_lang_idx = _ui_lang_codes.index(_current_ui_lang) if _current_ui_lang in _ui_lang_codes else 0
    ui_lang_label = st.selectbox(
        "UI Language / Lingua UI",
        _ui_lang_labels,
        index=_ui_lang_idx,
    )
    ui_language = _ui_lang_codes[_ui_lang_labels.index(ui_lang_label)]

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

    force_schema_import = st.toggle(
        "Importa sempre senza conferma schema",
        value=settings.get("force_schema_import", "false").lower() == "true",
        help=(
            "Se attivo, i file vengono importati automaticamente anche quando la classificazione "
            "dello schema ha confidenza bassa. Utile per utenti esperti che preferiscono "
            "velocità a revisione manuale. Un warning viene emesso se la confidenza è < 0.50."
        ),
    )
    if force_schema_import:
        st.caption("⚠️ La revisione schema è disabilitata — tutti i file vengono importati con lo schema rilevato.")

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
        col_name, col_bank, col_type, col_btn = st.columns([2, 2, 2, 1])
        new_acc_name = col_name.text_input("Nome conto", placeholder="Conto corrente POPSO")
        new_acc_bank = col_bank.text_input("Banca (opzionale)", placeholder="Banca Popolare di Sondrio")
        new_acc_type_label = col_type.selectbox(
            "Tipo conto", list(_ACCOUNT_TYPES.keys()), index=0,
        )
        if col_btn.form_submit_button("➕ Aggiungi", width="stretch"):
            if new_acc_name.strip():
                try:
                    cfg_svc.create_account(
                        new_acc_name, new_acc_bank or "",
                        account_type=_ACCOUNT_TYPES[new_acc_type_label],
                    )
                    st.success(f"Conto '{new_acc_name}' aggiunto.")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
            else:
                st.warning("Il nome del conto non può essere vuoto.")

    if _accounts:
        for acc in _accounts:
            c1, c2, c3, c4, c5 = st.columns([3, 2, 2, 1, 1])
            c1.markdown(f"**{acc.name}**")
            c2.caption(acc.bank_name or "—")
            c3.caption(_ACCOUNT_TYPE_LABELS.get(acc.account_type or "", acc.account_type or "—"))
            edit_key = f"edit_acc_{acc.id}"
            if c4.button("✏️", key=edit_key, help="Modifica conto"):
                st.session_state[f"_editing_acc"] = acc.id
            if c5.button("🗑️", key=f"del_acc_{acc.id}", help="Elimina conto"):
                cfg_svc.delete_account(acc.id)
                st.rerun()

            if st.session_state.get("_editing_acc") == acc.id:
                with st.container(border=True):
                    ec1, ec2, ec3 = st.columns(3)
                    edited_name = ec1.text_input(
                        "Nome", value=acc.name, key=f"ren_name_{acc.id}"
                    )
                    edited_bank = ec2.text_input(
                        "Banca", value=acc.bank_name or "", key=f"ren_bank_{acc.id}"
                    )
                    _current_type = acc.account_type or "bank_account"
                    _type_labels = list(_ACCOUNT_TYPES.keys())
                    _type_values = list(_ACCOUNT_TYPES.values())
                    _type_idx = _type_values.index(_current_type) if _current_type in _type_values else 0
                    edited_type_label = ec3.selectbox(
                        "Tipo conto", _type_labels, index=_type_idx, key=f"ren_type_{acc.id}"
                    )
                    bc1, bc2 = st.columns(2)
                    if bc1.button("Salva", key=f"save_acc_{acc.id}", type="primary"):
                        if not edited_name.strip():
                            st.error("Il nome del conto non può essere vuoto.")
                        else:
                            try:
                                n = cfg_svc.rename_account(
                                    acc.id, edited_name, edited_bank or None,
                                    new_account_type=_ACCOUNT_TYPES[edited_type_label],
                                )
                                st.session_state.pop("_editing_acc", None)
                                st.success(
                                    f"Conto rinominato. {n} transazion{'e' if n == 1 else 'i'} aggiornat{'a' if n == 1 else 'e'}."
                                )
                                st.rerun()
                            except ValueError as e:
                                st.error(str(e))
                    if bc2.button("Annulla", key=f"cancel_acc_{acc.id}"):
                        st.session_state.pop("_editing_acc", None)
                        st.rerun()
    else:
        st.info("Nessun conto configurato. Aggiungine uno per associarlo ai file importati.")

    st.divider()

    # ── Schema cache ──────────────────────────────────────────────────────────
    st.subheader("📐 Schema file importati")
    st.caption(
        "Spendify memorizza la struttura dei file importati per velocizzare le importazioni successive. "
        "Se un file viene importato con lo schema sbagliato (es. colonne mancanti), "
        "cancella la cache e reimporta."
    )
    if st.button("🗑️ Cancella tutti gli schemi salvati", help="Rimuove tutti gli schemi dalla cache. Al prossimo import il file verrà rianalizzato."):
        n = cfg_svc.delete_all_schemas()
        st.success(f"Eliminati {n} schemi dalla cache.")
        st.rerun()

    st.divider()

    # ── Configurazione LLM ─────────────────────────────────────────────────────
    st.subheader("🤖 Configurazione LLM")

    backend_label = st.selectbox(
        "Backend LLM",
        list(_BACKEND_OPTIONS.keys()),
        index=list(_BACKEND_OPTIONS.keys()).index(
            _key_for(_BACKEND_OPTIONS, settings.get("llm_backend", "local_llama_cpp"))
        ),
    )
    backend = _BACKEND_OPTIONS[backend_label]

    # Initialise llama_cpp settings variables (used in save)
    llama_cpp_model_path = settings.get("llama_cpp_model_path", "")
    llama_cpp_n_gpu_layers = settings.get("llama_cpp_n_gpu_layers", "-1")

    if backend == "local_llama_cpp":
        st.caption(
            "Backend locale basato su llama.cpp — nessun server esterno richiesto. "
            "Scarica un modello GGUF e Spendify lo usa direttamente."
        )
        from core.llm_backends import LlamaCppBackend, DEFAULT_GGUF_MODELS

        llama_cpp_model_path = st.text_input(
            "Percorso modello GGUF",
            value=settings.get("llama_cpp_model_path", ""),
            placeholder="Vuoto = auto-detect da ~/.spendify/models/",
            help="Lascia vuoto per usare automaticamente il primo file .gguf trovato in ~/.spendify/models/",
        )
        llama_cpp_n_gpu_layers = st.text_input(
            "GPU layers (-1 = auto, 0 = solo CPU)",
            value=settings.get("llama_cpp_n_gpu_layers", "-1"),
            help="-1 offload automatico su GPU, 0 usa solo CPU",
        )

        # Show locally available models
        local_models = LlamaCppBackend.list_local_models()
        if local_models:
            st.markdown("**Modelli disponibili localmente:**")
            for m in local_models:
                st.caption(f"  {m['name']}  ({m['size_gb']} GB) — `{m['path']}`")
        else:
            st.info("Nessun modello GGUF trovato in ~/.spendify/models/. Scaricane uno qui sotto.")

        # Download model
        st.markdown("**Scarica modello GGUF:**")
        model_options = {
            f"{v['description']}  ({v['size_gb']} GB)": k
            for k, v in DEFAULT_GGUF_MODELS.items()
        }
        selected_model_label = st.selectbox(
            "Modello da scaricare",
            list(model_options.keys()),
            key="llama_cpp_download_select",
            label_visibility="collapsed",
        )
        selected_model_key = model_options[selected_model_label]
        selected_model_info = DEFAULT_GGUF_MODELS[selected_model_key]

        btn_col1, btn_col2, btn_col3 = st.columns([1, 1, 2])
        with btn_col1:
            if st.button("⬇️ Scarica modello", key="llama_cpp_download"):
                try:
                    progress_bar = st.progress(0.0)
                    status_text = st.empty()
                    def _progress(downloaded, total):
                        if total > 0:
                            pct = downloaded / total
                            progress_bar.progress(min(pct, 1.0))
                            status_text.caption(
                                f"Scaricamento… {downloaded / (1024**2):.0f}/{total / (1024**2):.0f} MB"
                            )
                    with st.spinner(f"Download {selected_model_key}…"):
                        dest = LlamaCppBackend.download_model(
                            selected_model_info["url"],
                            progress_callback=_progress,
                        )
                    progress_bar.progress(1.0)
                    st.success(f"Modello scaricato in `{dest}`")
                except Exception as exc:
                    st.error(f"Errore durante il download: {exc}")
        with btn_col2:
            if st.button("🧪 Test LLM", key="test_llama_cpp"):
                test_kwargs = {}
                if llama_cpp_model_path:
                    test_kwargs["model_path"] = llama_cpp_model_path
                try:
                    n_gpu = int(llama_cpp_n_gpu_layers)
                except ValueError:
                    n_gpu = -1
                test_kwargs["n_gpu_layers"] = n_gpu
                _do_llm_test(backend, **test_kwargs)

        # Preserve other backends' settings
        ollama_url      = settings.get("ollama_base_url", "http://localhost:11434")
        ollama_model    = settings.get("ollama_model", "gemma3:12b")
        openai_key      = settings.get("openai_api_key", "")
        openai_model    = settings.get("openai_model", "gpt-4o-mini")
        anthropic_key   = settings.get("anthropic_api_key", "")
        anthropic_model = settings.get("anthropic_model", "claude-3-5-haiku-20241022")

    elif backend == "local_ollama":
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

        # ── Pull model + Test LLM ────────────────────────────────────────────
        btn_col1, btn_col2, btn_col3 = st.columns([1, 1, 2])
        with btn_col1:
            if st.button("⬇️ Pull modello", help="Scarica o aggiorna il modello su Ollama"):
                _do_ollama_pull(ollama_url, ollama_model)
        with btn_col2:
            if st.button("🧪 Test LLM", help="Verifica che il backend LLM risponda correttamente"):
                _do_llm_test(backend, ollama_url, ollama_model)

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
        if st.button("🧪 Test LLM", key="test_openai", help="Verifica che il backend LLM risponda"):
            _do_llm_test(backend, api_key=openai_key, model=openai_model)
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
        if st.button("🧪 Test LLM", key="test_claude", help="Verifica che il backend LLM risponda"):
            _do_llm_test(backend, api_key=anthropic_key, model=anthropic_model)
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
        if st.button("🧪 Test LLM", key="test_compat", help="Verifica che il backend LLM risponda"):
            _do_llm_test(backend, base_url=compat_base_url, api_key=compat_api_key, model=compat_model)
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

    # ── Profili rapidi (sezione nascosta per power user) ─────────────────────
    with st.expander("🤓 Sono un duro! — Profilo power user", expanded=False):
        st.caption(
            "Preset per utenti esperti: attiva tutte le opzioni avanzate, "
            "disabilita conferme, massimizza automazione."
        )
        if st.button("⚡ Applica profilo Power User", key="apply_nerd_profile"):
            # Force schema import — no review popup
            force_schema_import = True
            # Test mode off — process all rows
            import_test_mode = False
            # Max transaction amount — high ceiling
            max_tx_amount = 10_000_000
            st.success(
                "Profilo Power User applicato! "
                "• Import forzato senza conferma schema "
                "• Importo max: 10M€ "
                "• Premi 💾 Salva per confermare."
            )

    st.divider()

    # ── Salva ──────────────────────────────────────────────────────────────────
    if st.button("💾 Salva impostazioni", type="primary"):
        _ctx_clean = [c for c in st.session_state.get("settings_contexts", _ctx_list) if c]
        cfg_svc.set_bulk({
            "date_display_format":    _DATE_FORMAT_OPTIONS[date_label],
            "amount_decimal_sep":     _DECIMAL_SEP_OPTIONS[dec_label],
            "amount_thousands_sep":   _THOUSANDS_SEP_OPTIONS[thou_label],
            "description_language":   _LANGUAGE_OPTIONS[lang_label],
            "ui_language":            ui_language,
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
            "llama_cpp_model_path":   llama_cpp_model_path,
            "llama_cpp_n_gpu_layers": str(llama_cpp_n_gpu_layers),
            "owner_names":            owner_names_raw.strip(),
            "use_owner_names_giroconto": "true" if use_owner_giroconto else "false",
            "import_test_mode":       "true" if import_test_mode else "false",
            "force_schema_import":   "true" if force_schema_import else "false",
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

    # ── Reset tassonomia ───────────────────────────────────────────────────────
    st.divider()
    with st.expander("🔄 Reset tassonomia", expanded=False):
        st.warning(
            "⚠️ Questa operazione **sostituisce** tutte le categorie e sottocategorie "
            "con il template di default per la lingua selezionata. "
            "Le transazioni già categorizzate **non** vengono modificate."
        )
        lang_options = cfg_svc.get_default_taxonomy_languages()   # [(code, label)]
        lang_labels  = [label for _, label in lang_options]
        lang_codes   = [code  for code, _ in lang_options]
        current_lang = settings.get("description_language", "it")
        default_idx  = lang_codes.index(current_lang) if current_lang in lang_codes else 0
        reset_lang_label = st.selectbox(
            "Lingua tassonomia da applicare",
            options=lang_labels,
            index=default_idx,
            key="settings_reset_tax_lang",
        )
        reset_lang_code = lang_codes[lang_labels.index(reset_lang_label)]
        confirm_reset = st.checkbox(
            "Confermo: voglio sovrascrivere la tassonomia corrente",
            key="settings_reset_tax_confirm",
        )
        if st.button(
            "🔄 Applica tassonomia default",
            type="secondary",
            disabled=not confirm_reset,
            key="settings_reset_tax_btn",
        ):
            n = cfg_svc.apply_default_taxonomy(reset_lang_code)
            st.success(f"✅ Tassonomia **{reset_lang_label}** applicata — {n} categorie create.")
            logger.info(f"settings_page: reset taxonomy lang={reset_lang_code!r} categories={n}")
            st.rerun()
