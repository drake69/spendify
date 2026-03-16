"""Tests for classifier.py Phase 0 (deterministic pre-LLM column analysis).

Covers _run_step0_analysis, _classify_column_content, _inspect_neutral_column_sign
and the helper functions, without invoking any LLM backend.
"""
from __future__ import annotations

import pandas as pd
import pytest

from core.classifier import (
    _classify_column_content,
    _inspect_neutral_column_sign,
    _run_step0_analysis,
    _Step0Result,
    _format_step0_for_prompt,
    _merge_step0_into_result,
    _apply_step0_invert_sign,
)


# ─────────────────────────────────────────────────────────────────────────────
# _classify_column_content
# ─────────────────────────────────────────────────────────────────────────────

class TestClassifyColumnContent:

    def _series(self, values) -> pd.Series:
        return pd.Series(values)

    def test_date_column_detected(self):
        s = self._series(["01/01/2025", "15/03/2025", "31/12/2024"])
        assert _classify_column_content(s) == "date"

    def test_date_with_dashes_detected(self):
        s = self._series(["2025-01-01", "2025-03-15", "2024-12-31"])
        assert _classify_column_content(s) == "date"

    def test_amount_column_detected(self):
        s = self._series(["-10.50", "200.00", "-3.99", "1500.00"])
        assert _classify_column_content(s) == "amount"

    def test_amount_with_comma_decimal(self):
        s = self._series(["10,50", "200,00", "-3,99"])
        assert _classify_column_content(s) == "amount"

    def test_amount_with_thousands_separator(self):
        s = self._series(["1.500,00", "2.300,50", "100,00"])
        assert _classify_column_content(s) == "amount"

    def test_text_column_detected(self):
        s = self._series([
            "Pagamento supermercato",
            "Bonifico stipendio",
            "Addebito utenza gas",
        ])
        assert _classify_column_content(s) == "text"

    def test_empty_series_returns_text(self):
        s = self._series([None, None, None])
        assert _classify_column_content(s) == "text"

    def test_mixed_mostly_dates_returns_date(self):
        # 80% dates, 20% noise → should be classified as date
        s = self._series(["01/01/2025"] * 8 + ["testo", None])
        assert _classify_column_content(s) == "date"

    def test_mixed_mostly_text_returns_text(self):
        s = self._series(["testo", "altra desc", "bonifico", "01/01/2025"])
        assert _classify_column_content(s) == "text"


# ─────────────────────────────────────────────────────────────────────────────
# _run_step0_analysis — description column detection
# ─────────────────────────────────────────────────────────────────────────────

class TestRunStep0AnalysisDescription:

    def _make_df(self, col_data: dict) -> pd.DataFrame:
        return pd.DataFrame(col_data)

    def test_text_columns_assigned_as_description(self):
        df = self._make_df({
            "Data": ["01/01/2025", "02/01/2025"],
            "Causale": ["pagamento supermercato", "addebito gas"],
            "Importo": ["-10.50", "-20.00"],
        })
        result = _run_step0_analysis(list(df.columns), df_raw=df)
        assert result.description_col == "Causale"
        assert "Causale" in result.description_cols

    def test_multiple_text_columns_all_in_description_cols(self):
        df = self._make_df({
            "Data": ["01/01/2025", "02/01/2025"],
            "Causale": ["pagamento supermercato", "addebito gas"],
            "Note": ["nota1", "nota2"],
            "Importo": ["-10.50", "-20.00"],
        })
        result = _run_step0_analysis(list(df.columns), df_raw=df)
        assert len(result.description_cols) >= 2
        assert "Causale" in result.description_cols
        assert "Note" in result.description_cols

    def test_fallback_to_synonym_when_no_data(self):
        """Without df_raw, falls back to column-name synonym matching."""
        columns = ["Data operazione", "Causale", "Importo"]
        result = _run_step0_analysis(columns, df_raw=None)
        assert result.description_col is not None

    def test_synonym_causale_resolved(self):
        columns = ["Data", "Causale", "Importo"]
        result = _run_step0_analysis(columns, df_raw=None)
        assert result.description_col == "Causale"

    def test_synonym_descrizione_resolved(self):
        columns = ["Data", "Descrizione", "Amount"]
        result = _run_step0_analysis(columns, df_raw=None)
        assert result.description_col == "Descrizione"


# ─────────────────────────────────────────────────────────────────────────────
# _run_step0_analysis — date column detection
# ─────────────────────────────────────────────────────────────────────────────

class TestRunStep0AnalysisDate:

    def test_operation_date_detected(self):
        df = pd.DataFrame({
            "Data operazione": ["01/01/2025", "02/01/2025"],
            "Causale": ["desc1", "desc2"],
            "Importo": ["-10.50", "-20.00"],
        })
        result = _run_step0_analysis(list(df.columns), df_raw=df)
        assert result.date_col == "Data operazione"

    def test_both_date_columns_detected(self):
        df = pd.DataFrame({
            "Data operazione": ["01/01/2025", "02/01/2025"],
            "Data valuta": ["03/01/2025", "04/01/2025"],
            "Causale": ["desc1", "desc2"],
            "Importo": ["-10.50", "-20.00"],
        })
        result = _run_step0_analysis(list(df.columns), df_raw=df)
        assert result.date_col == "Data operazione"
        assert result.date_accounting_col == "Data valuta"

    def test_fallback_date_synonym_without_data(self):
        columns = ["Data operazione", "Causale", "Importo"]
        result = _run_step0_analysis(columns, df_raw=None)
        assert result.date_col == "Data operazione"

    def test_transaction_date_english_synonym(self):
        columns = ["Transaction date", "Description", "Amount"]
        result = _run_step0_analysis(columns, df_raw=None)
        assert result.date_col == "Transaction date"

    def test_value_date_english_as_accounting(self):
        columns = ["Transaction date", "Value date", "Description", "Amount"]
        result = _run_step0_analysis(columns, df_raw=None)
        assert result.date_accounting_col == "Value date"


# ─────────────────────────────────────────────────────────────────────────────
# _run_step0_analysis — amount / sign detection
# ─────────────────────────────────────────────────────────────────────────────

class TestRunStep0AnalysisAmount:

    def test_single_neutral_amount_column(self):
        df = pd.DataFrame({
            "Data": ["01/01/2025"],
            "Causale": ["pagamento"],
            "Importo": ["-10.50"],
        })
        result = _run_step0_analysis(list(df.columns), df_raw=df)
        assert result.amount_col == "Importo"

    def test_debit_column_name_sets_outflow_semantics(self):
        columns = ["Data", "Causale", "Addebito"]
        result = _run_step0_analysis(columns, df_raw=None)
        assert result.amount_col == "Addebito"
        assert result.amount_semantics == "outflow"
        assert result.invert_sign is True

    def test_credit_column_name_sets_inflow_semantics(self):
        columns = ["Data", "Causale", "Accredito"]
        result = _run_step0_analysis(columns, df_raw=None)
        assert result.amount_col == "Accredito"
        assert result.amount_semantics == "inflow"
        assert result.invert_sign is False

    def test_debit_and_credit_columns_sets_debit_positive(self):
        df = pd.DataFrame({
            "Data": ["01/01/2025", "02/01/2025"],
            "Causale": ["desc1", "desc2"],
            "Addebito": ["10.00", ""],
            "Accredito": ["", "20.00"],
        })
        result = _run_step0_analysis(list(df.columns), df_raw=df)
        assert result.amount_semantics == "debit_positive"
        assert result.debit_col is not None
        assert result.credit_col is not None

    def test_english_debit_column(self):
        columns = ["Date", "Description", "Debit", "Credit"]
        result = _run_step0_analysis(columns, df_raw=None)
        assert result.amount_semantics == "debit_positive"

    def test_neutral_amount_column_synonym(self):
        columns = ["Date", "Description", "Amount"]
        result = _run_step0_analysis(columns, df_raw=None)
        assert result.amount_semantics == "neutral"
        assert result.amount_col == "Amount"


# ─────────────────────────────────────────────────────────────────────────────
# _inspect_neutral_column_sign
# ─────────────────────────────────────────────────────────────────────────────

class TestInspectNeutralColumnSign:

    def _step0(self, col: str = "Importo") -> _Step0Result:
        r = _Step0Result()
        r.amount_col = col
        r.amount_semantics = "neutral"
        return r

    def test_majority_negative_sets_invert_sign_false(self):
        df = pd.DataFrame({"Importo": [-10, -20, -5, -15, 30]})
        step0 = self._step0()
        result = _inspect_neutral_column_sign(step0, df, "test")
        assert result.invert_sign is False
        assert result.amount_semantics == "signed_neutral"

    def test_majority_positive_leaves_unresolved(self):
        df = pd.DataFrame({"Importo": [10, 20, 5, 15, -2]})
        step0 = self._step0()
        result = _inspect_neutral_column_sign(step0, df, "test")
        assert result.invert_sign is None   # LLM must decide

    def test_all_positive_leaves_unresolved(self):
        df = pd.DataFrame({"Importo": [10, 20, 30, 40]})
        step0 = self._step0()
        result = _inspect_neutral_column_sign(step0, df, "test")
        assert result.invert_sign is None

    def test_missing_column_returns_unchanged(self):
        df = pd.DataFrame({"AltroCol": [1, 2, 3]})
        step0 = self._step0("Importo")   # col not in df
        result = _inspect_neutral_column_sign(step0, df, "test")
        assert result.invert_sign is None

    def test_empty_column_returns_unchanged(self):
        df = pd.DataFrame({"Importo": [None, None, None]})
        step0 = self._step0()
        result = _inspect_neutral_column_sign(step0, df, "test")
        assert result.invert_sign is None

    def test_amounts_with_currency_symbol_parsed(self):
        """Values like '€10,50' or '-€5,00' should be parsed correctly."""
        df = pd.DataFrame({"Importo": ["-10,50", "-20,00", "-5,00", "30,00"]})
        step0 = self._step0()
        result = _inspect_neutral_column_sign(step0, df, "test")
        # 3 negatives out of 4 → majority negative
        assert result.invert_sign is False


# ─────────────────────────────────────────────────────────────────────────────
# _format_step0_for_prompt
# ─────────────────────────────────────────────────────────────────────────────

class TestFormatStep0ForPrompt:

    def test_resolved_fields_marked_resolved(self):
        r = _Step0Result(
            description_col="Causale",
            description_cols=["Causale"],
            date_col="Data operazione",
            amount_col="Importo",
            amount_semantics="outflow",
            invert_sign=True,
        )
        text = _format_step0_for_prompt(r)
        assert "[RESOLVED]" in text
        assert "Causale" in text
        assert "Data operazione" in text

    def test_unresolved_fields_marked_unresolved(self):
        r = _Step0Result()   # nothing resolved
        text = _format_step0_for_prompt(r)
        assert "UNRESOLVED" in text

    def test_debit_positive_convention_in_output(self):
        r = _Step0Result(
            amount_semantics="debit_positive",
            debit_col="Uscita",
            credit_col="Entrata",
        )
        text = _format_step0_for_prompt(r)
        assert "debit_positive" in text
        assert "Uscita" in text
        assert "Entrata" in text


# ─────────────────────────────────────────────────────────────────────────────
# _merge_step0_into_result
# ─────────────────────────────────────────────────────────────────────────────

class TestMergeStep0IntoResult:

    def _base_result(self) -> dict:
        return {
            "doc_type": "bank_account",
            "date_col": "",
            "amount_col": "",
            "description_col": "",
            "sign_convention": "signed_single",
            "date_format": "%d/%m/%Y",
            "invert_sign": None,
        }

    def test_description_col_overrides_llm(self):
        step0 = _Step0Result(description_col="Causale", description_cols=["Causale"])
        result = _merge_step0_into_result(
            {"description_col": "WrongCol", **self._base_result()}, step0, "test"
        )
        assert result["description_col"] == "Causale"

    def test_date_col_filled_when_llm_empty(self):
        step0 = _Step0Result(date_col="Data operazione")
        base = self._base_result()
        base["date_col"] = ""
        result = _merge_step0_into_result(base, step0, "test")
        assert result["date_col"] == "Data operazione"

    def test_date_col_not_overridden_when_llm_filled(self):
        step0 = _Step0Result(date_col="Data operazione")
        base = self._base_result()
        base["date_col"] = "TransactionDate"  # LLM found something different
        result = _merge_step0_into_result(base, step0, "test")
        # date_col: LLM wins when already set
        assert result["date_col"] == "TransactionDate"

    def test_invert_sign_overridden_when_resolved(self):
        step0 = _Step0Result(
            amount_col="Importo",
            amount_semantics="outflow",
            invert_sign=True,
        )
        base = self._base_result()
        base["invert_sign"] = False
        result = _merge_step0_into_result(base, step0, "test")
        assert result["invert_sign"] is True

    def test_debit_positive_convention_set(self):
        step0 = _Step0Result(
            amount_semantics="debit_positive",
            debit_col="Uscita",
            credit_col="Entrata",
        )
        result = _merge_step0_into_result(self._base_result(), step0, "test")
        assert result["sign_convention"] == "debit_positive"
        assert result["debit_col"] == "Uscita"
        assert result["credit_col"] == "Entrata"

    def test_currency_col_cleared_if_same_as_accounting_date(self):
        """If the LLM assigned the accounting date column as currency_col, clear it."""
        step0 = _Step0Result(date_accounting_col="Valuta")
        base = self._base_result()
        base["currency_col"] = "Valuta"
        result = _merge_step0_into_result(base, step0, "test")
        assert result.get("currency_col") is None


# ─────────────────────────────────────────────────────────────────────────────
# _apply_step0_invert_sign (post-merge safety net)
# ─────────────────────────────────────────────────────────────────────────────

class TestApplyStep0InvertSign:

    def test_credit_card_forces_invert_sign_true(self):
        result = _apply_step0_invert_sign(
            {"doc_type": "credit_card", "sign_convention": "signed_single",
             "amount_col": "Importo", "invert_sign": False},
            "test"
        )
        assert result["invert_sign"] is True

    def test_credit_card_already_true_unchanged(self):
        result = _apply_step0_invert_sign(
            {"doc_type": "credit_card", "sign_convention": "signed_single",
             "amount_col": "Importo", "invert_sign": True},
            "test"
        )
        assert result["invert_sign"] is True

    def test_outflow_column_not_bank_forces_invert_true(self):
        result = _apply_step0_invert_sign(
            {"doc_type": "unknown", "sign_convention": "signed_single",
             "amount_col": "Addebito", "invert_sign": None},
            "test"
        )
        assert result["invert_sign"] is True

    def test_outflow_column_bank_account_not_overridden(self):
        """For bank_account doc_type, outflow column name should NOT force invert."""
        result = _apply_step0_invert_sign(
            {"doc_type": "bank_account", "sign_convention": "signed_single",
             "amount_col": "Addebito", "invert_sign": None},
            "test"
        )
        # bank_account is excluded from the outflow rule
        assert result["invert_sign"] is None

    def test_inflow_column_forces_invert_false(self):
        result = _apply_step0_invert_sign(
            {"doc_type": "unknown", "sign_convention": "signed_single",
             "amount_col": "Accredito", "invert_sign": True},
            "test"
        )
        assert result["invert_sign"] is False

    def test_debit_positive_convention_skipped(self):
        """Safety net only applies to signed_single convention."""
        result = _apply_step0_invert_sign(
            {"doc_type": "credit_card", "sign_convention": "debit_positive",
             "amount_col": "Importo", "invert_sign": None},
            "test"
        )
        # debit_positive → safety net is skipped
        assert result["invert_sign"] is None

    def test_neutral_amount_col_no_change(self):
        result = _apply_step0_invert_sign(
            {"doc_type": "bank_account", "sign_convention": "signed_single",
             "amount_col": "Importo", "invert_sign": None},
            "test"
        )
        # "Importo" is neutral → no deterministic rule applies
        assert result["invert_sign"] is None
