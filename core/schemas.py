from __future__ import annotations
from typing import Any, Optional
from pydantic import BaseModel, Field
from core.models import DocumentType, SignConvention, Confidence


class DocumentSchema(BaseModel):
    """Canonical parsing schema for a bank statement source."""
    # required
    doc_type: DocumentType
    date_col: str
    amount_col: str
    sign_convention: SignConvention
    date_format: str
    account_label: str
    confidence: Confidence

    # optional column mappings
    date_accounting_col: Optional[str] = None
    debit_col: Optional[str] = None
    credit_col: Optional[str] = None
    description_col: Optional[str] = None
    currency_col: Optional[str] = None
    default_currency: str = "EUR"

    # derived / pre-processing
    is_zero_sum: bool = False
    internal_transfer_patterns: list[str] = Field(default_factory=list)
    encoding: str = "utf-8"
    sheet_name: Optional[str] = None
    skip_rows: int = 0
    delimiter: Optional[str] = None

    # source tracking
    source_identifier: Optional[str] = None  # sha256 prefix of the file or institution key

    def llm_json_schema(self) -> dict[str, Any]:
        """Return the JSON schema for LLM structured output (Flow 2)."""
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "required": [
                "doc_type", "date_col", "amount_col", "sign_convention",
                "date_format", "account_label", "confidence",
            ],
            "additionalProperties": False,
            "properties": {
                "doc_type": {
                    "type": "string",
                    "enum": [t.value for t in DocumentType],
                },
                "date_col": {"type": "string"},
                "date_accounting_col": {"type": ["string", "null"]},
                "amount_col": {"type": "string"},
                "debit_col": {"type": ["string", "null"]},
                "credit_col": {"type": ["string", "null"]},
                "description_col": {"type": "string"},
                "currency_col": {"type": ["string", "null"]},
                "default_currency": {"type": "string", "pattern": "^[A-Z]{3}$"},
                "date_format": {"type": "string"},
                "sign_convention": {
                    "type": "string",
                    "enum": [c.value for c in SignConvention],
                },
                "is_zero_sum": {"type": "boolean"},
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
            },
        }


def build_categorization_schema(expense_categories: list[str], income_categories: list[str]) -> dict[str, Any]:
    """Build the JSON schema for LLM categorization structured output."""
    all_categories = expense_categories + income_categories
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "required": ["category", "subcategory", "confidence"],
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
