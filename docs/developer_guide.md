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
8. [Decisioni di design chiave](#8-decisioni-di-design-chiave)
9. [Documentazione tecnica di riferimento](#9-documentazione-tecnica-di-riferimento)

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

## 8. Decisioni di design chiave

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

## 9. Documentazione tecnica di riferimento

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
