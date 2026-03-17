# Spendify — Processing Pipeline

> Technical reference document. Every line of code that transforms a transaction passes through these stages, in this order.

---

## High-level map

```
FILE (CSV / XLSX)
        │
        ▼
┌───────────────────┐
│  1. LOADING       │  parse bytes, encoding, delimiter, header
└────────┬──────────┘
         │
         ▼
┌────────────────────────┐       schema in DB?
│  2. SCHEMA DECISION    │──────────────────────────────┐
└────────┬───────────────┘                              │
         │ no (Flow 2)                                  │ yes (Flow 1)
         ▼                                              │
┌────────────────────────┐                              │
│  2b. DOCUMENT          │  LLM → DocumentSchema        │
│      CLASSIFICATION    │                              │
│      [RF-01]           │                              │
└────────┬───────────────┘                              │
         └──────────────────────────┬───────────────────┘
                                    │
                                    ▼
                     ┌──────────────────────────┐
                     │  3. NORMALISATION         │  dates, amounts, SHA-256 ID
                     │     [RF-02]               │  transaction type
                     └────────────┬─────────────┘
                                  │
                                  ▼
                     ┌──────────────────────────┐
                     │  4. DEDUP CHECK           │  skip already-imported txs
                     │                           │  (ID calculated at step 3)
                     └────────────┬─────────────┘
                                  │  ← no LLM call on already-known txs
                                  ▼
                     ┌──────────────────────────┐
                     │  5. DESCRIPTION CLEANING  │  LLM extracts counterparty
                     │     [RF-02 pre-cat.]      │  (payer or payee)
                     └────────────┬─────────────┘
                                  │
                                  ▼
                     ┌──────────────────────────┐
                     │  6. INTERNAL TRANSFER     │  amount+date matching
                     │     DETECTION [RF-04]     │  or owner name
                     └────────────┬─────────────┘
                                  │
                                  ▼
                     ┌──────────────────────────┐
                     │  7. CARD RECONCILIATION   │  credit card ↔ debit
                     │     [RF-03]               │  on current account
                     └────────────┬─────────────┘
                                  │
                                  ▼
                     ┌──────────────────────────┐
                     │  8. CATEGORISATION        │  rules → LLM → fallback
                     │     [RF-05]               │
                     └────────────┬─────────────┘
                                  │
                                  ▼
                     ┌──────────────────────────┐
                     │  9. DB PERSISTENCE        │  idempotent upsert
                     │     [RF-06, RF-07]        │
                     └────────────┬─────────────┘
                                  │
                                  ▼
                     ┌──────────────────────────┐
                     │  10. MANUAL REVIEW        │  to_review=True → user
                     │      + RULES [RF-08]      │  → re-apply rules
                     │                           │
                     │      UI Pages:            │
                     │      • Review             │
                     │      • Bulk edits         │
                     └──────────────────────────┘
```

---

## Stage 1 — File loading

**Module:** `core/normalizer.py` → `load_raw_dataframe()`

```
detect_encoding(raw_bytes)
  └─ chardet → normalised alias (ascii → utf-8)

For XLSX / XLS:
  detect_best_sheet(workbook)
    └─ excludes sheets named summary/totale/riepilogo
    └─ score = n_rows + (n_numeric_columns × 10)
  pd.read_excel(sheet)

For CSV / text:
  detect_delimiter(content)
    └─ character frequency [, ; | TAB] → most frequent wins
  detect_header_row(lines)
    └─ first row with ≥ 2 non-numeric and non-empty fields
  pd.read_csv(sep=delimiter, skiprows=skip_rows)
```

After loading (both CSV and Excel), Phase 0 pre-processing is applied:

```
detect_and_strip_preheader_rows(df)
  └─ counts non-null cells per row → computes median → threshold = median × 0.5
  └─ contiguous rows at the top with density < threshold → removed (max 20 rows / 10%)
  └─ first non-sparse row becomes the new column header

drop_low_variability_columns(df)
  └─ for each column: nunique(col) / n_rows < 1.5% → metadata column
  └─ candidate columns removed (never drops below 2 columns)
```

**Output:** cleaned `DataFrame` + `PreprocessInfo(skipped_rows, dropped_columns)`

---

## Schema fingerprinting — header SHA256

**Module:** `core/normalizer.py` → `compute_header_sha256()`, `load_raw_head()`

To avoid running the LLM classifier on every import of the same file format, Spendify computes the SHA256 of the first `min(30, N)` raw rows (before any skip or pre-processing) and stores it alongside the confirmed schema.

```
compute_header_sha256(raw_bytes, filename, n=30)
  └─ Excel: first min(30, N) rows of the best sheet → serialised with "|" between cells
  └─ CSV:   first min(30, N) raw text rows
  └─ SHA256(content.encode()) → 64-character hex string

load_raw_head(raw_bytes, filename, n=10)
  └─ loads N rows without skiprows, without preprocessing
  └─ used by the schema review UI to show the raw structure of the file
```

**Algorithm on re-import:**
1. Compute `header_sha256` of the first min(30, N) raw rows
2. DB query: `SELECT * FROM document_schema WHERE header_sha256 = ?`
3. If found → use saved schema (includes `skip_rows`) → skip classifier and review UI
4. If not found → Flow 2 (LLM classification + mandatory review UI)

**Why the first rows?** Bank statement files typically contain static institutional header rows (bank name, account number, date range) that are identical across all monthly exports from the same institution. These rows are a reliable fingerprint of the format.

**`skip_rows_override`** — `load_raw_dataframe` accepts an optional parameter `skip_rows_override: int | None`. If provided:
- CSV: replaces `detect_header_row()`
- Excel: passes `skiprows=N` to `pd.read_excel` and skips `detect_and_strip_preheader_rows()`

---

## Stage 2 — Schema decision / Document classification [RF-01]

**Module:** `core/orchestrator.py`, `core/classifier.py`

### Flow 1 — schema already in DB

```
_schema_is_usable(known_schema)
  └─ requires: date_col AND (amount_col OR (debit_col AND credit_col))
  └─ if valid → skip classification
```

### Flow 2 — new source, LLM required

```
classify_document(df_raw, llm_backend)

  PHASE 0 — Python, deterministic
    └─ Column synonyms (no LLM):
       date_col   → data, date, data operazione, buchungsdatum, …
       amount_col → importo, amount, betrag, montant, …
       debit/credit → dare/avere, addebiti/accrediti, uscite/entrate, …
       description → descrizione, causale, memo, payee, …

  PHASE 0.5 — Sign inspection
    └─ If amount_col semantics "neutral":
       reads actual data → if any value < 0 → invert_sign=False certain

  PHASE 1 — LLM, ambiguous fields
    input:
      - column names
      - first 20 rows (sensitive data redacted)
      - Phase 0 results (as certain facts)
    JSON output:
      {
        doc_type:   bank_account | credit_card | debit_card | prepaid_card | savings | unknown
        date_format: strptime pattern (e.g. %d/%m/%Y)
        sign_convention: signed_single | debit_positive | credit_negative
        invert_sign: true/false  (cards: expenses typically positive in CSV)
        internal_transfer_patterns: ["bonifico", "giroconto", …]
      }

  POST-LLM — Phase 0 overrides LLM
    └─ merge: certain Phase 0 results overwrite the LLM
    └─ safety: if doc_type = card → invert_sign=True forced
```

**Output:** `DocumentSchema` with column mapping and sign conventions

### Schema review gate (Flow 2 mandatory)

On the first import of an unknown file (header SHA256 not found in DB), the import always stops — regardless of the classifier's confidence — and shows the user a review form with:

- **Raw preview**: first 10 rows of the file without preprocessing (via `load_raw_head()`)
- **skip_rows selector**: number input "Rows to skip before the header" — pre-populated with the automatically detected value, editable by the user
- **Schema fields**: doc_type, account_label, amount column, date, sign, debits/credits, invert sign
- **Parsed preview**: first 8 transactions processed with the current schema — updates live on every change
- **"Confirm and import" button**: saves the schema (with `header_sha256`) and starts the import

From the second import of the same format, the `header_sha256` is found in DB and the entire process is automatic (no LLM call, no UI).

---

## Stage 3 — Normalisation [RF-02]

**Module:** `core/normalizer.py` → `_normalize_df_with_schema()`

For each row of the DataFrame:

```
parse_date_safe(value, format)
  └─ tries schema format → fallback to common IT/ISO/US formats
  └─ None if it fails (row discarded)

apply_sign_convention(row, convention)
  ├─ signed_single:    uses amount_col as-is
  ├─ debit_positive:   credit − debit  (both positive in CSV)
  └─ credit_negative:  credit as-is, −|debit|

parse_amount(value)
  ├─ "1.234,56" (EU)  → 1234.56
  ├─ "1,234.56" (US)  → 1234.56
  └─ "1234,56"        → 1234.56

normalize_description(text)
  └─ NFC unicode + casefold + strip

compute_transaction_id(account_label, raw_date, raw_amount, raw_description)
  └─ SHA-256[:24] on RAW values
  └─ stable across normalisation versions

_infer_tx_type(amount, doc_type, description, internal_patterns)
  ├─ matches internal_patterns → internal_out (< 0) / internal_in (≥ 0)
  ├─ credit card / debit card / prepaid card → card_tx
  └─ otherwise: income (≥ 0) / expense (< 0)
```

**Intra-file dedup:**
```
Rows with the same (account_label + date + amount + description)
  → sum amounts, recompute hash
  (avoids double counting if the same tx appears multiple times in the export)
```

**Card balance row removal:**
```
remove_card_balance_row(txs, epsilon)
  └─ detects the row whose |amount| ≈ Σ|other amounts|
  └─ with owner_label → renames description (internal transfer detection captures it)
  └─ without owner_label → removes the row
```

**Output:** list of `dict` transactions with all canonical fields, immutable `raw_description`

---

## Stage 4 — Dedup check

**Module:** `db/repository.py` → `get_existing_tx_ids()`

> Transaction IDs are calculated at step 3 from raw values, so dedup
> happens **before any LLM call**: no tokens wasted on already-imported txs.

```
existing_ids = query DB WHERE id IN (all_ids_in_batch)
→ filters already-present txs
→ if all present → abort early (file already imported, zero LLM calls)
→ continues only with new txs
```

---

## Stage 5 — Description cleaning [RF-02, pre-categorisation]

**Module:** `core/description_cleaner.py` → `clean_descriptions_batch()`

Extracts the **counterparty** name from the bank's raw string.

```
Split by sign:
  expenses (amount < 0) → PASS 1: extract RECIPIENT
  income (amount ≥ 0)   → PASS 2: extract SENDER

Privacy (mandatory before every LLM call):
  redact_pii(description, sanitize_config)
    ├─ Owner names → plausible fictitious names (pool by language)
    │    IT: Carlo Brambilla, Marta Pellegrino, …
    │    EN: James Fletcher, Helen Norris, …
    │    DE: Klaus Hartmann, Monika Braun, …
    │    FR: Pierre Dumont, Claire Lebrun, …
    ├─ IBAN → <ACCOUNT_ID>
    ├─ PAN / card (13-19 digits) → <CARD_ID>
    ├─ Masked card (****0178) → <CARD_ID>
    ├─ Transaction codes (CAU, NDS, CRO, RIF, TRN…) → <TX_CODE>
    └─ Tax code → <FISCAL_ID>

  LLM processes redacted description

  restore_owner_placeholders(llm_result)
    └─ maps fictitious names → real owner names back
```

**What the LLM must remove:**
```
- Payment type labels: POS, Bonifico, Virement, Lastschrift, SCT, wire transfer
- Beneficiary markers: Fv., F.V., Beg., Begünstigter, Pour, For the benefit of
- VOSTRA DISPOSIZIONE, Disposizione
- Amounts and currencies: "352,00 EUR", "9.798,76 EUR"
- Dates: "23.12.2025", "2025-12-29", "29/10.41"
- Card numbers, auth codes (CAU/NDS), references (RIF:/CRO:/INV/)
- ORD. tokens, country codes (ITA)(FRA)
- City names after the company name
- Duplicate phrases: "Expense reimbursement expense reimbursement" → "Expense reimbursement"
```

**Bank-originated expenses** (no external counterparty):
```
→ label in the configured language:
   IT: "Interessi bancari", "Commissioni bancarie"
   EN: "Bank fees", "Bank interest"
   FR: "Frais bancaires", "Intérêts bancaires"
   DE: "Bankgebühren", "Bankzinsen"
```

**Fallback:** if LLM fails → keep original `raw_description`

**Output:** `transaction["description"]` updated; `raw_description` never modified

---

## Stage 6 — Internal transfer detection [RF-04]


**Module:** `core/normalizer.py` → `detect_internal_transfers()`

```
PHASE 1 — Matching between different accounts
  For every pair (i, j) with i.account_label ≠ j.account_label:

    amount_match = |amount_i + amount_j| ≤ epsilon          (0.01 €)
    date_match   = |date_i − date_j| ≤ delta_days           (5 days)

    If both:
      high_symmetry = |amount_i + amount_j| ≤ epsilon_strict (0.005 €)
                    AND |date_i − date_j| ≤ delta_days_strict (1 day)

      Confidence:
        HIGH   → keyword "bonifico/giroconto/transfer/…" in description
        MEDIUM → high_symmetry without keyword

      If require_keyword_confirmation=True AND confidence=MEDIUM:
        → marks transfer_pair_id but does NOT update tx_type (to_review)
      Otherwise:
        → updates tx_type: internal_out (outgoing) / internal_in (incoming)

PHASE 2 — Match by owner name (txs not yet paired)
  For every tx without a pair:
    If the description contains an owner name
    (regex with all permutations of the name tokens):
      → tx_type = internal_out / internal_in
      → transfer_confidence = HIGH
      (the owner is the counterparty: no pairing needed)
```

**Key parameters:**

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `tolerance` | 0.01 € | amount epsilon |
| `tolerance_strict` | 0.005 € | strict epsilon |
| `settlement_days` | 5 days | date window |
| `settlement_days_strict` | 1 day | strict window |

---

## Stage 7 — Card reconciliation [RF-03]

**Module:** `core/normalizer.py` → `find_card_settlement_matches()`

Matches `card_settlement` debits (from the current account) to individual `card_tx` entries (from the card).

```
For every debit:

  PHASE 1 — Time window
    └─ card_tx in [debit_date − 45 days, debit_date + 7 days]

  PHASE 2 — Sliding window (contiguous subsets)
    For every contiguous subset [i..j]:
      ├─ verify gap between consecutive txs ≤ max_gap_days (5 days)
      ├─ sum = Σ |amount[i..j]|
      └─ If |sum − debit_amount| ≤ epsilon → MATCH ✓

  PHASE 3 — Boundary subset sum (fallback)
    ├─ takes the k=10 txs before + k=10 txs after the debit date
    ├─ exhaustive search over all subsets (n ≤ 20 → 2^20 ≈ 1M, safe)
    └─ If any subset sums to the amount → MATCH ✓

  If MATCH found:
    → ReconciliationLink {settlement_id, matched_ids, delta, method}
    → matched txs: reconciled=True
```

---

## Stage 8 — Categorisation [RF-05]

**Module:** `core/categorizer.py` → `categorize_batch()`

Processes only `expense`, `income`, `card_tx`, `unknown`. Skips internal transfers and card_settlement.

```
For each transaction — 4-level cascade:

  LEVEL 0 — User rules (CategoryRule, sorted by priority)
  ──────────────────────────────────────────────────────────────
  For each rule (in descending priority order):
    CategoryRule.matches(description, doc_type):
      ├─ exact:    description.casefold() == pattern.casefold()
      ├─ contains: pattern.casefold() IN description.casefold()
      └─ regex:    re.search(pattern, description.casefold())

    If doc_type specified in the rule → must match

    FIRST matching rule wins →
      category, subcategory, confidence=HIGH, source=rule, to_review=False

  LEVEL 1 — Static keyword rules (direction-aware)
  ───────────────────────────────────────────────────────────────
  Hardcoded patterns, separated by expense/income:

  EXPENSES:
    conad|coop|esselunga|lidl|carrefour|…  → Food / Grocery shopping
    farmacia|pharma|…                      → Health / Medicines
    eni|shell|q8|tamoil|…                  → Transport / Fuel
    telepass|autostrad|…                   → Transport / Parking and ZTL
    trenitalia|italo|frecciarossa|…        → Transport / Public transport
    enel|iren|a2a|hera|…                   → Home / Electricity
    netflix|spotify|amazon prime|…         → Leisure / Streaming

  INCOME:
    stipendio|salary|busta paga|…          → Employment / Salary
    pensione|inps rendita|…                → Social benefits / Pension

    → confidence=HIGH, source=rule, to_review=False

  LEVEL 2 — ML model (stub)
  ──────────────────────────
  → returns None (reserved for future development)

  LEVEL 3 — LLM (two directional batches)
  ────────────────────────────────────────
  Separate batches for expenses and income.

  Privacy:
    redact_pii(description) before sending to LLM

  Payload for each tx:
    {"amount": "−352.00", "description": "Notorious Cinemas"}

  Expected response:
    {
      "results": [
        {
          "category": "Leisure and free time",
          "subcategory": "Cinema and theatre",
          "confidence": "high",
          "rationale": "Cinema"
        },
        …
      ]
    }

  LLM response validation:
    ├─ valid category + subcategory in taxonomy?
    ├─ correct direction (expense for expenses, income for income)?
    ├─ If subcategory not found → look for parent category
    ├─ If category not found → first valid sub for that category
    └─ If correction needed → confidence=low, to_review=True

  Confidence levels:
    HIGH   → to_review=False
    MEDIUM → to_review=False (above threshold 0.80)
    LOW    → to_review=True

  LEVEL 4 — Fallback (everything fails)
  ──────────────────────────────────────
  expenses: category=Other,        sub=Unclassified expenses
  income:   category=Other income, sub=Unclassified income
  confidence=LOW, source=llm, to_review=True
```

---

## Stage 9 — DB Persistence [RF-06, RF-07]

**Module:** `db/repository.py` → `persist_import_result()`

Everything in an atomic transaction, every operation is idempotent:

```
create_import_batch(sha256, filename, flow_used, n_transactions)
  └─ if sha256 already exists → return existing (file already imported)

upsert_document_schema(schema)
  └─ if source_identifier exists → update; otherwise create

For each transaction:
  upsert_transaction(tx)
    └─ if tx.id exists → skip (final dedup)
    └─ otherwise: INSERT with all fields

For each reconciliation:
  create_reconciliation_link(settlement_id, detail_id, delta, method)
  update tx: reconciled=True

For each internal transfer:
  create_transfer_link(out_id, in_id, confidence, keyword_matched)

session.commit()
```

---

## Stage 10 — Manual review and rules [RF-08]

**Page:** `ui/review_page.py`, `ui/rules_page.py`

```
Auto-apply rules (on every Review page load):
  apply_rules_to_review_transactions(session, user_rules)
    └─ for each tx with to_review=True:
       └─ first matching rule →
          category, source=rule, to_review=False

"▶️ Run all rules" button (Rules page):
  apply_all_rules_to_all_transactions(session, user_rules)
    └─ applies all rules to ALL transactions (not only to_review=True)
    └─ rules in descending priority order, first match wins
    └─ returns (n_matched, n_cleared_review)
    └─ requires confirmation via checkbox before execution

"Reprocess with LLM" button (Review page):
  _rerun_llm_on_review(engine)
    └─ loads all txs with to_review=True
       (excluding internal transfers and card_settlement)
    └─ re-runs clean_descriptions_batch()
    └─ re-runs categorize_batch()
       (skips txs with category_source=manual or rule)

Manual correction:
  update_transaction_category(tx_id, category, sub)
    └─ category_source=manual, to_review=False

Rule creation:
  create_category_rule(pattern, match_type, category, sub, priority)
    └─ immediately propagates to all similar txs

Bulk description edit:
  _apply_description_rule_bulk(engine, pattern, match_type, new_desc)
    └─ updates description for all txs with matching raw_description
    └─ re-categorises with LLM
```

---

## Summary table of category sources

| Source (`category_source`) | Meaning | `to_review` |
|----------------------------|---------|-------------|
| `rule` | User rule or static keyword | `False` |
| `llm` confidence HIGH/MEDIUM | LLM above threshold | `False` |
| `llm` confidence LOW | LLM below threshold | `True` |
| `manual` | Manual user correction | `False` |
| `llm` fallback (Other) | Everything failed | `True` |

---

## Global configuration parameters

| Parameter | Default | Where to set |
|-----------|---------|--------------|
| `llm_backend` | `local_ollama` | Settings |
| `description_language` | `it` | Settings |
| `confidence_threshold` | 0.80 | `ProcessingConfig` |
| `tolerance` (transfer amount) | 0.01 € | `ProcessingConfig` |
| `settlement_days` | 5 days | `ProcessingConfig` |
| `window_days` (card reconciliation) | 45 days | `ProcessingConfig` |
| `require_keyword_confirmation` | `True` | `ProcessingConfig` |
| `owner_names` | — | Settings |
| `batch_size` (LLM) | 20 tx/call | `categorize_batch()` |
