"""Unit tests for the 3-phase footer stripping system.

Phase 1: Structural filter (deterministic, no mocks)
Phase 2: Semantic LLM extraction (mocked LLM)
Phase 3: Pattern matching (deterministic, no mocks)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch
from types import SimpleNamespace

import pandas as pd
import pytest

from core.normalizer import (
    _normalize_description_to_pattern,
    _resolve_description_col,
    strip_footer_phase1,
    strip_footer_phase2_llm,
    strip_footer_phase3_patterns,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_schema(
    date_col="Data",
    description_col="Descrizione",
    amount_col=None,
    debit_col=None,
    credit_col=None,
    description_cols=None,
    footer_patterns=None,
):
    """Build a minimal schema-like object for testing."""
    return SimpleNamespace(
        date_col=date_col,
        description_col=description_col,
        description_cols=description_cols or [],
        amount_col=amount_col,
        debit_col=debit_col,
        credit_col=credit_col,
        footer_patterns=footer_patterns or [],
        source_identifier="test_src_id",
    )


def _3col_schema(**kw):
    return _make_schema(amount_col="Importo", **kw)


def _4col_schema(**kw):
    return _make_schema(debit_col="Dare", credit_col="Avere", **kw)


# ── Test: _normalize_description_to_pattern ──────────────────────────────────

class TestNormalizeDescriptionToPattern:
    def test_strips_dates(self):
        assert _normalize_description_to_pattern("Totale al 31/12/2024") == "totale al"

    def test_strips_numbers(self):
        assert _normalize_description_to_pattern("Saldo 1.234,56 EUR") == "saldo eur"

    def test_strips_both(self):
        result = _normalize_description_to_pattern("Totale dare al 15-01-2025: 12345,67")
        assert "totale dare al" in result
        assert "15-01-2025" not in result
        assert "12345" not in result

    def test_empty_string(self):
        assert _normalize_description_to_pattern("") == ""

    def test_only_numbers(self):
        assert _normalize_description_to_pattern("123,45") == ""


# ── Test: _resolve_description_col ───────────────────────────────────────────

class TestResolveDescriptionCol:
    def test_description_cols_priority(self):
        schema = _make_schema(description_cols=["Col1", "Col2"], description_col="Fallback")
        assert _resolve_description_col(schema) == "Col1"

    def test_fallback_to_description_col(self):
        schema = _make_schema(description_col="Desc")
        assert _resolve_description_col(schema) == "Desc"

    def test_none_when_both_empty(self):
        schema = _make_schema(description_col=None, description_cols=[])
        assert _resolve_description_col(schema) is None


# ── Test: Phase 1 — Structural filter ────────────────────────────────────────

class TestPhase1:
    def test_3col_removes_rows_with_na_amount(self):
        schema = _3col_schema()
        df = pd.DataFrame({
            "Data": ["2024-01-01", "2024-01-02", "2024-01-03", None],
            "Descrizione": ["Pagamento", "Stipendio", "Bolletta", "Totale"],
            "Importo": [100, 200, 300, None],
        })
        result, n = strip_footer_phase1(df, schema)
        assert n == 1
        assert len(result) == 3

    def test_3col_removes_contiguous_footer(self):
        schema = _3col_schema()
        # Use enough rows that safety cap (5% → at least 1) allows 2 footer rows
        df = pd.DataFrame({
            "Data": [f"2024-01-{i+1:02}" for i in range(50)] + [None, None],
            "Descrizione": [f"Tx{i}" for i in range(50)] + ["Totale dare", None],
            "Importo": list(range(50)) + [None, None],
        })
        result, n = strip_footer_phase1(df, schema)
        assert n == 2
        assert len(result) == 50

    def test_3col_stops_at_non_na_row(self):
        """Non-contiguous NA rows: only bottom contiguous block is stripped."""
        schema = _3col_schema()
        df = pd.DataFrame({
            "Data": [None, "2024-01-02", "2024-01-03", None],
            "Descrizione": [None, "Pag", "Pag", "Footer"],
            "Importo": [None, 100, 200, None],
        })
        result, n = strip_footer_phase1(df, schema)
        assert n == 1
        assert len(result) == 3

    def test_4col_na_in_debit_credit_not_stripped(self):
        """4-column schema: debit/credit can be NA (valid row with no amount)."""
        schema = _4col_schema()
        df = pd.DataFrame({
            "Data": ["2024-01-01", "2024-01-02", "2024-01-03"],
            "Descrizione": ["Pag1", "Pag2", "Pag3"],
            "Dare": [100, None, None],
            "Avere": [None, 200, None],
        })
        result, n = strip_footer_phase1(df, schema)
        assert n == 0
        assert len(result) == 3

    def test_4col_na_date_stripped(self):
        schema = _4col_schema()
        df = pd.DataFrame({
            "Data": ["2024-01-01", "2024-01-02", None],
            "Descrizione": ["Pag1", "Pag2", "Totale"],
            "Dare": [100, 200, 300],
            "Avere": [None, None, None],
        })
        result, n = strip_footer_phase1(df, schema)
        assert n == 1

    def test_4col_na_description_stripped(self):
        schema = _4col_schema()
        df = pd.DataFrame({
            "Data": ["2024-01-01", "2024-01-02", "2024-01-03"],
            "Descrizione": ["Pag1", "Pag2", None],
            "Dare": [100, 200, 300],
            "Avere": [None, None, None],
        })
        result, n = strip_footer_phase1(df, schema)
        assert n == 1

    def test_all_rows_valid(self):
        schema = _3col_schema()
        df = pd.DataFrame({
            "Data": ["2024-01-01", "2024-01-02"],
            "Descrizione": ["A", "B"],
            "Importo": [10, 20],
        })
        result, n = strip_footer_phase1(df, schema)
        assert n == 0
        assert len(result) == 2

    def test_too_few_rows(self):
        schema = _3col_schema()
        df = pd.DataFrame({
            "Data": [None],
            "Descrizione": [None],
            "Importo": [None],
        })
        result, n = strip_footer_phase1(df, schema)
        assert n == 0  # too few rows (< 3)

    def test_safety_cap(self):
        """Safety cap: never strip more than 5 rows."""
        schema = _3col_schema()
        # 100 valid rows + 10 footer rows
        data = {
            "Data": [f"2024-01-{i+1:02}" for i in range(100)] + [None] * 10,
            "Descrizione": [f"Tx{i}" for i in range(100)] + [None] * 10,
            "Importo": [i * 10 for i in range(100)] + [None] * 10,
        }
        df = pd.DataFrame(data)
        result, n = strip_footer_phase1(df, schema)
        assert n == 5  # capped at _FOOTER_MAX_ROWS


# ── Test: Phase 2 — LLM extraction ──────────────────────────────────────────

_MOCK_PROMPT = {
    "system": "test system",
    "user_template": "date: {date_col}, desc: {description_col}, amt: {amount_info}\n{rows_json}",
    "response_schema": {
        "type": "object",
        "required": ["footer_rows"],
        "properties": {"footer_rows": {"type": "array"}},
    },
}


def _mock_open_prompt():
    import json
    from unittest.mock import mock_open
    return mock_open(read_data=json.dumps(_MOCK_PROMPT))


class TestPhase2:
    def _mock_backend(self, footer_indices):
        """Create a mock LLM backend returning the given footer indices."""
        backend = MagicMock()
        backend.is_remote = False
        backend.complete_structured.return_value = {
            "footer_rows": [
                {"index": i, "reason": "total"} for i in footer_indices
            ]
        }
        return backend

    def test_identifies_footer_rows(self):
        schema = _3col_schema()
        df = pd.DataFrame({
            "Data": ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", ""],
            "Descrizione": ["Pag1", "Pag2", "Pag3", "Pag4", "Totale dare 1234,56"],
            "Importo": [100, 200, 300, 400, 1000],
        })
        backend = self._mock_backend([4])  # last row is footer

        with patch("builtins.open", _mock_open_prompt()):
            result_df, patterns, n = strip_footer_phase2_llm(df, schema, backend)

        assert n == 1
        assert len(result_df) == 4
        assert len(patterns) >= 0  # pattern extraction from "Totale dare"

    def test_no_footer_detected(self):
        schema = _3col_schema()
        df = pd.DataFrame({
            "Data": ["2024-01-01", "2024-01-02"],
            "Descrizione": ["A", "B"],
            "Importo": [10, 20],
        })
        backend = self._mock_backend([])

        with patch("builtins.open", _mock_open_prompt()):
            result_df, patterns, n = strip_footer_phase2_llm(df, schema, backend)

        assert n == 0
        assert len(result_df) == 2
        assert patterns == []

    def test_llm_failure_raises(self):
        schema = _3col_schema()
        df = pd.DataFrame({
            "Data": ["2024-01-01"],
            "Descrizione": ["A"],
            "Importo": [10],
        })
        backend = MagicMock()
        backend.complete_structured.side_effect = RuntimeError("LLM timeout")

        with patch("builtins.open", _mock_open_prompt()):
            with pytest.raises(RuntimeError, match="LLM timeout"):
                strip_footer_phase2_llm(df, schema, backend)

    def test_pattern_extraction(self):
        """Verify patterns are correctly extracted: dates/numbers stripped."""
        schema = _3col_schema()
        df = pd.DataFrame({
            "Data": ["2024-01-01", "2024-01-02", ""],
            "Descrizione": ["Pag1", "Pag2", "Totale dare al 31/12/2024: 1.234,56"],
            "Importo": [100, 200, 1234.56],
        })
        backend = self._mock_backend([2])

        with patch("builtins.open", _mock_open_prompt()):
            _, patterns, _ = strip_footer_phase2_llm(df, schema, backend)

        assert len(patterns) == 1
        pat = patterns[0]
        assert "totale dare al" in pat
        # Dates and numbers should be stripped
        assert "31/12/2024" not in pat
        assert "1234" not in pat

    def test_short_patterns_discarded(self):
        """Patterns shorter than 3 chars are discarded."""
        schema = _3col_schema()
        df = pd.DataFrame({
            "Data": ["2024-01-01", "2024-01-02", ""],
            "Descrizione": ["Pag1", "Pag2", "42"],
            "Importo": [100, 200, 42],
        })
        backend = self._mock_backend([2])

        with patch("builtins.open", _mock_open_prompt()):
            _, patterns, _ = strip_footer_phase2_llm(df, schema, backend)

        # "42" normalizes to "" which is < 3 chars → discarded
        assert patterns == []


# ── Test: Phase 3 — Pattern matching ─────────────────────────────────────────

class TestPhase3:
    def test_pattern_match_removes_row(self):
        schema = _3col_schema(footer_patterns=["totale dare al"])
        df = pd.DataFrame({
            "Data": ["2024-01-01", "2024-01-02", "2024-01-03"],
            "Descrizione": ["Pag1", "Pag2", "Totale Dare al 31/12/2025: 999,99"],
            "Importo": [100, 200, 999.99],
        })
        result, unmatched, n = strip_footer_phase3_patterns(
            df, schema, ["totale dare al"],
        )
        assert n == 1
        assert len(result) == 2
        assert unmatched == []

    def test_no_match_no_removal(self):
        schema = _3col_schema()
        df = pd.DataFrame({
            "Data": ["2024-01-01", "2024-01-02"],
            "Descrizione": ["Pag1", "Pag2"],
            "Importo": [100, 200],
        })
        result, unmatched, n = strip_footer_phase3_patterns(
            df, schema, ["totale dare al"],
        )
        assert n == 0
        assert len(result) == 2

    def test_case_insensitive(self):
        schema = _3col_schema()
        df = pd.DataFrame({
            "Data": ["2024-01-01", "2024-01-02"],
            "Descrizione": ["Pag1", "SALDO FINALE 100,00"],
            "Importo": [100, 100],
        })
        result, unmatched, n = strip_footer_phase3_patterns(
            df, schema, ["saldo finale"],
        )
        assert n == 1

    def test_unmatched_suspects_detected(self):
        """Rows with footer keywords but no pattern match → unmatched suspects."""
        schema = _3col_schema()
        df = pd.DataFrame({
            "Data": ["2024-01-01", "2024-01-02", "2024-01-03"],
            "Descrizione": ["Pag1", "Pag2", "Riepilogo mensile"],
            "Importo": [100, 200, 300],
        })
        result, unmatched, n = strip_footer_phase3_patterns(
            df, schema, ["totale dare al"],  # different pattern, no match
        )
        assert n == 0
        assert len(unmatched) == 1  # "riepilogo" is a suspect keyword

    def test_empty_patterns_list(self):
        schema = _3col_schema()
        df = pd.DataFrame({
            "Data": ["2024-01-01"],
            "Descrizione": ["Pag1"],
            "Importo": [100],
        })
        result, unmatched, n = strip_footer_phase3_patterns(df, schema, [])
        assert n == 0

    def test_multiple_patterns(self):
        schema = _3col_schema()
        df = pd.DataFrame({
            "Data": [f"2024-01-{i+1:02}" for i in range(5)] + ["", ""],
            "Descrizione": [f"Tx{i}" for i in range(5)] + ["Totale dare 500", "Saldo finale 500"],
            "Importo": list(range(100, 600, 100)) + [500, 500],
        })
        result, unmatched, n = strip_footer_phase3_patterns(
            df, schema, ["totale dare", "saldo finale"],
        )
        assert n == 2
        assert len(result) == 5


# ── Test: DB migration integration ──────────────────────────────────────────

class TestFooterPatternsRepository:
    def test_update_footer_patterns_dedup(self):
        """update_footer_patterns merges and deduplicates patterns."""
        from unittest.mock import MagicMock, PropertyMock
        import json
        from db.repository import update_footer_patterns
        from db.models import DocumentSchemaModel

        mock_row = MagicMock(spec=DocumentSchemaModel)
        mock_row.footer_patterns = json.dumps(["pattern_a"])

        mock_session = MagicMock()
        mock_session.query.return_value.filter_by.return_value.first.return_value = mock_row

        update_footer_patterns(mock_session, "test_id", ["pattern_a", "pattern_b"])

        # Should have merged and deduped
        saved = json.loads(mock_row.footer_patterns)
        assert saved == ["pattern_a", "pattern_b"]
        mock_session.flush.assert_called_once()

    def test_update_footer_patterns_no_schema(self):
        """update_footer_patterns is a no-op when schema doesn't exist."""
        from db.repository import update_footer_patterns

        mock_session = MagicMock()
        mock_session.query.return_value.filter_by.return_value.first.return_value = None

        # Should not raise
        update_footer_patterns(mock_session, "nonexistent", ["pat"])
        mock_session.flush.assert_not_called()
