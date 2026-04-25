"""Reusable collapsible taxonomy tree filter with tri-state checkboxes.

Usage:
    from ui.widgets.tree_filter import render_tree_filter

    selection = render_tree_filter(
        categories=categories,   # from SettingsService.get_taxonomy_raw()
        contexts=["Quotidianità", "Lavoro", "Vacanza"],
        key_prefix="my_page",
    )
    # selection["selected_contexts"]      -> list[str]
    # selection["selected_categories"]    -> list[str]
    # selection["selected_subcategories"] -> list[str]
"""
from __future__ import annotations

import streamlit as st


def _sk(prefix: str, *parts: str) -> str:
    """Build a session-state key."""
    return f"{prefix}_tf_{'_'.join(parts)}"


def _init_defaults(
    key_prefix: str,
    categories: list[dict],
    contexts: list[str],
    show_contexts: bool,
) -> None:
    """Populate session-state defaults (checked=True) on first run."""
    init_key = _sk(key_prefix, "inited")
    if st.session_state.get(init_key):
        return

    if show_contexts:
        for ctx in contexts:
            st.session_state.setdefault(_sk(key_prefix, "ctx", ctx), True)

    for cat in categories:
        st.session_state.setdefault(_sk(key_prefix, "cat", cat["name"]), True)
        for sub in cat.get("subcategories", []):
            sub_name = sub["name"] if isinstance(sub, dict) else sub
            st.session_state.setdefault(
                _sk(key_prefix, "sub", cat["name"], sub_name), True
            )

    st.session_state[init_key] = True


def _apply_pending_actions(
    key_prefix: str,
    categories: list[dict],
    contexts: list[str],
    show_contexts: bool,
) -> None:
    """Apply deferred writes BEFORE any widget is instantiated.

    All session_state mutations happen here, in the next rerun after
    the user action — never in the same run as widget instantiation.
    """
    # Pending select-all / deselect-all
    pending_set = st.session_state.pop(_sk(key_prefix, "pending_set"), None)
    if pending_set is not None:
        if show_contexts:
            for ctx in contexts:
                st.session_state[_sk(key_prefix, "ctx", ctx)] = pending_set
        for cat in categories:
            st.session_state[_sk(key_prefix, "cat", cat["name"])] = pending_set
            for sub in cat.get("subcategories", []):
                sub_name = sub["name"] if isinstance(sub, dict) else sub
                st.session_state[_sk(key_prefix, "sub", cat["name"], sub_name)] = pending_set

    # Pending parent→children toggle (from "Applica filtri" with parent checkbox changed)
    pending_toggles = st.session_state.pop(_sk(key_prefix, "pending_toggles"), None)
    if pending_toggles:
        for cat_name, value in pending_toggles.items():
            for cat in categories:
                if cat["name"] == cat_name:
                    for sub in cat.get("subcategories", []):
                        sub_name = sub["name"] if isinstance(sub, dict) else sub
                        st.session_state[_sk(key_prefix, "sub", cat_name, sub_name)] = value
                    break


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_tree_filter(
    categories: list[dict],
    contexts: list[str],
    key_prefix: str = "tree",
    show_contexts: bool = True,
) -> dict:
    """Render a collapsible taxonomy tree with checkboxes inside a form.

    The form prevents Streamlit from re-running (and closing expanders) on
    every checkbox toggle. The user clicks "Applica filtri" to apply.
    """
    _init_defaults(key_prefix, categories, contexts, show_contexts)

    # Apply any deferred writes from the PREVIOUS run (before widgets exist)
    _apply_pending_actions(key_prefix, categories, contexts, show_contexts)

    # ── Form: checkboxes don't cause rerun until submit ──────────────────
    with st.form(key=_sk(key_prefix, "form")):

        # ── Contexts ──────────────────────────────────────────────────────
        selected_contexts: list[str] = []
        if show_contexts and contexts:
            st.markdown("**Contesti**")
            for ctx in contexts:
                ctx_key = _sk(key_prefix, "ctx", ctx)
                val = st.checkbox(ctx, value=st.session_state.get(ctx_key, True), key=ctx_key)
                if val:
                    selected_contexts.append(ctx)
            st.markdown("---")

        # ── Categories & subcategories ────────────────────────────────────
        selected_categories: list[str] = []
        selected_subcategories: list[str] = []

        for cat in categories:
            cat_name = cat["name"]
            subs = cat.get("subcategories", [])
            cat_key = _sk(key_prefix, "cat", cat_name)

            # Compute sub-state for tri-state indicator
            sub_states = [
                st.session_state.get(
                    _sk(key_prefix, "sub", cat_name, s["name"] if isinstance(s, dict) else s),
                    True,
                )
                for s in subs
            ]
            n_on = sum(sub_states)
            if n_on == len(subs):
                indicator = "☑"
            elif n_on > 0:
                indicator = "☐…"
            else:
                indicator = "☐"

            with st.expander(f"{indicator} **{cat_name}** ({n_on}/{len(subs)})"):
                # Category-level toggle checkbox
                _all_on = n_on == len(subs) if subs else st.session_state.get(cat_key, True)
                st.checkbox(
                    f"Tutte le sottocategorie di {cat_name}",
                    value=_all_on,
                    key=cat_key,
                )

                # Individual subcategory checkboxes
                if subs:
                    for sub in subs:
                        sub_name = sub["name"] if isinstance(sub, dict) else sub
                        sub_key = _sk(key_prefix, "sub", cat_name, sub_name)
                        sub_val = st.checkbox(
                            sub_name,
                            value=st.session_state.get(sub_key, True),
                            key=sub_key,
                        )
                        if sub_val:
                            selected_subcategories.append(sub_name)

            # After expander: check if category has any sub selected
            final_cat_subs = [
                st.session_state.get(
                    _sk(key_prefix, "sub", cat_name, s["name"] if isinstance(s, dict) else s),
                    True,
                )
                for s in subs
            ]
            if any(final_cat_subs) or (not subs and st.session_state.get(cat_key, True)):
                selected_categories.append(cat_name)

        # ── Action buttons ────────────────────────────────────────────────
        bc1, bc2, bc3 = st.columns(3)
        with bc1:
            submitted = st.form_submit_button(
                "Applica filtri", use_container_width=True, type="primary",
            )
        with bc2:
            sel_all = st.form_submit_button(
                "Seleziona tutto", use_container_width=True,
            )
        with bc3:
            desel_all = st.form_submit_button(
                "Deseleziona tutto", use_container_width=True,
            )

    # ── Handle form actions (after form block — NO direct session_state writes
    #    to widget keys here; all deferred to next rerun via pending flags) ────
    if sel_all:
        st.session_state[_sk(key_prefix, "pending_set")] = True
        st.rerun()
    if desel_all:
        st.session_state[_sk(key_prefix, "pending_set")] = False
        st.rerun()

    if submitted:
        # Check if any parent toggle changed vs its children — if so, defer
        # the children sync to next rerun via pending_toggles.
        toggles: dict[str, bool] = {}
        for cat in categories:
            cat_name = cat["name"]
            cat_key = _sk(key_prefix, "cat", cat_name)
            subs = cat.get("subcategories", [])
            if not subs:
                continue
            new_cat_val = st.session_state.get(cat_key, True)
            child_states = [
                st.session_state.get(
                    _sk(key_prefix, "sub", cat_name, s["name"] if isinstance(s, dict) else s),
                    True,
                )
                for s in subs
            ]
            all_match = all(c == new_cat_val for c in child_states)
            if not all_match:
                toggles[cat_name] = new_cat_val

        if toggles:
            st.session_state[_sk(key_prefix, "pending_toggles")] = toggles
            st.rerun()

        # No parent toggles — just re-read the current widget values
        selected_categories.clear()
        selected_subcategories.clear()
        if show_contexts:
            selected_contexts.clear()
            for ctx in contexts:
                if st.session_state.get(_sk(key_prefix, "ctx", ctx), True):
                    selected_contexts.append(ctx)

        for cat in categories:
            cat_name = cat["name"]
            subs = cat.get("subcategories", [])
            cat_key = _sk(key_prefix, "cat", cat_name)
            for sub in subs:
                sub_name = sub["name"] if isinstance(sub, dict) else sub
                if st.session_state.get(_sk(key_prefix, "sub", cat_name, sub_name), True):
                    selected_subcategories.append(sub_name)
            fs = [
                st.session_state.get(
                    _sk(key_prefix, "sub", cat_name, s["name"] if isinstance(s, dict) else s),
                    True,
                )
                for s in subs
            ]
            if any(fs) or (not subs and st.session_state.get(cat_key, True)):
                selected_categories.append(cat_name)

    return {
        "selected_contexts": selected_contexts,
        "selected_categories": selected_categories,
        "selected_subcategories": selected_subcategories,
    }


# ---------------------------------------------------------------------------
# Helper to build the categories list from SettingsService.get_taxonomy_raw()
# ---------------------------------------------------------------------------

def build_tree_data(cfg_svc, type_key: str = "expense") -> list[dict]:
    """Build the ``categories`` list expected by ``render_tree_filter``
    from the raw taxonomy rows returned by ``SettingsService.get_taxonomy_raw()``.
    """
    cat_rows, sub_rows = cfg_svc.get_taxonomy_raw(type_key)

    subs_by_cat: dict[int, list[dict]] = {}
    for s in sub_rows:
        subs_by_cat.setdefault(s.category_id, []).append({"name": s.name})

    return [
        {
            "name": c.name,
            "type": c.type,
            "subcategories": sorted(
                subs_by_cat.get(c.id, []), key=lambda x: x["name"].lower()
            ),
        }
        for c in sorted(cat_rows, key=lambda c: c.name.lower())
    ]


def build_full_tree_data(cfg_svc) -> list[dict]:
    """Build a combined tree (expense + income) for ``render_tree_filter``."""
    return build_tree_data(cfg_svc, "expense") + build_tree_data(cfg_svc, "income")
