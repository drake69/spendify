"""SQLAlchemy ORM models (RF-07).

Six tables:
  import_batch            – one record per imported file
  document_schema         – parsing template (Flow 2 → Flow 1 promotion)
  transaction             – canonical transaction with all fields
  reconciliation_link     – card_settlement ↔ card_tx N:M
  internal_transfer_link  – giroconto pairs
  category_rule           – user-defined override rules (RF-05)
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship

DB_URL = "sqlite:///ledger.db"


class Base(DeclarativeBase):
    pass


def get_engine(db_url: str = DB_URL):
    return create_engine(db_url, connect_args={"check_same_thread": False})


def create_tables(engine=None):
    if engine is None:
        engine = get_engine()
    Base.metadata.create_all(engine)
    return engine


def get_session(engine=None) -> Session:
    from sqlalchemy.orm import sessionmaker
    if engine is None:
        engine = get_engine()
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


# ── Tables ────────────────────────────────────────────────────────────────────

class ImportBatch(Base):
    __tablename__ = "import_batch"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sha256 = Column(String(64), unique=True, nullable=False, index=True)
    filename = Column(String(512), nullable=False)
    imported_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    flow_used = Column(String(10))  # "flow1" | "flow2"
    n_transactions = Column(Integer, default=0)
    errors = Column(Text)

    transactions = relationship("Transaction", back_populates="batch")


class DocumentSchemaModel(Base):
    __tablename__ = "document_schema"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_identifier = Column(String(512), unique=True, nullable=False, index=True)
    doc_type = Column(String(32), nullable=False)
    date_col = Column(String(128))
    date_accounting_col = Column(String(128))
    amount_col = Column(String(128))
    debit_col = Column(String(128))
    credit_col = Column(String(128))
    description_col = Column(String(128))
    currency_col = Column(String(128))
    default_currency = Column(String(3), default="EUR")
    date_format = Column(String(64))
    sign_convention = Column(String(32))
    is_zero_sum = Column(Boolean, default=False)
    internal_transfer_patterns = Column(Text)  # JSON array
    account_label = Column(String(256))
    encoding = Column(String(32), default="utf-8")
    sheet_name = Column(String(256))
    skip_rows = Column(Integer, default=0)
    delimiter = Column(String(4))
    confidence = Column(String(10))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, onupdate=lambda: datetime.now(timezone.utc))


class Transaction(Base):
    __tablename__ = "transaction"

    id = Column(String(24), primary_key=True)  # SHA-256[:24]
    batch_id = Column(Integer, ForeignKey("import_batch.id"), nullable=True)
    date = Column(String(10), nullable=False)       # ISO 8601 YYYY-MM-DD
    date_accounting = Column(String(10))
    amount = Column(Numeric(precision=18, scale=4), nullable=False)
    currency = Column(String(3), default="EUR")
    description = Column(Text)
    source_file = Column(String(512))
    doc_type = Column(String(32))
    account_label = Column(String(256))
    tx_type = Column(String(32))
    category = Column(String(128))
    subcategory = Column(String(128))
    category_confidence = Column(String(10))
    category_source = Column(String(10))
    reconciled = Column(Boolean, default=False)
    to_review = Column(Boolean, default=False)
    transfer_pair_id = Column(String(64))
    transfer_confidence = Column(String(10))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    batch = relationship("ImportBatch", back_populates="transactions")


class ReconciliationLink(Base):
    __tablename__ = "reconciliation_link"

    id = Column(Integer, primary_key=True, autoincrement=True)
    settlement_id = Column(String(24), ForeignKey("transaction.id"), nullable=False)
    detail_id = Column(String(24), ForeignKey("transaction.id"), nullable=False)
    delta = Column(Numeric(precision=18, scale=4), default=0)
    method = Column(String(32))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint("settlement_id", "detail_id"),)


class InternalTransferLink(Base):
    __tablename__ = "internal_transfer_link"

    id = Column(Integer, primary_key=True, autoincrement=True)
    out_id = Column(String(24), ForeignKey("transaction.id"), nullable=False)
    in_id = Column(String(24), ForeignKey("transaction.id"), nullable=False)
    confidence = Column(String(10))
    keyword_matched = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint("out_id", "in_id"),)


class CategoryRule(Base):
    __tablename__ = "category_rule"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pattern = Column(Text, nullable=False)
    match_type = Column(String(10), nullable=False)  # contains | regex | exact
    category = Column(String(128), nullable=False)
    subcategory = Column(String(128))
    doc_type = Column(String(32))
    priority = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
