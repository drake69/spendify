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
from support.logging import setup_logging

logger = setup_logging()

# ── Configuration constants (internal, not user-facing) ───────────────────────
HISTORY_MIN_VALIDATED = 5        # min validated txs to trust a description
HISTORY_AUTO_THRESHOLD = 0.90    # C >= 0.90 → source=history, auto-assign
HISTORY_SUGGEST_THRESHOLD = 0.50 # 0.50 <= C < 0.90 → suggest with to_review=True


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
