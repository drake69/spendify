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


def _set_all(
    key_prefix: str,
    categories: list[dict],
    contexts: list[str],
    show_contexts: bool,
    value: bool,
) -> None:
    """Set every node to *value* (select all / deselect all)."""
    if show_contexts:
        for ctx in contexts:
            st.session_state[_sk(key_prefix, "ctx", ctx)] = value

    for cat in categories:
        st.session_state[_sk(key_prefix, "cat", cat["name"])] = value
        for sub in cat.get("subcategories", []):
            sub_name = sub["name"] if isinstance(sub, dict) else sub
            st.session_state[_sk(key_prefix, "sub", cat["name"], sub_name)] = value


def _sync_parent_from_children(key_prefix: str, cat: dict) -> None:
    """If all children are checked, check parent; if none, uncheck; otherwise leave."""
    subs = cat.get("subcategories", [])
    if not subs:
        return
    states = [
        st.session_state.get(
            _sk(key_prefix, "sub", cat["name"], s["name"] if isinstance(s, dict) else s),
            True,
        )
        for s in subs
    ]
    all_on = all(states)
    any_on = any(states)
    # Parent follows majority: checked if all, unchecked if none
    st.session_state[_sk(key_prefix, "cat", cat["name"])] = all_on if all_on else any_on


def _toggle_children(key_prefix: str, cat: dict, value: bool) -> None:
    """Set all subcategories of *cat* to *value*."""
    for sub in cat.get("subcategories", []):
        sub_name = sub["name"] if isinstance(sub, dict) else sub
        st.session_state[_sk(key_prefix, "sub", cat["name"], sub_name)] = value


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_tree_filter(
    categories: list[dict],
    contexts: list[str],
    key_prefix: str = "tree",
    show_contexts: bool = True,
) -> dict:
    """Render a collapsible taxonomy tree with checkboxes.

    Parameters
    ----------
    categories : list[dict]
        Each dict must have ``name`` (str), ``type`` (str, "expense"/"income"),
        and ``subcategories`` (list of dicts with ``name`` key, or list of str).
    contexts : list[str]
        The list of context labels (e.g. ``["Quotidianità", "Lavoro"]``).
    key_prefix : str
        Unique prefix so multiple instances on the same page don't clash.
    show_contexts : bool
        If ``True`` render context-level checkboxes above the category tree.

    Returns
    -------
    dict with keys ``selected_contexts``, ``selected_categories``,
    ``selected_subcategories``.
    """
    _init_defaults(key_prefix, categories, contexts, show_contexts)

    # ── Select-all / Deselect-all buttons ─────────────────────────────────
    btn_cols = st.columns(2)
    with btn_cols[0]:
        if st.button("Seleziona tutto", key=_sk(key_prefix, "sel_all"),
                      use_container_width=True):
            _set_all(key_prefix, categories, contexts, show_contexts, True)
            st.rerun()
    with btn_cols[1]:
        if st.button("Deseleziona tutto", key=_sk(key_prefix, "desel_all"),
                      use_container_width=True):
            _set_all(key_prefix, categories, contexts, show_contexts, False)
            st.rerun()

    # ── Contexts ──────────────────────────────────────────────────────────
    selected_contexts: list[str] = []
    if show_contexts and contexts:
        st.markdown("**Contesti**")
        for ctx in contexts:
            ctx_key = _sk(key_prefix, "ctx", ctx)
            val = st.checkbox(ctx, value=st.session_state.get(ctx_key, True), key=ctx_key)
            if val:
                selected_contexts.append(ctx)
        st.markdown("---")

    # ── Categories & subcategories ────────────────────────────────────────
    selected_categories: list[str] = []
    selected_subcategories: list[str] = []

    for cat in categories:
        cat_name = cat["name"]
        subs = cat.get("subcategories", [])
        cat_key = _sk(key_prefix, "cat", cat_name)

        # Determine tri-state indicator
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
            indicator = "☐…"  # partial
        else:
            indicator = "☐"

        with st.expander(f"{indicator} **{cat_name}** ({n_on}/{len(subs)})"):
            # Category-level toggle (toggles all children)
            prev_cat_val = st.session_state.get(cat_key, True)
            cat_val = st.checkbox(
                f"Tutte le sottocategorie di {cat_name}",
                value=prev_cat_val,
                key=cat_key,
            )
            # If user toggled the parent checkbox, propagate to children
            if cat_val != prev_cat_val:
                _toggle_children(key_prefix, cat, cat_val)
                st.rerun()

            # Individual subcategory checkboxes
            if subs:
                for sub in subs:
                    sub_name = sub["name"] if isinstance(sub, dict) else sub
                    sub_key = _sk(key_prefix, "sub", cat_name, sub_name)
                    sub_val = st.checkbox(
                        f"  {sub_name}",
                        value=st.session_state.get(sub_key, True),
                        key=sub_key,
                    )
                    if sub_val:
                        selected_subcategories.append(sub_name)

            # Sync parent from children state
            _sync_parent_from_children(key_prefix, cat)

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

    Parameters
    ----------
    cfg_svc : SettingsService
    type_key : str  ("expense" or "income")

    Returns
    -------
    list[dict] — each with ``name``, ``type``, ``subcategories``.
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
