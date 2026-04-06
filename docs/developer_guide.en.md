# Spendif.ai — Developer Guide

> Version: 3.0 — updated 2026-04-05
>
> For user features and quick reference see **[reference_guide.en.md](reference_guide.en.md)**.
> For detailed technical documentation (DB, pipeline, deployment, etc.) see the `documents/` folder.

---

## Table of Contents

1. [Layered architecture](#1-layered-architecture)
2. [Development environment setup](#2-development-environment-setup)
3. [Project structure](#3-project-structure)
4. [Service layer](#4-service-layer)
5. [Multi-step classifier](#5-multi-step-classifier)
6. [Coupling gate (CI)](#6-coupling-gate-ci)
7. [REST API](#7-rest-api)
7b. [Support chatbot](#7b-support-chatbot)
8. [Tests](#8-tests)
9. [Key design decisions](#9-key-design-decisions)
10. [Technical reference documentation](#10-technical-reference-documentation)

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
│  ImportService · TransactionService · RuleService         │
│  SettingsService · CategoryService · NsiTaxonomyService   │
│  ReviewService · BudgetService                            │
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
cd spendifai
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
SPENDIFAI_DB=sqlite:///ledger.db   # SQLite DB path
```

LLM configuration (backend, model, API key) lives in the database and is managed from the UI → Settings page.

### System Settings (developer tuning)

Internal tuning parameters **not exposed in the UI**. For developers and power users only.

**File:** `config/system_settings.yaml` (repo defaults) + `~/.spendifai/system_settings.yaml` (local overrides)

The loader (`config/__init__.py`) reads repo defaults, then deep-merges with the local file. Unspecified keys keep their default. Set `SPENDIFAI_SYSTEM_SETTINGS` env var for a custom path.

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
spendifai/
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

### NsiTaxonomyService — OSM tag → taxonomy mapping (C-08-cascade)

`NsiTaxonomyService` builds and maintains the `taxonomy_map`: a dictionary `{osm_tag → (category, subcategory)}` that enables direct LLM bypass for transactions identified via NSI.

```python
from services.nsi_taxonomy_service import NsiTaxonomyService

svc = NsiTaxonomyService(engine)
with session_scope() as s:
    taxonomy_map = svc.get_or_build(s, taxonomy, llm_backend)
```

**Lifecycle:**
- Stored in DB table `nsi_tag_mapping` (persistent across restarts)
- Automatically invalidated if the user's taxonomy changes (SHA-256 hash)
- First build: LLM call → static fallback from `osm_to_spendifai_map.json`
- Italian default taxonomy → 14+ tags mapped without LLM (`_static_map()`)

**Developer workflow (when to update `static_rules.json`):**
```bash
# After each NSI release or brand addition
python scripts/build_static_rules.py
git add core/static_rules.json
git commit -m "feat(nsi): update static_rules.json — NSI vX"
```
Users' `taxonomy_map` is automatically invalidated on the next import.

### CategoryService — categorization cascade

`CategoryService` orchestrates the 5-step cascade for each transaction:

| Step | Source | Condition | `source` |
|------|--------|-----------|----------|
| **0** | User rules (`category_rule` DB) | Deterministic pattern match | `rule` |
| **2** | History (`human_validated=True`) | Confidence ≥ threshold | `history` |
| **3b** | NSI + `taxonomy_map` | Brand match + `user_country ∈ countries` + osm_tag in map | `nsi` |
| **3b** | NSI hint | Brand match without bypass | `llm` (hint injected) |
| **4** | LLM batch | Unresolved transactions | `llm` |
| **5** | Fallback | No match | `llm` + `to_review=True` |

> **Note on Step 1 (deprecated):** The linguistic rules in `core/static_rules/_it.json` were deprecated in C-08-cascade. They operated on the raw description (payment reference), but `clean_descriptions_batch()` extracts only the counterparty name before categorization, making the regex patterns ineffective. Brands are now better covered by NSI (Step 3b).

### Onboarding

On the first run with an empty DB, `app.py` shows the onboarding wizard (4 steps: language, owner names, accounts, confirmation). After completing the wizard, `set_onboarding_done()` is called and the app reloads normally.

For existing installations (DB with data) onboarding is skipped automatically: `_migrate_set_onboarding_done_for_existing_users()` in `db/models.py` sets the flag if `taxonomy_category` already has rows.

---

## 5. Multi-step classifier

The classifier supports a 3-step sequential LLM pipeline where each step's output feeds as context into the next. This improves accuracy on small models that struggle to produce the full schema in a single call.

### 3-step architecture

| Step | Purpose | Output |
|------|---------|--------|
| **Step 1 — Document Identity** | Identify document type and reading parameters | `doc_type`, `encoding`, `delimiter`, `sheet_name`, `skip_rows` |
| **Step 2 — Column Mapping** | Map file columns to Spendif.ai fields | `date_col`, `amount_col`, `description_col`, `balance_col`, `credit_col`, `debit_col` |
| **Step 3 — Semantic Analysis** | Analyze value semantics (sign, date format, etc.) | `sign_convention`, `invert_sign`, `date_format`, `decimal_separator`, `account_holder` |

Each step receives the output of previous steps as context, allowing the model to focus on one sub-problem at a time.

### Key files and functions

| Component | Location | Role |
|-----------|----------|------|
| `_classify_multi_step()` | `core/classifier.py` | Orchestrates the 3 steps with error handling and fallback |
| `MultiStepDiagnostics` | `core/classifier.py` | Dataclass with per-step diagnostics (prompt, raw response, parsed JSON, duration) |
| `step1_json_schema()` | `core/schemas.py` | JSON Schema for Step 1 response |
| `step2_json_schema()` | `core/schemas.py` | JSON Schema for Step 2 response |
| `step3_json_schema()` | `core/schemas.py` | JSON Schema for Step 3 response |
| `fill_llm_defaults()` | `core/schemas.py` | Applies default values to optional fields not returned by the model |

### Classification mode (`classifier_mode` in `ProcessingConfig`)

| Value | Behavior |
|-------|----------|
| `"auto"` | **Default.** Auto-selects based on model size (see below) |
| `"single"` | Single LLM call (everything in one prompt) |
| `"multi_step"` | Forces the 3-step pipeline |

### Auto-detect logic

The auto mode selects the strategy based on backend and model size:

- **Local GGUF models < 5 GB** → `multi_step` (small models benefit from decomposition)
- **Local GGUF models >= 5 GB** → `single` (large models handle the full prompt well)
- **Remote backends** (OpenAI, Anthropic, etc.) → `single`

### Degradation

| Failure | Behavior |
|---------|----------|
| Step 1 fails | **Abort** — cannot proceed without document type |
| Step 2 fails | **Phase 0 fallback** — attempts parsing with deterministic rules |
| Step 3 fails | **Defaults** — `fill_llm_defaults()` applies defaults; `confidence` set to `low` |

---

## 6. Coupling gate (CI)

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

## 7. REST API

An optional FastAPI server exposes core operations as REST endpoints.

```bash
uv run uvicorn api.main:app --reload --port 8000
# Interactive docs: http://localhost:8000/docs
```

The server uses the same `services.*` as the Streamlit UI — no duplicated logic.

---

## 7b. Support chatbot

The `chat_bot/` module implements an adaptive chatbot that answers questions about Spendif.ai usage. The mode is auto-selected based on the user's LLM backend setting in Settings.

### Architecture

```
chat_bot/
├── engine.py           # ChatBotEngine — orchestrator, auto-detect mode
├── rag.py              # RAGEngine — TF-IDF retrieval + LLM generation
├── faq_classifier.py   # FAQClassifier — deterministic TF-IDF match (zero LLM)
├── kb_store.py         # Loads FAQ (JSON/MD) and doc chunks
├── prompts.json        # System prompts + multi-language no-answer messages
└── knowledge/<lang>/   # FAQ and docs per language (it, en)
    ├── faq.json        # [{"q": "...", "a": "...", "page": "<page_key>"}] — 150 items
    └── docs/           # .md/.txt files chunked for RAG (manuals, guides, etc.)
```

**Corpus status (2026-04-05):** `knowledge/it/` and `knowledge/en/` populated with 150 Q&A
(`faq_can_do` + `faq_cannot_do`) and 5 markdown manuals in `docs/`.
Canonical source: `documents/06_knowledge_base/` — edit there and regenerate.

### Three modes

| Mode | Condition (from user settings) | Behaviour |
|------|-------------------------------|-----------|
| `rag_cloud` | Backend = `openai` / `claude` / `openai_compatible` with API key | TF-IDF retrieval → cloud LLM generates answer |
| `rag_local` | Backend = `local_ollama` / `vllm` | TF-IDF retrieval → local LLM generates answer |
| `faq_match` | Backend = `local_llama_cpp` or none | Cosine similarity on FAQ, pre-built answer |

**Note:** `ChatBotEngine` is cached in `st.session_state["chatbot"]` and automatically
invalidated when the LLM backend changes (cleared in `settings_page.py` on save).

### Source display

`kb_store.py` exposes a `page_ref` field (`FAQEntry.page_ref`) containing the app page key
(e.g. `"import"`, `"review"`). `chat_page.py` renders it as a navigable label (`→ 📥 Import`).
Doc chunk sources show the filename (`📄 user_guide.md`). Sources without a `page_ref` are suppressed.

### Project integration

- **LLM Backend:** Uses `BackendFactory` from `core/llm_backends.py` — same backend as the user's
- **Settings:** Reads `llm_backend` and API keys from `user_settings` (DB) via `get_all_user_settings()`
- **UI:** `ui/chat_page.py` follows the `render_X_page(engine)` pattern, with `st.chat_message`
- **i18n:** Keys `chat.*` and `nav.chat*` in `ui/i18n/{it,en}.json`
- **Sidebar:** Entry `("chat", "chat")` in `_NAV_KEYS`

### Programmatic usage

```python
from chat_bot.engine import ChatBotEngine

bot = ChatBotEngine(db_engine=engine, lang="en")
print(bot.mode)       # ChatMode.FAQ_MATCH | RAG_LOCAL | RAG_CLOUD
response = bot.ask("How do I import a file?")
print(response.text)
print(response.sources)  # ["import", "review", ...] — page_ref keys
```

### Populating the knowledge base

1. Edit `documents/06_knowledge_base/faq_can_do.en.json` / `faq_cannot_do.en.json`
2. Run the conversion script to regenerate `knowledge/{lang}/faq.json`
3. For manuals: edit `.md` files in `documents/06_knowledge_base/` and copy to `knowledge/en/docs/`
4. Never index sensitive data (credentials, personal data, business strategies)

---

## 8. Tests

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

### LLM Benchmark — quick start

The recommended entry point for a full benchmark across all backends and both phases:

```bash
# macOS / Linux — RECOMMENDED (all backends × pipeline + categorizer)
bash benchmark/run_benchmark_full.sh                             # classifier + categorizer, 1 run
bash benchmark/run_benchmark_full.sh --benchmark classifier      # classifier only
bash benchmark/run_benchmark_full.sh --benchmark both --runs 3   # both phases, 3 runs each
bash benchmark/run_benchmark_full.sh --setup-only                # download models only

# Windows (PowerShell)
powershell -ExecutionPolicy Bypass -File .\tests\run_benchmark_full.ps1
powershell -ExecutionPolicy Bypass -File .\tests\run_benchmark_full.ps1 -Benchmark both -Runs 3

# llama.cpp only (skip Ollama and vLLM)
bash benchmark/run_benchmark_full.sh --skip-ollama --skip-vllm
```

`run_benchmark_full.sh` / `run_benchmark_full.ps1` perform full setup (download missing GGUF models, `ollama pull` missing Ollama models, detect vLLM), then run **classifier** and **categorizer** benchmarks for every active backend. The model list is read from `benchmark/benchmark_models.csv`. Flags: `--benchmark classifier|categorizer|both`, `--runs N`, `--setup-only`, `--skip-llama/ollama/vllm`, `--vllm-url`, `--ollama-url` (PS1 equivalents: `-Benchmark`, `-Runs`, `-SetupOnly`, `-SkipLlama`, `-SkipOllama`, `-SkipVllm`, `-VllmUrl`, `-OllamaUrl`).

### Model catalogue (benchmark_models.csv)

`benchmark/benchmark_models.csv` is the single source of truth for the model list used by all benchmark scripts, replacing hardcoded arrays. Columns: `name`, `gguf_file`, `gguf_repo`, `gguf_hf_url`, `ollama_tag`, `enabled`. A populated `gguf_file` makes the model available on llama.cpp; a populated `ollama_tag` makes it available on Ollama. Set `enabled=false` to skip a model in all scripts. The catalogue contains 11 models (Qwen2.5-1.5B, Gemma2-2B, Qwen3.5-2B, Qwen3.5-4B, Gemma4-E2B Q3+Q4, Llama3.2-3B, Qwen2.5-3B, Phi3-mini, Qwen2.5-7B, Gemma3-12B). vLLM models are not in the CSV — they are auto-detected from the running server at runtime.

### LLM Benchmark — HW monitoring

The benchmark suite (`benchmark/benchmark_classifier.py`, `benchmark/benchmark_categorizer.py`) includes cross-platform GPU monitoring via `benchmark/hw_monitor.py`. A background thread (`HWMonitor`) samples CPU and GPU utilization every 0.5 s for the duration of each run, replacing the old point-in-time sampling functions.

| Platform | GPU method |
|----------|-----------|
| macOS Apple Silicon | `ioreg` / AGXAccelerator (no sudo) |
| Linux NVIDIA | `nvidia-smi` (utilization + power) |
| Linux AMD | `rocm-smi` (utilization) |
| Fallback | 0.0 |

All GGUF models are now benchmarked regardless of file size (the previous 3 GB filter has been removed).

### Session versioning (bench_guard)

`benchmark/bench_guard.sh` / `bench_guard.ps1` — invoked automatically by `run_benchmark_full` at startup — ensure every benchmark session has a unique version string stamped into the `version` field of all produced CSVs.

| Context | Behaviour |
|---------|-----------|
| **Dev machine** (git available) | Regenerates `benchmark/.version` = `YYYYMMDDHHMMSS-<sha7>`. Fresh on every launch, independent of commits. |
| **Remote bench machine** (no git, `.version` present) | Uses the `.version` written by `bench_push_usb` / `bench_push_ssh`. |
| **No git + no `.version`** | **Fatal error** with a hint pointing to the deployment scripts. |

This allows the monitor to isolate the current session by filtering on `version` (same SHA+timestamp = same session), without requiring explicit pushes or commits on the dev machine.

### Benchmark progress monitor

`benchmark/monitor_benchmark.sh` / `monitor_benchmark.ps1` / `monitor_benchmark.py` show real-time benchmark progress. They read all archive files in `benchmark/results/*.csv` (not `results_all_runs.csv`, which is only produced by the offline aggregator) and filter by the `version` field to isolate the current session. Features: per-model progress bars with elapsed time and ETA, current pipeline phase (classifier/categorizer) detected from the `benchmark_type` column, live CPU/GPU stats via `HWMonitor.sample_once()`, and historical averages from the CSV. Options: `--interval N` (refresh in seconds), `--runs N` (expected runs per model), `--total N` (expected total rows), `--once` (print snapshot and exit), `--all` (show completed models too).

### Categorizer benchmark scenarios (`--scenario`)

The categorizer benchmark supports predefined scenarios that simulate different levels of "warm data" available at categorization time. The scenario controls which deterministic sources are active before the LLM call.

| Scenario | Active warm data | Expected LLM% | Notes |
|---|---|---|---|
| `cold` (default) | none | ~70% | Pure baseline — new user, zero history |
| `nsi_warm` | NSI + taxonomy_map | ~30-40% | Public sources only, no history |
| `cross_warm` | NSI + leave-one-out history | ~20-50% | **Realistic**: history from all GT files *except* the current one. Simulates a user with prior validated history encountering a new file |
| `full_warm` | NSI + history (all GT) | <5% | Theoretical upper bound — 100% by construction (includes the file itself) |
| `country_with` | NSI + country ranking | ~30-40% | Like nsi_warm with geographic bias |
| `country_without` | NSI, no country | ~30-40% | — |
| `all` | runs all scenarios in sequence | — | Order: cold → nsi_warm → cross_warm → full_warm → country |

> **Interpreting the scenarios:**
> `cold` and `full_warm` are the two extremes (lower/upper bound). `cross_warm` is the
> most representative of real usage: it measures the benefit of history on counterparts
> **recurring across different files** (e.g. same bank, different months).
> `full_warm` at 100% is tautological — do not use it as a primary metric.

> **💡 Key empirical finding — Counterpart extraction is deterministic**
>
> The 100% accuracy in `full_warm` does **not** mean the LLM "learns" from previous transactions.
> It means the **counterpart extraction step is deterministic and consistent across runs**:
> given the same raw bank string, the model always produces the same normalised merchant name
> (e.g. `PAGAM. POS 549,91 EUR DEL 01.01 CARTOLIBRERIA IL PAPIRO` → `Cartolibreria Il Papiro`).
> This makes the history cache key stable, and deterministic lookup reliable.
>
> **The LLM has invariant per-call quality** — it does not improve with use. What grows over
> time is the **deterministic shield** (history + rules + NSI-map), which monotonically reduces
> the number of LLM calls. The LLM invocation rate per transaction is the system maturity
> metric — not its cold-start accuracy.

**New CSV columns** produced per run with scenario: `scenario`, `n_nsi`, `nsi_accuracy`, `nsi_coverage_pct`, `taxonomy_map_hit_pct`.

> **⚠️ Known limitation — taxonomy_map shared across models**
>
> The benchmark builds the `taxonomy_map` (OSM tag → category/subcategory) **once per session**, before the model loop, using the static part of `NsiTaxonomyService` (`osm_to_spendifai_map.json`).
> In the real pipeline, the `taxonomy_map` also includes an LLM-assisted component (`_llm_map`) that varies per model, and is automatically invalidated when the user's taxonomy changes (via SHA-256).
>
> **Implications for the user:**
> - Comparing `nsi_warm` scenarios across different models in the same session may underestimate the advantage of more capable models in the mapping phase.
> - If the taxonomy changes between benchmark sessions, always relaunch the full session to ensure `taxonomy_map` consistency.
>
> **Roadmap:** rebuild the `taxonomy_map` for each tested model (adds ~N seconds per model, trade-off documented in T-09).

**Entry point:**
```bash
bash benchmark/run_benchmark_full.sh --scenario nsi_warm
bash benchmark/run_benchmark_full.sh --scenario all   # all scenarios in sequence
```

### Available scripts

| Script | Purpose |
|--------|---------|
| `benchmark/run_benchmark_full.sh` | **ENTRY POINT** (macOS/Linux): all backends × pipeline + categorizer |
| `benchmark/run_benchmark_full.ps1` | **ENTRY POINT** (Windows): all backends × pipeline + categorizer |
| `benchmark/bench_guard.sh` | Version gate (macOS/Linux): generates/verifies `benchmark/.version` |
| `benchmark/bench_guard.ps1` | Version gate (Windows): generates/verifies `benchmark\.version` |
| `benchmark/benchmark_models.csv` | Model catalogue (replaces hardcoded arrays in scripts) |
| `benchmark/monitor_benchmark.sh` | Benchmark progress monitor (macOS/Linux) |
| `benchmark/monitor_benchmark.ps1` | Benchmark progress monitor (Windows) |
| `benchmark/monitor_benchmark.py` | Benchmark progress monitor (cross-platform Python) |
| `benchmark/hw_monitor.py` | Background HW monitoring (CPU + GPU, cross-platform) |
| `benchmark/diagnose.ps1` | Windows environment diagnostics including GPU detection |

### Logging

Each run saves a log to `benchmark/logs/` (gitignored, one timestamped file per run):

| Script | Log |
|--------|-----|
| `run_benchmark_full.sh` | `benchmark/logs/benchmark_YYYYMMDD_HHMMSS.log` |
| `benchmark_classifier.py` | `benchmark/logs/classifier_YYYYMMDD_HHMMSS.log` |
| `benchmark_categorizer.py` | `benchmark/logs/categorizer_YYYYMMDD_HHMMSS.log` |
| `diagnose.ps1` | `~/spendifai_diagnose_YYYYMMDD_HHMMSS.log` |

Output goes to both console and file simultaneously (tee). On Windows, run `diagnose.ps1` first to check prerequisites including GPU detection (NVIDIA/AMD/Intel).

See [`tests/README.md`](../tests/README.md) for full benchmark documentation.

---

## 9. Key design decisions

| Decision | Rationale |
|----------|-----------|
| `Decimal` for amounts, never `float` | Avoids rounding errors in financial calculations |
| SHA-256 as `tx_id` | Idempotent import: re-importing the same file never creates duplicates |
| Idempotent migrations (`CREATE TABLE IF NOT EXISTS`, `INSERT OR IGNORE`) | Safe updates on existing DBs without separate migration scripts |
| Offline-first LLM (Ollama default) | Privacy: no financial data leaves the machine by default |
| PII sanitization before any remote call | IBANs, cards, tax codes, and names replaced in memory before sending |
| Service layer as sole access point for UI | Decoupling that allows testing logic independently of Streamlit |
| Default taxonomy in DB (not YAML) | Multi-language support (it/en/fr/de/es) without additional config files |
| NSI + `taxonomy_map` for LLM bypass | Known brands (e.g. Esselunga, Q8) categorized deterministically without LLM when `user_country` is confirmed. `nsi_tag_mapping` stored in DB, invalidated by SHA-256 on taxonomy changes. |
| `LlamaCppBackend.get_context_info()` — `n_ctx_train` fallback | `llama-cpp-python >= 0.3.x` removed `n_ctx_train()`. Code now catches `AttributeError` and falls back to GGUF metadata (`read_gguf_context_length`) or `n_ctx()`. |
| Match preview in "New rule" form (issue #15) | Before creating a rule, `rules_page.py` calls `tx_svc.get_by_rule_pattern()` and shows how many existing transactions match. If N > 0, a checkbox (default True) lets the user also apply the category to those transactions — same behaviour as the edit-rule form. |
| Chatbot: `ChatBotEngine` invalidated on backend change | The chatbot is cached in `st.session_state["chatbot"]`. `settings_page.py` clears it on LLM settings save, forcing re-initialisation with the new backend on next visit to the Chat page. |
| Chatbot: TF-IDF over 150 Q&A + 5 manuals, no vector DB | Total corpus < 1 MB: in-memory TF-IDF is sufficient. A vector DB (Chroma/Qdrant) would only be added if the corpus exceeded ~500 items. Canonical source in `documents/06_knowledge_base/`; `knowledge/{lang}/` is a generated artifact. |

---

## 10. Technical reference documentation

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
