"""Deterministic normalization pipeline (RF-02, RF-03, RF-04, RF-06).

All functions are pure / side-effect-free and unit-testable without LLM mocks.
"""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from itertools import combinations, permutations as _permutations
from statistics import median
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


# ── Pre-processing constants ───────────────────────────────────────────────────

_PREHEADER_MAX_ROWS: int = 20        # absolute max rows we are willing to strip
_PREHEADER_MAX_RATIO: float = 0.10   # 10 % of total rows — safety cap
_PREHEADER_DENSITY_THRESHOLD: float = 0.5   # fraction of median density below which a row is "sparse"
_LOW_VARIABILITY_RATIO: float = 0.015  # nunique/nrows < 1.5 % → metadata/constant column


@dataclass
class PreprocessInfo:
    """Metadata produced by the Phase-0 preprocessing step.

    Carried alongside the raw DataFrame so the rest of the pipeline can log
    or display what was stripped/dropped without re-running the analysis.
    """
    skipped_rows: int = 0
    dropped_columns: list[str] = field(default_factory=list)
    columns_before_drop: list[str] = field(default_factory=list)
    """Column names captured AFTER preheader-strip but BEFORE drop_low_variability_columns.
    Used for stable schema lookup (cols_key) regardless of file size."""


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
    """Convert a raw amount value to Decimal. Returns None on failure or non-finite values."""
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return None
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
        # Use abs() so the result is correct whether the bank stores values as
        # positive (standard) or negative (e.g. YELLOW "Uscite" = -2.60).
        # Column NAME determines direction; the sign in the cell is irrelevant.
        debit_abs = abs(debit) if debit is not None else Decimal(0)
        credit_abs = abs(credit) if credit is not None else Decimal(0)
        return credit_abs - debit_abs
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

def compute_transaction_id(source_file: str, raw_date: str, raw_amount: str, raw_description: str,
                           account_label: str = "") -> str:
    """Return the first 24 chars of SHA-256 of the raw (pre-normalisation) fields.

    Using raw strings keeps the key stable across normalisation changes:
    improving parse_amount or normalize_description won't shift existing IDs.
    For debit/credit conventions pass raw_amount as '<debit_raw>|<credit_raw>'.

    Deduplication key uses account_label (stable bank account identifier) instead
    of source_file (filename), so the same transaction imported from two different
    files of the same account is correctly recognised as a duplicate.
    Falls back to source_file if account_label is empty (e.g. unclassified files).
    """
    account_key = account_label.strip() if account_label and account_label.strip() else source_file
    key = f"{account_key}|{raw_date}|{raw_amount}|{raw_description}"
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


def compute_header_sha256(raw_bytes: bytes, filename: str, n: int = 30) -> str:
    """SHA256 of the first min(n, total) raw rows — stable fingerprint of the file header/pre-header area."""
    import io as _io
    name_lower = filename.lower()
    if name_lower.endswith((".xlsx", ".xls")):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(_io.BytesIO(raw_bytes), read_only=True, data_only=True)
            sheet_name = detect_best_sheet(wb)
            ws = wb[sheet_name]
            rows = []
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i >= n:
                    break
                rows.append("|".join(str(c) if c is not None else "" for c in row))
            wb.close()
            content = "\n".join(rows)
        except Exception:
            content = str(raw_bytes[:2000])
    else:
        encoding = detect_encoding(raw_bytes)
        text = raw_bytes.decode(encoding, errors="replace")
        lines = text.splitlines()
        content = "\n".join(lines[:n])
    return hashlib.sha256(content.encode()).hexdigest()


def load_raw_head(raw_bytes: bytes, filename: str, n: int = 10) -> "pd.DataFrame":
    """Load first n rows of a file with NO skip_rows and NO preprocessing.
    Used for the schema review UI to show the user the raw file structure."""
    import io as _io
    name_lower = filename.lower()
    if name_lower.endswith((".xlsx", ".xls")):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(_io.BytesIO(raw_bytes), read_only=True, data_only=True)
            sheet_name = detect_best_sheet(wb)
            wb.close()
        except Exception:
            sheet_name = 0
        df = pd.read_excel(_io.BytesIO(raw_bytes), sheet_name=sheet_name, header=None, nrows=n)
    else:
        encoding = detect_encoding(raw_bytes)
        text = raw_bytes.decode(encoding, errors="replace")
        delimiter = detect_delimiter(text)
        df = pd.read_csv(
            _io.StringIO(text),
            sep=delimiter,
            header=None,
            nrows=n,
            engine="python",
            on_bad_lines="skip",
        )
    return df


# ── Phase-0 preprocessing ──────────────────────────────────────────────────────

def detect_and_strip_preheader_rows(
    df: pd.DataFrame,
    source_name: str = "",
) -> tuple[pd.DataFrame, int]:
    """Remove spurious metadata rows that appear *before* the actual column header.

    Banks occasionally export files where the first few rows contain account
    info, report titles, or empty lines before the real transaction table
    header.  pandas reads those rows as data, producing wrong column names and
    extra NaN-heavy rows at the top.

    Algorithm (language-agnostic, statistical):
    1. Reconstruct the pandas-consumed header row as row 0 of a working copy
       (``Unnamed: N`` synthetic column names are treated as empty cells).
    2. Count non-null cells per row → density array.
    3. Compute the median density across all rows.
    4. Walk from row 0 downward; collect contiguous rows whose density is
       below ``median × _PREHEADER_DENSITY_THRESHOLD``.
    5. Safety caps: if the candidate count exceeds ``_PREHEADER_MAX_ROWS``
       **or** ``_PREHEADER_MAX_RATIO × total_rows``, raise ``ValueError``
       (import stops with a descriptive message rather than silently
       producing garbage data).
    6. If ``n_sparse > 0``, reassign column names from the first non-sparse
       row (row index ``n_sparse`` in the working copy) and return the data
       rows that follow.

    Args:
        df: Raw DataFrame as returned by ``pd.read_csv`` / ``pd.read_excel``.
        source_name: Filename used only for log messages.

    Returns:
        ``(trimmed_df, n_skipped)`` where ``n_skipped`` is the number of
        pre-header rows removed.  If nothing was stripped, ``n_skipped == 0``
        and the original DataFrame is returned unchanged.

    Raises:
        ValueError: If the number of sparse rows at the start exceeds the
            safety caps, indicating the file probably does not start with a
            transaction table at all.
    """
    if len(df) < 4:
        # Too short to run safely; nothing to strip.
        return df, 0

    total_rows = len(df) + 1  # +1 for the reconstructed header row

    # Step 1 – Reconstruct the pandas-consumed header row.
    header_values: list = [
        None if re.match(r"^Unnamed: \d+$", str(c)) else str(c)
        for c in df.columns
    ]
    n_cols = len(header_values)
    header_series = pd.Series(header_values, index=df.columns)
    df_full = pd.concat(
        [header_series.to_frame().T, df.reset_index(drop=True)],
        ignore_index=True,
    )

    # Step 2 – Non-null density per row.
    densities: list[float] = [
        row.notna().sum() / n_cols
        for _, row in df_full.iterrows()
    ]

    # Step 3 – Median density.
    med = median(densities)
    threshold = med * _PREHEADER_DENSITY_THRESHOLD

    # Step 4 – Contiguous sparse rows at the start.
    n_sparse = 0
    for d in densities:
        if d < threshold:
            n_sparse += 1
        else:
            break  # stop at first non-sparse row

    if n_sparse == 0:
        return df, 0

    # Step 5 – Safety caps.
    if n_sparse > _PREHEADER_MAX_ROWS:
        raise ValueError(
            f"[{source_name}] detect_and_strip_preheader_rows: "
            f"{n_sparse} sparse rows detected at the start, exceeding the "
            f"absolute cap of {_PREHEADER_MAX_ROWS}. "
            "The file may not contain a standard transaction table."
        )
    ratio = n_sparse / total_rows
    if ratio > _PREHEADER_MAX_RATIO:
        raise ValueError(
            f"[{source_name}] detect_and_strip_preheader_rows: "
            f"{n_sparse} sparse rows represent {ratio:.1%} of the file, "
            f"exceeding the {_PREHEADER_MAX_RATIO:.0%} safety cap. "
            "The file may not contain a standard transaction table."
        )

    # Step 6 – Reassign column names from the first non-sparse row.
    new_header_row = df_full.iloc[n_sparse]
    new_columns = [
        (str(v).strip() if pd.notna(v) and str(v).strip() else f"Unnamed: {i}")
        for i, v in enumerate(new_header_row)
    ]
    result = df_full.iloc[n_sparse + 1:].copy()
    result.columns = new_columns
    result = result.reset_index(drop=True)

    logger.info(
        "[%s] Stripped %d pre-header row(s) (density threshold=%.2f × median %.2f)",
        source_name, n_sparse, _PREHEADER_DENSITY_THRESHOLD, med,
    )
    return result, n_sparse


def drop_low_variability_columns(
    df: pd.DataFrame,
    source_name: str = "",
    min_ratio: float = _LOW_VARIABILITY_RATIO,
) -> tuple[pd.DataFrame, list[str]]:
    """Remove columns whose value diversity is too low to carry transaction data.

    Columns like "Nome titolare" (always the same name) or "Numero carta"
    (same masked PAN on every row) add noise without information.  A column
    is considered metadata/constant if::

        nunique(col) / nrows < min_ratio

    At least 2 columns are always preserved so downstream processing has
    something to work with.

    Args:
        df: DataFrame (may already be pre-header-stripped).
        source_name: Filename used only for log messages.
        min_ratio: Fraction threshold (default ``_LOW_VARIABILITY_RATIO``
            = 1.5 %).

    Returns:
        ``(cleaned_df, dropped_column_names)``.  If nothing was dropped,
        ``dropped_column_names`` is an empty list.
    """
    if len(df) < 2 or len(df.columns) <= 2:
        return df, []

    nrows = len(df)
    to_drop: list[str] = []

    for col in df.columns:
        ratio = df[col].nunique(dropna=True) / nrows
        if ratio < min_ratio:
            to_drop.append(col)

    # Never drop below 2 columns.
    max_droppable = len(df.columns) - 2
    if len(to_drop) > max_droppable:
        to_drop = to_drop[:max_droppable]

    if not to_drop:
        return df, []

    result = df.drop(columns=to_drop)
    logger.info(
        "[%s] Dropped %d low-variability column(s): %s",
        source_name, len(to_drop), to_drop,
    )
    return result, to_drop


# ── Internal transfer detection (RF-04) ──────────────────────────────────────

def _build_owner_name_regex(owner_names: list[str]) -> re.Pattern | None:
    """Build a regex that matches any token-permutation of each owner name.

    For "Mario Rossi" generates both "Mario Rossi" and "Rossi Mario" patterns
    so the description matches regardless of the order banks write the name.
    Each token permutation is joined with \\s+ to tolerate extra spaces.
    Single-token names (e.g. a company abbreviation) are matched as-is.
    """
    if not owner_names:
        return None
    patterns: list[str] = []
    for name in owner_names:
        tokens = name.strip().split()
        if not tokens:
            continue
        if len(tokens) == 1:
            patterns.append(re.escape(tokens[0]))
        else:
            for perm in _permutations(tokens):
                patterns.append(r"\s+".join(re.escape(t) for t in perm))
    if not patterns:
        return None
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE)


def detect_internal_transfers(
    df: pd.DataFrame,
    epsilon: Decimal = Decimal("0.01"),
    delta_days: int = 5,
    epsilon_strict: Decimal = Decimal("0.005"),
    delta_days_strict: int = 1,
    keyword_patterns: list[str] | None = None,
    require_keyword_confirmation: bool = True,
    owner_names: list[str] | None = None,
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

    # Owner-name pass: any unpaired transaction whose description contains an
    # owner name is marked directly as internal_out / internal_in (no pairing
    # needed — the owner is the other side of the transfer).
    # Owner-name pass: match any token-permutation of each owner name so that
    # both "Mario Rossi" and "ROSSI MARIO" (common in bank exports) are caught.
    owner_re = _build_owner_name_regex(owner_names) if owner_names else None
    if owner_re:
        for idx in df.index:
            if idx in already_paired:
                continue
            desc = str(df.at[idx, "description"] or "")
            if owner_re.search(desc):
                amt = df.at[idx, "amount"]
                if not isinstance(amt, Decimal):
                    amt = Decimal(str(amt or 0))
                df.at[idx, "tx_type"] = (
                    TransactionType.internal_out.value if amt < 0
                    else TransactionType.internal_in.value
                )
                df.at[idx, "transfer_confidence"] = Confidence.high.value
                logger.info(
                    f"detect_internal_transfers: owner-name match → "
                    f"idx={idx} desc='{desc[:40]}' "
                    f"type={df.at[idx, 'tx_type']}"
                )

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
    owner_name_label: str | None = None,
) -> tuple[list[dict], bool]:
    """Handle the single balance/totale summary row from a card file if present.

    Some card exports include a row whose |amount| equals the sum of |all other
    amounts| (the statement total). Including it would double-count every expense.

    Detection rule (requires ≥ 3 transactions):
        For each candidate row i:
            sum_others = Σ |amount_j| for j ≠ i
            if ||amount_i| - sum_others| ≤ epsilon  →  row i is the balance row

    Behaviour:
        - If owner_name_label is provided: rename the row's description to the
          owner name instead of removing it, so the transfer detector can later
          mark it as giroconto (internal transfer).
        - Otherwise: remove the row (legacy behaviour, avoids double-counting).

    Only the FIRST such row is handled (there should be at most one).

    Returns:
        (transactions, was_found)
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
            if owner_name_label:
                logger.info(
                    f"remove_card_balance_row: balance/totale row relabelled as '{owner_name_label}' "
                    f"id={tx.get('id', '?')} amount={amt_i} ≈ sum_others={sum_others} "
                    f"(source={tx.get('source_file', '?')})"
                )
                transactions[i]["description"] = owner_name_label
                return transactions, True
            else:
                logger.info(
                    f"remove_card_balance_row: removed balance/totale row "
                    f"id={tx.get('id', '?')} amount={amt_i} ≈ sum_others={sum_others} "
                    f"(source={tx.get('source_file', '?')})"
                )
                return transactions[:i] + transactions[i + 1:], True

    return transactions, False
