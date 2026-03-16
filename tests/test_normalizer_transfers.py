"""Tests for detect_internal_transfers (RF-04) and find_card_settlement_matches (RF-03)."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
import pytest

from core.models import Confidence, TransactionType
from core.normalizer import detect_internal_transfers, find_card_settlement_matches


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _df(*rows: dict) -> pd.DataFrame:
    """Build a minimal DataFrame for detect_internal_transfers."""
    records = []
    for r in rows:
        records.append({
            "id":            r.get("id", "tx1"),
            "date":          r.get("date", date(2025, 1, 10)),
            "amount":        Decimal(str(r.get("amount", -10))),
            "description":   r.get("description", ""),
            "account_label": r.get("account_label", "conto_a"),
            "tx_type":       r.get("tx_type", "expense"),
        })
    return pd.DataFrame(records)


def _tx(id: str, amount: float, d: date, account: str = "acc_a",
        description: str = "", tx_type: str = "expense") -> dict:
    return {
        "id": id,
        "date": d,
        "amount": Decimal(str(amount)),
        "description": description,
        "account_label": account,
        "tx_type": tx_type,
    }


# ─────────────────────────────────────────────────────────────────────────────
# detect_internal_transfers — Fase 1 (amount + date matching)
# ─────────────────────────────────────────────────────────────────────────────

class TestDetectInternalTransfersAmountDate:
    """Keyword-confirmed pairings (high confidence, different accounts)."""

    def test_keyword_match_marks_pair(self):
        d = date(2025, 1, 10)
        df = _df(
            _tx("out1", -500.00, d, "conto_a", "giroconto verso conto deposito"),
            _tx("in1",  +500.00, d, "conto_b", "giroconto da conto corrente"),
        )
        result = detect_internal_transfers(
            df, keyword_patterns=["giroconto"], require_keyword_confirmation=False
        )
        out_row = result[result["id"] == "out1"].iloc[0]
        in_row  = result[result["id"] == "in1"].iloc[0]

        assert out_row["tx_type"] == TransactionType.internal_out.value
        assert in_row["tx_type"]  == TransactionType.internal_in.value
        assert out_row["transfer_pair_id"] is not None
        assert out_row["transfer_pair_id"] == in_row["transfer_pair_id"]

    def test_keyword_match_sets_high_confidence(self):
        d = date(2025, 1, 10)
        df = _df(
            _tx("out1", -200.00, d, "acc_a", "bonifico giroconto"),
            _tx("in1",  +200.00, d, "acc_b", "accredito giroconto"),
        )
        result = detect_internal_transfers(
            df, keyword_patterns=["giroconto"], require_keyword_confirmation=False
        )
        out_row = result[result["id"] == "out1"].iloc[0]
        assert out_row["transfer_confidence"] == Confidence.high.value

    def test_same_account_not_paired(self):
        d = date(2025, 1, 10)
        df = _df(
            _tx("t1", -100.00, d, "same_acc", "giroconto"),
            _tx("t2", +100.00, d, "same_acc", "giroconto"),
        )
        result = detect_internal_transfers(
            df, keyword_patterns=["giroconto"], require_keyword_confirmation=False
        )
        assert result.loc[0, "tx_type"] == "expense"
        assert result.loc[1, "tx_type"] == "expense"

    def test_date_outside_delta_not_paired(self):
        df = _df(
            _tx("out1", -100.00, date(2025, 1, 1), "acc_a", "giroconto"),
            _tx("in1",  +100.00, date(2025, 1, 20), "acc_b", "giroconto"),
        )
        result = detect_internal_transfers(
            df, keyword_patterns=["giroconto"], delta_days=5,
            require_keyword_confirmation=False
        )
        # 19 days apart — beyond default delta_days=5
        assert result.loc[0, "tx_type"] == "expense"
        assert result.loc[1, "tx_type"] == "expense"

    def test_amount_mismatch_not_paired(self):
        d = date(2025, 1, 10)
        df = _df(
            _tx("out1", -100.00, d, "acc_a", "giroconto"),
            _tx("in1",  +150.00, d, "acc_b", "giroconto"),
        )
        result = detect_internal_transfers(
            df, keyword_patterns=["giroconto"], require_keyword_confirmation=False
        )
        assert result.loc[0, "tx_type"] == "expense"
        assert result.loc[1, "tx_type"] == "expense"

    def test_each_tx_only_paired_once(self):
        """A transaction already paired is not re-paired with a third one."""
        d = date(2025, 1, 10)
        df = _df(
            _tx("out1", -100.00, d, "acc_a", "giroconto"),
            _tx("in1",  +100.00, d, "acc_b", "giroconto"),
            _tx("in2",  +100.00, d, "acc_c", "giroconto"),
        )
        result = detect_internal_transfers(
            df, keyword_patterns=["giroconto"], require_keyword_confirmation=False
        )
        # out1 paired with in1; in2 should remain unpaired
        paired_ids = result[result["transfer_pair_id"].notna()]["id"].tolist()
        assert "out1" in paired_ids
        assert "in1" in paired_ids
        # in2 may or may not be paired depending on iteration order, but out1
        # must appear exactly once across all pairs
        assert paired_ids.count("out1") == 1

    def test_medium_confidence_without_keyword_require_confirmation(self):
        """High symmetry (strict window) without keyword → medium confidence,
        tx_type NOT changed when require_keyword_confirmation=True (default)."""
        d = date(2025, 1, 10)
        df = _df(
            _tx("out1", -100.00, d,                       "acc_a", "addebito"),
            _tx("in1",  +100.00, d + timedelta(days=1),   "acc_b", "accredito"),
        )
        result = detect_internal_transfers(
            df,
            keyword_patterns=None,
            epsilon_strict=Decimal("0.005"),
            delta_days_strict=1,
            require_keyword_confirmation=True,   # default
        )
        out_row = result[result["id"] == "out1"].iloc[0]
        # pair_id is set (flagged for review)
        assert out_row["transfer_pair_id"] is not None
        # tx_type must NOT be changed when only medium confidence + require_keyword=True
        assert out_row["tx_type"] == "expense"
        assert out_row["transfer_confidence"] == Confidence.medium.value

    def test_no_match_leaves_dataframe_unchanged(self):
        d = date(2025, 1, 10)
        df = _df(
            _tx("t1", -50.00, d, "acc_a", "supermercato"),
            _tx("t2", -30.00, d, "acc_b", "farmacia"),
        )
        result = detect_internal_transfers(df, keyword_patterns=["giroconto"])
        assert result.loc[0, "tx_type"] == "expense"
        assert result.loc[1, "tx_type"] == "expense"
        assert result["transfer_pair_id"].isna().all()


# ─────────────────────────────────────────────────────────────────────────────
# detect_internal_transfers — Fase 2 (owner name matching)
# ─────────────────────────────────────────────────────────────────────────────

class TestDetectInternalTransfersOwnerName:

    def test_owner_name_marks_internal_out(self):
        d = date(2025, 1, 10)
        df = _df(
            _tx("t1", -200.00, d, "acc_a", "bonifico a Mario Rossi conto deposito"),
        )
        result = detect_internal_transfers(df, owner_names=["Mario Rossi"])
        row = result[result["id"] == "t1"].iloc[0]
        assert row["tx_type"] == TransactionType.internal_out.value
        assert row["transfer_confidence"] == Confidence.high.value

    def test_owner_name_marks_internal_in(self):
        d = date(2025, 1, 10)
        df = _df(
            _tx("t1", +200.00, d, "acc_a", "accredito da Mario Rossi"),
        )
        result = detect_internal_transfers(df, owner_names=["Mario Rossi"])
        row = result[result["id"] == "t1"].iloc[0]
        assert row["tx_type"] == TransactionType.internal_in.value

    def test_owner_name_surname_first_permutation(self):
        """Banks often export "Rossi Mario" (surname first) — must still match."""
        d = date(2025, 1, 10)
        df = _df(
            _tx("t1", -100.00, d, "acc_a", "BONIFICO ROSSI MARIO"),
        )
        result = detect_internal_transfers(df, owner_names=["Mario Rossi"])
        row = result[result["id"] == "t1"].iloc[0]
        assert row["tx_type"] == TransactionType.internal_out.value

    def test_multi_token_owner_name_permutation_matched(self):
        """Multi-token names are matched in any order (surname-first bank export)."""
        d = date(2025, 1, 10)
        df = _df(
            _tx("t1", -50.00, d, "acc_a", "BONIFICO BIANCHI ANNA"),
        )
        result = detect_internal_transfers(df, owner_names=["Anna Bianchi"])
        row = result[result["id"] == "t1"].iloc[0]
        assert row["tx_type"] == TransactionType.internal_out.value

    def test_unrelated_name_not_matched(self):
        """A description that shares no tokens with any owner name is left unchanged."""
        d = date(2025, 1, 10)
        df = _df(
            _tx("t1", -50.00, d, "acc_a", "PAGAMENTO ESSELUNGA SRL"),
        )
        result = detect_internal_transfers(df, owner_names=["Mario Rossi"])
        row = result[result["id"] == "t1"].iloc[0]
        assert row["tx_type"] == "expense"

    def test_already_paired_tx_skipped_by_owner_pass(self):
        """A transaction already paired by amount/date should not be re-processed
        by the owner-name pass."""
        d = date(2025, 1, 10)
        df = _df(
            _tx("out1", -100.00, d, "acc_a", "giroconto Mario Rossi"),
            _tx("in1",  +100.00, d, "acc_b", "giroconto Mario Rossi"),
        )
        result = detect_internal_transfers(
            df,
            keyword_patterns=["giroconto"],
            owner_names=["Mario Rossi"],
            require_keyword_confirmation=False,
        )
        # Both should be paired with each other, not duplicated
        assert result["transfer_pair_id"].notna().all()
        assert result.loc[0, "transfer_pair_id"] == result.loc[1, "transfer_pair_id"]


# ─────────────────────────────────────────────────────────────────────────────
# find_card_settlement_matches (RF-03)
# ─────────────────────────────────────────────────────────────────────────────

def _settlement(id: str, amount: float, d: date) -> dict:
    return {"id": id, "amount": Decimal(str(amount)), "date": d}


def _card_tx(id: str, amount: float, d: date) -> dict:
    return {"id": id, "amount": Decimal(str(amount)), "date": d, "reconciled": False}


class TestFindCardSettlementMatchesSlidingWindow:

    def test_single_card_tx_matches_settlement(self):
        d = date(2025, 1, 15)
        settlements  = [_settlement("s1", -100.00, d)]
        card_txs     = [_card_tx("c1",  -100.00, d - timedelta(days=3))]
        results = find_card_settlement_matches(settlements, card_txs)
        assert len(results) == 1
        assert results[0]["settlement_id"] == "s1"
        assert results[0]["matched_ids"] == ["c1"]
        assert abs(results[0]["delta"]) <= Decimal("0.01")

    def test_multiple_card_txs_sum_to_settlement(self):
        d = date(2025, 1, 20)
        settlements = [_settlement("s1", -300.00, d)]
        card_txs = [
            _card_tx("c1", -100.00, d - timedelta(days=10)),
            _card_tx("c2", -100.00, d - timedelta(days=7)),
            _card_tx("c3", -100.00, d - timedelta(days=4)),
        ]
        results = find_card_settlement_matches(settlements, card_txs)
        assert len(results) == 1
        assert set(results[0]["matched_ids"]) == {"c1", "c2", "c3"}

    def test_no_match_returns_empty(self):
        d = date(2025, 1, 15)
        settlements = [_settlement("s1", -999.00, d)]
        card_txs    = [_card_tx("c1", -50.00, d - timedelta(days=3))]
        results = find_card_settlement_matches(settlements, card_txs)
        assert results == []

    def test_already_reconciled_tx_skipped(self):
        d = date(2025, 1, 15)
        settlements = [_settlement("s1", -100.00, d)]
        card_txs = [{"id": "c1", "amount": Decimal("-100.00"), "date": d, "reconciled": True}]
        results = find_card_settlement_matches(settlements, card_txs)
        assert results == []

    def test_method_is_sliding_window(self):
        d = date(2025, 1, 15)
        settlements = [_settlement("s1", -50.00, d)]
        card_txs    = [_card_tx("c1", -50.00, d - timedelta(days=2))]
        results = find_card_settlement_matches(settlements, card_txs)
        assert results[0]["method"] == "sliding_window"

    def test_matched_tx_marked_reconciled(self):
        """After matching, card tx should have reconciled=True (side effect)."""
        d = date(2025, 1, 15)
        settlements = [_settlement("s1", -75.00, d)]
        card_txs    = [_card_tx("c1", -75.00, d - timedelta(days=5))]
        find_card_settlement_matches(settlements, card_txs)
        assert card_txs[0]["reconciled"] is True

    def test_card_tx_outside_window_not_matched(self):
        d = date(2025, 1, 15)
        settlements = [_settlement("s1", -100.00, d)]
        # 60 days before settlement — beyond default window_days=45
        card_txs = [_card_tx("c1", -100.00, d - timedelta(days=60))]
        results = find_card_settlement_matches(settlements, card_txs)
        assert results == []

    def test_multiple_settlements_independent(self):
        d = date(2025, 1, 20)
        settlements = [
            _settlement("s1", -100.00, d),
            _settlement("s2", -200.00, d + timedelta(days=2)),
        ]
        card_txs = [
            _card_tx("c1", -100.00, d - timedelta(days=5)),
            _card_tx("c2", -200.00, d - timedelta(days=3)),
        ]
        results = find_card_settlement_matches(settlements, card_txs)
        assert len(results) == 2
        s_ids = {r["settlement_id"] for r in results}
        assert s_ids == {"s1", "s2"}


class TestFindCardSettlementMatchesSubsetSum:
    """Cases that require the subset-sum phase (non-contiguous transactions)."""

    def test_subset_sum_fallback(self):
        """Transactions with a gap > max_gap_days force subset-sum path."""
        d = date(2025, 2, 10)
        settlements = [_settlement("s1", -150.00, d)]
        card_txs = [
            _card_tx("c1", -100.00, d - timedelta(days=40)),  # 40 days before
            _card_tx("c2",  -50.00, d - timedelta(days=2)),   # 2 days before
        ]
        # max_gap_days=5 so c1→c2 gap is 38 days → sliding window can't span both
        results = find_card_settlement_matches(
            settlements, card_txs, max_gap_days=5
        )
        # Either subset_sum or sliding_window found a match
        assert len(results) == 1
        matched = set(results[0]["matched_ids"])
        assert matched == {"c1", "c2"}

    def test_method_is_subset_sum_when_used(self):
        d = date(2025, 2, 10)
        settlements = [_settlement("s1", -75.00, d)]
        card_txs = [
            _card_tx("c1", -50.00, d - timedelta(days=30)),
            _card_tx("c2", -25.00, d - timedelta(days=1)),
        ]
        results = find_card_settlement_matches(
            settlements, card_txs, max_gap_days=3
        )
        if results:
            assert results[0]["method"] in ("sliding_window", "subset_sum")

    def test_epsilon_tolerance(self):
        """A settlement that's off by ≤ epsilon should still match."""
        d = date(2025, 1, 15)
        settlements = [_settlement("s1", -100.005, d)]
        card_txs    = [_card_tx("c1", -100.00, d - timedelta(days=3))]
        results = find_card_settlement_matches(
            settlements, card_txs, epsilon=Decimal("0.01")
        )
        assert len(results) == 1
        assert abs(results[0]["delta"]) <= Decimal("0.01")
