"""API tests — /settings, /accounts, /taxonomy endpoints."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from api.main import app
from api.dependencies import get_settings_service, get_engine
from db.models import create_tables
from services.settings_service import SettingsService


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def mem_engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_tables(eng)
    return eng


@pytest.fixture
def client(mem_engine):
    app.dependency_overrides[get_settings_service] = lambda: SettingsService(mem_engine)
    app.dependency_overrides[get_engine] = lambda: mem_engine
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ── GET /settings ──────────────────────────────────────────────────────────────

def test_get_all_settings(client):
    r = client.get("/settings")
    assert r.status_code == 200
    data = r.json()
    assert "settings" in data
    assert "llm_backend" in data["settings"]


def test_get_all_settings_redacts_api_keys(client):
    r = client.get("/settings")
    settings = r.json()["settings"]
    assert settings.get("openai_api_key") == "***"
    assert settings.get("anthropic_api_key") == "***"


def test_get_single_setting(client):
    r = client.get("/settings/llm_backend")
    assert r.status_code == 200
    assert r.json()["key"] == "llm_backend"
    assert r.json()["value"] is not None


def test_get_missing_setting(client):
    r = client.get("/settings/nonexistent_key_xyz")
    assert r.status_code == 404


def test_get_api_key_blocked(client):
    r = client.get("/settings/openai_api_key")
    assert r.status_code == 403


# ── PUT /settings/{key} ────────────────────────────────────────────────────────

def test_update_setting(client):
    r = client.put("/settings/description_language", json={"value": "en"})
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    # verify persisted
    r2 = client.get("/settings/description_language")
    assert r2.json()["value"] == "en"


def test_update_api_key_blocked(client):
    r = client.put("/settings/openai_api_key", json={"value": "sk-secret"})
    assert r.status_code == 403


# ── GET/POST/DELETE /accounts ──────────────────────────────────────────────────

def test_list_accounts_empty(client):
    r = client.get("/accounts")
    assert r.status_code == 200
    assert r.json() == []


def test_create_account(client):
    r = client.post("/accounts", json={"name": "Conto POPSO", "bank_name": "Banca Popolare di Sondrio"})
    assert r.status_code == 201
    data = r.json()
    assert data["name"] == "Conto POPSO"
    assert data["id"] is not None


def test_create_and_list_accounts(client):
    client.post("/accounts", json={"name": "Fineco", "bank_name": "FinecoBank"})
    r = client.get("/accounts")
    names = [a["name"] for a in r.json()]
    assert "Fineco" in names


def test_delete_account(client):
    r = client.post("/accounts", json={"name": "ToDelete"})
    acc_id = r.json()["id"]
    d = client.delete(f"/accounts/{acc_id}")
    assert d.status_code == 200
    assert d.json()["deleted"] == 1


def test_delete_account_not_found(client):
    r = client.delete("/accounts/9999")
    assert r.status_code == 404


# ── GET /taxonomy/categories ───────────────────────────────────────────────────

def test_list_taxonomy_categories(client):
    r = client.get("/taxonomy/categories")
    assert r.status_code == 200
    # taxonomy is seeded from taxonomy.yaml or defaults
    assert isinstance(r.json(), list)


def test_list_taxonomy_filter_by_type(client):
    r_exp = client.get("/taxonomy/categories", params={"type": "expense"})
    r_inc = client.get("/taxonomy/categories", params={"type": "income"})
    assert r_exp.status_code == 200
    assert r_inc.status_code == 200
    exp_types = {c["type"] for c in r_exp.json()}
    inc_types = {c["type"] for c in r_inc.json()}
    assert exp_types <= {"expense"}
    assert inc_types <= {"income"}


def test_create_and_delete_category(client):
    r = client.post("/taxonomy/categories", json={"name": "Test Category", "type": "expense"})
    assert r.status_code == 201
    cat_id = r.json()["id"]

    d = client.delete(f"/taxonomy/categories/{cat_id}")
    assert d.status_code == 200


def test_create_subcategory(client):
    r = client.post("/taxonomy/categories", json={"name": "ParentCat", "type": "expense"})
    cat_id = r.json()["id"]
    rs = client.post(f"/taxonomy/categories/{cat_id}/subcategories", json={"name": "SubA"})
    assert rs.status_code == 201
    assert rs.json()["name"] == "SubA"


def test_update_subcategory(client):
    r = client.post("/taxonomy/categories", json={"name": "CatForSub", "type": "income"})
    cat_id = r.json()["id"]
    rs = client.post(f"/taxonomy/categories/{cat_id}/subcategories", json={"name": "OldName"})
    sub_id = rs.json()["id"]
    ru = client.patch(f"/taxonomy/subcategories/{sub_id}", json={"name": "NewName"})
    assert ru.status_code == 200


def test_delete_category_not_found(client):
    r = client.delete("/taxonomy/categories/99999")
    assert r.status_code == 404
