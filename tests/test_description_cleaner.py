"""Unit tests for core/description_cleaner.py — all LLM calls are mocked."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.description_cleaner import (
    _call_llm_batch,
    _process_group,
    _strip_non_text,
    clean_descriptions_batch,
)


# ─────────────────────────────────────────────────────────────────────────────
# _strip_non_text — pure function, no mocks needed
# ─────────────────────────────────────────────────────────────────────────────

class TestStripNonText:

    def test_plain_text_unchanged(self):
        assert _strip_non_text("Amazon Italy") == "Amazon Italy"

    def test_emoji_removed(self):
        result = _strip_non_text("Pagamento 🏬 supermercato")
        assert "🏬" not in result
        assert "supermercato" in result

    def test_euro_symbol_removed(self):
        result = _strip_non_text("Pagamento € 100,00")
        assert "€" not in result

    def test_multiple_spaces_collapsed(self):
        result = _strip_non_text("foo   bar   baz")
        assert result == "foo bar baz"

    def test_leading_trailing_stripped(self):
        result = _strip_non_text("  hello world  ")
        assert result == "hello world"

    def test_empty_string(self):
        assert _strip_non_text("") == ""

    def test_all_emoji_becomes_empty(self):
        assert _strip_non_text("🏬🛡️🏦") == ""

    def test_accented_characters_kept(self):
        result = _strip_non_text("Pagamento caffè")
        assert "caffè" in result

    def test_punctuation_kept(self):
        result = _strip_non_text("Mario Rossi, via Roma 1.")
        assert "," in result
        assert "." in result


# ─────────────────────────────────────────────────────────────────────────────
# _call_llm_batch — various LLM response scenarios
# ─────────────────────────────────────────────────────────────────────────────

class TestCallLlmBatch:

    def _call(self, descriptions, llm_result):
        backend = MagicMock()
        with patch("core.description_cleaner.call_with_fallback", return_value=llm_result):
            return _call_llm_batch(descriptions, "system", backend, None, "test", "expense")

    def test_success_returns_mapped_results(self):
        descs = ["desc1", "desc2"]
        result = self._call(descs, ({"results": ["Out1", "Out2"]}, "mock"))
        assert result == ["Out1", "Out2"]

    def test_llm_failure_returns_originals(self):
        """call_with_fallback returns (None, None) → fall back to original (lines 388-393)."""
        descs = ["desc1", "desc2"]
        result = self._call(descs, (None, None))
        assert result == descs

    def test_unexpected_response_type_returns_originals(self):
        """results is not a list → return originals (lines 396-401)."""
        descs = ["desc1", "desc2"]
        result = self._call(descs, ({"results": "not_a_list"}, "mock"))
        assert result == descs

    def test_short_results_padded_with_none(self):
        """LLM returns fewer results → missing ones fall back to originals (lines 403-410)."""
        descs = ["desc1", "desc2", "desc3"]
        # LLM returns only 2 instead of 3
        result = self._call(descs, ({"results": ["Out1", "Out2"]}, "mock"))
        assert len(result) == 3
        assert result[0] == "Out1"
        assert result[1] == "Out2"
        assert result[2] == "desc3"  # padded with original

    def test_null_result_entry_uses_original(self):
        """A None entry in results → maps to original description."""
        descs = ["desc1", "desc2"]
        result = self._call(descs, ({"results": [None, "Out2"]}, "mock"))
        assert result[0] == "desc1"
        assert result[1] == "Out2"


# ─────────────────────────────────────────────────────────────────────────────
# _process_group — batching logic
# ─────────────────────────────────────────────────────────────────────────────

class TestProcessGroup:

    def _make_txs(self, descs):
        return [{"description": d, "raw_description": d, "amount": "-10.00"} for d in descs]

    def test_updates_description_in_place(self):
        txs = self._make_txs(["Pagamento COOP supermercato Milano"])
        indices = [0]
        backend = MagicMock()
        with patch("core.description_cleaner.call_with_fallback",
                   return_value=({"results": ["COOP"]}, "mock")):
            count = _process_group(txs, indices, "system", backend, None, 30, "test", "expense")
        assert count == 1
        assert txs[0]["description"] == "COOP"

    def test_bad_llm_output_not_applied(self):
        """Known-bad outputs like 'null' are discarded (lines 355-360)."""
        txs = self._make_txs(["desc1"])
        indices = [0]
        backend = MagicMock()
        for bad_val in ["null", "none", "n/a", "nan", "-"]:
            txs[0]["description"] = "desc1"
            with patch("core.description_cleaner.call_with_fallback",
                       return_value=({"results": [bad_val]}, "mock")):
                _process_group(txs, indices, "sys", backend, None, 30, "test", "expense")
            assert txs[0]["description"] == "desc1"

    def test_single_char_result_not_applied(self):
        """Result with len < 2 is not applied."""
        txs = self._make_txs(["original desc"])
        indices = [0]
        backend = MagicMock()
        with patch("core.description_cleaner.call_with_fallback",
                   return_value=({"results": ["X"]}, "mock")):
            _process_group(txs, indices, "sys", backend, None, 30, "test", "expense")
        assert txs[0]["description"] == "original desc"

    def test_batching_splits_correctly(self):
        """With batch_size=2 and 5 items, creates 3 batches."""
        txs = self._make_txs([f"desc{i}" for i in range(5)])
        indices = list(range(5))
        backend = MagicMock()
        call_count = 0

        def fake_call(**kwargs):
            nonlocal call_count
            call_count += 1
            n = kwargs["user_prompt"].count('"desc')
            return {"results": [f"clean{i}" for i in range(n)]}, "mock"

        with patch("core.description_cleaner.call_with_fallback", side_effect=fake_call):
            _process_group(txs, indices, "sys", backend, None, 2, "test", "expense")
        assert call_count == 3  # ceil(5/2) = 3 batches

    def test_raw_description_preferred_over_description(self):
        """raw_description is used for LLM input when available."""
        txs = [{
            "description": "clean",
            "raw_description": "PAGAMENTO COOP S.C.A. MILANO",
            "amount": "-10.00",
        }]
        indices = [0]
        backend = MagicMock()
        captured = []

        def fake_call(**kwargs):
            captured.append(kwargs["user_prompt"])
            return {"results": ["COOP"]}, "mock"

        with patch("core.description_cleaner.call_with_fallback", side_effect=fake_call):
            _process_group(txs, indices, "sys", backend, None, 30, "test", "expense")
        assert "PAGAMENTO COOP" in captured[0]


# ─────────────────────────────────────────────────────────────────────────────
# clean_descriptions_batch — public API
# ─────────────────────────────────────────────────────────────────────────────

class TestCleanDescriptionsBatch:

    def test_empty_list_returned_unchanged(self):
        result = clean_descriptions_batch([], MagicMock())
        assert result == []

    def test_expense_and_income_split(self):
        """Expenses and income are processed in separate LLM passes."""
        txs = [
            {"description": "Spesa supermercato", "raw_description": "Spesa supermercato", "amount": "-20.00"},
            {"description": "Stipendio ACME", "raw_description": "Stipendio ACME", "amount": "1500.00"},
        ]
        backend = MagicMock()
        call_count = 0

        def fake_call(**kwargs):
            nonlocal call_count
            call_count += 1
            return {"results": ["Cleaned"]}, "mock"

        with patch("core.description_cleaner.call_with_fallback", side_effect=fake_call):
            result = clean_descriptions_batch(txs, backend)
        # Two passes → two LLM calls
        assert call_count == 2

    def test_invalid_amount_treated_as_income(self):
        """Transactions with unparseable amount go to income group."""
        txs = [{"description": "desc", "raw_description": "desc", "amount": "invalid"}]
        backend = MagicMock()
        with patch("core.description_cleaner.call_with_fallback",
                   return_value=({"results": ["Cleaned"]}, "mock")):
            result = clean_descriptions_batch(txs, backend)
        assert result[0]["description"] == "Cleaned"

    def test_all_expenses(self):
        txs = [
            {"description": f"Spesa {i}", "raw_description": f"Spesa {i}", "amount": f"-{i+1}.00"}
            for i in range(3)
        ]
        backend = MagicMock()
        with patch("core.description_cleaner.call_with_fallback",
                   return_value=({"results": [f"Clean{i}" for i in range(3)]}, "mock")):
            result = clean_descriptions_batch(txs, backend)
        assert result[0]["description"] == "Clean0"

    def test_all_income(self):
        txs = [
            {"description": "Stipendio", "raw_description": "Stipendio", "amount": "2000.00"},
        ]
        backend = MagicMock()
        with patch("core.description_cleaner.call_with_fallback",
                   return_value=({"results": ["ACME SRL"]}, "mock")):
            result = clean_descriptions_batch(txs, backend)
        assert result[0]["description"] == "ACME SRL"
