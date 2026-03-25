"""Document classification (Flow 2 / RF-01).

Given raw tabular data from an unknown source, uses LLM structured output to
produce a DocumentSchema that can be persisted as a template for Flow 1.

Architecture: two-phase classification
  Phase 0 (Python, pre-LLM): deterministic content-type detection on actual data.
    Classifies each column as 'date', 'amount', or 'text' by inspecting values.
    Text columns → description_cols; date columns → date_col(s);
    amount columns → amount_col / debit_col + credit_col.
    Column-name synonyms used only as tiebreakers within the same content type.
    Sometimes resolves: amount semantics (outflow/inflow/debit_positive), invert_sign.
  Phase 1 (LLM): receives Phase 0 findings as facts; focuses on genuinely
    ambiguous fields (doc_type, date_format, sign_convention for neutral amounts).
  Post-LLM (Python): merge Phase 0 results (Phase 0 wins), coerce column names,
    safety-net re-enforcement of invert_sign.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field as dc_field
from pathlib import Path

import pandas as pd

from core.llm_backends import LLMBackend, SanitizationRequiredError, call_with_fallback
from core.models import Confidence
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

# Personal-finance plausibility cap for amount column detection.
# Columns whose median absolute value exceeds this threshold are treated as
# reference/ID columns rather than monetary amounts.
# Configurable via user_settings key "max_transaction_amount" (default: 1 000 000).
_AMOUNT_PLAUSIBILITY_CAP_DEFAULT = 1_000_000


def compute_confidence_score(schema_dict: dict, header_certain: bool = True) -> float:
    """Compute a deterministic confidence score (0.0-1.0) from schema fields.

    Weighted components:
      - Header detection certain:      0.15
      - Date column found:             0.15
      - Amount/Debit-Credit resolved:  0.25
      - Description column found:      0.10
      - Sign convention resolved:      0.15
      - Doc type set:                  0.10
      - Account label present:         0.10
    """
    score = 0.0

    # Header detection certain
    if header_certain:
        score += 0.15

    # Date column found
    if schema_dict.get("date_col"):
        score += 0.15

    # Amount or Debit+Credit resolved
    if schema_dict.get("amount_col"):
        score += 0.25
    elif schema_dict.get("debit_col") and schema_dict.get("credit_col"):
        score += 0.25

    # Description column found
    if schema_dict.get("description_col"):
        score += 0.10

    # Sign convention resolved
    sign_conv = schema_dict.get("sign_convention")
    if sign_conv is not None and sign_conv != "":
        score += 0.15

    # Doc type set
    doc_type = schema_dict.get("doc_type")
    if doc_type is not None and doc_type != "" and doc_type != "unknown":
        score += 0.10

    # Account label present
    account_label = schema_dict.get("account_label")
    if account_label is not None and account_label != "":
        score += 0.10

    return round(min(score, 1.0), 2)


def classify_document(
    df_raw: pd.DataFrame,
    llm_backend: LLMBackend,
    source_name: str = "unknown",
    sanitize: bool = True,
    sanitize_config: SanitizationConfig | None = None,
    fallback_backend: LLMBackend | None = None,
    amount_plausibility_cap: float = _AMOUNT_PLAUSIBILITY_CAP_DEFAULT,
    header_certain: bool = True,
    account_type: str | None = None,
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
        header_certain: whether pre-load header detection was certain.
        account_type: user-specified account type (e.g. 'credit_card'); used as
            a constraint for doc_type inference and invert_sign logic.

    Returns:
        DocumentSchema or None if classification failed.
    """
    if not sanitize:
        raise SanitizationRequiredError(
            "Sanitization is mandatory before any LLM call (RF-10)."
        )

    # Build a compact sample for the prompt (max 20 rows)
    sample = df_raw.head(20).copy()

    if sanitize:
        for col in sample.select_dtypes(include="object").columns:
            sample[col] = sanitize_dataframe_descriptions(
                sample[col].astype(str).tolist(), sanitize_config
            )

    sample_json = sample.to_json(orient="records", force_ascii=False)
    columns_list = [str(c) for c in df_raw.columns]

    # ── Phase 0: Python deterministic pre-analysis (before LLM) ──────────────
    step0 = _run_step0_analysis(
        list(df_raw.columns), df_raw=df_raw,
        amount_plausibility_cap=amount_plausibility_cap,
    )
    # For neutral amount columns (name carries no sign semantics), inspect the
    # actual sample data: if any value is negative the column is already signed
    # → invert_sign=False can be resolved deterministically without the LLM.
    if step0.amount_semantics == "neutral" and step0.amount_col:
        step0 = _inspect_neutral_column_sign(step0, df_raw, source_name)
    step0_text = _format_step0_for_prompt(step0)

    # Inject account_type constraint when the user has specified the account type
    if account_type:
        _type_to_doc = {
            "credit_card": "credit_card",
            "bank_account": "bank_account",
            "debit_card": "debit_card",
            "prepaid_card": "prepaid_card",
            "savings_account": "savings",
            "cash": "bank_account",
        }
        _doc_hint = _type_to_doc.get(account_type, account_type)
        step0_text += (
            f"\n\n## Account type constraint (user-specified)\n"
            f"The user specified this account is a **{account_type}**. "
            f"Set doc_type = '{_doc_hint}' unless the data clearly contradicts.\n"
        )
        logger.info(
            f"classify_document [{source_name}]: account_type constraint "
            f"'{account_type}' → doc_type hint '{_doc_hint}'"
        )

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
    result = _apply_step0_invert_sign(result, source_name, account_type=account_type)

    # Compute deterministic confidence score from merged result
    score = compute_confidence_score(result, header_certain=header_certain)
    result["confidence_score"] = score
    result["confidence"] = Confidence.from_score(score).value
    logger.info(
        f"classify_document: confidence_score={score} "
        f"(confidence={result['confidence']}) for {source_name}"
    )

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


# ── Phase 0 synonym tables (multilingual) ─────────────────────────────────────

# Ordered list — earlier entry = higher priority for description_col selection.
_DESCRIPTION_PRIORITY: list[str] = [
    # Italian — "tipo" / "tipologia" inclusi: contengono spesso la descrizione completa
    # (es. Satispay usa "Tipo" per il tipo operazione, Sicily usa "Tipologia" per la narrativa)
    # Il filtro UUID nell'orchestrator rimuove eventuali ID transazione interni.
    "causale", "dettagli operazione", "descrizione", "dettagli",
    "tipo operazione", "tipologia", "tipo", "note", "memo",
    # English
    "description", "narrative", "transaction type", "details", "reference", "remarks",
    # French
    "libellé", "libelle", "opération", "operation", "détails", "motif",
    # German
    "buchungstext", "verwendungszweck", "beschreibung", "betreff",
    # Spanish
    "concepto", "descripción", "descripcion",
]

_DATE_OP_SYNONYMS: frozenset[str] = frozenset({
    # Italian
    "data operazione", "data movimento", "data transazione", "data addebito",
    # English
    "transaction date", "posting date", "booking date",
    # French
    "date opération", "date operation", "date de transaction",
    # German
    "buchungsdatum", "buchungstag",
    # Spanish
    "fecha operación", "fecha operacion", "fecha transacción", "fecha transaccion",
})

_DATE_ACC_SYNONYMS: frozenset[str] = frozenset({
    # Italian — standalone "valuta" almost always means "data di valuta"
    # (value date), NOT currency type (which is "divisa" in Italian).
    "data valuta", "data contabile", "data regolamento", "valuta",
    # English
    "value date", "settlement date", "accounting date",
    # French
    "date de valeur", "date valeur", "date comptable",
    # German
    "wertstellungsdatum", "wertstellung",
    # Spanish
    "fecha valor",
})

# Outflow-only column → expenses stored as positive → invert_sign=True
_DEBIT_COLUMN_SYNONYMS: frozenset[str] = frozenset({
    # Italian
    "uscita", "uscite", "addebito", "addebiti",
    "pagamento", "pagamenti", "importo addebitato", "spesa", "spese", "dare",
    # English
    "debit", "withdrawal", "withdrawals", "charge", "charges", "outflow",
    # French
    "débit", "debit", "sortie", "sorties", "dépense", "dépenses",
    "retrait", "retraits", "prélèvement",
    # German
    "soll", "ausgabe", "ausgaben", "belastung", "lastschrift",
    # Spanish
    "cargo", "cargos", "débito", "salida", "salidas", "gasto", "gastos",
})

# Inflow-only column → incomes stored as positive → invert_sign=False
_CREDIT_COLUMN_SYNONYMS: frozenset[str] = frozenset({
    # Italian
    "entrata", "entrate", "accredito", "accrediti",
    "importo accreditato", "avere", "credito",
    # English
    "credit", "credits", "deposit", "deposits", "income", "inflow", "receipt",
    # French
    "crédit", "credit", "entrée", "entrées", "recette", "recettes",
    # German
    "haben", "einnahme", "einnahmen", "gutschrift", "einzahlung",
    # Spanish
    "abono", "abonos", "crédito", "entrada", "entradas", "ingreso", "ingresos",
})

# Neutral amount column → sign direction unknown → needs LLM (Phase 1)
_AMOUNT_NEUTRAL_SYNONYMS: frozenset[str] = frozenset({
    # Italian / English / French / German / Spanish
    "importo", "amount", "valore", "totale",
    "montant", "valeur", "total",
    "betrag", "wert", "summe",
    "importe", "monto", "valor",
})

_BANK_DOC_TYPES: frozenset[str] = frozenset({"bank_account", "savings"})
_CREDIT_CARD_DOC_TYPES: frozenset[str] = frozenset({"credit_card"})

# ── Content-type detection regexes (Phase 0 data-driven) ─────────────────────
# Date: three digit-groups separated by / - . with optional time component
_CONTENT_DATE_RE = re.compile(
    r'^\s*\d{1,4}[/\-.]\d{1,2}[/\-.]\d{2,4}(\s+\d{1,2}:\d{2}(:\d{2})?)?\s*$',
    re.ASCII,
)
# Amount: optional sign/currency, then digits with at most one decimal separator
# (comma or dot followed by 1-2 digits).  Thousands separators (dot/comma before
# a 3-digit group) are also allowed.
_CONTENT_AMOUNT_RE = re.compile(
    r'^\s*[€$£¥₹+\-]?\s*\d[\d\s]*'          # leading sign/currency + first digits
    r'([.,]\d{3})*'                           # optional thousands groups
    r'([.,]\d{1,2})?\s*[€$£¥₹]?\s*$',        # optional decimal + trailing currency
    re.UNICODE,
)
_CONTENT_MIN_SAMPLE = 1    # minimum non-null values required to attempt classification
_CONTENT_MIN_RATIO  = 0.60 # fraction of samples that must match to confirm a type


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


def _is_categorical(series: pd.Series) -> bool:
    """True if the column looks like a category/flag rather than free text.

    A column is categorical when it has very few distinct values (≤ 5 absolute,
    or ≤ 3 % of total non-null rows).  This filters out enum-like columns such
    as Satispay "Tipo" (🏬/🛡️/🏦, 3 distinct values) or "Valuta" (EUR only).
    """
    samples = series.dropna().astype(str).str.strip()
    samples = samples[samples != ""]
    n = len(samples)
    if n == 0:
        return True
    n_distinct = samples.nunique()
    return n_distinct <= 5 or (n_distinct / n) <= 0.03


def _classify_column_content(
    series: pd.Series,
    amount_plausibility_cap: float = _AMOUNT_PLAUSIBILITY_CAP_DEFAULT,
) -> str:
    """Return 'date', 'amount', or 'text' by inspecting actual cell values.

    Samples up to 30 non-null, non-empty values.  A column is classified as
    'date' or 'amount' only when at least _CONTENT_MIN_RATIO of samples match
    the respective pattern AND at least _CONTENT_MIN_SAMPLE values are present.
    A column whose median absolute numeric value exceeds *amount_plausibility_cap*
    is rejected as a reference/ID column even if it matches the amount pattern.
    Everything else is 'text'.
    """
    samples = series.dropna().astype(str).str.strip()
    samples = samples[samples != ""].head(30)
    n = len(samples)
    if n < _CONTENT_MIN_SAMPLE:
        return "text"

    date_hits   = samples.apply(lambda v: bool(_CONTENT_DATE_RE.match(v))).sum()
    amount_hits = samples.apply(lambda v: bool(_CONTENT_AMOUNT_RE.match(v))).sum()

    if date_hits / n >= _CONTENT_MIN_RATIO:
        return "date"
    if amount_hits / n >= _CONTENT_MIN_RATIO:
        # Plausibility check: reference/ID columns contain large integers that
        # match the amount regex but are not actual monetary values.
        # Parse the matched samples and reject if median absolute value exceeds cap.
        numeric_vals: list[float] = []
        for v in samples:
            if _CONTENT_AMOUNT_RE.match(v):
                cleaned = re.sub(r'[€$£¥₹\s]', '', v)
                # Normalise European thousands separator (e.g. "1.234,56" → "1234.56")
                if '.' in cleaned and ',' in cleaned:
                    if cleaned.rfind('.') < cleaned.rfind(','):
                        cleaned = cleaned.replace('.', '').replace(',', '.')
                    else:
                        cleaned = cleaned.replace(',', '')
                elif ',' in cleaned:
                    parts = cleaned.split(',')
                    if len(parts) == 2 and len(parts[1]) <= 2:
                        cleaned = cleaned.replace(',', '.')
                    else:
                        cleaned = cleaned.replace(',', '')
                try:
                    numeric_vals.append(abs(float(cleaned)))
                except ValueError:
                    pass
        if numeric_vals:
            median_val = sorted(numeric_vals)[len(numeric_vals) // 2]
            if median_val > amount_plausibility_cap:
                return "text"
        return "amount"
    return "text"


def _assign_debit_credit_roles(
    df: pd.DataFrame, c1: str, c2: str, d1: float, d2: float,
) -> tuple[str, str]:
    """Deterministically assign debit/credit roles to two complementary columns.

    Strategy (language-agnostic, inspects actual values):
    1. Parse numeric values for each column.
    2. Column with negative values → debit (expenses/outflows).
    3. Column with only positive values → credit (income/inflows).
    4. If both have negatives or neither does, the denser column is debit
       (in a typical bank account, expenses outnumber income).

    Returns (debit_col, credit_col).
    """
    def _has_negatives(col_name: str) -> bool:
        vals = pd.to_numeric(
            df[col_name].astype(str)
            .str.replace(r"[€$£\s]", "", regex=True)
            .str.replace(",", ".", regex=False),
            errors="coerce",
        ).dropna()
        return (vals < 0).any()

    c1_neg = _has_negatives(c1)
    c2_neg = _has_negatives(c2)

    logger.info(
        "Phase 0 role assignment: '%s' has_neg=%s density=%.0f%% | '%s' has_neg=%s density=%.0f%%",
        c1, c1_neg, d1 * 100, c2, c2_neg, d2 * 100,
    )

    if c1_neg and not c2_neg:
        # c1 has negatives → debit (expenses), c2 → credit (income)
        return c1, c2
    elif c2_neg and not c1_neg:
        return c2, c1
    else:
        # Ambiguous sign → denser column is debit (more expense rows)
        logger.info(
            "Phase 0 role assignment: ambiguous sign, using density tiebreak "
            "(denser='%s' → debit)", c1 if d1 >= d2 else c2,
        )
        if d1 >= d2:
            return c1, c2
        return c2, c1


def _run_step0_analysis(
    columns: list[str],
    df_raw: pd.DataFrame | None = None,
    amount_plausibility_cap: float = _AMOUNT_PLAUSIBILITY_CAP_DEFAULT,
) -> _Step0Result:
    """Deterministic pre-LLM column analysis.

    Primary strategy (when df_raw is provided): inspect actual cell values to
    classify each column as 'date', 'amount', or 'text'.
      - All text columns  → description_cols (concatenated as description)
      - Date columns      → date_col / date_accounting_col
      - Amount columns    → amount_col / debit_col + credit_col

    Column-name synonyms are used only as tiebreakers to assign roles within
    the same content type (e.g. which date column is the operation date vs the
    accounting/value date, and whether an amount column is debit/credit/neutral).

    Falls back to pure name-synonym matching when df_raw is None or when the
    data sample is too small to reach the confidence threshold.
    """
    r = _Step0Result()
    # Coerce all column names to str — Excel files may have datetime/numeric headers
    columns = [str(c) for c in columns]
    lower_to_orig: dict[str, str] = {c.lower(): c for c in columns}

    # ── Content-type classification ───────────────────────────────────────────
    col_type: dict[str, str] = {}  # original col name → 'date'|'amount'|'text'
    if df_raw is not None:
        for col in columns:
            if col in df_raw.columns:
                col_type[col] = _classify_column_content(
                    df_raw[col], amount_plausibility_cap=amount_plausibility_cap
                )

    date_cols_found   = [c for c in columns if col_type.get(c) == "date"]
    amount_cols_found = [c for c in columns if col_type.get(c) == "amount"]
    # Exclude categorical columns (few distinct values — enum/flag, not free text)
    text_cols_found   = [
        c for c in columns
        if col_type.get(c) == "text"
        and (df_raw is None or not _is_categorical(df_raw[c]))
    ]

    # ── Description columns (all text columns) ────────────────────────────────
    if text_cols_found:
        r.description_cols = text_cols_found
        r.description_col  = text_cols_found[0]
    else:
        # Fallback: synonym matching on column names
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
            r.description_col  = desc_candidates[0]
            r.description_cols = desc_candidates

    # ── Date columns ──────────────────────────────────────────────────────────
    if date_cols_found:
        # Tiebreaker: use name synonyms to distinguish operation vs accounting date
        op_date = next(
            (c for c in date_cols_found if any(syn in c.lower() for syn in _DATE_OP_SYNONYMS)),
            None,
        )
        acc_date = next(
            (c for c in date_cols_found if any(syn in c.lower() for syn in _DATE_ACC_SYNONYMS)),
            None,
        )
        if op_date and acc_date:
            r.date_col = op_date
            r.date_accounting_col = acc_date
        elif op_date:
            r.date_col = op_date
            # second date column (if any) is likely accounting date
            others = [c for c in date_cols_found if c != op_date]
            if others:
                r.date_accounting_col = others[0]
        elif acc_date:
            # Only an accounting date found — promote it to date_col
            r.date_col = acc_date
        else:
            # No name hints — first detected date is operation date
            r.date_col = date_cols_found[0]
            if len(date_cols_found) > 1:
                r.date_accounting_col = date_cols_found[1]
    else:
        # Fallback: synonym matching on column names
        for col_low, col_orig in lower_to_orig.items():
            if r.date_col is None and any(syn in col_low for syn in _DATE_OP_SYNONYMS):
                r.date_col = col_orig
            if r.date_accounting_col is None and any(syn in col_low for syn in _DATE_ACC_SYNONYMS):
                r.date_accounting_col = col_orig
        if r.date_col is None:
            for prefix in ("data", "date", "datum", "fecha"):
                for col_low, col_orig in lower_to_orig.items():
                    if col_low.startswith(prefix):
                        r.date_col = col_orig
                        break
                if r.date_col:
                    break

    # ── Amount / sign columns ─────────────────────────────────────────────────
    # PRIMARY: density-based analysis (language-agnostic).
    # SECONDARY: name synonyms only as fallback for role assignment.
    if amount_cols_found and len(amount_cols_found) >= 2 and df_raw is not None:
        # Check for complementary density pattern (e.g. Entrate/Uscite split
        # where each row fills only one column).  This is language-agnostic.
        densities = {c: df_raw[c].notna().mean() for c in amount_cols_found}
        logger.info(
            "Phase 0: amount column densities: %s",
            {c: f"{d:.0%}" for c, d in densities.items()},
        )
        # Try all pairs when > 2 amount columns.
        # Complementary = sum ≈ 1.0 (>0.85) and neither is 100% (<0.98).
        # A bank account may have 90% expenses / 10% income — perfectly normal.
        best_pair = None
        best_complement = 0.0
        for i, c1 in enumerate(amount_cols_found):
            for c2 in amount_cols_found[i + 1:]:
                d1, d2 = densities[c1], densities[c2]
                complement = d1 + d2
                if complement > 0.85 and max(d1, d2) < 0.98 and complement > best_complement:
                    best_pair = (c1, c2, d1, d2)
                    best_complement = complement

        if best_pair:
            c1, c2, d1, d2 = best_pair
            # Assign roles DETERMINISTICALLY by inspecting actual values:
            # - Column with negative values → debit (expenses)
            # - Column with positive/no-neg values → credit (income)
            # If both or neither have negatives, the denser column is debit.
            debit_col, credit_col = _assign_debit_credit_roles(
                df_raw, c1, c2, d1, d2
            )
            r.debit_col = debit_col
            r.credit_col = credit_col
            r.amount_semantics = "debit_positive"
            logger.info(
                "Phase 0: complementary density RESOLVED — "
                "debit_col='%s' (%.0f%%), credit_col='%s' (%.0f%%) → debit_positive",
                debit_col, densities[debit_col] * 100,
                credit_col, densities[credit_col] * 100,
            )
        else:
            # No complementary pattern; fall back to single column
            r.amount_col = amount_cols_found[0]
            r.amount_semantics = "neutral"
            logger.info(
                "Phase 0: no complementary pattern found among %s → single amount_col='%s'",
                list(densities.keys()), r.amount_col,
            )
    elif amount_cols_found and len(amount_cols_found) == 1:
        r.amount_col = amount_cols_found[0]
        r.amount_semantics = "neutral"
    elif not amount_cols_found:
        # No content-detected amount columns — leave everything unresolved
        # for the LLM (Phase 1) to determine from column names and sample data.
        r.amount_semantics = "unclear"

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
        lines.append("- sign_convention = 'debit_positive'  [RESOLVED by density + sign analysis]")
        lines.append(f"- debit_col = '{r.debit_col}'  [RESOLVED — expenses/outflows column]")
        lines.append(f"- credit_col = '{r.credit_col}'  [RESOLVED — income/inflows column]")
        lines.append("- amount_col: not applicable (using debit/credit split)")
        lines.append("- invert_sign: not applicable (debit_positive convention)")
    elif r.amount_semantics == "debit_positive_candidates":
        lines.append("- sign_convention = 'debit_positive'  [RESOLVED by density analysis]")
        lines.append(
            f"- Two complementary amount columns detected: '{r.debit_col}' and '{r.credit_col}'"
        )
        lines.append(
            "- IMPORTANT: assign debit_col and credit_col based on column names and sample values. "
            "The column with expenses/outflows is debit_col; the column with income/inflows is credit_col."
        )
        lines.append("- invert_sign: not applicable (debit_positive convention)")
    elif r.amount_col:
        lines.append(
            f"- amount_col = '{r.amount_col}'  "
            f"[RESOLVED, semantics={r.amount_semantics}]"
        )
        # Add density hint if we have column density info
        if hasattr(r, '_density_info') and r._density_info:
            lines.append(f"  Column density: {r._density_info}")
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
        _cc  = out.get("currency_col")
        _dac = step0.date_accounting_col
        if (
            isinstance(_cc, str)
            and isinstance(_dac, str)
            and _cc.lower() == _dac.lower()
        ):
            logger.info(
                f"classify_document [{source_name}]: clearing currency_col "
                f"'{_cc}' — it's the value-date column"
            )
            out["currency_col"] = None

    # Amount / sign — override when Phase 0 resolved it
    if step0.amount_semantics == "debit_positive":
        _set("sign_convention", "debit_positive", "debit+credit columns found (density + sign)")
        if step0.debit_col:
            _set("debit_col", step0.debit_col, "deterministic: sign analysis")
        if step0.credit_col:
            _set("credit_col", step0.credit_col, "deterministic: sign analysis")
        # Clear amount_col — with debit/credit split it's not used and would
        # cause income rows to be lost (the exact bug we're fixing).
        if out.get("amount_col"):
            logger.info(
                "classify_document [%s]: Step 0 merge — clearing amount_col='%s' "
                "(debit/credit split takes precedence)",
                source_name, out.get("amount_col"),
            )
            out["amount_col"] = None
    elif step0.amount_semantics == "debit_positive_candidates":
        # Density detected the split pattern; sign_convention is debit_positive.
        # LLM assigns which candidate is debit and which is credit.
        _set("sign_convention", "debit_positive", "density complementary pattern")
        # LLM's debit_col/credit_col take precedence (it sees column names+values).
        # Only fill in from Phase 0 if the LLM left them blank.
        if not out.get("debit_col"):
            _set("debit_col", step0.debit_col, "density candidate (LLM did not assign)")
        if not out.get("credit_col"):
            _set("credit_col", step0.credit_col, "density candidate (LLM did not assign)")
    else:
        if step0.invert_sign is not None:
            _set("invert_sign", step0.invert_sign, f"semantics={step0.amount_semantics}")
        if step0.amount_col and not out.get("amount_col"):
            _set("amount_col", step0.amount_col, "deterministic match")

    return out


# ── Post-LLM safety net ───────────────────────────────────────────────────────


def _apply_step0_invert_sign(
    result: dict, source_name: str, account_type: str | None = None,
) -> dict:
    """Post-merge safety net: re-enforce invert_sign from doc_type or account_type.

    Runs after _merge_step0_into_result so both the LLM's doc_type and Phase 0
    column findings are available.  Only applies when sign_convention == signed_single.

    Rule: credit_card doc_type → invert_sign=True always (positive=charge,
    negative=payment).  If account_type == "credit_card", force invert_sign=True
    regardless of doc_type.  All other role assignments come from the LLM (Phase 1)
    — no language-dependent synonym matching here.
    """
    out = dict(result)

    convention = str(out.get("sign_convention", "")).lower()
    if convention not in ("signed_single", ""):
        return out

    doc_type = str(out.get("doc_type", "")).lower()

    # credit card → charges are positive → must invert
    if doc_type in _CREDIT_CARD_DOC_TYPES:
        if not out.get("invert_sign"):
            logger.info(
                f"classify_document [{source_name}]: safety-net — "
                f"doc_type=credit_card → invert_sign=True"
            )
            out["invert_sign"] = True

    # account_type constraint: credit_card → force invert_sign
    if account_type == "credit_card" and not out.get("invert_sign"):
        logger.info(
            f"classify_document [{source_name}]: account_type=credit_card "
            f"→ forcing invert_sign=True"
        )
        out["invert_sign"] = True

    return out


def _coerce_column_names(result: dict, available: list[str], source_name: str) -> dict:
    """For every column-mapping field in result, ensure the value is an actual column
    in `available`. Tries case-insensitive match first; nullifies on no match.
    Logs a warning for each correction so debugging is easy.
    """
    available = [str(c) for c in available]
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
