# Spendify v2.4

[![CI](https://github.com/drake69/spendify/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/drake69/spendify/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/drake69/spendify/graph/badge.svg)](https://codecov.io/gh/drake69/spendify)
[![Python 3.13](https://img.shields.io/badge/python-3.13-blue?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![License: PolyForm NC](https://img.shields.io/badge/license-PolyForm%20Noncommercial-orange)](LICENSE)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Streamlit](https://img.shields.io/badge/UI-Streamlit-ff4b4b?logo=streamlit&logoColor=white)](https://streamlit.io)
[![Issues](https://img.shields.io/github/issues/drake69/spendify)](https://github.com/drake69/spendify/issues)
[![Last commit](https://img.shields.io/github/last-commit/drake69/spendify)](https://github.com/drake69/spendify/commits/main)

> 🇬🇧 [Read in English](README.md)

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
- [Motore delle regole](#motore-delle-regole)
- [Giroconti](#giroconti)
- [Test](#test)
- [Decisioni di design](#decisioni-di-design)

---

## Caratteristiche principali

| Funzionalità | Dettaglio |
|---|---|
| **Classificazione automatica** | Rileva tipo di documento (conto corrente, carta, prepagata, deposito) senza configurazione preventiva |
| **Normalizzazione deterministica** | Encoding detection, delimiter detection, header detection, importi in `Decimal` (mai `float`) |
| **Correzione segno carta** | Flag `invert_sign` in `DocumentSchema`: quando un file carta salva le spese come valori positivi, vengono negati automaticamente |
| **Idempotenza SHA-256** | Re-importare lo stesso file produce esattamente lo stesso insieme di righe |
| **Riconciliazione carta–c/c (RF-03)** | Algoritmo a 3 fasi che elimina il double-counting da addebiti aggregati mensili |
| **Rilevamento giroconti (RF-04)** | Matching simbolico importo+finestra temporale; esclusione o neutralizzazione configurabile |
| **Categorizzazione a cascata (RF-05)** | Regole utente → regex statiche → LLM strutturato → fallback "Altro" |
| **Motore regole con applicazione retroattiva** | Le regole deterministiche vengono applicate a tutte le transazioni esistenti al momento del salvataggio, non solo alle future importazioni |
| **Sottocategoria come fonte di verità** | La sottocategoria è la chiave primaria: se LLM o regola assegna una sottocategoria presente in tassonomia, la categoria genitore viene risolta automaticamente |
| **Tassonomia a 2 livelli nel DB** | 15 categorie di spesa + 7 di entrata; gestita dalla pagina Tassonomia (DB-backed, nessun restart richiesto) |
| **Backend LLM multi-provider** | Ollama (locale, default), OpenAI, Claude — interfaccia astratta comune, nessun LangChain |
| **Config LLM nell'UI** | Backend, modello e chiavi API configurabili dalla pagina Impostazioni senza toccare `.env` |
| **PII sanitization (RF-10)** | IBAN, PAN, CF, nomi del titolare redatti prima di qualsiasi chiamata remota |
| **Circuit breaker** | Fallback automatico su Ollama locale; quarantena (`to_review=True`) se tutti i backend falliscono |
| **Contesti di vita** | Dimensione ortogonale configurabile dall'utente (es. Quotidianità / Lavoro / Vacanza) assegnabile a ogni transazione; suggerimenti automatici basati su similarità Jaccard con transazioni precedenti |
| **Re-run LLM su fallimenti** | Pulsante nella pagina Review che rielabora solo le transazioni in cui l'LLM aveva fallito (`description == raw_description`) |
| **Rilevamento giroconti cross-account** | Pulsante nella pagina Review che riesegue `detect_internal_transfers` globalmente su tutte le transazioni, intercettando le coppie non trovate in fase di import |
| **Permutazioni nome titolare** | Tutte le permutazioni dei token del nome del titolare vengono verificate per il rilevamento giroconti, evitando i falsi negativi quando l'ordine varia tra i file |
| **Persistenza SQLAlchemy** | 10 tabelle ORM; CRUD idempotente; migrazioni automatiche all'avvio |
| **Progresso import cross-session** | Stato del job di importazione salvato nel DB; tutte le sessioni browser vedono il progresso in tempo reale |
| **Export report** | HTML standalone (Plotly), CSV, XLSX |
| **UI Streamlit 9 pagine** | Import → Ledger → Modifiche massive → Analytics → Review → Regole → Tassonomia → Impostazioni → Check List |
| **Check List mensile** | Tabella pivot mese × conto con conteggio transazioni; evidenzia i mesi mancanti a colpo d'occhio |

---

## Architettura

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
 già noto              (campione sanitizzato)    invert_sign detection
        │
 normalizer.py          sanitizer.py      llm_backends.py
 ├─ encoding detect     ├─ IBAN/PAN/CF    ├─ OllamaBackend
 ├─ parse_amount()      ├─ owner names    ├─ OpenAIBackend
 ├─ SHA-256 tx_id       └─ assert_sani.. └─ ClaudeBackend
 ├─ invert_sign                              BackendFactory
 ├─ RF-03 reconcile                          call_with_fallback()
 └─ RF-04 transfers
        │
 categorizer.py  ←── TaxonomyConfig (caricato dal DB)
 Step 0: regole utente  (risoluzione sottocategoria → categoria)
 Step 1: regex statiche
 Step 2: stub ML
 Step 3: LLM structured output  (enum sottocategorie vincolato)
 Step 4: fallback "Altro"
        │
    db/repository.py   (SQLAlchemy, idempotente)
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
| **Attivazione** | `DocumentSchema` già in DB per quel fingerprint colonne | Prima importazione di un nuovo formato |
| **Schema** | Recuperato da DB, applicato direttamente | LLM inferisce lo schema da un campione anonimizzato |
| **Promozione** | — | Il template Flow 2 approvato viene salvato e diventa Flow 1 |
| **Costo LLM** | Zero (solo categorizzazione) | Una chiamata per classificazione + una per categorizzazione batch |

---

## Struttura del progetto

```
spendify/
├── app.py                  # Entry point Streamlit (9 pagine)
├── taxonomy.yaml           # Seed iniziale tassonomia (importato nel DB al primo avvio)
├── .env.example            # Template variabili d'ambiente
├── pyproject.toml          # Dipendenze (uv / pip)
│
├── core/
│   ├── models.py           # Enum: DocumentType, TransactionType, GirocontoMode …
│   ├── schemas.py          # DocumentSchema (Pydantic) + invert_sign + llm_json_schema()
│   ├── llm_backends.py     # LLMBackend ABC · Ollama · OpenAI · Claude · BackendFactory
│   ├── sanitizer.py        # PII redaction (RF-10)
│   ├── normalizer.py       # Encoding, parse_amount (Decimal), SHA-256, RF-03, RF-04
│   ├── classifier.py       # Flow 2: inferenza DocumentSchema via LLM
│   ├── categorizer.py      # Cascata 4-step + TaxonomyConfig (find_category_for_subcategory)
│   └── orchestrator.py     # Pipeline principale: ProcessingConfig · process_file()
│
├── db/
│   ├── models.py           # ORM SQLAlchemy (9 tabelle) + migrazioni automatiche
│   └── repository.py       # CRUD idempotente · persist_import_result() · CRUD tassonomia
│                           #   bulk_set_giroconto_by_description()
│                           #   get_transactions_by_rule_pattern()
│
├── reports/
│   ├── generator.py        # HTML (Jinja2+Plotly) · CSV · XLSX
│   └── template_report.html.j2
│
├── ui/
│   ├── sidebar.py          # Pulsanti navigazione (9 pagine) + modalità giroconto
│   ├── upload_page.py      # Import multi-file + progress bar cross-session
│   ├── registry_page.py    # Ledger filtrabile + selezione al click + bulk giroconto
│   ├── analysis_page.py    # 7 grafici Plotly: barre mensili, saldo cumulativo,
│   │                       #   pie+treemap spese, drill-down categoria, pie+treemap entrate,
│   │                       #   top-10 descrizioni, stacked per conto + export HTML
│   ├── review_page.py      # Correzione categoria + toggle giroconto + salvataggio regola
│   ├── bulk_edit_page.py   # Operazioni massive: categoria/contesto/giroconto + eliminazione da filtro
│   ├── rules_page.py       # CRUD completo regole + "Esegui tutte le regole" bulk re-categorizzazione
│   ├── taxonomy_page.py    # CRUD DB-backed per categorie e sottocategorie
│   ├── settings_page.py    # Locale (formato data/importo), lingua, config backend LLM
│   └── checklist_page.py   # Pivot mese × conto: checklist presenza transazioni
│
├── prompts/
│   ├── classifier.json     # Prompt Flow 2 (hint invert_sign per file carta)
│   └── categorizer.json    # Prompt categorizzazione transazioni
│
├── tests/
│   ├── test_normalizer.py          # Test deterministici (parse_amount, SHA-256 …)
│   ├── test_backends.py            # Factory backend, validazione, mock Ollama
│   ├── test_categorizer.py         # Regole statiche, cascata, risoluzione tassonomia
│   └── test_repository_rules.py    # Upsert regole, pattern matching, toggle giroconto, bulk ops
│
└── support/
    ├── formatting.py       # format_amount_display, format_date_display, format_raw_amount_display
    └── logging.py
```

---

## Installazione

### ⚡ Installazione rapida (Docker — niente git clone)

L'unico prerequisito è **[Docker Desktop](https://www.docker.com/products/docker-desktop/)** installato e avviato.

**Mac / Linux:**
```bash
curl -fsSL https://raw.githubusercontent.com/drake69/spendify/main/installer/install.sh | bash
```

**Windows (PowerShell):**
```powershell
irm https://raw.githubusercontent.com/drake69/spendify/main/installer/install.ps1 | iex
```

Lo script scarica l'immagine pre-compilata da GitHub Container Registry, avvia il container e apre il browser su **http://localhost:8501** automaticamente.

> **AI locale opzionale:** l'installer chiede se aggiungere Ollama + `gemma3:12b` (scaricato automaticamente, ~8 GB). Compatibile con Apple Silicon (arm64) e amd64.

> **Aggiornamento all'ultima versione:**
> ```bash
> docker compose --project-directory ~/spendify pull && docker compose --project-directory ~/spendify up -d
> ```

> **Disinstallazione:** `curl -fsSL https://raw.githubusercontent.com/drake69/spendify/main/installer/uninstall.sh | bash`

---

### Installazione developer (nativa, consigliata su Mac)

> Setup completo, convenzioni di codice, sistema di priorità e flusso PR → **[CONTRIBUTING.md](CONTRIBUTING.md)**

### Prerequisiti

- **Python 3.13+**
- **[uv](https://github.com/astral-sh/uv)** (gestore pacchetti consigliato)
- **[Ollama](https://ollama.com)** per il backend LLM locale (default)

### 1. Clona il repository

```bash
git clone https://github.com/drake69/spendify.git
cd spendify
```

### 2. Installa le dipendenze

```bash
uv sync
```

### 3. Configura le variabili d'ambiente

```bash
cp .env.example .env
# Nessuna modifica necessaria per un'installazione locale standard — percorsi già impostati
```

### 4. Scarica il modello LLM locale (opzionale)

```bash
ollama pull gemma3:12b   # ~8 GB — salta se hai intenzione di usare OpenAI/Anthropic
```

> Mantieni Ollama in esecuzione (`ollama serve`) durante l'uso dell'app. Backend LLM, modello e API key si configurano dalla pagina **⚙️ Impostazioni** — non nel `.env`.

---

## Configurazione

Il file `.env` contiene solo due parametri infrastrutturali — tutto il resto (backend LLM, API key, modello, nomi titolare, formato date, lingua…) si configura dalla pagina **⚙️ Impostazioni** e viene salvato nel DB:

```dotenv
# URI del database — lascia il default per uso locale; sovrascritto da docker-compose per Docker
SPENDIFY_DB=sqlite:///ledger.db

# Percorso del file YAML delle categorie (seed importato nel DB al primo avvio)
TAXONOMY_PATH=taxonomy.yaml

# Solo se si usa docker compose --profile llama-cpp:
# LLAMA_MODEL=gemma-3-4b-it-Q4_K_M.gguf
```

> **Nient'altro appartiene al `.env`.** Backend LLM, URL Ollama, nome modello, API key OpenAI/Anthropic e nomi titolare per la redazione PII sono tutti salvati nella tabella `user_settings` e modificabili in tempo reale dall'UI senza riavviare l'app.

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

L'app si apre su `http://localhost:8501` con 9 pagine:

| Pagina | Descrizione |
|---|---|
| **📥 Import** | Carica uno o più file (CSV / XLSX). Progresso live visibile da tutte le sessioni browser. Riepilogo: transazioni, riconciliazioni, transfer link, flow usato (1/2). |
| **📋 Ledger** | Tabella filtrabile per data, tipo, descrizione, categoria, contesto, flag revisione. Click su una riga per selezionarla istantaneamente. Colonne Entrata/Uscita separate e allineate a destra. Filtro contesto + pannello assegnazione con suggerimenti Jaccard. Toggle giroconto con bulk-apply. Download CSV/XLSX. |
| **✏️ Modifiche massive** | Operazioni in blocco su transazione di riferimento: toggle giroconto, assegnazione contesto (con similarità Jaccard), correzione categoria + salvataggio regola. Eliminazione massiva tramite filtri combinati (data, conto, tipo, descrizione, categoria) con anteprima e conferma `ELIMINA` obbligatoria. |
| **📊 Analytics** | 7 grafici Plotly interattivi: barre mensili entrate/uscite, saldo cumulativo, pie+treemap spese per categoria, drill-down interattivo categoria→sottocategoria con trend mensile, pie+treemap entrate, top-10 descrizioni, stacked per conto. Export HTML. |
| **🔍 Review** | Transazioni con `to_review=True`. Toggle giroconto (con bulk-apply). Correzione categoria/sottocategoria + salvataggio opzionale come regola permanente applicata immediatamente. Pulsante "Re-run LLM" per transazioni non pulite. Pulsante "Riesegui giroconti cross-account". |
| **📏 Regole** | CRUD completo regole di categorizzazione. Modifica/elimina regole + ricalcolo bulk delle transazioni già categorizzate. Pulsante "▶️ Esegui tutte le regole" applica tutte le regole a ogni transazione del ledger in un colpo. |
| **🗂️ Tassonomia** | CRUD DB-backed per categorie e sottocategorie (spese e entrate). Le modifiche hanno effetto immediato senza restart. |
| **⚙️ Impostazioni** | Formato data, separatori importo, lingua descrizioni, contesti di vita, lista conti bancari, backend LLM (modello + chiavi API). Tutto persistito nel DB. |
| **✅ Check List** | Tabella pivot mese × conto. Mese corrente in cima, ordine decrescente. Celle: numero tx o **—** se assenti. Colorazione per volume. Filtri: selezione conti, ultimi N mesi, nascondi mesi vuoti. Export CSV. |

---

## Tassonomia

La tassonomia è memorizzata nel database (tabelle `taxonomy_category` / `taxonomy_subcategory`) e gestita dalla pagina **🗂️ Tassonomia**. Al primo avvio il DB viene popolato da `taxonomy.yaml`.

**Categorie di spesa (15):** Casa · Alimentari · Ristorazione · Trasporti · Salute · Istruzione · Abbigliamento · Comunicazioni · Svago e tempo libero · Animali domestici · Finanza e assicurazioni · Cura personale · Tasse e tributi · Regali e donazioni · Altro

**Categorie di entrata (7):** Lavoro dipendente · Lavoro autonomo · Rendite finanziarie · Rendite immobiliari · Trasferimenti e rimborsi · Prestazioni sociali · Altro entrate

**La sottocategoria è la fonte di verità:** se LLM o una regola assegnano una sottocategoria presente in tassonomia, la categoria genitore corretta viene risolta automaticamente — i due livelli sono sempre consistenti nel DB.

---

## Motore delle regole

Le regole di categorizzazione sono memorizzate nella tabella `category_rule` e applicate in più punti del ciclo di vita.

### Tipi di matching

| Tipo | Comportamento |
|---|---|
| `contains` | Il pattern appare ovunque nella descrizione (case-insensitive) |
| `exact` | La descrizione corrisponde esattamente al pattern (case-insensitive) |
| `regex` | Regex Python completa confrontata con la descrizione |

`get_transactions_by_rule_pattern` ricerca **tutte** le transazioni indipendentemente da come erano state categorizzate (LLM, regola o correzione manuale). Salvare una nuova regola corregge correttamente anche le transazioni già categorizzate dall'LLM.

### Priorità

Quando più regole corrispondono alla stessa transazione vince quella con il valore di `priority` più alto. La priorità di default è 10; è possibile assegnare qualsiasi intero.

### Semantica upsert

Creare una regola con la stessa coppia `(pattern, match_type)` di una regola esistente la **aggiorna** sul posto (categoria, sottocategoria, priorità) anziché creare un duplicato.

### Applicazione retroattiva

Salvare una regola dalle pagine **Ledger** o **Review** la applica immediatamente a tutte le transazioni esistenti che corrispondono al pattern, non solo alle future importazioni. Il messaggio di conferma indica quante transazioni sono state aggiornate. Lo stesso comportamento è disponibile dalla pagina **Regole** tramite l'opzione di ricalcolo bulk su singola regola.

Inoltre, il pulsante **▶️ Esegui tutte le regole** nella pagina **Regole** applica tutte le regole a ogni transazione del ledger in un colpo solo (non limitato a `to_review=True`). Utile dopo aver creato più regole contemporaneamente o dopo aver importato dati storici.

---

## Giroconti

Un *giroconto* è un movimento interno tra conti di propria titolarità (es. bonifico da conto corrente a conto deposito, ricarica di una prepagata). Includere entrambi i lati nel saldo causerebbe double-counting.

### Tipi di transazione

| `tx_type` | Significato |
|---|---|
| `internal_out` | Lato uscente del giroconto (importo negativo) |
| `internal_in` | Lato entrante del giroconto (importo positivo) |

Entrambi i tipi sono esclusi dal saldo netto, dalle entrate e dalle uscite.

### Rilevamento automatico (RF-04)

La pipeline tenta di abbinare i giroconti automaticamente durante l'importazione con tre passaggi:

1. **Regex keyword** — la descrizione corrisponde a un pattern configurato (es. "Giroconto", "Bonifico tra i miei conti") → alta confidenza
2. **Matching importo + data** — stesso importo assoluto entro ±3 giorni, su `account_label` diversi → confidenza media/alta
3. **Permutazioni nome titolare** — la descrizione contiene qualsiasi permutazione dei token del nome del titolare → alta confidenza (intercetta sia "Corsaro Luigi Gerotti Elena" che "Luigi Corsaro Elena Gerotti")

### Riesecuzione cross-account

Quando le due transazioni di un giroconto appartengono a file importati in momenti diversi, il primo import non può trovare la coppia. Usa il pulsante **"🔁 Riesegui rilevamento giroconti"** nella pagina **🔍 Review** per rieseguire il rilevamento globalmente su tutte le transazioni non-giroconto.

### Toggle manuale

Dalle pagine **Ledger** o **Review** è possibile contrassegnare manualmente qualsiasi transazione come giroconto (o ripristinarla):

- **Toggle singolo** — cambia il `tx_type` della transazione selezionata (`expense` ↔ `internal_out`, `income` ↔ `internal_in`).
- **Bulk apply** — se altre transazioni condividono la stessa descrizione, una checkbox (default: abilitata) consente di applicare la stessa modifica a tutte con un solo click. Il numero di transazioni coinvolte è visibile prima di confermare.

`bulk_set_giroconto_by_description` in `db/repository.py` implementa l'operazione bulk: aggiorna tutte le transazioni con la descrizione indicata eccetto quella già modificata, e restituisce il numero di righe cambiate.

---

## Contesti di vita

I contesti di vita sono una dimensione di classificazione ortogonale alla tassonomia delle categorie. Mentre la categoria risponde *cosa è stato acquistato*, il contesto risponde *per quale area della vita*.

### Design

| Aspetto | Dettaglio |
|---|---|
| **Storage** | Colonna `context VARCHAR(64)` nullable sulla tabella `Transaction` |
| **Ortogonalità** | Indipendente da categoria/sottocategoria — qualsiasi combinazione è valida |
| **Configurabile** | Aggiunta, rinomina e rimozione contesti dalla pagina **⚙️ Impostazioni** (salvati come JSON in `user_settings`) |
| **Contesti default** | Quotidianità · Lavoro · Vacanza |

### Assegnazione

Dalla pagina **📋 Ledger**, seleziona una transazione e apri il pannello espandibile "🌍 Assegna contesto":

1. Scegli un contesto dal menu a discesa (o cancella quello esistente)
2. Attiva opzionalmente **"Applica anche a transazioni simili"** — la similarità Jaccard a livello di token (soglia 0.35) trova transazioni con descrizione semanticamente vicina e pre-assegna lo stesso contesto
3. Clicca **Applica**

### Filtro

La barra filtri del registro include un selettore contesto: *tutti*, i singoli valori configurati, o *— nessuno —* (transazioni senza contesto assegnato).

---

## Test

```bash
# Tutti i test (nessun mock LLM richiesto)
uv run python -m pytest tests/ -v

# Con coverage
uv run python -m pytest tests/ --cov=core --cov=db --cov-report=term-missing
```

### File di test

| File | Copertura |
|---|---|
| `test_normalizer.py` | `parse_amount`, dedup SHA-256, encoding detection |
| `test_backends.py` | Factory backend, validazione, mock Ollama |
| `test_categorizer.py` | Regole statiche, cascata 4-step, risoluzione tassonomia |
| `test_repository_rules.py` | Upsert regole, `get_transactions_by_rule_pattern` (tutti i tipi + regressione LLM-sourced), `apply_rules_to_review_transactions`, `toggle_transaction_giroconto`, `bulk_set_giroconto_by_description` |

Tutti i test usano un database SQLite in-memory — nessun I/O su file, nessun servizio esterno richiesto.

---

## Decisioni di design

### `Decimal` — mai `float`

Tutti gli importi sono `decimal.Decimal`. I float IEEE 754 introducono errori di arrotondamento che falsano saldi e riconciliazioni.

### Idempotenza SHA-256

Ogni transazione ha un `id` di 24 caratteri (SHA-256 troncato) calcolato deterministicamente da `(source_file, date, amount, description)`. Re-importare lo stesso file non genera duplicati.

### Correzione segno carta (`invert_sign`)

Gli estratti conto italiani per carte di credito/debito esportano spesso gli acquisti come valori positivi. Il flag `DocumentSchema.invert_sign`, impostato dall'LLM durante la classificazione Flow 2, istruisce il normalizzatore a negare tutti gli importi — le spese diventano negative e i rimborsi positivi con un'unica operazione simmetrica.

#### Algoritmo di rilevamento in due passi

Il classificatore decide il valore di `invert_sign` con un algoritmo in due passi. **Lo Step 0 ha la priorità massima: se si attiva, lo Step 1 viene saltato completamente.** Lo Step 1 è consultato solo quando lo Step 0 non riesce a dare una risposta definitiva.

**Step 0 — Sinonimi del nome colonna (priorità massima)**

Il nome della colonna importo viene confrontato con tre gruppi di sinonimi:

| Gruppo | Esempi di nomi | Decisione |
|---|---|---|
| **Sinonimi di uscita** | Uscita, Uscite, Addebito, Addebiti, Pagamento, Spesa, Dare, Importo addebitato | `invert_sign = true` (spese salvate come positivi → negarle) |
| **Sinonimi di entrata** | Entrata, Entrate, Accredito, Accrediti, Avere, Credito, Importo accreditato | `invert_sign = false` (entrate già positive → nessuna modifica) |
| **Nomi neutri** | Importo, Amount, Valore, Totale | Nessuna decisione — si procede allo Step 1 |

Il matching è case-insensitive e parziale (es. "Addebiti carta" corrisponde a "Addebito"). La regola dei sinonimi di uscita si applica solo ai doc_type carta; conti correnti e depositi mantengono sempre `invert_sign = false` indipendentemente dal nome della colonna.

**Step 1 — Analisi della distribuzione dei segni (solo nomi neutri)**

Quando lo Step 0 trova un nome neutro e non può classificare per nome, il classificatore conta i valori positivi e negativi nel campione e calcola `positive_ratio` e `negative_ratio`:

- File carta, maggioranza positivi (> 60 %): le spese sono salvate come positivi (convenzione AMEX / tipici export italiani) → `invert_sign = true`
- File carta, maggioranza negativi (> 60 %): le spese hanno già il segno corretto → `invert_sign = false`
- Split circa 50/50: si analizzano le descrizioni (nomi di esercenti con importi positivi → `invert_sign = true`; "bonifico ricevuto" con importo positivo → `invert_sign = false`)
- Conto corrente / deposito: sempre `invert_sign = false`, indipendentemente dalla distribuzione

#### Campi diagnostici

Ogni `DocumentSchema` prodotto dal Flow 2 include quattro campi diagnostici per audit e debug:

| Campo | Tipo | Contenuto |
|---|---|---|
| `positive_ratio` | `float \| null` | Frazione di valori > 0 nella colonna importo nel campione |
| `negative_ratio` | `float \| null` | Frazione di valori < 0 nella colonna importo nel campione |
| `semantic_evidence` | `list[str]` | 2–4 frasi brevi dell'LLM che spiegano la decisione |
| `normalization_case_id` | `str \| null` | C1 = conto corrente signed_single · C2 = carta invertita · C3 = carta già negativa · C4 = colonne Dare/Avere · C5 = ambiguo |

Questi campi sono persistiti nella tabella DB `document_schema` e visibili nel riepilogo dello schema Flow 2 nell'UI.

### Sottocategoria come chiave primaria

Il categorizzatore tratta la sottocategoria come autoritativa. `TaxonomyConfig.find_category_for_subcategory()` risolve la categoria genitore da qualsiasi nome di sottocategoria valido. LLM e regole possono specificare il livello più granulare e la gerarchia è sempre consistente nel DB.

### Tassonomia nel DB

La tassonomia a 2 livelli (categorie + sottocategorie) risiede in due tabelle DB (`taxonomy_category`, `taxonomy_subcategory`). Viene popolata da `taxonomy.yaml` al primo avvio e poi gestita interamente dall'UI — nessuna modifica di file o restart richiesto.

### PII sanitization come precondizione

`assert_sanitized()` è chiamata in `call_with_fallback()` prima di qualsiasi richiesta a backend remoto. Se il testo contiene pattern IBAN/PAN/CF rilevabili, la chiamata viene rifiutata — non degradata silenziosamente.

### Circuit breaker e quarantena

`call_with_fallback(primary, ...)` prova il backend primario, poi Ollama locale come fallback. Se entrambi falliscono, la transazione riceve `to_review=True` e viene messa in coda senza bloccare il resto del batch.

### Nessun LangChain

I backend LLM usano direttamente `openai` SDK, `anthropic` SDK e `requests` (per Ollama). Nessuna dipendenza da framework di orchestrazione LLM.

### RF-03: algoritmo a 3 fasi

La riconciliazione carta–conto corrente usa: (1) finestra temporale ±45 giorni, (2) sliding window contigua (gap ≤ 5 giorni, O(n²)), (3) subset sum al boundary (k=10 tx, ~10⁶ operazioni).

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
| `pyyaml` | ≥ 6.0 | Parsing seed taxonomy.yaml |
| `pytest` | ≥ 8.0 | Test |

---

*Tutti i dati sono salvati localmente nel database SQLite (`ledger.db`). Nessuna informazione finanziaria viene trasmessa a servizi esterni salvo esplicita configurazione del backend remoto e sanitizzazione PII obbligatoria.*
