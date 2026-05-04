from __future__ import annotations
from typing import Any, Optional
from pydantic import BaseModel, Field
from core.models import DocumentType, SignConvention, Confidence

# Fields the LLM must always produce (no sensible default)
LLM_REQUIRED_FIELDS = [
    "doc_type", "date_col", "date_format", "sign_convention",
    "description_col", "description_cols", "account_label",
    "invert_sign", "positive_ratio", "semantic_evidence",
]

# Defaults for fields the LLM may omit
LLM_OPTIONAL_DEFAULTS: dict[str, Any] = {
    "date_accounting_col": None,
    "amount_col": None,
    "debit_col": None,
    "credit_col": None,
    "currency_col": None,
    "default_currency": "EUR",
    "is_zero_sum": False,
    "internal_transfer_patterns": [],
    "encoding": "utf-8",
    "sheet_name": None,
    "skip_rows": 0,
    "delimiter": None,
    "negative_ratio": None,
    "normalization_case_id": None,
}


def fill_llm_defaults(data: dict[str, Any]) -> dict[str, Any]:
    """Fill missing optional fields with sensible defaults."""
    for key, default in LLM_OPTIONAL_DEFAULTS.items():
        if key not in data:
            data[key] = default
    return data


class DocumentSchema(BaseModel):
    """Canonical parsing schema for a movements file source."""
    # required
    doc_type: DocumentType
    date_col: str
    amount_col: Optional[str] = None  # None when debit_col+credit_col split is used
    sign_convention: SignConvention
    date_format: str
    account_label: str
    confidence: Confidence
    confidence_score: float = 0.0  # 0.0-1.0 deterministic score

    # optional column mappings
    date_accounting_col: Optional[str] = None
    debit_col: Optional[str] = None
    credit_col: Optional[str] = None
    description_col: Optional[str] = None
    description_cols: list[str] = Field(default_factory=list)  # multi-col concat; takes priority over description_col
    currency_col: Optional[str] = None
    default_currency: str = "EUR"

    # derived / pre-processing
    is_zero_sum: bool = False
    invert_sign: bool = False  # True when card file stores expenses as positive (negate all amounts)
    internal_transfer_patterns: list[str] = Field(default_factory=list)
    footer_patterns: list[str] = Field(default_factory=list)
    has_borders: bool = False  # True if XLSX source uses bordered table (detected on first load)
    encoding: str = "utf-8"
    sheet_name: Optional[str] = None
    skip_rows: int = 0
    delimiter: Optional[str] = None

    # classifier diagnostics — persisted for audit/debug; not used by normalizer
    positive_ratio: Optional[float] = None   # fraction of amount-column values > 0 in the sample
    negative_ratio: Optional[float] = None   # fraction of amount-column values < 0 in the sample
    semantic_evidence: list[str] = Field(default_factory=list)  # LLM reasoning sentences
    normalization_case_id: Optional[str] = None  # e.g. "C1", "C2" — matches classifier taxonomy

    # source tracking
    source_identifier: Optional[str] = None  # sha256 prefix of the file or institution key
    header_sha256: Optional[str] = None  # SHA256 of first min(30,N) raw rows — used for fast schema lookup

    def llm_json_schema(self) -> dict[str, Any]:
        """Return the JSON schema for LLM structured output (Flow 2)."""
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "required": LLM_REQUIRED_FIELDS,
            "additionalProperties": False,
            "properties": {
                "doc_type": {
                    "type": "string",
                    "enum": [t.value for t in DocumentType],
                },
                "date_col": {"type": "string"},
                "date_accounting_col": {"type": ["string", "null"]},
                "amount_col": {"type": ["string", "null"]},
                "debit_col": {"type": ["string", "null"]},
                "credit_col": {"type": ["string", "null"]},
                "description_col": {"type": ["string", "null"]},
                "description_cols": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "All columns containing descriptive text; concatenated space-separated into the transaction description.",
                },
                "currency_col": {"type": ["string", "null"]},
                "default_currency": {"type": "string", "pattern": "^[A-Z]{3}$"},
                "date_format": {"type": "string"},
                "sign_convention": {
                    "type": "string",
                    "enum": [c.value for c in SignConvention],
                },
                "is_zero_sum": {"type": "boolean"},
                "invert_sign": {
                    "type": "boolean",
                    "description": "Set true when a card file stores expenses as positive amounts (negate to get correct sign).",
                },
                "internal_transfer_patterns": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "account_label": {"type": "string"},
                "encoding": {"type": "string"},
                "sheet_name": {"type": ["string", "null"]},
                "skip_rows": {"type": "integer", "minimum": 0},
                "delimiter": {"type": ["string", "null"]},
                "confidence": {
                    "type": "string",
                    "enum": [c.value for c in Confidence],
                },
                "positive_ratio": {
                    "type": ["number", "null"],
                    "description": "Fraction of amount-column values > 0 in the sample (0.0–1.0).",
                },
                "negative_ratio": {
                    "type": ["number", "null"],
                    "description": "Fraction of amount-column values < 0 in the sample (0.0–1.0).",
                },
                "semantic_evidence": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Short sentences explaining why this schema was chosen (for audit/debug).",
                },
                "normalization_case_id": {
                    "type": ["string", "null"],
                    "description": "Short case identifier, e.g. C1=bank signed_single, C2=card inverted, C3=debit/credit columns.",
                },
            },
        }


# ── Multi-step classifier sub-schemas ─────────────────────────────────────

STEP1_REQUIRED = ["doc_type"]
STEP2_REQUIRED = ["date_col", "description_col", "description_cols"]
STEP3_REQUIRED = [
    "sign_convention", "date_format", "account_label",
    "invert_sign", "positive_ratio", "semantic_evidence",
]


def step1_json_schema() -> dict[str, Any]:
    """JSON schema for Step 1: Document Identity."""
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "required": STEP1_REQUIRED,
        "additionalProperties": False,
        "properties": {
            "doc_type": {
                "type": "string",
                "enum": [t.value for t in DocumentType],
            },
            "encoding": {"type": "string"},
            "delimiter": {"type": ["string", "null"]},
            "sheet_name": {"type": ["string", "null"]},
            "skip_rows": {"type": "integer", "minimum": 0},
        },
    }


def step2_json_schema() -> dict[str, Any]:
    """JSON schema for Step 2: Column Mapping."""
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "required": STEP2_REQUIRED,
        "additionalProperties": False,
        "properties": {
            "date_col": {"type": "string"},
            "date_accounting_col": {"type": ["string", "null"]},
            "amount_col": {"type": ["string", "null"]},
            "debit_col": {"type": ["string", "null"]},
            "credit_col": {"type": ["string", "null"]},
            "description_col": {"type": ["string", "null"]},
            "description_cols": {
                "type": "array",
                "items": {"type": "string"},
                "description": "All columns containing descriptive text.",
            },
            "currency_col": {"type": ["string", "null"]},
            "default_currency": {"type": "string", "pattern": "^[A-Z]{3}$"},
        },
    }


def step3_json_schema() -> dict[str, Any]:
    """JSON schema for Step 3: Semantic Analysis."""
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "required": STEP3_REQUIRED,
        "additionalProperties": False,
        "properties": {
            "sign_convention": {
                "type": "string",
                "enum": [c.value for c in SignConvention],
            },
            "invert_sign": {
                "type": "boolean",
                "description": "Set true when a card file stores expenses as positive amounts.",
            },
            "date_format": {"type": "string"},
            "is_zero_sum": {"type": "boolean"},
            "internal_transfer_patterns": {
                "type": "array",
                "items": {"type": "string"},
            },
            "account_label": {"type": "string"},
            "confidence": {
                "type": "string",
                "enum": [c.value for c in Confidence],
            },
            "positive_ratio": {
                "type": ["number", "null"],
                "description": "Fraction of amount-column values > 0 in the sample (0.0-1.0).",
            },
            "negative_ratio": {
                "type": ["number", "null"],
                "description": "Fraction of amount-column values < 0 in the sample (0.0-1.0).",
            },
            "semantic_evidence": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Short sentences explaining classification decision.",
            },
            "normalization_case_id": {
                "type": ["string", "null"],
                "description": "Case ID: C1=bank signed_single, C2=card inverted, C3=debit/credit.",
            },
        },
    }


def build_categorization_schema(expense_categories: list[str], income_categories: list[str]) -> dict[str, Any]:
    """Build the JSON schema for LLM categorization structured output."""
    all_categories = expense_categories + income_categories
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "required": ["category", "subcategory", "confidence", "rationale"],
        "additionalProperties": False,
        "properties": {
            "category": {
                "type": "string",
                "enum": all_categories,
                "description": "Level-1 category from taxonomy.yaml.",
            },
            "subcategory": {
                "type": "string",
                "description": "Level-2 subcategory consistent with category.",
            },
            "confidence": {
                "type": "string",
                "enum": ["high", "medium", "low"],
            },
            "rationale": {
                "type": "string",
                "maxLength": 120,
                "description": "One-sentence justification. Omit PII.",
            },
        },
    }


def build_categorization_batch_schema(categories: list[str], dir_subs: list[str]) -> dict[str, Any]:
    """Build the JSON schema for batched LLM categorization (array response)."""
    item_schema = {
        "type": "object",
        "required": ["category", "subcategory", "confidence", "rationale"],
        "additionalProperties": False,
        "properties": {
            "category": {
                "type": "string",
                "enum": categories,
            },
            "subcategory": {
                "type": "string",
                "enum": dir_subs,
            },
            "confidence": {
                "type": "string",
                "enum": ["high", "medium", "low"],
            },
            "rationale": {
                "type": "string",
                "maxLength": 120,
            },
        },
    }
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "required": ["results"],
        "additionalProperties": False,
        "properties": {
            "results": {
                "type": "array",
                "items": item_schema,
            }
        },
    }
