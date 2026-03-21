"""Router: /settings"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_settings_service
from api.schemas import (
    AccountCreate,
    AccountResponse,
    AllSettingsResponse,
    SettingResponse,
    SettingUpdate,
    StatusResponse,
    DeleteResponse,
)
from services import SettingsService

router = APIRouter(prefix="/settings", tags=["settings"])

# Sensitive keys are never exposed via the API
_REDACTED_KEYS = {"openai_api_key", "anthropic_api_key"}


def _safe_settings(raw: dict) -> dict:
    return {k: ("***" if k in _REDACTED_KEYS else v) for k, v in raw.items()}


@router.get("", response_model=AllSettingsResponse)
def get_all_settings(svc: SettingsService = Depends(get_settings_service)):
    return AllSettingsResponse(settings=_safe_settings(svc.get_all()))


@router.get("/{key}", response_model=SettingResponse)
def get_setting(key: str, svc: SettingsService = Depends(get_settings_service)):
    if key in _REDACTED_KEYS:
        raise HTTPException(status_code=403, detail="This setting is not accessible via API")
    value = svc.get(key)
    if value is None:
        raise HTTPException(status_code=404, detail=f"Setting {key!r} not found")
    return SettingResponse(key=key, value=value)


@router.put("/{key}", response_model=StatusResponse)
def set_setting(
    key: str,
    body: SettingUpdate,
    svc: SettingsService = Depends(get_settings_service),
):
    if key in _REDACTED_KEYS:
        raise HTTPException(status_code=403, detail="This setting cannot be updated via API")
    svc.set(key, body.value)
    return StatusResponse()


# ── Accounts ──────────────────────────────────────────────────────────────────

accounts_router = APIRouter(prefix="/accounts", tags=["accounts"])


def _account_to_schema(acc) -> AccountResponse:
    return AccountResponse(
        id=acc.id,
        name=acc.name,
        bank_name=acc.bank_name,
        created_at=acc.created_at,
    )


@accounts_router.get("", response_model=list[AccountResponse])
def list_accounts(svc: SettingsService = Depends(get_settings_service)):
    return [_account_to_schema(a) for a in svc.get_accounts()]


@accounts_router.post("", response_model=AccountResponse, status_code=201)
def create_account(
    body: AccountCreate,
    svc: SettingsService = Depends(get_settings_service),
):
    acc = svc.create_account(body.name, body.bank_name or "")
    return _account_to_schema(acc)


@accounts_router.delete("/{account_id}", response_model=DeleteResponse)
def delete_account(
    account_id: int,
    svc: SettingsService = Depends(get_settings_service),
):
    ok = svc.delete_account(account_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Account {account_id} not found")
    return DeleteResponse(deleted=1)
