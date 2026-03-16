# Spendify — Reference Guide

---

## Pagine dell'applicazione

| Pagina | Scopo |
|---|---|
| **Import** | Carica file CSV/XLSX, avvia la pipeline di elaborazione |
| **Ledger** | Vista tabellare di tutte le transazioni importate |
| **Modifiche massive** | Operazioni in blocco: categoria, contesto, giroconto, **eliminazione da filtro** |
| **Analytics** | Grafici e report aggregati per periodo/conto/categoria |
| **Review** | Transazioni con classificazione incerta o da rivedere |
| **Regole** | Gestione regole deterministiche di categorizzazione |
| **Tassonomia** | Struttura categorie/sottocategorie personalizzabile |
| **Impostazioni** | Backend LLM, API key, formati data/importo, lingua |

---

## Formati di import supportati

| Formato | Note |
|---|---|
| CSV | Auto-detect encoding (UTF-8, latin-1, cp1252), delimiter (`,` `;` `\t`) |
| XLSX / XLS | Celle numeriche lette come float (il formato locale originale non è recuperabile) |

Banche riconosciute automaticamente tramite fingerprint degli header. Nessuna configurazione manuale richiesta.

---

## Pipeline di elaborazione (ordine di esecuzione)

```
File in input
    │
    ▼
1. Classificazione documento   → identifica banca, tipo conto, schema colonne
2. Normalizzazione             → encoding, delimitatori, parse date/importi, SHA-256
3. Dedup check                 → scarta transazioni già presenti (zero LLM call)
4. Pulizia descrizioni         → LLM estrae nome controparte, PII redatte prima/dopo
5. Rilevamento giroconti       → esclude/neutralizza trasferimenti interni
6. Riconciliazione carta-c/c   → elimina double-counting addebiti mensili aggregati
7. Categorizzazione a cascata  → regole utente → regex statiche → LLM → fallback "Altro"
8. Persistenza                 → upsert idempotente per tx, link, schema
```

---

## Categorizzazione a cascata

La categoria viene assegnata nell'ordine seguente; il primo match vince:

1. **Regole utente** — definite nella pagina Regole (esatta / contiene / regex)
2. **Regole statiche** — pattern hardcoded per casi comuni (bolli, F24, affitti standard)
3. **LLM** — il modello configurato in Impostazioni; riceve la descrizione sanitizzata
4. **Fallback** — categoria "Altro" se tutti i passi precedenti falliscono

La **sottocategoria è la fonte di verità**: se LLM o regola assegna una sottocategoria presente in tassonomia, la categoria genitore viene derivata automaticamente.

---

## Regole di categorizzazione

### Tipi di match

| Tipo | Comportamento | Esempio pattern |
|---|---|---|
| `exact` | La descrizione intera deve corrispondere (case-insensitive) | `NETFLIX.COM` |
| `contains` | Il pattern deve apparire nella descrizione (case-insensitive) | `ESSELUNGA` |
| `regex` | Espressione regolare Python | `RATA\s+\d+/\d+` |

### Applicazione retroattiva
Quando salvi una nuova regola, viene applicata immediatamente a **tutte** le transazioni già presenti nel database, non solo alle future importazioni.

### Esegui tutte le regole
Il pulsante **▶️ Esegui tutte le regole** applica tutte le regole attive a ogni transazione del ledger in un colpo solo. Utile dopo aver creato più regole in sessioni diverse o dopo l'importazione di dati storici. L'operazione richiede conferma tramite checkbox; al termine mostra quante transazioni sono state aggiornate.

### Priorità
Le regole vengono valutate in ordine di priorità decrescente (campo `priorità`, default 10). In caso di parità di priorità, l'ordine è stabile ma non garantito. La **prima regola che fa match vince** — l'elaborazione si ferma al primo match trovato.

---

## Riconciliazione carta-conto (RF-03)

Quando la banca addebita sul conto corrente il totale mensile della carta di credito, le singole spese della carta e l'addebito cumulativo sul conto sarebbero contate due volte. Spendify risolve questo automaticamente:

- Le transazioni della carta rimangono visibili nel Ledger
- L'addebito aggregato sul conto viene marcato come giroconto (🔄) ed escluso dai totali

Non richiede configurazione. Se vedi comunque un duplicato, controlla in Review.

---

## Giroconti interni (RF-04)

Un giroconto è un trasferimento tra due conti tuoi (es. "Bonifico a Conto Deposito"). Se contato su entrambi i lati falsifica il saldo.

**Come viene rilevato:** matching importo + finestra temporale (±3 giorni) tra conti diversi dello stesso titolare.

**Come viene marcato:** icona 🔄 nel Ledger, escluso dai totali di Analytics.

**Pulsante "Rileva giroconti cross-account"** in Review: riesegue la detection globalmente su tutte le transazioni. Utile se hai importato i due lati del giroconto in sessioni separate.

---

## Backend LLM

| Backend | Dove gira | Privacy | Configurazione |
|---|---|---|---|
| **Ollama** | Locale (default) | Totale — nessun dato lascia il tuo PC | Richiede Ollama installato e modello scaricato |
| **OpenAI** | Remoto | PII redatte prima dell'invio | API key in Impostazioni |
| **Claude** | Remoto | PII redatte prima dell'invio | API key in Impostazioni |

**Circuit breaker:** se il backend configurato non risponde, Spendify fa fallback automatico su Ollama locale. Se anche Ollama è offline, la transazione viene importata con `to_review=True` e descrizione grezza.

---

## PII Sanitization

Prima di qualsiasi chiamata a backend remoto, Spendify redige:

| Dato | Esempio originale | Dopo sanitizzazione |
|---|---|---|
| IBAN | `IT60X0542811101000000123456` | `<ACCOUNT_ID>` |
| Numero carta | `4111 1111 1111 1111` | `<CARD_ID>` |
| Codice fiscale | `RSSMRA80A01H501U` | `<FISCAL_ID>` |
| Nome titolare | `Mario Rossi` | Nome fittizio (es. `Carlo Brambilla`) |

La sanitizzazione avviene in memoria; il dato originale non viene mai modificato nel database.

---

## Tassonomia

Struttura a 2 livelli: **Categoria → Sottocategoria**.

- Modificabile dalla pagina Tassonomia senza riavviare l'app
- Categorie predefinite: Alimentari, Casa, Trasporti, Salute, Svago, Abbonamenti, Utenze, Istruzione, Lavoro, Finanza, Viaggi, Regali, Tasse, Altro + categorie entrata
- Puoi aggiungere sottocategorie custom senza toccare il codice

---

## Idempotenza

Ogni transazione viene identificata da un hash SHA-256 calcolato su: data, importo, descrizione, conto. Reimportare lo stesso file produce lo stesso set di righe; i duplicati vengono scartati senza errori.

---

## Export

Dalla pagina Analytics → pulsante **Esporta**:

| Formato | Contenuto |
|---|---|
| **HTML** | Report standalone con grafici Plotly interattivi |
| **CSV** | Tutte le transazioni filtrate, colonne canoniche |
| **XLSX** | Come CSV ma con formattazione Excel |

---

## Regole descrizione (bulk edit)

Distinte dalle regole di categorizzazione. Servono a sostituire descrizioni grezze illeggibili con testo leggibile.

- Salvate nella tabella `description_rule` del database
- Applicabili in blocco dal pannello in fondo alla pagina Review
- Stessi tipi di match: `exact` / `contains` / `regex`
- Dopo l'applicazione, le transazioni aggiornate vengono riprocessate dal LLM per la categorizzazione

---

## Modifiche massive — pagina dedicata

La pagina **✏️ Modifiche massive** raccoglie tutte le operazioni che agiscono su più transazioni contemporaneamente.

### Sezioni 1–3: operazioni su transazione di riferimento

| Sezione | Operazione |
|---------|-----------|
| **1 · Scegli transazione** | Selezione con ricerca testuale e filtro "solo da rivedere" |
| **2a · Giroconto** | Toggle giroconto ↔ normale, con propagazione a tutte le tx con stessa descrizione |
| **2b · Contesto** | Assegna contesto alla tx selezionata e/o alle simili (Jaccard ≥ 35%) |
| **2c · Categoria** | Corregge categoria/sottocategoria, salva regola deterministica, propaga alle simili |

### Sezione 3: Eliminazione massiva da filtro

Permette di eliminare in blocco transazioni selezionate tramite filtri combinabili:

| Filtro | Tipo |
|--------|------|
| Da / A | Intervallo date |
| Conto | Account label esatto |
| Tipo | tx_type (expense, income, card_tx, …) |
| Descrizione | Ricerca `ILIKE` su `description` e `raw_description` |
| Categoria | Categoria esatta |

**Comportamento:**
- Se nessun filtro è impostato il pulsante di eliminazione non è disponibile (protezione da cancellazione accidentale di tutto il DB)
- Il contatore mostra in tempo reale il numero di transazioni che verranno cancellate
- Un'anteprima espandibile mostra le prime 10 righe corrispondenti
- La conferma richiede di digitare esattamente `ELIMINA` nel campo testo prima di abilitare il pulsante
- L'eliminazione è **irreversibile** — assicurarsi di avere un backup prima di procedere (vedi `deployment.md`)
- I link di riconciliazione e giroconti associati alle transazioni eliminate vengono rimossi in cascade

---

## Database

File SQLite: `ledger.db` nella directory dell'applicazione.

Tabelle principali:

| Tabella | Contenuto |
|---|---|
| `transaction` | Tutte le transazioni importate |
| `import_batch` | Metadati di ogni importazione (file, schema, conteggi) |
| `document_schema` | Template schema per Flow 1 (fingerprint colonne → configurazione) |
| `reconciliation_link` | Coppie carta–c/c riconciliate |
| `internal_transfer_link` | Coppie giroconto |
| `category_rule` | Regole di categorizzazione utente |
| `description_rule` | Regole di pulizia descrizione in blocco |
| `taxonomy_category` | Categorie tassonomia |
| `taxonomy_subcategory` | Sottocategorie tassonomia |
| `import_job` | Stato corrente del job di importazione |
| `user_settings` | Preferenze utente (formato date, separatori, LLM, contesti) |

Le migrazioni dello schema sono idempotenti: vengono eseguite automaticamente ad ogni avvio senza perdita di dati.

---

## Avvio rapido

```bash
# Prima installazione
./setup.sh          # macOS/Linux
setup.bat           # Windows

# Avvio
uv run streamlit run app.py
```

App disponibile su `http://localhost:8501`.
