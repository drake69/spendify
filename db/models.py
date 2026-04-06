"""SQLAlchemy ORM models (RF-07).

Thirteen tables:
  import_batch            – one record per imported file
  document_schema         – parsing template (Flow 2 → Flow 1 promotion)
  transaction             – canonical transaction with all fields
  reconciliation_link     – card_settlement ↔ card_tx N:M
  internal_transfer_link  – giroconto pairs
  category_rule           – user-defined override rules (RF-05)
  user_settings           – persistent user preferences (locale, language)
  taxonomy_category       – category definitions (expense / income)
  taxonomy_subcategory    – subcategory definitions (FK → taxonomy_category)
  account                 – user-defined bank accounts (stable dedup key)
  description_rule        – bulk description replacement rules (raw_description → cleaned)
  budget_target           – per-category % budget targets (A-02)
  nsi_tag_mapping         – OSM tag → (category, subcategory) for C-08-cascade NSI bypass
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
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


DEFAULT_USER_SETTINGS = {
    "date_display_format": "%d/%m/%Y",
    "amount_decimal_sep": ",",
    "amount_thousands_sep": ".",
    "description_language": "it",
    "country": "",  # ISO 3166-1 alpha-2 (e.g. "IT", "DE"). Empty = not set.
    "llm_backend": "local_llama_cpp",
    "llama_cpp_model_path": "",
    "llama_cpp_n_gpu_layers": "-1",
    "ollama_base_url": "http://localhost:11434",
    "ollama_model": "gemma3:12b",
    "openai_api_key": "",
    "openai_model": "gpt-4o-mini",
    "anthropic_api_key": "",
    "anthropic_model": "claude-3-5-haiku-latest",
    "compat_base_url": "",
    "compat_api_key": "",
    "compat_model": "",
    "use_owner_names_giroconto": "false",
    "import_test_mode": "false",
    "contexts": '["Quotidianità", "Lavoro", "Vacanza"]',
    "giroconto_mode": "neutral",
    "max_transaction_amount": "1000000",
    "force_schema_import": "false",  # I-04: skip schema review, always auto-import
    # NOTE: onboarding_done is NOT in defaults — it's managed by
    # _migrate_set_onboarding_done_for_existing_users() and SettingsService.
}


def create_tables(engine=None):
    if engine is None:
        engine = get_engine()
    try:
        Base.metadata.create_all(engine, checkfirst=True)
    except Exception as exc:
        if "already exists" not in str(exc).lower():
            raise
    _migrate_add_raw_columns(engine)
    _migrate_add_user_settings(engine)
    _migrate_add_import_job(engine)
    _migrate_add_invert_sign(engine)
    _migrate_add_taxonomy_default(engine)   # must run before _migrate_add_taxonomy
    _migrate_add_taxonomy(engine)
    _migrate_add_accounts(engine)
    _migrate_add_description_cols(engine)
    _migrate_add_context(engine)
    _migrate_add_description_rules(engine)
    _migrate_add_rule_context(engine)
    _migrate_add_header_sha256(engine)
    _migrate_add_confidence_score(engine)
    _migrate_add_transaction_updated_at(engine)
    _migrate_add_classification_tracking(engine)
    _migrate_add_taxonomy_fallback(engine)
    _migrate_add_account_type(engine)
    _migrate_consolidate_account_type(engine)
    _migrate_savings_to_savings_account(engine)
    _migrate_add_import_batch_tracking(engine)
    _migrate_add_budget_target(engine)
    _migrate_add_footer_patterns(engine)
    _migrate_add_has_borders(engine)
    _migrate_add_nsi_tag_mapping(engine)
    _migrate_set_onboarding_done_for_existing_users(engine)  # must run last
    return engine


def _migrate_add_user_settings(engine) -> None:
    """Create user_settings table and seed defaults if not already present."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        conn.execute(_text(
            'CREATE TABLE IF NOT EXISTS user_settings '
            '(key VARCHAR(64) PRIMARY KEY, value VARCHAR(255))'
        ))
        conn.commit()
        # Seed defaults only if the table was just created (no rows yet)
        row = conn.execute(_text("SELECT COUNT(*) FROM user_settings")).scalar()
        if row == 0:
            for k, v in DEFAULT_USER_SETTINGS.items():
                conn.execute(
                    _text("INSERT INTO user_settings (key, value) VALUES (:k, :v)"),
                    {"k": k, "v": v},
                )
            conn.commit()
        else:
            # Insert any missing keys (for existing DBs upgrading)
            for k, v in DEFAULT_USER_SETTINGS.items():
                conn.execute(
                    _text("INSERT OR IGNORE INTO user_settings (key, value) VALUES (:k, :v)"),
                    {"k": k, "v": v},
                )
            conn.commit()


def _migrate_add_raw_columns(engine) -> None:
    """Add raw_description / raw_amount to existing DBs (idempotent).

    Uses try/except instead of PRAGMA inspection to stay compatible with
    both SQLAlchemy 1.x and 2.x row-access semantics.
    """
    from sqlalchemy import text as _text
    stmts = [
        'ALTER TABLE "transaction" ADD COLUMN raw_description TEXT',
        'ALTER TABLE "transaction" ADD COLUMN raw_amount TEXT',
    ]
    with engine.connect() as conn:
        for stmt in stmts:
            try:
                conn.execute(_text(stmt))
                conn.commit()
            except Exception as exc:
                if "duplicate column name" in str(exc).lower():
                    pass  # column already exists, nothing to do
                else:
                    raise


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
    account_label = Column(String(256), nullable=True)
    imported_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    flow_used = Column(String(10))  # "flow1" | "flow2"
    n_transactions = Column(Integer, default=0)
    status = Column(String(16), default="completed")  # completed | cancelled
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
    description_cols = Column(Text)  # JSON array of column names for multi-col concat
    currency_col = Column(String(128))
    default_currency = Column(String(3), default="EUR")
    date_format = Column(String(64))
    sign_convention = Column(String(32))
    is_zero_sum = Column(Boolean, default=False)
    invert_sign = Column(Boolean, default=False)
    internal_transfer_patterns = Column(Text)  # JSON array
    footer_patterns = Column(Text)  # JSON array of learned footer text patterns
    has_borders = Column(Boolean, default=False)  # True if XLSX format uses bordered table
    account_label = Column(String(256))
    encoding = Column(String(32), default="utf-8")
    sheet_name = Column(String(256))
    skip_rows = Column(Integer, default=0)
    delimiter = Column(String(4))
    confidence = Column(String(10))
    confidence_score = Column(Float, nullable=True)  # 0.0-1.0 deterministic score
    header_sha256 = Column(String(64), nullable=True, index=True)
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
    category_source = Column(String(10))            # "manual" | "rule" | "llm" | future: "history"
    reconciled = Column(Boolean, default=False)
    to_review = Column(Boolean, default=False)
    transfer_pair_id = Column(String(64))
    transfer_confidence = Column(String(10))
    raw_description = Column(Text, nullable=True)   # original text before normalize_description
    raw_amount = Column(String(64), nullable=True)  # original string from source file
    context = Column(String(64), nullable=True)     # user-defined life context (e.g. Vacanza, Lavoro)
    human_validated = Column(Boolean, default=False)
    validated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, onupdate=lambda: datetime.now(timezone.utc))

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
    context = Column(String(64))               # optional — None means "don't set"
    doc_type = Column(String(32))
    priority = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class DescriptionRule(Base):
    """Bulk description replacement rule.

    When a transaction's raw_description matches raw_pattern (using match_type),
    its description field is replaced with cleaned_description.
    """
    __tablename__ = "description_rule"

    id = Column(Integer, primary_key=True, autoincrement=True)
    raw_pattern = Column(Text, nullable=False)          # pattern to match in raw_description
    match_type = Column(String(10), nullable=False)      # exact | contains | regex
    cleaned_description = Column(Text, nullable=False)  # replacement description
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint("raw_pattern", "match_type"),)


class UserSettings(Base):
    __tablename__ = "user_settings"

    key = Column(String(64), primary_key=True)
    value = Column(String(255), nullable=True)


class ImportJob(Base):
    __tablename__ = "import_job"

    id = Column(Integer, primary_key=True, autoincrement=True)
    status = Column(String(16), default="running")   # running | completed | error
    progress = Column(Numeric(5, 4), default=0.0)
    status_message = Column(Text)
    detail_message = Column(Text)
    n_transactions = Column(Integer, default=0)
    n_files = Column(Integer, default=0)
    started_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime)


class TaxonomyCategory(Base):
    __tablename__ = "taxonomy_category"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False)
    type = Column(String(8), nullable=False)   # "expense" | "income"
    sort_order = Column(Integer, default=0)
    is_fallback = Column(Boolean, default=False)

    subcategories = relationship(
        "TaxonomySubcategory",
        back_populates="category",
        cascade="all, delete-orphan",
        order_by="TaxonomySubcategory.sort_order",
    )


class TaxonomySubcategory(Base):
    __tablename__ = "taxonomy_subcategory"

    id = Column(Integer, primary_key=True, autoincrement=True)
    category_id = Column(Integer, ForeignKey("taxonomy_category.id"), nullable=False)
    name = Column(String(128), nullable=False)
    sort_order = Column(Integer, default=0)

    category = relationship("TaxonomyCategory", back_populates="subcategories")


class TaxonomyDefault(Base):
    """Built-in taxonomy templates, one row per (language, type, category, subcategory).

    These rows are seeded from db.taxonomy_defaults.TAXONOMY_DEFAULTS and never
    modified by the user.  On onboarding the user picks a language and the rows
    are copied into taxonomy_category / taxonomy_subcategory (user-editable).
    """
    __tablename__ = "taxonomy_default"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    language       = Column(String(8),   nullable=False)   # "it" | "en" | "fr" | "de" | "es"
    type           = Column(String(8),   nullable=False)   # "expense" | "income"
    category       = Column(String(128), nullable=False)
    subcategory    = Column(String(128), nullable=True)     # NULL for category-level rows
    sort_order_cat = Column(Integer, default=0)
    sort_order_sub = Column(Integer, default=0)
    is_fallback = Column(Boolean, default=False)


VALID_ACCOUNT_TYPES = frozenset({
    "bank_account", "credit_card", "debit_card", "prepaid_card",
    "savings_account", "cash",
})


class Account(Base):
    """User-defined bank account.  Provides a stable dedup key (name) that is
    independent of the filename used when importing the file."""
    __tablename__ = "account"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(256), unique=True, nullable=False)   # e.g. "Conto POPSO", "Carta CartaSI"
    bank_name = Column(String(256))                           # optional free-text bank name
    account_type = Column(String(32), nullable=True)          # bank_account | credit_card | debit_card | prepaid_card | savings_account | cash
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class BudgetTarget(Base):
    """Monthly budget target as % of total expenses for a category (A-02)."""
    __tablename__ = "budget_target"

    id = Column(Integer, primary_key=True, autoincrement=True)
    category = Column(String(128), nullable=False, unique=True)
    target_pct = Column(Numeric(5, 2), nullable=False)
    period_type = Column(String(16), default="monthly")       # monthly (future: quarterly, yearly)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, onupdate=lambda: datetime.now(timezone.utc))


class NsiTagMapping(Base):
    """OSM tag → (category, subcategory) mapping for C-08-cascade NSI bypass.

    Built once via NsiTaxonomyService.build() and invalidated when the user
    taxonomy changes (detected by SHA-256 hash comparison).
    """
    __tablename__ = "nsi_tag_mapping"

    osm_tag = Column(String(128), primary_key=True)   # e.g. "shop=supermarket"
    category = Column(String(128), nullable=False)
    subcategory = Column(String(128), nullable=False)
    taxonomy_hash = Column(String(64), nullable=False)  # SHA-256 of taxonomy at build time
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


def _migrate_add_import_job(engine) -> None:
    """Create import_job table if not present (idempotent)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        conn.execute(_text(
            'CREATE TABLE IF NOT EXISTS import_job ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'status VARCHAR(16) DEFAULT "running", '
            'progress NUMERIC(5,4) DEFAULT 0.0, '
            'status_message TEXT, '
            'detail_message TEXT, '
            'n_transactions INTEGER DEFAULT 0, '
            'n_files INTEGER DEFAULT 0, '
            'started_at DATETIME, '
            'completed_at DATETIME)'
        ))
        conn.commit()


def _migrate_add_taxonomy_default(engine) -> None:
    """Create taxonomy_default table and seed all built-in language templates (idempotent)."""
    from sqlalchemy import text as _text
    from db.taxonomy_defaults import TAXONOMY_DEFAULTS

    with engine.connect() as conn:
        conn.execute(_text(
            'CREATE TABLE IF NOT EXISTS taxonomy_default ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'language VARCHAR(8) NOT NULL, '
            'type VARCHAR(8) NOT NULL, '
            'category VARCHAR(128) NOT NULL, '
            'subcategory VARCHAR(128), '
            'sort_order_cat INTEGER DEFAULT 0, '
            'sort_order_sub INTEGER DEFAULT 0)'
        ))
        conn.execute(_text(
            'CREATE UNIQUE INDEX IF NOT EXISTS uq_taxonomy_default '
            'ON taxonomy_default(language, type, category, COALESCE(subcategory, \'\'))'
        ))
        conn.commit()

        # Seed all languages from the Python definitions (INSERT OR IGNORE = idempotent)
        for lang, data in TAXONOMY_DEFAULTS.items():
            for type_key, entries in (("expense", data["expenses"]), ("income", data["income"])):
                for sort_cat, entry in enumerate(entries):
                    cat_name = entry["category"]
                    conn.execute(_text(
                        'INSERT OR IGNORE INTO taxonomy_default '
                        '(language, type, category, subcategory, sort_order_cat, sort_order_sub) '
                        'VALUES (:lang, :type, :cat, NULL, :sc, 0)'
                    ), {"lang": lang, "type": type_key, "cat": cat_name, "sc": sort_cat})
                    for sort_sub, sub in enumerate(entry.get("subcategories", [])):
                        conn.execute(_text(
                            'INSERT OR IGNORE INTO taxonomy_default '
                            '(language, type, category, subcategory, sort_order_cat, sort_order_sub) '
                            'VALUES (:lang, :type, :cat, :sub, :sc, :ss)'
                        ), {"lang": lang, "type": type_key, "cat": cat_name,
                            "sub": sub, "sc": sort_cat, "ss": sort_sub})
        conn.commit()


def _migrate_add_taxonomy(engine) -> None:
    """Create taxonomy tables and seed from taxonomy_default (idempotent)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        conn.execute(_text(
            'CREATE TABLE IF NOT EXISTS taxonomy_category ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'name VARCHAR(128) NOT NULL, '
            'type VARCHAR(8) NOT NULL, '
            'sort_order INTEGER DEFAULT 0)'
        ))
        conn.execute(_text(
            'CREATE TABLE IF NOT EXISTS taxonomy_subcategory ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'category_id INTEGER NOT NULL REFERENCES taxonomy_category(id) ON DELETE CASCADE, '
            'name VARCHAR(128) NOT NULL, '
            'sort_order INTEGER DEFAULT 0)'
        ))
        conn.commit()

        # Deduplicate any rows that crept in via race conditions at first startup
        # (Streamlit can run app.py twice concurrently before the first commit).
        # Keep only the lowest-id row per (name, type).
        conn.execute(_text(
            "DELETE FROM taxonomy_category WHERE id NOT IN ("
            "  SELECT MIN(id) FROM taxonomy_category GROUP BY name, type"
            ")"
        ))
        conn.commit()

        # Unique index prevents future duplicates (CREATE … IF NOT EXISTS is safe
        # even when the index already exists).
        conn.execute(_text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_taxonomy_category_name_type "
            "ON taxonomy_category(name, type)"
        ))
        conn.commit()

        # Seed only if still empty (INSERT OR IGNORE makes it race-safe).
        count = conn.execute(_text("SELECT COUNT(*) FROM taxonomy_category")).scalar()
        if count == 0:
            _seed_taxonomy(conn)
            conn.commit()


def _seed_taxonomy(conn, language: str = "it") -> None:
    """Seed taxonomy_category/subcategory from taxonomy_default for *language*.

    Falls back to the Python TAXONOMY_DEFAULTS dict if taxonomy_default is empty
    (e.g. during the very first migration run before _migrate_add_taxonomy_default
    has committed its data).
    """
    from sqlalchemy import text as _text

    # Try reading from the taxonomy_default table first
    rows = conn.execute(_text(
        'SELECT type, category, subcategory, sort_order_cat, sort_order_sub '
        'FROM taxonomy_default WHERE language = :lang AND subcategory IS NOT NULL '
        'ORDER BY sort_order_cat, sort_order_sub'
    ), {"lang": language}).fetchall()

    if not rows:
        # Fallback: read from Python dict (covers the migration ordering edge case)
        from db.taxonomy_defaults import TAXONOMY_DEFAULTS
        data = TAXONOMY_DEFAULTS.get(language, TAXONOMY_DEFAULTS["it"])
        for type_key, entries in (("expense", data["expenses"]), ("income", data["income"])):
            for sort_i, entry in enumerate(entries):
                r = conn.execute(_text(
                    "INSERT OR IGNORE INTO taxonomy_category (name, type, sort_order) VALUES (:n,:t,:s)"
                ), {"n": entry["category"], "t": type_key, "s": sort_i})
                cat_id = r.lastrowid
                for sort_j, sub in enumerate(entry.get("subcategories", [])):
                    conn.execute(_text(
                        "INSERT INTO taxonomy_subcategory (category_id, name, sort_order) VALUES (:c,:n,:s)"
                    ), {"c": cat_id, "n": sub, "s": sort_j})
        return

    # Normal path: copy from taxonomy_default
    cat_id_map: dict[tuple[str, str], int] = {}
    for row in rows:
        type_key, cat_name, sub_name, sort_cat, sort_sub = row
        key = (type_key, cat_name)
        if key not in cat_id_map:
            r = conn.execute(_text(
                "INSERT OR IGNORE INTO taxonomy_category (name, type, sort_order) VALUES (:n,:t,:s)"
            ), {"n": cat_name, "t": type_key, "s": sort_cat})
            # lastrowid is 0 on IGNORE — fetch the real id
            existing = conn.execute(_text(
                "SELECT id FROM taxonomy_category WHERE name=:n AND type=:t"
            ), {"n": cat_name, "t": type_key}).scalar()
            cat_id_map[key] = existing
        conn.execute(_text(
            "INSERT INTO taxonomy_subcategory (category_id, name, sort_order) VALUES (:c,:n,:s)"
        ), {"c": cat_id_map[key], "n": sub_name, "s": sort_sub})


def _migrate_add_accounts(engine) -> None:
    """Create account table if not present (idempotent)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        conn.execute(_text(
            'CREATE TABLE IF NOT EXISTS account '
            '(id INTEGER PRIMARY KEY AUTOINCREMENT, '
            ' name TEXT UNIQUE NOT NULL, '
            ' bank_name TEXT, '
            ' created_at DATETIME)'
        ))
        conn.commit()


def _migrate_add_description_cols(engine) -> None:
    """Add description_cols column to document_schema if not present (idempotent)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        try:
            conn.execute(_text(
                'ALTER TABLE document_schema ADD COLUMN description_cols TEXT'
            ))
            conn.commit()
        except Exception as exc:
            if "duplicate column name" in str(exc).lower():
                pass  # column already exists
            else:
                raise


def _migrate_add_context(engine) -> None:
    """Add context column to transaction table if not present (idempotent)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        try:
            conn.execute(_text('ALTER TABLE "transaction" ADD COLUMN context VARCHAR(64)'))
            conn.commit()
        except Exception as exc:
            if "duplicate column name" in str(exc).lower():
                pass
            else:
                raise


def _migrate_add_invert_sign(engine) -> None:
    """Add invert_sign column to document_schema if not present (idempotent)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        try:
            conn.execute(_text(
                'ALTER TABLE document_schema ADD COLUMN invert_sign BOOLEAN DEFAULT 0'
            ))
            conn.commit()
        except Exception as exc:
            if "duplicate column name" in str(exc).lower():
                pass  # column already exists
            else:
                raise


def _migrate_add_description_rules(engine) -> None:
    """Create description_rule table if not present (idempotent)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        conn.execute(_text(
            'CREATE TABLE IF NOT EXISTS description_rule ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'raw_pattern TEXT NOT NULL, '
            'match_type VARCHAR(10) NOT NULL, '
            'cleaned_description TEXT NOT NULL, '
            'created_at DATETIME, '
            'UNIQUE(raw_pattern, match_type))'
        ))
        conn.commit()


def _migrate_add_rule_context(engine) -> None:
    """Add context column to category_rule if not present (idempotent)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        try:
            conn.execute(_text(
                'ALTER TABLE category_rule ADD COLUMN context VARCHAR(64)'
            ))
            conn.commit()
        except Exception as exc:
            if "duplicate column name" in str(exc).lower():
                pass
            else:
                raise


def _migrate_add_import_batch_tracking(engine) -> None:
    """Add account_label and status columns to import_batch if not present (idempotent)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        for col_sql in [
            'ALTER TABLE import_batch ADD COLUMN account_label VARCHAR(256)',
            'ALTER TABLE import_batch ADD COLUMN status VARCHAR(16) DEFAULT "completed"',
        ]:
            try:
                conn.execute(_text(col_sql))
                conn.commit()
            except Exception as exc:
                if "duplicate column name" in str(exc).lower():
                    pass
                else:
                    raise
        # Backfill status for existing rows that have NULL
        conn.execute(_text(
            "UPDATE import_batch SET status = 'completed' WHERE status IS NULL"
        ))
        conn.commit()


def _migrate_set_onboarding_done_for_existing_users(engine) -> None:
    """Mark onboarding as complete for DBs that already have taxonomy data.

    Prevents existing users from seeing the onboarding wizard after upgrading.
    Runs after all other migrations so taxonomy_category is guaranteed to exist.
    Condition: taxonomy_category has rows (= app was already set up before onboarding
    was introduced).  Does nothing if onboarding_done is already set.
    """
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        existing = conn.execute(_text(
            "SELECT value FROM user_settings WHERE key='onboarding_done'"
        )).scalar()
        if existing is not None:
            return  # already decided — don't touch
        count = conn.execute(_text("SELECT COUNT(*) FROM taxonomy_category")).scalar()
        if count and count > 0:
            conn.execute(_text(
                "INSERT OR REPLACE INTO user_settings (key, value) "
                "VALUES ('onboarding_done', 'true')"
            ))
            conn.commit()


def _migrate_add_header_sha256(engine) -> None:
    """Add header_sha256 column to document_schema if not already present."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        try:
            conn.execute(_text("ALTER TABLE document_schema ADD COLUMN header_sha256 VARCHAR(64)"))
            conn.execute(_text("CREATE INDEX IF NOT EXISTS ix_document_schema_header_sha256 ON document_schema (header_sha256)"))
            conn.commit()
        except Exception:
            pass  # column already exists


def _migrate_add_confidence_score(engine) -> None:
    """Add confidence_score column to document_schema if not already present."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        try:
            conn.execute(_text("ALTER TABLE document_schema ADD COLUMN confidence_score FLOAT"))
            conn.commit()
        except Exception:
            pass  # column already exists


def _migrate_add_transaction_updated_at(engine) -> None:
    """Add updated_at column to transaction table if not already present (idempotent)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        try:
            conn.execute(_text('ALTER TABLE "transaction" ADD COLUMN updated_at DATETIME'))
            conn.commit()
        except Exception as exc:
            if "duplicate column name" in str(exc).lower():
                pass  # column already exists
            else:
                raise


def _migrate_add_classification_tracking(engine) -> None:
    """Add human_validated and validated_at to transaction (idempotent)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        for col_sql in [
            'ALTER TABLE "transaction" ADD COLUMN human_validated BOOLEAN DEFAULT 0',
            'ALTER TABLE "transaction" ADD COLUMN validated_at DATETIME',
        ]:
            try:
                conn.execute(_text(col_sql))
                conn.commit()
            except Exception as exc:
                if "duplicate column name" in str(exc).lower():
                    pass
                else:
                    raise


def _migrate_add_account_type(engine) -> None:
    """Add account_type column to account table if not present (idempotent)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        try:
            conn.execute(_text(
                'ALTER TABLE account ADD COLUMN account_type VARCHAR(32)'
            ))
            conn.commit()
        except Exception as exc:
            if "duplicate column name" in str(exc).lower():
                pass  # column already exists
            else:
                raise


def _migrate_add_taxonomy_fallback(engine) -> None:
    """Add is_fallback to taxonomy_category and taxonomy_default, mark Altro as fallback (idempotent)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        for table in ["taxonomy_category", "taxonomy_default"]:
            try:
                conn.execute(_text(f'ALTER TABLE {table} ADD COLUMN is_fallback BOOLEAN DEFAULT 0'))
                conn.commit()
            except Exception as exc:
                if "duplicate column name" in str(exc).lower():
                    pass
                else:
                    raise
        # Mark existing fallback categories
        for name in ("Altro", "Altro entrate", "Other", "Other income"):
            conn.execute(_text('UPDATE taxonomy_category SET is_fallback = 1 WHERE name = :n'), {"n": name})
            conn.execute(_text('UPDATE taxonomy_default SET is_fallback = 1 WHERE category = :n'), {"n": name})
        conn.commit()


def _migrate_consolidate_account_type(engine) -> None:
    """Revert 'card' back to 'debit_card' (undo previous merge, idempotent)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        conn.execute(_text(
            "UPDATE account SET account_type = 'debit_card' "
            "WHERE account_type = 'card'"
        ))
        conn.commit()


def _migrate_savings_to_savings_account(engine) -> None:
    """Rename doc_type 'savings' to 'savings_account' everywhere (idempotent)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        conn.execute(_text(
            "UPDATE document_schema SET doc_type = 'savings_account' "
            "WHERE doc_type = 'savings'"
        ))
        conn.execute(_text(
            'UPDATE "transaction" SET doc_type = \'savings_account\' '
            "WHERE doc_type = 'savings'"
        ))
        conn.commit()


def _migrate_add_footer_patterns(engine) -> None:
    """Add footer_patterns column to document_schema if not already present."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        try:
            conn.execute(_text("ALTER TABLE document_schema ADD COLUMN footer_patterns TEXT"))
            conn.commit()
        except Exception:
            pass  # column already exists


def _migrate_add_has_borders(engine) -> None:
    """Add has_borders column to document_schema if not already present."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        try:
            conn.execute(_text(
                "ALTER TABLE document_schema ADD COLUMN has_borders BOOLEAN DEFAULT 0"
            ))
            conn.commit()
        except Exception:
            pass  # column already exists


def _migrate_add_budget_target(engine) -> None:
    """Create budget_target table if not present (idempotent, A-02)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        conn.execute(_text(
            'CREATE TABLE IF NOT EXISTS budget_target ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'category VARCHAR(128) NOT NULL UNIQUE, '
            'target_pct NUMERIC(5,2) NOT NULL, '
            'period_type VARCHAR(16) DEFAULT "monthly", '
            'created_at DATETIME, '
            'updated_at DATETIME)'
        ))
        conn.commit()


def _migrate_add_nsi_tag_mapping(engine) -> None:
    """Create nsi_tag_mapping table if not present (idempotent, C-08-cascade)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        conn.execute(_text(
            'CREATE TABLE IF NOT EXISTS nsi_tag_mapping ('
            'osm_tag VARCHAR(128) PRIMARY KEY, '
            'category VARCHAR(128) NOT NULL, '
            'subcategory VARCHAR(128) NOT NULL, '
            'taxonomy_hash VARCHAR(64) NOT NULL, '
            'updated_at DATETIME)'
        ))
        conn.commit()
