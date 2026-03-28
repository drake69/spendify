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
5. [Coupling gate (CI)](#5-coupling-gate-ci)
6. [REST API](#6-rest-api)
7. [Test](#7-test)
8. [Prompt Integrity Guard (S-01)](#8-prompt-integrity-guard-s-01)
9. [Benchmark (T-09)](#9-benchmark-t-09)
10. [Decisioni di design chiave](#10-decisioni-di-design-chiave)
11. [Documentazione tecnica di riferimento](#11-documentazione-tecnica-di-riferimento)

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

## 5. Coupling gate (CI)

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

## 6. REST API

Un server FastAPI opzionale espone le operazioni core come endpoint REST.

```bash
uv run uvicorn api.main:app --reload --port 8000
# Documentazione interattiva: http://localhost:8000/docs
```

Il server usa gli stessi `services.*` dell'UI Streamlit — nessuna logica duplicata.

---

## 7. Test

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

## 8. Prompt Integrity Guard (S-01)

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

## 9. Benchmark (T-09)

### Benchmark classifier (schema detection + parsing)

```bash
# Singolo modello
uv run python tests/benchmark_pipeline.py --runs 1 --backend local_ollama --model gemma3:12b

# Singolo modello llama.cpp
uv run python tests/benchmark_pipeline.py --runs 1 --backend local_llama_cpp \
  --model-path ~/.spendify/models/gemma-3-12b-it-Q4_K_M.gguf

# Suite completa (tutti i modelli Ollama + llama.cpp)
bash tests/run_all_benchmarks.sh
```

### Benchmark categorizer

```bash
uv run python tests/benchmark_categorizer.py --runs 1 --backend local_ollama --model gemma3:12b
```

Entrambi scrivono nello stesso `tests/generated_files/benchmark/results_all_runs.csv` con colonna `benchmark_type` (classifier/categorizer). Il resume key include `(run_id, filename, commit, branch, provider, model)` — cambiando modello o commit, i run precedenti vengono skippati.

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

---

## 10. Decisioni di design chiave

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

## 11. Documentazione tecnica di riferimento

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
