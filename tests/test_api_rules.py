"""API tests — /rules endpoints."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from api.main import app
from api.dependencies import get_rule_service, get_engine
from db.models import create_tables
from services.rule_service import RuleService


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def mem_engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_tables(eng)
    return eng


@pytest.fixture
def client(mem_engine):
    app.dependency_overrides[get_rule_service] = lambda: RuleService(mem_engine)
    app.dependency_overrides[get_engine] = lambda: mem_engine
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ── GET /rules/category ────────────────────────────────────────────────────────

def test_list_category_rules_empty(client):
    r = client.get("/rules/category")
    assert r.status_code == 200
    assert r.json() == []


# ── POST /rules/category ───────────────────────────────────────────────────────

def test_create_category_rule(client):
    payload = {
        "pattern": "coop",
        "match_type": "contains",
        "category": "Alimentari",
        "subcategory": "Supermercato",
        "priority": 1,
    }
    r = client.post("/rules/category", json=payload)
    assert r.status_code == 201
    data = r.json()
    assert data["pattern"] == "coop"
    assert data["category"] == "Alimentari"
    assert data["id"] is not None


def test_create_category_rule_invalid_match_type(client):
    r = client.post("/rules/category", json={
        "pattern": "x", "match_type": "invalid", "category": "Test", "subcategory": ""
    })
    assert r.status_code == 422


def test_create_and_list(client):
    client.post("/rules/category", json={
        "pattern": "eni", "match_type": "contains", "category": "Auto", "subcategory": "Carburante"
    })
    r = client.get("/rules/category")
    assert r.status_code == 200
    patterns = [rule["pattern"] for rule in r.json()]
    assert "eni" in patterns


# ── PATCH /rules/category/{id} ─────────────────────────────────────────────────

def test_update_category_rule(client):
    r = client.post("/rules/category", json={
        "pattern": "lidl", "match_type": "contains", "category": "Alimentari", "subcategory": ""
    })
    rule_id = r.json()["id"]
    patch = client.patch(f"/rules/category/{rule_id}", json={"category": "Svago"})
    assert patch.status_code == 200


def test_update_category_rule_not_found(client):
    r = client.patch("/rules/category/9999", json={"category": "Svago"})
    assert r.status_code == 404


def test_update_category_rule_empty_body(client):
    r = client.post("/rules/category", json={
        "pattern": "x", "match_type": "exact", "category": "Altro", "subcategory": ""
    })
    rule_id = r.json()["id"]
    patch = client.patch(f"/rules/category/{rule_id}", json={})
    assert patch.status_code == 422


# ── DELETE /rules/category/{id} ───────────────────────────────────────────────

def test_delete_category_rule(client):
    r = client.post("/rules/category", json={
        "pattern": "todelete", "match_type": "exact", "category": "Altro", "subcategory": ""
    })
    rule_id = r.json()["id"]
    d = client.delete(f"/rules/category/{rule_id}")
    assert d.status_code == 200
    assert d.json()["deleted"] == 1


def test_delete_category_rule_not_found(client):
    r = client.delete("/rules/category/9999")
    assert r.status_code == 404


# ── Description Rules ──────────────────────────────────────────────────────────

def test_list_description_rules_empty(client):
    r = client.get("/rules/description")
    assert r.status_code == 200
    assert r.json() == []


def test_create_description_rule(client):
    r = client.post("/rules/description", json={
        "raw_pattern": "AMAZON.IT*",
        "match_type": "contains",
        "cleaned_description": "Amazon",
    })
    assert r.status_code == 201
    data = r.json()
    assert data["cleaned_description"] == "Amazon"


def test_delete_description_rule(client):
    r = client.post("/rules/description", json={
        "raw_pattern": "DELETE_ME",
        "match_type": "exact",
        "cleaned_description": "ToDelete",
    })
    rule_id = r.json()["id"]
    d = client.delete(f"/rules/description/{rule_id}")
    assert d.status_code == 200
    assert d.json()["deleted"] == 1
