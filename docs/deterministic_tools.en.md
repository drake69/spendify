# Spendify — Deterministic Tools

> Complete inventory of all algorithms, rules and transformations that do not use LLM.
> Each entry indicates: where it lives in the code, which pipeline stage it is applied at, whether it is automatic or requires user action.

---

## Position map in the pipeline

```
FILE (CSV / XLSX)
     │
     ▼  ── STAGE 1: LOADING ──────────────────────────────────────────────
     │   detect_encoding          (normalizer.py)
     │   detect_delimiter         (normalizer.py)  [CSV only]
     │   detect_header_row        (normalizer.py)  [CSV only]
     │   detect_best_sheet        (normalizer.py)  [Excel only]
     │
     ▼  ── STAGE 1b: PRE-PROCESSING (Phase 0) ────────────────────────────
     │   detect_and_strip_preheader_rows (normalizer.py)  [CSV + Excel]
     │   drop_low_variability_columns    (normalizer.py)  [CSV + Excel]
     │
     ▼  ── STAGE 2: SCHEMA / CLASSIFICATION ─────────────────────────────
     │   compute_columns_key      (normalizer.py)  [schema cache]
     │   compute_file_hash        (normalizer.py)  [import idempotency]
     │   [PHASE 0 classifier.py: deterministic column synonyms]
     │
     ▼  ── STAGE 3: NORMALISATION ──────────────────────────────────────
     │   parse_date_safe          (normalizer.py)
     │   parse_amount             (normalizer.py)
     │   apply_sign_convention    (normalizer.py)
     │   normalize_description    (normalizer.py)
     │   compute_transaction_id   (normalizer.py)  [SHA-256 dedup]
     │   [intra-file dedup: aggregation of identical rows]
     │   remove_card_balance_row  (normalizer.py)
     │
     ▼  ── STAGE 4: DEDUP CHECK ────────────────────────────────────────────
     │   get_existing_tx_ids      (repository.py)
     │   [IDs already calculated at step 3 → no LLM call on already-known txs]
     │
     ▼  ── STAGE 5: DESCRIPTION CLEANING ──────────────────────────────────
     │   redact_pii               (sanitizer.py)   [before LLM]
     │   restore_owner_aliases    (sanitizer.py)   [after LLM]
     │   [LLM output filter: discard "null","none","nan",…]
     │
     ▼  ── STAGE 6: INTERNAL TRANSFER DETECTION ────────────────────────────
     │   detect_internal_transfers (normalizer.py)
     │     ├─ Phase 1: amount + date matching
     │     └─ Phase 2: owner name match
     │
     ▼  ── STAGE 7: CARD RECONCILIATION ───────────────────────────────────
     │   find_card_settlement_matches (normalizer.py)
     │     ├─ Phase 1: time window
     │     ├─ Phase 2: sliding window (contiguous)
     │     └─ Phase 3: boundary subset sum
     │
     ▼  ── STAGE 8: CATEGORISATION (cascade) ──────────────────────────────
     │   _try_deterministic       (categorizer.py)
     │     ├─ Level 0: user rules (CategoryRule.matches)
     │     └─ Level 1: static rules (_STATIC_RULES)
     │   [if no match → LLM]
     │   [after LLM: deterministic taxonomy validation]
     │
     ▼  ── STAGE 9: DB PERSISTENCE ──────────────────────────────────────
     │   [idempotent upsert for every tx, link, schema]
     │
     ▼  ── STAGE 10: MANUAL REVIEW ──────────────────────────────────────
         apply_rules_to_review_transactions  (repository.py)  [auto on Review page load]
         apply_all_rules_to_all_transactions (repository.py)  [button "Run all rules"]
         _apply_description_rule_bulk        (review_page.py) [on user request]
         _rerun_transfer_detection           (review_page.py) [on user request]
```

---

## Full catalogue by stage

---

### Stage 1 — File loading

#### `detect_encoding` · `core/normalizer.py`
**Function:** detects the file encoding via `chardet`
**Input:** raw bytes of the file
**Output:** encoding string (e.g. `"utf-8"`, `"latin-1"`)
**Notes:** `ascii` is normalised to `utf-8`; default `utf-8` if chardet fails

#### `detect_delimiter` · `core/normalizer.py`
**Function:** counts the frequency of candidate characters and returns the most frequent
**Input:** CSV text content
**Output:** one of `,` `;` `\t` `|`
**Candidates (hardcoded):** `[",", ";", "\t", "|"]`

#### `detect_header_row` · `core/normalizer.py`
**Function:** finds the first row with ≥ 2 non-numeric and non-empty fields
**Input:** list of text rows
**Output:** row index (0 if not found)
**Numeric pattern (hardcoded):** `^[\d\.\,\-\+\s€$£%]+$`

#### `detect_best_sheet` · `core/normalizer.py`
**Function:** selects the Excel sheet with the most useful data
**Input:** Excel workbook object
**Output:** sheet name
**Logic:**
- Excludes sheets with names matching `summary|totale|riepilogo` (case-insensitive)
- Score = `n_rows + n_numeric_columns × 10`
- Threshold: > 50% of rows must have numeric values for a column to be considered numeric

---

### Stage 1b — Pre-processing Phase 0

#### `detect_and_strip_preheader_rows` · `core/normalizer.py`
**Function:** removes sparse metadata rows present *before* the actual transaction table header
**Input:** raw DataFrame + filename (for logging)
**Output:** `(cleaned DataFrame, n_rows_removed)`
**Algorithm:**
1. Reconstructs the header row consumed by pandas as row 0 (column names `Unnamed: N` are treated as empty cells)
2. Computes non-null density per row (number of non-null cells / number of columns)
3. Computes the median of densities
4. Counts contiguous rows from the start with density < `median × 0.5`
5. Safety limits: max **20 rows** absolute **AND** max **10%** of total → otherwise `ValueError`
6. Reassigns column names from the first non-sparse row

**Constants (hardcoded):**

| Constant | Value | Description |
|---|---|---|
| `_PREHEADER_MAX_ROWS` | 20 | Max absolute sparse rows before error |
| `_PREHEADER_MAX_RATIO` | 0.10 | Max % sparse rows relative to total |
| `_PREHEADER_DENSITY_THRESHOLD` | 0.5 | Median multiplier for sparse threshold |

**Edge cases:**
- DataFrame with < 4 rows → returns unchanged
- 0 sparse rows → returns unchanged (fast path)

**Rationale:** statistical and language-agnostic approach — does not use banking term dictionaries

---

#### `drop_low_variability_columns` · `core/normalizer.py`
**Function:** removes columns with nearly constant values (e.g. "Account holder name", "Card number" in AmEx files)
**Input:** DataFrame + filename (for logging)
**Output:** `(cleaned DataFrame, removed_columns_list)`
**Algorithm:** for each column computes `nunique(col) / n_rows`; if < threshold → candidate for removal
**Protection:** never drops below 2 columns (always preserves a minimum workable set)

**Constants (hardcoded):**

| Constant | Value | Description |
|---|---|---|
| `_LOW_VARIABILITY_RATIO` | 0.015 | Threshold: < 1.5% unique/nrows → metadata column |

**Edge cases:**
- DataFrame with < 2 rows → returns unchanged
- DataFrame with ≤ 2 columns → returns unchanged

---

### Stage 2 — Schema / document classification

#### `compute_columns_key` · `core/normalizer.py`
**Function:** generates a cache key for the DocumentSchema based on column names
**Input:** pandas DataFrame
**Output:** `"cols:" + SHA-256[:16]`
**Use:** same bank layout recognised across different files (e.g. `CARD_2025.xlsx` → `CARD_2026.xlsx`)

#### `compute_file_hash` · `core/normalizer.py`
**Function:** SHA-256 of the raw file
**Input:** file bytes
**Output:** hex string
**Use:** import-level idempotency (same file not re-imported)

#### Classifier Phase 0 — column synonyms · `core/classifier.py`
**Function:** deterministic mapping of column names to canonical fields
**Recognised synonyms (examples):**

| Canonical field | Synonyms (excerpt) |
|---|---|
| `date_col` | data, date, data operazione, buchungsdatum, fecha, date valeur |
| `amount_col` | importo, amount, betrag, montant, importe |
| `debit_col` | dare, addebiti, uscite, debit, ausgaben, débits |
| `credit_col` | avere, accrediti, entrate, credit, eingaben, crédits |
| `description_col` | descrizione, causale, memo, payee, verwendungszweck, libellé |

**Phase 0 always overrides Phase 1 (LLM)** — certain results from deterministic matching are not overwritten

---

### Stage 3 — Normalisation

#### `parse_date_safe` · `core/normalizer.py`
**Function:** date parsing with fallback to common formats
**Input:** raw string, primary format from schema
**Output:** `date` object or `None` (row discarded if `None`)
**Fallback formats (in order):**
```
%d/%m/%Y  %d-%m-%Y  %d/%m/%y  %d-%m-%y
%Y-%m-%d  %Y/%m/%d  %m/%d/%Y  %m/%d/%y
```

#### `parse_amount` · `core/normalizer.py`
**Function:** converts any amount representation to `Decimal`
**Input:** `str | float | int | Decimal`
**Output:** `Decimal` or `None`
**Removed symbols (hardcoded):** `€ $ £` and spaces
**Separator detection:**

| Format | Example | Rule |
|---|---|---|
| European | `1.234,56` | `.` = thousands, `,` = decimal |
| American | `1,234.56` | `,` = thousands, `.` = decimal |
| Comma only | `1234,56` | `,` = decimal if ≤ 2 fractional digits |
| Dot only | `1234.56` | `.` = decimal |

#### `apply_sign_convention` · `core/normalizer.py`
**Function:** produces the correctly-signed amount according to the file's convention
**Input:** row, columns, `SignConvention` enum
**Conventions:**

| Convention | Logic |
|---|---|
| `signed_single` | amount column already signed |
| `debit_positive` | credit − debit (both positive in CSV) |
| `credit_negative` | credit as-is, `−|debit|` |

#### `normalize_description` · `core/normalizer.py`
**Function:** text normalisation for stable case-insensitive comparison
**Input:** string
**Output:** `NFC_unicode(text).casefold().strip()`

#### `compute_transaction_id` · `core/normalizer.py`
**Function:** stable deduplication key for each transaction
**Input:** `account_label | source_file`, raw date, raw amount, raw description
**Output:** `SHA-256[:24]` of the string `"key|raw_date|raw_amount|raw_desc"`
**Why raw values:** stable across normalisation algorithm updates

#### Intra-file dedup
**Function:** aggregates identical rows (same account_label + date + amount + description)
**Output:** sums amounts, recomputes hash
**Use:** avoids double counting if the same tx appears multiple times in the bank export

#### `remove_card_balance_row` · `core/normalizer.py`
**Function:** detects and removes the "total balance" row present in some card files
**Detection condition:** `||amount_i| − Σ|other amounts|| ≤ epsilon`
**Requires:** ≥ 3 transactions in the file
**Behaviour:**
- With `owner_name_label` → renames the description with the owner's name (internal transfer detection then captures it)
- Without `owner_name_label` → removes the row (legacy: avoids double counting)

---

### Stage 4 — Dedup check

#### `get_existing_tx_ids` · `db/repository.py`
**Function:** DB query to find which IDs in the batch already exist
**Input:** list of transaction IDs (calculated at step 3 from raw values)
**Output:** set of already-present IDs
**Use:** filters already-imported txs; if all present → immediate abort, zero LLM calls

> This step was moved **before** description cleaning to avoid wasting
> LLM tokens on transactions that would be discarded anyway because they are already in the DB.

---

### Stage 5 — Description cleaning (deterministic wrapper around LLM)

#### `redact_pii` · `core/sanitizer.py`
**Function:** replaces sensitive data with placeholders or fictitious names **before** every LLM call
**Input:** text, `SanitizationConfig`
**Output:** redacted text

**Hardcoded patterns (precompiled regex):**

| Data | Pattern | Placeholder |
|---|---|---|
| IBAN | `\b[A-Z]{2}\d{2}[A-Z0-9]{4,30}\b` | `<ACCOUNT_ID>` |
| PAN / card (13-19 digits) | `\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{1,7}\b` | `<CARD_ID>` |
| Masked card | `[\*X]{4}[\s\-]?\d{4}` | `<CARD_ID>` |
| Bank codes (CAU, NDS, CRO, RIF…) | `\b(CAU\|NDS\|CRO\|RIF\|TRN\|ID\s*TRANSAZIONE)\s*[\d\-]+` | `<TX_CODE>` |
| IT tax code | `\b[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]\b` | `<FISCAL_ID>` |
| Additional user patterns | from `SanitizationConfig.extra_patterns` | `<REDACTED>` |

**Owner names → fictitious names (pool by language):**

| Language | Pool (6 names) |
|---|---|
| IT | Carlo Brambilla, Marta Pellegrino, Alberto Marini, Giovanna Ferrara, Luca Montanari, Silvia Cattaneo |
| EN | James Fletcher, Helen Norris, Robert Ashworth, Patricia Holt, Edward Tilman, Susan Delaney |
| FR | Pierre Dumont, Claire Lebrun, François Martel, Isabelle Renaud, Gilles Fontaine, Nathalie Girard |
| DE | Klaus Hartmann, Monika Braun, Werner Schulze, Ingrid Bauer, Dieter Hoffmann, Renate Fischer |
| ES | Carlos Navarro, Elena Vega, Javier Romero, Isabel Fuentes, Andrés Molina, Lucía Castillo |

All permutations of tokens are captured ("Luigi Corsaro" **and** "Corsaro Luigi")

#### `restore_owner_aliases` · `core/sanitizer.py`
**Function:** reverse operation — replaces fictitious names with the real owner names
**When:** after every LLM response
**Why:** internal transfer detection works on real names

#### LLM output filter · `core/description_cleaner.py`
**Function:** discards unusable LLM responses
**Discarded values (hardcoded):** `"null"`, `"none"`, `"n/a"`, `"na"`, `"nan"`, `"-"`, `"—"`
**Additional check:** discards if result == original description (no change)

---

### Stage 6 — Internal transfer detection [RF-04]

#### `detect_internal_transfers` · `core/normalizer.py`
**Function:** identifies pairs of transactions that cancel each other out (internal transfer between accounts)
**Run:** automatically on import + on user request from the Review page

**Phase 1 — Matching by amount + date**

For every pair `(i, j)` with `i.account_label ≠ j.account_label`:

```
amount_match = |amount_i + amount_j| ≤ epsilon         (default 0.01 €)
date_match   = |date_i − date_j| ≤ delta_days           (default 5 days)

high_symmetry = |amount_i + amount_j| ≤ epsilon_strict   (default 0.005 €)
              AND |date_i − date_j| ≤ delta_days_strict   (default 1 day)

Confidence:
  HIGH   → keyword present in description (list from user DB)
  MEDIUM → high_symmetry without keyword

With require_keyword_confirmation=True AND confidence=MEDIUM:
  → sets transfer_pair_id but does NOT update tx_type (stays in review queue)
Otherwise:
  → tx_type = internal_out (outgoing) / internal_in (incoming)
```

**Phase 2 — Owner name match** (for txs not yet paired)

```
If the description contains an owner name
(regex with all permutations of tokens):
  → tx_type = internal_out / internal_in
  → transfer_confidence = HIGH
```

**Configurable parameters:**

| Parameter | Default | Configurable |
|---|---|---|
| `epsilon` | 0.01 € | Yes |
| `epsilon_strict` | 0.005 € | Yes |
| `delta_days` | 5 days | Yes |
| `delta_days_strict` | 1 day | Yes |
| `keyword_patterns` | from DB | Yes (Settings page) |
| `owner_names` | from DB | Yes (Settings page) |
| `require_keyword_confirmation` | `True` | Yes |

---

### Stage 7 — Card reconciliation [RF-03]

#### `find_card_settlement_matches` · `core/normalizer.py`
**Function:** matches `card_settlement` rows (from the current account) to individual `card_tx` entries (from the card)
**Run:** automatically on import when both files are present

**Phase 1 — Time window filter**
```
Considers only card_tx in [debit_date − 45 days, debit_date + 7 days]
```

**Phase 2 — Sliding window (contiguous subsets)**
```
For every contiguous subset [i..j]:
  verify gap between consecutive txs ≤ max_gap_days (default 5 days)
  sum = Σ |amount[i..j]|
  If |sum − debit_amount| ≤ epsilon → MATCH ✓ (method="sliding_window")
```

**Phase 3 — Boundary subset sum (fallback)**
```
Takes k=10 txs before + k=10 txs after the debit date (total ≤ 20)
Exhaustive search over all subsets: O(2^20) ≈ 1M → safe
If a subset sums to the amount → MATCH ✓ (method="subset_sum")
```

**Configurable parameters:**

| Parameter | Default | Configurable |
|---|---|---|
| `epsilon` | 0.01 € | Yes |
| `window_days` | 45 days | Yes |
| `max_gap_days` | 5 days | Yes |
| `boundary_k` | 10 | Yes |

---

### Stage 8 — Categorisation (deterministic part)

#### `_try_deterministic` · `core/categorizer.py`
**Function:** applies the deterministic cascade (Levels 0 and 1) before calling LLM
**Output:** `CategorizationResult` or `None` (→ proceeds to LLM)

---

#### Level 0 — User rules (`CategoryRule.matches`)

**Object:** `CategoryRule` in `core/categorizer.py`
**Key fields:** `pattern`, `match_type`, `category`, `subcategory`, `doc_type`, `priority`

**Matching logic (case-insensitive, casefold):**

| `match_type` | Condition |
|---|---|
| `exact` | `description == pattern` |
| `contains` | `pattern in description` |
| `regex` | `re.search(pattern, description)` |

Optional filter on `doc_type` (e.g. only `credit_card`)
**Order:** descending priority — the **first** matching rule wins
**Result:** `confidence=HIGH`, `source=rule`, `to_review=False`

---

#### Level 1 — Static rules (hardcoded) · `core/categorizer.py`

10 hardcoded rules, ordered by transaction type and direction.
All case-insensitive, applied only to the correct direction (expense/income).

| # | Pattern | Category | Subcategory | Direction |
|---|---|---|---|---|
| 1 | `conad\|coop\|esselunga\|lidl\|carrefour\|eurospin\|aldi\|penny\|pam\b` | Food | Grocery shopping | Expense |
| 2 | `farmacia\|pharma` | Health | Medicines | Expense |
| 3 | `eni\b\|shell\|q8\|tamoil\|ip\b\|api\b\|agip` | Transport | Fuel | Expense |
| 4 | `telepass\|autostrad` | Transport | Parking and ZTL | Expense |
| 5 | `trenitalia\|italo\|frecciarossa\|frecciargento` | Transport | Public transport | Expense |
| 6 | `enel\b\|iren\b\|a2a\b\|hera\b\|eni gas` | Home | Electricity | Expense |
| 7 | `netflix\|spotify\|amazon prime\|disney\+\|apple tv` | Leisure and free time | Streaming / digital subscriptions | Expense |
| 8 | `commissione\|canone conto\|spese tenuta` | Finance and insurance | Bank fees | Expense |
| 9 | `stipendio\|salary\|busta paga` | Employment | Salary | Income |
| 10 | `pensione\|inps rendita` | Social benefits | Pension / annuity | Income |

**Result if match:** `confidence=HIGH`, `source=rule`, `to_review=False`

---

#### Post-LLM validation (deterministic) · `core/categorizer.py`

After every LLM response, before accepting the categorisation:

```
1. Is the (category, subcategory) pair valid in the taxonomy?
   NO → look for the subcategory's parent category (find_category_for_subcategory)
         IF not found → use the first valid subcategory for that category
         SET confidence=LOW, to_review=True

2. Is the category in the correct direction (expense/income)?
   NO → fallback (Other / Unclassified expenses), to_review=True

3. LLM confidence ≥ threshold (0.80)?
   NO → to_review=True
```

---

### Stage 10 — Manual review

#### `apply_rules_to_review_transactions` · `db/repository.py`
**Function:** applies user rules to all txs with `to_review=True`
**When:** automatically on every load of the Review page
**Logic:** descending priority, first match wins
**Effect:** `to_review=False`, `category_source=rule`

#### `apply_all_rules_to_all_transactions` · `db/repository.py`
**Function:** applies all user rules to **all** transactions (not only `to_review=True`)
**When:** on user request via "▶️ Run all rules" button on the Rules page
**Logic:** rules sorted by descending priority; first match wins; updates `category`, `subcategory`, `category_source=rule`, `category_confidence=high`; if `to_review=True` → sets `False`
**Effect:** returns `(n_matched, n_cleared_review)` — transactions updated and transactions removed from the review queue

#### `_apply_description_rule_bulk` · `ui/review_page.py`
**Function:** updates the description of all txs whose `raw_description` matches the pattern
**When:** on user request ("Apply in bulk" button)
**Deterministic steps:**
1. `get_transactions_by_raw_pattern(session, pattern, match_type)` → list of txs
2. Updates `tx.description = new_description` for each tx
3. Starts LLM re-categorisation (non-deterministic)

**Rule saving (DescriptionRule):** idempotent on `(raw_pattern, match_type)`

#### `_rerun_transfer_detection` · `ui/review_page.py`
**Function:** re-runs `detect_internal_transfers` on all non-internal-transfer txs in the DB
**When:** on user request ("Re-run transfer detection" button)
**Typical use:** after importing files from multiple different accounts

---

## Summary table

| # | Tool | Module | Stage | Auto? | Configurable? |
|---|---|---|---|---|---|
| 1 | `detect_encoding` | normalizer.py | 1 – Loading | ✓ | No |
| 2 | `detect_delimiter` | normalizer.py | 1 – Loading | ✓ | No |
| 3 | `detect_header_row` | normalizer.py | 1 – Loading | ✓ | No |
| 4 | `detect_best_sheet` | normalizer.py | 1 – Loading | ✓ | No |
| 4b | `detect_and_strip_preheader_rows` | normalizer.py | 1b – Pre-processing | ✓ | No |
| 4c | `drop_low_variability_columns` | normalizer.py | 1b – Pre-processing | ✓ | No |
| 5 | `compute_file_hash` | normalizer.py | 2 – Schema | ✓ | No |
| 6 | `compute_columns_key` | normalizer.py | 2 – Schema | ✓ | No |
| 7 | Classifier Phase 0 (synonyms) | classifier.py | 2 – Classification | ✓ | No |
| 8 | `parse_date_safe` | normalizer.py | 3 – Normalisation | ✓ | No |
| 9 | `parse_amount` | normalizer.py | 3 – Normalisation | ✓ | No |
| 10 | `apply_sign_convention` | normalizer.py | 3 – Normalisation | ✓ | Partial |
| 11 | `normalize_description` | normalizer.py | 3 – Normalisation | ✓ | No |
| 12 | `compute_transaction_id` | normalizer.py | 3 – Normalisation | ✓ | No |
| 13 | Intra-file dedup | normalizer.py | 3 – Normalisation | ✓ | No |
| 14 | `remove_card_balance_row` | normalizer.py | 3 – Normalisation | ✓ | `epsilon` |
| 15 | `get_existing_tx_ids` | repository.py | 4 – Dedup check | ✓ | No |
| 16 | `redact_pii` | sanitizer.py | 5 – Desc. cleaning | ✓ | Owner names, extra patterns |
| 17 | `restore_owner_aliases` | sanitizer.py | 5 – Desc. cleaning | ✓ | No |
| 18 | LLM output filter | description_cleaner.py | 5 – Desc. cleaning | ✓ | No |
| 19 | `detect_internal_transfers` | normalizer.py | 6 – Internal transfers | ✓ + manual | Yes (all) |
| 20 | `find_card_settlement_matches` | normalizer.py | 7 – Reconciliation | ✓ | Yes |
| 21 | User rules (`CategoryRule`) | categorizer.py | 8 – Categorisation | ✓ | Yes (user) |
| 22 | Static rules (`_STATIC_RULES`) | categorizer.py | 8 – Categorisation | ✓ | No |
| 23 | Post-LLM taxonomy validation | categorizer.py | 8 – Categorisation | ✓ | No |
| 24 | `apply_rules_to_review_transactions` | repository.py | 10 – Review | ✓ (on load) | No |
| 25 | `apply_all_rules_to_all_transactions` | repository.py | 10 – Review | Manual (button) | No |
| 26 | `_apply_description_rule_bulk` | review_page.py | 10 – Review | Manual | Yes (pattern) |
| 27 | `_rerun_transfer_detection` | review_page.py | 10 – Review | Manual | No |
| 28 | `get_transactions_by_rule_pattern` | repository.py | 10 – Review | On demand | No |
| 29 | `get_transactions_by_raw_pattern` | repository.py | 10 – Review | On demand | No |
