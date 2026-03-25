"""Transaction categorization (RF-05).

Cascade:
  Step 0 – user-defined rules (category_rule table)
  Step 1 – static deterministic rules (keyword/regex patterns)
  Step 2 – supervised ML model (future; currently stub returning None)
  Step 3 – LLM structured output  ← two directional batches (expense / income)
  Fallback – to_review = True
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import yaml

from core.llm_backends import LLMBackend, call_with_fallback
from core.models import CategorySource, Confidence
from core.sanitizer import SanitizationConfig, redact_pii
from core.schemas import build_categorization_batch_schema
from support.logging import setup_logging

logger = setup_logging()

# Hardcoded defaults used as safety net when no DB fallback categories are available
_DEFAULT_FALLBACK_EXPENSE = ("Altro", "Spese non classificate")
_DEFAULT_FALLBACK_INCOME = ("Altro entrate", "Entrate non classificate")

_PROMPTS_FILE = Path(__file__).parent.parent / "prompts" / "categorizer.json"

def _load_prompts() -> dict:
    with open(_PROMPTS_FILE, encoding="utf-8") as f:
        return json.load(f)

_PROMPTS = _load_prompts()


@dataclass
class CategoryRule:
    id: int
    pattern: str
    match_type: str  # contains | regex | exact
    category: str
    subcategory: Optional[str]
    doc_type: Optional[str]
    priority: int = 0
    context: Optional[str] = None  # se impostato, viene applicato alle transazioni

    _compiled: re.Pattern = field(init=False, repr=False)

    def __post_init__(self):
        if self.match_type == "regex":
            self._compiled = re.compile(self.pattern, re.IGNORECASE)
        else:
            self._compiled = None

    def matches(self, description: str, doc_type: str | None = None) -> bool:
        if self.doc_type and doc_type and self.doc_type != doc_type:
            return False
        desc = description.casefold()
        if self.match_type == "exact":
            return desc == self.pattern.casefold()
        elif self.match_type == "contains":
            return self.pattern.casefold() in desc
        elif self.match_type == "regex":
            return bool(self._compiled.search(desc))
        return False


@dataclass
class TaxonomyConfig:
    expenses: dict[str, list[str]]  # category -> subcategories
    income: dict[str, list[str]]

    @classmethod
    def from_yaml(cls, path: str) -> "TaxonomyConfig":
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        expenses = {entry["category"]: entry["subcategories"] for entry in data.get("expenses", [])}
        income = {entry["category"]: entry["subcategories"] for entry in data.get("income", [])}
        return cls(expenses=expenses, income=income)

    @property
    def all_expense_categories(self) -> list[str]:
        return sorted(self.expenses.keys())

    @property
    def all_income_categories(self) -> list[str]:
        return sorted(self.income.keys())

    def valid_subcategories(self, category: str) -> list[str]:
        return sorted(self.expenses.get(category, self.income.get(category, [])))

    def is_valid_pair(self, category: str, subcategory: str) -> bool:
        subs = self.valid_subcategories(category)
        return subcategory in subs

    def find_category_for_subcategory(
        self, subcategory: str, direction: str | None = None
    ) -> Optional[str]:
        """Return the parent category for a given subcategory.

        direction: "expense" | "income" | None (search both, expense first).
        When direction is set, only the matching side is searched.
        """
        search_expense = direction in (None, "expense")
        search_income = direction in (None, "income")
        if search_expense:
            for cat, subs in self.expenses.items():
                if subcategory in subs:
                    return cat
        if search_income:
            for cat, subs in self.income.items():
                if subcategory in subs:
                    return cat
        return None

    @property
    def all_subcategories(self) -> list[str]:
        """Flat list of all subcategories across expenses and income."""
        result = []
        for subs in list(self.expenses.values()) + list(self.income.values()):
            result.extend(subs)
        return result


@dataclass
class CategorizationResult:
    category: str
    subcategory: str
    confidence: Confidence
    source: CategorySource
    rationale: str = ""
    to_review: bool = False


# ── Static keyword rules ──────────────────────────────────────────────────────
# Each rule is (pattern, category, subcategory, match_type, direction)
# direction: "expense" | "income" | "any"
_STATIC_RULES: list[tuple[str, str, str, str, str]] = [
    (r'(conad|coop|esselunga|lidl|carrefour|eurospin|aldi|penny|pam\b)', "Alimentari", "Spesa supermercato", "regex", "expense"),
    (r'(farmacia|pharma)', "Salute", "Farmaci", "regex", "expense"),
    (r'(eni\b|shell|q8|tamoil|ip\b|api\b|agip)', "Trasporti", "Carburante", "regex", "expense"),
    (r'(telepass|autostrad)', "Trasporti", "Parcheggio / ZTL", "regex", "expense"),
    (r'(trenitalia|italo|frecciarossa|frecciargento)', "Trasporti", "Trasporto pubblico", "regex", "expense"),
    (r'(enel\b|iren\b|a2a\b|hera\b|eni gas)', "Casa", "Energia elettrica", "regex", "expense"),
    (r'(netflix|spotify|amazon prime|disney\+|apple tv)', "Svago e tempo libero", "Streaming / abbonamenti digitali", "regex", "expense"),
    (r'(stipendio|salary|busta paga)', "Lavoro dipendente", "Stipendio", "regex", "income"),
    (r'(pensione|inps rendita)', "Prestazioni sociali", "Pensione / rendita", "regex", "income"),
    (r'(commissione|canone conto|spese tenuta)', "Finanza e assicurazioni", "Commissioni bancarie", "regex", "expense"),
]

_COMPILED_STATIC: list[tuple[re.Pattern, str, str, str]] = [
    (re.compile(pat, re.IGNORECASE), cat, sub, direction)
    for pat, cat, sub, _, direction in _STATIC_RULES
]


def _apply_static_rules(description: str, is_expense: bool) -> Optional[tuple[str, str]]:
    """Apply static rules respecting transaction direction."""
    for pattern, category, subcategory, direction in _COMPILED_STATIC:
        if direction == "expense" and not is_expense:
            continue
        if direction == "income" and is_expense:
            continue
        if pattern.search(description):
            return category, subcategory
    return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_amount(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal(0)


def _try_deterministic(
    description: str,
    amount: Decimal,
    doc_type: str,
    user_rules: list[CategoryRule],
    taxonomy: TaxonomyConfig,
) -> Optional[CategorizationResult]:
    """Apply user rules and static rules. Returns result or None if LLM needed."""
    # Step 0: user-defined rules (sorted by priority desc)
    for rule in sorted(user_rules, key=lambda r: r.priority, reverse=True):
        if rule.matches(description, doc_type):
            rule_cat = rule.category
            rule_sub = rule.subcategory or ""
            if rule_sub:
                resolved = taxonomy.find_category_for_subcategory(rule_sub)
                if resolved:
                    rule_cat = resolved
            if not rule_sub:
                subs = taxonomy.valid_subcategories(rule_cat)
                rule_sub = subs[0] if subs else ""
            return CategorizationResult(
                category=rule_cat,
                subcategory=rule_sub,
                confidence=Confidence.high,
                source=CategorySource.rule,
            )

    # Step 1: static rules (direction-aware)
    is_expense = amount < 0
    static_match = _apply_static_rules(description, is_expense)
    if static_match:
        category, subcategory = static_match
        return CategorizationResult(
            category=category,
            subcategory=subcategory,
            confidence=Confidence.high,
            source=CategorySource.rule,
        )

    return None


def _make_fallback(
    amount: Decimal,
    fallback_categories: dict[str, tuple[str, str]] | None = None,
) -> CategorizationResult:
    if fallback_categories:
        fallback_cat, fallback_sub = (
            fallback_categories.get("expense", _DEFAULT_FALLBACK_EXPENSE)
            if amount < 0
            else fallback_categories.get("income", _DEFAULT_FALLBACK_INCOME)
        )
    else:
        fallback_cat, fallback_sub = _DEFAULT_FALLBACK_EXPENSE if amount < 0 else _DEFAULT_FALLBACK_INCOME
    return CategorizationResult(
        category=fallback_cat,
        subcategory=fallback_sub,
        confidence=Confidence.low,
        source=CategorySource.llm,
        to_review=True,
    )


# ── LLM batch group ───────────────────────────────────────────────────────────

def _run_llm_batch_group(
    transactions: list[dict],
    indices: list[int],
    results: list[Optional[CategorizationResult]],
    categories: dict[str, list[str]],
    taxonomy: TaxonomyConfig,
    llm_backend: LLMBackend,
    fallback_backend: LLMBackend | None,
    sanitize_config: SanitizationConfig | None,
    description_language: str,
    batch_size: int,
    direction: str,  # "expense" | "income"
    source_name: str,
    fallback_categories: dict[str, tuple[str, str]] | None = None,
) -> None:
    """Run LLM categorization in batches for one direction. Updates results[] in place."""
    cat_keys = list(categories.keys())
    dir_subs: list[str] = [sub for subs in categories.values() for sub in subs]
    taxonomy_hint = "\n".join(f"  {cat}: {', '.join(subs)}" for cat, subs in categories.items())
    json_schema = build_categorization_batch_schema(cat_keys, dir_subs)

    for batch_start in range(0, len(indices), batch_size):
        batch_indices = indices[batch_start: batch_start + batch_size]

        items = []
        for idx in batch_indices:
            tx = transactions[idx]
            desc = tx.get("description", "") or ""
            # Always redact before any LLM call (local or remote) — permutation-aware
            desc = redact_pii(desc, sanitize_config)
            items.append({"amount": str(tx.get("amount", 0)), "description": desc})

        n = len(items)
        items_json = json.dumps(items, ensure_ascii=False, indent=2)
        user_prompt = _PROMPTS["user_template_batch"].format(
            n=n,
            direction=direction,
            description_language=description_language,
            transactions_json=items_json,
            taxonomy_hint=taxonomy_hint,
        )

        raw, backend_used = call_with_fallback(
            primary=llm_backend,
            system_prompt=_PROMPTS["system"],
            user_prompt=user_prompt,
            json_schema=json_schema,
            fallback=fallback_backend,
        )

        if raw is None:
            logger.warning(
                f"categorize_batch [{source_name}] {direction}: LLM failed for batch "
                f"{batch_start}..{batch_start + n} — using fallback"
            )
            for idx in batch_indices:
                if results[idx] is None:
                    results[idx] = _make_fallback(_parse_amount(transactions[idx].get("amount")), fallback_categories)
            continue

        llm_results = raw.get("results", [])
        if not isinstance(llm_results, list) or len(llm_results) != n:
            logger.warning(
                f"categorize_batch [{source_name}] {direction}: "
                f"unexpected response shape (expected {n}, got "
                f"{len(llm_results) if isinstance(llm_results, list) else type(llm_results)!r}) "
                f"— using fallback"
            )
            for idx in batch_indices:
                if results[idx] is None:
                    results[idx] = _make_fallback(_parse_amount(transactions[idx].get("amount")), fallback_categories)
            continue

        logger.debug(
            f"categorize_batch [{source_name}] {direction}: "
            f"batch of {n} via {backend_used}"
        )

        for j, idx in enumerate(batch_indices):
            item = llm_results[j] if j < len(llm_results) else {}
            amount = _parse_amount(transactions[idx].get("amount"))
            results[idx] = _validate_llm_result(item, categories, taxonomy, amount, direction, fallback_categories)


def _validate_llm_result(
    item: dict,
    categories: dict[str, list[str]],
    taxonomy: TaxonomyConfig,
    amount: Decimal,
    direction: str,
    fallback_categories: dict[str, tuple[str, str]] | None = None,
) -> CategorizationResult:
    """Validate and fix a single LLM result dict. Always returns a valid result."""
    category = item.get("category", "")
    subcategory = item.get("subcategory", "")
    confidence_str = item.get("confidence", "low")
    rationale = item.get("rationale", "")

    if not taxonomy.is_valid_pair(category, subcategory):
        # Try to resolve subcategory within the correct direction
        resolved_cat = taxonomy.find_category_for_subcategory(subcategory, direction)
        if resolved_cat:
            logger.info(
                f"LLM category='{category}' corrected to '{resolved_cat}' "
                f"via subcategory='{subcategory}'"
            )
            category = resolved_cat
        else:
            # Subcategory not in taxonomy — try to keep the category if valid
            valid_subs = categories.get(category, [])
            if valid_subs:
                logger.warning(
                    f"LLM subcategory '{subcategory}' not in taxonomy for '{category}'; "
                    f"using first valid subcategory '{valid_subs[0]}'"
                )
                subcategory = valid_subs[0]
                confidence_str = "low"
            else:
                logger.warning(
                    f"LLM returned unknown pair ({category!r}, {subcategory!r}) — fallback"
                )
                return _make_fallback(amount, fallback_categories)

    # Ensure the resolved category is in the expected direction
    if category not in categories:
        logger.warning(
            f"LLM assigned cross-direction category '{category}' for {direction} — fallback"
        )
        return _make_fallback(amount, fallback_categories)

    return CategorizationResult(
        category=category,
        subcategory=subcategory,
        confidence=Confidence(confidence_str) if confidence_str in ("high", "medium", "low") else Confidence.low,
        source=CategorySource.llm,
        rationale=rationale,
        to_review=(confidence_str == "low"),
    )


# ── Public API ────────────────────────────────────────────────────────────────

def categorize_batch(
    transactions: list[dict[str, Any]],
    taxonomy: TaxonomyConfig,
    user_rules: list[CategoryRule],
    llm_backend: LLMBackend | None,
    sanitize_config: SanitizationConfig | None = None,
    fallback_backend: LLMBackend | None = None,
    confidence_threshold: float = 0.8,
    description_language: str = "it",
    batch_size: int = 20,
    progress_callback=None,  # Callable[[float], None] — 0.0..1.0 within batch
    source_name: str = "unknown",
    fallback_categories: dict[str, tuple[str, str]] | None = None,
) -> list[CategorizationResult]:
    """Categorize transactions using two directional LLM batches (expense / income).

    Pipeline per transaction:
      1. User rules (deterministic)
      2. Static keyword rules (direction-aware)
      3. LLM — expense batch sees only expense categories, income batch only income categories
      4. Fallback → to_review=True
    """
    n = len(transactions)
    results: list[Optional[CategorizationResult]] = [None] * n

    llm_expense: list[int] = []
    llm_income: list[int] = []

    # Step 0 + 1: deterministic rules per transaction
    for i, tx in enumerate(transactions):
        amount = _parse_amount(tx.get("amount"))
        description = tx.get("description", "") or ""
        doc_type = tx.get("doc_type", "") or ""

        result = _try_deterministic(description, amount, doc_type, user_rules, taxonomy)
        if result is not None:
            results[i] = result
        elif amount < 0:
            llm_expense.append(i)
        else:
            llm_income.append(i)

    # Step 3: LLM — two directional batches
    if llm_backend is not None:
        if llm_expense:
            _run_llm_batch_group(
                transactions, llm_expense, results,
                taxonomy.expenses, taxonomy,
                llm_backend, fallback_backend, sanitize_config,
                description_language, batch_size, "expense", source_name,
                fallback_categories=fallback_categories,
            )
        if llm_income:
            _run_llm_batch_group(
                transactions, llm_income, results,
                taxonomy.income, taxonomy,
                llm_backend, fallback_backend, sanitize_config,
                description_language, batch_size, "income", source_name,
                fallback_categories=fallback_categories,
            )

    # Fallback for any still-None (llm_backend=None or batch error)
    for i in range(n):
        if results[i] is None:
            results[i] = _make_fallback(_parse_amount(transactions[i].get("amount")), fallback_categories)

    if progress_callback:
        progress_callback(1.0)

    logger.info(
        f"categorize_batch [{source_name}]: {n} transactions — "
        f"{sum(1 for r in results if r and r.source == CategorySource.rule)} by rules, "
        f"{sum(1 for r in results if r and r.source == CategorySource.llm)} by LLM"
    )

    return results


def _ml_predict(description: str, amount: Decimal) -> Optional[tuple[str, str, float]]:
    """Stub: supervised ML model. Returns (category, subcategory, confidence) or None."""
    return None


# ── Single-transaction API (kept for correction/review use-cases) ─────────────

def categorize_transaction(
    description: str,
    amount: Decimal,
    doc_type: str,
    taxonomy: TaxonomyConfig,
    user_rules: list[CategoryRule],
    llm_backend: LLMBackend | None,
    sanitize_config: SanitizationConfig | None = None,
    fallback_backend: LLMBackend | None = None,
    confidence_threshold: float = 0.8,
    description_language: str = "it",
    fallback_categories: dict[str, tuple[str, str]] | None = None,
) -> CategorizationResult:
    """Single-transaction categorization. Used for real-time correction in the UI."""
    result = _try_deterministic(description, amount, doc_type, user_rules, taxonomy)
    if result is not None:
        return result

    if llm_backend is not None:
        tx = [{"description": description, "amount": amount, "doc_type": doc_type}]
        results: list[Optional[CategorizationResult]] = [None]
        direction = "expense" if amount < 0 else "income"
        categories = taxonomy.expenses if amount < 0 else taxonomy.income
        _run_llm_batch_group(
            tx, [0], results,
            categories, taxonomy,
            llm_backend, fallback_backend, sanitize_config,
            description_language, 1, direction, "single",
            fallback_categories=fallback_categories,
        )
        if results[0] is not None:
            return results[0]

    return _make_fallback(amount, fallback_categories)
