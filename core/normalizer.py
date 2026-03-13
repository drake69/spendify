"""Deterministic normalization pipeline (RF-02, RF-03, RF-04, RF-06).

All functions are pure / side-effect-free and unit-testable without LLM mocks.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from itertools import combinations
from typing import Optional

import chardet
import pandas as pd

from core.models import (
    Confidence,
    DocumentType,
    GirocontoMode,
    SignConvention,
    TransactionType,
)
from core.schemas import DocumentSchema
from support.logging import setup_logging

logger = setup_logging()


# ── Encoding / format detection ───────────────────────────────────────────────

def detect_encoding(raw_bytes: bytes) -> str:
    """Detect file encoding using chardet. Returns lowercase encoding string."""
    result = chardet.detect(raw_bytes)
    enc = (result.get("encoding") or "utf-8").lower()
    # Normalize common aliases
    if enc in ("ascii",):
        enc = "utf-8"
    return enc


def detect_delimiter(content: str) -> str:
    """Detect CSV delimiter by frequency analysis."""
    candidates = [",", ";", "\t", "|"]
    counts = {d: content.count(d) for d in candidates}
    return max(counts, key=counts.get)


def detect_header_row(lines: list[str]) -> int:
    """Return index of the first line with ≥2 non-empty, non-numeric fields."""
    for i, line in enumerate(lines):
        fields = [f.strip() for f in re.split(r'[,;\t|]', line)]
        non_numeric = sum(
            1 for f in fields
            if f and not re.match(r'^[\d\.\,\-\+\s€$£%]+$', f)
        )
        if non_numeric >= 2:
            return i
    return 0


def detect_best_sheet(workbook) -> str:
    """Select the sheet with the most numeric columns and rows,
    excluding sheets whose name matches summary patterns."""
    summary_re = re.compile(r'summary|totale|riepilogo', re.IGNORECASE)
    best_sheet = None
    best_score = -1

    for name in workbook.sheetnames:
        if summary_re.search(name):
            continue
        ws = workbook[name]
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue
        n_rows = len(rows)
        n_numeric_cols = sum(
            1 for col_idx in range(len(rows[0]))
            if sum(1 for row in rows if _is_numeric_cell(row[col_idx] if col_idx < len(row) else None)) > n_rows * 0.5
        )
        score = n_rows + n_numeric_cols * 10
        if score > best_score:
            best_score = score
            best_sheet = name

    return best_sheet or workbook.sheetnames[0]


def _is_numeric_cell(value) -> bool:
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return True
    try:
        float(str(value).replace(",", ".").replace(" ", ""))
        return True
    except ValueError:
        return False


# ── Date normalization ────────────────────────────────────────────────────────

_DATE_FORMATS_FALLBACK = [
    "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y",
    "%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%m/%d/%y",
]


def parse_date_safe(value: str, fmt: str) -> Optional[date]:
    """Parse a date string. Tries fmt first, then common Italian/ISO fallbacks."""
    if not value or not isinstance(value, str):
        return None
    import datetime
    v = value.strip()
    # Try the specified format first
    if fmt:
        try:
            return datetime.datetime.strptime(v, fmt).date()
        except ValueError:
            pass
    # Fallback: try common formats
    for fallback in _DATE_FORMATS_FALLBACK:
        if fallback == fmt:
            continue
        try:
            return datetime.datetime.strptime(v, fallback).date()
        except ValueError:
            continue
    return None


# ── Amount normalization ──────────────────────────────────────────────────────

def parse_amount(value: str | float | int | Decimal) -> Optional[Decimal]:
    """Convert a raw amount value to Decimal. Returns None on failure."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if not isinstance(value, str):
        return None
    s = str(value).strip()
    s = re.sub(r'[€$£\s]', '', s)
    # Detect separators
    has_dot = '.' in s
    has_comma = ',' in s
    if has_dot and has_comma:
        # e.g. "1.234,56" → European; "1,234.56" → US
        if s.rfind('.') < s.rfind(','):
            s = s.replace('.', '').replace(',', '.')
        else:
            s = s.replace(',', '')
    elif has_comma:
        # Could be decimal separator
        parts = s.split(',')
        if len(parts) == 2 and len(parts[1]) <= 2:
            s = s.replace(',', '.')
        else:
            s = s.replace(',', '')
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def apply_sign_convention(
    row: dict,
    amount_col: str,
    debit_col: Optional[str],
    credit_col: Optional[str],
    convention: SignConvention,
) -> Optional[Decimal]:
    """Return a signed Decimal for the transaction amount (negative = expense)."""
    if convention == SignConvention.signed_single:
        return parse_amount(row.get(amount_col))
    elif convention == SignConvention.debit_positive:
        debit = parse_amount(row.get(debit_col)) if debit_col else None
        credit = parse_amount(row.get(credit_col)) if credit_col else None
        # If neither column parsed, fall back to amount_col (schema may have mismapped)
        if debit is None and credit is None:
            return parse_amount(row.get(amount_col)) if amount_col else None
        debit = debit or Decimal(0)
        credit = credit or Decimal(0)
        return credit - debit
    elif convention == SignConvention.credit_negative:
        # credit column is positive (income), debit column is negative (expense)
        credit = parse_amount(row.get(credit_col)) if credit_col else None
        debit = parse_amount(row.get(debit_col)) if debit_col else None
        if credit is None and debit is None:
            return parse_amount(row.get(amount_col)) if amount_col else None
        if credit and credit > 0:
            return credit
        if debit:
            return -abs(debit)
        return parse_amount(row.get(amount_col))
    return None


# ── Description normalization ─────────────────────────────────────────────────

def normalize_description(text: str) -> str:
    """casefold + trim + unicode NFC"""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", str(text))
    return text.casefold().strip()


# ── SHA-256 idempotency key ────────────────────────────────────────────────────

def compute_transaction_id(source_file: str, raw_date: str, raw_amount: str, raw_description: str) -> str:
    """Return the first 24 chars of SHA-256 of the raw (pre-normalisation) fields.

    Using raw strings keeps the key stable across normalisation changes:
    improving parse_amount or normalize_description won't shift existing IDs.
    For debit/credit conventions pass raw_amount as '<debit_raw>|<credit_raw>'.
    """
    key = f"{source_file}|{raw_date}|{raw_amount}|{raw_description}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def compute_columns_key(df: "pd.DataFrame") -> str:  # type: ignore[name-defined]
    """Return SHA-256[:16] of the sorted column names.

    Used as the DocumentSchema cache key so the same bank layout is recognised
    regardless of the export filename (e.g. CARTA_2025.xlsx vs CARTA_2026.xlsx).
    Columns are sorted to be robust to minor reordering.
    """
    cols = "|".join(sorted(str(c).strip() for c in df.columns))
    return "cols:" + hashlib.sha256(cols.encode("utf-8")).hexdigest()[:16]


def compute_file_hash(raw_bytes: bytes) -> str:
    """Return SHA-256 hex of raw file bytes (for import-level idempotency)."""
    return hashlib.sha256(raw_bytes).hexdigest()


# ── Internal transfer detection (RF-04) ──────────────────────────────────────

def detect_internal_transfers(
    df: pd.DataFrame,
    epsilon: Decimal = Decimal("0.01"),
    delta_days: int = 5,
    epsilon_strict: Decimal = Decimal("0.005"),
    delta_days_strict: int = 1,
    keyword_patterns: list[str] | None = None,
    require_keyword_confirmation: bool = True,
) -> pd.DataFrame:
    """
    Mark pairs of transactions as internal transfers (RF-04).

    Adds columns:
      - transfer_pair_id: shared ID for the matched pair (or None)
      - tx_type: updated to internal_out / internal_in where matched
      - transfer_confidence: Confidence enum value

    The df must have columns: id, date, amount (Decimal), description,
    account_label, tx_type.
    """
    df = df.copy()
    if "transfer_pair_id" not in df.columns:
        df["transfer_pair_id"] = None
    if "transfer_confidence" not in df.columns:
        df["transfer_confidence"] = None

    keyword_re = None
    if keyword_patterns:
        keyword_re = re.compile('|'.join(re.escape(p) for p in keyword_patterns), re.IGNORECASE)

    def _keyword_match(text: str) -> bool:
        if not keyword_re:
            return False
        return bool(keyword_re.search(text or ""))

    def _high_sym(ai: Decimal, di: date, aj: Decimal, dj: date) -> bool:
        return (
            abs(ai + aj) <= epsilon_strict
            and abs((di - dj).days) <= delta_days_strict
        )

    indices = df.index.tolist()
    already_paired: set = set()

    for i, j in combinations(indices, 2):
        if i in already_paired or j in already_paired:
            continue
        ri, rj = df.loc[i], df.loc[j]
        # Skip same account
        if ri.get("account_label") == rj.get("account_label"):
            continue

        ai: Decimal = ri["amount"] if isinstance(ri["amount"], Decimal) else Decimal(str(ri["amount"] or 0))
        aj: Decimal = rj["amount"] if isinstance(rj["amount"], Decimal) else Decimal(str(rj["amount"] or 0))
        di: date = ri["date"] if isinstance(ri["date"], date) else pd.to_datetime(ri["date"]).date()
        dj: date = rj["date"] if isinstance(rj["date"], date) else pd.to_datetime(rj["date"]).date()

        amount_match = abs(ai + aj) <= epsilon
        date_match = abs((di - dj).days) <= delta_days

        if not (amount_match and date_match):
            continue

        kw_i = _keyword_match(str(ri.get("description", "")))
        kw_j = _keyword_match(str(rj.get("description", "")))
        high_sym = _high_sym(ai, di, aj, dj)

        if kw_i or kw_j:
            confidence = Confidence.high
        elif high_sym:
            confidence = Confidence.medium
        else:
            continue

        if require_keyword_confirmation and confidence == Confidence.medium:
            # Mark for review but don't assign tx_type yet
            pair_id = f"transfer_{i}_{j}"
            df.at[i, "transfer_pair_id"] = pair_id
            df.at[j, "transfer_pair_id"] = pair_id
            df.at[i, "transfer_confidence"] = confidence.value
            df.at[j, "transfer_confidence"] = confidence.value
            already_paired.update([i, j])
            continue

        pair_id = f"transfer_{i}_{j}"
        out_idx = i if ai < 0 else j
        in_idx = j if ai < 0 else i

        df.at[out_idx, "tx_type"] = TransactionType.internal_out.value
        df.at[in_idx, "tx_type"] = TransactionType.internal_in.value
        df.at[i, "transfer_pair_id"] = pair_id
        df.at[j, "transfer_pair_id"] = pair_id
        df.at[i, "transfer_confidence"] = confidence.value
        df.at[j, "transfer_confidence"] = confidence.value
        already_paired.update([i, j])

    return df


# ── Card settlement reconciliation (RF-03) ────────────────────────────────────

def find_card_settlement_matches(
    settlements: list[dict],
    card_transactions: list[dict],
    epsilon: Decimal = Decimal("0.01"),
    window_days: int = 45,
    max_gap_days: int = 5,
    boundary_k: int = 10,
) -> list[dict]:
    """
    Match card_settlement rows from the bank account with card_tx rows.

    Returns a list of reconciliation records:
      {settlement_id, matched_ids: list[str], delta: Decimal, method: str}

    Three-phase algorithm:
      1. Temporal window filter
      2. Sliding window contiguous matching
      3. Subset sum at boundary
    """
    results = []

    for settlement in settlements:
        s_id = settlement["id"]
        s_amount = abs(settlement["amount"])
        s_date = settlement["date"] if isinstance(settlement["date"], date) else pd.to_datetime(settlement["date"]).date()

        # Phase 1: temporal window
        candidates = [
            tx for tx in card_transactions
            if not tx.get("reconciled", False)
        ]
        window_start = s_date - timedelta(days=window_days)
        window_end = s_date + timedelta(days=7)
        windowed = [
            tx for tx in candidates
            if window_start <= (tx["date"] if isinstance(tx["date"], date) else pd.to_datetime(tx["date"]).date()) <= window_end
        ]

        # Phase 2: sliding window (contiguous subsets respecting max_gap_days)
        match = _sliding_window_match(windowed, s_amount, epsilon, max_gap_days)
        if match:
            results.append({
                "settlement_id": s_id,
                "matched_ids": [tx["id"] for tx in match],
                "delta": s_amount - sum(abs(tx["amount"]) for tx in match),
                "method": "sliding_window",
            })
            for tx in match:
                tx["reconciled"] = True
            continue

        # Phase 3: subset sum at boundary
        boundary_txs = _get_boundary_transactions(windowed, s_date, boundary_k)
        match = _subset_sum_match(boundary_txs, s_amount, epsilon)
        if match:
            results.append({
                "settlement_id": s_id,
                "matched_ids": [tx["id"] for tx in match],
                "delta": s_amount - sum(abs(tx["amount"]) for tx in match),
                "method": "subset_sum",
            })
            for tx in match:
                tx["reconciled"] = True

    return results


def _sliding_window_match(
    transactions: list[dict],
    target: Decimal,
    epsilon: Decimal,
    max_gap: int,
) -> list[dict] | None:
    """Find a contiguous subset whose sum ≈ target."""
    txs = sorted(
        transactions,
        key=lambda t: t["date"] if isinstance(t["date"], date) else pd.to_datetime(t["date"]).date(),
    )
    n = len(txs)
    for start in range(n):
        total = Decimal(0)
        subset = []
        for end in range(start, n):
            d_cur = txs[end]["date"] if isinstance(txs[end]["date"], date) else pd.to_datetime(txs[end]["date"]).date()
            if subset:
                d_prev = txs[end - 1]["date"] if isinstance(txs[end - 1]["date"], date) else pd.to_datetime(txs[end - 1]["date"]).date()
                if (d_cur - d_prev).days > max_gap:
                    break
            total += abs(txs[end]["amount"]) if isinstance(txs[end]["amount"], Decimal) else Decimal(str(abs(txs[end]["amount"])))
            subset.append(txs[end])
            if abs(total - target) <= epsilon:
                return subset
            if total > target + epsilon:
                break
    return None


def _get_boundary_transactions(transactions: list[dict], ref_date: date, k: int) -> list[dict]:
    sorted_txs = sorted(
        transactions,
        key=lambda t: t["date"] if isinstance(t["date"], date) else pd.to_datetime(t["date"]).date(),
    )
    before = [tx for tx in sorted_txs if (tx["date"] if isinstance(tx["date"], date) else pd.to_datetime(tx["date"]).date()) < ref_date]
    after = [tx for tx in sorted_txs if (tx["date"] if isinstance(tx["date"], date) else pd.to_datetime(tx["date"]).date()) >= ref_date]
    return before[-k:] + after[:k]


def _subset_sum_match(
    transactions: list[dict],
    target: Decimal,
    epsilon: Decimal,
) -> list[dict] | None:
    """Exhaustive subset sum (O(2^n), n ≤ 2k = 20 by default → safe)."""
    n = len(transactions)
    for r in range(1, n + 1):
        for subset in combinations(range(n), r):
            total = sum(
                abs(transactions[i]["amount"]) if isinstance(transactions[i]["amount"], Decimal)
                else Decimal(str(abs(transactions[i]["amount"])))
                for i in subset
            )
            if abs(total - target) <= epsilon:
                return [transactions[i] for i in subset]
    return None


# ── Card within-file balance row removal (Case 5) ────────────────────────────

def remove_card_balance_row(
    transactions: list[dict],
    epsilon: Decimal = Decimal("0.01"),
) -> tuple[list[dict], bool]:
    """Remove the single balance/totale summary row from a card file if present.

    Some card exports include a row whose |amount| equals the sum of |all other
    amounts| (the statement total). Including it would double-count every expense.

    Detection rule (requires ≥ 3 transactions):
        For each candidate row i:
            sum_others = Σ |amount_j| for j ≠ i
            if ||amount_i| - sum_others| ≤ epsilon  →  row i is the balance row

    Only the FIRST such row is removed (there should be at most one).

    Returns:
        (filtered_transactions, was_removed)
    """
    if len(transactions) < 3:
        return transactions, False

    amounts = [
        abs(tx["amount"]) if isinstance(tx["amount"], Decimal) else Decimal(str(abs(tx["amount"])))
        for tx in transactions
    ]
    total = sum(amounts)

    for i, tx in enumerate(transactions):
        amt_i = amounts[i]
        sum_others = total - amt_i
        if abs(amt_i - sum_others) <= epsilon:
            logger.info(
                f"remove_card_balance_row: removed balance/totale row "
                f"id={tx.get('id', '?')} amount={amt_i} ≈ sum_others={sum_others} "
                f"(source={tx.get('source_file', '?')})"
            )
            return transactions[:i] + transactions[i + 1:], True

    return transactions, False
