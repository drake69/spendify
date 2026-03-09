"""Unit tests for core/normalizer.py — all deterministic, no LLM mocks needed."""
from decimal import Decimal
from datetime import date

import pytest

from core.normalizer import (
    compute_transaction_id,
    compute_file_hash,
    detect_delimiter,
    detect_encoding,
    normalize_description,
    parse_amount,
    parse_date_safe,
)


class TestParseAmount:
    def test_integer(self):
        assert parse_amount("42") == Decimal("42")

    def test_negative(self):
        assert parse_amount("-12.50") == Decimal("-12.50")

    def test_european_format(self):
        assert parse_amount("1.234,56") == Decimal("1234.56")

    def test_us_format(self):
        assert parse_amount("1,234.56") == Decimal("1234.56")

    def test_comma_only_decimal(self):
        assert parse_amount("12,50") == Decimal("12.50")

    def test_strip_currency_symbol(self):
        assert parse_amount("€ 99,99") == Decimal("99.99")

    def test_decimal_instance(self):
        d = Decimal("5.00")
        assert parse_amount(d) is d

    def test_float(self):
        assert parse_amount(3.14) == Decimal("3.14")

    def test_invalid_returns_none(self):
        assert parse_amount("N/A") is None


class TestParseDateSafe:
    def test_italian_format(self):
        assert parse_date_safe("15/03/2024", "%d/%m/%Y") == date(2024, 3, 15)

    def test_iso_format(self):
        assert parse_date_safe("2024-03-15", "%Y-%m-%d") == date(2024, 3, 15)

    def test_invalid_returns_none(self):
        assert parse_date_safe("not-a-date", "%d/%m/%Y") is None

    def test_empty_returns_none(self):
        assert parse_date_safe("", "%d/%m/%Y") is None


class TestNormalizeDescription:
    def test_casefold(self):
        assert normalize_description("AMAZON.IT") == "amazon.it"

    def test_strips_whitespace(self):
        assert normalize_description("  foo  ") == "foo"

    def test_empty(self):
        assert normalize_description("") == ""


class TestComputeTransactionId:
    def test_deterministic(self):
        id1 = compute_transaction_id("file.csv", date(2024, 1, 1), Decimal("100.00"), "test desc")
        id2 = compute_transaction_id("file.csv", date(2024, 1, 1), Decimal("100.00"), "test desc")
        assert id1 == id2

    def test_length_24(self):
        tx_id = compute_transaction_id("file.csv", date(2024, 1, 1), Decimal("100.00"), "desc")
        assert len(tx_id) == 24

    def test_different_inputs_differ(self):
        id1 = compute_transaction_id("file.csv", date(2024, 1, 1), Decimal("100.00"), "desc a")
        id2 = compute_transaction_id("file.csv", date(2024, 1, 1), Decimal("100.00"), "desc b")
        assert id1 != id2


class TestComputeFileHash:
    def test_deterministic(self):
        data = b"test content"
        assert compute_file_hash(data) == compute_file_hash(data)

    def test_length_64(self):
        assert len(compute_file_hash(b"anything")) == 64


class TestDetectDelimiter:
    def test_comma(self):
        assert detect_delimiter("a,b,c\n1,2,3") == ","

    def test_semicolon(self):
        assert detect_delimiter("a;b;c\n1;2;3") == ";"

    def test_tab(self):
        assert detect_delimiter("a\tb\tc\n1\t2\t3") == "\t"
