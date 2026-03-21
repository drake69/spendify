"""Router: /rules"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_rule_service
from api.schemas import (
    CategoryRuleCreate,
    CategoryRuleResponse,
    CategoryRuleUpdate,
    DeleteResponse,
    DescriptionRuleCreate,
    DescriptionRuleResponse,
    RuleApplyResponse,
    StatusResponse,
)
from services import RuleService

router = APIRouter(prefix="/rules", tags=["rules"])


def _rule_to_schema(rule) -> CategoryRuleResponse:
    # RuleService returns CoreCategoryRule (dataclass, no created_at) or ORM object
    return CategoryRuleResponse(
        id=rule.id,
        pattern=rule.pattern,
        match_type=rule.match_type,
        category=rule.category,
        subcategory=rule.subcategory,
        context=rule.context,
        doc_type=rule.doc_type,
        priority=rule.priority or 0,
        created_at=getattr(rule, "created_at", None),
    )


def _desc_rule_to_schema(rule) -> DescriptionRuleResponse:
    return DescriptionRuleResponse(
        id=rule.id,
        raw_pattern=rule.raw_pattern,
        match_type=rule.match_type,
        cleaned_description=rule.cleaned_description,
        created_at=rule.created_at,
    )


# ── Category Rules ─────────────────────────────────────────────────────────────

@router.get("/category", response_model=list[CategoryRuleResponse])
def list_category_rules(svc: RuleService = Depends(get_rule_service)):
    return [_rule_to_schema(r) for r in svc.get_rules()]


@router.post("/category", response_model=CategoryRuleResponse, status_code=201)
def create_category_rule(
    body: CategoryRuleCreate,
    svc: RuleService = Depends(get_rule_service),
):
    rule, _ = svc.create_rule(
        pattern=body.pattern,
        match_type=body.match_type,
        category=body.category,
        subcategory=body.subcategory or "",
        context=body.context,
        doc_type=body.doc_type,
        priority=body.priority,
    )
    return _rule_to_schema(rule)


@router.patch("/category/{rule_id}", response_model=StatusResponse)
def update_category_rule(
    rule_id: int,
    body: CategoryRuleUpdate,
    svc: RuleService = Depends(get_rule_service),
):
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update")
    ok = svc.update_rule(rule_id, **updates)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")
    return StatusResponse()


@router.delete("/category/{rule_id}", response_model=DeleteResponse)
def delete_category_rule(
    rule_id: int,
    svc: RuleService = Depends(get_rule_service),
):
    ok = svc.delete_rule(rule_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")
    return DeleteResponse(deleted=1)


@router.post("/category/apply-to-review", response_model=RuleApplyResponse)
def apply_rules_to_review(svc: RuleService = Depends(get_rule_service)):
    n = svc.apply_to_review()
    return RuleApplyResponse(applied=n, message=f"Applied to {n} review transactions")


@router.post("/category/apply-to-all", response_model=RuleApplyResponse)
def apply_rules_to_all(svc: RuleService = Depends(get_rule_service)):
    n_cat, n_ctx = svc.apply_to_all()
    total = n_cat + n_ctx
    return RuleApplyResponse(applied=total, message=f"Updated {n_cat} categories, {n_ctx} contexts")


# ── Description Rules ──────────────────────────────────────────────────────────

@router.get("/description", response_model=list[DescriptionRuleResponse])
def list_description_rules(svc: RuleService = Depends(get_rule_service)):
    return [_desc_rule_to_schema(r) for r in svc.get_description_rules()]


@router.post("/description", response_model=DescriptionRuleResponse, status_code=201)
def create_description_rule(
    body: DescriptionRuleCreate,
    svc: RuleService = Depends(get_rule_service),
):
    rule, _ = svc.create_description_rule(
        raw_pattern=body.raw_pattern,
        match_type=body.match_type,
        cleaned_description=body.cleaned_description,
    )
    return _desc_rule_to_schema(rule)


@router.delete("/description/{rule_id}", response_model=DeleteResponse)
def delete_description_rule(
    rule_id: int,
    svc: RuleService = Depends(get_rule_service),
):
    ok = svc.delete_description_rule(rule_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Description rule {rule_id} not found")
    return DeleteResponse(deleted=1)
