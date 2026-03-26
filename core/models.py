from enum import Enum


class DocumentType(str, Enum):
    bank_account = "bank_account"
    credit_card = "credit_card"
    debit_card = "debit_card"
    prepaid_card = "prepaid_card"
    savings_account = "savings_account"
    cash             = "cash"
    unknown = "unknown"


# Derived sets — single source of truth
INVERT_SIGN_TYPES = frozenset({DocumentType.credit_card})
NO_INVERT_TYPES   = frozenset({DocumentType.bank_account, DocumentType.savings_account,
                                DocumentType.debit_card, DocumentType.prepaid_card,
                                DocumentType.cash})
CARD_TYPES        = frozenset({DocumentType.credit_card, DocumentType.debit_card,
                                DocumentType.prepaid_card})


class TransactionType(str, Enum):
    expense = "expense"
    income = "income"
    card_tx = "card_tx"
    card_settlement = "card_settlement"
    aggregate_debit = "aggregate_debit"
    internal_out = "internal_out"
    internal_in = "internal_in"
    unknown = "unknown"


class SignConvention(str, Enum):
    signed_single = "signed_single"     # single column, negative = expense
    debit_positive = "debit_positive"   # debit and credit in separate columns, both positive
    credit_negative = "credit_negative" # credit column positive, debit column negative


class Confidence(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"

    @classmethod
    def from_score(cls, score: float) -> "Confidence":
        """Derive a categorical confidence level from a numeric score (0.0-1.0)."""
        if score >= 0.80:
            return cls.high
        elif score >= 0.50:
            return cls.medium
        return cls.low


class MatchType(str, Enum):
    contains = "contains"
    regex = "regex"
    exact = "exact"


class GirocontoMode(str, Enum):
    neutral = "neutral"
    exclude = "exclude"


class LLMBackendName(str, Enum):
    local_ollama = "local_ollama"
    openai = "openai"
    claude = "claude"
    openai_compatible = "openai_compatible"


class CategorySource(str, Enum):
    rule = "rule"
    llm = "llm"
    manual = "manual"
