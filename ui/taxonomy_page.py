"""Taxonomy management page — view, add, rename and delete categories/subcategories (DB-backed)."""
from __future__ import annotations

import streamlit as st

from services.settings_service import SettingsService
from support.logging import setup_logging
from ui.i18n import t
from ui.widgets.tree_filter import render_tree_filter, build_tree_data

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
        st.info(t("taxonomy.no_match", query=search))
    elif not cats:
        st.info(t("taxonomy.no_categories"))

    for cat in cats_to_show:
        subs = cat["subs"]
        with st.expander(t("taxonomy.cat_expander", name=cat['name'], n=len(subs)), expanded=bool(q)):

            # ── Sottocategorie ────────────────────────────────────────
            if subs:
                for sub in subs:
                    c1, c2, c3 = st.columns([6, 2, 1])
                    c1.write(f"• {sub['name']}")
                    new_sub_name = c2.text_input(
                        t("taxonomy.rename"), value=sub["name"],
                        key=f"rename_sub_{sub['id']}",
                        label_visibility="collapsed",
                    )
                    if c2.button("✏️", key=f"rename_sub_btn_{sub['id']}",
                                 help=t("taxonomy.rename_subcategory")):
                        ns = new_sub_name.strip()
                        if ns and ns != sub["name"]:
                            cfg_svc.update_subcategory(sub["id"], ns)
                            st.success(t("taxonomy.renamed_to", name=ns))
                            logger.info(f"taxonomy: renamed subcategory {sub['id']} → '{ns}'")
                            changed = True
                    if c3.button("🗑", key=f"del_sub_{sub['id']}",
                                 help=t("taxonomy.delete_sub_help", name=sub['name'])):
                        cfg_svc.delete_subcategory(sub["id"])
                        st.success(t("taxonomy.sub_deleted", name=sub['name']))
                        logger.info(f"taxonomy: deleted subcategory {sub['id']}")
                        changed = True
            else:
                st.caption(t("taxonomy.no_subcategories"))

            st.divider()

            # ── Aggiungi sottocategoria ───────────────────────────────
            all_sub_names = [s["name"] for s in subs_by_cat.get(cat["id"], [])]
            new_sub_key = f"new_sub_{type_key}_{cat['id']}"
            new_sub = st.text_input(
                t("taxonomy.new_subcategory"), key=new_sub_key,
                placeholder=t("taxonomy.new_sub_placeholder"),
            )
            if st.button(t("taxonomy.add_subcategory_btn"),
                         key=f"add_sub_btn_{type_key}_{cat['id']}"):
                ns = new_sub.strip()
                if not ns:
                    st.warning(t("taxonomy.name_empty"))
                elif ns in all_sub_names:
                    st.warning(t("taxonomy.sub_exists"))
                else:
                    cfg_svc.create_subcategory(cat["id"], ns)
                    st.success(t("taxonomy.sub_added", name=ns))
                    logger.info(f"taxonomy: added subcategory '{ns}' to cat {cat['id']}")
                    changed = True

            st.divider()

            # ── Rinomina categoria ────────────────────────────────────
            rename_val = st.text_input(
                t("taxonomy.rename_category"), value=cat["name"],
                key=f"rename_cat_{cat['id']}",
            )
            if st.button(t("taxonomy.save_cat_name"),
                         key=f"rename_cat_btn_{cat['id']}"):
                new_name = rename_val.strip()
                if not new_name:
                    st.warning(t("taxonomy.name_empty"))
                elif new_name == cat["name"]:
                    st.info(t("taxonomy.no_change"))
                else:
                    ok = cfg_svc.update_category(cat["id"], new_name)
                    if ok:
                        st.success(t("taxonomy.renamed_to", name=new_name))
                        logger.info(f"taxonomy: renamed category {cat['id']} → '{new_name}'")
                        changed = True

            st.divider()

            # ── Elimina categoria ─────────────────────────────────────
            confirm = st.checkbox(
                t("taxonomy.confirm_delete_cat"),
                key=f"confirm_del_cat_{cat['id']}",
            )
            if st.button(t("taxonomy.delete_cat_btn"),
                         key=f"del_cat_btn_{cat['id']}",
                         disabled=not confirm, type="secondary"):
                ok = cfg_svc.delete_category(cat["id"])
                if ok:
                    st.success(t("taxonomy.cat_deleted", name=cat['name']))
                    logger.info(f"taxonomy: deleted category {cat['id']}")
                    changed = True

    # ── Nuova categoria ────────────────────────────────────────────────────
    if not q:
        st.divider()
        with st.form(f"new_cat_form_{type_key}", clear_on_submit=True):
            st.write(f"**{t('taxonomy.new_category_title')}**")
            new_cat_name = st.text_input(
                t("taxonomy.category_name"),
                placeholder=t("taxonomy.cat_name_placeholder"),
            )
            new_cat_subs_raw = st.text_area(
                t("taxonomy.subcategories_per_line"),
                placeholder=t("taxonomy.subs_placeholder"),
                height=100,
            )
            submitted = st.form_submit_button(t("taxonomy.create_cat_btn"), type="primary")

        if submitted:
            nc = new_cat_name.strip()
            if not nc:
                st.error(t("taxonomy.cat_name_empty"))
            else:
                existing = [c.name for c in cfg_svc.get_categories(type_filter=type_key)]
                if nc in existing:
                    st.error(t("taxonomy.cat_exists", name=nc))
                else:
                    new_cat_obj = cfg_svc.create_category(nc, type_key)
                    subs_list = [s.strip() for s in new_cat_subs_raw.splitlines() if s.strip()]
                    for sub_name in subs_list:
                        cfg_svc.create_subcategory(new_cat_obj.id, sub_name)
                    st.success(t("taxonomy.cat_created", name=nc, n=len(subs_list)))
                    logger.info(f"taxonomy: created category '{nc}' ({type_key}) with {len(subs_list)} subs")
                    changed = True

    return changed


# ── Main page ─────────────────────────────────────────────────────────────────

def render_taxonomy_page(engine):
    st.header(t("taxonomy.title"))
    st.caption(t("taxonomy.caption"))

    cfg_svc = SettingsService(engine)

    search = st.text_input(
        t("taxonomy.search"),
        placeholder=t("taxonomy.search_placeholder"),
        key="taxonomy_search",
    )

    changed = False
    tab_exp, tab_inc = st.tabs([t("taxonomy.tab_expenses"), t("taxonomy.tab_income")])

    with tab_exp:
        with st.expander(t("taxonomy.tree_overview_expenses"), expanded=False):
            _tree_exp = build_tree_data(cfg_svc, "expense")
            _sel_exp = render_tree_filter(
                categories=_tree_exp,
                contexts=[],
                key_prefix="tax_tree_exp",
                show_contexts=False,
            )
            _filter_cats_exp = set(_sel_exp["selected_categories"])
        if _render_section(cfg_svc, "expense", t("taxonomy.section_expenses"), search):
            changed = True

    with tab_inc:
        with st.expander(t("taxonomy.tree_overview_income"), expanded=False):
            _tree_inc = build_tree_data(cfg_svc, "income")
            _sel_inc = render_tree_filter(
                categories=_tree_inc,
                contexts=[],
                key_prefix="tax_tree_inc",
                show_contexts=False,
            )
        if _render_section(cfg_svc, "income", t("taxonomy.section_income"), search):
            changed = True

    if changed:
        st.rerun()
