# Spendify — Project Document

> Version: 2.4 — updated 2026-03-14

---

## 1. Objective

Spendify is a personal financial ledger that aggregates heterogeneous bank statements (CSV / XLSX from Italian banks) into a single chronological ledger. The system automatically eliminates double-counting caused by:

- Periodic credit card charges on the current account (RF-03 reconciliation)
- Internal transfers between accounts owned by the same holder (RF-04)

Processing is **offline-first**: the default LLM backend is local Ollama; OpenAI and Claude are supported as opt-in with mandatory PII sanitization.

---

## 2. Technology Stack

| Layer | Technology |
|---|---|
| UI | Streamlit ≥ 1.45 |
| Pipeline | Python 3.13, pandas 2.x |
| ORM / DB | SQLAlchemy 2.x + SQLite |
| Schema validation | Pydantic v2 |
| LLM (local) | Ollama + gemma3:12b (default) |
| LLM (remote) | OpenAI SDK, Anthropic SDK |
| Charts | Plotly |
| HTML Export | Jinja2 |
| Tests | pytest, SQLite in-memory |
| Package manager | uv |

---

## 3. System Architecture

### 3.0 Architectural layers

```
┌─────────────────────────────────────────────────┐
│  Presentation                                   │
│  ├─ Streamlit UI  (ui/, app.py)   :8501         │
│  └─ FastAPI REST  (api/)          :8000         │
├─────────────────────────────────────────────────┤
│  Service layer   (services/)                    │
│  TransactionService · RuleService               │
│  SettingsService · CategoryService              │
│  ImportService                                  │
├─────────────────────────────────────────────────┤
│  Business logic  (core/)                        │
│  classifier · normalizer · categorizer          │
│  sanitizer · description_cleaner · orchestrator │
├─────────────────────────────────────────────────┤
│  Persistence     (db/)                          │
│  models (SQLAlchemy ORM) · repository (CRUD)    │
│  SQLite: ledger.db                              │
└─────────────────────────────────────────────────┘
```

The UI and API are fully independent and interchangeable: both rely on the service layer, which has no knowledge of HTTP or Streamlit.

### 3.1 Import Pipeline

```
CSV/XLSX File
     │
     ▼
core/classifier.py   ──── Flow 1: schema already in DB (SHA-256 column fingerprint)
     │                    Flow 2: LLM infers schema from sanitized sample
     ▼
core/normalizer.py
  ├─ Encoding detection (chardet)
  ├─ Delimiter / header detection
  ├─ parse_amount() → Decimal (never float)
  ├─ SHA-256 tx_id (idempotent dedup)
  ├─ invert_sign (card sign correction)
  ├─ RF-03 card–current account reconciliation
  └─ RF-04 internal transfer detection
     │
core/description_cleaner.py
  └─ LLM: noise removal, text standardization
     │
core/categorizer.py
  ├─ Step 0: user rules
  ├─ Step 1: static regex
  ├─ Step 2: ML stub (future)
  ├─ Step 3: LLM structured output (constrained subcategory enum)
  └─ Step 4: fallback "Altro"
     │
db/repository.py → persist_import_result()
```

### 3.2 Schema Identification (Flow 1 vs Flow 2)

Each imported file is "signed" by a SHA-256 of the normalized column headers. If the signature is already in `document_schema`, the saved schema is used (Flow 1, zero LLM cost). Otherwise the LLM infers the schema (Flow 2) and the approved template is saved for subsequent imports.

### 3.3 Import Job

Processing runs in a background thread. Progress is saved in the DB (`import_job` table) and polled every 2 seconds by any open browser session. This allows opening a second browser and viewing the progress of an import started elsewhere.

**Significant progress points:**
- `0%` — start
- `15%` — schema identified / approved
- `25%` — normalization completed
- `38%` — description cleaning completed
- `40%→100%` — batch categorization (≈1 min per 20 transactions with local LLM)

---

## 4. Data Model

### 4.1 Main Tables

| Table | Description |
|---|---|
| `transaction` | Each row of the ledger. Key: `id` (SHA-256 24 char) |
| `import_batch` | Metadata for each import (file, schema, counts) |
| `document_schema` | Schema template for Flow 1 (fingerprint → configuration) |
| `reconciliation_link` | Reconciled card–current account pairs (RF-03) |
| `internal_transfer_link` | Internal transfer pairs (RF-04) |
| `category_rule` | Deterministic categorization rules |
| `user_settings` | User preferences (key/value store) |
| `import_job` | Current state of the import job |
| `taxonomy_category` | Taxonomy categories (2 levels) |
| `taxonomy_subcategory` | Taxonomy subcategories |

### 4.2 Transaction — Key Columns

| Column | Type | Notes |
|---|---|---|
| `id` | VARCHAR(24) PK | Truncated SHA-256 of (source_file, date, amount, description) |
| `date` | VARCHAR(10) | ISO 8601: `YYYY-MM-DD` |
| `amount` | Numeric(18,4) | Always Decimal, never float; negative = expense |
| `tx_type` | VARCHAR | `expense` / `income` / `internal_out` / `internal_in` |
| `description` | TEXT | Description cleaned by the LLM |
| `raw_description` | TEXT | Original description from the file |
| `category` | VARCHAR | Taxonomy category |
| `subcategory` | VARCHAR | Taxonomy subcategory |
| `context` | VARCHAR(64) | Life context (nullable, orthogonal to category) |
| `account_label` | VARCHAR | Stable account identifier (from user_settings) |
| `to_review` | BOOLEAN | True if LLM failed or ambiguous |
| `source_identifier` | VARCHAR | SHA-256 of columns (schema fingerprint) |

### 4.3 UserSettings — Relevant Keys

| Key | Default | Description |
|---|---|---|
| `date_display_format` | `%d/%m/%Y` | Date format in the UI |
| `amount_decimal_sep` | `,` | Decimal separator |
| `amount_thousands_sep` | `.` | Thousands separator |
| `description_language` | `it` | Language used in LLM prompts |
| `giroconto_mode` | `neutral` | `neutral` or `exclude` |
| `llm_backend` | `local_ollama` | Active LLM backend |
| `ollama_base_url` | `http://localhost:11434` | Ollama server URL |
| `ollama_model` | `gemma3:12b` | Ollama model |
| `openai_api_key` | — | OpenAI key |
| `openai_model` | `gpt-4o-mini` | OpenAI model |
| `anthropic_api_key` | — | Anthropic key |
| `anthropic_model` | `claude-3-5-haiku-20241022` | Claude model |
| `owner_names` | — | Account holder names (CSV) for PII redaction and internal transfers |
| `use_owner_names_giroconto` | `false` | Use holder names to detect internal transfers |
| `contexts` | `["Quotidianità","Lavoro","Vacanza"]` | Life contexts (JSON array) |
| `import_test_mode` | `false` | Import only the first 20 rows |

---

## 5. Main Features

### 5.1 Internal Transfer Detection (RF-04)

Three deterministic steps, all without LLM:

**Step 1 — Keyword regex**
The description is compared against patterns configured per schema (`internal_transfer_patterns`). Positive match → `tx_type = internal_out/in` with high confidence.

**Step 2 — Amount matching + time window**
Among transactions with different `account_label`, a pair with the same absolute amount within ±3 days is searched for. Positive match → link in `internal_transfer_link`.

**Step 3 — Owner name permutations**
`_build_owner_name_regex()` in `core/sanitizer.py` builds a regex that intercepts all permutations of the holder name tokens. This avoids false negatives when the surname/name order varies across files from different banks.

**Cross-account re-execution**
Available from the Review page via `_rerun_transfer_detection()` in `ui/review_page.py`. Loads all non-internal-transfer transactions, aggregates patterns from all schemas (`get_all_transfer_keyword_patterns`), and re-runs the three steps updating only the rows where `tx_type` has changed.

### 5.2 Card–Account Reconciliation (RF-03)

3-phase algorithm to eliminate double-counting of periodic card charges:

1. **Time window** ±45 days
2. **Contiguous sliding window** (gap ≤ 5 days, O(n²))
3. **Subset sum at boundary** (k=10 transactions, ≈10⁶ operations)

Reconciled pairs are recorded in `reconciliation_link`. Reconciled card transactions are excluded from the net balance.

### 5.3 Cascading Categorization (RF-05)

```
Transaction
    │
    ├─ Step 0: match on category_rule (highest priority)
    │          subcategory → parent category via TaxonomyConfig
    │
    ├─ Step 1: static regex in core/categorizer.py
    │
    ├─ Step 2: [ML stub — future]
    │
    ├─ Step 3: LLM with constrained enum
    │          prompt: categorizer.json
    │          output: subcategory chosen from valid enum
    │          TaxonomyConfig.find_category_for_subcategory() resolves the category
    │
    └─ Step 4: fallback → "Altro" / "Altro entrate"
                          to_review = True
```

### 5.4 Life Contexts

Orthogonal dimension to the taxonomy. Each transaction can have at most one context (`context VARCHAR(64)`). Configurable by the user (add/rename/delete) from the Impostazioni page.

**Assignment**: from the Ledger page, "🌍 Assegna contesto" panel:
- Manual selection from dropdown menu
- "Apply to similar transactions" option: `get_similar_transactions()` in `db/repository.py` uses Jaccard token similarity (threshold 0.35) to find transactions with a similar description

**Filter**: the ledger can be filtered by specific context, "all", or "none" (NULL).

### 5.5 Description Cleaning

`core/description_cleaner.py` calls the LLM to remove noise (internal bank codes, operation IDs, partial IBANs) and standardize the text. The result is saved in `description`; the original remains in `raw_description`.

If the LLM fails, `description` remains equal to `raw_description`. The "🔄 Rielabora con LLM" button on the Review page uses this condition as a filter to identify transactions to reprocess.

### 5.6 PII Sanitization (RF-10)

Before any call to a remote backend:

| Pattern | Replacement |
|---|---|
| IBAN | `<ACCOUNT_ID>` |
| PAN (card) | `<CARD_ID>` |
| Tax code | `<FISCAL_ID>` |
| Holder names | `<OWNER>` |

`assert_sanitized()` verifies the absence of detectable patterns and blocks the call if any are found.

---

## 6. DB Migrations

Migrations are idempotent and run automatically at startup in `db/models.py → create_tables()`:

| Function | Addition |
|---|---|
| `_migrate_add_user_settings()` | `user_settings` table (key/value store) |
| `_migrate_add_import_job()` | `import_job` table |
| `_migrate_add_raw_description()` | `raw_description` column on `transaction` |
| `_migrate_add_account_label()` | `account_label` column on `transaction` |
| `_migrate_add_context()` | `context` column on `transaction` |

---

## 7. User Interface

### 7.1 Navigation

9 Streamlit pages managed by `app.py` + `ui/sidebar.py`:

```
📥 Import              upload_page.py
📋 Ledger              registry_page.py
✏️ Modifiche massive   bulk_edit_page.py
📊 Analytics           analysis_page.py
🔍 Review              review_page.py
📏 Regole              rules_page.py
🗂️ Tassonomia          taxonomy_page.py
⚙️ Impostazioni        settings_page.py
✅ Check List          checklist_page.py
```

### 7.2 Import Page

- Multi-file upload (CSV / XLSX)
- Live progress bar (DB polling every 2s)
- Visible from any browser that has the app open
- Summary at completion: imported transactions, reconciled, internal transfers found, flow used

### 7.3 Modifiche massive Page

- Bulk operations on a reference transaction: internal transfer toggle, context assignment (Jaccard similarity ≥ 35%), category/subcategory correction + rule saving
- Bulk deletion by filter: combinable filters (date, account, type, description, category); at least one filter required; preview of first 10 rows; confirmation by typing `ELIMINA`; irreversible deletion
- Cross-account duplicate detection: pivot table to identify transactions present on multiple accounts

### 7.4 Ledger Page

- Filters: date range, transaction type, description (full-text on description + raw_description), category, context, review flag
- Click on a row → instant selection with details in sidebar
- "🌍 Assegna contesto" panel with similarity suggestions
- Internal transfer toggle (single + bulk by description)
- Separate Income/Expense columns, right-aligned
- Metrics: net balance, total income, total expenses
- CSV / XLSX download of the filtered ledger

### 7.5 Review Page

- Only transactions with `to_review=True`
- Internal transfer toggle + bulk-apply
- Category/subcategory correction with optional saving as a rule
- **"🔄 Rielabora con LLM"**: re-runs cleaning + categorization on uncleaned transactions
- **"🔁 Riesegui rilevamento giroconti"**: re-runs RF-04 globally

### 7.6 Impostazioni Page

- Date format and amount separators (with live preview)
- Description language (used in LLM prompts)
- Internal transfer mode (neutral / exclude)
- Holder names + toggle for use in internal transfer detection
- Life contexts (editable list: add/rename/delete)
- Import test mode (first 20 rows only)
- Bank account list (add/delete, used as stable `account_label` for dedup)
- LLM backend: Ollama / OpenAI / Claude + model + API keys

### 7.7 Check List Page

- **Month × account** pivot table with the number of transactions for each combination
- Rows in **descending** order: current month at the top, then going back in time
- The current month always appears (even if it has no transactions yet)
- Columns: all accounts defined in `account` + any `account_label` from transactions not yet formalized
- Empty cell (0 tx): **—** symbol in light grey; cell with tx: number with proportional coloring (light blue → dark blue)
- Three KPIs at the top: total transactions, monitored accounts, months with data
- Filters: account selection, last N months, hide months without transactions
- CSV download of the filtered table

---

## 8. Categorization Rules

### 8.1 Match Types

| Type | Behavior |
|---|---|
| `contains` | Pattern anywhere in the description (case-insensitive) |
| `exact` | Description equal to the pattern (case-insensitive) |
| `regex` | Full Python regex |

### 8.2 Upsert Semantics

Same `(pattern, match_type)` pair → in-place update of category/priority (no duplicates).

### 8.3 Retroactive Application

Rules are applied to **all** existing transactions on save, not just to future imports. The count of updated transactions is shown to the user.

**Run all rules (bulk):** the "▶️ Esegui tutte le regole" button on the Regole page applies all active rules to every transaction in the ledger at once (not only `to_review=True`). Useful after creating multiple rules in different sessions or after importing historical data without an active LLM.

---

## 9. Taxonomy

### 9.1 Storage

Two DB tables: `taxonomy_category` and `taxonomy_subcategory`. Initial seeding from `taxonomy.yaml`.

### 9.2 Subcategory as Source of Truth

`TaxonomyConfig.find_category_for_subcategory()` resolves the parent category from any valid subcategory. The LLM and rules can specify only the subcategory and the category is resolved automatically.

### 9.3 Default Categories

**Expenses (15):** Casa · Alimentari · Ristorazione · Trasporti · Salute · Istruzione · Abbigliamento · Comunicazioni · Svago e tempo libero · Animali domestici · Finanza e assicurazioni · Cura personale · Tasse e tributi · Regali e donazioni · Altro

**Income (7):** Lavoro dipendente · Lavoro autonomo · Rendite finanziarie · Rendite immobiliari · Trasferimenti e rimborsi · Prestazioni sociali · Altro entrate

---

## 10. Privacy and Security

- **Local-first**: Ollama is the default backend, no data leaves the process
- **Mandatory sanitization**: `assert_sanitized()` blocks remote calls if PII is found
- **Holder names**: configurable from the UI, removed from all descriptions before remote LLM calls and used to detect internal transfers
- **No LangChain**: OpenAI, Anthropic SDKs and requests used directly, minimal attack surface

---

## 11. Known Limitations

- **Excel and numeric locale**: Excel numeric cells lose the original format (e.g., `2,50` becomes `2.5`). The `raw_amount` field for Excel files will show `"2.5"` — a limitation of the Excel format, not a bug.
- **Cross-account internal transfers**: detected only if both files have already been imported. Solution: "Riesegui rilevamento giroconti" button on the Review page.
- **Async LLM**: categorization happens in the background. With local Ollama and gemma3:12b, each batch of 20 transactions takes about 1 minute.

---

## 12. REST API

The FastAPI layer (`api/`) exposes the same ledger operations over HTTP/JSON, without touching the Streamlit UI.

**Start:** `uv run uvicorn api.main:app --host 0.0.0.0 --port 8000`
**Interactive docs:** `http://localhost:8000/docs` (Swagger UI)

### Main endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Liveness check |
| GET | `/transactions` | List with filters (date, category, account, to_review) |
| PATCH | `/transactions/{id}/category` | Update category and subcategory |
| PATCH | `/transactions/{id}/context` | Update life context |
| POST | `/transactions/{id}/toggle-giroconto` | Toggle internal transfer flag |
| DELETE | `/transactions` | Bulk delete by filter (at least 1 filter required) |
| GET/POST/PATCH/DELETE | `/rules/category` | Category rule CRUD |
| POST | `/rules/category/apply-to-review` | Apply rules to pending review |
| POST | `/rules/category/apply-to-all` | Apply rules to all transactions |
| GET/POST/DELETE | `/rules/description` | Description rule CRUD |
| GET | `/settings` | All settings (API keys redacted) |
| GET/PUT | `/settings/{key}` | Read/write a single setting |
| GET/POST/DELETE | `/accounts` | Account CRUD |
| GET/POST/PATCH/DELETE | `/taxonomy/categories` | Taxonomy category CRUD |
| GET | `/import/jobs/latest` | Latest import job status |

### Security

- API keys (`openai_api_key`, `anthropic_api_key`) are always redacted (`***`) in GET responses
- The same keys cannot be updated via API (403 Forbidden) — only from the Settings UI
- CORS configured for `localhost:8501` (Streamlit) by default

### Docker

In Docker Compose, the `api` service shares the `spendify_data` volume (same `ledger.db`) with the Streamlit service.

---

## 13. Tests

```bash
# Full suite
uv run python -m pytest tests/ -v

# With coverage
uv run python -m pytest tests/ --cov=core --cov=db --cov-report=term-missing
```

All tests use SQLite in-memory — no files, no external services.

| File | Coverage |
|---|---|
| `test_normalizer.py` | `parse_amount`, SHA-256, encoding |
| `test_backends.py` | Factory, validation, mock Ollama |
| `test_categorizer.py` | 4-step cascade, taxonomy resolution |
| `test_repository_rules.py` | Rule upsert, pattern matching, internal transfer toggle, bulk ops |
