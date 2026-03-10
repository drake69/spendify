# Spendify v2.1

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
| **SHA-256 idempotency** | Re-importing the same file always produces exactly the same set of rows |
| **Card–account reconciliation (RF-03)** | 3-phase algorithm that eliminates double-counting from monthly aggregate settlements |
| **Internal transfer detection (RF-04)** | Symbolic amount + time-window matching; configurable exclusion or neutralization |
| **Cascade categorization (RF-05)** | User rules → static regex → structured LLM → fallback "Other" |
| **2-level taxonomy** | 15 expense categories + 7 income categories, customizable via `taxonomy.yaml` |
| **Multi-provider LLM backend** | Ollama (local, default), OpenAI, Claude — shared abstract interface, no LangChain |
| **PII sanitization (RF-10)** | IBAN, PAN, fiscal codes, owner names redacted before any remote call |
| **Circuit breaker** | Automatic fallback to local Ollama; quarantine (`to_review=True`) if all backends fail |
| **SQLAlchemy persistence** | 6 ORM tables; idempotent CRUD; Alembic migrations |
| **Report export** | Standalone HTML (Plotly), CSV, XLSX |
| **4-page Streamlit UI** | Import → Ledger → Analytics → Review |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        app.py  (Streamlit)                      │
│   upload_page  │  registry_page  │  analysis_page  │ review_page│
└────────────────────────┬────────────────────────────────────────┘
                         │
                 core/orchestrator.py
                 ProcessingConfig  ·  process_file()
                         │
          ┌──────────────┼───────────────────┐
          │              │                   │
   Flow 1 (template)   Flow 2 (schema-on-read)
   DocumentSchema       classifier.py → LLM  → DocumentSchema
   already in DB        (sanitized sample)
          │
   normalizer.py        sanitizer.py     llm_backends.py
   ├─ encoding detect   ├─ IBAN/PAN/CF   ├─ OllamaBackend
   ├─ parse_amount()    ├─ owner names   ├─ OpenAIBackend
   ├─ SHA-256 tx_id     └─ assert_sani.. └─ ClaudeBackend
   ├─ RF-03 reconcile                       BackendFactory
   └─ RF-04 transfers                       call_with_fallback()
          │
   categorizer.py  ←── taxonomy.yaml (2 levels)
   Step 0: user rules
   Step 1: static regex
   Step 2: ML stub
   Step 3: LLM structured output
   Step 4: fallback "Other"
          │
      db/repository.py   (SQLAlchemy, idempotent)
      └─ Transaction · ImportBatch · DocumentSchemaModel
         ReconciliationLink · InternalTransferLink · CategoryRule
          │
      reports/generator.py
      └─ HTML (Jinja2+Plotly) · CSV · XLSX
```

### Flow 1 vs Flow 2

| | Flow 1 | Flow 2 |
|---|---|---|
| **Trigger** | `DocumentSchema` already in DB for that file hash / institution | First import of a new format |
| **Schema** | Retrieved from DB and applied directly | LLM infers the schema from an anonymized sample |
| **Promotion** | — | Approved Flow 2 template is saved and becomes Flow 1 |
| **LLM cost** | Zero (categorization only) | One call for classification + one for batch categorization |

---

## Project structure

```
spendify/
├── app.py                  # Streamlit entry point
├── taxonomy.yaml           # 2-level taxonomy (expenses + income)
├── .env.example            # Environment variable template
├── pyproject.toml          # Dependencies (uv / pip)
│
├── core/
│   ├── models.py           # Enums: DocumentType, TransactionType, GirocontoMode …
│   ├── schemas.py          # DocumentSchema (Pydantic) + llm_json_schema()
│   ├── llm_backends.py     # LLMBackend ABC · Ollama · OpenAI · Claude · BackendFactory
│   ├── sanitizer.py        # PII redaction (RF-10)
│   ├── normalizer.py       # Encoding, parse_amount (Decimal), SHA-256, RF-03, RF-04
│   ├── classifier.py       # Flow 2: DocumentSchema inference via LLM
│   ├── categorizer.py      # 4-step cascade + TaxonomyConfig
│   └── orchestrator.py     # Main pipeline: ProcessingConfig · process_file()
│
├── db/
│   ├── models.py           # SQLAlchemy ORM (6 tables)
│   └── repository.py       # Idempotent CRUD · persist_import_result()
│
├── reports/
│   ├── generator.py        # HTML (Jinja2+Plotly) · CSV · XLSX
│   └── template_report.html.j2
│
├── ui/
│   ├── sidebar.py          # Navigation + backend/giroconto selectors
│   ├── upload_page.py      # Multi-file import
│   ├── registry_page.py    # Filterable ledger + download
│   ├── analysis_page.py    # 5 Plotly charts + HTML export
│   ├── review_page.py      # Category correction + rule saving
│   └── reconciliation_page.py
│
├── tests/
│   ├── test_normalizer.py  # 18 deterministic tests (parse_amount, SHA-256 …)
│   ├── test_backends.py    # 10 tests (factory, validation, Ollama mock)
│   └── test_categorizer.py # 10 tests (static rules, cascade)
│
└── support/                # Legacy modules kept for compatibility
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

All options are set in the `.env` file:

```dotenv
# Database (SQLite by default, any SQLAlchemy URL works)
SPENDIFY_DB=sqlite:///ledger.db

# Custom taxonomy path
TAXONOMY_PATH=taxonomy.yaml

# Account owner names to redact before remote calls
OWNER_NAMES=Mario Rossi,M. Rossi

# LLM backend: local_ollama | openai | claude
LLM_BACKEND=local_ollama

# --- Ollama (local, default, privacy guaranteed) ---
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=gemma3:12b

# --- OpenAI (remote opt-in — requires PII sanitization) ---
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini

# --- Claude / Anthropic (remote opt-in — requires PII sanitization) ---
ANTHROPIC_API_KEY=sk-ant-...
CLAUDE_MODEL=claude-3-5-haiku-20241022
```

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

The app opens at `http://localhost:8501` with 4 pages:

| Page | Description |
|---|---|
| **Import** | Upload one or more files (CSV / XLSX / PDF). Shows summary: imported transactions, reconciliations, transfer links, flow used (1/2). |
| **Ledger** | Filterable table by date, type, review flag. Net/income/expense metrics. CSV/XLSX download. |
| **Analytics** | 5 Plotly charts: monthly bar chart, cumulative balance line, expense treemap, top-10 categories, stacked by account. HTML export. |
| **Review** | Transactions with `to_review=True`. Category correction + optional save as permanent rule. |

---

## Taxonomy

The taxonomy is defined in `taxonomy.yaml` and loaded at runtime. Structure:

```yaml
version: "1"

expenses:
  - category: "Casa"
    subcategories:
      - "Mutuo / Affitto"
      - "Gas"
      - "Energia elettrica"
      # …

income:
  - category: "Lavoro dipendente"
    subcategories:
      - "Stipendio"
      - "Bonus e premi"
      # …
```

**Expense categories (15):** Casa · Alimentari · Ristorazione · Trasporti · Salute · Istruzione · Abbigliamento · Comunicazioni · Svago e tempo libero · Animali domestici · Finanza e assicurazioni · Cura personale · Tasse e tributi · Regali e donazioni · Altro

**Income categories (7):** Lavoro dipendente · Lavoro autonomo · Rendite finanziarie · Rendite immobiliari · Trasferimenti e rimborsi · Prestazioni sociali · Altro entrate

To modify the taxonomy, edit `taxonomy.yaml` and restart the app — the JSON Schema enum for LLM prompts is regenerated automatically.

---

## Tests

```bash
# Full suite (44 deterministic tests, no LLM mocks required)
uv run python -m pytest tests/ -v

# With coverage
uv run python -m pytest tests/ --cov=core --cov=db --cov-report=term-missing
```

Tests cover: `parse_amount` (Decimal, EU/US formats), `parse_date_safe`, `normalize_description`, `compute_transaction_id` (SHA-256), `compute_file_hash`, `detect_delimiter`, `BackendFactory`, `ProcessingConfig` validation, `TaxonomyConfig`, categorization cascade.

---

## Design decisions

### `Decimal` — never `float`

All amounts are `decimal.Decimal`. IEEE 754 floats introduce rounding errors that corrupt balances and reconciliation results.

### SHA-256 idempotency

Each transaction has a 24-character `id` (truncated SHA-256) computed deterministically from `(source_file_hash, date, amount, description)`. Re-importing the same file does not create duplicates: `upsert_transaction` skips rows whose id already exists.

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
| `pyyaml` | ≥ 6.0 | taxonomy.yaml parsing |
| `alembic` | ≥ 1.13 | DB migrations |
| `pytest` | ≥ 8.0 | Tests |

---

*All data is stored locally in the SQLite database (`ledger.db`). No financial information is transmitted to external services unless a remote backend is explicitly configured with mandatory PII sanitization.*
