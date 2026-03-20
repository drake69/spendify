"""Router: /taxonomy"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_settings_service
from api.schemas import (
    DeleteResponse,
    StatusResponse,
    SubcategoryCreate,
    SubcategoryResponse,
    SubcategoryUpdate,
    TaxonomyCategoryCreate,
    TaxonomyCategoryResponse,
    TaxonomyCategoryUpdate,
)
from services import SettingsService

router = APIRouter(prefix="/taxonomy", tags=["taxonomy"])


def _sub_to_schema(sub) -> SubcategoryResponse:
    return SubcategoryResponse(id=sub.id, name=sub.name, sort_order=sub.sort_order or 0)


def _cat_to_schema(cat) -> TaxonomyCategoryResponse:
    # subcategories is lazy-loaded — may be inaccessible if the session is closed
    from sqlalchemy.orm.exc import DetachedInstanceError
    try:
        subs = [_sub_to_schema(s) for s in (cat.subcategories or [])]
    except DetachedInstanceError:
        subs = []
    return TaxonomyCategoryResponse(
        id=cat.id,
        name=cat.name,
        type=cat.type,
        sort_order=cat.sort_order or 0,
        subcategories=subs,
    )


@router.get("/categories", response_model=list[TaxonomyCategoryResponse])
def list_categories(
    type: str | None = None,
    svc: SettingsService = Depends(get_settings_service),
):
    return [_cat_to_schema(c) for c in svc.get_categories(type_filter=type)]


@router.post("/categories", response_model=TaxonomyCategoryResponse, status_code=201)
def create_category(
    body: TaxonomyCategoryCreate,
    svc: SettingsService = Depends(get_settings_service),
):
    cat = svc.create_category(body.name, body.type)
    return _cat_to_schema(cat)


@router.patch("/categories/{cat_id}", response_model=StatusResponse)
def update_category(
    cat_id: int,
    body: TaxonomyCategoryUpdate,
    svc: SettingsService = Depends(get_settings_service),
):
    ok = svc.update_category(cat_id, body.name)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Category {cat_id} not found")
    return StatusResponse()


@router.delete("/categories/{cat_id}", response_model=DeleteResponse)
def delete_category(
    cat_id: int,
    svc: SettingsService = Depends(get_settings_service),
):
    ok = svc.delete_category(cat_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Category {cat_id} not found")
    return DeleteResponse(deleted=1)


@router.post("/categories/{cat_id}/subcategories", response_model=SubcategoryResponse, status_code=201)
def create_subcategory(
    cat_id: int,
    body: SubcategoryCreate,
    svc: SettingsService = Depends(get_settings_service),
):
    sub = svc.create_subcategory(cat_id, body.name)
    return _sub_to_schema(sub)


@router.patch("/subcategories/{sub_id}", response_model=StatusResponse)
def update_subcategory(
    sub_id: int,
    body: SubcategoryUpdate,
    svc: SettingsService = Depends(get_settings_service),
):
    ok = svc.update_subcategory(sub_id, body.name)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Subcategory {sub_id} not found")
    return StatusResponse()


@router.delete("/subcategories/{sub_id}", response_model=DeleteResponse)
def delete_subcategory(
    sub_id: int,
    svc: SettingsService = Depends(get_settings_service),
):
    ok = svc.delete_subcategory(sub_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Subcategory {sub_id} not found")
    return DeleteResponse(deleted=1)
