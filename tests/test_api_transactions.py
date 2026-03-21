"""API tests — /transactions endpoints."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from api.main import app
from api.dependencies import get_transaction_service, get_engine
from db.models import Base, Transaction, create_tables
from services.transaction_service import TransactionService


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def mem_engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_tables(eng)
    return eng


@pytest.fixture
def client(mem_engine):
    app.dependency_overrides[get_transaction_service] = lambda: TransactionService(mem_engine)
    app.dependency_overrides[get_engine] = lambda: mem_engine
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _seed(engine, *, tx_id="abc123", description="Supermercato Coop",
          amount=-42.0, category="Alimentari", subcategory="Supermercato",
          to_review=False, context=None):
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=engine)
    with Session() as s:
        t = Transaction(
            id=tx_id, date="2025-03-01",
            description=description, amount=amount, currency="EUR",
            category=category, subcategory=subcategory,
            category_source="llm", category_confidence="high",
            to_review=to_review, context=context,
        )
        s.add(t)
        s.commit()


# ── Health ─────────────────────────────────────────────────────────────────────

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ── GET /transactions ──────────────────────────────────────────────────────────

def test_list_transactions_empty(client):
    r = client.get("/transactions")
    assert r.status_code == 200
    data = r.json()
    assert data["items"] == []
    assert data["total"] == 0


def test_list_transactions_returns_seeded(client, mem_engine):
    _seed(mem_engine)
    r = client.get("/transactions")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    tx = data["items"][0]
    assert tx["id"] == "abc123"
    assert tx["category"] == "Alimentari"
    assert tx["amount"] == pytest.approx(-42.0)


def test_list_transactions_filter_by_category(client, mem_engine):
    _seed(mem_engine, tx_id="t1", description="Coop", category="Alimentari")
    _seed(mem_engine, tx_id="t2", description="Trenitalia", category="Trasporti")
    r = client.get("/transactions", params={"category": "Alimentari"})
    assert r.status_code == 200
    ids = [t["id"] for t in r.json()["items"]]
    assert "t1" in ids
    assert "t2" not in ids


def test_list_transactions_filter_to_review(client, mem_engine):
    _seed(mem_engine, tx_id="r1", to_review=True)
    _seed(mem_engine, tx_id="r2", to_review=False)
    r = client.get("/transactions", params={"to_review": "true"})
    assert r.status_code == 200
    ids = [t["id"] for t in r.json()["items"]]
    assert "r1" in ids
    assert "r2" not in ids


def test_list_transactions_limit(client, mem_engine):
    for i in range(10):
        _seed(mem_engine, tx_id=f"tx{i:02d}", description=f"tx {i}")
    r = client.get("/transactions", params={"limit": 3})
    assert r.status_code == 200
    assert len(r.json()["items"]) == 3


# ── PATCH /transactions/{id}/category ─────────────────────────────────────────

def test_update_category(client, mem_engine):
    _seed(mem_engine)
    r = client.patch("/transactions/abc123/category", json={"category": "Svago", "subcategory": "Cinema"})
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_update_category_not_found(client):
    r = client.patch("/transactions/nonexistent/category", json={"category": "Svago", "subcategory": ""})
    assert r.status_code == 404


def test_update_category_missing_field(client, mem_engine):
    _seed(mem_engine)
    r = client.patch("/transactions/abc123/category", json={"subcategory": "Cinema"})
    assert r.status_code == 422


# ── PATCH /transactions/{id}/context ──────────────────────────────────────────

def test_update_context(client, mem_engine):
    _seed(mem_engine)
    r = client.patch("/transactions/abc123/context", json={"context": "Vacanza"})
    assert r.status_code == 200


def test_update_context_clear(client, mem_engine):
    _seed(mem_engine, context="Lavoro")
    r = client.patch("/transactions/abc123/context", json={"context": None})
    assert r.status_code == 200


def test_update_context_not_found(client):
    r = client.patch("/transactions/ghost/context", json={"context": "Vacanza"})
    assert r.status_code == 404


# ── DELETE /transactions ───────────────────────────────────────────────────────

def test_delete_transactions_by_category(client, mem_engine):
    _seed(mem_engine, tx_id="d1", category="Alimentari")
    _seed(mem_engine, tx_id="d2", category="Trasporti")
    r = client.delete("/transactions", params={"category": "Alimentari"})
    assert r.status_code == 200
    assert r.json()["deleted"] >= 1


def test_delete_transactions_no_filter_rejected(client):
    r = client.delete("/transactions")
    assert r.status_code == 422
