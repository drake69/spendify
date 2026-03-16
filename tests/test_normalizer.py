"""Unit tests for core/normalizer.py — all deterministic, no LLM mocks needed."""
from decimal import Decimal
from datetime import date

import pandas as pd
import pytest

from core.normalizer import (
    PreprocessInfo,
    compute_transaction_id,
    compute_file_hash,
    detect_and_strip_preheader_rows,
    detect_delimiter,
    detect_encoding,
    drop_low_variability_columns,
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
        id1 = compute_transaction_id("file.csv", "01/01/2024", "100,00", "SUPERMERCATO COOP")
        id2 = compute_transaction_id("file.csv", "01/01/2024", "100,00", "SUPERMERCATO COOP")
        assert id1 == id2

    def test_length_24(self):
        tx_id = compute_transaction_id("file.csv", "01/01/2024", "100,00", "desc")
        assert len(tx_id) == 24

    def test_different_inputs_differ(self):
        id1 = compute_transaction_id("file.csv", "01/01/2024", "100,00", "desc a")
        id2 = compute_transaction_id("file.csv", "01/01/2024", "100,00", "desc b")
        assert id1 != id2

    def test_debit_credit_convention(self):
        # debit_positive: raw_amount is "<debit>|<credit>"
        id1 = compute_transaction_id("file.csv", "01/01/2024", "50,00|", "pagamento")
        id2 = compute_transaction_id("file.csv", "01/01/2024", "|100,00", "accredito")
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


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_tx_df(n_rows: int = 20) -> pd.DataFrame:
    """Return a clean transaction-like DataFrame with no pre-header rows."""
    return pd.DataFrame({
        "Data": [f"2024-01-{i+1:02d}" for i in range(n_rows)],
        "Descrizione": [f"Pagamento {i}" for i in range(n_rows)],
        "Importo": [f"{(i+1)*10:.2f}" for i in range(n_rows)],
        "Tipo": ["Addebito" if i % 2 == 0 else "Accredito" for i in range(n_rows)],
    })


# ── TestDetectAndStripPreheaderRows ────────────────────────────────────────────

class TestDetectAndStripPreheaderRows:

    def test_no_preheader_rows_unchanged(self):
        df = _make_tx_df(20)
        result, n = detect_and_strip_preheader_rows(df, "test.csv")
        assert n == 0
        assert len(result) == len(df)

    def test_strips_sparse_rows_at_start(self):
        """Simulate the real scenario: bank file with title rows before data.

        When a bank CSV looks like:
            Estratto Conto,,,,
            Dal 01/01/2024 al 31/01/2024,,,,
            Data;Descrizione;Importo;Tipo
            2024-01-01;Pag 0;10.00;Addebito
            ...

        pandas.read_csv (with detect_header_row returning 0) consumes the
        FIRST row as column names: ["Estratto Conto", ..., Unnamed: N].
        The actual header row ("Data", "Descrizione", ...) ends up as row 1 in
        the DataFrame body; row 0 is the second metadata line.

        Inside detect_and_strip_preheader_rows, both the reconstructed
        header (from Unnamed-heavy column names) AND the metadata body row
        are sparse — so n_sparse == 2 and the real header becomes the new
        column names.
        """
        n_data = 20
        # Simulate pandas output: first real column has a title value,
        # the rest are Unnamed (because the title row had empty cells).
        title_cols = ["Estratto Conto", "Unnamed: 1", "Unnamed: 2", "Unnamed: 3"]
        rows = [
            ["Dal 01/01/2024 al 31/01/2024", None, None, None],    # sparse body row
            ["Data", "Descrizione", "Importo", "Tipo"],              # real header
        ] + [
            [f"2024-01-{i+1:02d}", f"Pag {i}", f"{(i+1)*10:.2f}", "Addebito"]
            for i in range(n_data)
        ]
        df = pd.DataFrame(rows, columns=title_cols)

        result, n = detect_and_strip_preheader_rows(df, "bank.csv")

        # The reconstructed header row ("Estratto Conto" | None | None | None,
        # density=1/4=0.25) and body row 0 (density=1/4=0.25) are both sparse
        # → n_sparse == 2; real header is in df_full row 2.
        assert n == 2
        assert list(result.columns) == ["Data", "Descrizione", "Importo", "Tipo"]
        assert len(result) == n_data

    def test_too_short_returns_unchanged(self):
        df = _make_tx_df(3)
        result, n = detect_and_strip_preheader_rows(df, "short.csv")
        assert n == 0
        assert len(result) == 3

    def test_exceeds_absolute_cap_raises(self):
        """If more than 20 contiguous sparse rows exist → ValueError.

        Use Unnamed column names so the reconstructed header row is also sparse
        (density 0/4 = 0.0), making it contiguous with the empty body rows.
        """
        n_sparse = 25
        n_data = 50
        # Unnamed columns → reconstructed header is fully sparse
        cols = [f"Unnamed: {i}" for i in range(4)]
        sparse_rows = [[None] * 4 for _ in range(n_sparse)]
        data_rows = [[f"v{i}", f"desc {i}", f"{i}.00", "X"] for i in range(n_data)]
        df = pd.DataFrame(sparse_rows + data_rows, columns=cols)
        with pytest.raises(ValueError, match="exceeding the absolute cap"):
            detect_and_strip_preheader_rows(df, "bad.csv")

    def test_exceeds_ratio_cap_raises(self):
        """If sparse rows > 10 % of total → ValueError.

        With Unnamed columns: 4 sparse rows + reconstructed header (also sparse)
        = 5 total sparse in df_full of 36 rows → 5/36 ≈ 13.9 % > 10 %.
        """
        # 4 sparse body rows + 30 data rows.
        # df_full total = 1 (header) + 4 (sparse body) + 30 (data) = 35 rows
        # n_sparse = 5 (header + 4 body) → 5/35 ≈ 14.3 % > 10 % → raises
        n_sparse_body = 4
        n_data = 30
        cols = [f"Unnamed: {i}" for i in range(4)]
        sparse_rows = [[None] * 4 for _ in range(n_sparse_body)]
        data_rows = [[f"v{i}", f"desc {i}", f"{i}.00", "X"] for i in range(n_data)]
        df = pd.DataFrame(sparse_rows + data_rows, columns=cols)
        with pytest.raises(ValueError, match="safety cap"):
            detect_and_strip_preheader_rows(df, "ratio.csv")

    def test_clean_file_with_many_rows_not_triggered(self):
        """A large clean file should never trigger false positives."""
        df = _make_tx_df(100)
        result, n = detect_and_strip_preheader_rows(df, "large.csv")
        assert n == 0
        assert len(result) == 100


# ── TestDropLowVariabilityColumns ─────────────────────────────────────────────

class TestDropLowVariabilityColumns:

    def test_no_low_variability_columns_unchanged(self):
        df = _make_tx_df(20)
        result, dropped = drop_low_variability_columns(df, "test.csv")
        assert dropped == []
        assert list(result.columns) == list(df.columns)

    def test_drops_constant_column(self):
        """A column with the same value on every row should be dropped.

        With 100 rows: 1 unique / 100 rows = 1 % < 1.5 % threshold → dropped.
        """
        df = _make_tx_df(100)
        df["Nome titolare"] = "Mario Rossi"   # constant — will be dropped
        result, dropped = drop_low_variability_columns(df, "amex.csv")
        assert "Nome titolare" in dropped
        assert "Nome titolare" not in result.columns

    def test_drops_near_constant_column(self):
        """A column that barely varies (< 1.5 %) should also be dropped."""
        n = 100
        df = _make_tx_df(n)
        # Only 1 unique value among 100 rows → ratio = 1/100 = 1 % < 1.5 %
        df["Numero carta"] = "**** **** **** 1234"
        result, dropped = drop_low_variability_columns(df, "amex.csv")
        assert "Numero carta" in dropped

    def test_preserves_minimum_two_columns(self):
        """Even if all columns are constant, at least 2 are kept.

        With n=100 rows: 1 unique / 100 = 1 % < 1.5 % for every column,
        so all three are flagged. But max_droppable = 3 - 2 = 1, so only
        one column is removed, leaving exactly 2.
        """
        n = 100
        df = pd.DataFrame({
            "A": ["x"] * n,
            "B": ["y"] * n,
            "C": ["z"] * n,
        })
        result, dropped = drop_low_variability_columns(df, "edge.csv")
        assert len(result.columns) == 2

    def test_does_not_drop_variable_columns(self):
        """Columns with sufficient variability must be kept."""
        n = 100
        df = pd.DataFrame({
            "Data": [f"2024-01-{i % 28 + 1:02d}" for i in range(n)],
            "Importo": [f"{i:.2f}" for i in range(n)],
            "Descrizione": [f"Pagamento {i}" for i in range(n)],
        })
        result, dropped = drop_low_variability_columns(df, "good.csv")
        assert dropped == []
        assert len(result.columns) == 3

    def test_too_few_rows_unchanged(self):
        df = pd.DataFrame({"A": ["x"], "B": ["y"], "C": ["z"]})
        result, dropped = drop_low_variability_columns(df, "tiny.csv")
        assert dropped == []
        assert len(result.columns) == 3

    def test_already_two_columns_unchanged(self):
        df = pd.DataFrame({"A": ["x"] * 20, "B": ["y"] * 20})
        result, dropped = drop_low_variability_columns(df, "min.csv")
        assert dropped == []
        assert len(result.columns) == 2


# ── TestPreprocessInfo ─────────────────────────────────────────────────────────

class TestPreprocessInfo:
    def test_defaults(self):
        info = PreprocessInfo()
        assert info.skipped_rows == 0
        assert info.dropped_columns == []

    def test_custom_values(self):
        info = PreprocessInfo(skipped_rows=3, dropped_columns=["Nome titolare"])
        assert info.skipped_rows == 3
        assert info.dropped_columns == ["Nome titolare"]
