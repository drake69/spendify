# Spendify — Developer Guide

> Versione: 3.0 — aggiornato 2026-03-21
>
> Per le funzionalità utente e il reference rapido vedi **[reference_guide.md](reference_guide.md)**.
> Per la documentazione tecnica dettagliata (DB, pipeline, deployment, ecc.) vedi la cartella `documents/`.

---

## Indice

1. [Architettura a layer](#1-architettura-a-layer)
2. [Setup ambiente di sviluppo](#2-setup-ambiente-di-sviluppo)
3. [Struttura del progetto](#3-struttura-del-progetto)
4. [Service layer](#4-service-layer)
5. [Classifier multi-step](#5-classifier-multi-step)
6. [Coupling gate (CI)](#6-coupling-gate-ci)
7. [REST API](#7-rest-api)
8. [Test](#8-test)
9. [Prompt Integrity Guard (S-01)](#9-prompt-integrity-guard-s-01)
10. [Benchmark (T-09)](#10-benchmark-t-09)
11. [Decisioni di design chiave](#11-decisioni-di-design-chiave)
12. [Documentazione tecnica di riferimento](#12-documentazione-tecnica-di-riferimento)

---

## 1. Architettura a layer

```
┌──────────────────────────────────────────────────────┐
│                   app.py  (Streamlit)                │
│  ui/upload  ui/ledger  ui/analytics  ui/settings … │
└──────────────────────┬───────────────────────────────┘
                       │  importa solo da services.*
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

**Regola fondamentale:** i moduli `ui/` importano **solo** da `services.*`.
Non devono mai importare direttamente da `core.*`, `db.*`, `support.*`.
Questa regola è verificata automaticamente in CI (vedi §5).

---

## 2. Setup ambiente di sviluppo

### Prerequisiti

| Strumento | Versione minima |
|-----------|----------------|
| Python | 3.13 |
| uv | qualsiasi |
| Docker Desktop | opzionale (smoke test locale) |

### Installazione

```bash
git clone https://github.com/drake69/spendify.git
cd spendify
uv sync
cp .env.example .env

# Script di avvio (consigliato)
./start.sh          # solo UI (default)
./start.sh api      # solo REST API
./start.sh all      # UI + API

# Oppure manualmente
uv run streamlit run app.py
```

App disponibile su `http://localhost:8501`.

### Variabili d'ambiente

`.env` contiene solo:

```
SPENDIFY_DB=sqlite:///ledger.db   # percorso DB SQLite
```

La configurazione LLM (backend, modello, API key) vive nel database e si gestisce dall'UI → Impostazioni.

### System Settings (tuning per sviluppatori)

Parametri interni di tuning **non esposti nell'UI**. Solo per sviluppatori e power user.

**File:** `config/system_settings.yaml` (default nel repo) + `~/.spendify/system_settings.yaml` (override locale)

```yaml
# Esempio override locale (~/.spendify/system_settings.yaml):
history:
  auto_threshold: 0.85      # abbassa la soglia auto-assign
history_context:
  top_n: 100                # più associazioni nel prompt LLM
```

**Come funziona:**
- Il loader (`config/__init__.py`) legge i default dal repo, poi fa deep merge con il file locale
- Le chiavi non specificate nel file locale mantengono il valore di default
- Variabile d'ambiente `SPENDIFY_SYSTEM_SETTINGS` per path custom
- **Non serve riavviare** — i valori sono caricati all'import del modulo

**Sezioni disponibili:**

| Sezione | Parametri chiave | Default |
|---------|-----------------|---------|
| `history` | `min_validated`, `auto_threshold`, `suggest_threshold` | 5, 0.90, 0.50 |
| `history_context` | `min_validated`, `min_confidence`, `top_n`, `max_chars` | 3, 0.50, 50, 2000 |
| `classifier` | `confidence_threshold`, `max_transaction_amount` | 0.80, 1000000 |
| `border_detection` | `max_scan_rows`, `min_region_cols`, `min_region_rows` | 60, 3, 3 |
| `categorizer` | `batch_size`, `llm_timeout_s` | 20, 120 |
| `footer` | `max_tail_rows`, `phase2_enabled` | 10, true |

---

## 3. Struttura del progetto

```
spendify/
├── app.py                  # entry point Streamlit
├── config/                 # system settings (YAML, non UI)
│   ├── __init__.py         # loader con deep merge
│   └── system_settings.yaml # default di tuning
├── ui/                     # pagine Streamlit (solo import da services.*)
├── services/               # service layer — facade tra UI e core/db
│   ├── import_service.py
│   ├── transaction_service.py
│   ├── rule_service.py
│   ├── settings_service.py
│   ├── category_service.py
│   └── review_service.py
├── core/                   # logica di dominio pura (no UI, no DB)
│   ├── orchestrator.py     # entry point pipeline
│   ├── normalizer.py       # parsing, 3-phase footer strip, transfer detection
│   ├── classifier.py
│   ├── categorizer.py
│   ├── description_cleaner.py
│   └── sanitizer.py
├── db/                     # ORM, migrazioni, repository
│   ├── models.py           # tabelle SQLAlchemy + migrazioni idempotenti
│   ├── repository.py       # query CRUD per servizi
│   └── taxonomy_defaults.py # template tassonomia per 5 lingue
├── chat_bot/               # chatbot di supporto adattivo
│   ├── engine.py           # ChatBotEngine (auto-detect modalità)
│   ├── rag.py              # RAG: retrieval TF-IDF + generazione LLM
│   ├── faq_classifier.py   # match deterministico TF-IDF
│   ├── faq_store.py        # caricamento FAQ e documenti
│   └── knowledge/<lang>/   # FAQ e doc per lingua
├── api/                    # REST API FastAPI (opzionale)
│   ├── main.py
│   └── routers/
├── tests/                  # pytest — 453+ test, 0 mock su DB
├── tools/                  # strumenti di sviluppo
│   ├── coupling_check.py   # analisi statica import UI → service
│   └── coupling_baseline.json
└── docs/                   # documentazione pubblica nel repo
    ├── reference_guide.md
    └── developer_guide.md  # ← questo file
```

---

## 4. Service layer

Ogni servizio è una classe che riceve `engine: Engine` nel costruttore e incapsula tutte le operazioni di un dominio. La UI non vede mai SQLAlchemy o i modelli `core`.

### ImportService — facade completa

`ImportService` è il punto di accesso a tutta la pipeline di importazione. Re-esporta i tipi di dominio (`DocumentType`, `SignConvention`, `DocumentSchema`, ecc.) via `__all__` in modo che la UI non debba mai importare da `core.*`.

```python
from services.import_service import ImportService, DocumentType, SignConvention

svc = ImportService(engine)
analysis = svc.analyze_file(raw_bytes, filename)
config   = svc.build_config(giroconto_mode="neutral")
result   = svc.process_file_single(raw_bytes, filename, config)
svc.persist_result(result)
```

> **Nota:** `giroconto_mode` (`neutral`/`exclude`) controlla solo la visibilita nelle viste (Ledger, Analytics, Report). I giroconti vengono **sempre rilevati e sempre persistiti** nel database come `internal_in`/`internal_out`, indipendentemente dalla modalita scelta. Questo garantisce riconciliazione e integrita dei dati.

### SettingsService — configurazione utente

Legge e scrive `user_settings` (chiave-valore). Espone:

```python
svc.get(key, default)
svc.set(key, value)
svc.set_bulk(dict)
svc.is_onboarding_done()
svc.set_onboarding_done()
svc.apply_default_taxonomy(language)   # 'it' | 'en' | 'fr' | 'de' | 'es'
```

### Onboarding

Alla prima esecuzione su un DB vuoto, `app.py` mostra il wizard di onboarding (4 step: lingua, nomi titolari, conti, conferma). Dopo aver completato il wizard, `set_onboarding_done()` è chiamato e l'app ricarica normalmente.

Per installazioni esistenti (DB con dati) l'onboarding è saltato automaticamente: `_migrate_set_onboarding_done_for_existing_users()` in `db/models.py` imposta il flag se `taxonomy_category` ha già righe.

---

## 5. Classifier multi-step

Il classifier supporta una pipeline LLM a 3 step sequenziali, dove l'output di ogni step alimenta il contesto dello step successivo. Questo approccio migliora l'accuratezza su modelli piccoli che faticano a produrre l'intero schema in una sola chiamata.

### Architettura a 3 step

| Step | Scopo | Output |
|------|-------|--------|
| **Step 1 — Document Identity** | Identifica il tipo di documento e i parametri di lettura | `doc_type`, `encoding`, `delimiter`, `sheet_name`, `skip_rows` |
| **Step 2 — Column Mapping** | Mappa le colonne del file ai campi Spendify | `date_col`, `amount_col`, `description_col`, `balance_col`, `credit_col`, `debit_col` |
| **Step 3 — Semantic Analysis** | Analizza la semantica dei valori (segno, formato data, ecc.) | `sign_convention`, `invert_sign`, `date_format`, `decimal_separator`, `account_holder` |

Ogni step riceve come contesto l'output degli step precedenti, consentendo al modello di concentrarsi su un sotto-problema alla volta.

### File e funzioni chiave

| Componente | Posizione | Ruolo |
|------------|-----------|-------|
| `_classify_multi_step()` | `core/classifier.py` | Orchestrazione dei 3 step con gestione errori e fallback |
| `MultiStepDiagnostics` | `core/classifier.py` | Dataclass con diagnostica per-step (prompt, risposta raw, JSON parsato, durata) |
| `step1_json_schema()` | `core/schemas.py` | JSON Schema per la risposta dello Step 1 |
| `step2_json_schema()` | `core/schemas.py` | JSON Schema per la risposta dello Step 2 |
| `step3_json_schema()` | `core/schemas.py` | JSON Schema per la risposta dello Step 3 |
| `fill_llm_defaults()` | `core/schemas.py` | Applica valori di default ai campi opzionali non restituiti dal modello |

### Modalita di classificazione (`classifier_mode` in `ProcessingConfig`)

| Valore | Comportamento |
|--------|--------------|
| `"auto"` | **Default.** Seleziona automaticamente in base alla dimensione del modello (vedi sotto) |
| `"single"` | Chiamata LLM singola (tutto in un prompt) |
| `"multi_step"` | Forza la pipeline a 3 step |

### Auto-detect

La logica auto seleziona il modo in base al backend e alla dimensione del modello:

- **Modelli GGUF locali < 5 GB** → `multi_step` (modelli piccoli beneficiano della decomposizione)
- **Modelli GGUF locali >= 5 GB** → `single` (modelli grandi gestiscono bene il prompt completo)
- **Backend remoti** (OpenAI, Anthropic, ecc.) → `single`

### Degradazione

| Fallimento | Comportamento |
|------------|--------------|
| Step 1 fallisce | **Abort** — impossibile procedere senza il tipo di documento |
| Step 2 fallisce | **Fallback Phase 0** — si tenta il parsing con regole deterministiche |
| Step 3 fallisce | **Defaults** — `fill_llm_defaults()` applica i default; `confidence` impostata a `low` |

---

## 6. Coupling gate (CI)

`tools/coupling_check.py` analizza staticamente tutti i file `ui/` e verifica che non importino da `core.*`, `db.*`, `support.*`.

```bash
# Run locale
uv run python tools/coupling_check.py --strict

# Output atteso
✅ Coupling check passed — 0 violations across 12 UI files
```

Il job `coupling-check` in `.github/workflows/ci.yml` esegue `--strict --json` e posta un commento Markdown sulla PR con il dettaglio per file. Un file con nuove violazioni fa fallire la CI.

**Baseline:** `tools/coupling_baseline.json` — attualmente vuoto `{}` (tutti i file devono avere 0 violazioni). Aggiungere un file alla baseline è possibile ma richiede una motivazione esplicita nel JSON.

---

## 7. REST API

Un server FastAPI opzionale espone le operazioni core come endpoint REST.

```bash
uv run uvicorn api.main:app --reload --port 8000
# Documentazione interattiva: http://localhost:8000/docs
```

Il server usa gli stessi `services.*` dell'UI Streamlit — nessuna logica duplicata.

---

## 7b. Chatbot di supporto

Il modulo `chat_bot/` implementa un chatbot adattivo che risponde a domande sull'uso di Spendify. La modalità viene scelta automaticamente in base al backend LLM configurato dall'utente in Impostazioni.

### Architettura

```
chat_bot/
├── engine.py           # ChatBotEngine — orchestratore, auto-detect modalità
├── rag.py              # RAGEngine — TF-IDF retrieval + generazione LLM
├── faq_classifier.py   # FAQClassifier — match deterministico TF-IDF (zero LLM)
├── faq_store.py        # Caricamento FAQ (JSON/MD) e chunk documenti
├── prompts.json        # System prompt + messaggi no-answer multilingua
└── knowledge/<lang>/   # FAQ e documenti per lingua (it, en, de, es, fr, pt)
    ├── faq.json        # [{"q": "...", "a": "..."}]
    └── docs/           # File .md/.txt chunked per RAG
```

### Tre modalità

| Modalità | Condizione (da user settings) | Funzionamento |
|----------|-------------------------------|---------------|
| `rag_cloud` | Backend = `openai` / `claude` / `openai_compatible` con API key | Retrieval TF-IDF → LLM cloud genera risposta |
| `rag_local` | Backend = `local_ollama` / `vllm` | Retrieval TF-IDF → LLM locale genera risposta |
| `faq_match` | Backend = `local_llama_cpp` o nessuno | Cosine similarity su FAQ, risposta preconfezionata |

### Integrazione con il progetto

- **Backend LLM:** Usa `BackendFactory` da `core/llm_backends.py` — stesso backend dell'utente
- **Settings:** Legge `llm_backend` e API key da `user_settings` (DB) via `get_all_user_settings()`
- **UI:** `ui/chat_page.py` segue il pattern `render_X_page(engine)`, con `st.chat_message`
- **i18n:** Chiavi `chat.*` e `nav.chat*` in `ui/i18n/{it,en}.json`
- **Sidebar:** Voce `("chat", "chat")` in `_NAV_KEYS`

### Utilizzo programmatico

```python
from chat_bot.engine import ChatBotEngine

bot = ChatBotEngine(db_engine=engine, lang="it")
print(bot.mode)       # ChatMode.FAQ_MATCH | RAG_LOCAL | RAG_CLOUD
response = bot.ask("Come importo un file?")
print(response.text)  # Risposta
print(response.sources)  # ["faq.json"] (opzionale)
```

### Popolare la knowledge base

1. **FAQ:** Aggiungere file `.json` o `.md` in `chat_bot/knowledge/<lang>/`
2. **Documenti RAG:** Aggiungere file `.md` o `.txt` in `chat_bot/knowledge/<lang>/docs/`
3. Non indicizzare mai dati sensibili (credenziali, dati personali, strategie di business)

---

## 8. Test

```bash
# Tutti i test
uv run pytest tests/ -v

# Con coverage
uv run pytest tests/ -v --cov=. --cov-report=term-missing

# Un singolo modulo
uv run pytest tests/test_normalizer.py -v
```

**Soglie di coverage:**

| Modulo | Minima |
|--------|--------|
| `core/normalizer.py` | 100% |
| `core/description_cleaner.py` | 100% |
| `core/classifier.py` | ≥ 99% |
| Tutti gli altri | ≥ 80% |

I test usano SQLite in-memory (`create_engine("sqlite://")`) — nessun mock sul DB.

---

## 9. Prompt Integrity Guard (S-01)

I prompt LLM sono protetti da SHA256 pinning per prevenire prompt injection via PR/commit.

**File protetti:** `prompts/classifier.json`, `categorizer.json`, `description_cleaner.json`, `footer_detector.json`

**Come funziona:**
- `prompts/prompt_hashes.json` contiene gli hash SHA256 di ogni file prompt
- All'avvio dell'app, `core/prompt_guard.py` verifica che gli hash corrispondano
- In CI, `tools/compute_prompt_hashes.py --verify` blocca le PR con prompt modificati senza hash aggiornato
- Nuovi file `.json` in `prompts/` senza hash → segnalati come "non autorizzati"

**Workflow per modificare un prompt:**
```bash
# 1. Modifica il prompt
vim prompts/categorizer.json

# 2. Rigenera gli hash
python tools/compute_prompt_hashes.py

# 3. Committa entrambi
git add prompts/categorizer.json prompts/prompt_hashes.json
git commit -m "feat: update categorizer prompt + hash"
```

**Pre-commit hook (opzionale):**
```bash
# Copia in .git/hooks/pre-commit e rendi eseguibile
#!/bin/bash
python tools/compute_prompt_hashes.py --verify || {
  echo "Prompt modificati senza aggiornamento hash."
  echo "Esegui: python tools/compute_prompt_hashes.py"
  exit 1
}
```

---

## 10. Benchmark (T-09)

### Quick start (zero-config)

Su una macchina qualsiasi — anche appena clonata — basta un solo comando:

```bash
# macOS / Linux — ENTRY POINT consigliato (tutti i backend × pipeline + categorizer)
bash tests/run_benchmark_full.sh                             # pipeline + categorizer, 1 run, tutti i backend
bash tests/run_benchmark_full.sh --benchmark pipeline        # solo pipeline
bash tests/run_benchmark_full.sh --benchmark both --runs 3   # entrambi, 3 run ciascuno
bash tests/run_benchmark_full.sh --setup-only                # solo download modelli

# Windows (PowerShell) — ENTRY POINT consigliato
powershell -ExecutionPolicy Bypass -File .\tests\run_benchmark_full.ps1
powershell -ExecutionPolicy Bypass -File .\tests\run_benchmark_full.ps1 -Benchmark both -Runs 3

# Singolo backend (llama.cpp) — macOS / Linux
bash tests/run_benchmark.sh                         # pipeline, 1 run, tutti i modelli piccoli
bash tests/run_benchmark.sh categorizer              # solo categorizer
bash tests/run_benchmark.sh both --runs 3            # entrambi, 3 run ciascuno
bash tests/run_benchmark.sh pipeline --files 'CC-1*' # pipeline con filtro file

# Singolo backend — Windows (PowerShell)
powershell -ExecutionPolicy Bypass -File .\tests\run_benchmark.ps1
powershell -ExecutionPolicy Bypass -File .\tests\run_benchmark.ps1 both -Runs 3
```

`run_benchmark_full.sh` / `run_benchmark_full.ps1` gestiscono: setup completo (GGUF + Ollama pull + rilevamento vLLM), poi eseguono pipeline e categorizer per ogni backend attivo. La lista modelli è letta da `tests/benchmark_models.csv`.

`run_benchmark.sh` / `run_benchmark.ps1` gestiscono automaticamente: installazione `uv`, creazione `.venv`, `uv sync`, download modelli GGUF mancanti, esecuzione benchmark su llama.cpp. `run_benchmark.sh` esegue **tutti** i modelli GGUF presenti (nessun filtro per dimensione).

### Setup modelli + Dual benchmark (llama.cpp + Ollama)

Per eseguire un benchmark completo su tutti i backend:

```bash
cd ~/Documents/Progetti/PERSONALE/Spendify/sw_artifacts

# Full benchmark: tutti i backend (llama.cpp + Ollama + vLLM), setup automatico
bash tests/run_benchmark_full.sh

# Solo setup modelli, senza benchmark
bash tests/run_benchmark_full.sh --setup-only

# Con più run per ridurre la varianza
bash tests/run_benchmark_full.sh --runs 3

# Salta un backend specifico
bash tests/run_benchmark_full.sh --skip-ollama
bash tests/run_benchmark_full.sh --skip-vllm
```

Il setup scarica automaticamente i modelli GGUF mancanti e fa `ollama pull` per i modelli Ollama. La lista modelli è in `tests/benchmark_models.csv`.

### Comandi manuali (avanzato)

```bash
# Singolo modello llama.cpp (n_ctx auto-detect dal GGUF)
uv run python tests/benchmark_pipeline.py --runs 1 --backend local_llama_cpp \
  --model-path ~/.spendify/models/gemma-3-12b-it-Q4_K_M.gguf

# Forza un n_ctx specifico (limita RAM)
uv run python tests/benchmark_pipeline.py --runs 1 --backend local_llama_cpp \
  --model-path ~/.spendify/models/gemma-3-12b-it-Q4_K_M.gguf --n-ctx 2048

# Singolo modello Ollama (n_ctx auto-detect via /api/show)
uv run python tests/benchmark_pipeline.py --runs 1 --backend local_ollama --model gemma3:12b

# Gemma 4 E2B
uv run python tests/benchmark_pipeline.py --runs 1 --backend local_llama_cpp \
  --model-path ~/.spendify/models/gemma-4-E2B-it-Q4_K_M.gguf
uv run python tests/benchmark_pipeline.py --runs 1 --backend local_ollama --model gemma4:e2b

# Categorizer con Ollama
uv run python tests/benchmark_categorizer.py --runs 1 --backend local_ollama --model gemma3:12b

# vLLM (locale o remoto — auto-detect modello e context window)
vllm serve Qwen/Qwen2.5-3B-Instruct  # in un altro terminale
uv run python tests/benchmark_pipeline.py --runs 1 --backend vllm

# Suite completa (tutti i backend)
bash tests/run_benchmark_full.sh
```

Tutti i run scrivono in `tests/generated_files/benchmark/results_all_runs.csv` (append-only). Resume key: `(run_id, filename, commit, branch, provider, model)`.

### Context window auto-detect

Il benchmark rileva automaticamente la context window ottimale per ogni modello:

| Backend | Metodo |
|---------|--------|
| llama.cpp | Legge `llama.context_length` dall'header GGUF (senza caricare i pesi) |
| Ollama | Chiama `/api/show` e legge il context del modello |
| OpenAI / Claude | Lookup statico (`_KNOWN_CONTEXT`: gpt-4o=128k, claude-3-5=200k, …) |
| vLLM | Interroga `/v1/models` |

`--n-ctx 0` (default) = auto-detect. Imposta un valore esplicito per limitare l'uso di RAM.

### Catalogo modelli (benchmark_models.csv)

`tests/benchmark_models.csv` è la sorgente unica della lista modelli per tutti gli script di benchmark (sostituisce gli array hardcoded nei vecchi script). Colonne: `name`, `gguf_file`, `gguf_repo`, `gguf_hf_url`, `ollama_tag`, `enabled`. Se `gguf_file` è valorizzato il modello è disponibile su llama.cpp; se `ollama_tag` è valorizzato è disponibile su Ollama. Impostare `enabled=false` per saltare un modello in tutti gli script. Il catalogo contiene 11 modelli (Qwen2.5-1.5B, Gemma2-2B, Qwen3.5-2B, Qwen3.5-4B, Gemma4-E2B Q3+Q4, Llama3.2-3B, Qwen2.5-3B, Phi3-mini, Qwen2.5-7B, Gemma3-12B). I modelli vLLM non sono nel CSV — vengono auto-rilevati dal server al runtime.

### Monitoraggio HW (CPU + GPU)

Il modulo `tests/hw_monitor.py` (`HWMonitor`) campiona CPU e GPU in background ogni 0.5 s durante l'intero run di benchmark, producendo medie più accurate rispetto ai vecchi campioni point-in-time.

| Piattaforma | Metodo GPU | Note |
|-------------|-----------|------|
| macOS Apple Silicon | `ioreg` / AGXAccelerator → Device Utilization % | Nessun sudo richiesto |
| Linux NVIDIA | `nvidia-smi` → utilization % + power watts | Richiede driver NVIDIA |
| Linux AMD | `rocm-smi` → utilization % | Richiede ROCm |
| Fallback | — | GPU utilization = 0.0 |

`benchmark_pipeline.py` e `benchmark_categorizer.py` usano `HWMonitor` al posto delle vecchie funzioni inline `_sample_cpu_load()` / `_sample_gpu_utilization()`.

### Monitor avanzamento (monitor_benchmark)

`tests/monitor_benchmark.sh` / `monitor_benchmark.ps1` / `monitor_benchmark.py` mostrano l'avanzamento in tempo reale leggendo `results_all_runs.csv`. Features: progress bar per modello, fase corrente (classifier/categorizer) rilevata dalla colonna `benchmark_type`, statistiche CPU/GPU live via `HWMonitor.sample_once()` e medie storiche dal CSV. Opzioni principali: `--interval N` (refresh in secondi), `--runs N` (run attesi per modello), `--total N` (righe totali attese), `--once` (snapshot e termina), `--all` (mostra anche modelli completati).

### Script disponibili

| Script | Scopo |
|--------|-------|
| `tests/run_benchmark_full.sh` | **ENTRY POINT** (macOS/Linux): tutti i backend × pipeline + categorizer |
| `tests/run_benchmark_full.ps1` | **ENTRY POINT** (Windows): tutti i backend × pipeline + categorizer |
| `tests/run_benchmark.sh` | **Zero-config** (macOS/Linux): env + modelli + benchmark llama.cpp in un comando |
| `tests/run_benchmark.ps1` | **Zero-config** (Windows): equivalente PowerShell, include download modelli |
| `tests/cleanup_benchmark.sh` | Pulizia file generati |
| `tests/benchmark_models.csv` | Catalogo modelli (sostituisce array hardcoded negli script) |
| `tests/hw_monitor.py` | Monitoraggio HW in background (CPU + GPU cross-platform) |
| `tests/monitor_benchmark.sh` | Monitor avanzamento benchmark in tempo reale (macOS/Linux) |
| `tests/monitor_benchmark.ps1` | Monitor avanzamento benchmark in tempo reale (Windows) |
| `tests/monitor_benchmark.py` | Monitor avanzamento benchmark cross-platform (Python) |
| `tests/diagnose.ps1` | Diagnostica ambiente Windows (include rilevamento GPU: NVIDIA/AMD/Intel) |

### Logging

Ogni esecuzione salva un log in `tests/logs/` (gitignored, un file per run con timestamp):

| Script | Log |
|--------|-----|
| `run_benchmark.sh` | `tests/logs/benchmark_YYYYMMDD_HHMMSS.log` |
| `run_benchmark_full.sh` | `tests/logs/benchmark_YYYYMMDD_HHMMSS.log` |
| `benchmark_pipeline.py` | `tests/logs/pipeline_YYYYMMDD_HHMMSS.log` |
| `benchmark_categorizer.py` | `tests/logs/categorizer_YYYYMMDD_HHMMSS.log` |

Output su console e file simultaneamente (tee). Utile per troubleshooting e confronto tra run.

### Metriche registrate

| Metrica | Classifier | Categorizer |
|---------|-----------|-------------|
| header_match | ✅ | — |
| rows_match | ✅ | — |
| doc_type_match | ✅ | — |
| parse_rate | ✅ | — |
| amount_accuracy | ✅ | — |
| date_accuracy | ✅ | — |
| category_accuracy | — | ✅ |
| cat_fuzzy_accuracy | — | ✅ |
| cat_fallback_rate | — | ✅ |
| duration_seconds | ✅ | ✅ |
| AUTOMATION SCORE | ✅ | ✅ |

### Benchmark cross-platform (Mac remoto)

Per confrontare performance tra macchine diverse (es. M1 Max locale vs M4 remoto):

**Setup server (Mac M4 — host remoto):**

```bash
# 1. Installa llama.cpp
brew install llama.cpp

# 2. Scarica modello
brew install huggingface-cli
huggingface-cli download google/gemma-3-12b-it-GGUF gemma-3-12b-it-Q4_K_M.gguf \
  --local-dir ~/.spendify/models/

# 3. Lancia server (aperto sulla rete locale)
llama-server -m ~/.spendify/models/gemma-3-12b-it-Q4_K_M.gguf \
  --host 0.0.0.0 --port 8080 -ngl 99 -c 4096

# 4. Verifica
curl http://localhost:8080/v1/models
```

**Setup client (Mac M1 Max — esegue i benchmark):**

```bash
# Punta al server remoto via backend openai_compatible
uv run python tests/benchmark_pipeline.py --runs 1 \
  --backend openai_compatible \
  --base-url http://192.168.x.x:8080/v1 \
  --model gemma-3-12b-it

# Oppure: configura in Settings → Backend → OpenAI Compatible
# URL: http://192.168.x.x:8080/v1
# Model: gemma-3-12b-it
```

**Confronto risultati:**

I risultati includono `runtime_os`, `runtime_cpu`, `runtime_ram_gb`, `runtime_gpu` — filtrabili nel CSV per confrontare tok/s e `duration_seconds` tra macchine diverse con lo stesso modello e commit.

**Parametri chiave per il confronto:**

| Parametro | Dove | Default |
|-----------|------|---------|
| `-ngl 99` | llama-server | Offload tutti i layer su GPU Metal |
| `-c 4096` | llama-server | Context window |
| `--threads N` | llama-server | CPU threads (auto-detect) |
| `--flash-attn` | llama-server | Flash attention (più veloce su M4) |

### Benchmark veloce tok/s (senza Spendify)

Per misurare la velocità pura del modello senza overhead pipeline:

```bash
# llama-bench (incluso in llama.cpp)
llama-bench -m ~/.spendify/models/gemma-3-12b-it-Q4_K_M.gguf -ngl 99

# Tutti i modelli
for m in ~/.spendify/models/*.gguf; do
  echo "=== $(basename $m) ==="
  llama-bench -m "$m" -ngl 99
done
```

### Modelli disponibili localmente

```bash
# GGUF (llama.cpp)
ls -lh ~/.spendify/models/*.gguf

# Ollama
ollama list
```

### Benchmark cloud su Azure ML (T-09d)

Per benchmark su HW normalizzato (GPU cloud), eliminando la variabilità della macchina locale:

```
Developer Mac                    Azure ML
─────────────                    ────────
files sintetici ──── upload ────► Docker container
manifest.csv                     ├─ Pull GGUF da HuggingFace
expected/*.csv                   ├─ Classifier benchmark (50 file)
                                 ├─ Categorizer benchmark (50 file)
results_all_    ◄── download ──── results_all_runs.csv
runs.csv
```

**Workflow (flusso locale, zero token):**

```bash
# 1. Lancia benchmark su Azure (singolo modello o tutti)
python tools/azure_benchmark.py --model qwen2.5-3b --compute Standard_NC6s_v3
python tools/azure_benchmark.py --all-models   # N job paralleli

# 2. Scarica risultati quando i job completano
python tools/azure_benchmark.py --download --job-id <id>
#    → merge automatico nel CSV locale (append-only, dedup by resume key)

# 3. Apri PR con i risultati (credenziali git locali, nessun token extra)
git checkout -b bench/$(date +%Y-%m-%d)-azure-t4
git add tests/generated_files/benchmark/results_all_runs.csv
git commit -m "bench: azure T4 results"
gh pr create --title "bench: Azure T4 results" --body "14 modelli su GPU T4"
```

Il job Azure non fa push — il developer scarica e apre la PR dalla sua macchina.

**Setup completo (one-time):**

```bash
# 1. Azure CLI + login
brew install azure-cli
az login

# 2. Azure ML SDK
uv add azure-ai-ml azure-identity

# 3. Creare risorse Azure (una volta sola)
az group create -n spendify-rg -l westeurope
az ml workspace create -n spendify-ml -g spendify-rg
az acr create -n spendifyacr -g spendify-rg --sku Basic
az ml compute create -n gpu-t4-spot -g spendify-rg -w spendify-ml \
    --type AmlCompute --size Standard_NC6s_v3 \
    --min-instances 0 --max-instances 5 --tier low_priority

# 4. Esportare variabili (.env o shell)
export AZURE_SUBSCRIPTION_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
export AZURE_RESOURCE_GROUP=spendify-rg
export AZURE_ML_WORKSPACE=spendify-ml
export AZURE_ACR_NAME=spendifyacr
```

**Run completo (build + submit + wait + download + PR):**

```bash
# Tutto automatico — un solo comando
bash tools/run_cloud_benchmarks.sh

# Oppure step-by-step:
python tools/azure_benchmark.py --build                     # Docker → ACR
python tools/azure_benchmark.py --all-models --skip-build   # Submit N jobs
python tools/azure_benchmark.py --list                      # Vedi status + Studio URL
python tools/azure_benchmark.py --download --job-name <id>  # Scarica risultati
# → git checkout -b bench/... → git push → gh pr create
```

Ogni job stampa il link **Azure ML Studio** per monitorare l'esecuzione in tempo reale.

**Perché Azure ML anziché locale:**
- **HW fisso** → confronto equo tra modelli (stessa GPU per tutti)
- **Parallelismo** → 14 modelli in 14 container simultanei → ~30 min totali
- **Riproducibilità** → stesso Docker + commit + GPU = stessi risultati
- **Costo** → spot instances T4: ~$2.50 per suite completa

**Strategia di selezione modello:**
1. Eseguire benchmark cloud con tutti i modelli candidati su HW normalizzato (T4)
2. Trovare il **modello ideale** = miglior automation_score con tempo accettabile
3. Scalare HW in up/down per definire i requisiti minimi per quel modello
4. Il `models_registry.yaml` viene aggiornato con i risultati reali

### Workflow collaborativo: push/pull dei risultati

Il CSV `results_all_runs.csv` è **committato nel repo** e cresce in modo append-only. Ogni developer aggiunge le proprie righe (suo HW, suo modello, suo commit) e le condivide via git.

```
Developer A (Mac M1 Max)        GitHub repo           Developer B (Mac M4)
─────────────────────────       ───────────           ─────────────────────
git pull                        results_all_          git pull
  (prende righe di B)           runs.csv              (prende righe di A)
                                (cumulativo)
lancia benchmark                                      lancia benchmark
  resume: skip righe                                    resume: skip righe
  già presenti (A+B)                                    già presenti (A+B)
  aggiunge solo nuove                                   aggiunge solo nuove

git push ──────────────────►  merge CSV  ◄────────────── git push
```

**Regole:**
- `git pull` **prima** di lanciare un benchmark → il resume skippa ciò che altri hanno già fatto
- `git push` **dopo** ogni benchmark → condivide i risultati
- Il CSV non ha conflitti: ogni riga è unica per `(run_id, filename, commit, branch, provider, model)`
- Ogni riga include `runtime_os`, `runtime_cpu`, `runtime_ram_gb`, `runtime_gpu` → i risultati sono filtrabili per HW

**Automazione (pre-push hook opzionale):**
```bash
# .git/hooks/pre-push — auto-include risultati benchmark nel push
BENCH_CSV="tests/generated_files/benchmark/results_all_runs.csv"
if git diff --name-only HEAD | grep -q "$BENCH_CSV"; then
  echo "Benchmark results included in push"
fi
```

**Flusso per un nuovo developer:**
```bash
git clone ...
git pull                          # prende tutti i risultati storici

bash tests/run_benchmark_full.sh  # resume skippa tutto ciò che esiste,
                                  # aggiunge solo il suo HW + commit

# Apri PR con i risultati (mai push diretto su main)
git checkout -b bench/$(date +%Y-%m-%d)-m1max
git add tests/generated_files/benchmark/results_all_runs.csv
git commit -m "bench: add results for M1 Max 64GB"
gh pr create --title "bench: M1 Max results" --body "Aggiunge risultati benchmark"
```

**CI check sulla PR** (`tools/verify_bench_csv.py --pr`):
- Verifica che il CSV contiene solo righe **aggiunte** (append-only)
- Nessuna riga esistente modificata o rimossa
- Header CSV invariato
- Ogni nuova riga ha `benchmark_type`, `provider`, `model` compilati
- Se violazione → PR bloccata

---

## 11. Decisioni di design chiave

| Decisione | Motivazione |
|-----------|-------------|
| `Decimal` per gli importi, mai `float` | Evita errori di arrotondamento nei calcoli finanziari |
| SHA-256 come `tx_id` | Importazione idempotente: re-import dello stesso file non crea duplicati |
| Migrazioni idempotenti (`CREATE TABLE IF NOT EXISTS`, `INSERT OR IGNORE`) | Aggiornamenti sicuri su DB esistenti senza script di migrazione separati |
| LLM offline-first (Ollama default) | Privacy: nessun dato finanziario lascia la macchina per default |
| PII sanitization prima di ogni chiamata remota | IBAN, carte, codici fiscali e nomi sostituiti in memoria prima dell'invio |
| Service layer come unica porta d'accesso per la UI | Disaccoppiamento che permette di testare la logica indipendentemente da Streamlit |
| Tassonomia default nel DB (non in YAML) | Supporto multi-lingua (it/en/fr/de/es) senza file di configurazione aggiuntivi |

---

## 12. Documentazione tecnica di riferimento

La documentazione di ingegneria dettagliata è in `documents/` (fuori dal repo):

| File | Contenuto |
|------|-----------|
| `documents/progetto.md` | Documento di progetto: obiettivi, stack, architettura |
| `documents/pipeline.md` | Pipeline di importazione passo-passo |
| `documents/database.md` | Schema DB completo, migrazioni, backup/restore |
| `documents/deployment.md` | Deployment Docker, variabili d'ambiente, aggiornamenti |
| `documents/configurazione.md` | Tutti i parametri configurabili, provider LLM, API key |
| `documents/deterministic_rules.md` | Motore regole: sintassi, priorità, applicazione retroattiva |
| `documents/deterministic_tools.md` | Tools di debug e analisi pipeline |
| `documents/installazione.md` | Installazione nativa (Mac/Linux/Windows), Docker |
| `documents/guida_utente.md` | Guida operativa per l'utente finale |
| `documents/landing_page.md` | Copy landing page |

Per contribuire al codice vedi anche **[CONTRIBUTING.md](../CONTRIBUTING.md)**.
