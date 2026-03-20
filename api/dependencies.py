"""FastAPI dependency injection — DB engine and service instances."""
from __future__ import annotations

import os
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from db.models import create_tables
from services import (
    TransactionService,
    RuleService,
    SettingsService,
    CategoryService,
    ImportService,
)


def _db_url() -> str:
    return os.getenv("DATABASE_URL", "sqlite:///ledger.db")


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    engine = create_engine(_db_url(), connect_args={"check_same_thread": False})
    create_tables(engine)
    return engine


# FastAPI `Depends()` callables — one service instance per request is fine
# because each service manages its own session context internally.

def get_transaction_service() -> TransactionService:
    return TransactionService(get_engine())


def get_rule_service() -> RuleService:
    return RuleService(get_engine())


def get_settings_service() -> SettingsService:
    return SettingsService(get_engine())


def get_category_service() -> CategoryService:
    return CategoryService(get_engine())


def get_import_service() -> ImportService:
    return ImportService(get_engine())
