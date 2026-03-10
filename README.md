# Spendify v2.1

Registro finanziario personale unificato con pipeline ibrida deterministica + LLM.

Aggrega estratti conto eterogenei (conti correnti, carte di credito, carte di debito, conti deposito, prepagate) in un unico ledger cronologico, eliminando il double-counting da addebiti carta periodici e da giroconti interni. Il processing avviene in modalità **offline-first**; i backend LLM remoti sono supportati come opt-in con sanitizzazione PII obbligatoria.

---

## Indice

- [Caratteristiche principali](#caratteristiche-principali)
- [Architettura](#architettura)
- [Struttura del progetto](#struttura-del-progetto)
- [Installazione](#installazione)
- [Configurazione](#configurazione)
- [Avvio](#avvio)
- [Tassonomia](#tassonomia)
- [Test](#test)
- [Decisioni di design](#decisioni-di-design)

---

## Caratteristiche principali

| Funzionalità | Dettaglio |
|---|---|
| **Classificazione automatica** | Rileva tipo di documento (conto corrente, carta, prepagata, deposito) senza configurazione preventiva |
| **Normalizzazione deterministica** | Encoding detection, delimiter detection, header detection, importi in `Decimal` (mai `float`) |
| **Idempotenza SHA-256** | Re-importare lo stesso file produce esattamente lo stesso insieme di righe |
| **Riconciliazione carta–c/c (RF-03)** | Algoritmo a 3 fasi che elimina il double-counting da addebiti aggregati mensili |
| **Rilevamento giroconti (RF-04)** | Matching simbolico importo+finestra temporale; esclusione o neutralizzazione configurabile |
| **Categorizzazione a cascata (RF-05)** | Regole utente → regex statiche → LLM strutturato → fallback "Altro" |
| **Tassonomia a 2 livelli** | 15 categorie di spesa + 7 di entrata, personalizzabile via `taxonomy.yaml` |
| **Backend LLM multi-provider** | Ollama (locale, default), OpenAI, Claude — interfaccia astratta comune, nessun LangChain |
| **PII sanitization (RF-10)** | IBAN, PAN, CF, nomi del titolare redatti prima di qualsiasi chiamata remota |
| **Circuit breaker** | Fallback automatico su Ollama locale; quarantena (`to_review=True`) se tutti i backend falliscono |
| **Persistenza SQLAlchemy** | 6 tabelle ORM; CRUD idempotente; migrazioni Alembic |
| **Export report** | HTML standalone (Plotly), CSV, XLSX |
| **UI Streamlit 4 pagine** | Import → Ledger → Analytics → Revisione |

---

## Architettura

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
   già noto             (campione sanitizzato)
          │
   normalizer.py        sanitizer.py     llm_backends.py
   ├─ encoding detect   ├─ IBAN/PAN/CF   ├─ OllamaBackend
   ├─ parse_amount()    ├─ owner names   ├─ OpenAIBackend
   ├─ SHA-256 tx_id     └─ assert_sani.. └─ ClaudeBackend
   ├─ RF-03 reconcile                       BackendFactory
   └─ RF-04 transfers                       call_with_fallback()
          │
   categorizer.py  ←── taxonomy.yaml (2 livelli)
   Step 0: regole utente
   Step 1: regex statiche
   Step 2: stub ML
   Step 3: LLM structured output
   Step 4: fallback "Altro"
          │
      db/repository.py   (SQLAlchemy, idempotente)
      └─ Transaction · ImportBatch · DocumentSchemaModel
         ReconciliationLink · InternalTransferLink · CategoryRule
          │
      reports/generator.py
      └─ HTML (Jinja2+Plotly) · CSV · XLSX
```

### Flow 1 vs Flow 2

| | Flow 1 | Flow 2 |
|---|---|---|
| **Attivazione** | `DocumentSchema` già in DB per quel file hash / istituto | Prima importazione di un nuovo formato |
| **Schema** | Recuperato da DB, applicato direttamente | LLM inferisce lo schema da un campione anonimizzato |
| **Promozione** | — | Il template Flow 2 approvato viene salvato e diventa Flow 1 |
| **Costo LLM** | Zero (solo categorizzazione) | Una chiamata per classificazione + una per categorizzazione batch |

---

## Struttura del progetto

```
spendify/
├── app.py                  # Entry point Streamlit
├── taxonomy.yaml           # Tassonomia a 2 livelli (spese + entrate)
├── .env.example            # Template variabili d'ambiente
├── pyproject.toml          # Dipendenze (uv / pip)
│
├── core/
│   ├── models.py           # Enum: DocumentType, TransactionType, GirocontoMode …
│   ├── schemas.py          # DocumentSchema (Pydantic) + llm_json_schema()
│   ├── llm_backends.py     # LLMBackend ABC · Ollama · OpenAI · Claude · BackendFactory
│   ├── sanitizer.py        # PII redaction (RF-10)
│   ├── normalizer.py       # Encoding, parse_amount (Decimal), SHA-256, RF-03, RF-04
│   ├── classifier.py       # Flow 2: inferenza DocumentSchema via LLM
│   ├── categorizer.py      # Cascata a 4 step + TaxonomyConfig
│   └── orchestrator.py     # Pipeline principale: ProcessingConfig · process_file()
│
├── db/
│   ├── models.py           # ORM SQLAlchemy (6 tabelle)
│   └── repository.py       # CRUD idempotente · persist_import_result()
│
├── reports/
│   ├── generator.py        # HTML (Jinja2+Plotly) · CSV · XLSX
│   └── template_report.html.j2
│
├── ui/
│   ├── sidebar.py          # Navigazione + selettori backend/giroconto
│   ├── upload_page.py      # Import multi-file
│   ├── registry_page.py    # Ledger filtrable + download
│   ├── analysis_page.py    # 5 grafici Plotly + export HTML
│   ├── review_page.py      # Correzione categorie + salvataggio regole
│   └── reconciliation_page.py
│
├── tests/
│   ├── test_normalizer.py  # 18 test deterministici (parse_amount, SHA-256 …)
│   ├── test_backends.py    # 10 test (factory, validazione, mock Ollama)
│   └── test_categorizer.py # 10 test (regole statiche, cascata)
│
└── support/                # Moduli legacy mantenuti per compatibilità
```

---

## Installazione

### Prerequisiti

- **Python 3.13+**
- **[uv](https://github.com/astral-sh/uv)** (gestore pacchetti consigliato) oppure `pip`
- **[Ollama](https://ollama.com)** per il backend LLM locale (default)

### 1. Clona il repository

```bash
git clone https://github.com/drake69/spendify.git
cd spendify
```

### 2. Installa le dipendenze

```bash
# Con uv (consigliato)
uv sync

# Oppure con pip
pip install -e .
```

### 3. Configura le variabili d'ambiente

```bash
cp .env.example .env
# Modifica .env con i tuoi valori
```

### 4. Scarica il modello LLM locale

```bash
ollama pull gemma3:9b
```

> Mantieni Ollama in esecuzione (`ollama serve`) durante l'uso dell'app.

---

## Configurazione

Tutte le opzioni si impostano nel file `.env`:

```dotenv
# Database (SQLite di default, qualsiasi URL SQLAlchemy)
SPENDIFY_DB=sqlite:///ledger.db

# Percorso tassonomia personalizzata
TAXONOMY_PATH=taxonomy.yaml

# Nomi del titolare da redarre prima di chiamate remote
OWNER_NAMES=Mario Rossi,M. Rossi

# Backend LLM: local_ollama | openai | claude
LLM_BACKEND=local_ollama

# --- Ollama (locale, default, privacy garantita) ---
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=gemma3:9b

# --- OpenAI (opt-in remoto — richiede sanitizzazione PII) ---
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini

# --- Claude / Anthropic (opt-in remoto — richiede sanitizzazione PII) ---
ANTHROPIC_API_KEY=sk-ant-...
CLAUDE_MODEL=claude-3-5-haiku-20241022
```

### Modalità giroconto

Configurabile dalla sidebar dell'app:

| Modalità | Comportamento |
|---|---|
| `neutral` | I giroconti restano nel ledger come `internal_out` / `internal_in` (default) |
| `exclude` | I giroconti vengono rimossi dal registro (saldo netto non influenzato) |

### Privacy e backend remoti

```
[LOCAL — default]  Ollama locale: nessun dato esce dal processo.
                   Nessuna sanitizzazione richiesta.

[REMOTE — opt-in]  OpenAI / Claude: PII sanitization OBBLIGATORIA.
                   IBAN → <ACCOUNT_ID>  |  PAN → <CARD_ID>
                   CF   → <FISCAL_ID>  |  owner → <OWNER>
                   Chiamata bloccata se assert_sanitized() fallisce.
```

---

## Avvio

```bash
# Con uv
uv run streamlit run app.py

# Oppure
streamlit run app.py
```

L'app si apre su `http://localhost:8501` con 4 pagine:

| Pagina | Descrizione |
|---|---|
| **Import** | Carica uno o più file (CSV / XLSX / PDF). Mostra riepilogo: transazioni importate, riconciliazioni, transfer link, flow usato (1/2). |
| **Ledger** | Tabella filtrabile per data, tipo, flag revisione. Metriche netto/entrate/uscite. Download CSV/XLSX. |
| **Analytics** | 5 grafici Plotly: barre mensili, linea saldo cumulativo, treemap spese, top-10 categorie, stacked per conto. Export HTML. |
| **Revisione** | Transazioni con `to_review=True`. Correzione categoria + salvataggio opzionale come regola permanente. |

---

## Tassonomia

La tassonomia è definita in `taxonomy.yaml` e caricata a runtime. Struttura:

```yaml
version: "1"

expenses:
  - category: "Casa"
    subcategories:
      - "Mutuo / Affitto"
      - "Gas"
      - "Energia elettrica"
      # …

  - category: "Alimentari"
    subcategories:
      - "Spesa supermercato"
      # …

income:
  - category: "Lavoro dipendente"
    subcategories:
      - "Stipendio"
      - "Bonus e premi"
      # …
```

**Categorie di spesa (15):** Casa · Alimentari · Ristorazione · Trasporti · Salute · Istruzione · Abbigliamento · Comunicazioni · Svago e tempo libero · Animali domestici · Finanza e assicurazioni · Cura personale · Tasse e tributi · Regali e donazioni · Altro

**Categorie di entrata (7):** Lavoro dipendente · Lavoro autonomo · Rendite finanziarie · Rendite immobiliari · Trasferimenti e rimborsi · Prestazioni sociali · Altro entrate

Per modificare la tassonomia modifica `taxonomy.yaml` e riavvia l'app — l'enum JSON Schema per i prompt LLM viene rigenerato automaticamente.

---

## Test

```bash
# Tutti i test (44 test deterministici, nessun mock LLM richiesto)
uv run python -m pytest tests/ -v

# Con coverage
uv run python -m pytest tests/ --cov=core --cov=db --cov-report=term-missing
```

I test coprono: `parse_amount` (Decimal, formati EU/US), `parse_date_safe`, `normalize_description`, `compute_transaction_id` (SHA-256), `compute_file_hash`, `detect_delimiter`, `BackendFactory`, validazione `ProcessingConfig`, `TaxonomyConfig`, cascata di categorizzazione.

---

## Decisioni di design

### `Decimal` — mai `float`

Tutti gli importi sono `decimal.Decimal`. I float IEEE 754 introducono errori di arrotondamento che falsano saldi e riconciliazioni.

### Idempotenza SHA-256

Ogni transazione ha un `id` di 24 caratteri (SHA-256 troncato) calcolato deterministicamente da `(source_file_hash, date, amount, description)`. Re-importare lo stesso file non genera duplicati: `upsert_transaction` salta le righe con id già presente.

### PII sanitization come precondizione

`assert_sanitized()` è chiamata in `call_with_fallback()` prima di qualsiasi richiesta a backend remoto. Se il testo contiene pattern IBAN/PAN/CF rilevabili, la chiamata viene rifiutata — non degradata silenziosamente.

### Circuit breaker e quarantena

`call_with_fallback(primary, ...)` prova il backend primario, poi Ollama locale come fallback. Se entrambi falliscono, la transazione riceve `to_review=True` e viene messa in coda di revisione manuale senza bloccare il resto del batch.

### Nessun LangChain

I backend LLM usano direttamente `openai` SDK, `anthropic` SDK e `requests` (per Ollama). Nessuna dipendenza da framework di orchestrazione LLM — meno surface di attacco, aggiornamenti SDK indipendenti.

### RF-03: algoritmo a 3 fasi

La riconciliazione carta–conto corrente usa: (1) finestra temporale ±45 giorni, (2) sliding window contigua (gap ≤ 5 giorni, O(n²)), (3) subset sum al boundary (k=10 tx, ~10⁶ operazioni). Le transazioni riconciliate vengono escluse dal saldo netto per evitare double-counting.

---

## Dipendenze principali

| Pacchetto | Versione | Scopo |
|---|---|---|
| `streamlit` | ≥ 1.35 | UI |
| `pandas` | ≥ 2.2 | Elaborazione dati |
| `sqlalchemy` | ≥ 2.0 | ORM / persistenza |
| `pydantic` | ≥ 2.0 | Validazione schemi |
| `openai` | ≥ 1.30 | Backend OpenAI |
| `anthropic` | ≥ 0.28 | Backend Claude |
| `requests` | ≥ 2.31 | Backend Ollama |
| `chardet` | ≥ 5.0 | Encoding detection |
| `plotly` | ≥ 5.20 | Grafici |
| `jinja2` | ≥ 3.1 | Template report HTML |
| `pyyaml` | ≥ 6.0 | Parsing taxonomy.yaml |
| `alembic` | ≥ 1.13 | Migrazioni DB |
| `pytest` | ≥ 8.0 | Test |

---

*Tutti i dati sono salvati localmente nel database SQLite (`ledger.db`). Nessuna informazione finanziaria viene trasmessa a servizi esterni salvo esplicita configurazione del backend remoto e sanitizzazione PII obbligatoria.*
