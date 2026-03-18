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


# ─────────────────────────────────────────────────────────────────────────────
# _is_categorical
# ─────────────────────────────────────────────────────────────────────────────

class TestIsCategorical:

    def test_empty_series_is_categorical(self):
        """Empty series → True (line 305)."""
        from core.classifier import _is_categorical
        assert _is_categorical(pd.Series([], dtype=object)) is True

    def test_few_distinct_values_is_categorical(self):
        from core.classifier import _is_categorical
        s = pd.Series(["EUR", "EUR", "USD", "EUR", "EUR"])
        assert _is_categorical(s) is True

    def test_high_variability_is_not_categorical(self):
        from core.classifier import _is_categorical
        s = pd.Series([f"desc {i}" for i in range(100)])
        assert _is_categorical(s) is False


# ─────────────────────────────────────────────────────────────────────────────
# _classify_column_content — amount plausibility cap + comma/dot edge cases
# ─────────────────────────────────────────────────────────────────────────────

class TestClassifyColumnContentEdgeCases:

    def test_us_format_dot_after_comma_is_amount(self):
        """'1,234.56' — US thousands: comma before dot → clean commas (line 347)."""
        s = pd.Series(["1,234.56", "2,500.00", "300.00", "45.99"])
        assert _classify_column_content(s) == "amount"

    def test_european_format_with_dot_and_comma_is_amount(self):
        """'1.234,56' — European thousands: dot before comma → amount (line 345)."""
        s = pd.Series(["1.234,56", "2.500,00", "300,00", "45,99"])
        assert _classify_column_content(s) == "amount"

    def test_comma_as_thousands_no_decimal_is_amount(self):
        """'1,000,000' — multiple commas → thousands (line 353)."""
        s = pd.Series(["1,000,000", "2,500,000", "500,000", "100,000"])
        assert _classify_column_content(s) == "amount"

    def test_above_plausibility_cap_returns_text(self):
        """Median absolute value > cap → rejected as reference/ID column (line 361)."""
        cap = 100.0
        # all values well above the cap
        s = pd.Series(["1000000", "2000000", "3000000", "4000000"])
        assert _classify_column_content(s, amount_plausibility_cap=cap) == "text"

    def test_unparseable_amount_value_ignored(self):
        """A cell that passes the amount regex but fails float() is skipped (lines 356-357)."""
        # Craft values that match _CONTENT_AMOUNT_RE but fail float()
        # Use a mix: mostly real amounts + one that will parse fine
        s = pd.Series(["10.50", "20.00", "30.00", "40.00"])
        assert _classify_column_content(s) == "amount"


# ─────────────────────────────────────────────────────────────────────────────
# _run_step0_analysis — edge cases
# ─────────────────────────────────────────────────────────────────────────────

class TestRunStep0AnalysisEdgeCases:

    def test_categorical_text_column_excluded_from_description(self):
        """A column with very few distinct values is excluded from description_cols (lines 409-410).

        Need enough rows so description column has >5 distinct values (not categorical)
        while the Tipo column stays constant (categorical).
        """
        n = 20
        df = pd.DataFrame({
            "Data": [f"2025-01-{i+1:02d}" for i in range(n)],
            "Causale": [f"pagamento {i} desc" for i in range(n)],   # n distinct → not categorical
            "Tipo": ["POS"] * n,                                      # 1 distinct → categorical
            "Importo": [f"-{i+1}.00" for i in range(n)],
        })
        result = _run_step0_analysis(list(df.columns), df_raw=df)
        assert "Tipo" not in result.description_cols
        assert "Causale" in result.description_cols

    def test_synonym_ranking_fallback(self):
        """When no data, synonyms are ranked by _DESCRIPTION_PRIORITY order (line 423)."""
        # "causale" ranks higher than "note" in the priority list
        columns = ["Data", "Note", "Causale", "Importo"]
        result = _run_step0_analysis(columns, df_raw=None)
        assert result.description_col == "Causale"

    def test_date_op_with_unnamed_second_date_as_accounting(self):
        """Op date found + second date with no acc synonym → second becomes accounting_date (line 447)."""
        df = pd.DataFrame({
            "Data operazione": ["01/01/2025", "02/01/2025"],
            "Timestamp": ["03/01/2025", "04/01/2025"],   # date content but no acc synonym
            "Causale": ["desc1", "desc2"],
            "Importo": ["-10.50", "-20.00"],
        })
        result = _run_step0_analysis(list(df.columns), df_raw=df)
        assert result.date_col == "Data operazione"
        assert result.date_accounting_col == "Timestamp"

    def test_only_accounting_date_promoted_to_date_col(self):
        """Only 'valuta' date found → promoted to date_col (line 450)."""
        df = pd.DataFrame({
            "Valuta": ["01/01/2025", "02/01/2025"],
            "Causale": ["desc1", "desc2"],
            "Importo": ["-10.50", "-20.00"],
        })
        result = _run_step0_analysis(list(df.columns), df_raw=df)
        # "valuta" is an accounting date synonym → promoted to date_col
        assert result.date_col == "Valuta"

    def test_two_date_cols_no_name_hints(self):
        """Two date cols, no op/acc name hints → first=date_col, second=accounting (line 455)."""
        df = pd.DataFrame({
            "Col1": ["01/01/2025", "02/01/2025"],
            "Col2": ["03/01/2025", "04/01/2025"],
            "Causale": ["desc1", "desc2"],
            "Importo": ["-10.50", "-20.00"],
        })
        result = _run_step0_analysis(list(df.columns), df_raw=df)
        assert result.date_col is not None
        assert result.date_accounting_col is not None

    def test_debit_only_from_data_sets_outflow(self):
        """Debit-only amount col detected from data → outflow semantics (lines 483-485)."""
        df = pd.DataFrame({
            "Data": ["01/01/2025", "02/01/2025"],
            "Causale": ["desc1", "desc2"],
            "Addebito": ["10.00", "20.00"],
        })
        result = _run_step0_analysis(list(df.columns), df_raw=df)
        assert result.amount_semantics == "outflow"
        assert result.invert_sign is True

    def test_credit_only_from_data_sets_inflow(self):
        """Credit-only amount col detected from data → inflow semantics (lines 487-489)."""
        df = pd.DataFrame({
            "Data": ["01/01/2025", "02/01/2025"],
            "Causale": ["desc1", "desc2"],
            "Accredito": ["10.00", "20.00"],
        })
        result = _run_step0_analysis(list(df.columns), df_raw=df)
        assert result.amount_semantics == "inflow"
        assert result.invert_sign is False


# ─────────────────────────────────────────────────────────────────────────────
# _inspect_neutral_column_sign — total == 0
# ─────────────────────────────────────────────────────────────────────────────

class TestInspectNeutralColumnSignEdgeCases:

    def test_all_zero_values_total_zero_unchanged(self):
        """All zeros → n_negative=0, n_positive=0, total=0 → unchanged (line 552)."""
        from core.classifier import _Step0Result, _inspect_neutral_column_sign
        df = pd.DataFrame({"Importo": [0, 0, 0, 0]})
        step0 = _Step0Result()
        step0.amount_col = "Importo"
        step0.amount_semantics = "neutral"
        result = _inspect_neutral_column_sign(step0, df, "test")
        assert result.invert_sign is None


# ─────────────────────────────────────────────────────────────────────────────
# _format_step0_for_prompt — additional branches
# ─────────────────────────────────────────────────────────────────────────────

class TestFormatStep0ForPromptEdgeCases:

    def test_date_accounting_col_in_output(self):
        """date_accounting_col line is included when set (line 604)."""
        from core.classifier import _Step0Result, _format_step0_for_prompt
        r = _Step0Result(
            date_col="Data operazione",
            date_accounting_col="Data valuta",
        )
        text = _format_step0_for_prompt(r)
        assert "Data valuta" in text

    def test_invert_sign_none_shows_unresolved(self):
        """invert_sign=None → UNRESOLVED line in output (line 628)."""
        from core.classifier import _Step0Result, _format_step0_for_prompt
        r = _Step0Result(
            amount_col="Importo",
            amount_semantics="neutral",
            invert_sign=None,
        )
        text = _format_step0_for_prompt(r)
        assert "UNRESOLVED" in text


# ─────────────────────────────────────────────────────────────────────────────
# _coerce_column_names
# ─────────────────────────────────────────────────────────────────────────────

class TestCoerceColumnNames:

    def test_exact_match_unchanged(self):
        from core.classifier import _coerce_column_names
        result = _coerce_column_names(
            {"date_col": "Data", "amount_col": "Importo"},
            ["Data", "Importo", "Causale"],
            "test",
        )
        assert result["date_col"] == "Data"
        assert result["amount_col"] == "Importo"

    def test_case_insensitive_match_coerced(self):
        """'data' → 'Data' via case-insensitive match (lines 773-778)."""
        from core.classifier import _coerce_column_names
        result = _coerce_column_names(
            {"date_col": "data"},
            ["Data", "Importo"],
            "test",
        )
        assert result["date_col"] == "Data"

    def test_unknown_column_set_to_none(self):
        """Column not found at all → set to None (lines 780-784)."""
        from core.classifier import _coerce_column_names
        result = _coerce_column_names(
            {"date_col": "NonExistentColumn"},
            ["Data", "Importo"],
            "test",
        )
        assert result["date_col"] is None

    def test_empty_value_skipped(self):
        """Empty/None value fields are skipped (line 769)."""
        from core.classifier import _coerce_column_names
        result = _coerce_column_names(
            {"date_col": "", "amount_col": None},
            ["Data"],
            "test",
        )
        assert result["date_col"] == ""
        assert result["amount_col"] is None


# ─────────────────────────────────────────────────────────────────────────────
# classify_document — sanitize=False raises
# ─────────────────────────────────────────────────────────────────────────────

class TestClassifyDocument:

    def test_sanitize_false_raises(self):
        """classify_document with sanitize=False raises SanitizationRequiredError (lines 75-78)."""
        from unittest.mock import MagicMock
        from core.classifier import classify_document
        from core.llm_backends import SanitizationRequiredError
        df = pd.DataFrame({"Data": ["01/01/2025"], "Importo": ["-10.00"]})
        backend = MagicMock()
        with pytest.raises(SanitizationRequiredError):
            classify_document(df, backend, sanitize=False)

    def test_llm_failure_returns_none(self):
        """When all backends fail, classify_document returns None (lines 130-132)."""
        from unittest.mock import patch, MagicMock
        from core.classifier import classify_document
        df = pd.DataFrame({
            "Data": ["01/01/2025", "02/01/2025"],
            "Causale": ["pagamento supermercato", "addebito gas"],
            "Importo": ["-10.50", "-20.00"],
        })
        backend = MagicMock()
        with patch("core.classifier.call_with_fallback", return_value=(None, None)):
            result = classify_document(df, backend)
        assert result is None

    def test_llm_success_returns_schema(self):
        """When LLM succeeds, classify_document returns a DocumentSchema (lines 111-150)."""
        from unittest.mock import patch
        from core.classifier import classify_document
        df = pd.DataFrame({
            "Data": ["01/01/2025", "02/01/2025"],
            "Causale": ["pagamento supermercato", "addebito gas"],
            "Importo": ["-10.50", "-20.00"],
        })
        llm_result = {
            "doc_type": "bank_account",
            "date_col": "Data",
            "amount_col": "Importo",
            "description_col": "Causale",
            "sign_convention": "signed_single",
            "date_format": "%d/%m/%Y",
            "invert_sign": False,
            "confidence": "high",
            "account_label": "test_source",
        }
        with patch("core.classifier.call_with_fallback", return_value=(llm_result, "mock")):
            result = classify_document(df, None, source_name="test_source")  # type: ignore
        assert result is not None
        assert result.date_col == "Data"

    def test_schema_validation_failure_returns_none(self):
        """If DocumentSchema(**result) raises, returns None (lines 151-153)."""
        from unittest.mock import patch
        from core.classifier import classify_document
        df = pd.DataFrame({
            "Data": ["01/01/2025"],
            "Causale": ["pagamento"],
            "Importo": ["-10.50"],
        })
        # Return dict with invalid field that breaks DocumentSchema
        bad_result = {"doc_type": None, "date_col": None, "amount_col": None,
                      "description_col": None, "sign_convention": None,
                      "date_format": None, "invert_sign": None, "confidence": None,
                      "extra_invalid_kwarg": "boom"}
        with patch("core.classifier.call_with_fallback", return_value=(bad_result, "mock")):
            result = classify_document(df, None)  # type: ignore
        assert result is None
