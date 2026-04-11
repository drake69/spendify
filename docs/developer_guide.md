# Spendif.ai — Developer Guide

> Versione: 3.1 — aggiornato 2026-04-09
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
cd spendifai
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
SPENDIFAI_DB=sqlite:///ledger.db   # percorso DB SQLite
```

La configurazione LLM (backend, modello, API key) vive nel database e si gestisce dall'UI → Impostazioni.

### System Settings (tuning per sviluppatori)

Parametri interni di tuning **non esposti nell'UI**. Solo per sviluppatori e power user.

**File:** `config/system_settings.yaml` (default nel repo) + `~/.spendifai/system_settings.yaml` (override locale)

```yaml
# Esempio override locale (~/.spendifai/system_settings.yaml):
history:
  auto_threshold: 0.85      # abbassa la soglia auto-assign
history_context:
  top_n: 100                # più associazioni nel prompt LLM
```

**Come funziona:**
- Il loader (`config/__init__.py`) legge i default dal repo, poi fa deep merge con il file locale
- Le chiavi non specificate nel file locale mantengono il valore di default
- Variabile d'ambiente `SPENDIFAI_SYSTEM_SETTINGS` per path custom
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
spendifai/
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
│   ├── kb_store.py         # caricamento FAQ e documenti (knowledge base)
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

**Campi principali in `DEFAULT_USER_SETTINGS`:**

| Chiave | Default | Descrizione |
|--------|---------|-------------|
| `language` | `"it"` | Lingua UI (`it`, `en`, `fr`, `de`, `es`) |
| `country` | `""` | Paese utente ISO 3166-1 alpha-2 (es. `IT`, `CH`, `DE`). Usato da `nsi_lookup.py` per disambiguare brand multinazionali e da `NsiTaxonomyService` per il bypass diretto (C-08-cascade). |
| `llm_backend` | `"local_ollama"` | Backend LLM attivo |
| `giroconto_mode` | `"neutral"` | Visibilità giroconti (`neutral` / `exclude`) |

### NsiTaxonomyService — mapping OSM tag → tassonomia (C-08-cascade)

`NsiTaxonomyService` costruisce e mantiene la `taxonomy_map`: dizionario `{osm_tag → (category, subcategory)}` che permette il bypass diretto del LLM per le transazioni identificate tramite NSI.

```python
from services.nsi_taxonomy_service import NsiTaxonomyService

svc = NsiTaxonomyService(engine)
with session_scope() as s:
    taxonomy_map = svc.get_or_build(s, taxonomy, llm_backend)
```

**Lifecycle:**
- Memorizzata in tabella DB `nsi_tag_mapping` (persistente tra riavvii)
- Invalidata automaticamente se la tassonomia dell'utente cambia (hash SHA-256)
- Prima build: LLM call → fallback statico da `osm_to_spendifai_map.json`
- Tassonomia italiana default → 14+ tag mappati senza LLM (`_static_map()`)

**Workflow developer (quando aggiornare `static_rules.json`):**
```bash
# Dopo ogni release NSI o aggiunta brand
python scripts/build_static_rules.py
git add core/static_rules.json
git commit -m "feat(nsi): update static_rules.json — NSI vX"
```
Il `taxonomy_map` degli utenti si invalida automaticamente al prossimo import.

### CategoryService — cascata di categorizzazione

`CategoryService` orchestra la cascata a 5 step per ogni transazione:

| Step | Fonte | Condizione | `source` |
|------|-------|------------|----------|
| **0** | User rules (`category_rule` DB) | Pattern match deterministico | `rule` |
| **2** | History (`human_validated=True`) | Confidence ≥ soglia | `history` |
| **3b** | NSI + `taxonomy_map` | Brand match + `user_country ∈ countries` + osm_tag in map | `nsi` |
| **3b** | NSI hint | Brand match senza bypass | `llm` (hint iniettato) |
| **4** | LLM batch | Transazioni non risolte | `llm` |
| **5** | Fallback | Nessun match | `llm` + `to_review=True` |

Ogni transazione categorizzata via LLM registra il modello specifico nel campo `category_model` (es. `gemma-2-2b-it-Q4_K_M`). Il campo viene azzerato quando la fonte cambia a `rule` o `manual`.

> **Nota Step 1 (deprecato):** Le regole linguistiche di `core/static_rules/_it.json` sono state deprecate in C-08-cascade. Operavano sulla descrizione grezza (causale), ma `clean_descriptions_batch()` estrae il solo nome controparte prima della categorizzazione, rendendo le regex inutili. I brand sono ora coperti meglio da NSI (Step 3b).

### `core/description_cleaner.py` — pulizia descrizioni e mapping LLM

`description_cleaner.py` espone `clean_descriptions_batch()`, che invia al LLM un batch di descrizioni grezze e riceve il nome controparte normalizzato per ciascuna. Due meccanismi garantiscono la correttezza del mapping input→output anche quando il modello non rispetta l'ordine.

**I-16 — Mapping via campo `idx`**

Ogni item del batch include un campo `idx` numerico. Il LLM deve restituire lo stesso `idx` nella risposta. Al ritorno, il cleaner riordina i risultati usando `idx` come chiave primaria. Questo copre la quasi totalità dei casi con modelli aderenti allo schema.

**I-17 — Reverse matching post-hoc anti-shuffle** (`_reverse_match()`)

Quando un modello ignora il campo `idx` e restituisce i risultati in ordine errato, I-17 recupera il mapping corretto via containment scoring:

- `_containment_score(output, input)`: Jaccard containment a livello token — `|out ∩ in| / |out|`. Score 1.0 = tutti i token dell'output compaiono nell'input.
- `_reverse_match()`: assegnazione greedy — ordina tutte le coppie (input, output) per score decrescente, assegna il best match per primo; ogni output è usato al massimo una volta.
- Si attiva **solo** sulle posizioni non risolte da `idx` (I-16 ha priorità). Zero overhead quando il mapping via `idx` copre tutte le posizioni.

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
| **Step 2 — Column Mapping** | Mappa le colonne del file ai campi Spendif.ai | `date_col`, `amount_col`, `description_col`, `balance_col`, `credit_col`, `debit_col` |
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

## 5b. Token usage tracking

Ogni chiamata LLM (tutti i backend: llama.cpp, Ollama, OpenAI, Claude) viene registrata nella tabella `llm_usage_log` per analisi statistica del consumo di token.

### Schema tabella

| Colonna | Tipo | Descrizione |
|---------|------|-------------|
| `backend` | String(30) | `local_llama_cpp`, `ollama`, `openai`, `claude` |
| `model_id` | String(120) | Identificativo modello (es. `gemma-2-2b-it-Q4_K_M`) |
| `caller` | String(30) | Modulo chiamante: `categorizer`, `classifier`, `description_cleaner`, `normalizer` |
| `step` | String(60) | Fase: `step1_identity`, `step2_mapping`, `step3_semantic`, `batch_expense`, `batch_income`, `footer_detect` |
| `source_name` | String(256) | Nome file sorgente per tracciabilità |
| `batch_size` | Integer | Numero di item nel batch |
| `prompt_tokens` | Integer | Token di input |
| `completion_tokens` | Integer | Token di output |
| `total_tokens` | Integer | Somma input + output |
| `n_ctx` | Integer | Finestra di contesto configurata (solo backend locali) |
| `duration_ms` | Integer | Tempo di inferenza in millisecondi |

### Architettura

- **`call_with_fallback()`** (`core/llm_backends.py`): accetta i parametri `caller`, `step`, `source_name`, `batch_size`. Dopo ogni chiamata riuscita, persiste il log tramite `_log_usage_to_db()` (best-effort, mai bloccante).
- **Pre-call token check** (solo `LlamaCppBackend`): prima di inviare il prompt, tokenizza l'input con `self._llm.tokenize()` e verifica che rimangano almeno 256 token per l'output. Se il budget è insufficiente, lancia `LLMValidationError` con messaggio diagnostico.

### Query statistiche

`db/repository.py` espone:

- **`get_token_usage_stats(backend, model_id, caller)`** — statistiche descrittive (count, mean, std, p50, p95, p99, CI 95%) raggruppate per (backend, model, caller).
- **`get_adaptive_n_ctx_cap(session, model_id, min_observations=1000)`** — ritorna il massimo CI-95% upper-bound dei `total_tokens` su tutti i gruppi `(caller, step)` con ≥ `min_observations` righe per il modello dato. Il risultato è arrotondato in su al prossimo multiplo di 1024 (floor: 2048). Ritorna `None` se i dati sono insufficienti.

### Cap adattivo n_ctx

La finestra di contesto si adatta automaticamente all'utilizzo reale in produzione:

1. **Cold start** (< 1000 osservazioni per caller/step): `LlamaCppBackend` usa il cap statico `DEFAULT_N_CTX_CAP` (16 384 token, ~2.3× il p100 osservato nel benchmark).
2. **Convergenza** (≥ 1000 osservazioni): il backend interroga `get_adaptive_n_ctx_cap()` al caricamento del modello e usa `min(gguf_nativo, cap_adattivo)` come finestra di contesto. Il cap adattivo è il massimo CI-95% upper-bound dei `total_tokens` su tutte le combinazioni caller/step, garantendo che la singola KV cache copra il worst-case.
3. **Stratificazione** — il CI è calcolato per gruppo `(caller, step)`, catturando le diverse dimensioni di prompt di ogni fase della pipeline (es. `classifier/step1_identity` vs `categorizer/batch_expense`).
4. **Safety** — il cap adattivo è sempre ≥ 2048 token. Se il DB non è disponibile, il cap statico viene usato silenziosamente.

```sql
-- Esempio: consumo per fase
SELECT caller, step, COUNT(*) as n,
       ROUND(AVG(total_tokens)) as avg_tokens,
       MAX(total_tokens) as max_tokens
FROM llm_usage_log GROUP BY caller, step;
```

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

Il modulo `chat_bot/` implementa un chatbot adattivo che risponde a domande sull'uso di Spendif.ai. La modalità viene scelta automaticamente in base al backend LLM configurato dall'utente in Impostazioni.

### Architettura

```
chat_bot/
├── engine.py           # ChatBotEngine — orchestratore, auto-detect modalità
├── rag.py              # RAGEngine — TF-IDF retrieval + generazione LLM
├── faq_classifier.py   # FAQClassifier — match deterministico TF-IDF (zero LLM)
├── kb_store.py         # Caricamento FAQ (JSON/MD) e chunk documenti
├── prompts.json        # System prompt + messaggi no-answer multilingua
└── knowledge/<lang>/   # FAQ e documenti per lingua (it, en)
    ├── faq.json        # [{"q": "...", "a": "...", "page": "<page_key>"}] — 150 item
    └── docs/           # File .md/.txt chunked per RAG (installazione, guida utente, ecc.)
```

**Stato corpus (2026-04-05):** `knowledge/it/` e `knowledge/en/` popolati con 150 Q&A
(`faq_can_do` + `faq_cannot_do`) e 5 manuali MD in `docs/`
(installazione, guida utente, configurazione, guida classificazione, reference guide).
Sorgente canonica: `documents/06_knowledge_base/` — modificare lì e rigenerare con lo script di conversione.

### Tre modalità

| Modalità | Condizione (da user settings) | Funzionamento |
|----------|-------------------------------|---------------|
| `rag_cloud` | Backend = `openai` / `claude` / `openai_compatible` con API key | Retrieval TF-IDF → LLM cloud genera risposta |
| `rag_local` | Backend = `local_ollama` / `vllm` | Retrieval TF-IDF → LLM locale genera risposta |
| `faq_match` | Backend = `local_llama_cpp` o nessuno | Cosine similarity su FAQ, risposta preconfezionata |

> **Nota:** `vllm_offline` è un backend esclusivo del benchmark (in-process, Linux/CUDA) e **non** è selezionabile come backend app in Impostazioni. Nell'app il backend vLLM esposto all'utente è sempre `vllm` (server OpenAI-compatible).

**Nota:** `ChatBotEngine` viene cached in `st.session_state["chatbot"]` e reinizializzato
automaticamente al cambio di backend (invalidato in `settings_page.py` al salvataggio).

### Sorgenti nelle risposte

`kb_store.py` espone il campo `page_ref` (chiave pagina app, es. `"import"`, `"review"`)
su ogni `FAQEntry`. `chat_page.py` lo converte in etichetta navigabile (`→ 📥 Import`).
Per i chunk da docs, viene mostrato il filename (`📄 guida_utente.md`). Sorgenti prive
di `page_ref` e non-doc (es. nomi file interni) vengono soppresse.

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
print(response.text)
print(response.sources)  # ["import", "review", ...] — page_ref keys
```

### Pipeline RAG (modalità cloud/local)

```
user question
  │
  ├─ _analyze_query()          LLM call 1 — query rewriting
  │   Riscrive la domanda in forma estesa con sinonimi e termini correlati.
  │   Restituisce stringa vuota se il messaggio è un puro saluto.
  │   NON riceve la history — evita context bleed tra domande consecutive.
  │
  ├─ se vuoto + (no "?" e len ≤ 30) → greeting response via _call_llm()
  │
  ├─ TF-IDF retrieval su query riscritta (top_k=5)
  │   Corpus: FAQ (tutti i lang) + doc chunks (tutti i lang)
  │   Indice costruito a startup, in-memory.
  │
  └─ _call_llm()               LLM call 2 — answer generation
      context (top-5) + history (ultimi 3 turni) + domanda originale
      System prompt: risponde nella lingua della domanda (language guard)
```

### Limiti noti e soluzioni

| Limite | Causa | Soluzione |
|--------|-------|-----------|
| **"Ciao, si paga?"** può essere trattato come saluto | Il modello a volte restituisce `""` per query con saluto iniziale | Fallback: se `""` ma `"?" in question` o `len > 30`, si usa la query raw per il retrieval |
| **Vocabulary mismatch TF-IDF** ("si paga" ≠ "gratis/costo") | TF-IDF è statistico, non semantico | Query rewriting via LLM espande con sinonimi; FAQ entry con vocabolario diretto degli utenti |
| **Risposta superficiale** ("vedi docs/X.md") | FAQ entry troppo shallow batte doc chunk nel retrieval | Rimuovere FAQ entry che rimandano a doc già nel corpus; system prompt istruisce il LLM a preferire passi concreti |
| **Scalabilità** oltre ~5000 entry | TF-IDF O(n) per query | Sostituire con FAISS o ChromaDB; la logica di retrieval è isolata in `_build_index` / `query()` |
| **FAQ_MATCH senza LLM** non gestisce saluti | Nessun LLM disponibile per _analyze_query | `knowledge/common/greetings.json` con entry per saluto comuni in tutte le lingue |
| **no_answer in lingua sbagliata** se LLM fallisce completamente | Fallback hardcoded usa `self._lang` (lingua UI) | Second-attempt con domanda originale; hardcoded solo come ultima istanza |
| **Allucinazione** — il modello inventa passi non nel contesto | GPT-4 e modelli potenti ignorano "ONLY using context" se non abbastanza esplicito | System prompt: "STRICTLY and EXCLUSIVELY", "do NOT add, infer, or invent"; non eliminabile al 100% con prompt engineering |

### Gestione della knowledge base

**Regola principale:** i doc in `knowledge/<lang>/docs/` sono la fonte primaria per contenuto dettagliato. Le FAQ servono per risposte brevi e dirette su fatti stabili, e per coprire il vocabolario degli utenti (varianti di domanda, sinonimi). **Non aggiungere FAQ entry che rimandano a un doc già nel corpus.**

1. Modificare `documents/06_knowledge_base/faq_can_do.it.json` / `faq_cannot_do.it.json`
2. Eseguire lo script di conversione per rigenerare `knowledge/{lang}/faq.json`
3. Per i manuali: modificare i `.md` in `sw_artifacts/docs/` e ricopiare in `knowledge/<lang>/docs/` (sia `it` che `en`)
4. Non indicizzare mai dati sensibili (credenziali, dati personali, strategie di business)

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
bash benchmark/run_benchmark_full.sh                             # classifier + categorizer, 1 run, tutti i backend
bash benchmark/run_benchmark_full.sh --benchmark classifier      # solo classifier
bash benchmark/run_benchmark_full.sh --benchmark both --runs 3   # entrambi, 3 run ciascuno
bash benchmark/run_benchmark_full.sh --setup-only                # solo download modelli

# Windows (PowerShell) — ENTRY POINT consigliato
powershell -ExecutionPolicy Bypass -File .\benchmark\run_benchmark_full.ps1
powershell -ExecutionPolicy Bypass -File .\benchmark\run_benchmark_full.ps1 -Benchmark both -Runs 3

# Solo llama.cpp (skip Ollama, vLLM e vLLM offline)
bash benchmark/run_benchmark_full.sh --skip-ollama --skip-vllm --skip-vllm-offline

# Solo vLLM offline (Linux/CUDA — no server, no GGUF download)
bash benchmark/run_benchmark_full.sh --skip-llama --skip-ollama --skip-vllm

# Skip dependency sync (usa venv esistente, utile con librerie compilate custom)
bash benchmark/run_benchmark_full.sh --skip-sync
```

`run_benchmark_full.sh` / `run_benchmark_full.ps1` gestiscono: setup completo (GGUF + Ollama pull + rilevamento vLLM), poi eseguono pipeline e categorizer per ogni backend attivo. La lista modelli è letta da `benchmark/benchmark_models.csv`.

### Piano di benchmarking multi-macchina

Il benchmarking si articola in due fasi:

**Fase 1 — Quick scan (8 file)**: 1 file per ogni combinazione `doc_type × format` (8 tipi), tutti i modelli abilitati (11 modelli: Qwen 2.5/3.5, Gemma 4, Phi4-mini, Nemotron-Mini, Mistral-7B, DeepSeek-R1). Serve per scegliere max 2 modelli. Stima: ~3h su GPU, ~6-8h su CPU.

```bash
bash benchmark/run_benchmark_full.sh --max-files 8
# Windows:
# powershell -ExecutionPolicy Bypass -File benchmark\run_benchmark_full.ps1 -MaxFiles 8
```

**Fase 2 — Full (50 file)**: solo i 2 modelli selezionati, tutti i 50 file per risultati statisticamente robusti.

**Gestione spazio disco (download just-in-time)**: i GGUF **non** vengono scaricati tutti in anticipo. Per ogni modello il flusso è: (1) verifica disco ≥ 16GB + size modello, (2) download just-in-time se non presente, (3) run benchmark, (4) cleanup se disco < 16GB dopo il run, (5) prossimo modello. Questo permette di testare l'intero catalogo (11 modelli, ~35GB) anche su macchine con soli 20-25GB liberi. Il filtro RAM (`RAM × 3/4`) skippa modelli troppo grandi per la memoria.

**File di tracking**: `benchmark/benchmark_plan.csv` traccia il completamento per macchina × modello × backend × fase. Colonne: `machine, model, backend, phase, n_files_target, status, exact_pct, fuzzy_pct, err_pct, fb_pct, s_per_10tx, notes`. Status: `todo` → `running` → `done` / `skip` / `blocked`.

**Nomi macchine**: `benchmark/machine_names.csv` mappa hostname tecnici a nomi amichevoli. Aggiungere una riga per ogni nuova macchina bench:

```csv
hostname,machine_name
Luigis-MacBook-Pro.local,Mac Luigi
junone,Junone
```

Lo script `benchmark_stats.py --by-host` usa automaticamente i nomi amichevoli.

**Flusso collection risultati**:

1. Copiare i CSV dalla macchina bench → `benchmark/results/`
2. Aggiungere hostname → nome in `machine_names.csv` (se nuovo)
3. Lanciare `python benchmark/benchmark_stats.py --by-group --by-host`
4. Aggiornare `benchmark_plan.csv` con i KPI

### Protezione librerie compilate custom

Quando si compila manualmente una libreria GPU-specific (es. `llama-cpp-python` con Vulkan o ROCm), `uv sync` potrebbe sovrascriverla con la versione PyPI (CPU-only). Il benchmark ha un meccanismo di protezione a tre livelli:

1. **Gate automatico**: prima del sync, lo script fa un dry-run. Se `uv sync` toccherebbe una libreria custom elencata in `benchmark/.custom_packages`, chiede conferma interattiva (`Proceed? [y/N]`). Se l'utente rifiuta, il sync viene saltato.

2. **Backup/restore con confronto versioni**: se il sync procede, le librerie custom vengono backuppate. Dopo il sync:
   - Se uv ha **rimosso** la libreria → ripristina il backup
   - Se uv ha **downgradato** (versione minore) → ripristina il backup (build custom vince)
   - Se uv ha **aggiornato** (versione uguale o maggiore) → tiene quella di uv

3. **`--skip-sync`**: salta completamente `uv sync`, usa il venv così com'è. Consigliato su macchine con build GPU custom già funzionanti (es. Linux + Vulkan).

**File `benchmark/.custom_packages`**: lista dei pacchetti da proteggere (uno per riga). Aggiungere qualsiasi libreria compilata manualmente:

```
# benchmark/.custom_packages
llama_cpp_python
vllm
torch
triton
```

### Rilevamento GPU

Lo script rileva automaticamente il backend GPU in ordine di priorità:

| Priorità | Backend | Condizione |
|----------|---------|------------|
| 1 | Metal | macOS + Apple Silicon |
| 2 | CUDA | `nvidia-smi` presente |
| 3 | ROCm | `rocm-smi` + `rocminfo` + GPU CDNA (gfx9xx, serie MI) |
| 4 | Vulkan | `vulkaninfo` + GPU AMD/Radeon |
| 5 | CPU | fallback |

**Nota**: GPU AMD consumer (Radeon RX) hanno `rocm-smi` installato ma non supportano ROCm per compute. Lo script verifica che `rocminfo` sia presente e che la GPU sia CDNA (gfx9xx) prima di selezionare ROCm. Le Radeon RDNA (gfx10xx, gfx11xx) cadono su Vulkan.

### Setup modelli + Dual benchmark (llama.cpp + Ollama)

Per eseguire un benchmark completo su tutti i backend:

```bash
cd ~/Documents/Progetti/PERSONALE/Spendif.ai/sw_artifacts

# Full benchmark: tutti i backend (llama.cpp + Ollama + vLLM), setup automatico
bash benchmark/run_benchmark_full.sh

# Solo setup modelli, senza benchmark
bash benchmark/run_benchmark_full.sh --setup-only

# Con più run per ridurre la varianza
bash benchmark/run_benchmark_full.sh --runs 3

# Salta un backend specifico
bash benchmark/run_benchmark_full.sh --skip-ollama
bash benchmark/run_benchmark_full.sh --skip-vllm
bash benchmark/run_benchmark_full.sh --skip-vllm-offline
```

Il setup scarica automaticamente i modelli GGUF mancanti e fa `ollama pull` per i modelli Ollama. La lista modelli è in `benchmark/benchmark_models.csv`.

### Comandi manuali (avanzato)

```bash
# Singolo modello llama.cpp (n_ctx auto-detect dal GGUF)
uv run python benchmark/benchmark_classifier.py --runs 1 --backend local_llama_cpp \
  --model-path ~/.spendifai/models/gemma-3-12b-it-Q4_K_M.gguf

# Forza un n_ctx specifico (limita RAM)
uv run python benchmark/benchmark_classifier.py --runs 1 --backend local_llama_cpp \
  --model-path ~/.spendifai/models/gemma-3-12b-it-Q4_K_M.gguf --n-ctx 2048

# Singolo modello Ollama (n_ctx auto-detect via /api/show)
uv run python benchmark/benchmark_classifier.py --runs 1 --backend local_ollama --model gemma3:12b

# Gemma 4 E2B
uv run python benchmark/benchmark_classifier.py --runs 1 --backend local_llama_cpp \
  --model-path ~/.spendifai/models/gemma-4-E2B-it-Q4_K_M.gguf
uv run python benchmark/benchmark_classifier.py --runs 1 --backend local_ollama --model gemma4:e2b

# Categorizer con Ollama
uv run python benchmark/benchmark_categorizer.py --runs 1 --backend local_ollama --model gemma3:12b

# vLLM server (locale o remoto — auto-detect modello e context window)
vllm serve Qwen/Qwen2.5-3B-Instruct  # in un altro terminale
uv run python benchmark/benchmark_classifier.py --runs 1 --backend vllm

# vLLM offline (Linux/CUDA — in-process, nessun server, usa HF model ID da benchmark_models.csv)
# Richiede: pip install vllm && GPU CUDA disponibile
uv run python benchmark/benchmark_classifier.py --runs 1 \
  --backend vllm_offline --model Qwen/Qwen2.5-3B-Instruct
uv run python benchmark/benchmark_categorizer.py --runs 1 \
  --backend vllm_offline --model google/gemma-3-4b-it

# Diagnostica ambiente (prima di eseguire benchmark)
bash benchmark/bench_report.sh

# Suite completa (tutti i backend)
bash benchmark/run_benchmark_full.sh
```

Ogni invocazione scrive i propri risultati in `benchmark/results/<YYYYMMDDHHMMSS>-<sha>_<hostname>_<N>.csv` (un file per run, mai sovrascrive). L'aggregatore offline `benchmark/aggregate_results.py` produce `results_all_runs.csv` sommando tutti i file dell'archivio; il monitor legge direttamente i file archivio e non `results_all_runs.csv`.

### Context window auto-detect

Il benchmark rileva automaticamente la context window ottimale per ogni modello:

| Backend | Metodo |
|---------|--------|
| llama.cpp | Legge `llama.context_length` dall'header GGUF (senza caricare i pesi) |
| Ollama | Chiama `/api/show` e legge il context del modello |
| OpenAI / Claude | Lookup statico (`_KNOWN_CONTEXT`: gpt-4o=128k, claude-3-5=200k, …) |
| vLLM (server) | Interroga `/v1/models` |
| vLLM offline | Usa `max_tokens=4096` (fisso) via `SamplingParams`; il context del modello è gestito internamente da vLLM |

`--n-ctx 0` (default) = auto-detect. Imposta un valore esplicito per limitare l'uso di RAM.

### Catalogo modelli (benchmark_models.csv)

`benchmark/benchmark_models.csv` è la sorgente unica della lista modelli per tutti gli script di benchmark. Colonne:

| Colonna | Descrizione |
|---------|-------------|
| `name` | Nome breve del modello (es. `Qwen3.5-4B`) |
| `gguf_file` | Nome file GGUF — se valorizzato, il modello è disponibile su llama.cpp |
| `gguf_repo` | Repository HuggingFace del GGUF (es. `bartowski/Qwen_Qwen3.5-4B-GGUF`) |
| `gguf_hf_url` | URL diretto al file GGUF su HuggingFace (download) |
| `ollama_tag` | Tag Ollama (es. `qwen3.5:4b`) — se valorizzato, il modello è disponibile su Ollama |
| `vllm_model` | HF model ID per vLLM offline (es. `Qwen/Qwen2.5-3B-Instruct`) — se vuoto, il modello non è eseguito con vllm_offline |
| `size_mb` | Dimensione approssimativa in MB (usata per il filtro RAM) |
| `enabled` | `true`/`false` — se `false`, il modello è saltato in tutti gli script |
| `delete_after` | `true`/`false` — se `true`, il file GGUF viene eliminato dopo il run |

Il catalogo contiene 20 modelli: Qwen3.5 (0.8B–35B), Gemma4 (E2B–31B), Llama3.2-3B, Phi4-mini e Phi4-14B, nelle varianti Q3 e Q4. I modelli vLLM server non sono nel CSV — vengono auto-rilevati dal server in esecuzione tramite `/v1/models` al momento del benchmark.

### Monitoraggio HW (CPU + GPU)

Il modulo `benchmark/hw_monitor.py` (`HWMonitor`) campiona CPU e GPU in background ogni 0.5 s durante l'intero run di benchmark, producendo medie più accurate rispetto ai vecchi campioni point-in-time.

| Piattaforma | Metodo GPU | Note |
|-------------|-----------|------|
| macOS Apple Silicon | `ioreg` / AGXAccelerator → Device Utilization % | Nessun sudo richiesto |
| Linux NVIDIA | `nvidia-smi` → utilization % + power watts | Richiede driver NVIDIA |
| Linux AMD | `rocm-smi` → utilization % | Richiede ROCm |
| Fallback | — | GPU utilization = 0.0 |

`benchmark_classifier.py` e `benchmark_categorizer.py` usano `HWMonitor` al posto delle vecchie funzioni inline `_sample_cpu_load()` / `_sample_gpu_utilization()`.

### Versioning sessione (bench_guard)

`benchmark/bench_guard.sh` / `bench_guard.ps1` — eseguiti automaticamente da `run_benchmark_full` all'avvio — garantiscono che ogni sessione di benchmark abbia una stringa di versione univoca nel campo `version` di tutti i CSV prodotti.

| Contesto | Comportamento |
|----------|---------------|
| **Macchina dev** (git disponibile) | Rigenera `benchmark/.version` = `YYYYMMDDHHMMSS-<sha7>`. Fresco a ogni lancio, indipendente da commit. |
| **Macchina remota** (no git, `.version` presente) | Usa il `.version` scritto da `bench_push_usb` / `bench_push_ssh`. |
| **No git + no `.version`** | **Errore bloccante** con hint sugli script di deployment. |

Questo consente al monitor di isolare la sessione corrente filtrando per `version` (stesso SHA+timestamp → stessa sessione), senza dipendere da push/commit espliciti sulla macchina dev.

### Limitazioni backend Ollama

Ollama supporta output JSON strutturato via `format: json_schema`, ma fallisce con schema complessi (lo schema single-step del classifier, ~20 campi, restituisce risposte vuote). L'`OllamaBackend` ora rileva `model_size_bytes` via `/api/show` per abilitare la classificazione multi-step (3 schema più piccoli) sui modelli piccoli, migliorando il tasso di successo. Tuttavia Ollama è **skippato di default** nel benchmark (`SKIP_OLLAMA=true`) — usare llama.cpp. Ollama resta disponibile nell'app come backend opzionale.

### Equivalenza di accuratezza tra commit

Non tutti i commit modificano la pipeline di classificazione/categorizzazione. Commit che toccano solo infra, docs, CI o performance producono **risultati di accuratezza identici**. I risultati di benchmark sono confrontabili solo tra commit dello stesso gruppo di equivalenza.

La tabella completa è in [`benchmark/ACCURACY_EQUIVALENCE.md`](../benchmark/ACCURACY_EQUIVALENCE.md).

**Regola pratica**: un commit è un _accuracy boundary_ se tocca `core/categorizer.py`, `core/classifier.py`, `core/description_cleaner.py`, `core/normalizer.py`, `core/sanitizer.py` o `core/nsi_lookup.py`. Tutti i commit tra due boundary consecutivi sono equivalenti.

**Uso nello script di statistiche**:

```bash
# Statistiche raggruppate per gruppo di equivalenza (non per commit singolo)
python benchmark/benchmark_stats.py --by-group

# Statistiche per commit singolo (utile per debug, non per confronto accuratezza)
python benchmark/benchmark_stats.py --by-commit
```

**Nota su `164bcac` (cap n_ctx)**: questo commit non cambia la logica di classificazione, ma limita `n_ctx` a 16K. Senza il cap, modelli con context window grande (es. Qwen 3.5, 262K) allocano KV cache enormi → GPU sottoutilizzata → output troncati → fallback artificialmente più alti. In pratica, risultati pre-cap e post-cap nello stesso gruppo possono divergere sui modelli Qwen.

### Monitor avanzamento (monitor_benchmark)

`benchmark/monitor_benchmark.sh` / `monitor_benchmark.ps1` / `monitor_benchmark.py` mostrano l'avanzamento in tempo reale. Leggono tutti i file archivio in `benchmark/results/*.csv` (non `results_all_runs.csv`, che è prodotto solo dall'aggregatore offline) e filtrano per il campo `version` per isolare la sessione corrente. Features: progress bar per modello, fase corrente (classifier/categorizer) rilevata dalla colonna `benchmark_type`, statistiche CPU/GPU live via `HWMonitor.sample_once()` e medie storiche dal CSV. Opzioni principali: `--interval N` (refresh in secondi), `--runs N` (run attesi per modello), `--total N` (righe totali attese), `--once` (snapshot e termina), `--all` (mostra anche modelli completati).

### Script disponibili

| Script | Scopo |
|--------|-------|
| `benchmark/run_benchmark_full.sh` | **ENTRY POINT** (macOS/Linux): tutti i backend × pipeline + categorizer |
| `benchmark/run_benchmark_full.ps1` | **ENTRY POINT** (Windows): tutti i backend × pipeline + categorizer |
| `benchmark/bench_guard.sh` | Version gate (macOS/Linux): genera/verifica `benchmark/.version` |
| `benchmark/bench_guard.ps1` | Version gate (Windows): genera/verifica `benchmark\.version` |
| `benchmark/bench_report.sh` | Report diagnostico ambiente: backend disponibili, modelli, GPU, RAM — non modifica nulla |
| `benchmark/cleanup_benchmark.sh` | Pulizia file generati |
| `benchmark/benchmark_models.csv` | Catalogo modelli — sorgente unica per tutti gli script |
| `benchmark/hw_monitor.py` | Monitoraggio HW in background (CPU + GPU cross-platform) |
| `benchmark/monitor_benchmark.sh` | Monitor avanzamento benchmark in tempo reale (macOS/Linux) |
| `benchmark/monitor_benchmark.ps1` | Monitor avanzamento benchmark in tempo reale (Windows) |
| `benchmark/monitor_benchmark.py` | Monitor avanzamento benchmark cross-platform (Python) |
| `benchmark/benchmark_stats.py` | Statistiche di accuratezza per fase e modello (`--by-commit`, `--by-group`, `--by-host`) |
| `benchmark/benchmark_plan.csv` | Tracking completamento benchmark per macchina × modello × fase |
| `benchmark/machine_names.csv` | Mapping hostname tecnici → nomi amichevoli (usato da stats `--by-host`) |
| `benchmark/ACCURACY_EQUIVALENCE.md` | Tabella gruppi di equivalenza commit (quali commit producono risultati confrontabili) |
| `benchmark/diagnose.ps1` | Diagnostica ambiente Windows (include rilevamento GPU: NVIDIA/AMD/Intel) |

### Logging

Ogni esecuzione salva un log in `benchmark/logs/` (gitignored, un file per run con timestamp):

| Script | Log |
|--------|-----|
| `run_benchmark_full.sh` | `benchmark/logs/benchmark_YYYYMMDD_HHMMSS.log` |
| `benchmark_classifier.py` | `benchmark/logs/classifier_YYYYMMDD_HHMMSS.log` |
| `benchmark_categorizer.py` | `benchmark/logs/categorizer_YYYYMMDD_HHMMSS.log` |

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

### Scenari benchmark categorizer (`--scenario`)

Il benchmark categorizer supporta scenari predefiniti che simulano diversi livelli di "warm data" disponibile al momento della categorizzazione. Lo scenario controlla quali sorgenti deterministiche sono attive prima della chiamata LLM.

| Scenario | Warm data attivo | LLM% atteso | Note |
|---|---|---|---|
| `cold` (default) | nessuno | ~70% | Baseline pura — utente nuovo |
| `nsi_warm` | NSI + taxonomy_map | ~30-40% | Solo fonti pubbliche, nessuna history |
| `cross_warm` | NSI + history leave-one-out | ~20-50% | **Realistico**: history da tutti i file GT tranne il file corrente. Simula utente con storico pregresso ma file mai visto |
| `full_warm` | NSI + history (tutti i GT) | <5% | Upper bound teorico — 100% per costruzione (include il file stesso) |
| `country_with` | NSI + country ranking | ~30-40% | Come nsi_warm con bias geografico |
| `country_without` | NSI, senza country | ~30-40% | — |
| `all` | esegue tutti gli scenari in sequenza | — | Ordine: cold → nsi_warm → cross_warm → full_warm → country |

> **Interpretazione degli scenari:**
> `cold` e `full_warm` sono i due estremi (lower/upper bound). `cross_warm` è lo scenario
> più rappresentativo della realtà: misura il beneficio dello storico su controparti
> **ricorrenti tra file diversi** (es. stessa banca, mesi diversi).
> `full_warm` a 100% è tautologico — non usarlo come metrica principale.

> **💡 Risultato empirico chiave — Determinismo del counterpart extraction**
>
> Il 100% di accuratezza in `full_warm` **non** indica che l'LLM "impara" dalle transazioni precedenti.
> Indica che il passo di **estrazione controparte è deterministico e consistente tra run diversi**:
> data la stessa stringa raw di banca, il modello produce sempre lo stesso nome normalizzato
> (es. `PAGAM. POS 549,91 EUR DEL 01.01 CARTOLIBRERIA IL PAPIRO` → `Cartolibreria Il Papiro`).
> Questo rende la chiave del cache storico stabile, e il lookup deterministico affidabile.
>
> **L'LLM ha qualità per-chiamata invariante** — non migliora con l'uso. Ciò che cresce nel
> tempo è lo **scudo deterministico** (storico + regole + NSI-map), che riduce monotonicamente
> il numero di chiamate LLM. Il tasso di invocazione LLM per transazione è la metrica di
> maturità del sistema, non la sua accuracy sul cold start.
>
> **Due motori di crescita indipendenti:** (1) *user-driven* — storico validato, regole personali,
> taxonomy map individuale; (2) *community-driven* — il knowledge base NSI (OpenStreetMap/GeoNames)
> cresce continuamente grazie ai contributori globali: quando un nuovo brand viene aggiunto a OSM,
> la pipeline lo risolve deterministicamente al prossimo refresh del DB, senza alcuna azione
> dell'utente. La metrica `taxonomy_map_hit_pct` misura la maturità NSI nel tempo.

**Nuove colonne CSV** prodotte da ogni run con scenario: `scenario`, `n_nsi`, `nsi_accuracy`, `nsi_coverage_pct`, `taxonomy_map_hit_pct`.

> **⚠️ Limitazione nota — taxonomy_map condivisa tra modelli**
>
> Il benchmark costruisce la `taxonomy_map` (OSM tag → categoria/sottocategoria) **una sola volta** per sessione, prima del loop sui modelli, usando la parte statica di `NsiTaxonomyService` (`osm_to_spendifai_map.json`).
> Nella pipeline reale la `taxonomy_map` include anche una componente LLM-assistita (step `_llm_map`) che varia per modello, ed è invalidata automaticamente al cambio della taxonomy dell'utente (via SHA-256).
>
> **Implicazioni per l'utente:**
> - Confrontare scenari `nsi_warm` tra modelli diversi nella stessa sessione può sottostimare il vantaggio dei modelli più capaci nella fase di mapping.
> - Se si modifica la taxonomy tra sessioni di benchmark, rilanciare sempre l'intera sessione per garantire coerenza della `taxonomy_map`.
>
> **Roadmap:** ricostruire la `taxonomy_map` per ogni modello testato (aggiunge ~N secondi per modello, trade-off documentato in T-09).

**Entry point:**
```bash
bash benchmark/run_benchmark_full.sh --scenario nsi_warm
bash benchmark/run_benchmark_full.sh --scenario all   # tutti gli scenari in sequenza
```

### Benchmark cross-platform (Mac remoto)

Per confrontare performance tra macchine diverse (es. M1 Max locale vs M4 remoto):

**Setup server (Mac M4 — host remoto):**

```bash
# 1. Installa llama.cpp
brew install llama.cpp

# 2. Scarica modello
brew install huggingface-cli
huggingface-cli download google/gemma-3-12b-it-GGUF gemma-3-12b-it-Q4_K_M.gguf \
  --local-dir ~/.spendifai/models/

# 3. Lancia server (aperto sulla rete locale)
llama-server -m ~/.spendifai/models/gemma-3-12b-it-Q4_K_M.gguf \
  --host 0.0.0.0 --port 8080 -ngl 99 -c 4096

# 4. Verifica
curl http://localhost:8080/v1/models
```

**Setup client (Mac M1 Max — esegue i benchmark):**

```bash
# Punta al server remoto via backend openai_compatible
uv run python benchmark/benchmark_classifier.py --runs 1 \
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

### Benchmark veloce tok/s (senza Spendif.ai)

Per misurare la velocità pura del modello senza overhead pipeline:

```bash
# llama-bench (incluso in llama.cpp)
llama-bench -m ~/.spendifai/models/gemma-3-12b-it-Q4_K_M.gguf -ngl 99

# Tutti i modelli
for m in ~/.spendifai/models/*.gguf; do
  echo "=== $(basename $m) ==="
  llama-bench -m "$m" -ngl 99
done
```

### Modelli disponibili localmente

```bash
# GGUF (llama.cpp)
ls -lh ~/.spendifai/models/*.gguf

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
git add benchmark/results_all_runs.csv
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
az group create -n spendifai-rg -l westeurope
az ml workspace create -n spendifai-ml -g spendifai-rg
az acr create -n spendifaiacr -g spendifai-rg --sku Basic
az ml compute create -n gpu-t4-spot -g spendifai-rg -w spendifai-ml \
    --type AmlCompute --size Standard_NC6s_v3 \
    --min-instances 0 --max-instances 5 --tier low_priority

# 4. Esportare variabili (.env o shell)
export AZURE_SUBSCRIPTION_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
export AZURE_RESOURCE_GROUP=spendifai-rg
export AZURE_ML_WORKSPACE=spendifai-ml
export AZURE_ACR_NAME=spendifaiacr
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
BENCH_CSV="benchmark/results_all_runs.csv"
if git diff --name-only HEAD | grep -q "$BENCH_CSV"; then
  echo "Benchmark results included in push"
fi
```

**Flusso per un nuovo developer:**
```bash
git clone ...
git pull                          # prende tutti i risultati storici

bash benchmark/run_benchmark_full.sh  # resume skippa tutto ciò che esiste,
                                  # aggiunge solo il suo HW + commit

# Apri PR con i risultati (mai push diretto su main)
git checkout -b bench/$(date +%Y-%m-%d)-m1max
git add benchmark/results_all_runs.csv
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
| NSI + `taxonomy_map` per bypass LLM | Brand noti (Esselunga, Q8, …) categorizzati deterministicamente senza LLM se `user_country` confermato. `nsi_tag_mapping` in DB, invalidata da SHA-256 sulla tassonomia. |
| Skip fogli XLSX aggregate (`_SUMMARY_SHEET_RE`) | I workbook multi-foglio delle banche contengono spesso un foglio riepilogativo ("Riepilogo", "Summary", "Zusammenfassung", …). `detect_best_sheet()` lo esclude selezionando solo il foglio con più righe numeriche. La regex è allineata con `_FOOTER_SUSPECT_KEYWORDS` per le 5 lingue supportate (it/en/fr/de/es). |
| `LlamaCppBackend.get_context_info()` — fallback `n_ctx_train` | `llama-cpp-python >= 0.3.x` ha rimosso il metodo `n_ctx_train()`. Il codice ora prova `n_ctx_train()`, e in caso di `AttributeError` legge il valore dal metadata GGUF (`read_gguf_context_length`) o usa `n_ctx()` come ultimo fallback. |
| Preview match nella form "Nuova regola" (issue #15) | Prima di creare una regola, `rules_page.py` chiama `tx_svc.get_by_rule_pattern()` e mostra quante transazioni esistenti corrispondono. Se N > 0 compare un checkbox (default True) per applicare la categoria anche alle transazioni già presenti — stesso comportamento del form di modifica regola. |
| Chatbot: `ChatBotEngine` invalidato al cambio backend | Il chatbot è cached in `st.session_state["chatbot"]` per evitare reload a ogni interazione. `settings_page.py` cancella la chiave al salvataggio delle impostazioni LLM, forzando la reinizializzazione con il nuovo backend alla prossima apertura della pagina Chat. |
| Chatbot: TF-IDF su 150 Q&A + 5 manuali, nessun vector DB | Corpus totale < 1 MB: TF-IDF in-memory è sufficiente. Vector DB (Chroma/Qdrant) aggiunto solo se il corpus supera ~500 item. Sorgente canonica in `documents/06_knowledge_base/`; `knowledge/{lang}/` è artifact generato. |

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
