"""Router: /transactions"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from api.dependencies import get_transaction_service
from api.schemas import (
    DeleteResponse,
    StatusResponse,
    TransactionCategoryUpdate,
    TransactionContextUpdate,
    TransactionListResponse,
    TransactionResponse,
)
from services import TransactionService

router = APIRouter(prefix="/transactions", tags=["transactions"])


def _tx_to_schema(tx) -> TransactionResponse:
    return TransactionResponse(
        id=tx.id,
        date=tx.date,
        date_accounting=tx.date_accounting,
        amount=float(tx.amount),
        currency=tx.currency or "EUR",
        description=tx.description,
        doc_type=tx.doc_type,
        account_label=tx.account_label,
        tx_type=tx.tx_type,
        category=tx.category,
        subcategory=tx.subcategory,
        category_confidence=tx.category_confidence,
        category_source=tx.category_source,
        reconciled=bool(tx.reconciled),
        to_review=bool(tx.to_review),
        context=tx.context,
        created_at=tx.created_at,
    )


@router.get("", response_model=TransactionListResponse)
def list_transactions(
    from_date: str | None = Query(None, description="ISO date YYYY-MM-DD"),
    to_date: str | None = Query(None, description="ISO date YYYY-MM-DD"),
    account_label: str | None = Query(None),
    category: str | None = Query(None),
    tx_type: str | None = Query(None),
    to_review: bool | None = Query(None),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    svc: TransactionService = Depends(get_transaction_service),
):
    filters: dict = {}
    if from_date:
        filters["from_date"] = from_date
    if to_date:
        filters["to_date"] = to_date
    if account_label:
        filters["account_label"] = account_label
    if category:
        filters["category"] = category
    if tx_type:
        filters["tx_type"] = tx_type
    if to_review is not None:
        filters["to_review"] = to_review

    txs = svc.get_transactions(filters, limit=limit, offset=offset)
    items = [_tx_to_schema(t) for t in txs]
    return TransactionListResponse(items=items, total=len(items))


@router.patch("/{tx_id}/category", response_model=StatusResponse)
def update_category(
    tx_id: str,
    body: TransactionCategoryUpdate,
    svc: TransactionService = Depends(get_transaction_service),
):
    ok = svc.update_category(tx_id, body.category, body.subcategory)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Transaction {tx_id!r} not found")
    return StatusResponse()


@router.patch("/{tx_id}/context", response_model=StatusResponse)
def update_context(
    tx_id: str,
    body: TransactionContextUpdate,
    svc: TransactionService = Depends(get_transaction_service),
):
    ok = svc.update_context(tx_id, body.context)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Transaction {tx_id!r} not found")
    return StatusResponse()


@router.post("/{tx_id}/toggle-giroconto", response_model=StatusResponse)
def toggle_giroconto(
    tx_id: str,
    svc: TransactionService = Depends(get_transaction_service),
):
    ok, msg = svc.toggle_giroconto(tx_id)
    if not ok:
        raise HTTPException(status_code=404, detail=msg or f"Transaction {tx_id!r} not found")
    return StatusResponse(detail=msg)


@router.delete("", response_model=DeleteResponse)
def delete_transactions(
    from_date: str | None = Query(None),
    to_date: str | None = Query(None),
    account_label: str | None = Query(None),
    category: str | None = Query(None),
    svc: TransactionService = Depends(get_transaction_service),
):
    filters: dict = {}
    if from_date:
        filters["from_date"] = from_date
    if to_date:
        filters["to_date"] = to_date
    if account_label:
        filters["account_label"] = account_label
    if category:
        filters["category"] = category
    if not filters:
        raise HTTPException(
            status_code=422,
            detail="At least one filter is required for bulk delete",
        )
    n = svc.delete_by_filter(filters)
    return DeleteResponse(deleted=n)
