# Spendify — Reference Guide

> For detailed configuration of all parameters and LLM providers see **[developer_guide.en.md](developer_guide.en.md)** and the technical documentation in `documents/configurazione.en.md`.

---

## Application Pages

| Page | Purpose |
|---|---|
| **Import** | Upload CSV/XLSX files, start the processing pipeline |
| **Ledger** | Table view of all imported transactions |
| **Modifiche massive** | Bulk operations: category, context, internal transfer, **deletion by filter** |
| **Analytics** | Aggregated charts and reports by period/account/category |
| **Review** | Transactions with uncertain classification or requiring review |
| **Regole** | Management of deterministic categorization rules |
| **Tassonomia** | Customizable category/subcategory structure |
| **Impostazioni** | LLM backend, API keys, date/amount formats, language |
| **Check List** | Month × account pivot table: presence and quantity of transactions |

---

## Supported Import Formats

| Format | Notes |
|---|---|
| CSV | Auto-detect encoding (UTF-8, latin-1, cp1252), delimiter (`,` `;` `\t`) |
| XLSX / XLS | Numeric cells read as float (original local format is not recoverable) |

Banks recognized automatically via header fingerprint. No manual configuration required.

---

## Processing Pipeline (execution order)

```
Input file
    │
    ▼
0. Pre-processing Phase 0      → removal of sparse pre-header rows; drop low-variability columns
0b. Header SHA256               → hash of first min(30,N) raw rows → schema lookup in DB (O(1))
1. Document classification     → only if schema not found by SHA256: LLM Flow 2 + mandatory UI review
2. Normalization               → encoding, delimiters, skip_rows, parse dates/amounts, SHA-256
3. Dedup check                 → discard already-present transactions (zero LLM calls)
4. Description cleaning        → LLM extracts counterparty name, PII redacted before/after
5. Internal transfer detection → excludes/neutralizes internal transfers
6. Card–account reconciliation → eliminates double-counting of aggregated monthly charges
7. Cascading categorization    → user rules → static regex → LLM → fallback "Altro"
8. Persistence                 → idempotent upsert for tx, links, schema (with header_sha256)
```

## Schema Review Gate (first import)

On the **first import** of a file with an unknown format (header SHA256 not present in DB), the app stops and shows a mandatory review form. The user sees:

1. **Raw preview** — first 10 rows of the raw file (without preprocessing)
2. **skip_rows selector** — how many rows to skip before the actual header
3. **Editable schema fields** — document type, columns, sign convention
4. **Parsed preview** — first 8 transactions with the current schema (live)

After confirmation, the schema is saved with the `header_sha256` fingerprint. On re-import of the same format: immediate lookup, no LLM call, no UI.

---

## Cascading Categorization

The category is assigned in the following order; the first match wins:

1. **User rules** — defined in the Regole page (exact / contains / regex)
2. **Static rules** — hardcoded patterns for common cases (stamp duties, F24, standard rents)
3. **LLM** — the model configured in Impostazioni; receives the sanitized description
4. **Fallback** — category "Altro" if all previous steps fail

The **subcategory is the source of truth**: if the LLM or a rule assigns a subcategory present in the taxonomy, the parent category is derived automatically.

---

## Categorization Rules

### Match Types

| Type | Behavior | Example pattern |
|---|---|---|
| `exact` | The entire description must match (case-insensitive) | `NETFLIX.COM` |
| `contains` | The pattern must appear in the description (case-insensitive) | `ESSELUNGA` |
| `regex` | Python regular expression | `RATA\s+\d+/\d+` |

### Retroactive Application
When you save a new rule, it is applied immediately to **all** transactions already present in the database, not just to future imports.

### Run All Rules
The **▶️ Esegui tutte le regole** button applies all active rules to every transaction in the ledger at once. Useful after creating multiple rules in different sessions or after importing historical data. The operation requires confirmation via checkbox; upon completion it shows how many transactions were updated.

### Priority
Rules are evaluated in descending priority order (`priority` field, default 10). In case of equal priority, the order is stable but not guaranteed. The **first matching rule wins** — processing stops at the first match found.

---

## Card–Account Reconciliation (RF-03)

When the bank charges the monthly credit card total to the current account, the individual card expenses and the cumulative debit on the account would be counted twice. Spendify resolves this automatically:

- Card transactions remain visible in the Ledger
- The aggregated debit on the account is marked as an internal transfer (🔄) and excluded from totals

No configuration required. If you still see a duplicate, check in Review.

---

## Internal Transfers (RF-04)

An internal transfer is a transfer between two of your own accounts (e.g., "Transfer to Savings Account"). If counted on both sides it distorts the balance.

**How it is detected:** amount matching + time window (±3 days) between different accounts of the same holder.

**How it is marked:** 🔄 icon in the Ledger, excluded from Analytics totals.

**"Rileva giroconti cross-account" button** in Review: re-runs detection globally on all transactions. Useful if you imported both sides of the transfer in separate sessions.

---

## LLM Backend

| Backend | Runs | Privacy | Configuration |
|---|---|---|---|
| **Ollama** | Local (default) | Total — no data leaves your PC | Requires Ollama installed and model downloaded |
| **llama.cpp** | Local (Docker container) | Total — no data leaves your PC | GGUF files in `models/`, URL `http://llama-cpp:8080/v1` |
| **OpenAI** | Remote | PII redacted before sending | API key in Impostazioni |
| **Claude** | Remote | PII redacted before sending | API key in Impostazioni |

**Circuit breaker:** if the configured backend does not respond, Spendify automatically falls back to local Ollama. If Ollama is also offline, the transaction is imported with `to_review=True` and raw description.

---

## PII Sanitization

Before any call to a remote backend, Spendify redacts:

| Data | Original example | After sanitization |
|---|---|---|
| IBAN | `IT60X0542811101000000123456` | `<ACCOUNT_ID>` |
| Card number | `4111 1111 1111 1111` | `<CARD_ID>` |
| Tax code | `RSSMRA80A01H501U` | `<FISCAL_ID>` |
| Holder name | `Mario Rossi` | Fictional name (e.g., `Carlo Brambilla`) |

Sanitization occurs in memory; the original data is never modified in the database.

---

## Tassonomia

2-level structure: **Category → Subcategory**.

- Editable from the Tassonomia page without restarting the app
- Default categories: Alimentari, Casa, Trasporti, Salute, Svago, Abbonamenti, Utenze, Istruzione, Lavoro, Finanza, Viaggi, Regali, Tasse, Altro + income categories
- You can add custom subcategories without touching the code

---

## Idempotency

Each transaction is identified by a SHA-256 hash calculated from: date, amount, description, account. Re-importing the same file produces the same set of rows; duplicates are discarded without errors.

---

## Export

From the Analytics page → **Esporta** button:

| Format | Content |
|---|---|
| **HTML** | Standalone report with interactive Plotly charts |
| **CSV** | All filtered transactions, canonical columns |
| **XLSX** | Same as CSV but with Excel formatting |

---

## Description Rules (bulk edit)

Distinct from categorization rules. Used to replace unreadable raw descriptions with human-readable text.

- Stored in the `description_rule` table of the database
- Applicable in bulk from the panel at the bottom of the Review page
- Same match types: `exact` / `contains` / `regex`
- After application, updated transactions are reprocessed by the LLM for categorization

---

## Modifiche massive — dedicated page

The **✏️ Modifiche massive** page collects all operations that act on multiple transactions simultaneously.

### Sections 1–3: operations on a reference transaction

| Section | Operation |
|---------|-----------|
| **1 · Choose transaction** | Selection with text search and "review only" filter |
| **2a · Internal transfer** | Internal transfer ↔ normal toggle, with propagation to all tx with the same description |
| **2b · Context** | Assign context to the selected tx and/or to similar ones (Jaccard ≥ 35%) |
| **2c · Category** | Correct category/subcategory, save deterministic rule, propagate to similar ones |

### Section 3: Bulk deletion by filter

Allows bulk deletion of transactions selected via combinable filters:

| Filter | Type |
|--------|------|
| From / To | Date range |
| Account | Exact account label |
| Type | tx_type (expense, income, card_tx, …) |
| Description | `ILIKE` search on `description` and `raw_description` |
| Category | Exact category |

**Behavior:**
- If no filter is set, the delete button is not available (protection against accidental deletion of the entire DB)
- The counter shows in real time the number of transactions that will be deleted
- An expandable preview shows the first 10 matching rows
- Confirmation requires typing exactly `ELIMINA` in the text field before enabling the button
- Deletion is **irreversible** — make sure you have a backup before proceeding (see `documents/deployment.md`)
- Reconciliation and internal transfer links associated with deleted transactions are removed in cascade

---

## Check List — dedicated page

The **✅ Check List** page shows a **month × account** pivot table with the number of transactions present for each combination.

### Layout

- **Rows:** months in **descending** order (current month at the top, then going back to the oldest month with data)
- **Columns:** all accounts defined in the `account` table + any `account_label` present in transactions but not yet formalized as an account
- **Cell with transactions:** integer (color proportional to quantity)
- **Cell without transactions:** **—** symbol in light grey

### Coloring

| Color | Range |
|---|---|
| Grey (—) | 0 transactions |
| Light blue | 1–4 transactions |
| Medium blue | 5–19 transactions |
| Dark blue | ≥ 20 transactions |

### Available Filters

| Filter | Effect |
|---|---|
| Show only accounts | Reduces columns to selected accounts |
| Last N months | Limits rows to the last N months (0 = all) |
| Hide months without tx | Removes rows with all zeros |

### Summary Metrics

Three KPIs at the top of the page: **total transactions**, **monitored accounts**, **months with data**.

### Export

**⬇️ Scarica CSV** button to export the filtered table.

---

## Database

SQLite file: `ledger.db` in the application directory.

Main tables:

| Table | Content |
|---|---|
| `transaction` | All imported transactions |
| `import_batch` | Metadata for each import (file, schema, counts) |
| `document_schema` | Schema template for Flow 1 (column fingerprint → configuration) |
| `reconciliation_link` | Reconciled card–account pairs |
| `internal_transfer_link` | Internal transfer pairs |
| `category_rule` | User categorization rules |
| `description_rule` | Bulk description cleaning rules |
| `taxonomy_category` | Taxonomy categories |
| `taxonomy_subcategory` | Taxonomy subcategories |
| `import_job` | Current state of the import job |
| `user_settings` | User preferences (date format, separators, LLM, contexts) |

Schema migrations are idempotent: they run automatically at every startup without data loss.

---

## REST API

The FastAPI layer (`api/`) exposes the same ledger features over HTTP/JSON — independent of the Streamlit UI, usable from scripts, automation, or future mobile/desktop apps.

**Port:** `8000` · **Interactive docs:** `http://localhost:8000/docs`

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Liveness check |
| GET | `/transactions` | List transactions with filters |
| PATCH | `/transactions/{id}/category` | Update category/subcategory |
| PATCH | `/transactions/{id}/context` | Update life context |
| POST | `/transactions/{id}/toggle-giroconto` | Toggle internal transfer flag |
| DELETE | `/transactions` | Bulk delete by filter |
| GET | `/rules/category` | List categorisation rules |
| POST | `/rules/category` | Create rule |
| PATCH | `/rules/category/{id}` | Update rule |
| DELETE | `/rules/category/{id}` | Delete rule |
| POST | `/rules/category/apply-to-review` | Apply rules to pending transactions |
| POST | `/rules/category/apply-to-all` | Apply rules to entire ledger |
| GET/POST/DELETE | `/rules/description` | Description rule CRUD |
| GET | `/settings` | All settings (API keys redacted) |
| GET/PUT | `/settings/{key}` | Read/write setting |
| GET/POST/DELETE | `/accounts` | Account CRUD |
| GET/POST/PATCH/DELETE | `/taxonomy/categories` | Category CRUD |
| POST/PATCH/DELETE | `/taxonomy/categories/{id}/subcategories` | Subcategory CRUD |
| GET | `/import/jobs/latest` | Latest import job status |

### Query filters — GET /transactions

| Parameter | Type | Description |
|-----------|------|-------------|
| `from_date` | `YYYY-MM-DD` | Start date |
| `to_date` | `YYYY-MM-DD` | End date |
| `account_label` | string | Bank account |
| `category` | string | Category |
| `tx_type` | string | Transaction type |
| `to_review` | bool | Pending review only |
| `limit` | int (1–5000) | Max results (default 500) |
| `offset` | int | Pagination offset |

### Security notes

- `openai_api_key` and `anthropic_api_key` are always masked (`***`) in responses
- The same keys cannot be updated via API (403) — Settings UI only
- `DELETE /transactions` requires at least one filter (422 without filters)

---

## Quick Start

```bash
# Docker one-liner install (Mac/Linux)
curl -fsSL https://raw.githubusercontent.com/drake69/spendify/main/installer/install.sh | bash

# Docker one-liner install (Windows PowerShell)
# irm https://raw.githubusercontent.com/drake69/spendify/main/installer/install.ps1 | iex

# Development start (build from source)
uv run streamlit run app.py
```

App available at `http://localhost:8501`.
