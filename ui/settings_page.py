"""Settings page — persistent user preferences (locale, language, LLM)."""
from __future__ import annotations

import json

import streamlit as st

from services.settings_service import SettingsService
from support.logging import setup_logging
from ui.i18n import t

logger = setup_logging()


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
        with st.spinner(t("settings.download_model_spinner", model=model)):
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
                    status_text.caption(t("settings.download_status", status=status, done=f"{done_mb:.0f}", total=f"{size_mb:.0f}"))
                else:
                    status_text.caption(status)

            progress_bar.progress(1.0)
            st.success(t("settings.ollama.model_ready", model=model))
    except requests.ConnectionError:
        st.error(t("settings.ollama.connection_error", url=base_url))
    except requests.HTTPError as exc:
        st.error(t("settings.ollama.http_error", error=exc))
    except Exception as exc:
        st.error(t("settings.ollama.pull_error", error=exc))


def _autodetect_ctx_llama() -> None:
    """on_change callback: read GGUF context length and update session state."""
    from core.llm_backends import LlamaCppBackend
    path = st.session_state.get("_wgt_llama_path", "")
    if not path:
        try:
            path = LlamaCppBackend._default_model_path()
        except Exception:
            return
    ctx = LlamaCppBackend.read_gguf_context_length(path)
    if ctx:
        st.session_state["_wgt_llama_n_ctx"] = ctx


def _autodetect_ctx_ollama() -> None:
    """on_change callback: query Ollama /api/show for context length."""
    from core.llm_backends import OllamaBackend
    model   = st.session_state.get("_wgt_ollama_model", "")
    base_url = st.session_state.get("_wgt_ollama_url", "http://localhost:11434")
    ctx = OllamaBackend.fetch_context_length(model, base_url)
    st.session_state["_ollama_ctx_detected"] = ctx


def _autodetect_ctx_openai() -> None:
    """on_change callback: lookup known context window for OpenAI models."""
    from core.llm_backends import _KNOWN_CONTEXT
    model = st.session_state.get("_wgt_openai_model", "")
    st.session_state["_openai_ctx_detected"] = _KNOWN_CONTEXT.get(model)


def _autodetect_ctx_claude() -> None:
    """on_change callback: lookup known context window for Claude models."""
    from core.llm_backends import _KNOWN_CONTEXT
    model = st.session_state.get("_wgt_claude_model", "")
    st.session_state["_claude_ctx_detected"] = _KNOWN_CONTEXT.get(model)


def _autodetect_ctx_vllm() -> None:
    """on_change callback: query vLLM /v1/models for context length."""
    from core.llm_backends import VllmBackend
    base_url = st.session_state.get("_wgt_vllm_url", "http://localhost:8000/v1")
    model    = st.session_state.get("_wgt_vllm_model", "")
    ctx = VllmBackend.fetch_context_length(base_url, model)
    st.session_state["_vllm_ctx_detected"] = ctx


def _ctx_caption(ctx: int | None) -> str:
    """Format a detected context length as a Streamlit caption string."""
    if ctx:
        return f"📐 contesto nativo: **{ctx:,}** token"
    return ""


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

        with st.spinner(t("settings.test_llm_spinner")):
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
            st.success(t("settings.test_llm_ok", cat=cat, conf=conf))
            ctx = llm.get_context_info()
            if ctx:
                n_cfg = ctx.get("n_ctx")
                n_max = ctx.get("n_ctx_train")
                parts = []
                if n_cfg:
                    parts.append(t("settings.test_llm_ctx_configured", n=f"{n_cfg:,}"))
                if n_max:
                    parts.append(t("settings.test_llm_ctx_max", n=f"{n_max:,}"))
                if parts:
                    st.info("📐 " + " · ".join(parts))

    except LLMValidationError as exc:
        st.error(t("settings.test_llm_validation_error", error=exc))
    except Exception as exc:
        error_msg = str(exc)
        if "Connection" in error_msg or "refused" in error_msg:
            st.error(t("settings.test_llm_connection_error", error=error_msg))
        elif "401" in error_msg or "auth" in error_msg.lower():
            st.error(t("settings.test_llm_auth_error", error=error_msg))
        else:
            st.error(t("settings.test_llm_generic_error", error=error_msg))


def render_settings_page(engine):
    # ── Build option dicts inside function so t() works at runtime ─────────
    _DATE_FORMAT_OPTIONS = {
        t("settings.date_fmt.dmy"): "%d/%m/%Y",
        t("settings.date_fmt.iso"): "%Y-%m-%d",
        t("settings.date_fmt.mdy"): "%m/%d/%Y",
    }

    _DECIMAL_SEP_OPTIONS = {
        t("settings.decimal_sep.comma"): ",",
        t("settings.decimal_sep.dot"): ".",
    }

    _THOUSANDS_SEP_OPTIONS = {
        t("settings.thousands_sep.dot"): ".",
        t("settings.thousands_sep.comma"): ",",
        t("settings.thousands_sep.space"): " ",
        t("settings.thousands_sep.none"): "",
    }

    _LANGUAGE_OPTIONS = {
        t("settings.lang.it"): "it",
        t("settings.lang.en"): "en",
        t("settings.lang.fr"): "fr",
        t("settings.lang.de"): "de",
    }

    _ACCOUNT_TYPES = {
        t("settings.account_type.bank_account"): "bank_account",
        t("settings.account_type.credit_card"): "credit_card",
        t("settings.account_type.debit_card"): "debit_card",
        t("settings.account_type.prepaid_card"): "prepaid_card",
        t("settings.account_type.savings_account"): "savings_account",
        t("settings.account_type.cash"): "cash",
    }

    _ACCOUNT_TYPE_LABELS = {v: k for k, v in _ACCOUNT_TYPES.items()}

    _GIROCONTO_OPTIONS = {
        t("settings.giroconto.neutral"): "neutral",
        t("settings.giroconto.exclude"): "exclude",
    }

    _BACKEND_OPTIONS = {
        t("settings.backend.llama_cpp"): "local_llama_cpp",
        t("settings.backend.ollama"): "local_ollama",
        t("settings.backend.openai"): "openai",
        t("settings.backend.claude"): "claude",
        t("settings.backend.openai_compatible"): "openai_compatible",
    }

    st.header(t("settings.title"))

    cfg_svc = SettingsService(engine)
    settings = cfg_svc.get_all()

    # Sync session_state immediately so other pages (upload, ledger) see current values
    st.session_state.setdefault("giroconto_mode", settings.get("giroconto_mode", "neutral"))

    # ── Formato visualizzazione ────────────────────────────────────────────────
    st.subheader(t("settings.display_format"))

    date_label = st.selectbox(
        t("settings.date_format"),
        list(_DATE_FORMAT_OPTIONS.keys()),
        index=list(_DATE_FORMAT_OPTIONS.keys()).index(
            _key_for(_DATE_FORMAT_OPTIONS, settings.get("date_display_format", "%d/%m/%Y"))
        ),
    )

    col1, col2 = st.columns(2)
    with col1:
        dec_label = st.selectbox(
            t("settings.decimal_sep"),
            list(_DECIMAL_SEP_OPTIONS.keys()),
            index=list(_DECIMAL_SEP_OPTIONS.keys()).index(
                _key_for(_DECIMAL_SEP_OPTIONS, settings.get("amount_decimal_sep", ","))
            ),
        )
    with col2:
        thou_label = st.selectbox(
            t("settings.thousands_sep"),
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
    st.info(t("settings.preview", date=preview_date, amount=preview_amount))

    st.divider()

    # ── Lingua descrizioni ─────────────────────────────────────────────────────
    st.subheader(t("settings.desc_language"))
    st.caption(t("settings.desc_language_caption"))

    lang_label = st.selectbox(
        t("settings.desc_language_label"),
        list(_LANGUAGE_OPTIONS.keys()),
        index=list(_LANGUAGE_OPTIONS.keys()).index(
            _key_for(_LANGUAGE_OPTIONS, settings.get("description_language", "it"))
        ),
    )

    st.divider()

    # ── Lingua interfaccia (i18n) ──────────────────────────────────────────────
    st.subheader(t("settings.ui_language"))
    st.caption(t("settings.ui_language_caption"))
    from ui.i18n import available_languages
    _ui_langs = available_languages()  # [(code, label), ...]
    _ui_lang_labels = [label for _, label in _ui_langs]
    _ui_lang_codes = [code for code, _ in _ui_langs]
    _current_ui_lang = settings.get("ui_language", "it")
    _ui_lang_idx = _ui_lang_codes.index(_current_ui_lang) if _current_ui_lang in _ui_lang_codes else 0
    ui_lang_label = st.selectbox(
        t("settings.ui_language_label"),
        _ui_lang_labels,
        index=_ui_lang_idx,
    )
    ui_language = _ui_lang_codes[_ui_lang_labels.index(ui_lang_label)]

    st.divider()

    # ── Paese ──────────────────────────────────────────────────────────────────
    st.subheader(t("settings.country"))
    st.caption(t("settings.country_caption"))
    from ui.onboarding_page import _COUNTRIES, _COUNTRY_LABELS, _COUNTRY_CODES, _COUNTRY_BY_NAME
    _none_label = t("settings.country_none")
    _country_options = [_none_label] + _COUNTRY_LABELS
    _current_country = settings.get("country", "")
    _country_idx = (
        _COUNTRY_CODES.index(_current_country) + 1  # +1 per il None iniziale
        if _current_country in _COUNTRY_CODES else 0
    )
    country_sel = st.selectbox(
        t("settings.country_label"),
        _country_options,
        index=_country_idx,
        label_visibility="collapsed",
    )
    country_code = "" if country_sel == _none_label else _COUNTRY_BY_NAME.get(country_sel, "")

    st.divider()

    # ── Modalità Giroconti ─────────────────────────────────────────────────────
    st.subheader(t("settings.giroconto_mode_title"))
    st.caption(t("settings.giroconto_mode_caption"))

    giroconto_label = st.radio(
        t("settings.giroconto_label"),
        list(_GIROCONTO_OPTIONS.keys()),
        index=list(_GIROCONTO_OPTIONS.keys()).index(
            _key_for(_GIROCONTO_OPTIONS, settings.get("giroconto_mode", "neutral"))
        ),
        label_visibility="collapsed",
        horizontal=True,
    )

    st.divider()

    # ── Titolari del conto ─────────────────────────────────────────────────────
    st.subheader(t("settings.owners_title"))
    st.caption(t("settings.owners_caption"))
    owner_names_raw = st.text_input(
        t("settings.owners_label"),
        value=settings.get("owner_names", ""),
        placeholder=t("settings.owners_placeholder"),
    )

    _owner_list = [n.strip() for n in owner_names_raw.split(",") if n.strip()]
    use_owner_giroconto = st.toggle(
        t("settings.use_owners_giroconto"),
        value=settings.get("use_owner_names_giroconto", "false").lower() == "true",
        disabled=not _owner_list,
        help=t("settings.use_owners_giroconto_help"),
    )

    st.divider()

    # ── Contesti di vita ───────────────────────────────────────────────────────
    st.subheader(t("settings.contexts_title"))
    st.caption(t("settings.contexts_caption"))

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
            if st.button("🗑️", key=f"ctx_del_{i}", help=t("settings.remove_context")):
                ctx_to_remove = i

    if ctx_to_remove is not None:
        st.session_state["settings_contexts"].pop(ctx_to_remove)
        st.rerun()

    with st.form("new_ctx_form", clear_on_submit=True):
        nc1, nc2 = st.columns([4, 1])
        new_ctx = nc1.text_input(t("settings.new_context"), placeholder=t("settings.new_context_placeholder"), label_visibility="collapsed")
        if nc2.form_submit_button(t("settings.add_context")):
            val = new_ctx.strip()
            if val and val not in st.session_state["settings_contexts"]:
                st.session_state["settings_contexts"].append(val)
                st.rerun()

    st.divider()

    # ── Import ─────────────────────────────────────────────────────────────────
    st.subheader(t("settings.import_title"))

    force_schema_import = st.toggle(
        t("settings.force_schema"),
        value=settings.get("force_schema_import", "false").lower() == "true",
        help=t("settings.force_schema_help"),
    )
    if force_schema_import:
        st.caption(t("settings.force_schema_active"))

    import_test_mode = st.toggle(
        t("settings.test_mode"),
        value=settings.get("import_test_mode", "false").lower() == "true",
        help=t("settings.test_mode_help"),
    )
    if import_test_mode:
        st.caption(t("settings.test_mode_active"))

    max_tx_amount = st.number_input(
        t("settings.max_tx_amount"),
        min_value=1_000,
        max_value=100_000_000,
        value=int(settings.get("max_transaction_amount", "1000000")),
        step=10_000,
        help=t("settings.max_tx_amount_help"),
    )

    st.divider()

    # ── Conti bancari ──────────────────────────────────────────────────────────
    st.subheader(t("settings.accounts_title"))
    st.caption(t("settings.accounts_caption"))

    _accounts = cfg_svc.get_accounts()

    with st.form("new_account_form", clear_on_submit=True):
        col_name, col_bank, col_type, col_btn = st.columns([2, 2, 2, 1])
        new_acc_name = col_name.text_input(t("settings.account_name"), placeholder="Conto corrente POPSO")
        new_acc_bank = col_bank.text_input(t("settings.account_bank"), placeholder="Banca Popolare di Sondrio")
        new_acc_type_label = col_type.selectbox(
            t("settings.account_type"), list(_ACCOUNT_TYPES.keys()), index=0,
        )
        if col_btn.form_submit_button(t("settings.add_account"), width="stretch"):
            if new_acc_name.strip():
                try:
                    cfg_svc.create_account(
                        new_acc_name, new_acc_bank or "",
                        account_type=_ACCOUNT_TYPES[new_acc_type_label],
                    )
                    st.success(t("settings.account_added", name=new_acc_name))
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
            else:
                st.warning(t("settings.account_name_empty"))

    if _accounts:
        for acc in _accounts:
            c1, c2, c3, c4, c5 = st.columns([3, 2, 2, 1, 1])
            c1.markdown(f"**{acc.name}**")
            c2.caption(acc.bank_name or "—")
            c3.caption(_ACCOUNT_TYPE_LABELS.get(acc.account_type or "", acc.account_type or "—"))
            edit_key = f"edit_acc_{acc.id}"
            if c4.button("✏️", key=edit_key, help=t("settings.edit_account")):
                st.session_state[f"_editing_acc"] = acc.id
            if c5.button("🗑️", key=f"del_acc_{acc.id}", help=t("settings.delete_account")):
                cfg_svc.delete_account(acc.id)
                st.rerun()

            if st.session_state.get("_editing_acc") == acc.id:
                with st.container(border=True):
                    ec1, ec2, ec3 = st.columns(3)
                    edited_name = ec1.text_input(
                        t("settings.account_name"), value=acc.name, key=f"ren_name_{acc.id}"
                    )
                    edited_bank = ec2.text_input(
                        t("settings.account_bank"), value=acc.bank_name or "", key=f"ren_bank_{acc.id}"
                    )
                    _current_type = acc.account_type or "bank_account"
                    _type_labels = list(_ACCOUNT_TYPES.keys())
                    _type_values = list(_ACCOUNT_TYPES.values())
                    _type_idx = _type_values.index(_current_type) if _current_type in _type_values else 0
                    edited_type_label = ec3.selectbox(
                        t("settings.account_type"), _type_labels, index=_type_idx, key=f"ren_type_{acc.id}"
                    )
                    bc1, bc2 = st.columns(2)
                    if bc1.button(t("common.save"), key=f"save_acc_{acc.id}", type="primary"):
                        if not edited_name.strip():
                            st.error(t("settings.account_name_empty"))
                        else:
                            try:
                                n = cfg_svc.rename_account(
                                    acc.id, edited_name, edited_bank or None,
                                    new_account_type=_ACCOUNT_TYPES[edited_type_label],
                                )
                                st.session_state.pop("_editing_acc", None)
                                st.success(t("settings.account_renamed", n=n))
                                st.rerun()
                            except ValueError as e:
                                st.error(str(e))
                    if bc2.button(t("common.cancel"), key=f"cancel_acc_{acc.id}"):
                        st.session_state.pop("_editing_acc", None)
                        st.rerun()
    else:
        st.info(t("settings.no_accounts"))

    st.divider()

    # ── Schema cache ──────────────────────────────────────────────────────────
    st.subheader(t("settings.schema_cache_title"))
    st.caption(t("settings.schema_cache_caption"))
    if st.button(t("settings.clear_schemas_btn"), help=t("settings.clear_schemas_help")):
        n = cfg_svc.delete_all_schemas()
        st.success(t("settings.schemas_cleared", n=n))
        st.rerun()

    st.divider()

    # ── Configurazione LLM ─────────────────────────────────────────────────────
    st.subheader(t("settings.llm_config_title"))

    backend_label = st.selectbox(
        t("settings.llm_backend"),
        list(_BACKEND_OPTIONS.keys()),
        index=list(_BACKEND_OPTIONS.keys()).index(
            _key_for(_BACKEND_OPTIONS, settings.get("llm_backend", "local_llama_cpp"))
        ),
    )
    backend = _BACKEND_OPTIONS[backend_label]

    # Initialise llama_cpp settings variables (used in save)
    llama_cpp_model_path = settings.get("llama_cpp_model_path", "")
    llama_cpp_n_gpu_layers = settings.get("llama_cpp_n_gpu_layers", "-1")
    # Seed session state for n_ctx only on first load (not every rerun)
    if "_wgt_llama_n_ctx" not in st.session_state:
        st.session_state["_wgt_llama_n_ctx"] = int(settings.get("llama_cpp_n_ctx", "4096"))
    llama_cpp_n_ctx = st.session_state["_wgt_llama_n_ctx"]

    if backend == "local_llama_cpp":
        st.caption(t("settings.llama_cpp.caption"))
        from core.llm_backends import LlamaCppBackend, DEFAULT_GGUF_MODELS

        llama_cpp_model_path = st.text_input(
            t("settings.llama_cpp.model_path"),
            value=settings.get("llama_cpp_model_path", ""),
            placeholder=t("settings.llama_cpp.model_path_placeholder"),
            help=t("settings.llama_cpp.model_path_help"),
            key="_wgt_llama_path",
            on_change=_autodetect_ctx_llama,
        )
        llama_cpp_n_gpu_layers = st.text_input(
            t("settings.llama_cpp.gpu_layers"),
            value=settings.get("llama_cpp_n_gpu_layers", "-1"),
            help=t("settings.llama_cpp.gpu_layers_help"),
        )
        llama_cpp_n_ctx = st.number_input(
            t("settings.llama_cpp.n_ctx"),
            min_value=512,
            max_value=131072,
            step=512,
            help=t("settings.llama_cpp.n_ctx_help"),
            key="_wgt_llama_n_ctx",
        )

        # Show locally available models + allow selecting one (auto-fills path + n_ctx)
        local_models = LlamaCppBackend.list_local_models()
        if local_models:
            st.markdown(t("settings.llama_cpp.local_models"))

            def _on_local_model_select():
                idx = st.session_state.get("_wgt_llama_local_select")
                if idx is not None:
                    m = local_models[idx]
                    st.session_state["_wgt_llama_path"] = m["path"]
                    ctx = LlamaCppBackend.read_gguf_context_length(m["path"])
                    if ctx:
                        st.session_state["_wgt_llama_n_ctx"] = ctx

            local_labels = [f"{m['name']}  ({m['size_gb']} GB)" for m in local_models]
            st.selectbox(
                t("settings.llama_cpp.select_local"),
                options=range(len(local_models)),
                format_func=lambda i: local_labels[i],
                index=None,
                placeholder=t("settings.llama_cpp.select_local_placeholder"),
                key="_wgt_llama_local_select",
                on_change=_on_local_model_select,
                label_visibility="collapsed",
            )
            for m in local_models:
                st.caption(f"  {m['name']}  ({m['size_gb']} GB) — `{m['path']}`")
        else:
            st.info(t("settings.llama_cpp.no_models"))

        # Download model
        st.markdown(t("settings.llama_cpp.download_title"))
        model_options = {
            f"{v['description']}  ({v['size_gb']} GB)": k
            for k, v in DEFAULT_GGUF_MODELS.items()
        }
        selected_model_label = st.selectbox(
            t("settings.llama_cpp.download_model"),
            list(model_options.keys()),
            key="llama_cpp_download_select",
            label_visibility="collapsed",
        )
        selected_model_key = model_options[selected_model_label]
        selected_model_info = DEFAULT_GGUF_MODELS[selected_model_key]

        btn_col1, btn_col2, btn_col3 = st.columns([1, 1, 2])
        with btn_col1:
            if st.button(t("settings.llama_cpp.download_btn"), key="llama_cpp_download"):
                try:
                    progress_bar = st.progress(0.0)
                    status_text = st.empty()
                    def _progress(downloaded, total):
                        if total > 0:
                            pct = downloaded / total
                            progress_bar.progress(min(pct, 1.0))
                            status_text.caption(
                                t("settings.llama_cpp.downloading", done=f"{downloaded / (1024**2):.0f}", total=f"{total / (1024**2):.0f}")
                            )
                    with st.spinner(f"Download {selected_model_key}…"):
                        dest = LlamaCppBackend.download_model(
                            selected_model_info["url"],
                            progress_callback=_progress,
                        )
                    progress_bar.progress(1.0)
                    st.success(t("settings.llama_cpp.downloaded", path=dest))
                except Exception as exc:
                    st.error(t("settings.llama_cpp.download_error", error=exc))
        with btn_col2:
            if st.button(t("settings.test_llm_btn"), key="test_llama_cpp"):
                test_kwargs = {}
                if llama_cpp_model_path:
                    test_kwargs["model_path"] = llama_cpp_model_path
                try:
                    n_gpu = int(llama_cpp_n_gpu_layers)
                except ValueError:
                    n_gpu = -1
                test_kwargs["n_gpu_layers"] = n_gpu
                test_kwargs["n_ctx"] = int(llama_cpp_n_ctx)
                _do_llm_test(backend, **test_kwargs)

        # Preserve other backends' settings
        ollama_url      = settings.get("ollama_base_url", "http://localhost:11434")
        ollama_model    = settings.get("ollama_model", "gemma3:12b")
        openai_key      = settings.get("openai_api_key", "")
        openai_model    = settings.get("openai_model", "gpt-4o-mini")
        anthropic_key   = settings.get("anthropic_api_key", "")
        anthropic_model = settings.get("anthropic_model", "claude-haiku-4-5-20251001")

    elif backend == "local_ollama":
        col_url, col_model = st.columns([2, 1])
        with col_url:
            ollama_url = st.text_input(
                t("settings.ollama.url"),
                value=settings.get("ollama_base_url", "http://localhost:11434"),
                key="_wgt_ollama_url",
                on_change=_autodetect_ctx_ollama,
            )
        with col_model:
            ollama_model = st.text_input(
                t("settings.ollama.model"),
                value=settings.get("ollama_model", "gemma3:12b"),
                key="_wgt_ollama_model",
                on_change=_autodetect_ctx_ollama,
            )
        _ollama_ctx = st.session_state.get("_ollama_ctx_detected")
        if _ollama_ctx:
            st.caption(_ctx_caption(_ollama_ctx))

        # ── Pull model + Test LLM ────────────────────────────────────────────
        btn_col1, btn_col2, btn_col3 = st.columns([1, 1, 2])
        with btn_col1:
            if st.button(t("settings.ollama.pull_btn"), help=t("settings.ollama.pull_help")):
                _do_ollama_pull(ollama_url, ollama_model)
        with btn_col2:
            if st.button(t("settings.test_llm_btn"), help=t("settings.test_llm_help")):
                _do_llm_test(backend, ollama_url, ollama_model)

        openai_key = settings.get("openai_api_key", "")
        openai_model = settings.get("openai_model", "gpt-4o-mini")
        anthropic_key = settings.get("anthropic_api_key", "")
        anthropic_model = settings.get("anthropic_model", "claude-haiku-4-5-20251001")

    elif backend == "openai":
        col_key, col_model = st.columns([2, 1])
        with col_key:
            openai_key = st.text_input(
                t("settings.openai.api_key"),
                type="password",
                value=settings.get("openai_api_key", ""),
                placeholder="sk-...",
            )
        with col_model:
            openai_model = st.text_input(
                t("settings.openai.model"),
                value=settings.get("openai_model", "gpt-4o-mini"),
                key="_wgt_openai_model",
                on_change=_autodetect_ctx_openai,
            )
        _openai_ctx = st.session_state.get("_openai_ctx_detected")
        if _openai_ctx:
            st.caption(_ctx_caption(_openai_ctx))
        if st.button(t("settings.test_llm_btn"), key="test_openai", help=t("settings.test_llm_help")):
            _do_llm_test(backend, api_key=openai_key, model=openai_model)
        ollama_url = settings.get("ollama_base_url", "http://localhost:11434")
        ollama_model = settings.get("ollama_model", "gemma3:12b")
        anthropic_key = settings.get("anthropic_api_key", "")
        anthropic_model = settings.get("anthropic_model", "claude-haiku-4-5-20251001")

    elif backend == "claude":
        col_key, col_model = st.columns([2, 1])
        with col_key:
            anthropic_key = st.text_input(
                t("settings.anthropic.api_key"),
                type="password",
                value=settings.get("anthropic_api_key", ""),
                placeholder="sk-ant-...",
            )
        with col_model:
            anthropic_model = st.text_input(
                t("settings.anthropic.model"),
                value=settings.get("anthropic_model", "claude-haiku-4-5-20251001"),
                key="_wgt_claude_model",
                on_change=_autodetect_ctx_claude,
            )
        _claude_ctx = st.session_state.get("_claude_ctx_detected")
        if _claude_ctx:
            st.caption(_ctx_caption(_claude_ctx))
        if st.button(t("settings.test_llm_btn"), key="test_claude", help=t("settings.test_llm_help")):
            _do_llm_test(backend, api_key=anthropic_key, model=anthropic_model)
        ollama_url = settings.get("ollama_base_url", "http://localhost:11434")
        ollama_model = settings.get("ollama_model", "gemma3:12b")
        openai_key = settings.get("openai_api_key", "")
        openai_model = settings.get("openai_model", "gpt-4o-mini")
        compat_base_url = settings.get("compat_base_url", "")
        compat_api_key  = settings.get("compat_api_key", "")
        compat_model    = settings.get("compat_model", "")

    elif backend == "openai_compatible":
        st.caption(t("settings.compat.caption"))
        col_url, col_key, col_model = st.columns([2, 2, 1])
        with col_url:
            compat_base_url = st.text_input(
                t("settings.compat.base_url"),
                value=settings.get("compat_base_url", ""),
                placeholder="https://api.groq.com/openai/v1",
            )
        with col_key:
            compat_api_key = st.text_input(
                t("settings.compat.api_key"),
                type="password",
                value=settings.get("compat_api_key", ""),
                placeholder="gsk_...",
            )
        with col_model:
            compat_model = st.text_input(
                t("settings.compat.model"),
                value=settings.get("compat_model", ""),
                placeholder="gemma3-12b-it",
            )
        if st.button(t("settings.test_llm_btn"), key="test_compat", help=t("settings.test_llm_help")):
            _do_llm_test(backend, base_url=compat_base_url, api_key=compat_api_key, model=compat_model)
        ollama_url      = settings.get("ollama_base_url", "http://localhost:11434")
        ollama_model    = settings.get("ollama_model", "gemma3:12b")
        openai_key      = settings.get("openai_api_key", "")
        openai_model    = settings.get("openai_model", "gpt-4o-mini")
        anthropic_key   = settings.get("anthropic_api_key", "")
        anthropic_model = settings.get("anthropic_model", "claude-haiku-4-5-20251001")

    if backend != "openai_compatible":
        compat_base_url = settings.get("compat_base_url", "")
        compat_api_key  = settings.get("compat_api_key", "")
        compat_model    = settings.get("compat_model", "")

    st.divider()

    # ── Profili rapidi (sezione nascosta per power user) ─────────────────────
    with st.expander(t("settings.power_user_title"), expanded=False):
        st.caption(t("settings.power_user_caption"))
        if st.button(t("settings.power_user_btn"), key="apply_nerd_profile"):
            # Force schema import — no review popup
            force_schema_import = True
            # Test mode off — process all rows
            import_test_mode = False
            # Max transaction amount — high ceiling
            max_tx_amount = 10_000_000
            st.success(t("settings.power_user_applied"))

    st.divider()

    # ── Salva ──────────────────────────────────────────────────────────────────
    if st.button(t("settings.save_btn"), type="primary"):
        _ctx_clean = [c for c in st.session_state.get("settings_contexts", _ctx_list) if c]
        cfg_svc.set_bulk({
            "date_display_format":    _DATE_FORMAT_OPTIONS[date_label],
            "amount_decimal_sep":     _DECIMAL_SEP_OPTIONS[dec_label],
            "amount_thousands_sep":   _THOUSANDS_SEP_OPTIONS[thou_label],
            "description_language":   _LANGUAGE_OPTIONS[lang_label],
            "ui_language":            ui_language,
            "country":                country_code,
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
            "llama_cpp_n_ctx":        str(int(llama_cpp_n_ctx)),
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
        # invalidate chatbot so it re-initialises with the new backend
        st.session_state.pop("chatbot", None)
        st.success(t("settings.saved"))
        logger.info(f"settings_page: saved backend={backend!r} ollama_url={ollama_url!r}")
        st.rerun()

    # ── Reset tassonomia ───────────────────────────────────────────────────────
    st.divider()
    with st.expander(t("settings.reset_taxonomy_title"), expanded=False):
        st.warning(t("settings.reset_taxonomy_warning"))
        lang_options = cfg_svc.get_default_taxonomy_languages()   # [(code, label)]
        lang_labels  = [label for _, label in lang_options]
        lang_codes   = [code  for code, _ in lang_options]
        current_lang = settings.get("description_language", "it")
        default_idx  = lang_codes.index(current_lang) if current_lang in lang_codes else 0
        reset_lang_label = st.selectbox(
            t("settings.reset_taxonomy_lang_label"),
            options=lang_labels,
            index=default_idx,
            key="settings_reset_tax_lang",
        )
        reset_lang_code = lang_codes[lang_labels.index(reset_lang_label)]
        confirm_reset = st.checkbox(
            t("settings.reset_taxonomy_confirm"),
            key="settings_reset_tax_confirm",
        )
        if st.button(
            t("settings.reset_taxonomy_btn"),
            type="secondary",
            disabled=not confirm_reset,
            key="settings_reset_tax_btn",
        ):
            n = cfg_svc.apply_default_taxonomy(reset_lang_code)
            st.success(t("settings.reset_taxonomy_applied", lang=reset_lang_label, n=n))
            logger.info(f"settings_page: reset taxonomy lang={reset_lang_code!r} categories={n}")
            st.rerun()
