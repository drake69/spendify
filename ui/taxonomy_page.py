"""Taxonomy management page — view, add, rename and delete categories/subcategories (DB-backed)."""
from __future__ import annotations

import streamlit as st

from services.settings_service import SettingsService
from support.logging import setup_logging

logger = setup_logging()


# ── Section renderer ──────────────────────────────────────────────────────────

def _render_section(cfg_svc: SettingsService, type_key: str, section_label: str, search: str = "") -> bool:
    """Render CRUD for one taxonomy type ('expense' or 'income').
    Returns True if any change occurred (triggers rerun)."""
    changed = False

    cat_rows, sub_rows = cfg_svc.get_taxonomy_raw(type_key)

    subs_by_cat: dict[int, list[dict]] = {}
    for s in sub_rows:
        subs_by_cat.setdefault(s.category_id, []).append(
            {"id": s.id, "name": s.name, "sort_order": s.sort_order}
        )

    cats = [
        {
            "id": c.id,
            "name": c.name,
            "type": c.type,
            "subs": sorted(subs_by_cat.get(c.id, []), key=lambda x: x["name"].lower()),
        }
        for c in cat_rows
    ]

    # Apply search filter
    q = search.strip().lower()
    if q:
        cats_to_show = []
        for cat in cats:
            cat_match = q in cat["name"].lower()
            matching_subs = [s for s in cat["subs"] if q in s["name"].lower()]
            if cat_match or matching_subs:
                cats_to_show.append({**cat, "subs": cat["subs"] if cat_match else matching_subs})
    else:
        cats_to_show = cats

    st.subheader(section_label)

    if q and not cats_to_show:
        st.info(f"Nessuna corrispondenza per \"{search}\".")
    elif not cats:
        st.info("Nessuna categoria presente.")

    for cat in cats_to_show:
        subs = cat["subs"]
        with st.expander(f"**{cat['name']}** — {len(subs)} sottocategorie", expanded=bool(q)):

            # ── Sottocategorie ────────────────────────────────────────
            if subs:
                for sub in subs:
                    c1, c2, c3 = st.columns([6, 2, 1])
                    c1.write(f"• {sub['name']}")
                    new_sub_name = c2.text_input(
                        "Rinomina", value=sub["name"],
                        key=f"rename_sub_{sub['id']}",
                        label_visibility="collapsed",
                    )
                    if c2.button("✏️", key=f"rename_sub_btn_{sub['id']}",
                                 help="Rinomina sottocategoria"):
                        ns = new_sub_name.strip()
                        if ns and ns != sub["name"]:
                            cfg_svc.update_subcategory(sub["id"], ns)
                            st.success(f"Rinominata in '{ns}'.")
                            logger.info(f"taxonomy: renamed subcategory {sub['id']} → '{ns}'")
                            changed = True
                    if c3.button("🗑", key=f"del_sub_{sub['id']}",
                                 help=f"Elimina '{sub['name']}'"):
                        cfg_svc.delete_subcategory(sub["id"])
                        st.success(f"Sottocategoria '{sub['name']}' eliminata.")
                        logger.info(f"taxonomy: deleted subcategory {sub['id']}")
                        changed = True
            else:
                st.caption("Nessuna sottocategoria.")

            st.divider()

            # ── Aggiungi sottocategoria ───────────────────────────────
            all_sub_names = [s["name"] for s in subs_by_cat.get(cat["id"], [])]
            new_sub_key = f"new_sub_{type_key}_{cat['id']}"
            new_sub = st.text_input(
                "Nuova sottocategoria", key=new_sub_key,
                placeholder="es. Abbonamento palestra",
            )
            if st.button("➕ Aggiungi sottocategoria",
                         key=f"add_sub_btn_{type_key}_{cat['id']}"):
                ns = new_sub.strip()
                if not ns:
                    st.warning("Il nome non può essere vuoto.")
                elif ns in all_sub_names:
                    st.warning("Sottocategoria già presente.")
                else:
                    cfg_svc.create_subcategory(cat["id"], ns)
                    st.success(f"Aggiunta '{ns}'.")
                    logger.info(f"taxonomy: added subcategory '{ns}' to cat {cat['id']}")
                    changed = True

            st.divider()

            # ── Rinomina categoria ────────────────────────────────────
            rename_val = st.text_input(
                "Rinomina categoria", value=cat["name"],
                key=f"rename_cat_{cat['id']}",
            )
            if st.button("✏️ Salva nome categoria",
                         key=f"rename_cat_btn_{cat['id']}"):
                new_name = rename_val.strip()
                if not new_name:
                    st.warning("Il nome non può essere vuoto.")
                elif new_name == cat["name"]:
                    st.info("Nessuna modifica.")
                else:
                    ok = cfg_svc.update_category(cat["id"], new_name)
                    if ok:
                        st.success(f"Rinominata in '{new_name}'.")
                        logger.info(f"taxonomy: renamed category {cat['id']} → '{new_name}'")
                        changed = True

            st.divider()

            # ── Elimina categoria ─────────────────────────────────────
            confirm = st.checkbox(
                "Conferma eliminazione categoria",
                key=f"confirm_del_cat_{cat['id']}",
            )
            if st.button("🗑️ Elimina categoria",
                         key=f"del_cat_btn_{cat['id']}",
                         disabled=not confirm, type="secondary"):
                ok = cfg_svc.delete_category(cat["id"])
                if ok:
                    st.success(f"Categoria '{cat['name']}' eliminata.")
                    logger.info(f"taxonomy: deleted category {cat['id']}")
                    changed = True

    # ── Nuova categoria ────────────────────────────────────────────────────
    if not q:
        st.divider()
        with st.form(f"new_cat_form_{type_key}", clear_on_submit=True):
            st.write("**➕ Nuova categoria**")
            new_cat_name = st.text_input(
                "Nome categoria",
                placeholder="es. Sport e benessere",
            )
            new_cat_subs_raw = st.text_area(
                "Sottocategorie (una per riga)",
                placeholder="es.\nPalestra\nNuoto\nCorsa",
                height=100,
            )
            submitted = st.form_submit_button("💾 Crea categoria", type="primary")

        if submitted:
            nc = new_cat_name.strip()
            if not nc:
                st.error("Il nome categoria non può essere vuoto.")
            else:
                existing = [c.name for c in cfg_svc.get_categories(type_filter=type_key)]
                if nc in existing:
                    st.error(f"Categoria '{nc}' già presente.")
                else:
                    new_cat_obj = cfg_svc.create_category(nc, type_key)
                    subs_list = [s.strip() for s in new_cat_subs_raw.splitlines() if s.strip()]
                    for sub_name in subs_list:
                        cfg_svc.create_subcategory(new_cat_obj.id, sub_name)
                    st.success(f"Categoria '{nc}' creata con {len(subs_list)} sottocategorie.")
                    logger.info(f"taxonomy: created category '{nc}' ({type_key}) with {len(subs_list)} subs")
                    changed = True

    return changed


# ── Main page ─────────────────────────────────────────────────────────────────

def render_taxonomy_page(engine):
    st.header("🗂️ Tassonomia — Categorie e Sottocategorie")
    st.caption(
        "Le modifiche vengono salvate immediatamente nel database. "
        "Le nuove categorie/sottocategorie sono subito disponibili in Review e Regole."
    )

    cfg_svc = SettingsService(engine)

    search = st.text_input(
        "🔍 Cerca categoria o sottocategoria",
        placeholder="es. palestra, netflix, carburante…",
        key="taxonomy_search",
    )

    changed = False
    tab_exp, tab_inc = st.tabs(["💸 Spese", "💰 Entrate"])

    with tab_exp:
        if _render_section(cfg_svc, "expense", "Categorie di Spesa", search):
            changed = True

    with tab_inc:
        if _render_section(cfg_svc, "income", "Categorie di Entrata", search):
            changed = True

    if changed:
        st.rerun()
