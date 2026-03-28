# Spendify — Developer Guide

> Version: 3.0 — updated 2026-03-21
>
> For user features and quick reference see **[reference_guide.en.md](reference_guide.en.md)**.
> For detailed technical documentation (DB, pipeline, deployment, etc.) see the `documents/` folder.

---

## Table of Contents

1. [Layered architecture](#1-layered-architecture)
2. [Development environment setup](#2-development-environment-setup)
3. [Project structure](#3-project-structure)
4. [Service layer](#4-service-layer)
5. [Coupling gate (CI)](#5-coupling-gate-ci)
6. [REST API](#6-rest-api)
7. [Tests](#7-tests)
8. [Key design decisions](#8-key-design-decisions)
9. [Technical reference documentation](#9-technical-reference-documentation)

---

## 1. Layered architecture

```
┌──────────────────────────────────────────────────────┐
│                   app.py  (Streamlit)                │
│  ui/upload  ui/ledger  ui/analytics  ui/settings … │
└──────────────────────┬───────────────────────────────┘
                       │  imports only from services.*
┌──────────────────────▼───────────────────────────────┐
│                  services/                           │
│  ImportService · TransactionService · RuleService    │
│  SettingsService · CategoryService · ReviewService  │
└──────┬────────────────────────────────────┬──────────┘
       │                                    │
┌──────▼──────┐                    ┌────────▼────────┐
│   core/     │                    │    db/          │
│ orchestrator│                    │ models.py       │
│ normalizer  │                    │ repository.py   │
│ classifier  │                    └─────────────────┘
│ categorizer │
│ sanitizer   │
└─────────────┘
```

**Fundamental rule:** `ui/` modules import **only** from `services.*`.
They must never import directly from `core.*`, `db.*`, or `support.*`.
This rule is enforced automatically in CI (see §5).

---

## 2. Development environment setup

### Prerequisites

| Tool | Minimum version |
|------|----------------|
| Python | 3.13 |
| uv | any |
| Docker Desktop | optional (local smoke test) |

### Installation

```bash
git clone https://github.com/drake69/spendify.git
cd spendify
uv sync
cp .env.example .env

# Startup script (recommended)
./start.sh          # UI only (default)
./start.sh api      # REST API only
./start.sh all      # UI + API

# Or manually
uv run streamlit run app.py
```

App available at `http://localhost:8501`.

### Environment variables

`.env` contains only:

```
SPENDIFY_DB=sqlite:///ledger.db   # SQLite DB path
```

LLM configuration (backend, model, API key) lives in the database and is managed from the UI → Settings page.

### System Settings (developer tuning)

Internal tuning parameters **not exposed in the UI**. For developers and power users only.

**File:** `config/system_settings.yaml` (repo defaults) + `~/.spendify/system_settings.yaml` (local overrides)

The loader (`config/__init__.py`) reads repo defaults, then deep-merges with the local file. Unspecified keys keep their default. Set `SPENDIFY_SYSTEM_SETTINGS` env var for a custom path.

| Section | Key parameters | Defaults |
|---------|---------------|----------|
| `history` | `min_validated`, `auto_threshold`, `suggest_threshold` | 5, 0.90, 0.50 |
| `history_context` | `min_validated`, `min_confidence`, `top_n`, `max_chars` | 3, 0.50, 50, 2000 |
| `classifier` | `confidence_threshold`, `max_transaction_amount` | 0.80, 1000000 |
| `border_detection` | `max_scan_rows`, `min_region_cols`, `min_region_rows` | 60, 3, 3 |
| `categorizer` | `batch_size`, `llm_timeout_s` | 20, 120 |
| `footer` | `max_tail_rows`, `phase2_enabled` | 10, true |

---

## 3. Project structure

```
spendify/
├── app.py                  # Streamlit entry point
├── config/                 # system settings (YAML, not UI-exposed)
│   ├── __init__.py         # loader with deep merge
│   └── system_settings.yaml # tuning defaults
├── ui/                     # Streamlit pages (imports only from services.*)
├── services/               # service layer — facade between UI and core/db
│   ├── import_service.py
│   ├── transaction_service.py
│   ├── rule_service.py
│   ├── settings_service.py
│   ├── category_service.py
│   └── review_service.py
├── core/                   # pure domain logic (no UI, no DB)
│   ├── orchestrator.py     # pipeline entry point
│   ├── normalizer.py
│   ├── classifier.py
│   ├── categorizer.py
│   ├── description_cleaner.py
│   └── sanitizer.py
├── db/                     # ORM, migrations, repository
│   ├── models.py           # SQLAlchemy tables + idempotent migrations
│   ├── repository.py       # CRUD queries for services
│   └── taxonomy_defaults.py # taxonomy templates for 5 languages
├── api/                    # FastAPI REST API (optional)
│   ├── main.py
│   └── routers/
├── tests/                  # pytest — 453+ tests, no DB mocks
├── tools/                  # development tools
│   ├── coupling_check.py   # static analysis of UI → service imports
│   └── coupling_baseline.json
└── docs/                   # public documentation in the repo
    ├── reference_guide.en.md
    └── developer_guide.en.md  # ← this file
```

---

## 4. Service layer

Each service is a class that takes `engine: Engine` in its constructor and encapsulates all operations for a domain. The UI never sees SQLAlchemy or `core` models directly.

### ImportService — complete facade

`ImportService` is the access point for the entire import pipeline. It re-exports domain types (`DocumentType`, `SignConvention`, `DocumentSchema`, etc.) via `__all__` so the UI never needs to import from `core.*`.

```python
from services.import_service import ImportService, DocumentType, SignConvention

svc = ImportService(engine)
analysis = svc.analyze_file(raw_bytes, filename)
config   = svc.build_config(giroconto_mode="neutral")
result   = svc.process_file_single(raw_bytes, filename, config)
svc.persist_result(result)
```

> **Note:** `giroconto_mode` (`neutral`/`exclude`) controls only the visibility in views (Ledger, Analytics, Reports). Internal transfers are **always detected and always persisted** to the database as `internal_in`/`internal_out`, regardless of the chosen mode. This ensures reconciliation and data integrity.

### SettingsService — user configuration

Reads and writes `user_settings` (key-value). Exposes:

```python
svc.get(key, default)
svc.set(key, value)
svc.set_bulk(dict)
svc.is_onboarding_done()
svc.set_onboarding_done()
svc.apply_default_taxonomy(language)   # 'it' | 'en' | 'fr' | 'de' | 'es'
```

### Onboarding

On the first run with an empty DB, `app.py` shows the onboarding wizard (4 steps: language, owner names, accounts, confirmation). After completing the wizard, `set_onboarding_done()` is called and the app reloads normally.

For existing installations (DB with data) onboarding is skipped automatically: `_migrate_set_onboarding_done_for_existing_users()` in `db/models.py` sets the flag if `taxonomy_category` already has rows.

---

## 5. Coupling gate (CI)

`tools/coupling_check.py` statically analyzes all `ui/` files and verifies they do not import from `core.*`, `db.*`, or `support.*`.

```bash
# Local run
uv run python tools/coupling_check.py --strict

# Expected output
✅ Coupling check passed — 0 violations across 12 UI files
```

The `coupling-check` job in `.github/workflows/ci.yml` runs `--strict --json` and posts a Markdown comment on the PR with per-file detail. A file with new violations fails the CI.

**Baseline:** `tools/coupling_baseline.json` — currently empty `{}` (all files must have 0 violations). Adding a file to the baseline is possible but requires an explicit justification in the JSON.

---

## 6. REST API

An optional FastAPI server exposes core operations as REST endpoints.

```bash
uv run uvicorn api.main:app --reload --port 8000
# Interactive docs: http://localhost:8000/docs
```

The server uses the same `services.*` as the Streamlit UI — no duplicated logic.

---

## 7. Tests

```bash
# All tests
uv run pytest tests/ -v

# With coverage
uv run pytest tests/ -v --cov=. --cov-report=term-missing

# Single module
uv run pytest tests/test_normalizer.py -v
```

**Coverage thresholds:**

| Module | Minimum |
|--------|---------|
| `core/normalizer.py` | 100% |
| `core/description_cleaner.py` | 100% |
| `core/classifier.py` | ≥ 99% |
| All others | ≥ 80% |

Tests use SQLite in-memory (`create_engine("sqlite://")`) — no DB mocks.

---

## 8. Key design decisions

| Decision | Rationale |
|----------|-----------|
| `Decimal` for amounts, never `float` | Avoids rounding errors in financial calculations |
| SHA-256 as `tx_id` | Idempotent import: re-importing the same file never creates duplicates |
| Idempotent migrations (`CREATE TABLE IF NOT EXISTS`, `INSERT OR IGNORE`) | Safe updates on existing DBs without separate migration scripts |
| Offline-first LLM (Ollama default) | Privacy: no financial data leaves the machine by default |
| PII sanitization before any remote call | IBANs, cards, tax codes, and names replaced in memory before sending |
| Service layer as sole access point for UI | Decoupling that allows testing logic independently of Streamlit |
| Default taxonomy in DB (not YAML) | Multi-language support (it/en/fr/de/es) without additional config files |

---

## 9. Technical reference documentation

Detailed engineering documentation is in `documents/` (outside the repo):

| File | Content |
|------|---------|
| `documents/progetto.en.md` | Project document: goals, stack, architecture |
| `documents/pipeline.en.md` | Step-by-step import pipeline |
| `documents/database.en.md` | Full DB schema, migrations, backup/restore |
| `documents/deployment.en.md` | Docker deployment, environment variables, updates |
| `documents/configurazione.en.md` | All configurable parameters, LLM providers, API keys |
| `documents/deterministic_rules.en.md` | Rule engine: syntax, priority, retroactive application |
| `documents/deterministic_tools.en.md` | Debug and pipeline analysis tools |
| `documents/installazione.en.md` | Native installation (Mac/Linux/Windows), Docker |
| `documents/guida_utente.en.md` | End-user operational guide |
| `documents/landing_page.en.md` | Landing page copy |

For contributing code see also **[CONTRIBUTING.md](../CONTRIBUTING.md)**.
