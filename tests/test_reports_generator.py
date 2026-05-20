"""Tests for reports/generator.py — HTML/CSV/XLSX report builders.

The module had zero coverage; these tests exercise the four public
functions through the actual Transaction table so we catch regressions
in the filter / aggregation / serialisation logic.
"""
from __future__ import annotations

import csv
import io
from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from db.models import Transaction, create_tables, get_session
from reports.generator import (
    _query_summary,
    generate_csv_export,
    generate_html_report,
    generate_xlsx_export,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    create_tables(eng)
    return eng


def _make_tx(
    tx_id: str,
    date: str,
    amount: str,
    tx_type: str = "expense",
    category: str | None = None,
    description: str = "test",
    account: str = "BancaX",
) -> Transaction:
    """Build a Transaction row with sane defaults for testing."""
    return Transaction(
        id=tx_id,
        date=date,
        amount=Decimal(amount),
        currency="EUR",
        description=description,
        source_file="fixture.csv",
        doc_type="bank_statement",
        account_label=account,
        tx_type=tx_type,
        category=category,
        subcategory=None,
        reconciled=False,
        to_review=False,
    )


def _seed(engine, txs: list[Transaction]) -> None:
    with get_session(engine) as s:
        for tx in txs:
            s.add(tx)
        s.commit()


# ── _query_summary ────────────────────────────────────────────────────────────

class TestQuerySummary:

    def test_empty_db_returns_zero_totals(self, engine):
        with get_session(engine) as s:
            summary = _query_summary(s)
        assert summary["transactions"] == []
        assert summary["net"] == 0
        assert summary["total_income"] == 0
        assert summary["total_expense"] == 0
        assert summary["by_category"] == {}
        assert summary["date_from"] is None
        assert summary["date_to"] is None

    def test_net_income_expense_split(self, engine):
        _seed(engine, [
            _make_tx("a" * 24, "2026-01-15", "+1000", tx_type="income", category="Stipendio"),
            _make_tx("b" * 24, "2026-01-20", "-300", tx_type="expense", category="Alimentari"),
            _make_tx("c" * 24, "2026-01-25", "-100", tx_type="expense", category="Trasporti"),
        ])
        with get_session(engine) as s:
            summary = _query_summary(s)
        assert summary["total_income"] == Decimal("1000")
        assert summary["total_expense"] == Decimal("-400")
        assert summary["net"] == Decimal("600")

    def test_excludes_internal_and_settlement_types(self, engine):
        """internal_out/in, card_settlement, aggregate_debit must NOT count
        toward net/income/expense or per-category totals."""
        _seed(engine, [
            _make_tx("a" * 24, "2026-01-01", "+1000", tx_type="income", category="Stipendio"),
            _make_tx("b" * 24, "2026-01-02", "-500", tx_type="internal_out", category="Giroconto"),
            _make_tx("c" * 24, "2026-01-03", "+500", tx_type="internal_in", category="Giroconto"),
            _make_tx("d" * 24, "2026-01-04", "-50", tx_type="card_settlement", category="Carta"),
            _make_tx("e" * 24, "2026-01-05", "-25", tx_type="aggregate_debit", category="Bollette"),
        ])
        with get_session(engine) as s:
            summary = _query_summary(s)
        assert summary["net"] == Decimal("1000")
        assert summary["total_income"] == Decimal("1000")
        assert summary["total_expense"] == Decimal("0")
        # Excluded categories must not show up
        for cat in ("Giroconto", "Carta", "Bollette"):
            assert cat not in summary["by_category"]

    def test_groups_by_category_with_altro_fallback_for_null(self, engine):
        _seed(engine, [
            _make_tx("a" * 24, "2026-01-01", "-50", category="Alimentari"),
            _make_tx("b" * 24, "2026-01-02", "-30", category="Alimentari"),
            _make_tx("c" * 24, "2026-01-03", "-20", category=None),  # → "Altro"
        ])
        with get_session(engine) as s:
            summary = _query_summary(s)
        assert summary["by_category"]["Alimentari"] == Decimal("-80")
        assert summary["by_category"]["Altro"] == Decimal("-20")

    def test_date_filters_are_passed_through(self, engine):
        _seed(engine, [
            _make_tx("a" * 24, "2026-01-15", "-50", category="Alimentari"),
            _make_tx("b" * 24, "2026-02-15", "-30", category="Alimentari"),
        ])
        with get_session(engine) as s:
            summary = _query_summary(s, date_from="2026-02-01", date_to="2026-02-28")
        assert summary["date_from"] == "2026-02-01"
        assert summary["date_to"] == "2026-02-28"
        # Only February tx survives
        assert len(summary["transactions"]) == 1
        assert summary["transactions"][0].date == "2026-02-15"


# ── generate_html_report ──────────────────────────────────────────────────────

class TestGenerateHtmlReport:

    def test_empty_db_produces_valid_html(self, engine):
        with get_session(engine) as s:
            html = generate_html_report(s)
        assert isinstance(html, str)
        assert "<html" in html.lower() or "<!doctype" in html.lower()

    def test_with_transactions_includes_category_and_amounts(self, engine):
        _seed(engine, [
            _make_tx("a" * 24, "2026-01-01", "+1000", tx_type="income", category="Stipendio"),
            _make_tx("b" * 24, "2026-01-02", "-200", tx_type="expense", category="Alimentari"),
        ])
        with get_session(engine) as s:
            html = generate_html_report(s)
        # Categories should appear (either in template or fallback)
        assert "Stipendio" in html or "Alimentari" in html
        # And the HTML must be non-trivial
        assert len(html) > 100


# ── generate_csv_export ───────────────────────────────────────────────────────

class TestGenerateCsvExport:

    def test_empty_db_returns_header_only(self, engine):
        with get_session(engine) as s:
            data = generate_csv_export(s)
        assert isinstance(data, bytes)
        text = data.decode("utf-8")
        reader = list(csv.reader(io.StringIO(text)))
        assert len(reader) == 1  # header only
        assert "id" in reader[0]
        assert "date" in reader[0]
        assert "amount" in reader[0]

    def test_row_per_transaction(self, engine):
        _seed(engine, [
            _make_tx("a" * 24, "2026-01-01", "-50", category="Alimentari", description="Esselunga"),
            _make_tx("b" * 24, "2026-01-02", "-30", category="Trasporti", description="ATM"),
        ])
        with get_session(engine) as s:
            data = generate_csv_export(s)
        text = data.decode("utf-8")
        reader = list(csv.reader(io.StringIO(text)))
        assert len(reader) == 3  # header + 2 rows
        # The description column appears somewhere in the row payload
        all_text = "\n".join(text.splitlines()[1:])
        assert "Esselunga" in all_text
        assert "ATM" in all_text


# ── generate_xlsx_export ──────────────────────────────────────────────────────

class TestGenerateXlsxExport:

    def test_empty_db_returns_valid_xlsx_bytes(self, engine):
        with get_session(engine) as s:
            data = generate_xlsx_export(s)
        assert isinstance(data, bytes)
        # XLSX is a ZIP container; the magic bytes are PK\x03\x04
        assert data.startswith(b"PK")

    def test_xlsx_roundtrip_with_pandas(self, engine):
        _seed(engine, [
            _make_tx("a" * 24, "2026-01-01", "-50", category="Alimentari"),
            _make_tx("b" * 24, "2026-01-02", "-30", category="Trasporti"),
        ])
        with get_session(engine) as s:
            data = generate_xlsx_export(s)
        import pandas as pd
        df = pd.read_excel(io.BytesIO(data), sheet_name="Transactions")
        assert len(df) == 2
        assert "amount" in df.columns
        assert "category" in df.columns
        assert set(df["category"].tolist()) == {"Alimentari", "Trasporti"}
