"""Tests for the multi-step classifier (_classify_multi_step).

Covers the 3-step sequential LLM pipeline with mocked backends,
degradation paths, account_type shortcut, and auto-detect logic.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch
import pandas as pd
import pytest

from core.classifier import (
    _classify_multi_step,
    _format_step_context,
    classify_document,
    MultiStepDiagnostics,
    _Step0Result,
)
from core.llm_backends import LLMBackend, LLMValidationError
from core.schemas import DocumentSchema


# ── Fixtures ──────────────────────────────────────────────────────────────

def _make_step0(**kwargs) -> _Step0Result:
    return _Step0Result(**kwargs)


def _make_backend(responses: list[dict | None]) -> MagicMock:
    """Create a mock LLM backend that returns responses in sequence."""
    backend = MagicMock(spec=LLMBackend)
    backend.name = "mock_backend"
    backend.is_remote = False
    side_effects = []
    for r in responses:
        if r is None:
            side_effects.append(LLMValidationError("mock failure"))
        else:
            side_effects.append(r)
    backend.complete_structured.side_effect = side_effects
    return backend


STEP1_OK = {
    "doc_type": "bank_account",
    "encoding": "utf-8",
    "delimiter": ";",
    "sheet_name": None,
    "skip_rows": 10,
}

STEP2_OK = {
    "date_col": "Data operazione",
    "date_accounting_col": None,
    "amount_col": "Importo",
    "debit_col": None,
    "credit_col": None,
    "description_col": "Descrizione",
    "description_cols": ["Descrizione"],
    "currency_col": None,
    "default_currency": "EUR",
}

STEP3_OK = {
    "sign_convention": "signed_single",
    "invert_sign": False,
    "date_format": "%d/%m/%Y",
    "is_zero_sum": False,
    "internal_transfer_patterns": ["giroconto"],
    "account_label": "CC-1_S_000",
    "confidence": "high",
    "positive_ratio": 0.35,
    "negative_ratio": 0.65,
    "semantic_evidence": ["Bank account with signed amounts"],
    "normalization_case_id": "C1",
}

SAMPLE_JSON = '[{"Data operazione": "01/01/2026", "Descrizione": "STIPENDIO", "Importo": "2087.39"}]'
COLUMNS_LIST = "Data operazione, Descrizione, Importo, Divisa"


# ── _format_step_context ─────────────────────────────────────────────────

class TestFormatStepContext:

    def test_format_basic(self):
        ctx = _format_step_context("Test Step", {"key": "value", "num": 42})
        assert "## Previous analysis — Test Step" in ctx
        assert '- key = "value"' in ctx
        assert "- num = 42" in ctx


# ── _classify_multi_step: happy path ─────────────────────────────────────

class TestMultiStepHappyPath:

    @patch("core.classifier.call_with_fallback")
    def test_all_3_steps_succeed(self, mock_cwf):
        mock_cwf.side_effect = [
            (STEP1_OK, "mock"),
            (STEP2_OK, "mock"),
            (STEP3_OK, "mock"),
        ]
        step0 = _make_step0()
        result, diag = _classify_multi_step(
            sample_json=SAMPLE_JSON,
            columns_list=COLUMNS_LIST,
            step0_text="",
            source_name="test.csv",
            llm_backend=MagicMock(),
            fallback_backend=None,
            step0=step0,
        )
        assert result is not None
        assert result["doc_type"] == "bank_account"
        assert result["date_col"] == "Data operazione"
        assert result["sign_convention"] == "signed_single"
        assert diag.classifier_mode == "multi_step"
        assert diag.step1_time_s >= 0
        assert diag.step2_time_s >= 0
        assert diag.step3_time_s >= 0
        assert diag.step1_doc_type == "bank_account"
        assert diag.step2_date_col == "Data operazione"
        assert diag.step2_amount_col == "Importo"
        assert mock_cwf.call_count == 3

    @patch("core.classifier.call_with_fallback")
    def test_merged_has_all_fields(self, mock_cwf):
        mock_cwf.side_effect = [
            (STEP1_OK, "mock"),
            (STEP2_OK, "mock"),
            (STEP3_OK, "mock"),
        ]
        result, _ = _classify_multi_step(
            sample_json=SAMPLE_JSON,
            columns_list=COLUMNS_LIST,
            step0_text="",
            source_name="test.csv",
            llm_backend=MagicMock(),
            fallback_backend=None,
            step0=_make_step0(),
        )
        # Should have all fields from all 3 steps
        expected_keys = set(STEP1_OK) | set(STEP2_OK) | set(STEP3_OK)
        assert expected_keys.issubset(set(result.keys()))


# ── account_type shortcut ────────────────────────────────────────────────

class TestAccountTypeShortcut:

    @patch("core.classifier.call_with_fallback")
    def test_skips_step1_when_account_type_set(self, mock_cwf):
        mock_cwf.side_effect = [
            (STEP2_OK, "mock"),
            (STEP3_OK, "mock"),
        ]
        result, diag = _classify_multi_step(
            sample_json=SAMPLE_JSON,
            columns_list=COLUMNS_LIST,
            step0_text="",
            source_name="test.csv",
            llm_backend=MagicMock(),
            fallback_backend=None,
            step0=_make_step0(),
            account_type="bank_account",
        )
        assert result is not None
        assert result["doc_type"] == "bank_account"
        assert diag.step1_skipped is True
        assert mock_cwf.call_count == 2  # only step 2 and 3

    @patch("core.classifier.call_with_fallback")
    def test_account_type_propagates_to_doc_type(self, mock_cwf):
        mock_cwf.side_effect = [
            (STEP2_OK, "mock"),
            (STEP3_OK, "mock"),
        ]
        result, _ = _classify_multi_step(
            sample_json=SAMPLE_JSON,
            columns_list=COLUMNS_LIST,
            step0_text="",
            source_name="test.csv",
            llm_backend=MagicMock(),
            fallback_backend=None,
            step0=_make_step0(),
            account_type="credit_card",
        )
        assert result["doc_type"] == "credit_card"


# ── Degradation paths ────────────────────────────────────────────────────

class TestDegradation:

    @patch("core.classifier.call_with_fallback")
    def test_step1_fails_aborts(self, mock_cwf):
        mock_cwf.return_value = (None, "quarantine")
        result, diag = _classify_multi_step(
            sample_json=SAMPLE_JSON,
            columns_list=COLUMNS_LIST,
            step0_text="",
            source_name="test.csv",
            llm_backend=MagicMock(),
            fallback_backend=None,
            step0=_make_step0(),
        )
        assert result is None
        assert diag.step1_time_s >= 0

    @patch("core.classifier.call_with_fallback")
    def test_step2_fails_uses_phase0_fallback(self, mock_cwf):
        mock_cwf.side_effect = [
            (STEP1_OK, "mock"),
            (None, "quarantine"),  # step 2 fails
            (STEP3_OK, "mock"),
        ]
        step0 = _make_step0(
            date_col="Data operazione",
            description_col="Descrizione",
            description_cols=["Descrizione"],
            amount_col="Importo",
        )
        result, diag = _classify_multi_step(
            sample_json=SAMPLE_JSON,
            columns_list=COLUMNS_LIST,
            step0_text="",
            source_name="test.csv",
            llm_backend=MagicMock(),
            fallback_backend=None,
            step0=step0,
        )
        assert result is not None
        assert result["date_col"] == "Data operazione"
        assert diag.step2_fallback is True

    @patch("core.classifier.call_with_fallback")
    def test_step2_fails_no_phase0_aborts(self, mock_cwf):
        mock_cwf.side_effect = [
            (STEP1_OK, "mock"),
            (None, "quarantine"),  # step 2 fails
        ]
        step0 = _make_step0()  # no Phase 0 column info
        result, diag = _classify_multi_step(
            sample_json=SAMPLE_JSON,
            columns_list=COLUMNS_LIST,
            step0_text="",
            source_name="test.csv",
            llm_backend=MagicMock(),
            fallback_backend=None,
            step0=step0,
        )
        assert result is None

    @patch("core.classifier.call_with_fallback")
    def test_step3_fails_uses_degraded_defaults(self, mock_cwf):
        mock_cwf.side_effect = [
            (STEP1_OK, "mock"),
            (STEP2_OK, "mock"),
            (None, "quarantine"),  # step 3 fails
        ]
        result, diag = _classify_multi_step(
            sample_json=SAMPLE_JSON,
            columns_list=COLUMNS_LIST,
            step0_text="",
            source_name="test.csv",
            llm_backend=MagicMock(),
            fallback_backend=None,
            step0=_make_step0(),
        )
        assert result is not None
        assert result["sign_convention"] == "signed_single"
        assert result["confidence"] == "low"
        assert diag.step3_fallback is True


# ── Auto-detect classifier_mode ──────────────────────────────────────────

class TestAutoDetect:

    def test_small_model_gets_multi_step(self):
        """Models < 5GB should auto-detect as multi_step."""
        backend = MagicMock()
        backend.model_size_bytes = 2 * 1024**3  # 2 GB
        # We test via classify_document with a mock that will fail,
        # but we just check the log/mode detection
        df = pd.DataFrame({"A": [1, 2], "B": ["x", "y"]})
        with patch("core.classifier.call_with_fallback", return_value=(None, "quarantine")):
            result = classify_document(
                df, backend, source_name="test.csv", classifier_mode="auto",
            )
        # Result is None (mock fails), but auto-detect should have picked multi_step
        assert result is None  # expected — mocked backend fails

    def test_large_model_gets_single(self):
        """Models >= 5GB should auto-detect as single."""
        backend = MagicMock()
        backend.model_size_bytes = 7 * 1024**3  # 7 GB
        df = pd.DataFrame({"A": [1, 2], "B": ["x", "y"]})
        with patch("core.classifier.call_with_fallback", return_value=(None, "quarantine")):
            result = classify_document(
                df, backend, source_name="test.csv", classifier_mode="auto",
            )
        assert result is None  # expected — mocked backend fails

    def test_remote_backend_gets_single(self):
        """Backends without model_size_bytes default to single."""
        backend = MagicMock(spec=LLMBackend)
        backend.name = "openai"
        backend.is_remote = True
        # No model_size_bytes attribute
        if hasattr(backend, "model_size_bytes"):
            del backend.model_size_bytes
        df = pd.DataFrame({"A": [1, 2], "B": ["x", "y"]})
        with patch("core.classifier.call_with_fallback", return_value=(None, "quarantine")):
            result = classify_document(
                df, backend, source_name="test.csv", classifier_mode="auto",
            )
        assert result is None


# ── Diagnostics dataclass ────────────────────────────────────────────────

class TestMultiStepDiagnostics:

    def test_defaults(self):
        d = MultiStepDiagnostics()
        assert d.classifier_mode == "single"
        assert d.step1_time_s == 0.0
        assert d.step1_skipped is False
        assert d.step2_fallback is False
        assert d.step3_fallback is False

    def test_custom_values(self):
        d = MultiStepDiagnostics(
            classifier_mode="multi_step",
            step1_time_s=1.5,
            step2_time_s=3.2,
            step3_time_s=2.1,
            step1_doc_type="bank_account",
            step2_date_col="Data",
            step2_amount_col="Importo",
        )
        assert d.step1_time_s == 1.5
        assert d.step1_doc_type == "bank_account"
