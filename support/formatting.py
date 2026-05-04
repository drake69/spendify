"""Display formatting helpers — locale-aware date and amount rendering."""
from __future__ import annotations

from datetime import datetime

_EMPTY_VALUES = {"", "nan", "none", "0", "0.0", "0.00"}


def format_raw_amount_display(raw_amount: str | None) -> str:
    """Clean up raw_amount for display.

    Handles the internal ``'debit|credit'`` storage format used when a
    document has separate debit / credit columns:
    - ``"2.5|"``   → ``"2.5"``
    - ``"|3.00"``  → ``"3.00"``
    - ``"|"``      → ``""``
    Plain values are returned as-is (with leading/trailing whitespace stripped).
    """
    if not raw_amount:
        return ""
    raw = str(raw_amount).strip()
    if "|" not in raw:
        return raw
    debit_raw, _, credit_raw = raw.partition("|")
    d = debit_raw.strip()
    c = credit_raw.strip()
    # Return whichever side is meaningful (non-empty, non-zero, not 'nan')
    if c.lower() not in _EMPTY_VALUES:
        return c
    if d.lower() not in _EMPTY_VALUES:
        return d
    return ""


def strftime_to_momentjs(fmt: str) -> str:
    """Convert a Python strftime format string to Moment.js tokens.

    Needed because Streamlit DateColumn renders dates on the frontend
    using Moment.js, not Python strftime.
    """
    _MAP = {
        "%d": "DD", "%m": "MM", "%Y": "YYYY", "%y": "YY",
        "%B": "MMMM", "%b": "MMM", "%A": "dddd", "%a": "ddd",
        "%H": "HH", "%I": "hh", "%M": "mm", "%S": "ss", "%p": "A",
    }
    result = fmt
    for py_tok, mj_tok in _MAP.items():
        result = result.replace(py_tok, mj_tok)
    return result


def format_date_display(date_str: str, fmt: str = "%d/%m/%Y") -> str:
    """Convert an ISO date string (YYYY-MM-DD) to the given display format.

    Returns the original string if parsing fails.
    """
    if not date_str:
        return date_str
    try:
        dt = datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
        return dt.strftime(fmt)
    except ValueError:
        return date_str


def format_amount_display(
    amount: float,
    decimal_sep: str = ",",
    thousands_sep: str = ".",
    symbol: str = "€",
    decimals: int = 2,
) -> str:
    """Format an amount with locale-specific separators.

    Examples:
        format_amount_display(1234.56, ",", ".")  →  "1.234,56 €"
        format_amount_display(1234.56, ".", ",")  →  "1,234.56 €"
    """
    # Format with standard US separators first, then swap
    formatted = f"{abs(amount):,.{decimals}f}"
    # formatted is like "1,234.56"
    # swap . and , via a temp placeholder
    formatted = formatted.replace(",", "\x00").replace(".", decimal_sep).replace("\x00", thousands_sep)
    sign = "-" if amount < 0 else ""
    if symbol:
        return f"{sign}{formatted} {symbol}"
    return f"{sign}{formatted}"
