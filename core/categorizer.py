"""Transaction categorization (RF-05).

Cascade:
  Step 0 – user-defined rules (category_rule table)
  Step 1 – static deterministic rules (keyword/regex patterns)
  Step 2 – supervised ML model (future; currently stub returning None)
  Step 3 – LLM structured output
  Fallback – to_review = True, category = "Altro"
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
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

_SYSTEM_PROMPT_TEMPLATE = """You are a financial transaction categorizer.
You receive one bank transaction at a time and must assign it a two-level category
(category + subcategory) and a confidence level.

Rules:
- Use ONLY the category/subcategory pairs defined in the response schema.
- The subcategory must be valid for the chosen category.
- Base the decision on the description, amount sign, and context.
- Negative amount = expense; positive amount = income.
- If the description contains transfer/giroconto keywords, use "Trasferimenti e rimborsi"
  (this should have been filtered upstream).
- Do NOT infer or expose any PII from the description.
- If genuinely ambiguous, return category "Altro" / "Spese non classificate" (expense)
  or "Altro entrate" / "Entrate non classificate" (income) with confidence "low".
- rationale must be one sentence, max 120 chars, no PII.
"""


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
    (r'(netflix|spotify|amazon prime|disney\+|apple tv)', "Comunicazioni", "Streaming / abbonamenti digitali", "regex"),
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
) -> CategorizationResult:
    """
    Run the categorization cascade for a single transaction.
    Returns a CategorizationResult.
    """
    # Step 0: user-defined rules (sorted by priority desc)
    for rule in sorted(user_rules, key=lambda r: r.priority, reverse=True):
        if rule.matches(description, doc_type):
            sub = rule.subcategory or taxonomy.valid_subcategories(rule.category)[0] if taxonomy.valid_subcategories(rule.category) else ""
            return CategorizationResult(
                category=rule.category,
                subcategory=sub,
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

    # Step 3: LLM
    if llm_backend is not None:
        llm_result = _categorize_with_llm(
            description=description,
            amount=amount,
            taxonomy=taxonomy,
            llm_backend=llm_backend,
            sanitize_config=sanitize_config,
            fallback_backend=fallback_backend,
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
    taxonomy: TaxonomyConfig,
    llm_backend: LLMBackend,
    sanitize_config: SanitizationConfig | None,
    fallback_backend: LLMBackend | None,
) -> Optional[CategorizationResult]:
    if llm_backend.is_remote:
        safe_desc = redact_pii(description, sanitize_config)
    else:
        safe_desc = description

    expense_cats = taxonomy.all_expense_categories
    income_cats = taxonomy.all_income_categories
    json_schema = build_categorization_schema(expense_cats, income_cats)

    # Inject subcategory enum as well
    all_subs = []
    for subs in list(taxonomy.expenses.values()) + list(taxonomy.income.values()):
        all_subs.extend(subs)
    json_schema["properties"]["subcategory"]["enum"] = all_subs

    currency = "EUR"
    user_prompt = (
        f"Categorize the following transaction:\n\n"
        f"date: (not provided)\n"
        f"amount: {amount} {currency}\n"
        f'description: "{safe_desc}"\n\n'
        f"Respond with the JSON schema defined in the system context."
    )

    result, _ = call_with_fallback(
        primary=llm_backend,
        system_prompt=_SYSTEM_PROMPT_TEMPLATE,
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
        logger.warning(f"LLM returned invalid pair ({category}, {subcategory}), using fallback")
        fallback_cat, fallback_sub = TAXONOMY_FALLBACK_EXPENSE if amount < 0 else TAXONOMY_FALLBACK_INCOME
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
) -> list[CategorizationResult]:
    """Categorize a list of transaction dicts (row-by-row)."""
    results = []
    for tx in transactions:
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
        )
        results.append(result)
    return results
