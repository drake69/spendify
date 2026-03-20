"""Pydantic request/response schemas for the REST API.

These are the public API contracts — independent from the SQLAlchemy ORM models.
All field names follow snake_case and match the ORM columns unless noted.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ── Transaction ────────────────────────────────────────────────────────────────

class TransactionResponse(BaseModel):
    id: str
    date: str
    date_accounting: str | None = None
    amount: float
    currency: str = "EUR"
    description: str | None = None
    doc_type: str | None = None
    account_label: str | None = None
    tx_type: str | None = None
    category: str | None = None
    subcategory: str | None = None
    category_confidence: str | None = None
    category_source: str | None = None
    reconciled: bool = False
    to_review: bool = False
    context: str | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class TransactionListResponse(BaseModel):
    items: list[TransactionResponse]
    total: int


class TransactionCategoryUpdate(BaseModel):
    category: str = Field(..., min_length=1)
    subcategory: str = Field(default="")


class TransactionContextUpdate(BaseModel):
    context: str | None = None


# ── Category Rule ──────────────────────────────────────────────────────────────

class CategoryRuleResponse(BaseModel):
    id: int
    pattern: str
    match_type: str
    category: str
    subcategory: str | None = None
    context: str | None = None
    doc_type: str | None = None
    priority: int = 0
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class CategoryRuleCreate(BaseModel):
    pattern: str = Field(..., min_length=1)
    match_type: str = Field(..., pattern="^(contains|regex|exact)$")
    category: str = Field(..., min_length=1)
    subcategory: str | None = None
    context: str | None = None
    doc_type: str | None = None
    priority: int = 0


class CategoryRuleUpdate(BaseModel):
    pattern: str | None = None
    match_type: str | None = Field(default=None, pattern="^(contains|regex|exact)$")
    category: str | None = None
    subcategory: str | None = None
    context: str | None = None
    doc_type: str | None = None
    priority: int | None = None


class RuleApplyResponse(BaseModel):
    applied: int
    message: str


# ── Description Rule ──────────────────────────────────────────────────────────

class DescriptionRuleResponse(BaseModel):
    id: int
    raw_pattern: str
    match_type: str
    cleaned_description: str
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class DescriptionRuleCreate(BaseModel):
    raw_pattern: str = Field(..., min_length=1)
    match_type: str = Field(..., pattern="^(contains|regex|exact)$")
    cleaned_description: str = Field(..., min_length=1)


# ── Settings ──────────────────────────────────────────────────────────────────

class SettingResponse(BaseModel):
    key: str
    value: str | None


class SettingUpdate(BaseModel):
    value: str


class AllSettingsResponse(BaseModel):
    settings: dict[str, str | None]


# ── Taxonomy ──────────────────────────────────────────────────────────────────

class SubcategoryResponse(BaseModel):
    id: int
    name: str
    sort_order: int = 0

    model_config = {"from_attributes": True}


class TaxonomyCategoryResponse(BaseModel):
    id: int
    name: str
    type: str
    sort_order: int = 0
    subcategories: list[SubcategoryResponse] = []

    model_config = {"from_attributes": True}


class TaxonomyCategoryCreate(BaseModel):
    name: str = Field(..., min_length=1)
    type: str = Field(..., pattern="^(expense|income)$")


class TaxonomyCategoryUpdate(BaseModel):
    name: str = Field(..., min_length=1)


class SubcategoryCreate(BaseModel):
    name: str = Field(..., min_length=1)


class SubcategoryUpdate(BaseModel):
    name: str = Field(..., min_length=1)


# ── Account ───────────────────────────────────────────────────────────────────

class AccountResponse(BaseModel):
    id: int
    name: str
    bank_name: str | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class AccountCreate(BaseModel):
    name: str = Field(..., min_length=1)
    bank_name: str | None = None


# ── Import Job ────────────────────────────────────────────────────────────────

class ImportJobResponse(BaseModel):
    id: int
    status: str
    progress: float = 0.0
    status_message: str | None = None
    detail_message: str | None = None
    n_transactions: int = 0
    n_files: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


# ── Generic ───────────────────────────────────────────────────────────────────

class DeleteResponse(BaseModel):
    deleted: int
    message: str = "ok"


class StatusResponse(BaseModel):
    status: str = "ok"
    detail: str | None = None
