"""Onboarding wizard — first-run setup (4 steps).

Step 0 — Lingua & formato      : rilevamento browser, selezione lingua, anteprima formati
Step 1 — Nomi titolari         : nome/i del/dei titolare/i dei conti (essenziale per giroconti)
Step 2 — Conti bancari         : aggiunta di almeno un conto (nome + banca opzionale)
Step 3 — Riepilogo & conferma  : mostra tutto, applica in un'unica transazione

Tutto viene applicato solo al click di "Inizia →" (step 3): nessuna scrittura parziale.
"""
from __future__ import annotations

from datetime import date

import streamlit as st

from services.settings_service import SettingsService
from support.logging import setup_logging

logger = setup_logging()

# ── Session-state keys ────────────────────────────────────────────────────────
_K_STEP     = "_ob_step"
_K_LANG     = "_ob_lang"
_K_NAMES    = "_ob_owner_names"
_K_ACCOUNTS = "_ob_accounts"

_ACCOUNT_TYPES = {
    "Conto corrente": "bank_account",
    "Carta di credito": "credit_card",
    "Carta di debito": "debit_card",
    "Carta prepagata": "prepaid_card",
    "Conto risparmio": "savings_account",
    "Contanti": "cash",
}
_ACCOUNT_TYPE_LABELS_LIST = list(_ACCOUNT_TYPES.keys())

# ── Locale defaults per language ──────────────────────────────────────────────
# (date_display_format, amount_decimal_sep, amount_thousands_sep)
_LOCALE: dict[str, dict] = {
    "it": {"date_display_format": "%d/%m/%Y",  "amount_decimal_sep": ",", "amount_thousands_sep": "."},
    "en": {"date_display_format": "%d/%m/%Y",  "amount_decimal_sep": ".", "amount_thousands_sep": ","},
    "fr": {"date_display_format": "%d/%m/%Y",  "amount_decimal_sep": ",", "amount_thousands_sep": " "},
    "de": {"date_display_format": "%d.%m.%Y",  "amount_decimal_sep": ",", "amount_thousands_sep": "."},
    "es": {"date_display_format": "%d/%m/%Y",  "amount_decimal_sep": ",", "amount_thousands_sep": "."},
}
_DEFAULT_LOCALE = _LOCALE["it"]

# ── UI labels (multi-language minimal set for the wizard itself) ───────────────
_UI: dict[str, dict] = {
    "it": {"next": "Avanti →", "back": "← Indietro", "start": "🚀 Inizia!",
           "step_labels": ["Lingua", "Titolari", "Conti", "Conferma"]},
    "en": {"next": "Next →",   "back": "← Back",      "start": "🚀 Let's go!",
           "step_labels": ["Language", "Owners",   "Accounts", "Confirm"]},
    "fr": {"next": "Suivant →","back": "← Retour",    "start": "🚀 Commencer!",
           "step_labels": ["Langue",   "Titulaires","Comptes",  "Confirmer"]},
    "de": {"next": "Weiter →", "back": "← Zurück",    "start": "🚀 Loslegen!",
           "step_labels": ["Sprache",  "Inhaber",   "Konten",   "Bestätigen"]},
    "es": {"next": "Siguiente →","back": "← Atrás",   "start": "🚀 ¡Empezar!",
           "step_labels": ["Idioma",   "Titulares", "Cuentas",  "Confirmar"]},
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _detect_browser_language(supported: list[str]) -> str:
    """Read Accept-Language header and return the best match in *supported*."""
    try:
        header = st.context.headers.get("Accept-Language", "") or ""
    except Exception:
        return "it"
    for part in header.split(","):
        lang = part.split(";")[0].strip().split("-")[0].lower()
        if lang in supported:
            return lang
    return "it"


def _ui(lang: str) -> dict:
    return _UI.get(lang, _UI["it"])


def _locale(lang: str) -> dict:
    return _LOCALE.get(lang, _DEFAULT_LOCALE)


def _fmt_date(lang: str) -> str:
    fmt = _locale(lang)["date_display_format"]
    return date.today().strftime(fmt)


def _fmt_amount(lang: str) -> str:
    loc = _locale(lang)
    dec, thou = loc["amount_decimal_sep"], loc["amount_thousands_sep"]
    # Format 1234567.89 with the locale separators
    raw = f"{1_234_567.89:.2f}"          # "1234567.89"
    int_part, dec_part = raw.split(".")
    # Group integer part by 3
    groups = []
    while int_part:
        groups.append(int_part[-3:])
        int_part = int_part[:-3]
    return thou.join(reversed(groups)) + dec + dec_part + " €"


def _progress_bar(current: int, labels: list[str]) -> None:
    cols = st.columns(len(labels))
    for i, (col, label) in enumerate(zip(cols, labels)):
        if i < current:
            col.markdown(f"<div style='text-align:center;color:#4CAF50'>✔ {label}</div>",
                         unsafe_allow_html=True)
        elif i == current:
            col.markdown(f"<div style='text-align:center;font-weight:bold'>● {label}</div>",
                         unsafe_allow_html=True)
        else:
            col.markdown(f"<div style='text-align:center;color:#aaa'>○ {label}</div>",
                         unsafe_allow_html=True)
    st.markdown(
        f"<div style='height:4px;background:linear-gradient(to right,"
        f"#4CAF50 {int(current/(len(labels)-1)*100)}%,#ddd {int(current/(len(labels)-1)*100)}%)'>"
        f"</div>", unsafe_allow_html=True
    )
    st.write("")


# ── Steps ─────────────────────────────────────────────────────────────────────

def _step0_language(cfg_svc: SettingsService, lang_options: list[tuple[str, str]]) -> None:
    """Step 0 — Lingua & formato."""
    lang = st.session_state[_K_LANG]
    labels = [lbl for _, lbl in lang_options]
    codes  = [code for code, _ in lang_options]

    st.subheader("🌍 Lingua e formato")
    st.caption(
        "Scegli la lingua della tassonomia e i formati di data e importo. "
        "Abbiamo rilevato la lingua dal tuo browser — puoi cambiarla."
    )

    sel_label = st.radio(
        "Lingua",
        options=labels,
        index=codes.index(lang) if lang in codes else 0,
        horizontal=True,
        key="_ob_lang_radio",
    )
    sel_code = codes[labels.index(sel_label)]
    st.session_state[_K_LANG] = sel_code

    # UI language selector (i18n)
    from ui.i18n import available_languages, set_language
    _ui_langs = available_languages()
    _ui_labels = [lbl for _, lbl in _ui_langs]
    _ui_codes = [c for c, _ in _ui_langs]
    _ui_default = sel_code if sel_code in _ui_codes else "it"
    _ui_idx = _ui_codes.index(st.session_state.get("_ob_ui_lang", _ui_default))
    ui_lang_sel = st.selectbox(
        "🌐 Lingua interfaccia / UI Language",
        _ui_labels,
        index=_ui_idx,
        key="_ob_ui_lang_select",
    )
    _ui_lang_code = _ui_codes[_ui_labels.index(ui_lang_sel)]
    st.session_state["_ob_ui_lang"] = _ui_lang_code
    set_language(_ui_lang_code)

    # Format preview
    loc = _locale(sel_code)
    preview = cfg_svc.get_default_taxonomy_preview(sel_code)
    c1, c2, c3 = st.columns(3)
    c1.metric("Data", _fmt_date(sel_code))
    c2.metric("Importo", _fmt_amount(sel_code))
    c3.metric("Tassonomia", f"{len(preview['expenses'])} cat. spese")

    if preview["expenses"]:
        st.caption(
            f"**Categorie spese ({sel_label}):** "
            + ", ".join(preview["expenses"][:6]) + "…"
        )

    st.write("")
    _, col_next = st.columns([1, 1])
    with col_next:
        if st.button(_ui(sel_code)["next"], type="primary", use_container_width=True):
            st.session_state[_K_STEP] = 1
            st.rerun()


def _step1_owners(lang: str) -> None:
    """Step 1 — Nomi titolari."""
    st.subheader("👤 Nomi titolari")
    st.caption(
        "Inserisci il nome (o i nomi) delle persone titolari dei conti, separati da virgola. "
        "Vengono usati per rilevare automaticamente i **giroconti** tra conti propri "
        "e per pulire le descrizioni delle transazioni."
    )
    st.info(
        "💡 Esempio: `Mario Rossi, Maria Rossi` "
        "— includi tutte le varianti del nome con cui appaiono nei file movimenti."
    )

    names = st.text_input(
        "Nome/i titolare/i",
        value=st.session_state.get(_K_NAMES, ""),
        placeholder="Mario Rossi, Maria Rossi",
        key="_ob_owner_input",
    )
    st.session_state[_K_NAMES] = names

    valid = bool(names.strip())
    if not valid:
        st.warning("⚠️ Inserisci almeno un nome per continuare.")

    col_back, col_next = st.columns(2)
    with col_back:
        if st.button(_ui(lang)["back"], use_container_width=True):
            st.session_state[_K_STEP] = 0
            st.rerun()
    with col_next:
        if st.button(_ui(lang)["next"], type="primary",
                     disabled=not valid, use_container_width=True):
            st.session_state[_K_STEP] = 2
            st.rerun()


def _step2_accounts(lang: str) -> None:
    """Step 2 — Conti bancari."""
    st.subheader("🏦 Conti bancari")
    st.caption(
        "Aggiungi i tuoi conti correnti, carte e conti di investimento. "
        "Il **nome conto** è un'etichetta libera (es. *BancaX corrente*, *Carta Visa*). "
        "Puoi aggiungere altri conti in qualsiasi momento da ⚙️ Impostazioni."
    )

    # Initialize with one empty row if list is empty
    if not st.session_state.get(_K_ACCOUNTS):
        st.session_state[_K_ACCOUNTS] = [{"name": "", "bank": "", "type": "bank_account"}]

    accounts: list[dict] = st.session_state[_K_ACCOUNTS]
    # Backfill 'type' key for accounts created before this field existed
    for _a in accounts:
        _a.setdefault("type", "bank_account")
    to_remove = None

    for i, acc in enumerate(accounts):
        c1, c2, c2b, c3 = st.columns([3, 2, 2, 1])
        acc["name"] = c1.text_input(
            "Nome conto" if i == 0 else "",
            value=acc["name"],
            placeholder="BancaX corrente",
            key=f"_ob_acc_name_{i}",
            label_visibility="visible" if i == 0 else "collapsed",
        )
        acc["bank"] = c2.text_input(
            "Banca (opzionale)" if i == 0 else "",
            value=acc["bank"],
            placeholder="Banca Esempio",
            key=f"_ob_acc_bank_{i}",
            label_visibility="visible" if i == 0 else "collapsed",
        )
        _type_values = list(_ACCOUNT_TYPES.values())
        _cur_type_idx = _type_values.index(acc["type"]) if acc["type"] in _type_values else 0
        _sel_type_label = c2b.selectbox(
            "Tipo" if i == 0 else "",
            _ACCOUNT_TYPE_LABELS_LIST,
            index=_cur_type_idx,
            key=f"_ob_acc_type_{i}",
            label_visibility="visible" if i == 0 else "collapsed",
        )
        acc["type"] = _ACCOUNT_TYPES[_sel_type_label]
        with c3:
            if i == 0:
                st.write("")   # align with label height
                st.write("")
            if len(accounts) > 1:
                if st.button("🗑", key=f"_ob_acc_del_{i}", help="Rimuovi"):
                    to_remove = i

    if to_remove is not None:
        accounts.pop(to_remove)
        st.session_state[_K_ACCOUNTS] = accounts
        st.rerun()

    if st.button("➕ Aggiungi conto", key="_ob_acc_add"):
        accounts.append({"name": "", "bank": "", "type": "bank_account"})
        st.session_state[_K_ACCOUNTS] = accounts
        st.rerun()

    valid_accounts = [a for a in accounts if a["name"].strip()]
    if not valid_accounts:
        st.warning(
            "⚠️ Nessun conto aggiunto. Puoi continuare ma il dedup delle transazioni "
            "potrebbe non essere stabile."
        )

    col_back, col_next = st.columns(2)
    with col_back:
        if st.button(_ui(lang)["back"], use_container_width=True, key="_ob_acc_back"):
            st.session_state[_K_STEP] = 1
            st.rerun()
    with col_next:
        if st.button(_ui(lang)["next"], type="primary",
                     use_container_width=True, key="_ob_acc_next"):
            st.session_state[_K_STEP] = 3
            st.rerun()


def _step3_confirm(cfg_svc: SettingsService, lang_options: list[tuple[str, str]]) -> None:
    """Step 3 — Riepilogo & conferma."""
    lang     = st.session_state[_K_LANG]
    names    = st.session_state.get(_K_NAMES, "").strip()
    accounts = [a for a in st.session_state.get(_K_ACCOUNTS, []) if a["name"].strip()]
    lang_label = next((lbl for code, lbl in lang_options if code == lang), lang)
    loc = _locale(lang)

    st.subheader("✅ Riepilogo")
    st.caption("Controlla le impostazioni e clicca **Inizia** per applicarle.")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**🌍 Lingua & formato**")
        st.markdown(f"- Lingua: **{lang_label}**")
        st.markdown(f"- Data: `{_fmt_date(lang)}`")
        st.markdown(f"- Importo: `{_fmt_amount(lang)}`")

        st.write("")
        st.markdown("**👤 Nomi titolari**")
        for name in [n.strip() for n in names.split(",") if n.strip()]:
            st.markdown(f"- {name}")
        if not names:
            st.caption("*(nessun nome inserito)*")

    with col2:
        _type_labels_inv = {v: k for k, v in _ACCOUNT_TYPES.items()}
        st.markdown("**🏦 Conti bancari**")
        if accounts:
            for acc in accounts:
                bank_note = f" — {acc['bank']}" if acc["bank"].strip() else ""
                type_note = f" ({_type_labels_inv.get(acc.get('type', ''), acc.get('type', ''))})"
                st.markdown(f"- **{acc['name']}**{bank_note}{type_note}")
        else:
            st.caption("*(nessun conto aggiunto)*")

        preview = cfg_svc.get_default_taxonomy_preview(lang)
        st.write("")
        st.markdown("**🗂️ Tassonomia**")
        st.caption(
            f"{len(preview['expenses'])} categorie spese · "
            f"{len(preview['income'])} categorie redditi"
        )

    # ── LLM Model status ───────────────────────────────────────────────
    st.write("")
    st.markdown("**🤖 Modello LLM**")
    from core.model_manager import detect_hw, list_local_models
    from config import get_recommended_model

    _hw = detect_hw()
    _local = list_local_models()
    _rec = get_recommended_model(_hw["ram_gb"])

    if _local:
        st.success(
            f"Modello disponibile: **{_local[0].name}** "
            f"({_local[0].stat().st_size / 1e9:.1f} GB)"
        )
    elif _rec:
        st.info(
            f"HW: {_hw['gpu']} · {_hw['ram_gb']} GB RAM → "
            f"Consigliato: **{_rec.name}** ({_rec.size_mb} MB). "
            f"Verrà scaricato al primo import."
        )
    else:
        st.warning("Nessun modello compatibile trovato. Configura manualmente in Impostazioni.")

    st.divider()

    col_back, _, col_start = st.columns([1, 2, 1])
    with col_back:
        if st.button(_ui(lang)["back"], use_container_width=True, key="_ob_conf_back"):
            st.session_state[_K_STEP] = 2
            st.rerun()

    with col_start:
        if st.button(_ui(lang)["start"], type="primary",
                     use_container_width=True, key="_ob_conf_start"):
            _apply_onboarding(cfg_svc, lang, names, accounts, loc)


def _apply_onboarding(
    cfg_svc: SettingsService,
    lang: str,
    owner_names: str,
    accounts: list[dict],
    loc: dict,
) -> None:
    """Apply all onboarding settings in one shot and mark as done."""
    with st.spinner("Configurazione in corso…"):
        # 1. Taxonomy (also sets description_language)
        n_cats = cfg_svc.apply_default_taxonomy(lang)

        # 2. Locale + owner settings + UI language
        _ui_lang = st.session_state.get("_ob_ui_lang", lang)
        cfg_svc.set_bulk({
            "date_display_format":     loc["date_display_format"],
            "amount_decimal_sep":      loc["amount_decimal_sep"],
            "amount_thousands_sep":    loc["amount_thousands_sep"],
            "owner_names":             owner_names,
            "use_owner_names_giroconto": "true" if owner_names.strip() else "false",
            "ui_language":             _ui_lang,
        })

        # 3. Accounts
        for acc in accounts:
            if acc["name"].strip():
                cfg_svc.create_account(
                    acc["name"].strip(), acc["bank"].strip(),
                    account_type=acc.get("type", "bank_account"),
                )

        # 4. Mark done
        cfg_svc.set_onboarding_done()

    logger.info(
        f"onboarding: applied lang={lang!r} cats={n_cats} "
        f"names={owner_names!r} accounts={len(accounts)}"
    )

    # Clean up session state
    for k in (_K_STEP, _K_LANG, _K_NAMES, _K_ACCOUNTS):
        st.session_state.pop(k, None)

    st.success("✅ Configurazione completata!")
    st.rerun()


# ── Entry point ───────────────────────────────────────────────────────────────

def render_onboarding_page(engine) -> None:
    cfg_svc = SettingsService(engine)

    # Build language list from taxonomy_default table
    lang_options = cfg_svc.get_default_taxonomy_languages()  # [(code, label)]
    if not lang_options:
        st.error("Nessuna tassonomia default trovata nel database.")
        return
    supported_codes = [code for code, _ in lang_options]

    # ── Initialise session state (only on very first render) ──────────────────
    if _K_STEP not in st.session_state:
        detected = _detect_browser_language(supported_codes)
        st.session_state[_K_STEP]     = 0
        st.session_state[_K_LANG]     = detected
        st.session_state[_K_NAMES]    = ""
        st.session_state[_K_ACCOUNTS] = [{"name": "", "bank": "", "type": "bank_account"}]

    step = st.session_state[_K_STEP]
    lang = st.session_state[_K_LANG]
    ui   = _ui(lang)

    # ── Header ────────────────────────────────────────────────────────────────
    st.title("👋 Benvenuto in Spendify")
    st.caption("Configurazione iniziale — ci vorranno meno di 2 minuti.")

    _progress_bar(step, ui["step_labels"])

    st.divider()

    # ── Step routing ──────────────────────────────────────────────────────────
    if step == 0:
        _step0_language(cfg_svc, lang_options)
    elif step == 1:
        _step1_owners(lang)
    elif step == 2:
        _step2_accounts(lang)
    elif step == 3:
        _step3_confirm(cfg_svc, lang_options)
