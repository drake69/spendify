"""Transaction categorization (RF-05).

Cascade:
  Step 0 – user-defined rules (category_rule table)
  Step 1 – static deterministic rules (keyword/regex patterns)
  Step 2 – supervised ML model (future; currently stub returning None)
  Step 3 – LLM structured output
  Fallback – to_review = True, category = "Altro"
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
from core.schemas import build_categorization_schema
from support.logging import setup_logging

logger = setup_logging()

TAXONOMY_FALLBACK_EXPENSE = ("Altro", "Spese non classificate")
TAXONOMY_FALLBACK_INCOME = ("Altro entrate", "Entrate non classificate")

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
        return list(self.expenses.keys())

    @property
    def all_income_categories(self) -> list[str]:
        return list(self.income.keys())

    def valid_subcategories(self, category: str) -> list[str]:
        return self.expenses.get(category, self.income.get(category, []))

    def is_valid_pair(self, category: str, subcategory: str) -> bool:
        subs = self.valid_subcategories(category)
        return subcategory in subs

    def find_category_for_subcategory(self, subcategory: str) -> Optional[str]:
        """Return the parent category for a given subcategory, or None if not found.

        The subcategory is treated as authoritative: if an LLM or rule assigns a
        subcategory that exists in the taxonomy, this resolves the correct category.
        """
        for cat, subs in self.expenses.items():
            if subcategory in subs:
                return cat
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

_STATIC_RULES: list[tuple[str, str, str, str]] = [
    # (pattern, category, subcategory, match_type)
    (r'(conad|coop|esselunga|lidl|carrefour|eurospin|aldi|penny|pam\b)', "Alimentari", "Spesa supermercato", "regex"),
    (r'(farmacia|pharma)', "Salute", "Farmaci", "regex"),
    (r'(eni\b|shell|q8|tamoil|ip\b|api\b|agip)', "Trasporti", "Carburante", "regex"),
    (r'(telepass|autostrad)', "Trasporti", "Parcheggio / ZTL", "regex"),
    (r'(trenitalia|italo|frecciarossa|frecciargento)', "Trasporti", "Trasporto pubblico", "regex"),
    (r'(enel\b|iren\b|a2a\b|hera\b|eni gas)', "Casa", "Energia elettrica", "regex"),
    (r'(netflix|spotify|amazon prime|disney\+|apple tv)', "Svago e tempo libero", "Streaming / abbonamenti digitali", "regex"),
    (r'(stipendio|salary|busta paga)', "Lavoro dipendente", "Stipendio", "regex"),
    (r'(pensione|inps rendita)', "Prestazioni sociali", "Pensione / rendita", "regex"),
    (r'(commissione|canone conto|spese tenuta)', "Finanza e assicurazioni", "Commissioni bancarie", "regex"),
]

_COMPILED_STATIC: list[tuple[re.Pattern, str, str]] = [
    (re.compile(pat, re.IGNORECASE), cat, sub)
    for pat, cat, sub, _ in _STATIC_RULES
]


def _apply_static_rules(description: str) -> Optional[tuple[str, str]]:
    for pattern, category, subcategory in _COMPILED_STATIC:
        if pattern.search(description):
            return category, subcategory
    return None


# ── Main categorization cascade ───────────────────────────────────────────────

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
) -> CategorizationResult:
    """
    Run the categorization cascade for a single transaction.
    Returns a CategorizationResult.
    """
    # Step 0: user-defined rules (sorted by priority desc)
    for rule in sorted(user_rules, key=lambda r: r.priority, reverse=True):
        if rule.matches(description, doc_type):
            rule_cat = rule.category
            rule_sub = rule.subcategory or ""
            # If subcategory is set and exists in taxonomy, resolve the correct category
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

    # Step 1: static rules
    static_match = _apply_static_rules(description)
    if static_match:
        category, subcategory = static_match
        return CategorizationResult(
            category=category,
            subcategory=subcategory,
            confidence=Confidence.high,
            source=CategorySource.rule,
        )

    # Step 2: ML model (stub – always returns None)
    ml_result = _ml_predict(description, amount)
    if ml_result and ml_result[2] >= confidence_threshold:
        return CategorizationResult(
            category=ml_result[0],
            subcategory=ml_result[1],
            confidence=Confidence.high,
            source=CategorySource.rule,
        )

    # Step 3: LLM — only show categories that match the transaction direction
    if llm_backend is not None:
        categories = taxonomy.income if amount > 0 else taxonomy.expenses
        llm_result = _categorize_with_llm(
            description=description,
            amount=amount,
            categories=categories,
            taxonomy=taxonomy,
            llm_backend=llm_backend,
            sanitize_config=sanitize_config,
            fallback_backend=fallback_backend,
            description_language=description_language,
        )
        if llm_result:
            return llm_result

    # Fallback
    fallback_cat, fallback_sub = TAXONOMY_FALLBACK_EXPENSE if amount < 0 else TAXONOMY_FALLBACK_INCOME
    return CategorizationResult(
        category=fallback_cat,
        subcategory=fallback_sub,
        confidence=Confidence.low,
        source=CategorySource.llm,
        to_review=True,
    )


def _ml_predict(description: str, amount: Decimal) -> Optional[tuple[str, str, float]]:
    """Stub: supervised ML model. Returns (category, subcategory, confidence) or None."""
    return None


def _categorize_with_llm(
    description: str,
    amount: Decimal,
    categories: dict[str, list[str]],
    taxonomy: TaxonomyConfig,
    llm_backend: LLMBackend,
    sanitize_config: SanitizationConfig | None,
    fallback_backend: LLMBackend | None,
    description_language: str = "it",
) -> Optional[CategorizationResult]:
    if llm_backend.is_remote:
        safe_desc = redact_pii(description, sanitize_config)
    else:
        safe_desc = description

    # Only expose the relevant category direction to the LLM
    cat_keys = list(categories.keys())
    is_expense_direction = categories is taxonomy.expenses
    json_schema = build_categorization_schema(
        expense_categories=cat_keys if is_expense_direction else [],
        income_categories=cat_keys if not is_expense_direction else [],
    )

    # Inject flat subcategory enum constrained to the relevant direction
    dir_subs: list[str] = []
    for subs in categories.values():
        dir_subs.extend(subs)
    json_schema["properties"]["subcategory"]["enum"] = dir_subs

    # Build compact taxonomy hint (only relevant direction)
    tax_lines = [f"  {cat}: {', '.join(subs)}" for cat, subs in categories.items()]
    taxonomy_hint = "\n".join(tax_lines)

    currency = "EUR"
    user_prompt = _PROMPTS["user_template"].format(
        amount=amount,
        currency=currency,
        safe_desc=safe_desc,
        taxonomy_hint=taxonomy_hint,
        description_language=description_language,
    )

    result, _ = call_with_fallback(
        primary=llm_backend,
        system_prompt=_PROMPTS["system"],
        user_prompt=user_prompt,
        json_schema=json_schema,
        fallback=fallback_backend,
    )

    if result is None:
        return None

    category = result.get("category", "")
    subcategory = result.get("subcategory", "")
    confidence_str = result.get("confidence", "low")
    rationale = result.get("rationale", "")

    if not taxonomy.is_valid_pair(category, subcategory):
        # Subcategory is authoritative: if it exists in taxonomy under a different
        # category, use the correct parent category instead of the LLM's category.
        resolved_cat = taxonomy.find_category_for_subcategory(subcategory)
        if resolved_cat:
            logger.info(
                f"LLM returned category='{category}' but subcategory='{subcategory}' "
                f"belongs to '{resolved_cat}' — correcting category"
            )
            category = resolved_cat
        else:
            # Subcategory not in taxonomy at all; try to keep valid category
            valid_subs = taxonomy.valid_subcategories(category)
            if valid_subs:
                logger.warning(
                    f"LLM subcategory '{subcategory}' not in taxonomy for '{category}'; "
                    f"using first valid subcategory '{valid_subs[0]}'"
                )
                subcategory = valid_subs[0]
                confidence_str = "low"
            else:
                logger.warning(
                    f"LLM returned unknown pair ({category}, {subcategory}), using fallback"
                )
                fallback_cat, fallback_sub = (
                    TAXONOMY_FALLBACK_EXPENSE if amount < 0 else TAXONOMY_FALLBACK_INCOME
                )
                category, subcategory = fallback_cat, fallback_sub
                confidence_str = "low"

    return CategorizationResult(
        category=category,
        subcategory=subcategory,
        confidence=Confidence(confidence_str),
        source=CategorySource.llm,
        rationale=rationale,
        to_review=(confidence_str == "low"),
    )


def categorize_batch(
    transactions: list[dict[str, Any]],
    taxonomy: TaxonomyConfig,
    user_rules: list[CategoryRule],
    llm_backend: LLMBackend | None,
    sanitize_config: SanitizationConfig | None = None,
    fallback_backend: LLMBackend | None = None,
    confidence_threshold: float = 0.8,
    description_language: str = "it",
    progress_callback=None,  # Callable[[float], None] — 0.0..1.0 within batch
) -> list[CategorizationResult]:
    """Categorize a list of transaction dicts (row-by-row)."""
    results = []
    n = len(transactions)
    for i, tx in enumerate(transactions):
        description = tx.get("description", "")
        amount = tx.get("amount", Decimal(0))
        if not isinstance(amount, Decimal):
            amount = Decimal(str(amount or 0))
        doc_type = tx.get("doc_type", "")
        result = categorize_transaction(
            description=description,
            amount=amount,
            doc_type=doc_type,
            taxonomy=taxonomy,
            user_rules=user_rules,
            llm_backend=llm_backend,
            sanitize_config=sanitize_config,
            fallback_backend=fallback_backend,
            confidence_threshold=confidence_threshold,
            description_language=description_language,
        )
        results.append(result)
        if progress_callback:
            progress_callback((i + 1) / n if n else 1.0)
    return results
