"""Ledger page (RF-08): editable transaction table + export."""
from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
import streamlit as st

from services.rule_service import RuleService
from services.settings_service import SettingsService
from services.transaction_service import TransactionService
from support.formatting import format_amount_display, format_date_display
from support.logging import setup_logging

logger = setup_logging()

EXCLUDED_FROM_BALANCE = {"internal_out", "internal_in", "card_settlement", "aggregate_debit"}
_ALL_TX_TYPES = [
    "tutti", "expense", "income", "card_tx",
    "internal_out", "internal_in", "card_settlement", "unknown",
]


def render_registry_page(engine):
    st.header("📋 Ledger — Registro Transazioni")

    cfg_svc  = SettingsService(engine)
    tx_svc   = TransactionService(engine)
    rule_svc = RuleService(engine)

    # ── Settings & taxonomy ────────────────────────────────────────────────────
    settings = cfg_svc.get_all()
    taxonomy = cfg_svc.get_taxonomy()

    _date_fmt = settings.get("date_display_format", "%d/%m/%Y")
    _dec = settings.get("amount_decimal_sep", ",")
    _thou = settings.get("amount_thousands_sep", ".")
    giroconto_mode = settings.get("giroconto_mode", "neutral")

    try:
        _contexts: list[str] = json.loads(
            settings.get("contexts", '["Quotidianità", "Lavoro", "Vacanza"]')
        )
    except Exception:
        _contexts = ["Quotidianità", "Lavoro", "Vacanza"]

    _expense_cats = sorted(taxonomy.all_expense_categories)
    _income_cats  = sorted(taxonomy.all_income_categories)
    _all_cats     = _expense_cats + _income_cats
    _all_sub      = sorted({
        sub
        for cat in _all_cats
        for sub in taxonomy.valid_subcategories(cat)
    })

    _accounts = tx_svc.get_distinct_account_labels()
    today = date.today()

    # ── Date preset initialisation (default: mese corrente) ───────────────────
    if "ledger_from" not in st.session_state:
        st.session_state["ledger_from"] = today.replace(day=1)
    if "ledger_to" not in st.session_state:
        st.session_state["ledger_to"] = today

    _first_cur  = today.replace(day=1)
    _three_ago  = today - timedelta(days=90)
    _first_year = today.replace(month=1, day=1)

    _cur_from = st.session_state.get("ledger_from", _first_cur)
    if not isinstance(_cur_from, date):
        _cur_from = _first_cur
    _rel_last_prev  = _cur_from - timedelta(days=1)
    _rel_first_prev = _rel_last_prev.replace(day=1)

    st.caption("**Periodo rapido**")
    pc1, pc2, pc3, pc4, pc5 = st.columns(5)
    if pc1.button("📅 Mese corrente",  key="preset_cur",  use_container_width=True):
        st.session_state["ledger_from"] = _first_cur
        st.session_state["ledger_to"]   = today
    if pc2.button("⏮ Mese precedente", key="preset_prev", use_container_width=True):
        st.session_state["ledger_from"] = _rel_first_prev
        st.session_state["ledger_to"]   = _rel_last_prev
    if pc3.button("📆 Ultimi 3 mesi",  key="preset_3m",  use_container_width=True):
        st.session_state["ledger_from"] = _three_ago
        st.session_state["ledger_to"]   = today
    if pc4.button("🗓 Anno corrente",   key="preset_year", use_container_width=True):
        st.session_state["ledger_from"] = _first_year
        st.session_state["ledger_to"]   = today
    if pc5.button("♾ Tutto",            key="preset_all",  use_container_width=True):
        for _k in ("ledger_from", "ledger_to", "ledger_account", "ledger_type",
                   "ledger_cat", "ledger_desc", "ledger_review"):
            if _k in st.session_state:
                del st.session_state[_k]
        st.rerun()

    # ── Filter row ─────────────────────────────────────────────────────────────
    fc1, fc2, fc3, fc4 = st.columns(4)
    with fc1:
        date_from = st.date_input("Da", key="ledger_from")
    with fc2:
        date_to = st.date_input("A", key="ledger_to")
    with fc3:
        account_filter = st.selectbox(
            "Conto", ["tutti i conti"] + _accounts, key="ledger_account"
        )
    with fc4:
        tx_type_filter = st.selectbox("Tipo", _ALL_TX_TYPES, key="ledger_type")

    fc5, fc6, fc7, fc8, fc9 = st.columns([3, 2, 1, 1, 1])
    with fc5:
        desc_filter = st.text_input(
            "🔍 Descrizione", placeholder="cerca in descrizione e raw…", key="ledger_desc"
        )
    with fc6:
        cat_filter = st.selectbox(
            "Categoria", ["tutte"] + _all_cats, key="ledger_cat"
        )
    with fc7:
        review_only = st.checkbox("Solo da rivedere ⚠️", key="ledger_review")
    with fc8:
        hide_giro = st.checkbox(
            "Nascondi giroconti",
            key="ledger_hide_giro",
            value=st.session_state.get("ledger_hide_giro", giroconto_mode == "exclude"),
        )
    with fc9:
        show_raw = st.checkbox("Mostra raw", key="ledger_show_raw")

    # ── Build query filters ────────────────────────────────────────────────────
    filters: dict = {}
    if date_from:
        filters["date_from"] = date_from.isoformat()
    if date_to:
        filters["date_to"] = date_to.isoformat()
    if account_filter != "tutti i conti":
        filters["account_label"] = account_filter
    if tx_type_filter != "tutti":
        filters["tx_type"] = tx_type_filter
    elif hide_giro:
        filters["exclude_tx_types"] = ["internal_in", "internal_out"]
    if desc_filter.strip():
        filters["description"] = desc_filter.strip()
    if cat_filter != "tutte":
        filters["category"] = cat_filter
    if review_only:
        filters["to_review"] = True

    txs = tx_svc.get_transactions(filters=filters)

    if not txs:
        st.info("Nessuna transazione trovata con i filtri selezionati.")
        return

    # ── Metrics ────────────────────────────────────────────────────────────────
    _bal_txs = [tx for tx in txs if tx.tx_type not in EXCLUDED_FROM_BALANCE]
    net       = sum(Decimal(str(tx.amount)) for tx in _bal_txs)
    income_t  = sum(Decimal(str(tx.amount)) for tx in _bal_txs if Decimal(str(tx.amount)) > 0)
    expense_t = sum(Decimal(str(tx.amount)) for tx in _bal_txs if Decimal(str(tx.amount)) < 0)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Transazioni", len(txs))
    m2.metric("Saldo netto",  format_amount_display(float(net),            _dec, _thou))
    m3.metric("Entrate",      format_amount_display(float(income_t),       _dec, _thou))
    m4.metric("Uscite",       format_amount_display(float(abs(expense_t)), _dec, _thou))

    # ── Pagination ────────────────────────────────────────────────────────────
    _pg1, _pg2 = st.columns([1, 4])
    with _pg1:
        rows_per_page = st.selectbox(
            "Righe/pagina", [15, 25, 50, 100, 200], index=0, key="ledger_page_size"
        )

    total_rows  = len(txs)
    total_pages = max(1, -(-total_rows // rows_per_page))

    _fp = f"{total_rows}_{sorted(filters.items())}"
    if st.session_state.get("_ledger_fp") != _fp:
        st.session_state["_ledger_fp"] = _fp
        st.session_state["ledger_page"] = 0

    page_num  = max(0, min(st.session_state.get("ledger_page", 0), total_pages - 1))
    page_start = page_num * rows_per_page
    page_end   = min(page_start + rows_per_page, total_rows)
    page_txs   = txs[page_start:page_end]

    with _pg2:
        st.caption(
            f"Pagina **{page_num + 1}** / **{total_pages}** "
            f"· righe {page_start + 1}–{page_end} di {total_rows}"
        )

    # ── Editable table ────────────────────────────────────────────────────────
    st.caption(
        "✏️ Modifica **Categoria**, **Sottocategoria**, **Contesto** e **🔄 Giroconto** "
        "direttamente nella tabella, poi clicca **Salva modifiche**."
    )

    _SOURCE_BADGE = {
        "llm": "🧠 AI",
        "rule": "📏 Regola",
        "manual": "👤 Manuale",
        "history": "📚 Storico",
    }

    orig_rows = [
        {
            "_id":           tx.id,
            "_sel":          False,
            "Data":          format_date_display(tx.date, _date_fmt),
            "Descrizione":   (tx.description or "")[:80],
            **({"Raw": (tx.raw_description or "")[:80]} if show_raw else {}),
            "Entrata":       float(tx.amount) if float(tx.amount) > 0 else None,
            "Uscita":        abs(float(tx.amount)) if float(tx.amount) < 0 else None,
            "Conto":         tx.account_label or "",
            "Tipo":          tx.tx_type or "",
            "Categoria":     tx.category or "",
            "Sottocategoria": tx.subcategory or "",
            "Contesto":      tx.context or "",
            "Fonte":         _SOURCE_BADGE.get(tx.category_source, "—"),
            "⚠️":            "⚠️" if tx.to_review else "·",
            "✅":            "✅" if tx.human_validated else "·",
            "🔄":            "🔄" if tx.tx_type in ("internal_out", "internal_in") else "·",
            "Validato":      bool(tx.human_validated),
            "🔄 Giroconto":  tx.tx_type in ("internal_out", "internal_in"),
        }
        for tx in page_txs
    ]
    orig_df = pd.DataFrame(orig_rows)

    _col_cfg: dict = {
        "_id":            None,
        "_sel":           st.column_config.CheckboxColumn("📏", width=40),
        "Data":           st.column_config.TextColumn("Data",         disabled=True, width="small"),
        "Descrizione":    st.column_config.TextColumn("Descrizione",  disabled=True),
        "Entrata":        st.column_config.NumberColumn("Entrata",    disabled=True, format="%.2f", width="small"),
        "Uscita":         st.column_config.NumberColumn("Uscita",     disabled=True, format="%.2f", width="small"),
        "Conto":          st.column_config.TextColumn("Conto",        disabled=True, width="small"),
        "Tipo":           st.column_config.TextColumn("Tipo",         disabled=True, width="small"),
        "Categoria":      st.column_config.SelectboxColumn(
            "Categoria", options=[""] + _all_cats, required=False, width="medium",
        ),
        "Sottocategoria": st.column_config.SelectboxColumn(
            "Sottocategoria", options=[""] + _all_sub, required=False, width="medium",
        ),
        "Contesto":       st.column_config.SelectboxColumn(
            "Contesto", options=[""] + _contexts, required=False, width="small",
        ),
        "Fonte":          st.column_config.TextColumn("Fonte", disabled=True, width=100),
        "⚠️":             st.column_config.TextColumn("⚠️", disabled=True, width=40),
        "✅":             st.column_config.TextColumn("✅", disabled=True, width=40),
        "🔄":             st.column_config.TextColumn("🔄", disabled=True, width=40),
        "Validato":       st.column_config.CheckboxColumn("Validato", width=60),
        "🔄 Giroconto":   st.column_config.CheckboxColumn("🔄 Giroconto", width="small"),
    }
    if show_raw:
        _col_cfg["Raw"] = st.column_config.TextColumn(
            "Raw description", disabled=True, width="medium"
        )

    edited_df = st.data_editor(
        orig_df,
        use_container_width=True,
        hide_index=True,
        height=min(650, 42 + len(orig_df) * 35),
        column_config=_col_cfg,
        key="ledger_editor",
    )

    # ── Enforce single selection for rule creation ──────────────────────────
    _sel_indices = [i for i in range(len(edited_df)) if edited_df.iloc[i].get("_sel", False)]
    if len(_sel_indices) > 1:
        st.error("⚠️ Si può creare ed applicare una regola alla volta — seleziona una sola riga")

    # ── Auto-save Validato checkbox changes (realtime) ───────────────────────
    _n_auto_val = 0
    for i in range(len(edited_df)):
        new_val = bool(edited_df.iloc[i].get("Validato", False))
        old_val = bool(orig_df.iloc[i].get("Validato", False))
        if new_val != old_val:
            _tid = orig_df.iloc[i]["_id"]
            if new_val:
                tx_svc.validate(_tid)
            else:
                tx_svc.unvalidate(_tid)
            _n_auto_val += 1
    if _n_auto_val:
        st.toast(f"✅ {_n_auto_val} validazioni aggiornate")
        logger.info(f"ledger_page: auto-saved {_n_auto_val} validation changes")
        st.rerun()

    # ── Save & Validate buttons ──────────────────────────────────────────────
    sv_col, val_col, _ = st.columns([1, 1, 4])
    with sv_col:
        save_clicked = st.button("💾 Salva modifiche", type="primary", key="ledger_save",
                                 use_container_width=True)
    with val_col:
        _sel_ids = [
            orig_df.iloc[i]["_id"]
            for i in range(len(edited_df))
            if edited_df.iloc[i].get("_sel", False)
        ]
        if st.button("✅ Valida selezionate", disabled=len(_sel_ids) == 0,
                      key="ledger_validate_bulk", use_container_width=True):
            n_ok = 0
            for _tid in _sel_ids:
                if tx_svc.validate(_tid):
                    n_ok += 1
            st.success(f"✅ {n_ok} transazioni validate.")
            logger.info(f"ledger_page: validated {n_ok} transactions")
            st.rerun()

    if save_clicked:
        logger.info("ledger_page: save_clicked=True, comparing %d rows", len(orig_df))
        n_cat  = 0
        n_ctx  = 0
        n_giro = 0
        n_val  = 0
        _fan_out_candidates: list[tuple[str, str]] = []  # (tx_id, description) for fan-out check
        for idx in range(len(orig_df)):
            orig = orig_df.iloc[idx]
            edit = edited_df.iloc[idx]
            tx_id = orig["_id"]

            cat_changed  = str(edit["Categoria"])      != str(orig["Categoria"])
            sub_changed  = str(edit["Sottocategoria"]) != str(orig["Sottocategoria"])
            ctx_changed  = str(edit["Contesto"])       != str(orig["Contesto"])
            giro_changed = bool(edit["🔄 Giroconto"])  != bool(orig["🔄 Giroconto"])
            val_changed  = bool(edit["Validato"])       != bool(orig["Validato"])

            if cat_changed or sub_changed:
                tx_svc.update_category(
                    tx_id,
                    edit["Categoria"]      or orig["Categoria"],
                    edit["Sottocategoria"] or orig["Sottocategoria"],
                )
                n_cat += 1
                _desc = str(orig["Descrizione"]).strip()
                if _desc:
                    _fan_out_candidates.append((tx_id, _desc))

            if ctx_changed:
                tx_svc.update_context(tx_id, edit["Contesto"] or None)
                n_ctx += 1

            if giro_changed:
                tx_svc.toggle_giroconto(tx_id)
                n_giro += 1

            if val_changed:
                logger.info("ledger_page: tx %s val_changed: %s -> %s", tx_id, orig["Validato"], edit["Validato"])
                if bool(edit["Validato"]):
                    tx_svc.validate(tx_id)
                else:
                    tx_svc.unvalidate(tx_id)
                n_val += 1

        total_saved = n_cat + n_ctx + n_giro + n_val
        if total_saved:
            parts = []
            if n_cat:  parts.append(f"{n_cat} categorie")
            if n_ctx:  parts.append(f"{n_ctx} contesti")
            if n_giro: parts.append(f"{n_giro} giroconti")
            if n_val:  parts.append(f"{n_val} validate")
            st.success(f"✅ Salvate: {' · '.join(parts)}")
            logger.info(f"ledger_page: saved cat={n_cat} ctx={n_ctx} giro={n_giro}")

            # ── C-06: Fan-out — check for similar uncategorized transactions ──
            if _fan_out_candidates:
                _fan_out_all: dict[str, list] = {}  # tx_id -> similar txs
                for _fo_tx_id, _fo_desc in _fan_out_candidates:
                    _similar = tx_svc.find_similar_uncategorized(_fo_desc, _fo_tx_id)
                    if _similar:
                        _fan_out_all[_fo_tx_id] = _similar
                if _fan_out_all:
                    _total_similar = sum(len(v) for v in _fan_out_all.values())
                    st.session_state["_fan_out_pending"] = _fan_out_all
                    st.info(
                        f"Trovate **{_total_similar}** transazioni simili non ancora categorizzate. "
                        f"Vuoi applicare la stessa categoria?"
                    )
                else:
                    st.rerun()
            else:
                st.rerun()
        else:
            st.info("Nessuna modifica rilevata.")

    # ── C-06: Fan-out action buttons ─────────────────────────────────────────
    if st.session_state.get("_fan_out_pending"):
        _fan_out_data = st.session_state["_fan_out_pending"]
        _total_fan = sum(len(v) for v in _fan_out_data.values())
        fo_col1, fo_col2, _ = st.columns([1, 1, 4])
        with fo_col1:
            if st.button(
                f"Applica a tutte ({_total_fan})",
                key="ledger_fan_out_apply",
                type="primary",
                use_container_width=True,
            ):
                _n_applied = 0
                for _src_id, _targets in _fan_out_data.items():
                    _n_applied += tx_svc.apply_fan_out(
                        _src_id, [t.id for t in _targets]
                    )
                del st.session_state["_fan_out_pending"]
                st.toast(f"Fan-out: {_n_applied} transazioni aggiornate")
                logger.info(f"ledger_page: fan-out applied to {_n_applied} transactions")
                st.rerun()
        with fo_col2:
            if st.button(
                "No grazie",
                key="ledger_fan_out_skip",
                use_container_width=True,
            ):
                del st.session_state["_fan_out_pending"]
                st.rerun()

    # ── Crea regola dalla selezione ──────────────────────────────────────────
    if len(_sel_ids) == 1:
        _rule_tx_id = _sel_ids[0]
        _rule_tx_row = orig_df[orig_df["_id"] == _rule_tx_id].iloc[0]
        _rule_tx_desc = _rule_tx_row["Descrizione"]
        _rule_tx_cat = _rule_tx_row["Categoria"]
        _rule_tx_sub = _rule_tx_row["Sottocategoria"]
        _rule_tx_ctx = _rule_tx_row["Contesto"]

        with st.expander("📏 Crea regola dalla selezione", expanded=True):
            rc1, rc2 = st.columns([3, 1])
            with rc1:
                rule_pattern = st.text_input(
                    "Pattern", value=_rule_tx_desc, key="rule_create_pattern"
                )
            with rc2:
                _match_labels = {
                    "Contiene il testo": "contains",
                    "Uguale esatto": "exact",
                    "Espressione avanzata": "regex",
                }
                _match_label = st.selectbox(
                    "Tipo corrispondenza", list(_match_labels.keys()),
                    index=0, key="rule_create_match_type",
                )
                rule_match_type = _match_labels[_match_label]

            rc3, rc4, rc5, rc6 = st.columns(4)
            with rc3:
                _rc_cat_idx = (_all_cats.index(_rule_tx_cat)
                               if _rule_tx_cat in _all_cats else 0)
                rule_category = st.selectbox(
                    "Categoria", options=_all_cats,
                    index=_rc_cat_idx, key="rule_create_category",
                )
            with rc4:
                _rc_sub_idx = (_all_sub.index(_rule_tx_sub)
                               if _rule_tx_sub in _all_sub else 0)
                rule_subcategory = st.selectbox(
                    "Sottocategoria", options=_all_sub,
                    index=_rc_sub_idx, key="rule_create_subcategory",
                )
            with rc5:
                _ctx_options = ["— nessuno —"] + _contexts
                _rc_ctx_idx = (_ctx_options.index(_rule_tx_ctx)
                               if _rule_tx_ctx in _ctx_options else 0)
                rule_context = st.selectbox(
                    "Contesto", options=_ctx_options,
                    index=_rc_ctx_idx, key="rule_create_context",
                )
            with rc6:
                rule_priority = st.number_input(
                    "Priorità", value=10, min_value=0, max_value=100,
                    key="rule_create_priority",
                )

            # Check if rule already exists + preview
            _rule_exists = False
            if rule_pattern.strip():
                _existing_rules = rule_svc.get_rules()
                _rule_exists = any(
                    r.pattern.lower() == rule_pattern.strip().lower()
                    and r.match_type == rule_match_type
                    for r in _existing_rules
                )
                _rule_matching = tx_svc.get_by_rule_pattern(
                    rule_pattern.strip(), rule_match_type
                )
                if _rule_exists:
                    st.warning(f"⚠️ Regola già esistente — verrà aggiornata. Matcherà {len(_rule_matching)} transazioni")
                else:
                    st.info(f"Questa regola matcherà {len(_rule_matching)} transazioni")

            _btn_label = "📏 Modifica regola e applica" if _rule_exists else "📏 Crea regola e applica"
            if st.button(_btn_label, key="rule_create_apply"):
                _ctx_val = rule_context if rule_context != "— nessuno —" else None
                _, _created = rule_svc.create_rule(
                    pattern=rule_pattern.strip(),
                    match_type=rule_match_type,
                    category=rule_category,
                    subcategory=rule_subcategory,
                    context=_ctx_val,
                    priority=rule_priority,
                )
                n_matched, n_cleared = rule_svc.apply_to_all()
                _action = "creata" if _created else "aggiornata"
                st.toast(f"📏 Regola {_action} — {n_matched} transazioni aggiornate")
                logger.info(
                    f"ledger_page: rule created pattern={rule_pattern!r} "
                    f"matched={n_matched} cleared={n_cleared}"
                )
                st.rerun()
    else:
        st.caption("Seleziona una riga (📏) per creare una regola")

    # ── Page navigation ───────────────────────────────────────────────────────
    nav1, nav2, _ = st.columns([1, 1, 5])
    with nav1:
        if st.button("◀ Precedente", disabled=(page_num == 0), key="ledger_prev",
                     use_container_width=True):
            st.session_state["ledger_page"] = page_num - 1
            st.rerun()
    with nav2:
        if st.button("Successiva ▶", disabled=(page_num >= total_pages - 1), key="ledger_next",
                     use_container_width=True):
            st.session_state["ledger_page"] = page_num + 1
            st.rerun()

    # ── Export ────────────────────────────────────────────────────────────────
    st.divider()
    ec1, ec2 = st.columns(2)
    with ec1:
        csv_bytes = tx_svc.export_csv(filters=filters)
        st.download_button(
            "📥 Esporta CSV", csv_bytes, "spendify_export.csv", "text/csv",
            use_container_width=True,
        )
    with ec2:
        xlsx_bytes = tx_svc.export_xlsx(filters=filters)
        st.download_button(
            "📥 Esporta XLSX", xlsx_bytes, "spendify_export.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
