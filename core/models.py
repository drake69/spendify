from enum import Enum


class DocumentType(str, Enum):
    bank_account = "bank_account"
    credit_card = "credit_card"
    debit_card = "debit_card"
    prepaid_card = "prepaid_card"
    savings = "savings"
    unknown = "unknown"


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


class CategorySource(str, Enum):
    rule = "rule"
    llm = "llm"
    manual = "manual"
