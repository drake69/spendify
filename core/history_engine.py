"""History-based auto-learning engine (C-03 / C-04 / C-05).

Queries validated transactions to build description→category associations,
computes entropy/homogeneity per description, and provides a lookup function
for history-based categorization before falling back to LLM.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import func
from sqlalchemy.orm import Session

from db.models import Transaction
from config import system_settings
from support.logging import setup_logging

logger = setup_logging()

# ── Configuration (loaded from config/system_settings.yaml) ──────────────────
_hist_cfg = system_settings.get("history", {})
HISTORY_MIN_VALIDATED = _hist_cfg.get("min_validated", 5)
HISTORY_AUTO_THRESHOLD = _hist_cfg.get("auto_threshold", 0.90)
HISTORY_SUGGEST_THRESHOLD = _hist_cfg.get("suggest_threshold", 0.50)

# C-07: LLM context injection thresholds
_ctx_cfg = system_settings.get("history_context", {})
HISTORY_CONTEXT_MIN_VALIDATED = _ctx_cfg.get("min_validated", 3)
HISTORY_CONTEXT_MIN_CONFIDENCE = _ctx_cfg.get("min_confidence", 0.50)


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class DescriptionAssociation:
    description: str
    category: str
    subcategory: str | None
    count: int  # number of validated transactions with this combination


@dataclass
class DescriptionProfile:
    description: str
    associations: list[DescriptionAssociation]
    total_validated: int
    homogeneity: float   # 1 - normalized entropy (0=dispersed, 1=uniform)
    confidence: float    # homogeneity * min(1, total/HISTORY_MIN_VALIDATED)
    top_category: str
    top_subcategory: str | None


# ── Entropy ───────────────────────────────────────────────────────────────────

def _shannon_entropy(counts: list[int]) -> float:
    """Normalized Shannon entropy. Returns 0.0-1.0."""
    total = sum(counts)
    if total == 0 or len(counts) <= 1:
        return 0.0
    probs = [c / total for c in counts]
    h = -sum(p * math.log2(p) for p in probs if p > 0)
    h_max = math.log2(len(counts))
    return h / h_max if h_max > 0 else 0.0


# ── Public API ────────────────────────────────────────────────────────────────

def get_associations(session: Session) -> list[DescriptionAssociation]:
    """Query all validated description->category associations grouped by count."""
    rows = (
        session.query(
            Transaction.description,
            Transaction.category,
            Transaction.subcategory,
            func.count().label("n"),
        )
        .filter(
            Transaction.human_validated.is_(True),
            Transaction.category.isnot(None),
        )
        .group_by(
            Transaction.description,
            Transaction.category,
            Transaction.subcategory,
        )
        .order_by(Transaction.description, func.count().desc())
        .all()
    )
    return [
        DescriptionAssociation(
            description=row[0],
            category=row[1],
            subcategory=row[2],
            count=row[3],
        )
        for row in rows
    ]


def get_description_profiles(session: Session) -> list[DescriptionProfile]:
    """Build profiles with entropy for all descriptions that have validated transactions."""
    associations = get_associations(session)

    # Group by description
    by_desc: dict[str, list[DescriptionAssociation]] = defaultdict(list)
    for assoc in associations:
        by_desc[assoc.description].append(assoc)

    profiles: list[DescriptionProfile] = []
    for desc, assocs in by_desc.items():
        total = sum(a.count for a in assocs)
        counts = [a.count for a in assocs]

        entropy = _shannon_entropy(counts)
        homogeneity = 1.0 - entropy
        confidence = homogeneity * min(1.0, total / HISTORY_MIN_VALIDATED)

        # Top category = highest count
        top = max(assocs, key=lambda a: a.count)

        profiles.append(DescriptionProfile(
            description=desc,
            associations=assocs,
            total_validated=total,
            homogeneity=homogeneity,
            confidence=confidence,
            top_category=top.category,
            top_subcategory=top.subcategory,
        ))

    return profiles


def lookup_history(
    session: Session, description: str,
) -> tuple[str | None, str | None, float]:
    """Look up a single description in the history.

    Returns (category, subcategory, confidence) or (None, None, 0.0) if no match.
    """
    rows = (
        session.query(
            Transaction.category,
            Transaction.subcategory,
            func.count().label("n"),
        )
        .filter(
            Transaction.human_validated.is_(True),
            Transaction.category.isnot(None),
            Transaction.description == description,
        )
        .group_by(Transaction.category, Transaction.subcategory)
        .all()
    )

    if not rows:
        return None, None, 0.0

    total = sum(r[2] for r in rows)
    counts = [r[2] for r in rows]

    entropy = _shannon_entropy(counts)
    homogeneity = 1.0 - entropy
    confidence = homogeneity * min(1.0, total / HISTORY_MIN_VALIDATED)

    # Pick the top category (highest count)
    top = max(rows, key=lambda r: r[2])
    return top[0], top[1], confidence


# ── Batch cache helper (for orchestrator performance) ─────────────────────────

class HistoryCache:
    """Pre-loaded history profiles for batch categorization.

    Avoids N+1 queries when categorizing a large batch of transactions.
    """

    def __init__(self, session: Session):
        profiles = get_description_profiles(session)
        self._cache: dict[str, DescriptionProfile] = {
            p.description: p for p in profiles
        }
        logger.info(
            f"HistoryCache: loaded {len(self._cache)} description profiles "
            f"from validated transactions"
        )

    def lookup(self, description: str) -> tuple[str | None, str | None, float]:
        """Look up a description from the pre-loaded cache.

        Returns (category, subcategory, confidence) or (None, None, 0.0).
        """
        profile = self._cache.get(description)
        if profile is None:
            return None, None, 0.0
        return profile.top_category, profile.top_subcategory, profile.confidence


# ── C-07: LLM context injection ───────────────────────────────────────────────


def get_top_associations_text(
    cache: HistoryCache,
    top_n: int = 50,
    max_chars: int = 2000,
) -> str:
    """Build a text block of top historical associations for LLM prompt injection.

    Returns a formatted string like:
        Historical associations (user's validated patterns — use as reference, not absolute rule):
          ESSELUNGA → Alimentari / Spesa supermercato (47x)
          TELECOM ITALIA → Casa / Telefono fisso (12x)
          ...

    Returns "" if no qualifying associations (e.g., first run with empty DB).
    """
    # Filter profiles by confidence and validation count
    profiles = [
        p for p in cache._cache.values()
        if p.confidence >= HISTORY_CONTEXT_MIN_CONFIDENCE
        and p.total_validated >= HISTORY_CONTEXT_MIN_VALIDATED
    ]

    if not profiles:
        return ""

    # Sort by total_validated descending — most common patterns first
    profiles.sort(key=lambda p: p.total_validated, reverse=True)
    profiles = profiles[:top_n]

    # Format lines
    lines = []
    for p in profiles:
        sub = f" / {p.top_subcategory}" if p.top_subcategory else ""
        lines.append(f"  {p.description} → {p.top_category}{sub} ({p.total_validated}x)")

    # Truncate to max_chars (remove last lines if too long)
    header = (
        "Historical associations (user's validated patterns — "
        "use as reference, not absolute rule):\n"
    )
    result = header + "\n".join(lines) + "\n"

    while len(result) > max_chars and lines:
        lines.pop()
        result = header + "\n".join(lines) + "\n"

    logger.info(
        f"get_top_associations_text: {len(profiles)} profiles → "
        f"{len(lines)} lines, {len(result)} chars"
    )
    return result


# ── C-06: Fan-out comportamentale ─────────────────────────────────────────────

def find_similar_uncategorized(
    session: Session, description: str, exclude_tx_id: str | None = None,
) -> list[Transaction]:
    """Find transactions with same description that could benefit from the same category.

    Returns transactions where:
    - description matches (exact, after normalization)
    - category_source is 'llm' or None (not manually set or rule-based)
    - human_validated is False
    """
    from sqlalchemy import or_

    query = (
        session.query(Transaction)
        .filter(
            Transaction.description == description,
            Transaction.human_validated.isnot(True),
            or_(
                Transaction.category_source.is_(None),
                Transaction.category_source == "llm",
            ),
        )
    )
    if exclude_tx_id:
        query = query.filter(Transaction.id != exclude_tx_id)

    results = query.order_by(Transaction.date.desc()).all()
    logger.debug(
        f"find_similar_uncategorized: description={description!r} "
        f"found={len(results)} (excluded={exclude_tx_id})"
    )
    return results


def apply_fan_out(
    session: Session,
    source_tx_id: str,
    target_tx_ids: list[str],
) -> int:
    """Apply the same category/subcategory/context from source to all targets.

    Sets category_source='history' on targets. Does NOT overwrite
    transactions that are already human_validated.
    Returns count of updated transactions.
    """
    from datetime import datetime, timezone

    source = session.get(Transaction, source_tx_id)
    if source is None:
        logger.warning(f"apply_fan_out: source tx {source_tx_id} not found")
        return 0

    updated = 0
    for tid in target_tx_ids:
        tx = session.get(Transaction, tid)
        if tx is None:
            continue
        # Safety: never overwrite already-validated transactions
        if tx.human_validated:
            continue

        tx.category = source.category
        tx.subcategory = source.subcategory
        if source.context:
            tx.context = source.context
        tx.category_source = "history"
        tx.category_confidence = "high"
        tx.to_review = False
        tx.updated_at = datetime.now(timezone.utc)
        updated += 1

    if updated:
        session.flush()
    logger.info(
        f"apply_fan_out: source={source_tx_id} targets={len(target_tx_ids)} "
        f"updated={updated}"
    )
    return updated
