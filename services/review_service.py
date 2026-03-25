"""ReviewService — orchestrates LLM re-run, transfer detection and bulk description fixes.

All three operations were previously embedded as private functions in ui/review_page.py.
Moving them here gives:
  - a testable unit independent of Streamlit
  - a single place to wire core.* imports
  - a clean import boundary: ui/ never touches core.* or db.* directly
"""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal as _Decimal

import pandas as pd
from sqlalchemy.orm import sessionmaker

from db.models import Transaction, get_session
from db import repository


class ReviewService:
    def __init__(self, engine) -> None:
        self.engine = engine
        self._Session = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def _session(self):
        s = self._Session()
        try:
            yield s
        finally:
            s.close()

    # ── Utility queries ───────────────────────────────────────────────────────

    _CATEGORIZABLE_TYPES = ("expense", "income", "card_tx", "unknown")

    def count_to_review(self) -> int:
        """Count transactions with to_review=True and categorizable tx_type."""
        with self._session() as s:
            return (
                s.query(Transaction)
                .filter(
                    Transaction.to_review == True,  # noqa: E712
                    Transaction.tx_type.in_(self._CATEGORIZABLE_TYPES),
                )
                .count()
            )

    def count_similar_by_description(self, description: str, exclude_id: str) -> int:
        """Count transactions with the same description, excluding the given id."""
        with self._session() as s:
            return (
                s.query(Transaction)
                .filter(
                    Transaction.description == description,
                    Transaction.id != exclude_id,
                )
                .count()
            )

    # ── LLM re-run ────────────────────────────────────────────────────────────

    def rerun_llm_on_review(self) -> tuple[int, int]:
        """Re-run description cleaning + categorization on all to_review=True transactions.

        - Always cleans description (if raw_description differs case-insensitively).
        - Re-categorizes unless category_source is 'manual' or 'rule'.

        Returns (n_cleaned, n_categorized).
        """
        from core.description_cleaner import clean_descriptions_batch
        from core.categorizer import categorize_batch
        from core.orchestrator import ProcessingConfig, _build_backend, _get_fallback_backend
        from core.sanitizer import SanitizationConfig

        with self._session() as s:
            settings = repository.get_all_user_settings(s)

        owner_names = [n.strip() for n in settings.get("owner_names", "").split(",") if n.strip()]
        config = ProcessingConfig(
            llm_backend=settings.get("llm_backend", "local_ollama"),
            sanitize_config=SanitizationConfig(
                owner_names=owner_names,
                description_language=settings.get("description_language", "it"),
            ),
            ollama_base_url=settings.get("ollama_base_url", "http://localhost:11434"),
            ollama_model=settings.get("ollama_model", "gemma3:12b"),
            openai_model=settings.get("openai_model", "gpt-4o-mini"),
            openai_api_key=settings.get("openai_api_key", ""),
            claude_model=settings.get("anthropic_model", "claude-3-5-haiku-20241022"),
            anthropic_api_key=settings.get("anthropic_api_key", ""),
            compat_base_url=settings.get("compat_base_url", ""),
            compat_api_key=settings.get("compat_api_key", ""),
            compat_model=settings.get("compat_model", ""),
            description_language=settings.get("description_language", "it"),
        )
        backend = _build_backend(config)
        fallback = _get_fallback_backend(config)

        # Load all non-giroconto transactions that still need review
        with self._session() as s:
            uncleaned = (
                s.query(Transaction)
                .filter(
                    Transaction.to_review == True,  # noqa: E712
                    Transaction.tx_type.in_(self._CATEGORIZABLE_TYPES),
                )
                .all()
            )
            tx_dicts = [
                {
                    "id": tx.id,
                    "description": tx.description or "",
                    "raw_description": tx.raw_description or "",
                    "tx_type": tx.tx_type or "unknown",
                    "amount": float(tx.amount or 0),
                    "date": tx.date,
                    "source_file": tx.source_file or "",
                    "category_source": tx.category_source or "",
                }
                for tx in uncleaned
            ]

        if not tx_dicts:
            return 0, 0

        # Re-run description cleaner
        tx_dicts = clean_descriptions_batch(
            tx_dicts,
            llm_backend=backend,
            fallback_backend=fallback,
            source_name="review_rerun",
            sanitize_config=config.sanitize_config,
        )
        n_cleaned = sum(1 for tx in tx_dicts if tx["description"] != tx["raw_description"])

        # Re-run categorizer on categorizable types — skip manual/rule-categorized
        _protected = {"manual", "rule"}
        to_categorize = [
            t for t in tx_dicts
            if t["tx_type"] in set(self._CATEGORIZABLE_TYPES)
            and t.get("category_source") not in _protected
        ]

        cat_map: dict = {}
        if to_categorize:
            with self._session() as s:
                taxonomy = repository.get_taxonomy_config(s)
                user_rules = repository.get_category_rules(s)
            cat_results = categorize_batch(
                transactions=to_categorize,
                taxonomy=taxonomy,
                user_rules=user_rules,
                llm_backend=backend,
                sanitize_config=config.sanitize_config,
                fallback_backend=fallback,
                confidence_threshold=config.confidence_threshold,
                description_language=config.description_language,
                source_name="review_rerun",
            )
            cat_map = {tx["id"]: result for tx, result in zip(to_categorize, cat_results)}

        # Write results back to DB
        n_categorized = 0
        with self._session() as s:
            for tx_dict in tx_dicts:
                tx = s.get(Transaction, tx_dict["id"])
                if tx is None:
                    continue
                tx.description = tx_dict["description"]
                result = cat_map.get(tx_dict["id"])
                if result and result.category:
                    tx.category = result.category
                    tx.subcategory = result.subcategory
                    tx.category_confidence = result.confidence.value
                    tx.category_source = result.source.value
                    tx.to_review = result.to_review
                    tx.human_validated = False
                    n_categorized += 1
            s.commit()

        return n_cleaned, n_categorized

    # ── Transfer detection ────────────────────────────────────────────────────

    def rerun_transfer_detection(self) -> int:
        """Re-run detect_internal_transfers on ALL non-giroconto transactions.

        Finds cross-account pairs that couldn't be matched at import time because
        the counterpart file hadn't been imported yet.

        Returns count of transactions whose tx_type was updated.
        """
        import pandas as _pd
        from datetime import date as _date
        from core.normalizer import detect_internal_transfers

        _non_internal = {"expense", "income", "card_tx", "unknown"}

        with self._session() as s:
            settings = repository.get_all_user_settings(s)
            keyword_patterns = repository.get_all_transfer_keyword_patterns(s)
            txs = (
                s.query(Transaction)
                .filter(Transaction.tx_type.in_(_non_internal))
                .all()
            )
            rows = [
                {
                    "id": tx.id,
                    "date": tx.date,
                    "amount": _Decimal(str(tx.amount or 0)),
                    "description": tx.description or "",
                    "account_label": tx.account_label or "",
                    "tx_type": tx.tx_type or "unknown",
                    "transfer_pair_id": tx.transfer_pair_id,
                    "transfer_confidence": tx.transfer_confidence,
                }
                for tx in txs
            ]

        if not rows:
            return 0

        owner_names = [n.strip() for n in settings.get("owner_names", "").split(",") if n.strip()]
        use_owner = settings.get("use_owner_names_giroconto", "false").lower() == "true"

        df = _pd.DataFrame(rows)
        df["date"] = _pd.to_datetime(df["date"]).dt.date

        df_result = detect_internal_transfers(
            df,
            keyword_patterns=keyword_patterns,
            owner_names=owner_names if use_owner else None,
        )

        changed = df_result[df_result["tx_type"] != df["tx_type"]]
        if changed.empty:
            return 0

        with self._session() as s:
            updated = 0
            for _, row in changed.iterrows():
                tx = s.get(Transaction, row["id"])
                if tx is None:
                    continue
                tx.tx_type = row["tx_type"]
                tx.transfer_pair_id = row.get("transfer_pair_id") or tx.transfer_pair_id
                tx.transfer_confidence = row.get("transfer_confidence") or tx.transfer_confidence
                updated += 1
            if updated:
                s.commit()

        return updated

    # ── Bulk description fix ──────────────────────────────────────────────────

    def apply_description_rule_bulk(
        self,
        raw_pattern: str,
        match_type: str,
        cleaned_description: str,
    ) -> tuple[int, int]:
        """Update description for all matching transactions, then re-categorize with LLM.

        Returns (n_updated, n_categorized).
        """
        from core.categorizer import categorize_batch
        from core.orchestrator import ProcessingConfig, _build_backend, _get_fallback_backend
        from core.sanitizer import SanitizationConfig

        with self._session() as s:
            settings = repository.get_all_user_settings(s)
            matching = repository.get_transactions_by_raw_pattern(s, raw_pattern, match_type)
            matching_ids = [tx.id for tx in matching]
            tx_dicts = [
                {
                    "id": tx.id,
                    "description": cleaned_description,
                    "raw_description": tx.raw_description or "",
                    "tx_type": tx.tx_type or "unknown",
                    "amount": float(tx.amount or 0),
                    "date": tx.date,
                    "source_file": tx.source_file or "",
                }
                for tx in matching
            ]
            for tx in matching:
                tx.description = cleaned_description
            s.commit()

        n_updated = len(matching_ids)
        if n_updated == 0:
            return 0, 0

        owner_names = [n.strip() for n in settings.get("owner_names", "").split(",") if n.strip()]
        config = ProcessingConfig(
            llm_backend=settings.get("llm_backend", "local_ollama"),
            sanitize_config=SanitizationConfig(
                owner_names=owner_names,
                description_language=settings.get("description_language", "it"),
            ),
            ollama_base_url=settings.get("ollama_base_url", "http://localhost:11434"),
            ollama_model=settings.get("ollama_model", "gemma3:12b"),
            openai_model=settings.get("openai_model", "gpt-4o-mini"),
            openai_api_key=settings.get("openai_api_key", ""),
            claude_model=settings.get("anthropic_model", "claude-3-5-haiku-20241022"),
            anthropic_api_key=settings.get("anthropic_api_key", ""),
            compat_base_url=settings.get("compat_base_url", ""),
            compat_api_key=settings.get("compat_api_key", ""),
            compat_model=settings.get("compat_model", ""),
            description_language=settings.get("description_language", "it"),
        )
        backend = _build_backend(config)
        fallback = _get_fallback_backend(config)

        _categorizable = {"expense", "income", "card_tx", "unknown"}
        to_categorize = [t for t in tx_dicts if t["tx_type"] in _categorizable]

        n_categorized = 0
        if to_categorize:
            with self._session() as s:
                taxonomy = repository.get_taxonomy_config(s)
                user_rules = repository.get_category_rules(s)

            cat_results = categorize_batch(
                transactions=to_categorize,
                taxonomy=taxonomy,
                user_rules=user_rules,
                llm_backend=backend,
                sanitize_config=config.sanitize_config,
                fallback_backend=fallback,
                confidence_threshold=config.confidence_threshold,
                description_language=config.description_language,
                source_name="desc_rule_bulk",
            )
            cat_map = {tx["id"]: result for tx, result in zip(to_categorize, cat_results)}

            with self._session() as s:
                for tx_id in matching_ids:
                    tx = s.get(Transaction, tx_id)
                    if tx is None:
                        continue
                    result = cat_map.get(tx_id)
                    if result and result.category:
                        tx.category = result.category
                        tx.subcategory = result.subcategory
                        tx.category_confidence = result.confidence.value
                        tx.category_source = result.source.value
                        tx.to_review = result.to_review
                        tx.human_validated = False
                        n_categorized += 1
                s.commit()

        return n_updated, n_categorized

    # ── Pipeline rerun on arbitrary tx set ────────────────────────────────────

    def rerun_pipeline_on_txs(
        self,
        tx_ids: list[str],
        run_cleaner: bool = True,
        run_categorizer: bool = True,
        categorizer_progress_callback=None,
    ) -> tuple[int, int, int]:
        """Re-run description cleaning and/or categorization on specific transaction IDs.

        Returns (n_desc_updated, n_cat_updated, n_review).
        """
        from core.description_cleaner import clean_descriptions_batch
        from core.categorizer import categorize_batch
        from core.orchestrator import ProcessingConfig, _build_backend, _get_fallback_backend
        from core.sanitizer import SanitizationConfig

        with self._session() as s:
            settings = repository.get_all_user_settings(s)
            txs = s.query(Transaction).filter(Transaction.id.in_(tx_ids)).all()
            tx_dicts = [
                {
                    "id": tx.id,
                    "description": tx.description or "",
                    "raw_description": tx.raw_description or "",
                    "amount": float(tx.amount or 0),
                    "doc_type": tx.doc_type or "",
                    "tx_type": tx.tx_type or "unknown",
                    "category_source": tx.category_source or "",
                }
                for tx in txs
            ]
            user_rules = repository.get_category_rules(s)
            taxonomy = repository.get_taxonomy_config(s)

        if not tx_dicts:
            return 0, 0, 0

        owner_names = [n.strip() for n in settings.get("owner_names", "").split(",") if n.strip()]
        config = ProcessingConfig(
            llm_backend=settings.get("llm_backend", "local_ollama"),
            sanitize_config=SanitizationConfig(
                owner_names=owner_names,
                description_language=settings.get("description_language", "it"),
            ),
            ollama_base_url=settings.get("ollama_base_url", "http://localhost:11434"),
            ollama_model=settings.get("ollama_model", "gemma3:12b"),
            openai_model=settings.get("openai_model", "gpt-4o-mini"),
            openai_api_key=settings.get("openai_api_key", ""),
            claude_model=settings.get("anthropic_model", "claude-3-5-haiku-20241022"),
            anthropic_api_key=settings.get("anthropic_api_key", ""),
            compat_base_url=settings.get("compat_base_url", ""),
            compat_api_key=settings.get("compat_api_key", ""),
            compat_model=settings.get("compat_model", ""),
            description_language=settings.get("description_language", "it"),
        )
        backend = _build_backend(config)
        fallback = _get_fallback_backend(config)

        if run_cleaner:
            tx_dicts = clean_descriptions_batch(
                tx_dicts, backend, fallback,
                source_name="bulk_rerun",
                sanitize_config=config.sanitize_config,
            )

        cat_results = None
        if run_categorizer:
            cat_results = categorize_batch(
                tx_dicts, taxonomy, user_rules, backend,
                sanitize_config=config.sanitize_config,
                fallback_backend=fallback,
                description_language=config.description_language,
                source_name="bulk_rerun",
                progress_callback=categorizer_progress_callback,
            )

        n_desc_updated = n_cat_updated = n_review = 0
        with self._session() as s:
            for i, td in enumerate(tx_dicts):
                tx = s.get(Transaction, td["id"])
                if tx is None:
                    continue
                if run_cleaner:
                    new_desc = td.get("description", "")
                    if new_desc and new_desc != (tx.description or ""):
                        tx.description = new_desc
                        n_desc_updated += 1
                if run_categorizer and cat_results:
                    r = cat_results[i]
                    tx.category = r.category
                    tx.subcategory = r.subcategory
                    tx.category_confidence = (
                        r.confidence.value if hasattr(r.confidence, "value") else str(r.confidence)
                    )
                    tx.category_source = (
                        r.source.value if hasattr(r.source, "value") else str(r.source)
                    )
                    tx.to_review = r.to_review
                    tx.human_validated = False
                    n_cat_updated += 1
                    if r.to_review:
                        n_review += 1
            s.commit()

        return n_desc_updated, n_cat_updated, n_review
