"""FastAPI application — REST API layer for the personal finance ledger.

All business logic lives in /services/.  This module is a thin HTTP adapter.

Endpoints:
  GET    /health
  GET    /transactions
  PATCH  /transactions/{id}/category
  PATCH  /transactions/{id}/context
  POST   /transactions/{id}/toggle-giroconto
  DELETE /transactions

  GET    /rules/category
  POST   /rules/category
  PATCH  /rules/category/{id}
  DELETE /rules/category/{id}
  POST   /rules/category/apply-to-review
  POST   /rules/category/apply-to-all
  GET    /rules/description
  POST   /rules/description
  DELETE /rules/description/{id}

  GET    /settings
  GET    /settings/{key}
  PUT    /settings/{key}

  GET    /accounts
  POST   /accounts
  DELETE /accounts/{id}

  GET    /taxonomy/categories
  POST   /taxonomy/categories
  PATCH  /taxonomy/categories/{id}
  DELETE /taxonomy/categories/{id}
  POST   /taxonomy/categories/{id}/subcategories
  PATCH  /taxonomy/subcategories/{id}
  DELETE /taxonomy/subcategories/{id}

  GET    /import/jobs/latest
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import import_, rules, taxonomy, transactions
from api.routers.settings import accounts_router, router as settings_router
from api.schemas import StatusResponse

app = FastAPI(
    title="Spendif.ai API",
    description="REST API for the personal finance ledger. UI-independent — all logic in /services/.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501", "http://127.0.0.1:8501"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(transactions.router)
app.include_router(rules.router)
app.include_router(settings_router)
app.include_router(accounts_router)
app.include_router(taxonomy.router)
app.include_router(import_.router)


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health", response_model=StatusResponse, tags=["health"])
def health() -> StatusResponse:
    return StatusResponse(status="ok")
