"""Document classification (Flow 2 / RF-01).

Given raw tabular data from an unknown source, uses LLM structured output to
produce a DocumentSchema that can be persisted as a template for Flow 1.

Architecture: two-phase classification
  Phase 0 (Python, pre-LLM): deterministic synonym matching on column names.
    Always resolves: description_col, description_cols, date_col candidates.
    Sometimes resolves: amount semantics (outflow/inflow/debit_positive),
      invert_sign, debit_col, credit_col.
  Phase 1 (LLM): receives Phase 0 findings as facts; focuses on genuinely
    ambiguous fields (doc_type, date_format, sign_convention for neutral amounts).
  Post-LLM (Python): merge Phase 0 results (Phase 0 wins), coerce column names,
    safety-net re-enforcement of invert_sign.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field as dc_field
from pathlib import Path

import pandas as pd

from core.llm_backends import LLMBackend, SanitizationRequiredError, call_with_fallback
from core.normalizer import compute_columns_key
from core.sanitizer import SanitizationConfig, sanitize_dataframe_descriptions
from core.schemas import DocumentSchema
from support.logging import setup_logging

logger = setup_logging()

_PROMPTS_FILE = Path(__file__).parent.parent / "prompts" / "classifier.json"


def _load_prompts() -> dict:
    with open(_PROMPTS_FILE, encoding="utf-8") as f:
        return json.load(f)


_PROMPTS = _load_prompts()


def classify_document(
    df_raw: pd.DataFrame,
    llm_backend: LLMBackend,
    source_name: str = "unknown",
    sanitize: bool = True,
    sanitize_config: SanitizationConfig | None = None,
    fallback_backend: LLMBackend | None = None,
) -> DocumentSchema | None:
    """
    Flow 2: classify a raw DataFrame and return a DocumentSchema.

    Args:
        df_raw: raw DataFrame as loaded from CSV/Excel (no normalization yet).
        llm_backend: the LLM backend to use.
        source_name: name of the source file (for logging).
        sanitize: whether to sanitize descriptions before sending to LLM.
        sanitize_config: PII sanitization configuration.
        fallback_backend: fallback LLM backend (must be local).

    Returns:
        DocumentSchema or None if classification failed.
    """
    if llm_backend.is_remote and not sanitize:
        raise SanitizationRequiredError(
            "Sanitization is mandatory for remote LLM backends (RF-10)."
        )

    # Build a compact sample for the prompt (max 20 rows)
    sample = df_raw.head(20).copy()

    if sanitize:
        for col in sample.select_dtypes(include="object").columns:
            sample[col] = sanitize_dataframe_descriptions(
                sample[col].astype(str).tolist(), sanitize_config
            )

    sample_json = sample.to_json(orient="records", force_ascii=False)
    columns_list = df_raw.columns.tolist()

    # ── Phase 0: Python deterministic pre-analysis (before LLM) ──────────────
    step0 = _run_step0_analysis(list(df_raw.columns))
    # For neutral amount columns (name carries no sign semantics), inspect the
    # actual sample data: if any value is negative the column is already signed
    # → invert_sign=False can be resolved deterministically without the LLM.
    if step0.amount_semantics == "neutral" and step0.amount_col:
        step0 = _inspect_neutral_column_sign(step0, df_raw, source_name)
    step0_text = _format_step0_for_prompt(step0)

    user_prompt = _PROMPTS["user_template"].format(
        source_name=source_name,
        columns_list=columns_list,
        sample_json=sample_json,
        step0_analysis=step0_text,
    )

    schema = DocumentSchema(
        doc_type="unknown",
        date_col="",
        amount_col="",
        sign_convention="signed_single",
        date_format="%d/%m/%Y",
        account_label=source_name,
        confidence="low",
    )
    json_schema = schema.llm_json_schema()

    result, backend_used = call_with_fallback(
        primary=llm_backend,
        system_prompt=_PROMPTS["system"],
        user_prompt=user_prompt,
        json_schema=json_schema,
        fallback=fallback_backend,
    )

    if result is None:
        logger.warning(f"classify_document: all backends failed for {source_name}")
        return None

    logger.info(f"classify_document: classified via {backend_used} (confidence={result.get('confidence')})")

    # Validate that column names returned by the LLM actually exist in the DataFrame.
    result = _coerce_column_names(result, list(df_raw.columns), source_name)

    # Merge Phase 0 deterministic findings — Phase 0 wins for all resolved fields.
    result = _merge_step0_into_result(result, step0, source_name)

    # Safety net: re-enforce invert_sign after merge (catches any LLM re-override).
    result = _apply_step0_invert_sign(result, source_name)

    try:
        doc_schema = DocumentSchema(**result)
        # Use columns fingerprint as cache key, not the filename, so the same
        # bank layout is recognised across differently-named export files.
        doc_schema.source_identifier = compute_columns_key(df_raw)
        return doc_schema
    except Exception as exc:
        logger.error(f"classify_document: schema validation failed: {exc}")
        return None


# ── Column fields ─────────────────────────────────────────────────────────────

_COLUMN_FIELDS = (
    "date_col", "date_accounting_col",
    "amount_col", "debit_col", "credit_col",
    "description_col", "currency_col",
)


# ── Phase 0 synonym tables ────────────────────────────────────────────────────

# Ordered list — earlier entry = higher priority for description_col selection.
_DESCRIPTION_PRIORITY: list[str] = [
    "causale",
    "descrizione",
    "dettagli operazione",
    "dettagli",
    "note",
    "memo",
    "tipo operazione",
    "tipo",
]

_DATE_OP_SYNONYMS: frozenset[str] = frozenset({
    "data operazione",
    "data movimento",
    "data transazione",
    "data addebito",
})

_DATE_ACC_SYNONYMS: frozenset[str] = frozenset({
    "data valuta",
    "data contabile",
    "data regolamento",
    # standalone "valuta" in Italian bank exports almost always means
    # "data di valuta" (value date), NOT currency type (which is "divisa").
    "valuta",
})

# Outflow-only column → expenses stored as positive → invert_sign=True
_DEBIT_COLUMN_SYNONYMS: frozenset[str] = frozenset({
    "uscita", "uscite",
    "addebito", "addebiti",
    "pagamento", "pagamenti",
    "importo addebitato",
    "spesa", "spese",
    "dare",
})

# Inflow-only column → incomes stored as positive → invert_sign=False
_CREDIT_COLUMN_SYNONYMS: frozenset[str] = frozenset({
    "entrata", "entrate",
    "accredito", "accrediti",
    "importo accreditato",
    "avere",
    "credito",
})

# Neutral amount column → sign direction unknown → needs LLM (Phase 1)
_AMOUNT_NEUTRAL_SYNONYMS: frozenset[str] = frozenset({
    "importo", "amount", "valore", "totale",
})

_BANK_DOC_TYPES: frozenset[str] = frozenset({"bank_account", "savings"})
_CREDIT_CARD_DOC_TYPES: frozenset[str] = frozenset({"credit_card"})


# ── Phase 0 result dataclass ──────────────────────────────────────────────────

@dataclass
class _Step0Result:
    """Output of the deterministic pre-LLM column analysis."""
    # Description
    description_col: str | None = None
    description_cols: list[str] = dc_field(default_factory=list)

    # Date
    date_col: str | None = None
    date_accounting_col: str | None = None

    # Amount / sign
    amount_col: str | None = None        # single-column (signed_single)
    debit_col: str | None = None         # debit_positive convention
    credit_col: str | None = None        # debit_positive convention
    amount_semantics: str = "unclear"    # "outflow"|"inflow"|"neutral"|"debit_positive"|"unclear"
    invert_sign: bool | None = None      # None = unresolved → LLM must decide


def _run_step0_analysis(columns: list[str]) -> _Step0Result:
    """Deterministic pre-LLM column analysis.

    Matches column names against synonym tables for description, date, and
    amount/sign semantics.  Returns a _Step0Result; unresolved fields stay
    None / "unclear" for the LLM (Phase 1) to determine.
    """
    r = _Step0Result()
    lower_to_orig: dict[str, str] = {c.lower(): c for c in columns}

    # ── Description columns ───────────────────────────────────────────────────
    desc_candidates: list[str] = []
    for col_low, col_orig in lower_to_orig.items():
        if any(syn in col_low for syn in _DESCRIPTION_PRIORITY):
            desc_candidates.append(col_orig)

    if desc_candidates:
        def _desc_rank(col: str) -> int:
            cl = col.lower()
            for i, syn in enumerate(_DESCRIPTION_PRIORITY):
                if syn in cl:
                    return i
            return len(_DESCRIPTION_PRIORITY)

        desc_candidates.sort(key=_desc_rank)
        r.description_col = desc_candidates[0]
        r.description_cols = desc_candidates

    # ── Date columns ──────────────────────────────────────────────────────────
    # Check known multi-word synonyms first (e.g. "Data Operazione"),
    # then fall back to any column whose name starts with "data".
    for col_low, col_orig in lower_to_orig.items():
        if r.date_col is None and any(syn in col_low for syn in _DATE_OP_SYNONYMS):
            r.date_col = col_orig
        if r.date_accounting_col is None and any(syn in col_low for syn in _DATE_ACC_SYNONYMS):
            r.date_accounting_col = col_orig

    if r.date_col is None:
        for col_low, col_orig in lower_to_orig.items():
            if col_low.startswith("data"):
                r.date_col = col_orig
                break

    # ── Amount / sign columns ─────────────────────────────────────────────────
    debit_candidates: list[str] = []
    credit_candidates: list[str] = []
    neutral_candidates: list[str] = []

    for col_low, col_orig in lower_to_orig.items():
        if any(syn in col_low for syn in _DEBIT_COLUMN_SYNONYMS):
            debit_candidates.append(col_orig)
        elif any(syn in col_low for syn in _CREDIT_COLUMN_SYNONYMS):
            credit_candidates.append(col_orig)
        elif any(syn in col_low for syn in _AMOUNT_NEUTRAL_SYNONYMS):
            neutral_candidates.append(col_orig)

    if debit_candidates and credit_candidates:
        # Two separate columns → debit_positive convention (e.g. Dare/Avere)
        r.debit_col = debit_candidates[0]
        r.credit_col = credit_candidates[0]
        r.amount_semantics = "debit_positive"
        # invert_sign not applicable for debit_positive
    elif debit_candidates:
        # Single outflow column (e.g. Uscita, Addebito) → expenses stored as positive
        r.amount_col = debit_candidates[0]
        r.amount_semantics = "outflow"
        r.invert_sign = True
    elif credit_candidates:
        # Single inflow column alone (e.g. Accredito without a paired Addebito)
        r.amount_col = credit_candidates[0]
        r.amount_semantics = "inflow"
        r.invert_sign = False
    elif neutral_candidates:
        # Neutral column (Importo, Amount…) → LLM must decide invert_sign from data
        r.amount_col = neutral_candidates[0]
        r.amount_semantics = "neutral"
        # r.invert_sign remains None

    return r


def _inspect_neutral_column_sign(step0: _Step0Result, df: pd.DataFrame, source_name: str) -> _Step0Result:
    """Data-driven sign inspection for neutral amount columns.

    When the column name carries no sign semantics (Importo, Amount…), look at
    the actual sample values:
    - If ANY value is negative → signs are already embedded → invert_sign=False.
    - If ALL values are non-negative → ambiguous → leave invert_sign=None for LLM.
    """
    col = step0.amount_col
    if col not in df.columns:
        return step0

    # Parse numeric, dropping unparseable cells (currency symbols, separators…)
    vals = pd.to_numeric(
        df[col].astype(str).str.replace(r"[€$£\s]", "", regex=True)
                           .str.replace(",", ".", regex=False),
        errors="coerce",
    ).dropna()

    if len(vals) == 0:
        return step0

    n_negative = (vals < 0).sum()
    n_positive = (vals > 0).sum()
    total = n_negative + n_positive

    if total == 0:
        return step0

    # Resolve only when the majority of non-zero values are negative:
    # → expenses already negative (standard bank account style) → invert_sign=False.
    # When positive values dominate (credit-card style: positive=expense,
    # negative=payment) or the split is ambiguous, leave UNRESOLVED for the LLM.
    pct_negative = n_negative / total
    if pct_negative > 0.5:
        logger.info(
            f"classify_document [{source_name}]: Step 0 data inspection — "
            f"neutral column '{col}': {pct_negative:.0%} negative → invert_sign=False [RESOLVED]"
        )
        step0.invert_sign = False
        step0.amount_semantics = "signed_neutral"
    else:
        logger.info(
            f"classify_document [{source_name}]: Step 0 data inspection — "
            f"neutral column '{col}': {pct_negative:.0%} negative, majority positive → "
            f"invert_sign UNRESOLVED (LLM will decide)"
        )

    return step0


def _format_step0_for_prompt(r: _Step0Result) -> str:
    """Render _Step0Result as a prompt section injected before the LLM call."""
    lines = [
        "## Step 0 — Python deterministic pre-analysis",
        "Fields marked [RESOLVED] were identified by exact synonym matching on column names.",
        "Treat them as facts; only override if the sample data clearly contradicts.",
        "",
    ]

    # Description
    if r.description_col:
        lines.append(f"- description_col = '{r.description_col}'  [RESOLVED]")
    else:
        lines.append("- description_col = UNRESOLVED — infer from column names / sample")

    if r.description_cols:
        cols_str = ", ".join(f"'{c}'" for c in r.description_cols)
        lines.append(f"- description_cols = [{cols_str}]  [RESOLVED]")
    else:
        lines.append("- description_cols = UNRESOLVED")

    # Date
    if r.date_col:
        lines.append(f"- date_col = '{r.date_col}'  [RESOLVED]")
    else:
        lines.append("- date_col = UNRESOLVED — infer from column names")

    if r.date_accounting_col:
        lines.append(f"- date_accounting_col = '{r.date_accounting_col}'  [RESOLVED]")

    # Amount / sign
    if r.amount_semantics == "debit_positive":
        lines.append("- sign_convention = 'debit_positive'  [RESOLVED]")
        lines.append(f"- debit_col = '{r.debit_col}'  [RESOLVED]")
        lines.append(f"- credit_col = '{r.credit_col}'  [RESOLVED]")
        lines.append("- invert_sign: not applicable (debit_positive convention)")
    elif r.amount_col:
        lines.append(
            f"- amount_col = '{r.amount_col}'  "
            f"[RESOLVED, semantics={r.amount_semantics}]"
        )
        if r.invert_sign is not None:
            reason = (
                "column contains negative values → signs already embedded"
                if r.amount_semantics == "signed_neutral"
                else f"column is {r.amount_semantics}"
            )
            lines.append(
                f"- invert_sign = {str(r.invert_sign).lower()}  "
                f"[RESOLVED — {reason}]"
            )
        else:
            lines.append(
                "- invert_sign = UNRESOLVED — determine from sample sign distribution (Step 1)"
            )
    else:
        lines.append("- amount_col = UNRESOLVED — identify from column names / sample")
        lines.append("- invert_sign = UNRESOLVED — determine from sample sign distribution (Step 1)")

    return "\n".join(lines)


def _merge_step0_into_result(result: dict, step0: _Step0Result, source_name: str) -> dict:
    """Merge Phase 0 deterministic findings into the LLM result dict.

    Phase 0 always wins for resolved fields.  Fields that Step 0 left as
    None / "unclear" are taken from the LLM as-is.
    """
    out = dict(result)

    def _set(fld: str, val, reason: str) -> None:
        if out.get(fld) != val:
            logger.info(
                f"classify_document [{source_name}]: Step 0 merge "
                f"{fld} '{out.get(fld)}' → '{val}' ({reason})"
            )
        out[fld] = val

    # Description — always override (deterministic synonym match)
    if step0.description_col:
        _set("description_col", step0.description_col, "deterministic match")
    if step0.description_cols:
        _set("description_cols", step0.description_cols, "deterministic match")

    # Date — fill in only if LLM left them empty (LLM may recognise unusual names)
    if step0.date_col and not out.get("date_col"):
        _set("date_col", step0.date_col, "deterministic match")
    # date_accounting_col: always override — the LLM may confuse "Valuta" (value
    # date) with currency_col; Step 0 synonym matching is authoritative here.
    if step0.date_accounting_col:
        _set("date_accounting_col", step0.date_accounting_col, "deterministic match")
        # If the LLM assigned the same column to currency_col, clear it to avoid
        # using a date column as currency (e.g. POPSO "Valuta" column).
        if out.get("currency_col", "").lower() == step0.date_accounting_col.lower():
            logger.info(
                f"classify_document [{source_name}]: clearing currency_col "
                f"'{out['currency_col']}' — it's the value-date column"
            )
            out["currency_col"] = None

    # Amount / sign — override when Phase 0 resolved it
    if step0.amount_semantics == "debit_positive":
        _set("sign_convention", "debit_positive", "debit+credit columns found")
        if step0.debit_col:
            _set("debit_col", step0.debit_col, "deterministic match")
        if step0.credit_col:
            _set("credit_col", step0.credit_col, "deterministic match")
    else:
        if step0.invert_sign is not None:
            _set("invert_sign", step0.invert_sign, f"semantics={step0.amount_semantics}")
        if step0.amount_col and not out.get("amount_col"):
            _set("amount_col", step0.amount_col, "deterministic match")

    return out


# ── Post-LLM safety net ───────────────────────────────────────────────────────

# Aliases so the safety-net function reuses the same synonym tables as Phase 0.
_OUTFLOW_SYNONYMS: frozenset[str] = _DEBIT_COLUMN_SYNONYMS
_INFLOW_SYNONYMS: frozenset[str] = _CREDIT_COLUMN_SYNONYMS


def _apply_step0_invert_sign(result: dict, source_name: str) -> dict:
    """Post-merge safety net: re-enforce invert_sign from doc_type + amount column semantics.

    Runs after _merge_step0_into_result so both the LLM's doc_type and Phase 0
    column findings are available.  Only applies when sign_convention == signed_single.

    Rules (in priority order):
    1. credit_card doc_type → invert_sign=True always (positive=charge, negative=payment).
       Breaks the circular dependency: doc_type comes from LLM, invert_sign is then
       resolved deterministically here without needing it during Phase 0.
    2. Outflow column name → invert_sign=True (unless it's a bank account).
    3. Inflow column name → invert_sign=False.
    """
    out = dict(result)

    convention = str(out.get("sign_convention", "")).lower()
    if convention not in ("signed_single", ""):
        return out

    doc_type = str(out.get("doc_type", "")).lower()
    amount_col = str(out.get("amount_col") or "").strip().lower()

    # Rule 1: credit card → charges are positive → must invert
    if doc_type in _CREDIT_CARD_DOC_TYPES:
        if not out.get("invert_sign"):
            logger.info(
                f"classify_document [{source_name}]: safety-net — "
                f"doc_type=credit_card → invert_sign=True"
            )
            out["invert_sign"] = True
        return out

    # Rule 2/3: column-name semantics
    is_outflow = any(syn in amount_col for syn in _OUTFLOW_SYNONYMS)
    is_inflow = any(syn in amount_col for syn in _INFLOW_SYNONYMS)

    if is_outflow and doc_type not in _BANK_DOC_TYPES:
        if not out.get("invert_sign"):
            logger.info(
                f"classify_document [{source_name}]: safety-net — "
                f"amount_col='{out.get('amount_col')}' is outflow → invert_sign=True"
            )
            out["invert_sign"] = True
    elif is_inflow:
        if out.get("invert_sign"):
            logger.info(
                f"classify_document [{source_name}]: safety-net — "
                f"amount_col='{out.get('amount_col')}' is inflow → invert_sign=False"
            )
            out["invert_sign"] = False

    return out


def _coerce_column_names(result: dict, available: list[str], source_name: str) -> dict:
    """For every column-mapping field in result, ensure the value is an actual column
    in `available`. Tries case-insensitive match first; nullifies on no match.
    Logs a warning for each correction so debugging is easy.
    """
    lower_map = {c.lower(): c for c in available}
    out = dict(result)
    for col_field in _COLUMN_FIELDS:
        val = out.get(col_field)
        if not val:
            continue
        if val in available:
            continue  # exact match, keep as-is
        canonical = lower_map.get(val.lower())
        if canonical:
            logger.info(
                f"classify_document [{source_name}]: coerced {col_field} "
                f"'{val}' → '{canonical}' (case-insensitive match)"
            )
            out[col_field] = canonical
        else:
            logger.warning(
                f"classify_document [{source_name}]: {col_field}='{val}' not found in "
                f"columns {available!r} — setting to null"
            )
            out[col_field] = None
    return out
