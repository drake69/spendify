# Spendify v2.4

[![CI](https://github.com/drake69/spendify/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/drake69/spendify/actions/workflows/ci.yml)
[![Python 3.13](https://img.shields.io/badge/python-3.13-blue?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![License: PolyForm NC](https://img.shields.io/badge/license-PolyForm%20Noncommercial-orange)](LICENSE)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Streamlit](https://img.shields.io/badge/UI-Streamlit-ff4b4b?logo=streamlit&logoColor=white)](https://streamlit.io)
[![Issues](https://img.shields.io/github/issues/drake69/spendify)](https://github.com/drake69/spendify/issues)
[![Last commit](https://img.shields.io/github/last-commit/drake69/spendify)](https://github.com/drake69/spendify/commits/main)

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
- [Rule engine](#rule-engine)
- [Giroconto (internal transfers)](#giroconto-internal-transfers)
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
| **Rule engine with bulk apply** | Deterministic rules apply to all existing transactions on save, not just future imports |
| **Subcategory-authoritative matching** | Subcategory is the primary key: if an LLM or rule assigns a subcategory present in the taxonomy, the parent category is resolved automatically |
| **2-level taxonomy in DB** | 15 expense + 7 income categories; managed via the Tassonomia UI page (DB-backed, no file restart required) |
| **Multi-provider LLM backend** | Ollama (local, default), OpenAI, Claude — shared abstract interface, no LangChain |
| **LLM config in UI** | Backend, model and API keys are configurable from the Settings page without editing `.env` |
| **PII sanitization (RF-10)** | IBAN, PAN, fiscal codes, owner names redacted before any remote call |
| **Circuit breaker** | Automatic fallback to local Ollama; quarantine (`to_review=True`) if all backends fail |
| **Life contexts** | User-configurable orthogonal dimension (e.g. Quotidianità / Lavoro / Vacanza) assignable to any transaction; Jaccard-based similarity suggestions pre-fill context from past transactions |
| **LLM re-run on failures** | Review page button re-runs description cleaning + categorization only on transactions where the LLM previously failed (`description == raw_description`) |
| **Cross-account giroconto re-detection** | Review page button re-runs `detect_internal_transfers` globally on all transactions to catch pairs missed because the counterpart file was imported later |
| **Owner-name permutation matching** | All token permutations of account-holder names are checked for giroconto detection, preventing missed matches when the name order varies across bank files |
| **SQLAlchemy persistence** | 10 ORM tables; idempotent CRUD; automatic migrations on startup |
| **Cross-session import progress** | Import job state stored in DB; all browser sessions see live progress |
| **Report export** | Standalone HTML (Plotly), CSV, XLSX |
| **9-page Streamlit UI** | Import → Ledger → Modifiche massive → Analytics → Review → Regole → Tassonomia → Impostazioni → Check List |
| **Monthly coverage checklist** | Pivot table (month × account) showing transaction counts; highlights missing months at a glance |

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                            app.py  (Streamlit)                           │
│  upload │ ledger │ bulk-edit │ analytics │ review │ rules │ taxonomy │ settings  │
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
├── app.py                  # Streamlit entry point (9 pages)
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
│                           #   bulk_set_giroconto_by_description()
│                           #   get_transactions_by_rule_pattern()
│
├── reports/
│   ├── generator.py        # HTML (Jinja2+Plotly) · CSV · XLSX
│   └── template_report.html.j2
│
├── ui/
│   ├── sidebar.py          # Navigation buttons (9 pages) + giroconto mode
│   ├── upload_page.py      # Multi-file import + cross-session progress bar
│   ├── registry_page.py    # Filterable ledger + row-click selection + giroconto bulk-apply
│   ├── analysis_page.py    # 7 Plotly charts: monthly bars, cumulative balance,
│   │                       #   expense pie+treemap, category drill-down, income pie+treemap,
│   │                       #   top-10 descriptions, stacked by account + HTML export
│   ├── review_page.py      # Category correction + giroconto toggle + optional rule saving
│   ├── bulk_edit_page.py   # Bulk operations: category/context/giroconto + mass deletion by filter
│   ├── rules_page.py       # Full CRUD for CategoryRule + "Run all rules" bulk re-categorization
│   ├── taxonomy_page.py    # DB-backed CRUD for categories and subcategories
│   ├── settings_page.py    # Locale (date/amount format), language, LLM backend config
│   └── checklist_page.py   # Month × account pivot: transaction presence checklist
│
├── prompts/
│   ├── classifier.json     # System+user prompts for Flow 2 schema detection (invert_sign hint)
│   └── categorizer.json    # System+user prompts for transaction categorization
│
├── tests/
│   ├── test_normalizer.py      # Deterministic tests (parse_amount, SHA-256 …)
│   ├── test_backends.py        # Backend factory, validation, Ollama mock
│   ├── test_categorizer.py     # Static rules, cascade, taxonomy resolution
│   └── test_repository_rules.py  # Rule upsert, pattern matching, giroconto toggle, bulk ops
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

The app opens at `http://localhost:8501` with 9 pages:

| Page | Description |
|---|---|
| **📥 Import** | Upload one or more files (CSV / XLSX). Shows live progress (visible across all browser sessions). Summary: imported transactions, reconciliations, transfer links, flow used (1/2). |
| **📋 Ledger** | Filterable table (date, type, description, category, context, review flag). Click any row to select it instantly. Split Entrata/Uscita columns, right-aligned. Net/income/expense metrics. Context filter + assignment expander with Jaccard similarity suggestions. Giroconto toggle with bulk-apply. CSV/XLSX download. |
| **✏️ Modifiche massive** | Bulk operations on a reference transaction: giroconto toggle, context assignment (with Jaccard similarity), category correction + rule save. Mass deletion by combined filters (date, account, type, description, category) with preview and mandatory `ELIMINA` confirmation. |
| **📊 Analytics** | 7 interactive Plotly charts: monthly bar chart, cumulative balance, expense pie+treemap, interactive category drill-down with subcategory bar + monthly trend, income pie+treemap, top-10 descriptions, stacked by account. HTML export. |
| **🔍 Review** | Transactions with `to_review=True`. Giroconto toggle (with bulk-apply). Category/subcategory correction + optional save as permanent rule applied immediately. "Re-run LLM" button for uncleaned transactions. "Re-detect cross-account giroconti" button. |
| **📏 Regole** | Full CRUD for category rules. Edit/delete existing rules + optional bulk re-categorization of already-matched transactions. "▶️ Esegui tutte le regole" button applies all rules to every transaction in the ledger at once. |
| **🗂️ Tassonomia** | DB-backed CRUD for categories and subcategories (expenses and income). Changes take effect immediately without restarting. |
| **⚙️ Impostazioni** | Date format, amount separators, description language, life contexts, bank account list, LLM backend (model + API keys). All persisted in DB. |
| **✅ Check List** | Pivot table (month × account). Current month at top, descending. Cells show tx count; **—** = no transactions. Color-coded by volume. Filters: account selection, last N months, hide empty rows. CSV export. |

---

## Taxonomy

The taxonomy is stored in the database (`taxonomy_category` / `taxonomy_subcategory` tables) and managed from the **🗂️ Tassonomia** page. On first startup the DB is seeded from `taxonomy.yaml`.

**Expense categories (15):** Casa · Alimentari · Ristorazione · Trasporti · Salute · Istruzione · Abbigliamento · Comunicazioni · Svago e tempo libero · Animali domestici · Finanza e assicurazioni · Cura personale · Tasse e tributi · Regali e donazioni · Altro

**Income categories (7):** Lavoro dipendente · Lavoro autonomo · Rendite finanziarie · Rendite immobiliari · Trasferimenti e rimborsi · Prestazioni sociali · Altro entrate

**Subcategory is authoritative:** if the LLM or a rule assigns a subcategory that exists in the taxonomy, the correct parent category is resolved automatically — the two levels are always consistent in the DB.

---

## Rule engine

Category rules are stored in the `category_rule` table and applied at multiple points in the lifecycle:

### Rule matching

Rules support three match types, all case-insensitive:

| Match type | Behaviour |
|---|---|
| `contains` | Pattern appears anywhere in the description (case-insensitive) |
| `exact` | Description equals the pattern exactly (case-insensitive) |
| `regex` | Full Python regex matched against the description |

`get_transactions_by_rule_pattern` searches **all** transactions regardless of how they were previously categorized (LLM, rule, or manual correction). This means saving a new rule will correctly identify and update transactions that the LLM had already categorized.

### Rule priority

When multiple rules match the same transaction the one with the highest `priority` value wins. The default priority is 10; you can assign any integer.

### Upsert semantics

Creating a rule with the same `(pattern, match_type)` pair as an existing rule **updates** it in place (category, subcategory, priority), rather than creating a duplicate. The return value indicates whether the rule was newly created or updated.

### Retroactive application

Saving a rule from the **Ledger** or **Review** pages applies it immediately to all existing transactions that match the pattern, not just future imports. The success message reports how many transactions were updated. The same behaviour is available from the **Regole** page via the bulk re-categorization option on each rule.

Additionally, the **▶️ Esegui tutte le regole** button on the **Regole** page runs all rules against every transaction in the ledger at once (not limited to `to_review=True`). Useful after creating several rules at once or after importing historic data.

---

## Giroconto (internal transfers)

A *giroconto* is an internal fund movement between accounts you own (e.g., a transfer from a current account to a savings account, or a top-up of a prepaid card). Including both sides in the balance would cause double-counting.

### Transaction types

| `tx_type` | Meaning |
|---|---|
| `internal_out` | Outgoing side of an internal transfer (negative amount) |
| `internal_in` | Incoming side of an internal transfer (positive amount) |

Both types are excluded from net balance, income, and expense metrics.

### Automatic detection (RF-04)

The pipeline tries to match transfers automatically during import using three passes:

1. **Keyword regex** — description matches a configured keyword pattern (e.g. "Giroconto", "Bonifico tra i miei conti") → high confidence
2. **Amount + date matching** — same absolute amount within a ±3-day window, on different `account_label` values → medium/high confidence
3. **Owner-name permutation** — description contains any permutation of the account-holder name tokens → high confidence (catches "Corsaro Luigi Gerotti Elena" and "Luigi Corsaro Elena Gerotti" equally)

### Cross-account re-detection

When two counterpart transactions belong to files imported at different times, the first import cannot find the pair. Use the **"🔁 Riesegui rilevamento giroconti"** button on the **🔍 Review** page to re-run detection globally on all non-giroconto transactions and update newly detectable pairs.

### Manual toggle

From the **Ledger** or **Review** pages you can manually mark any transaction as a giroconto (or revert it):

- **Single toggle** — flips the `tx_type` of the selected transaction (`expense` ↔ `internal_out`, `income` ↔ `internal_in`).
- **Bulk apply** — if other transactions share the same description, a checkbox (default: enabled) offers to apply the same change to all of them in one click. The count of affected transactions is shown before confirming.

`bulk_set_giroconto_by_description` in `db/repository.py` implements the bulk operation: it updates all transactions with the given description except the one already toggled, and returns the number of rows changed.

---

## Life contexts

Life contexts are an orthogonal classification dimension that complements the category taxonomy. Where a category answers *what was bought*, a context answers *for which area of life*.

### Design

| Aspect | Detail |
|---|---|
| **Storage** | Nullable `VARCHAR(64)` column `context` on the `Transaction` table |
| **Orthogonality** | Independent of category/subcategory — any combination is valid |
| **User-configurable** | Add, rename, or remove contexts from the **⚙️ Impostazioni** page (stored as JSON in `user_settings`) |
| **Default contexts** | Quotidianità · Lavoro · Vacanza |

### Assignment

From the **📋 Ledger** page, select any transaction and open the "🌍 Assegna contesto" expander:

1. Choose a context from the dropdown (or clear it)
2. Optionally enable **"Applica anche a transazioni simili"** — Jaccard token similarity (threshold 0.35) finds other transactions whose cleaned description is semantically close and pre-fills the same context
3. Click **Applica**

### Filtering

The ledger's filter bar includes a context selector: *tutti*, individual context values, or *— nessuno —* (transactions with no context assigned).

---

## Tests

```bash
# Full suite (no LLM mocks required)
uv run python -m pytest tests/ -v

# With coverage
uv run python -m pytest tests/ --cov=core --cov=db --cov-report=term-missing
```

### Test files

| File | Coverage |
|---|---|
| `test_normalizer.py` | `parse_amount`, SHA-256 dedup, encoding detection |
| `test_backends.py` | Backend factory, validation, Ollama mock |
| `test_categorizer.py` | Static rules, 4-step cascade, taxonomy resolution |
| `test_repository_rules.py` | Rule upsert, `get_transactions_by_rule_pattern` (all match types + regression for LLM-sourced), `apply_rules_to_review_transactions`, `toggle_transaction_giroconto`, `bulk_set_giroconto_by_description` |

All tests use an in-memory SQLite database — no file I/O, no external services required.

---

## Design decisions

### `Decimal` — never `float`

All amounts are `decimal.Decimal`. IEEE 754 floats introduce rounding errors that corrupt balances and reconciliation results.

### SHA-256 idempotency

Each transaction has a 24-character `id` (truncated SHA-256) computed deterministically from `(source_file, date, amount, description)`. Re-importing the same file does not create duplicates.

### Card sign correction (`invert_sign`)

Italian card exports often store purchases as positive values. The `DocumentSchema.invert_sign` flag, set by the LLM during Flow 2 classification, instructs the normalizer to negate all amounts so that expenses become negative and refunds become positive — with a single symmetric operation.

#### Two-step detection algorithm

The classifier decides the value of `invert_sign` using a two-step algorithm. **Step 0 takes priority: if it fires, Step 1 is skipped entirely.** Step 1 is only consulted when Step 0 finds no definitive answer.

**Step 0 — Column name synonym check (highest priority)**

The column name is inspected for membership in one of three synonym groups:

| Group | Example names | Decision |
|---|---|---|
| **Outflow synonyms** | Uscita, Uscite, Addebito, Addebiti, Pagamento, Spesa, Dare, Importo addebitato | `invert_sign = true` (expenses stored as positive → negate) |
| **Inflow synonyms** | Entrata, Entrate, Accredito, Accrediti, Avere, Credito, Importo accreditato | `invert_sign = false` (incomes already positive → no change) |
| **Neutral names** | Importo, Amount, Valore, Totale | No decision — proceed to Step 1 |

Outflow and inflow synonym matching is case-insensitive and partial (e.g. "Addebiti carta" matches "Addebito"). The outflow rule applies to card doc_types only; bank account and savings files always keep `invert_sign = false` regardless of column name.

**Step 1 — Sign distribution analysis (neutral column names only)**

When Step 0 finds a neutral column name it cannot classify by name alone, the classifier counts positive vs. negative values in the sample and computes `positive_ratio` and `negative_ratio`:

- Card file, majority positive (> 60 %): expenses are stored as positive (AMEX / typical Italian export convention) → `invert_sign = true`
- Card file, majority negative (> 60 %): expenses already carry the correct sign → `invert_sign = false`
- Roughly 50/50 split: descriptions are inspected (merchant names with positive amounts → `invert_sign = true`; "bonifico ricevuto" with positive amounts → `invert_sign = false`)
- Bank account / savings: always `invert_sign = false`, regardless of distribution

#### Diagnostic fields

Every `DocumentSchema` produced by Flow 2 includes four diagnostic fields for audit and debugging:

| Field | Type | Content |
|---|---|---|
| `positive_ratio` | `float \| null` | Fraction of amount-column values > 0 in the sample |
| `negative_ratio` | `float \| null` | Fraction of amount-column values < 0 in the sample |
| `semantic_evidence` | `list[str]` | 2–4 short sentences from the LLM explaining the decision |
| `normalization_case_id` | `str \| null` | C1 = bank signed_single · C2 = card inverted · C3 = card already negative · C4 = Dare/Avere columns · C5 = ambiguous |

These fields are persisted in the `document_schema` DB table and are visible in the Flow 2 schema review step in the UI.

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
