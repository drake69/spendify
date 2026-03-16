# Spendify — Documento di Progetto

> Versione: 2.4 — aggiornato 2026-03-14

---

## 1. Obiettivo

Spendify è un registro finanziario personale che aggrega estratti conto eterogenei (CSV / XLSX da banche italiane) in un unico ledger cronologico. Il sistema elimina automaticamente il double-counting causato da:

- Addebiti periodici carta sul conto corrente (riconciliazione RF-03)
- Giroconti interni tra conti di propria titolarità (RF-04)

Il processing è **offline-first**: il backend LLM di default è Ollama locale; OpenAI e Claude sono supportati come opt-in con sanitizzazione PII obbligatoria.

---

## 2. Stack tecnologico

| Layer | Tecnologia |
|---|---|
| UI | Streamlit ≥ 1.45 |
| Pipeline | Python 3.13, pandas 2.x |
| ORM / DB | SQLAlchemy 2.x + SQLite |
| Validazione schema | Pydantic v2 |
| LLM (locale) | Ollama + gemma3:12b (default) |
| LLM (remoti) | OpenAI SDK, Anthropic SDK |
| Grafici | Plotly |
| Export HTML | Jinja2 |
| Test | pytest, SQLite in-memory |
| Package manager | uv |

---

## 3. Architettura del sistema

### 3.1 Pipeline di importazione

```
File CSV/XLSX
     │
     ▼
core/classifier.py   ──── Flow 1: schema già in DB (fingerprint SHA-256 colonne)
     │                    Flow 2: LLM inferisce schema da campione sanitizzato
     ▼
core/normalizer.py
  ├─ Encoding detection (chardet)
  ├─ Delimiter / header detection
  ├─ parse_amount() → Decimal (mai float)
  ├─ SHA-256 tx_id (dedup idempotente)
  ├─ invert_sign (correzione segno carte)
  ├─ RF-03 riconciliazione carta–c/c
  └─ RF-04 rilevamento giroconti
     │
core/description_cleaner.py
  └─ LLM: rimozione rumore, standardizzazione testo
     │
core/categorizer.py
  ├─ Step 0: regole utente
  ├─ Step 1: regex statiche
  ├─ Step 2: stub ML (futuro)
  ├─ Step 3: LLM structured output (enum sottocategorie vincolato)
  └─ Step 4: fallback "Altro"
     │
db/repository.py → persist_import_result()
```

### 3.2 Identificazione schema (Flow 1 vs Flow 2)

Ogni file importato viene "firmato" da un SHA-256 delle intestazioni colonna normalizzate. Se la firma è già in `document_schema`, viene usato lo schema salvato (Flow 1, zero costo LLM). Altrimenti l'LLM inferisce lo schema (Flow 2) e il template approvato viene salvato per le importazioni successive.

### 3.3 Job di importazione

Il processing avviene in un thread background. Il progresso è salvato nel DB (`import_job` table) e polling ogni 2 secondi da qualsiasi sessione browser aperta. Questo permette di aprire un secondo browser e vedere il progresso di un'importazione avviata altrove.

**Punti di progresso significativi:**
- `0%` — inizio
- `15%` — schema identificato / approvato
- `25%` — normalizzazione completata
- `38%` — description cleaning completato
- `40%→100%` — categorizzazione batch (≈1 min per 20 transazioni con LLM locale)

---

## 4. Modello dati

### 4.1 Tabelle principali

| Tabella | Descrizione |
|---|---|
| `transaction` | Ogni riga del ledger. Chiave: `id` (SHA-256 24 char) |
| `import_batch` | Metadati di ogni importazione (file, schema, conteggi) |
| `document_schema` | Template schema per Flow 1 (fingerprint → configurazione) |
| `reconciliation_link` | Coppie carta–c/c riconciliate (RF-03) |
| `internal_transfer_link` | Coppie giroconto (RF-04) |
| `category_rule` | Regole deterministiche di categorizzazione |
| `user_settings` | Preferenze utente (key/value store) |
| `import_job` | Stato corrente del job di importazione |
| `taxonomy_category` | Categorie tassonomia (2 livelli) |
| `taxonomy_subcategory` | Sottocategorie tassonomia |

### 4.2 Transaction — colonne chiave

| Colonna | Tipo | Note |
|---|---|---|
| `id` | VARCHAR(24) PK | SHA-256 troncato di (source_file, date, amount, description) |
| `date` | VARCHAR(10) | ISO 8601: `YYYY-MM-DD` |
| `amount` | Numeric(18,4) | Sempre Decimal, mai float; negativo = spesa |
| `tx_type` | VARCHAR | `expense` / `income` / `internal_out` / `internal_in` |
| `description` | TEXT | Descrizione pulita dall'LLM |
| `raw_description` | TEXT | Descrizione originale dal file |
| `category` | VARCHAR | Categoria tassonomia |
| `subcategory` | VARCHAR | Sottocategoria tassonomia |
| `context` | VARCHAR(64) | Contesto di vita (nullable, ortogonale a categoria) |
| `account_label` | VARCHAR | Identificativo stabile del conto (da user_settings) |
| `to_review` | BOOLEAN | True se LLM fallito o ambiguo |
| `source_identifier` | VARCHAR | SHA-256 delle colonne (fingerprint schema) |

### 4.3 UserSettings — chiavi rilevanti

| Key | Default | Descrizione |
|---|---|---|
| `date_display_format` | `%d/%m/%Y` | Formato data nella UI |
| `amount_decimal_sep` | `,` | Separatore decimali |
| `amount_thousands_sep` | `.` | Separatore migliaia |
| `description_language` | `it` | Lingua usata nei prompt LLM |
| `giroconto_mode` | `neutral` | `neutral` o `exclude` |
| `llm_backend` | `local_ollama` | Backend LLM attivo |
| `ollama_base_url` | `http://localhost:11434` | URL server Ollama |
| `ollama_model` | `gemma3:12b` | Modello Ollama |
| `openai_api_key` | — | Chiave OpenAI |
| `openai_model` | `gpt-4o-mini` | Modello OpenAI |
| `anthropic_api_key` | — | Chiave Anthropic |
| `anthropic_model` | `claude-3-5-haiku-20241022` | Modello Claude |
| `owner_names` | — | Nomi titolari (CSV) per PII redaction e giroconto |
| `use_owner_names_giroconto` | `false` | Usa nomi titolari per rilevare giroconti |
| `contexts` | `["Quotidianità","Lavoro","Vacanza"]` | Contesti di vita (JSON array) |
| `import_test_mode` | `false` | Importa solo prime 20 righe |

---

## 5. Funzionalità principali

### 5.1 Rilevamento giroconti (RF-04)

Tre passaggi deterministici, tutti senza LLM:

**Passaggio 1 — Keyword regex**
La descrizione viene confrontata con pattern configurati per schema (`internal_transfer_patterns`). Match positivo → `tx_type = internal_out/in` con alta confidenza.

**Passaggio 2 — Matching importo + finestra temporale**
Tra transazioni con `account_label` diversi, si cerca una coppia con stesso importo assoluto entro ±3 giorni. Match positivo → link in `internal_transfer_link`.

**Passaggio 3 — Permutazioni nome titolare**
`_build_owner_name_regex()` in `core/sanitizer.py` costruisce una regex che intercetta tutte le permutazioni dei token dei nomi dei titolari. Questo evita falsi negativi quando l'ordine di cognome/nome varia tra file di banche diverse.

**Riesecuzione cross-account**
Disponibile dalla pagina Review tramite `_rerun_transfer_detection()` in `ui/review_page.py`. Carica tutte le transazioni non-giroconto, aggrega i pattern da tutti gli schema (`get_all_transfer_keyword_patterns`), e riesegue i tre passaggi aggiornando solo le righe in cui `tx_type` è cambiato.

### 5.2 Riconciliazione carta–conto (RF-03)

Algoritmo a 3 fasi per eliminare il double-counting degli addebiti periodici carta:

1. **Finestra temporale** ±45 giorni
2. **Sliding window contigua** (gap ≤ 5 giorni, O(n²))
3. **Subset sum al boundary** (k=10 transazioni, ≈10⁶ operazioni)

Le coppie riconciliate vengono registrate in `reconciliation_link`. Le transazioni carta riconciliate sono escluse dal saldo netto.

### 5.3 Categorizzazione a cascata (RF-05)

```
Transazione
    │
    ├─ Step 0: match su category_rule (priorità massima)
    │          subcategory → categoria genitore via TaxonomyConfig
    │
    ├─ Step 1: regex statiche in core/categorizer.py
    │
    ├─ Step 2: [stub ML — futuro]
    │
    ├─ Step 3: LLM con enum vincolato
    │          prompt: categorizer.json
    │          output: subcategory scelta tra enum valido
    │          TaxonomyConfig.find_category_for_subcategory() risolve la categoria
    │
    └─ Step 4: fallback → "Altro" / "Altro entrate"
                          to_review = True
```

### 5.4 Contesti di vita

Dimensione ortogonale alla tassonomia. Ogni transazione può avere al più un contesto (`context VARCHAR(64)`). Configurabili dall'utente (add/rename/delete) dalla pagina Impostazioni.

**Assegnazione**: dalla pagina Ledger, pannello "🌍 Assegna contesto":
- Selezione manuale dal menu a discesa
- Opzione "Applica anche a transazioni simili": `get_similar_transactions()` in `db/repository.py` usa Jaccard token similarity (threshold 0.35) per trovare transazioni con descrizione simile

**Filtro**: il registro può essere filtrato per contesto specifico, "tutti", o "nessuno" (NULL).

### 5.5 Pulizia descrizioni

`core/description_cleaner.py` chiama l'LLM per rimuovere rumore (codici interni banca, ID operazione, IBAN parziali) e standardizzare il testo. Il risultato viene salvato in `description`; l'originale rimane in `raw_description`.

Se l'LLM fallisce, `description` rimane uguale a `raw_description`. Il pulsante "🔄 Rielabora con LLM" nella pagina Review usa questa condizione come filtro per identificare le transazioni da rielaborare.

### 5.6 PII sanitization (RF-10)

Prima di qualsiasi chiamata a backend remoto:

| Pattern | Sostituzione |
|---|---|
| IBAN | `<ACCOUNT_ID>` |
| PAN (carta) | `<CARD_ID>` |
| Codice fiscale | `<FISCAL_ID>` |
| Nomi titolari | `<OWNER>` |

`assert_sanitized()` verifica l'assenza di pattern rilevabili e blocca la chiamata se trovati.

---

## 6. Migrazioni DB

Le migrazioni sono idempotenti e vengono eseguite automaticamente all'avvio in `db/models.py → create_tables()`:

| Funzione | Aggiunta |
|---|---|
| `_migrate_add_user_settings()` | Tabella `user_settings` (key/value store) |
| `_migrate_add_import_job()` | Tabella `import_job` |
| `_migrate_add_raw_description()` | Colonna `raw_description` su `transaction` |
| `_migrate_add_account_label()` | Colonna `account_label` su `transaction` |
| `_migrate_add_context()` | Colonna `context` su `transaction` |

---

## 7. Interfaccia utente

### 7.1 Navigazione

9 pagine Streamlit gestite da `app.py` + `ui/sidebar.py`:

```
📥 Import              upload_page.py
📋 Ledger              registry_page.py
✏️ Modifiche massive   bulk_edit_page.py
📊 Analytics           analysis_page.py
🔍 Review              review_page.py
📏 Regole              rules_page.py
🗂️ Tassonomia          taxonomy_page.py
⚙️ Impostazioni        settings_page.py
✅ Check List          checklist_page.py
```

### 7.2 Pagina Import

- Upload multi-file (CSV / XLSX)
- Progress bar live (polling DB ogni 2s)
- Visibile da qualsiasi browser che ha l'app aperta
- Riepilogo al termine: transazioni importate, riconciliate, giroconti trovati, flow usato

### 7.3 Pagina Modifiche massive

- Operazioni in blocco su transazione di riferimento: toggle giroconto, assegnazione contesto (similarità Jaccard ≥ 35%), correzione categoria/sottocategoria + salvataggio regola
- Eliminazione massiva da filtro: filtri combinabili (data, conto, tipo, descrizione, categoria); almeno un filtro obbligatorio; anteprima prime 10 righe; conferma con digitazione di `ELIMINA`; eliminazione irreversibile
- Cross-account duplicate detection: pivot table per identificare transazioni presenti su più conti

### 7.4 Pagina Ledger

- Filtri: date range, tipo transazione, descrizione (full-text su description + raw_description), categoria, contesto, flag revisione
- Click su una riga → selezione istantanea con dettagli a sidebar
- Pannello "🌍 Assegna contesto" con suggerimenti similarità
- Toggle giroconto (singolo + bulk per descrizione)
- Colonne Entrata/Uscita separate, allineate a destra
- Metriche: saldo netto, totale entrate, totale uscite
- Download CSV / XLSX del ledger filtrato

### 7.5 Pagina Review

- Solo transazioni con `to_review=True`
- Toggle giroconto + bulk-apply
- Correzione categoria/sottocategoria con salvataggio opzionale come regola
- **"🔄 Rielabora con LLM"**: riesegue cleaning + categorizzazione sulle transazioni non pulite
- **"🔁 Riesegui rilevamento giroconti"**: riesegue RF-04 globalmente

### 7.6 Pagina Impostazioni

- Formato data e separatori importo (con anteprima live)
- Lingua delle descrizioni (usata nei prompt LLM)
- Modalità giroconti (neutral / exclude)
- Nomi titolari + toggle uso per giroconto
- Contesti di vita (lista modificabile: add/rename/delete)
- Modalità test import (solo prime 20 righe)
- Lista conti bancari (add/delete, usata come `account_label` stabile per dedup)
- Backend LLM: Ollama / OpenAI / Claude + modello + chiavi API

### 7.7 Pagina Check List

- Tabella pivot **mese × conto** con il numero di transazioni per ogni combinazione
- Righe in ordine **decrescente**: mese corrente in cima, poi a ritroso
- Il mese corrente appare sempre (anche se non ha ancora transazioni)
- Colonne: tutti i conti definiti in `account` + eventuali `account_label` da transazioni non ancora formalizzati
- Cella vuota (0 tx): simbolo **—** in grigio chiaro; cella con tx: numero con colorazione proporzionale (azzurro tenue → scuro)
- Tre KPI in cima: transazioni totali, conti monitorati, mesi con dati
- Filtri: selezione conti, ultimi N mesi, nascondi mesi senza transazioni
- Download CSV della tabella filtrata

---

## 8. Regole di categorizzazione

### 8.1 Tipi di match

| Tipo | Comportamento |
|---|---|
| `contains` | Pattern ovunque nella descrizione (case-insensitive) |
| `exact` | Descrizione uguale al pattern (case-insensitive) |
| `regex` | Regex Python completa |

### 8.2 Semantica upsert

Stessa coppia `(pattern, match_type)` → aggiornamento in-place di categoria/priorità (nessun duplicato).

### 8.3 Applicazione retroattiva

Le regole vengono applicate a **tutte** le transazioni esistenti al salvataggio, non solo alle future importazioni. Il conteggio delle transazioni aggiornate è mostrato all'utente.

**Esegui tutte le regole (bulk):** il pulsante "▶️ Esegui tutte le regole" nella pagina Regole applica in un colpo solo tutte le regole attive a ogni transazione del ledger (non solo `to_review=True`). Utile dopo aver creato più regole in sessioni diverse o dopo aver importato dati storici senza LLM attivo.

---

## 9. Tassonomia

### 9.1 Storage

Due tabelle DB: `taxonomy_category` e `taxonomy_subcategory`. Seeding iniziale da `taxonomy.yaml`.

### 9.2 Sottocategoria come fonte di verità

`TaxonomyConfig.find_category_for_subcategory()` risolve la categoria genitore da qualsiasi sottocategoria valida. LLM e regole possono specificare solo la sottocategoria e la categoria viene risolta automaticamente.

### 9.3 Categorie di default

**Spese (15):** Casa · Alimentari · Ristorazione · Trasporti · Salute · Istruzione · Abbigliamento · Comunicazioni · Svago e tempo libero · Animali domestici · Finanza e assicurazioni · Cura personale · Tasse e tributi · Regali e donazioni · Altro

**Entrate (7):** Lavoro dipendente · Lavoro autonomo · Rendite finanziarie · Rendite immobiliari · Trasferimenti e rimborsi · Prestazioni sociali · Altro entrate

---

## 10. Privacy e sicurezza

- **Local-first**: Ollama è il backend default, nessun dato esce dal processo
- **Sanitizzazione obbligatoria**: `assert_sanitized()` blocca le chiamate remote se trova PII
- **Nomi titolari**: configurabili dalla UI, rimossi da tutte le descrizioni prima di chiamate LLM remote e usati per rilevare giroconti
- **Nessun LangChain**: SDK OpenAI, Anthropic e requests direttamente, superficie di attacco minima

---

## 11. Limitazioni note

- **Excel e locale numerico**: le celle numeriche Excel perdono il formato originale (es. `2,50` diventa `2.5`). Il campo `raw_amount` per file Excel mostrerà `"2.5"` — limitazione del formato Excel, non un bug.
- **Giroconti cross-account**: rilevati solo se entrambi i file sono già stati importati. Soluzione: pulsante "Riesegui rilevamento giroconti" nella pagina Review.
- **LLM asincrono**: la categorizzazione avviene in background. Con Ollama locale e gemma3:12b, ogni batch di 20 transazioni richiede circa 1 minuto.

---

## 12. Test

```bash
# Suite completa
uv run python -m pytest tests/ -v

# Con coverage
uv run python -m pytest tests/ --cov=core --cov=db --cov-report=term-missing
```

Tutti i test usano SQLite in-memory — nessun file, nessun servizio esterno.

| File | Copertura |
|---|---|
| `test_normalizer.py` | `parse_amount`, SHA-256, encoding |
| `test_backends.py` | Factory, validazione, mock Ollama |
| `test_categorizer.py` | Cascata 4-step, risoluzione tassonomia |
| `test_repository_rules.py` | Upsert regole, pattern matching, giroconto toggle, bulk ops |
