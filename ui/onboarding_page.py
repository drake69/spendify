"""Onboarding page — first-run setup wizard.

Shown automatically when:
  - The user taxonomy (taxonomy_category) is empty, OR
  - The user_setting 'onboarding_done' is not 'true'.

The user picks their language; we copy the corresponding built-in taxonomy
template into their taxonomy_category / taxonomy_subcategory tables and mark
onboarding as complete.  The taxonomy remains fully editable afterwards
(Tassonomia page).
"""
from __future__ import annotations

import streamlit as st

from services.settings_service import SettingsService
from support.logging import setup_logging

logger = setup_logging()

# Flag keys
_LANG_KEY     = "onboarding_lang_sel"
_CONFIRM_KEY  = "onboarding_confirmed"


def render_onboarding_page(engine) -> None:
    cfg_svc = SettingsService(engine)

    st.title("👋 Benvenuto / Welcome / Bienvenue / Willkommen")
    st.markdown(
        "**Prima di iniziare, scegli la lingua della tua tassonomia.**  \n"
        "Le categorie di spesa e reddito verranno create nella lingua che selezioni.  \n"
        "Potrai modificarle in qualsiasi momento dalla pagina **Tassonomia**."
    )
    st.divider()

    # Build language options from taxonomy_default
    lang_options = cfg_svc.get_default_taxonomy_languages()  # [(code, label), ...]
    if not lang_options:
        st.error("⚠️ Nessuna tassonomia default trovata nel database. "
                 "Controlla che la migrazione sia avvenuta correttamente.")
        return

    lang_labels  = [label for _, label in lang_options]
    lang_codes   = [code  for code, _ in lang_options]

    # Default selection: match existing description_language setting if any
    existing_lang = cfg_svc.get_all().get("description_language", "it")
    default_idx   = lang_codes.index(existing_lang) if existing_lang in lang_codes else 0

    selected_label = st.radio(
        "🌍 Seleziona la lingua / Select language",
        options=lang_labels,
        index=default_idx,
        horizontal=True,
        key=_LANG_KEY,
    )
    selected_code = lang_codes[lang_labels.index(selected_label)]

    # Preview: show a few sample categories for the selected language
    preview = cfg_svc.get_default_taxonomy_preview(selected_code)
    if preview["expenses"]:
        st.caption(
            f"**Esempio categorie spese:** {', '.join(preview['expenses'][:5])}…  \n"
            f"**Esempio categorie redditi:** {', '.join(preview['income'][:3])}…"
        )

    st.divider()

    col_btn, col_info = st.columns([1, 3])
    with col_btn:
        if st.button("✅ Inizia / Start", type="primary", use_container_width=True):
            with st.spinner("Configurazione tassonomia…"):
                n = cfg_svc.apply_default_taxonomy(selected_code)
                cfg_svc.set_onboarding_done()
            logger.info(f"onboarding: applied taxonomy lang={selected_code!r} categories={n}")
            st.success(
                f"Tassonomia **{selected_label}** applicata — {n} categorie create. "
                "Puoi personalizzarla dalla pagina **Tassonomia**."
            )
            st.rerun()

    with col_info:
        st.info(
            "💡 Puoi reimpostare la tassonomia in qualsiasi momento dalla pagina "
            "**⚙️ Impostazioni → Reset tassonomia**."
        )
