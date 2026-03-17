# Spendify — Deterministic Tools

> Complete inventory of all rules, algorithms and transformations that are **non-LLM** and implemented in the system.
> For each tool: where it lives, what it does, the hardcoded rules and the point of application in the pipeline.

---

## Pipeline map

```
FILE (CSV / XLSX)
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│  1. FORMAT DETECTION                                         │ ◄─ DETERMINISTIC
│     detect_encoding · detect_delimiter                       │
│     detect_header_row · detect_best_sheet                    │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│  1b. PRE-PROCESSING Phase 0                                  │ ◄─ DETERMINISTIC
│     detect_and_strip_preheader_rows                          │
│     drop_low_variability_columns                             │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│  2. DOCUMENT CLASSIFICATION — Phase 0                        │ ◄─ DETERMINISTIC
│     column synonyms · sign inspection                        │
└────────────────────────────┬────────────────────────────────┘
                             │ LLM for ambiguous fields (Phase 1)
                             ▼
┌─────────────────────────────────────────────────────────────┐
│  3. NORMALISATION                                            │ ◄─ DETERMINISTIC
│     parse_date_safe · parse_amount · apply_sign_convention   │
│     normalize_description · compute_transaction_id (SHA-256)│
│     _infer_tx_type · remove_card_balance_row                 │
└────────────────────────────┬────────────────────────────────┘
                             │  ID calculated here from raw values
                             ▼
┌─────────────────────────────────────────────────────────────┐
│  4. DEDUP CHECK                                              │ ◄─ DETERMINISTIC
│     get_existing_tx_ids (repository.py)                      │
│     → abort if all already in DB, zero wasted LLM calls      │
└────────────────────────────┬────────────────────────────────┘
                             │  only new txs proceed
                             ▼
┌─────────────────────────────────────────────────────────────┐
│  5. DESCRIPTION CLEANING                                     │
│     PRIVACY / PII REDACTION  ◄─ DETERMINISTIC               │
│     redact_pii · restore_owner_placeholders                  │
│     (applied BEFORE and AFTER every LLM call)                │
│                              ◄─ LLM (counterparty extraction)│
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│  6. INTERNAL TRANSFER DETECTION [RF-04]                      │ ◄─ DETERMINISTIC
│     detect_internal_transfers                                │
│     Phase 1: amount+date matching                            │
│     Phase 2: owner name matching                             │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│  7. CARD RECONCILIATION [RF-03]                              │ ◄─ DETERMINISTIC
│     find_card_settlement_matches                             │
│     sliding window · subset sum                              │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│  8. CATEGORISATION — Levels 0 and 1                          │ ◄─ DETERMINISTIC
│     Lv. 0: user rules (CategoryRule.matches)                 │
│     Lv. 1: static keyword rules                              │
└────────────────────────────┬────────────────────────────────┘
                             │ LLM only if no rule matches
                             ▼
┌─────────────────────────────────────────────────────────────┐
│  9. DB PERSISTENCE                                           │ ◄─ DETERMINISTIC
│     idempotent upsert · SHA-256 for file and transaction     │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│  10. REVIEW — auto-apply rules                               │ ◄─ DETERMINISTIC
│      apply_rules_to_review_transactions  (to_review=True)    │
│      apply_all_rules_to_all_transactions (all txs)           │
│      bulk description rules · DescriptionRule                │
└─────────────────────────────────────────────────────────────┘
```

---

## 1 — File format detection

**Module:** `core/normalizer.py`
**When:** stage 1, before any parsing

| Function | Hardcoded rule |
|----------|----------------|
| `detect_encoding(raw_bytes)` | chardet → normalises alias (`ascii` → `utf-8`) |
| `detect_delimiter(content)` | counts frequency of `,` `;` `\t` `\|` → most frequent wins |
| `detect_header_row(lines)` | first row with ≥ 2 non-numeric fields; numeric pattern: `^[\d\.\,\-\+\s€$£%]+$` |
| `detect_best_sheet(workbook)` | excludes sheets named `summary\|totale\|riepilogo`; score = rows + (numeric columns × 10) |

---

## 2 — Document classification — Phase 0

**Module:** `core/classifier.py`
**When:** stage 2 (Flow 2), only if source has no schema in DB

Resolves column fields **without LLM** via synonyms:

| Field | Recognised synonyms |
|-------|---------------------|
| `date_col` | data, date, data operazione, booking date, buchungsdatum, … |
| `amount_col` | importo, amount, betrag, montant, somme, … |
| `debit_col` | dare, addebiti, uscite, debit, ausgaben, … |
| `credit_col` | avere, accrediti, entrate, credit, einnahmen, … |
| `description_col` | descrizione, causale, memo, payee, bezeichnung, libellé, … |

**Sign inspection (Phase 0.5):**
If `amount_col` semantics "neutral" → reads actual data; if any value < 0 → `invert_sign=False` certain, no LLM needed.

---

## 3 — Normalisation

**Module:** `core/normalizer.py`, `core/orchestrator.py`
**When:** stage 3, after schema classification

### 3a — Date parsing

**`parse_date_safe(value, format)`**

1. Tries the schema format
2. Fallback to common formats (in order):
   `%d/%m/%Y` · `%d-%m-%Y` · `%d/%m/%y` · `%d-%m-%y` · `%Y-%m-%d` · `%Y/%m/%d` · `%m/%d/%Y` · `%m/%d/%y`
3. Returns `None` if everything fails (row discarded)

### 3b — Amount parsing

**`parse_amount(value)`**

```
Strip symbols: €  $  £  (spaces)

Separator heuristic:
  "1.234,56"  → dot = thousands, comma = decimal → 1234.56
  "1,234.56"  → comma = thousands, dot = decimal → 1234.56
  "1234,56"   → comma only with ≤ 2 decimal digits → 1234.56
  "1234.56"   → dot only with ≤ 2 decimal digits   → 1234.56
```

### 3c — Sign convention

**`apply_sign_convention(row, convention)`**

| Convention | Rule |
|------------|------|
| `signed_single` | uses `amount_col` as-is |
| `debit_positive` | `credit − debit` (both positive in CSV) |
| `credit_negative` | credit as-is positive; debit negated |

After: if `invert_sign=True` (typical for cards) → multiply by −1.

### 3d — Description normalisation

**`normalize_description(text)`**
`unicodedata.normalize("NFC", text).casefold().strip()`
Ensures stable case-insensitive comparisons; never modifies `raw_description`.

### 3e — Transaction identifier (idempotency key)

**`compute_transaction_id(account_label, date, amount, description)`**
SHA-256[:24] of the string: `{account_label}|{ISO date}|{amount}|{raw_description}`
Used on **raw values** → stable across normalisation versions.

**`compute_file_hash(raw_bytes)`**
Full SHA-256 of the file → import-level dedup.

### 3f — Transaction type inference

**`_infer_tx_type(amount, doc_type, description, internal_patterns)`**

```
1. description matches internal_patterns (list from DB) → internal_out / internal_in
2. doc_type in {credit_card, debit_card, prepaid_card}  → card_tx
3. amount ≥ 0                                           → income
4. amount < 0                                           → expense
```

### 3g — Card balance row removal

**`remove_card_balance_row(txs, epsilon, owner_label)`**
Detects the row whose `|amount| ≈ Σ|other amounts|` (within epsilon 0.01 €).
With `owner_label` → renames the description (internal transfer detection captures it).
Without `owner_label` → removes the row (avoids double counting).

---

## 4 — Dedup check

**Module:** `db/repository.py` → `get_existing_tx_ids()`
**When:** stage 4, after normalisation and **before** description cleaning (LLM)
**Why:** the SHA-256 ID is calculated at step 3 from raw values → duplicates can be discarded without wasting LLM tokens

```
existing_ids = SELECT id FROM transaction WHERE id IN (all_ids_in_batch)
→ filters already-present txs
→ if all present → abort early (file already imported)
```

---

## 5 — Privacy / PII Redaction

**Module:** `core/sanitizer.py`
**When:** BEFORE every LLM call (description cleaning + categorisation); AFTER for owner name restoration

### Redaction rules

| Pattern | Regex | Replaced with |
|---------|-------|---------------|
| IBAN | `[A-Z]{2}\d{2}[A-Z0-9]{4,30}` | `<ACCOUNT_ID>` |
| PAN / card (13-19 digits) | `\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{1,7}` | `<CARD_ID>` |
| Masked card | `[\*X]{4}[\s\-]?\d{4}` | `<CARD_ID>` |
| Transaction codes | `(CAU\|NDS\|TRN\|CRO\|RIF\|ID TRANSAZIONE)\s*[\d\-]+` | `<TX_CODE>` |
| IT tax code | `[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]` | `<FISCAL_ID>` |
| Additional user patterns | configurable | `<REDACTED>` |

### Owner names → fictitious names (for LLM)

Real names are replaced with **plausible but fake** names (the LLM can still recognise them as persons and extract them correctly). After the LLM response, `restore_owner_placeholders()` puts the real names back.

| Language | Fictitious name pool |
|----------|---------------------|
| IT | Carlo Brambilla, Marta Pellegrino, Alberto Marini, Giovanna Ferrara, … |
| EN | James Fletcher, Helen Norris, David Lawson, Susan Palmer, … |
| DE | Klaus Hartmann, Monika Braun, Stefan Richter, Ingrid Weber, … |
| FR | Pierre Dumont, Claire Lebrun, Michel Garnier, Sophie Renard, … |
| ES | Carlos Navarro, Elena Vega, Miguel Torres, Isabel Molina, … |

**Final guard:** `assert_sanitized(text)` → raises `ValueError` if IBAN or PAN are still present.

---

## 6 — Internal transfer detection [RF-04]

**Module:** `core/normalizer.py` → `detect_internal_transfers()`
**When:** stage 6, after dedup

### Phase 1 — Amount + date matching

```
For every pair (i, j) with account_label_i ≠ account_label_j:

  amount_match = |amount_i + amount_j| ≤ epsilon
  date_match   = |date_i − date_j| ≤ delta_days

  If both verified:
    high_symmetry = amount ≤ epsilon_strict AND date ≤ delta_days_strict

    Confidence:
      HIGH   → keyword from internal_patterns list found in description
      MEDIUM → high_symmetry without keyword

    If require_keyword_confirmation=True AND confidence=MEDIUM:
      → marks transfer_pair_id, does NOT update tx_type (goes to review)
    Otherwise:
      → tx_type: internal_out (outgoing) / internal_in (incoming)
```

### Phase 2 — Owner name matching

```
For every tx not yet paired:
  If description contains an owner name
  (regex with all permutations of the name tokens):
    → tx_type = internal_out / internal_in
    → transfer_confidence = HIGH
```

### Key parameters

| Parameter | Default |
|-----------|---------|
| `epsilon` | 0.01 € |
| `epsilon_strict` | 0.005 € |
| `delta_days` | 5 days |
| `delta_days_strict` | 1 day |

---

## 7 — Card reconciliation [RF-03]

**Module:** `core/normalizer.py` → `find_card_settlement_matches()`
**When:** stage 7, matches `card_settlement` (current account) with `card_tx` (card)

### Phase 1 — Time window
```
card_tx in [debit_date − 45 days, debit_date + 7 days]
```

### Phase 2 — Sliding window (contiguous subsets)
```
For every contiguous subset [i..j]:
  verify: gap between consecutive txs ≤ max_gap_days (5 days)
  sum = Σ |amount[i..j]|
  If |sum − debit_amount| ≤ epsilon → MATCH ✓
```

### Phase 3 — Boundary subset sum (fallback)
```
Takes k=10 txs before + k=10 after the debit date (max 20 txs)
Exhaustive search: all subsets → 2^20 ≈ 1M combinations (safe)
First combination that sums to the amount → MATCH ✓
```

---

## 8 — Categorisation — deterministic levels

**Module:** `core/categorizer.py`
**When:** stage 8, before LLM (levels 0 and 1)

### Level 0 — User rules (CategoryRule)

Saved in DB, sorted by descending priority. **First match wins.**

**`CategoryRule.matches(description, doc_type)`:**

| Type | Logic |
|------|-------|
| `exact` | `description.casefold() == pattern.casefold()` |
| `contains` | `pattern.casefold() IN description.casefold()` |
| `regex` | `re.search(pattern, description, IGNORECASE)` |

If `doc_type` specified in the rule → must match the transaction's doc_type.

### Level 1 — Static keyword rules

Hardcoded in the code, direction-aware (expenses/income separated):

**EXPENSES:**

| Pattern (regex, case-insensitive) | Category | Subcategory |
|-----------------------------------|----------|-------------|
| `conad\|coop\|esselunga\|lidl\|carrefour\|eurospin\|aldi\|penny\|pam` | Food | Grocery shopping |
| `farmacia\|pharma` | Health | Medicines |
| `eni\|shell\|q8\|tamoil\|ip\|api\|agip` | Transport | Fuel |
| `telepass\|autostrad` | Transport | Parking / ZTL |
| `trenitalia\|italo\|frecciarossa\|frecciargento` | Transport | Public transport |
| `enel\|iren\|a2a\|hera\|eni gas` | Home | Electricity |
| `netflix\|spotify\|amazon prime\|disney+\|apple tv` | Leisure | Streaming / digital subscriptions |
| `commissione\|canone conto\|spese tenuta` | Finance | Bank fees |

**INCOME:**

| Pattern | Category | Subcategory |
|---------|----------|-------------|
| `stipendio\|salary\|busta paga` | Employment | Salary |
| `pensione\|inps rendita` | Social benefits | Pension / annuity |

---

## 9 — DB Persistence

**Module:** `db/repository.py`
**When:** stage 9, everything idempotent

| Function | Idempotency rule |
|----------|------------------|
| `upsert_transaction(tx)` | if `tx.id` exists → skip |
| `create_import_batch(sha256)` | if sha256 exists → return existing |
| `upsert_document_schema(schema)` | if `source_identifier` exists → update |
| `create_reconciliation_link(sid, did)` | if pair `(sid, did)` exists → skip |
| `create_transfer_link(out_id, in_id)` | if pair exists → skip |
| `update_transaction_category()` | always sets: `confidence=high`, `source=manual`, `to_review=False` |

---

## 10 — Manual review — deterministic tools

**Module:** `db/repository.py`, `ui/review_page.py`

### Auto-apply rules (Review page)

**`apply_rules_to_review_transactions(session, user_rules)`**
On every load of the Review page:
```
For each tx with to_review=True:
  For each rule (sorted by priority DESC):
    If rule.matches(tx.description, tx.doc_type):
      → update category, source=rule, to_review=False
      → move to next tx
```

### Run all rules (Rules page)

**`apply_all_rules_to_all_transactions(session, user_rules)`**
"▶️ Run all rules" button on the Rules page:
```
Applies all rules to ALL transactions (not only to_review=True):
  Rules sorted by priority DESC
  For each tx:
    For each rule:
      If rule.matches(tx.description, tx.doc_type):
        → update category, subcategory, source=rule, confidence=high
        → if tx.to_review=True → set to_review=False (n_cleared++)
        → move to next tx (first match wins)
  Returns (n_matched, n_cleared_review)
```
Requires confirmation via checkbox before execution.

### DescriptionRule — bulk description correction rules

Saved in DB (`description_rule`). Pattern on `raw_description`:

| Type | Logic |
|------|-------|
| `exact` | `raw_description.lower() == pattern.lower()` |
| `contains` | `pattern.lower() IN raw_description.lower()` |
| `regex` | `re.search(pattern, raw_description, IGNORECASE)` |

Application: updates `description` → re-categorises with LLM.

---

## 11 — Analytics — thresholds and filters

**Module:** `ui/analytics_page.py`

### Types excluded from charts

```python
EXCLUDED = {"internal_out", "internal_in", "card_settlement", "aggregate_debit"}
```

### Spending benchmarks (ISTAT comparison)

Thresholds applied for each category against the reference household benchmark:

| Signal | Condition | Icon |
|--------|-----------|------|
| Abnormally high spending | spending > **1.5 ×** benchmark | 🔴 |
| Abnormally low spending | spending < **0.5 ×** benchmark | 🔵 |
| Normal spending | between 0.5× and 1.5× | 🟢 |
| Absent | no spending in category | ⚪ |

---

## Summary — All tools by pipeline stage

| Stage | Tool | Module | LLM? |
|-------|------|--------|------|
| 1. File format | detect_encoding / detect_delimiter / detect_header_row / detect_best_sheet | normalizer.py | ✗ |
| 1b. Pre-processing | detect_and_strip_preheader_rows / drop_low_variability_columns | normalizer.py | ✗ |
| 2. Schema — Phase 0 | column synonyms, sign inspection | classifier.py | ✗ |
| 2. Schema — Phase 1 | doc_type classification, date_format, sign_convention | classifier.py | ✓ LLM |
| 3. Normalisation | parse_date_safe / parse_amount / apply_sign_convention / normalize_description / compute_transaction_id / _infer_tx_type / remove_card_balance_row | normalizer.py + orchestrator.py | ✗ |
| 4. Dedup | get_existing_tx_ids | repository.py | ✗ |
| 5. Privacy | redact_pii / restore_owner_placeholders | sanitizer.py | ✗ |
| 5. Description cleaning | clean_descriptions_batch | description_cleaner.py | ✓ LLM |
| 6. Internal transfers | detect_internal_transfers (Phase 1 + Phase 2) | normalizer.py | ✗ |
| 7. Card reconciliation | find_card_settlement_matches (3 phases) | normalizer.py | ✗ |
| 8. Categorisation Lv. 0 | CategoryRule.matches (user rules) | categorizer.py | ✗ |
| 8. Categorisation Lv. 1 | _apply_static_rules (hardcoded keywords) | categorizer.py | ✗ |
| 8. Categorisation Lv. 3 | categorize_batch (LLM) | categorizer.py | ✓ LLM |
| 9. Persistence | upsert_transaction / persist_import_result | repository.py | ✗ |
| 10. Auto-rules | apply_rules_to_review_transactions | repository.py | ✗ |
| 10. Run all rules | apply_all_rules_to_all_transactions | repository.py | ✗ |
| 10. Bulk descriptions | DescriptionRule + _apply_description_rule_bulk | repository.py + review_page.py | ✓ LLM (re-cat.) |
| Analytics | EXCLUDED / ISTAT benchmark 0.5×–1.5× | analytics_page.py | ✗ |

---

## Global configuration parameters

All defaults are in `ProcessingConfig` (`core/orchestrator.py`):

| Parameter | Default | Used by |
|-----------|---------|---------|
| `tolerance` | 0.01 € | internal transfer detection, card reconciliation |
| `tolerance_strict` | 0.005 € | high-symmetry internal transfers |
| `settlement_days` | 5 days | internal transfer matching window |
| `settlement_days_strict` | 1 day | strict internal transfer window |
| `window_days` | 45 days | card reconciliation time window |
| `max_gap_days` | 5 days | card sliding window |
| `boundary_pre_post` | 10 txs | reconciliation subset sum |
| `confidence_threshold` | 0.80 | LLM threshold → to_review |
| `require_keyword_confirmation` | True | medium internal transfers → to_review if no keyword |
| `batch_size` (descriptions) | 30 tx/call | clean_descriptions_batch |
| `batch_size` (categories) | 20 tx/call | categorize_batch |
