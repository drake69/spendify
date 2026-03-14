# Spendify — Reference Guide

---

## Pagine dell'applicazione

| Pagina | Scopo |
|---|---|
| **Import** | Carica file CSV/XLSX, avvia la pipeline di elaborazione |
| **Ledger** | Vista tabellare di tutte le transazioni importate |
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
3. Sanitizzazione PII          → redazione IBAN, PAN, CF, nome titolare
4. Riconciliazione carta-c/c   → elimina double-counting addebiti mensili aggregati
5. Rilevamento giroconti       → esclude/neutralizza trasferimenti interni
6. Categorizzazione a cascata  → regole utente → regex statiche → LLM → fallback "Altro"
7. Persistenza                 → INSERT OR IGNORE per idempotenza
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

### Priorità
Le regole vengono valutate nell'ordine: prima le `exact`, poi le `contains`, poi le `regex`. In caso di conflitto tra due regole dello stesso tipo, vince quella creata prima (ID minore).

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
| IBAN | `IT60X0542811101000000123456` | `[IBAN]` |
| Numero carta | `4111 1111 1111 1111` | `[PAN]` |
| Codice fiscale | `RSSMRA80A01H501U` | `[CF]` |
| Nome titolare | `Mario Rossi` | `[OWNER]` |

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

## Database

File SQLite: `ledger.db` nella directory dell'applicazione.

Tabelle principali:

| Tabella | Contenuto |
|---|---|
| `transaction` | Tutte le transazioni importate |
| `account` | Conti/carte riconosciuti |
| `category` | Categorie di primo livello |
| `subcategory` | Sottocategorie |
| `category_rule` | Regole di categorizzazione utente |
| `description_rule` | Regole di pulizia descrizione |
| `import_job` | Storico e stato dei job di importazione |
| `user_settings` | Preferenze utente (formato date, separatori, LLM) |

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
