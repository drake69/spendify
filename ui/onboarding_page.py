"""Onboarding wizard — first-run setup (4 steps).

Step 0 — Lingua & formato      : rilevamento browser, selezione lingua, anteprima formati
Step 1 — Nomi titolari         : nome/i del/dei titolare/i dei conti (essenziale per giroconti)
Step 2 — Conti bancari         : aggiunta di almeno un conto (nome + banca opzionale)
Step 3 — Riepilogo & conferma  : mostra tutto, applica in un'unica transazione

Tutto viene applicato solo al click di "Inizia →" (step 3): nessuna scrittura parziale.
"""
from __future__ import annotations

from datetime import date

import json
import os
import time
from pathlib import Path

import streamlit as st

from services.settings_service import SettingsService
from support.logging import setup_logging
from ui.i18n import t

logger = setup_logging()

# ── Model download status (set by desktop launcher's background thread) ──────
_MODEL_STATUS_FILE = Path.home() / ".spendifai" / "model_download.status"


def _read_model_download_status() -> dict | None:
    """Return the live status dict written by ``desktop/launcher.py``.

    Returns ``None`` when no file exists — typical of source/dev installs
    where the launcher is not in play and the LLM is whatever the user
    configured in ``.env``. The wizard treats "no status file" as "no
    download in progress" and the gate is open.
    """
    if not _MODEL_STATUS_FILE.exists():
        return None
    try:
        return json.loads(_MODEL_STATUS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def _format_download_eta(seconds: int | None) -> str:
    """Render seconds-remaining in a human-friendly compact form."""
    if seconds is None:
        return "calcolo..."
    if seconds < 60:
        return f"{seconds} sec"
    if seconds < 3600:
        return f"~{seconds // 60} min"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"~{h}h {m}min"


# ── Session-state keys ────────────────────────────────────────────────────────
_K_STEP     = "_ob_step"
_K_LANG     = "_ob_lang"
_K_COUNTRY  = "_ob_country"
_K_NAMES    = "_ob_owner_names"
_K_ACCOUNTS = "_ob_accounts"
# Taxonomy edits: dict {"expenses": [{"original": str, "name": str, "enabled": bool}],
#                       "income":   [{"original": str, "name": str, "enabled": bool}],
#                       "lang":     str}  (lang keeps the snapshot tied to the language
# that was current when the preview was seeded — invalidates the cache if the user
# goes back and changes language).
_K_TAXONOMY = "_ob_taxonomy_edits"


def _account_types() -> dict[str, str]:
    return {
        t("onboarding.account_type.bank_account"): "bank_account",
        t("onboarding.account_type.credit_card"): "credit_card",
        t("onboarding.account_type.debit_card"): "debit_card",
        t("onboarding.account_type.prepaid_card"): "prepaid_card",
        t("onboarding.account_type.savings_account"): "savings_account",
        t("onboarding.account_type.cash"): "cash",
    }


# ── Country list & language → country suggestion ──────────────────────────────
# Codes only — display names are resolved at render time via t("country.<code>")
# so they follow the active UI language. The list order here is the *display*
# order in the dropdown; alphabetisation by translated name happens at use site.
_COUNTRY_CODES: list[str] = [
    "AT", "AU", "BE", "CA", "CH", "CZ", "DE", "DK", "ES", "FI",
    "FR", "GB", "HU", "IE", "IT", "LU", "NL", "NO", "PL", "PT",
    "RO", "SE", "SI", "SK", "SM", "US",
]


def _country_label(code: str) -> str:
    """Translated country name for the currently active UI language."""
    return t(f"country.{code}")


def _sorted_country_labels() -> list[str]:
    """Country display names sorted alphabetically in the active language."""
    return sorted((_country_label(c) for c in _COUNTRY_CODES), key=lambda s: s.lower())


def _code_from_label(label: str) -> str | None:
    """Reverse-lookup: translated label → country code. None if unknown."""
    for code in _COUNTRY_CODES:
        if _country_label(code) == label:
            return code
    return None

# Default country suggestion per taxonomy language
_COUNTRY_SUGGESTION: dict[str, str] = {
    "it": "IT",
    "de": "DE",
    "fr": "FR",
    "es": "ES",
    "en": "GB",
}

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
           "step_labels": ["Lingua", "Titolari", "Conti", "Tassonomia", "Categorie", "Conferma"]},
    "en": {"next": "Next →",   "back": "← Back",      "start": "🚀 Let's go!",
           "step_labels": ["Language", "Owners", "Accounts", "Taxonomy", "Categories", "Confirm"]},
    "fr": {"next": "Suivant →","back": "← Retour",    "start": "🚀 Commencer!",
           "step_labels": ["Langue", "Titulaires", "Comptes", "Taxonomie", "Catégories", "Confirmer"]},
    "de": {"next": "Weiter →", "back": "← Zurück",    "start": "🚀 Loslegen!",
           "step_labels": ["Sprache", "Inhaber", "Konten", "Taxonomie", "Kategorien", "Bestätigen"]},
    "es": {"next": "Siguiente →","back": "← Atrás",   "start": "🚀 ¡Empezar!",
           "step_labels": ["Idioma", "Titulares", "Cuentas", "Taxonomía", "Categorías", "Confirmar"]},
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

    st.subheader(t("onboarding.step0.title"))
    st.caption(t("onboarding.step0.caption"))

    sel_label = st.radio(
        t("onboarding.step0.language"),
        options=labels,
        index=codes.index(lang) if lang in codes else 0,
        horizontal=True,
        key="_ob_lang_radio",
    )
    sel_code = codes[labels.index(sel_label)]
    _prev_lang = st.session_state.get(_K_LANG)
    st.session_state[_K_LANG] = sel_code

    # One choice drives everything: taxonomy language, locale (date/amount
    # format), AND UI language. If the user picks a language whose UI JSON
    # is missing (e.g. fr.json absent), i18n._load returns {} and t() falls
    # back to Italian per its lookup order. No second selectbox needed.
    from ui.i18n import set_language
    _ui_lang_code = sel_code
    st.session_state["_ob_ui_lang"] = _ui_lang_code
    set_language(_ui_lang_code)

    # When the user picks a different language, everything above the radio
    # (title, caption, step labels) was already rendered in the *previous*
    # language during this same script execution — Streamlit reads the radio
    # value only when reaching the widget. A full rerun re-paints the whole
    # page coherently in the new language. Otherwise the user sees a mixed
    # paint: page header in EN, country expander + Next button in IT.
    if _prev_lang is not None and _prev_lang != sel_code:
        st.rerun()

    # Format preview
    loc = _locale(sel_code)
    preview = cfg_svc.get_default_taxonomy_preview(sel_code)
    c1, c2, c3 = st.columns(3)
    c1.metric(t("onboarding.step0.date"), _fmt_date(sel_code))
    c2.metric(t("onboarding.step0.amount"), _fmt_amount(sel_code))
    c3.metric(t("onboarding.step0.taxonomy"), t("onboarding.step0.expense_cats", n=len(preview['expenses'])))

    if preview["expenses"]:
        st.caption(
            t("onboarding.step0.categories_preview", lang=sel_label, cats=", ".join(preview["expenses"][:6]))
        )

    st.write("")

    # ── Paese ──────────────────────────────────────────────────────────────────
    # Auto-suggest country from selected language; user can override.
    # If language changed since last render, reset suggestion.
    _prev_lang_key = "_ob_prev_lang_for_country"
    # Also reset the cached selectbox label whenever the UI language changes,
    # otherwise streamlit keeps showing the previous-language label and the
    # selectbox loses its selection (label not found in the new options list).
    if st.session_state.get(_prev_lang_key) != sel_code:
        suggested = _COUNTRY_SUGGESTION.get(sel_code, "IT")
        st.session_state["_ob_country_select"] = _country_label(suggested)
        st.session_state[_K_COUNTRY] = suggested
    st.session_state[_prev_lang_key] = sel_code

    # Country is auto-derived from the chosen language above. Most users
    # never need to change it; a manual override lives behind an expander
    # so the default onboarding stays a single visible question.
    _country_labels = _sorted_country_labels()
    _fallback_label = _country_label("IT")
    _cur_country_label = st.session_state.get("_ob_country_select", _fallback_label)
    if _cur_country_label not in _country_labels:
        # Stale label from a previous language → reset to auto-suggestion.
        _cur_country_label = _country_label(st.session_state[_K_COUNTRY])
        st.session_state["_ob_country_select"] = _cur_country_label
    _auto_country = _country_label(st.session_state[_K_COUNTRY])
    with st.expander(t("onboarding.step0.country_expander", country=_auto_country), expanded=False):
        st.caption(t("onboarding.step0.country_caption"))
        selected_country_label = st.selectbox(
            t("onboarding.step0.country"),
            _country_labels,
            index=_country_labels.index(_cur_country_label),
            key="_ob_country_select",
            label_visibility="collapsed",
        )
        st.session_state[_K_COUNTRY] = _code_from_label(selected_country_label) or "IT"

    st.write("")
    _, col_next = st.columns([1, 1])
    with col_next:
        if st.button(_ui(sel_code)["next"], type="primary", use_container_width=True):
            st.session_state[_K_STEP] = 1
            st.rerun()


def _step1_owners(lang: str) -> None:
    """Step 1 — Nomi titolari."""
    st.subheader(t("onboarding.step1.title"))
    st.caption(t("onboarding.step1.caption"))
    st.info(t("onboarding.step1.hint"))

    names = st.text_input(
        t("onboarding.step1.label"),
        value=st.session_state.get(_K_NAMES, ""),
        placeholder=t("onboarding.step1.placeholder"),
        key="_ob_owner_input",
    )
    st.session_state[_K_NAMES] = names

    valid = bool(names.strip())
    if not valid:
        st.warning(t("onboarding.step1.warning"))

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
    st.subheader(t("onboarding.step2.title"))
    st.caption(t("onboarding.step2.caption"))

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
            t("onboarding.step2.account_name") if i == 0 else "",
            value=acc["name"],
            placeholder=t("onboarding.step2.account_name_placeholder"),
            key=f"_ob_acc_name_{i}",
            label_visibility="visible" if i == 0 else "collapsed",
        )
        acc["bank"] = c2.text_input(
            t("onboarding.step2.bank") if i == 0 else "",
            value=acc["bank"],
            placeholder=t("onboarding.step2.bank_placeholder"),
            key=f"_ob_acc_bank_{i}",
            label_visibility="visible" if i == 0 else "collapsed",
        )
        _types = _account_types()
        _type_values = list(_types.values())
        _type_labels_list = list(_types.keys())
        _cur_type_idx = _type_values.index(acc["type"]) if acc["type"] in _type_values else 0
        _sel_type_label = c2b.selectbox(
            t("onboarding.step2.type") if i == 0 else "",
            _type_labels_list,
            index=_cur_type_idx,
            key=f"_ob_acc_type_{i}",
            label_visibility="visible" if i == 0 else "collapsed",
        )
        acc["type"] = _types[_sel_type_label]
        with c3:
            if i == 0:
                st.write("")   # align with label height
                st.write("")
            if len(accounts) > 1:
                if st.button("🗑", key=f"_ob_acc_del_{i}", help=t("onboarding.step2.remove")):
                    to_remove = i

    if to_remove is not None:
        accounts.pop(to_remove)
        st.session_state[_K_ACCOUNTS] = accounts
        st.rerun()

    if st.button(t("onboarding.step2.add_account"), key="_ob_acc_add"):
        accounts.append({"name": "", "bank": "", "type": "bank_account"})
        st.session_state[_K_ACCOUNTS] = accounts
        st.rerun()

    valid_accounts = [a for a in accounts if a["name"].strip()]
    if not valid_accounts:
        st.warning(t("onboarding.step2.no_accounts_warning"))

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


def _taxonomy_language(default_lang: str) -> str:
    """Pick which language to seed and display the taxonomy in.

    The user's choice in step 0 is split into ``_K_LANG`` (controls date /
    amount format) and ``_ob_ui_lang`` (controls all i18n strings — labels,
    buttons, captions). We tie the taxonomy to the UI language so the
    category names the user sees in the wizard editor match the language
    the rest of the UI is rendered in.
    """
    return st.session_state.get("_ob_ui_lang") or default_lang


def _seed_taxonomy_edits(cfg_svc: SettingsService, lang: str) -> dict:
    """Seed the editable taxonomy snapshot from the default template for *lang*.

    Returns the full 3-level snapshot:
        {
          "lang": "<lang>",
          "expenses": [
              {"original": str, "name": str, "enabled": bool,
               "subcategories": [{"original": str, "name": str, "enabled": bool}, ...]},
              ...
          ],
          "income": [...]
        }

    ``original`` keys preserve the seed names so the apply step can match
    edits to the just-seeded DB rows, even after the user renamed them in
    the editor.
    """
    full = cfg_svc.get_default_taxonomy_full_preview(lang)

    def _row(cat: dict) -> dict:
        return {
            "original": cat["category"],
            "name": cat["category"],
            "enabled": True,
            "subcategories": [
                {"original": s, "name": s, "enabled": True}
                for s in cat.get("subcategories", [])
            ],
        }

    return {
        "lang": lang,
        "expenses": [_row(c) for c in full.get("expenses", [])],
        "income":   [_row(c) for c in full.get("income", [])],
    }


def _taxonomy_diff_counts(edits: dict | None) -> dict:
    """Count edits made on top of the snapshot: renamed / disabled categories
    and subcategories. Used by the recap step to summarise the user's
    customisations at a glance."""
    if not edits:
        return {"cat_total": 0, "cat_kept": 0, "cat_renamed": 0,
                "sub_total": 0, "sub_kept": 0, "sub_renamed": 0}
    cat_total = cat_kept = cat_renamed = 0
    sub_total = sub_kept = sub_renamed = 0
    for group in ("expenses", "income"):
        for cat in edits.get(group, []):
            cat_total += 1
            if cat.get("enabled", True):
                cat_kept += 1
            if cat.get("enabled", True) and (cat.get("name") or "").strip() != cat.get("original"):
                cat_renamed += 1
            for sub in cat.get("subcategories", []):
                sub_total += 1
                if sub.get("enabled", True):
                    sub_kept += 1
                if sub.get("enabled", True) and (sub.get("name") or "").strip() != sub.get("original"):
                    sub_renamed += 1
    return {
        "cat_total": cat_total, "cat_kept": cat_kept, "cat_renamed": cat_renamed,
        "sub_total": sub_total, "sub_kept": sub_kept, "sub_renamed": sub_renamed,
    }


def _step3_taxonomy(cfg_svc: SettingsService, lang: str) -> None:
    """Step 3 — Review and edit the default taxonomy.

    Lightweight editor: per-category rename + enable/disable. We do NOT
    surface subcategory editing here — too much UI for a wizard, and the
    Settings → Taxonomy page already covers the full editor.

    On Next, the edits live in ``st.session_state[_K_TAXONOMY]`` and the
    confirm step's apply call replays them on top of the default seed.

    The taxonomy is rendered (and later seeded) in the UI language — the
    user explicitly picked it on step 0 and expects the categories to
    match the rest of the UI.
    """
    tax_lang = _taxonomy_language(lang)
    st.subheader(t("onboarding.step_taxonomy.title"))
    st.caption(t("onboarding.step_taxonomy.caption"))

    # Re-seed the snapshot whenever the user came back and switched language.
    edits = st.session_state.get(_K_TAXONOMY)
    if edits is None or edits.get("lang") != tax_lang:
        edits = _seed_taxonomy_edits(cfg_svc, tax_lang)
        st.session_state[_K_TAXONOMY] = edits

    def _render_group(title_icon: str, title_key: str, rows: list[dict], key_prefix: str):
        n_cat_enabled = sum(1 for r in rows if r["enabled"])
        n_sub_total   = sum(len(r.get("subcategories", [])) for r in rows)
        n_sub_enabled = sum(
            sum(1 for s in r.get("subcategories", []) if s["enabled"])
            for r in rows if r["enabled"]
        )
        st.markdown(
            f"**{title_icon} {t(title_key)}** "
            f"· {n_cat_enabled}/{len(rows)} · "
            f"{n_sub_enabled}/{n_sub_total} sub"
        )
        for idx, row in enumerate(rows):
            col_chk, col_name = st.columns([1, 6], vertical_alignment="center")
            row["enabled"] = col_chk.checkbox(
                "✓",
                value=row["enabled"],
                key=f"{key_prefix}_chk_{idx}",
                label_visibility="collapsed",
            )
            row["name"] = col_name.text_input(
                "",
                value=row["name"],
                key=f"{key_prefix}_name_{idx}",
                label_visibility="collapsed",
                disabled=not row["enabled"],
            )

            subs = row.get("subcategories", [])
            if subs and row["enabled"]:
                _sub_enabled = sum(1 for s in subs if s["enabled"])
                with st.expander(
                    t("onboarding.step_taxonomy.sub_expander",
                      n=_sub_enabled, total=len(subs)),
                    expanded=False,
                ):
                    for s_idx, sub in enumerate(subs):
                        sc1, sc2 = st.columns([1, 6], vertical_alignment="center")
                        sub["enabled"] = sc1.checkbox(
                            "✓",
                            value=sub["enabled"],
                            key=f"{key_prefix}_subchk_{idx}_{s_idx}",
                            label_visibility="collapsed",
                        )
                        sub["name"] = sc2.text_input(
                            "",
                            value=sub["name"],
                            key=f"{key_prefix}_subname_{idx}_{s_idx}",
                            label_visibility="collapsed",
                            disabled=not sub["enabled"],
                        )

    col_exp, col_inc = st.columns(2)
    with col_exp:
        _render_group("💸", "onboarding.step_taxonomy.expenses",
                      edits["expenses"], "_ob_tax_exp")
    with col_inc:
        _render_group("💰", "onboarding.step_taxonomy.income",
                      edits["income"], "_ob_tax_inc")

    st.caption(t("onboarding.step_taxonomy.customize_later"))

    # Reset link — restores the default for this language.
    if st.button(t("onboarding.step_taxonomy.reset"), key="_ob_tax_reset"):
        st.session_state[_K_TAXONOMY] = _seed_taxonomy_edits(cfg_svc, tax_lang)
        st.rerun()

    st.divider()
    col_back, _, col_next = st.columns([1, 2, 1])
    with col_back:
        if st.button(_ui(lang)["back"], use_container_width=True, key="_ob_tax_back"):
            st.session_state[_K_STEP] = 2
            st.rerun()
    with col_next:
        if st.button(_ui(lang)["next"], type="primary",
                     use_container_width=True, key="_ob_tax_next"):
            # Persist taxonomy + settings + accounts BEFORE the NSI pre-warm
            # step, which needs them in DB. If the user later steps back here
            # and changes the taxonomy, _persist_choices is idempotent.
            _names = st.session_state.get(_K_NAMES, "").strip()
            _accounts = [a for a in st.session_state.get(_K_ACCOUNTS, []) if a["name"].strip()]
            _country = st.session_state.get(_K_COUNTRY, "")
            _loc = _locale(lang)
            with st.spinner(t("onboarding.step3.applying")):
                _persist_choices(cfg_svc, lang, _names, _accounts, _loc, country=_country)
            # Force NSI re-prewarm if the user re-entered step 3 after editing
            # the taxonomy.
            st.session_state.pop("_ob_nsi_prewarm_done", None)
            st.session_state[_K_STEP] = 4
            st.rerun()


def _step4_prepare_categories(cfg_svc: SettingsService, lang: str) -> None:
    """Step 4 — NSI taxonomy_map pre-warm.

    Runs the single ~5-minute LLM call that maps OSM tags to the user's
    chosen taxonomy. We sit on this step until the call returns, then
    advance automatically to the confirmation step. Idempotent via the
    `_ob_nsi_prewarm_done` session flag, so navigating back+forward does
    not re-run the call."""
    st.subheader(t("onboarding.preparing_categories.title"))
    st.caption(t("onboarding.preparing_categories.caption"))

    _flag = "_ob_nsi_prewarm_done"
    _already = st.session_state.get(_flag, False)

    if not _already:
        with st.spinner(t("onboarding.preparing_categories.title")):
            _ok = _run_nsi_prewarm(cfg_svc)
        st.session_state[_flag] = True  # mark done even on failure (static fallback covers)
        if _ok:
            logger.info("onboarding: NSI pre-warm completed")
        st.session_state[_K_STEP] = 5
        st.rerun()

    # Should rarely render — only if Streamlit reruns after the flag is set
    # but before the rerun above takes effect.
    st.success(t("onboarding.preparing_categories.done"))


def _step5_confirm(cfg_svc: SettingsService, lang_options: list[tuple[str, str]]) -> None:
    """Step 5 — Riepilogo & conferma finale."""
    lang     = st.session_state[_K_LANG]
    names    = st.session_state.get(_K_NAMES, "").strip()
    accounts = [a for a in st.session_state.get(_K_ACCOUNTS, []) if a["name"].strip()]
    lang_label    = next((lbl for code, lbl in lang_options if code == lang), lang)
    country_code  = st.session_state.get(_K_COUNTRY, "")
    country_label = _country_label(country_code) if country_code else "—"
    loc = _locale(lang)

    st.subheader(t("onboarding.step3.title"))
    st.caption(t("onboarding.step3.caption"))

    col1, col2 = st.columns(2)

    with col1:
        st.markdown(t("onboarding.step3.lang_format"))
        st.markdown(f"- {t('onboarding.step3.language')}: **{lang_label}**")
        st.markdown(f"- {t('onboarding.step3.date')}: `{_fmt_date(lang)}`")
        st.markdown(f"- {t('onboarding.step3.amount')}: `{_fmt_amount(lang)}`")
        st.markdown(f"- {t('onboarding.step3.country')}: **{country_label}**")

        st.write("")
        st.markdown(t("onboarding.step3.owners_title"))
        for name in [n.strip() for n in names.split(",") if n.strip()]:
            st.markdown(f"- {name}")
        if not names:
            st.caption(t("onboarding.step3.no_names"))

    with col2:
        _type_labels_inv = {v: k for k, v in _account_types().items()}
        st.markdown(t("onboarding.step3.accounts_title"))
        if accounts:
            for acc in accounts:
                bank_note = f" — {acc['bank']}" if acc["bank"].strip() else ""
                type_note = f" ({_type_labels_inv.get(acc.get('type', ''), acc.get('type', ''))})"
                st.markdown(f"- **{acc['name']}**{bank_note}{type_note}")
        else:
            st.caption(t("onboarding.step3.no_accounts"))

        # ── Taxonomy recap ──────────────────────────────────────────────────
        # Surface what will be persisted: total kept / disabled / renamed
        # categories and subcategories. The full editor is one step away
        # (← Indietro) if the user wants to tweak before confirming.
        st.write("")
        st.markdown(t("onboarding.step3.taxonomy_title"))
        _tax_edits = st.session_state.get(_K_TAXONOMY) or {}
        _counts = _taxonomy_diff_counts(_tax_edits)
        st.markdown(
            t(
                "onboarding.step3.taxonomy_counts",
                cat_kept=_counts["cat_kept"],
                cat_total=_counts["cat_total"],
                sub_kept=_counts["sub_kept"],
                sub_total=_counts["sub_total"],
            )
        )
        if _counts["cat_renamed"] or _counts["sub_renamed"] \
                or _counts["cat_kept"] < _counts["cat_total"] \
                or _counts["sub_kept"] < _counts["sub_total"]:
            st.caption(
                t(
                    "onboarding.step3.taxonomy_edits",
                    cat_disabled=_counts["cat_total"] - _counts["cat_kept"],
                    cat_renamed=_counts["cat_renamed"],
                    sub_disabled=_counts["sub_total"] - _counts["sub_kept"],
                    sub_renamed=_counts["sub_renamed"],
                )
            )

    # ── LLM Model status ───────────────────────────────────────────────
    st.write("")
    st.markdown(t("onboarding.step3.llm_title"))
    from services.llm_service import detect_system_hardware, list_available_models, get_recommended_model

    _hw = detect_system_hardware()
    _local = list_available_models()
    _rec = get_recommended_model(_hw["ram_gb"])

    if _local:
        st.success(
            t("onboarding.step3.llm_available",
              name=_local[0].name,
              size=f"{_local[0].stat().st_size / 1e9:.1f}")
        )
    elif _rec:
        st.info(
            t("onboarding.step3.llm_recommended",
              gpu=_hw['gpu'],
              ram=_hw['ram_gb'],
              name=_rec.name,
              size=_rec.size_mb)
        )
    else:
        st.warning(t("onboarding.step3.llm_none"))

    st.divider()

    # ── AI model download gate ──────────────────────────────────────────────
    # The desktop launcher kicks off the model download as soon as the app
    # boots (before this wizard even renders). The user can complete all the
    # onboarding steps in parallel; here at the final step we hold the "Avvia"
    # button until the download reaches 100% so import/categorisation work the
    # moment onboarding completes.
    _dl_status = _read_model_download_status()
    _dl_ready = _dl_status is None or _dl_status.get("done") is True
    _dl_error = _dl_status.get("error") if _dl_status else None

    if not _dl_ready:
        _pct = float(_dl_status.get("pct", 0.0)) if _dl_status else 0.0
        _eta = _dl_status.get("eta_remaining_s") if _dl_status else None
        _eta_str = _format_download_eta(_eta)
        st.info(
            f"⏳ **{t('onboarding.step3.waiting_model')}** — {int(_pct * 100)}% · {_eta_str}",
            icon="📚",
        )
        st.progress(_pct, text=f"{int(_pct * 100)}%")
    elif _dl_error:
        st.error(t("onboarding.step3.model_error", error=_dl_error))

    col_back, _, col_start = st.columns([1, 2, 1])
    with col_back:
        if st.button(_ui(lang)["back"], use_container_width=True, key="_ob_conf_back"):
            # Back to taxonomy editor (step 3). If the user edits the taxonomy
            # again, the next Avanti will re-persist and re-trigger NSI prewarm.
            st.session_state[_K_STEP] = 3
            st.rerun()

    with col_start:
        # `help` shows a tooltip on hover. When the button is disabled we tell
        # the user *why* — otherwise they're left staring at a greyed-out
        # "Avvia" with no explanation.
        _start_tooltip = (
            t("onboarding.step3.start_disabled_tooltip")
            if not _dl_ready
            else None
        )
        if st.button(_ui(lang)["start"], type="primary",
                     use_container_width=True, key="_ob_conf_start",
                     disabled=not _dl_ready,
                     help=_start_tooltip):
            _finalise_onboarding(cfg_svc)

    # Auto-refresh every 2 s while we are waiting on the model so the user
    # sees live progress without manually re-running.
    if not _dl_ready:
        time.sleep(2)
        st.rerun()


def _persist_choices(
    cfg_svc: SettingsService,
    lang: str,
    owner_names: str,
    accounts: list[dict],
    loc: dict,
    country: str = "",
) -> None:
    """Persist taxonomy + settings + accounts + localised contexts.

    Runs at the "Avanti" of step 3 (Taxonomy), BEFORE the NSI pre-warm of
    step 4 — the pre-warm needs the taxonomy in DB to build the mapping.
    onboarding_done is NOT set here; that happens at the end of step 5."""
    tax_lang = _taxonomy_language(lang)
    with st.spinner(t("onboarding.step3.applying")):
        # 1. Taxonomy: default seed + user's wizard customisations (renames +
        #    disables). apply_default_taxonomy is destructive (wipes
        #    taxonomy_category / _subcategory first), so we always run it then
        #    replay the edits on top. The seed language matches the UI
        #    language so the category names persisted to the DB match what
        #    the user saw in the editor (and what the rest of the UI shows).
        n_cats = cfg_svc.apply_default_taxonomy(tax_lang)
        taxonomy_edits = st.session_state.get(_K_TAXONOMY) or {}
        if taxonomy_edits.get("lang") == tax_lang:
            renames: dict[str, str] = {}
            deletions: list[str] = []
            sub_renames: dict[tuple[str, str], str] = {}
            sub_deletions: list[tuple[str, str]] = []
            for group in ("expenses", "income"):
                for row in taxonomy_edits.get(group, []):
                    cat_orig = row.get("original", "")
                    cat_new = (row.get("name") or "").strip()
                    if not row.get("enabled", True):
                        deletions.append(cat_orig)
                        continue  # subcategories cascade via FK — skip them
                    elif cat_new and cat_new != cat_orig:
                        renames[cat_orig] = cat_new
                    for sub in row.get("subcategories", []):
                        sub_orig = sub.get("original", "")
                        sub_new = (sub.get("name") or "").strip()
                        key = (cat_orig, sub_orig)
                        if not sub.get("enabled", True):
                            sub_deletions.append(key)
                        elif sub_new and sub_new != sub_orig:
                            sub_renames[key] = sub_new
            if renames or deletions or sub_renames or sub_deletions:
                cfg_svc.apply_taxonomy_overrides(
                    renames=renames,
                    deletions=deletions,
                    sub_renames=sub_renames,
                    sub_deletions=sub_deletions,
                )

        # 2. Locale + owner settings + UI language + country + invisible LLM defaults.
        # The LLM defaults are part of onboarding even though the wizard does not
        # surface them: a fresh install needs a fully functional LLM backend
        # configured the moment onboarding completes, otherwise the Import page
        # would refuse to categorise with "llm_backend not configured". The
        # desktop launcher downloads a llama.cpp GGUF in parallel and writes
        # LLAMA_CPP_MODEL_PATH; we point llm_backend at it here so the app
        # works end-to-end on first run with no further user intervention.
        _ui_lang = st.session_state.get("_ob_ui_lang", lang)
        cfg_svc.set_bulk({
            "date_display_format":     loc["date_display_format"],
            "amount_decimal_sep":      loc["amount_decimal_sep"],
            "amount_thousands_sep":    loc["amount_thousands_sep"],
            "owner_names":             owner_names,
            "use_owner_names_giroconto": "true" if owner_names.strip() else "false",
            "ui_language":             _ui_lang,
            "country":                 country,
            # ── Invisible LLM defaults ──────────────────────────────────────
            "llm_backend":             "local_llama_cpp",
            "cat_llm_backend":         "local_llama_cpp",
            "llama_cpp_n_gpu_layers":  "0",      # CPU by default; user can opt-in via Settings
            "llama_cpp_n_ctx":         "4096",   # fits Qwen2.5 / Gemma-3 / Phi-4
            "llama_cpp_model_path":    os.environ.get("LLAMA_CPP_MODEL_PATH", ""),
        })

        # 3. Accounts
        for acc in accounts:
            if acc["name"].strip():
                cfg_svc.create_account(
                    acc["name"].strip(), acc["bank"].strip(),
                    account_type=acc.get("type", "bank_account"),
                )

        # 3-bis. Localised default life-contexts. The base seed in
        # DEFAULT_USER_SETTINGS is Italian; replace it now that we know the
        # chosen UI language so a Spanish user starts with "Cotidiano /
        # Trabajo / Vacaciones" instead of "Quotidianità / Lavoro / Vacanza".
        import json as _json
        _localised_contexts = [
            t("context.daily"), t("context.work"), t("context.holiday"),
        ]
        cfg_svc.set_bulk({"contexts": _json.dumps(_localised_contexts, ensure_ascii=False)})

    logger.info(
        f"onboarding: persisted lang={lang!r} cats={n_cats} "
        f"names={owner_names!r} accounts={len(accounts)}"
    )


def _run_nsi_prewarm(cfg_svc: SettingsService) -> bool:
    """Single LLM call that maps OSM tags → (category, subcategory) for the
    user's chosen taxonomy. Runs once at step 4 of the wizard; the import
    path uses the static fallback only. Returns True on success, False on
    any failure (non-fatal: static fallback covers us at first import)."""
    try:
        from services.nsi_taxonomy_service import NsiTaxonomyService
        from services.category_service import CategoryService
        from core.orchestrator import _build_backend, _build_categorizer_backend
        from db import repository as _repo
        from db.models import get_session as _get_session

        with _get_session(cfg_svc.engine) as _s:
            _settings = _repo.get_all_user_settings(_s)
            _taxonomy = _repo.get_taxonomy_config(_s)
        _config = CategoryService._config_from_settings(_settings)
        _backend = _build_categorizer_backend(_config) or _build_backend(_config)
        _nsi = NsiTaxonomyService(cfg_svc.engine)
        with _get_session(cfg_svc.engine) as _s:
            _nsi.build(_s, _taxonomy, llm_backend=_backend)
        return True
    except Exception as _exc:
        logger.warning(f"onboarding: NSI pre-warm failed ({_exc!r}) — static fallback at import time")
        return False


def _finalise_onboarding(cfg_svc: SettingsService) -> None:
    """Last action of the wizard: flip onboarding_done, clear session state,
    show success toast, rerun so the app navigates to the home page."""
    cfg_svc.set_onboarding_done()

    for k in (_K_STEP, _K_LANG, _K_COUNTRY, _K_NAMES, _K_ACCOUNTS,
              "_ob_prev_lang_for_country", "_ob_country_select",
              "_ob_nsi_prewarm_done"):
        st.session_state.pop(k, None)

    st.success(t("onboarding.step3.done"))
    st.rerun()


# ── Entry point ───────────────────────────────────────────────────────────────

def render_onboarding_page(engine) -> None:
    cfg_svc = SettingsService(engine)

    # Build language list from taxonomy_default table
    lang_options = cfg_svc.get_default_taxonomy_languages()  # [(code, label)]
    if not lang_options:
        st.error(t("onboarding.no_taxonomy"))
        return
    supported_codes = [code for code, _ in lang_options]

    # ── Initialise session state (only on very first render) ──────────────────
    if _K_STEP not in st.session_state:
        detected = _detect_browser_language(supported_codes)
        st.session_state[_K_STEP]     = 0
        st.session_state[_K_LANG]     = detected
        st.session_state[_K_COUNTRY]  = _COUNTRY_SUGGESTION.get(detected, "IT")
        st.session_state[_K_NAMES]    = ""
        st.session_state[_K_ACCOUNTS] = [{"name": "", "bank": "", "type": "bank_account"}]

    step = st.session_state[_K_STEP]
    lang = st.session_state[_K_LANG]
    ui   = _ui(lang)

    # Activate the chosen language BEFORE the first t() call, otherwise the
    # header renders in Italian (the i18n default) while the radio below
    # shows the detected language selected — confusing mismatch on first
    # paint when the browser locale is not Italian.
    from ui.i18n import set_language as _set_language
    _set_language(lang)

    # ── Header ────────────────────────────────────────────────────────────────
    st.title(t("onboarding.title"))
    st.caption(t("onboarding.subtitle"))

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
        _step3_taxonomy(cfg_svc, lang)
    elif step == 4:
        _step4_prepare_categories(cfg_svc, lang)
    elif step == 5:
        _step5_confirm(cfg_svc, lang_options)
