# Spendify v2.2

> 🇮🇹 [Leggi in italiano](README.it.md)

Unified personal finance ledger with a hybrid deterministic + LLM pipeline.

Aggregates heterogeneous bank statements (current accounts, credit cards, debit cards, savings accounts, prepaid cards) into a single chronological ledger, eliminating double-counting from periodic card settlements and internal transfers. Processing runs **offline-first**; remote LLM backends are supported as opt-in with mandatory PII sanitization.

---

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Project structure](#project-structure)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the app](#running-the-app)
- [Taxonomy](#taxonomy)
- [Tests](#tests)
- [Design decisions](#design-decisions)

---

## Features

| Feature | Detail |
|---|---|
| **Automatic classification** | Detects document type (current account, credit/debit card, prepaid, savings) with no prior configuration |
| **Deterministic normalization** | Encoding detection, delimiter detection, header detection, amounts as `Decimal` (never `float`) |
| **Card sign correction** | `invert_sign` flag in `DocumentSchema`: when a card file stores expenses as positive values, they are negated automatically |
| **SHA-256 idempotency** | Re-importing the same file always produces exactly the same set of rows |
| **Card–account reconciliation (RF-03)** | 3-phase algorithm that eliminates double-counting from monthly aggregate settlements |
| **Internal transfer detection (RF-04)** | Symbolic amount + time-window matching; configurable exclusion or neutralization |
| **Cascade categorization (RF-05)** | User rules → static regex → structured LLM → fallback "Other" |
| **Subcategory-authoritative matching** | Subcategory is the primary key: if an LLM or rule assigns a subcategory present in the taxonomy, the parent category is resolved automatically |
| **2-level taxonomy in DB** | 15 expense + 7 income categories; managed via the Tassonomia UI page (DB-backed, no file restart required) |
| **Multi-provider LLM backend** | Ollama (local, default), OpenAI, Claude — shared abstract interface, no LangChain |
| **LLM config in UI** | Backend, model and API keys are configurable from the Settings page without editing `.env` |
| **PII sanitization (RF-10)** | IBAN, PAN, fiscal codes, owner names redacted before any remote call |
| **Circuit breaker** | Automatic fallback to local Ollama; quarantine (`to_review=True`) if all backends fail |
| **SQLAlchemy persistence** | 9 ORM tables; idempotent CRUD; automatic migrations on startup |
| **Cross-session import progress** | Import job state stored in DB; all browser sessions see live progress |
| **Report export** | Standalone HTML (Plotly), CSV, XLSX |
| **8-page Streamlit UI** | Import → Ledger → Analytics → Review → Regole → Tassonomia → Impostazioni |

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                            app.py  (Streamlit)                           │
│  upload  │ ledger │ analytics │ review │ rules │ taxonomy │ settings     │
└──────────────────────────┬───────────────────────────────────────────────┘
                           │
               core/orchestrator.py
               ProcessingConfig  ·  process_file()
                           │
        ┌──────────────────┼───────────────────┐
        │                  │                   │
 Flow 1 (template)    Flow 2 (schema-on-read)
 DocumentSchema        classifier.py → LLM  → DocumentSchema
 already in DB         (sanitized sample)      invert_sign detection
        │
 normalizer.py          sanitizer.py      llm_backends.py
 ├─ encoding detect     ├─ IBAN/PAN/CF    ├─ OllamaBackend
 ├─ parse_amount()      ├─ owner names    ├─ OpenAIBackend
 ├─ SHA-256 tx_id       └─ assert_sani.. └─ ClaudeBackend
 ├─ invert_sign                              BackendFactory
 ├─ RF-03 reconcile                          call_with_fallback()
 └─ RF-04 transfers
        │
 categorizer.py  ←── TaxonomyConfig (loaded from DB)
 Step 0: user rules  (subcategory → category resolution)
 Step 1: static regex
 Step 2: ML stub
 Step 3: LLM structured output  (subcategory constrained enum)
 Step 4: fallback "Other"
        │
    db/repository.py   (SQLAlchemy, idempotent)
    └─ Transaction · ImportBatch · DocumentSchemaModel
       ReconciliationLink · InternalTransferLink · CategoryRule
       UserSettings · ImportJob · TaxonomyCategory · TaxonomySubcategory
        │
    reports/generator.py
    └─ HTML (Jinja2+Plotly) · CSV · XLSX
```

### Flow 1 vs Flow 2

| | Flow 1 | Flow 2 |
|---|---|---|
| **Trigger** | `DocumentSchema` already in DB for that column fingerprint | First import of a new format |
| **Schema** | Retrieved from DB and applied directly | LLM infers the schema from an anonymized sample |
| **Promotion** | — | Approved Flow 2 template is saved and becomes Flow 1 |
| **LLM cost** | Zero (categorization only) | One call for classification + one for batch categorization |

---

## Project structure

```
spendify/
├── app.py                  # Streamlit entry point (8 pages)
├── taxonomy.yaml           # Initial taxonomy seed (imported into DB on first run)
├── .env.example            # Environment variable template
├── pyproject.toml          # Dependencies (uv / pip)
│
├── core/
│   ├── models.py           # Enums: DocumentType, TransactionType, GirocontoMode …
│   ├── schemas.py          # DocumentSchema (Pydantic) + invert_sign + llm_json_schema()
│   ├── llm_backends.py     # LLMBackend ABC · Ollama · OpenAI · Claude · BackendFactory
│   ├── sanitizer.py        # PII redaction (RF-10)
│   ├── normalizer.py       # Encoding, parse_amount (Decimal), SHA-256, RF-03, RF-04
│   ├── classifier.py       # Flow 2: DocumentSchema inference via LLM
│   ├── categorizer.py      # 4-step cascade + TaxonomyConfig (find_category_for_subcategory)
│   └── orchestrator.py     # Main pipeline: ProcessingConfig · process_file()
│
├── db/
│   ├── models.py           # SQLAlchemy ORM (9 tables) + automatic migrations
│   └── repository.py       # Idempotent CRUD · persist_import_result() · taxonomy CRUD
│
├── reports/
│   ├── generator.py        # HTML (Jinja2+Plotly) · CSV · XLSX
│   └── template_report.html.j2
│
├── ui/
│   ├── sidebar.py          # Navigation buttons (8 pages) + giroconto mode
│   ├── upload_page.py      # Multi-file import + cross-session progress bar
│   ├── registry_page.py    # Filterable ledger (Entrata/Uscita split) + download
│   ├── analysis_page.py    # 7 Plotly charts: monthly bars, cumulative balance,
│   │                       #   expense pie+treemap, category drill-down, income pie+treemap,
│   │                       #   top-10 descriptions, stacked by account + HTML export
│   ├── review_page.py      # Category correction + optional rule saving
│   ├── rules_page.py       # Full CRUD for CategoryRule + bulk transaction re-categorization
│   ├── taxonomy_page.py    # DB-backed CRUD for categories and subcategories
│   └── settings_page.py    # Locale (date/amount format), language, LLM backend config
│
├── prompts/
│   ├── classifier.json     # System+user prompts for Flow 2 schema detection (invert_sign hint)
│   └── categorizer.json    # System+user prompts for transaction categorization
│
├── tests/
│   ├── test_normalizer.py  # Deterministic tests (parse_amount, SHA-256 …)
│   ├── test_backends.py    # Backend factory, validation, Ollama mock
│   └── test_categorizer.py # Static rules, cascade, taxonomy resolution
│
└── support/
    ├── formatting.py       # format_amount_display, format_date_display, format_raw_amount_display
    └── logging.py
```

---

## Installation

### Prerequisites

- **Python 3.13+**
- **[uv](https://github.com/astral-sh/uv)** (recommended package manager) or `pip`
- **[Ollama](https://ollama.com)** for the local LLM backend (default)

### 1. Clone the repository

```bash
git clone https://github.com/drake69/spendify.git
cd spendify
```

### 2. Install dependencies

```bash
# With uv (recommended)
uv sync

# Or with pip
pip install -e .
```

### 3. Configure environment variables

```bash
cp .env.example .env
# Edit .env with your values
```

### 4. Pull the local LLM model

```bash
ollama pull gemma3:12b
```

> Keep Ollama running (`ollama serve`) while using the app.

---

## Configuration

Minimal required settings in `.env`:

```dotenv
# Database (SQLite by default, any SQLAlchemy URL works)
SPENDIFY_DB=sqlite:///ledger.db

# Account owner names to redact before remote calls
OWNER_NAMES=Mario Rossi,M. Rossi

# LLM backend: local_ollama | openai | claude  (also configurable from Settings page)
LLM_BACKEND=local_ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=gemma3:12b
```

API keys and model selection can also be set from the **⚙️ Settings** page in the UI — they are persisted in the DB and take priority over `.env` values.

### Transfer mode (giroconto)

Configurable from the app sidebar:

| Mode | Behaviour |
|---|---|
| `neutral` | Internal transfers stay in the ledger as `internal_out` / `internal_in` (default) |
| `exclude` | Internal transfers are removed from the ledger (net balance unaffected) |

### Privacy and remote backends

```
[LOCAL — default]  Local Ollama: no data leaves the process.
                   No sanitization required.

[REMOTE — opt-in]  OpenAI / Claude: PII sanitization MANDATORY.
                   IBAN → <ACCOUNT_ID>  |  PAN → <CARD_ID>
                   CF   → <FISCAL_ID>  |  owner → <OWNER>
                   Call blocked if assert_sanitized() fails.
```

---

## Running the app

```bash
# With uv
uv run streamlit run app.py

# Or directly
streamlit run app.py
```

The app opens at `http://localhost:8501` with 8 pages:

| Page | Description |
|---|---|
| **📥 Import** | Upload one or more files (CSV / XLSX). Shows live progress (visible across all browser sessions). Summary: imported transactions, reconciliations, transfer links, flow used (1/2). |
| **📋 Ledger** | Filterable table by date, type, review flag. Split Entrata/Uscita columns. Net/income/expense metrics. CSV/XLSX download. |
| **📊 Analytics** | 7 interactive Plotly charts: monthly bar chart, cumulative balance, expense pie+treemap, interactive category drill-down with subcategory bar + monthly trend, income pie+treemap, top-10 descriptions, stacked by account. HTML export. |
| **🔍 Review** | Transactions with `to_review=True`. Category/subcategory correction + optional save as permanent rule. |
| **📏 Regole** | Full CRUD for category rules. Edit/delete existing rules + optional bulk re-categorization of already-matched transactions. |
| **🗂️ Tassonomia** | DB-backed CRUD for categories and subcategories (expenses and income). Changes take effect immediately without restarting. |
| **⚙️ Impostazioni** | Date format, amount separators, description language, LLM backend (model + API keys). All persisted in DB. |

---

## Taxonomy

The taxonomy is stored in the database (`taxonomy_category` / `taxonomy_subcategory` tables) and managed from the **🗂️ Tassonomia** page. On first startup the DB is seeded from `taxonomy.yaml`.

**Expense categories (15):** Casa · Alimentari · Ristorazione · Trasporti · Salute · Istruzione · Abbigliamento · Comunicazioni · Svago e tempo libero · Animali domestici · Finanza e assicurazioni · Cura personale · Tasse e tributi · Regali e donazioni · Altro

**Income categories (7):** Lavoro dipendente · Lavoro autonomo · Rendite finanziarie · Rendite immobiliari · Trasferimenti e rimborsi · Prestazioni sociali · Altro entrate

**Subcategory is authoritative:** if the LLM or a rule assigns a subcategory that exists in the taxonomy, the correct parent category is resolved automatically — the two levels are always consistent.

---

## Tests

```bash
# Full suite (no LLM mocks required)
uv run python -m pytest tests/ -v

# With coverage
uv run python -m pytest tests/ --cov=core --cov=db --cov-report=term-missing
```

---

## Design decisions

### `Decimal` — never `float`

All amounts are `decimal.Decimal`. IEEE 754 floats introduce rounding errors that corrupt balances and reconciliation results.

### SHA-256 idempotency

Each transaction has a 24-character `id` (truncated SHA-256) computed deterministically from `(source_file, date, amount, description)`. Re-importing the same file does not create duplicates.

### Card sign correction (`invert_sign`)

Italian card exports often store purchases as positive values. The `DocumentSchema.invert_sign` flag, set by the LLM during Flow 2 classification, instructs the normalizer to negate all amounts so that expenses become negative and refunds become positive — with a single symmetric operation.

### Subcategory as primary key

The categorizer treats subcategory as authoritative. `TaxonomyConfig.find_category_for_subcategory()` resolves the parent category from any valid subcategory name. This means LLMs and rules can specify the most granular level and the hierarchy is always consistent in the DB.

### Taxonomy in DB

The 2-level taxonomy (categories + subcategories) lives in two DB tables (`taxonomy_category`, `taxonomy_subcategory`). It is seeded from `taxonomy.yaml` on first run and then managed entirely from the UI — no file edits or restarts required.

### PII sanitization as a precondition

`assert_sanitized()` is called inside `call_with_fallback()` before any request to a remote backend. If the text contains detectable IBAN/PAN/fiscal-code patterns, the call is rejected — not silently degraded.

### Circuit breaker and quarantine

`call_with_fallback(primary, ...)` tries the primary backend, then local Ollama as fallback. If both fail, the transaction receives `to_review=True` and is queued for manual review without blocking the rest of the batch.

### No LangChain

LLM backends use the `openai` SDK, `anthropic` SDK, and `requests` (for Ollama) directly. No LLM orchestration framework dependency — smaller attack surface, independent SDK updates.

### RF-03: 3-phase algorithm

Card–account reconciliation uses: (1) temporal window ±45 days, (2) contiguous sliding window (gap ≤ 5 days, O(n²)), (3) boundary subset sum (k=10 txs, ~10⁶ operations). Reconciled transactions are excluded from the net balance to prevent double-counting.

---

## Key dependencies

| Package | Version | Purpose |
|---|---|---|
| `streamlit` | ≥ 1.35 | UI |
| `pandas` | ≥ 2.2 | Data processing |
| `sqlalchemy` | ≥ 2.0 | ORM / persistence |
| `pydantic` | ≥ 2.0 | Schema validation |
| `openai` | ≥ 1.30 | OpenAI backend |
| `anthropic` | ≥ 0.28 | Claude backend |
| `requests` | ≥ 2.31 | Ollama backend |
| `chardet` | ≥ 5.0 | Encoding detection |
| `plotly` | ≥ 5.20 | Charts |
| `jinja2` | ≥ 3.1 | HTML report template |
| `pyyaml` | ≥ 6.0 | taxonomy.yaml seed parsing |
| `pytest` | ≥ 8.0 | Tests |

---

*All data is stored locally in the SQLite database (`ledger.db`). No financial information is transmitted to external services unless a remote backend is explicitly configured with mandatory PII sanitization.*
