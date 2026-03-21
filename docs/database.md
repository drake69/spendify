# Spendify — Gestione del database

> Il database di Spendify è un singolo file SQLite (`ledger.db`).
> Questa guida copre tutto quello che riguarda i dati: dove si trovano, come fare backup, come ripristinarli, come spostarli su un altro computer.

---

## Indice

1. [Cosa contiene il database](#1--cosa-contiene-il-database)
2. [Dove si trova il database](#2--dove-si-trova-il-database)
3. [Backup](#3--backup)
4. [Ripristino](#4--ripristino)
5. [Primo avvio con un database esistente](#5--primo-avvio-con-un-database-esistente)
6. [Spostare il database su un altro computer](#6--spostare-il-database-su-un-altro-computer)
7. [Ispezione diretta (sqlite3)](#7--ispezione-diretta-sqlite3)
8. [Corruzione del database](#8--corruzione-del-database)

---

## 1 — Cosa contiene il database

Il file `ledger.db` contiene **tutto** — non esistono altri file di dati da considerare nel backup:

| Tabella | Contenuto |
|---------|-----------|
| `transaction` | Tutte le transazioni importate |
| `category_rule` | Regole deterministiche di categorizzazione |
| `description_rule` | Regole di pulizia delle descrizioni |
| `document_schema` | Schemi dei file CSV/XLSX (colonne, formato, ecc.) |
| `user_settings` | Impostazioni utente (LLM, locale, formato date, contesti, flag onboarding…) |
| `account` | Conti bancari definiti dall'utente (nome + banca) |
| `taxonomy_category` / `taxonomy_subcategory` | Tassonomia personalizzata (modificabile) |
| `taxonomy_default` | Template tassonomia built-in (5 lingue — sola lettura, non modificare) |
| `reconciliation_link` | Link carta–conto (riconciliazione RF-03) |
| `internal_transfer_link` | Giroconti interni (RF-04) |
| `import_batch` / `import_job` | Storico delle importazioni |

> I template tassonomia (`taxonomy_default`) sono incorporati nel codice (`db/taxonomy_defaults.py`) e vengono ricreati automaticamente da zero ad ogni migrazione. Non è necessario includerli in un backup separato.

---

## 2 — Dove si trova il database

| Modalità di installazione | Percorso |
|--------------------------|----------|
| **One-liner Docker** (`install.sh` / `install.ps1`) | Volume Docker `spendify_data` → `/app/data/ledger.db` dentro il container |
| **Docker Compose da repo** | Volume Docker `spendify_data` → `/app/data/ledger.db` dentro il container |
| **Nativa (Mac/Linux, uv)** | `./ledger.db` nella cartella del progetto |

### Perché il volume Docker non è una cartella normale?

Il volume `spendify_data` è gestito da Docker e non è direttamente accessibile dal filesystem del tuo computer come una cartella normale. Per leggere o scrivere nel volume bisogna usare un container temporaneo come "ponte" — i comandi nelle sezioni seguenti fanno esattamente questo.

---

## 3 — Backup

### 3.1 — Backup (installazione nativa)

```bash
# Crea la cartella di backup (una volta sola)
mkdir -p ~/spendify-backup

# Copia il DB con un nome che include la data
cp ledger.db ~/spendify-backup/ledger_$(date +%Y%m%d_%H%M%S).db
```

### 3.2 — Backup (Docker — container in esecuzione)

Metodo diretto con `docker cp`, non richiede container aggiuntivi:

```bash
mkdir -p ~/spendify-backup

docker cp spendify_app:/app/data/ledger.db \
  ~/spendify-backup/ledger_$(date +%Y%m%d_%H%M%S).db
```

> `spendify_app` è il nome del container (definito in `docker-compose.yml`).
> Il container **deve essere in esecuzione** per usare `docker cp`.

### 3.3 — Backup (Docker — container fermo)

Se il container è fermo usa un container temporaneo Alpine (più leggero di Python):

```bash
mkdir -p ~/spendify-backup

docker run --rm \
  -v spendify_data:/data \
  -v ~/spendify-backup:/backup \
  alpine cp /data/ledger.db /backup/ledger_$(date +%Y%m%d_%H%M%S).db
```

> **Windows (PowerShell):** sostituisci `~/spendify-backup` con `$env:USERPROFILE\spendify-backup`
> e `$(date +%Y%m%d_%H%M%S)` con la data a mano, es. `20260317_120000`.

### 3.4 — Backup automatico (crontab, Linux/Mac)

```cron
# Backup ogni giorno alle 03:00
0 3 * * * docker cp spendify_app:/app/data/ledger.db ~/spendify-backup/ledger_$(date +\%Y\%m\%d).db

# Cancella i backup più vecchi di 30 giorni
0 4 * * * find ~/spendify-backup -name "ledger_*.db" -mtime +30 -delete
```

Per installazione nativa:

```cron
0 3 * * * cp /percorso/progetto/ledger.db ~/spendify-backup/ledger_$(date +\%Y\%m\%d).db
0 4 * * * find ~/spendify-backup -name "ledger_*.db" -mtime +30 -delete
```

---

## 4 — Ripristino

### 4.1 — Ripristino (installazione nativa)

```bash
# 1. Ferma l'app
pkill -f "streamlit run app.py"

# 2. Salva il DB attuale (per sicurezza)
cp ledger.db ledger_before_restore_$(date +%Y%m%d_%H%M%S).db

# 3. Ripristina il backup scelto
cp ~/spendify-backup/ledger_20260317_030000.db ledger.db

# 4. Riavvia
uv run streamlit run app.py
```

### 4.2 — Ripristino (Docker)

```bash
# 1. Ferma il container
docker compose -C ~/spendify down

# 2. Copia il backup nel volume
docker run --rm \
  -v spendify_data:/data \
  -v ~/spendify-backup:/backup:ro \
  alpine cp /backup/ledger_20260317_030000.db /data/ledger.db

# 3. Riavvia
docker compose -C ~/spendify up -d
```

> Se hai installato da repository invece che con il one-liner, sostituisci
> `docker compose -C ~/spendify` con `docker compose` dalla cartella del progetto.

### 4.3 — Ripristino parziale (solo alcune tabelle)

Utile se vuoi recuperare solo le regole di categorizzazione da un backup senza sovrascrivere le transazioni. Richiede `sqlite3` installato sull'host:

```bash
sqlite3 ledger.db "
ATTACH DATABASE '/percorso/backup/ledger_20260317.db' AS bkp;
DELETE FROM category_rule;
INSERT INTO category_rule SELECT * FROM bkp.category_rule;
DETACH DATABASE bkp;
"
```

Stessa logica per altre tabelle: `description_rule`, `user_settings`, `taxonomy_category`, `taxonomy_subcategory`.

---

## 5 — Primo avvio con un database esistente

Se hai già un `ledger.db` (ad esempio creato con l'installazione nativa) e vuoi usarlo nel container Docker, devi copiarlo nel volume **prima** di avviare l'app.

```bash
# 1. Assicurati che il container sia fermo
docker compose -C ~/spendify down

# 2. Copia il DB nel volume
docker run --rm \
  -v spendify_data:/data \
  -v "/percorso/assoluto/ledger.db":/source/ledger.db:ro \
  alpine cp /source/ledger.db /data/ledger.db

# 3. Verifica che il file sia arrivato
docker run --rm \
  -v spendify_data:/data \
  alpine ls -lh /data/

# 4. Avvia l'app
docker compose -C ~/spendify up -d
```

> **Mac:** il percorso assoluto è `/Users/tuonome/spendify/ledger.db`
> **Linux:** `/home/tuonome/spendify/ledger.db`

---

## 6 — Spostare il database su un altro computer

1. **Fai il backup** del DB sul computer di origine (sezione 3)
2. **Copia il file** `.db` sul nuovo computer (USB, cloud, scp, ecc.)
3. **Installa Spendify** sul nuovo computer con il one-liner (`install.sh` / `install.ps1`)
4. **Importa il DB** nel volume Docker (sezione 5)
5. Apri l'app: tutte le transazioni, regole e impostazioni sono presenti

> Il file SQLite è **portabile**: funziona identicamente su Mac, Linux e Windows, indipendentemente dall'architettura del processore (Intel / ARM).

---

## 7 — Ispezione diretta (sqlite3)

Puoi aprire il database con qualsiasi client SQLite. Esempi:

**Da terminale (sqlite3):**
```bash
# Installazione nativa — dalla cartella del progetto
sqlite3 ledger.db

# Docker — estrai prima il DB con docker cp
docker cp spendify_app:/app/data/ledger.db /tmp/ledger_inspect.db
sqlite3 /tmp/ledger_inspect.db
```

**Query utili:**
```sql
-- Numero di transazioni per anno
SELECT strftime('%Y', date) AS anno, COUNT(*) FROM "transaction" GROUP BY anno;

-- Ultime 10 transazioni
SELECT date, description, amount, category FROM "transaction" ORDER BY date DESC LIMIT 10;

-- Regole attive
SELECT pattern, category, subcategory FROM category_rule ORDER BY priority;

-- Impostazioni utente
SELECT key, value FROM user_settings;
```

**Client grafici:** [DB Browser for SQLite](https://sqlitebrowser.org) (gratuito, Mac/Linux/Windows) — apri direttamente il file `.db`.

---

## 8 — Corruzione del database

La corruzione del file SQLite è rara ma può avvenire in caso di interruzione di corrente durante una scrittura.

### Verifica

```bash
sqlite3 ledger.db "PRAGMA integrity_check;"
# Output atteso: ok
# Se l'output contiene errori, il file è corrotto
```

### Tentativo di recupero automatico

```bash
sqlite3 ledger.db ".recover" | sqlite3 ledger_recovered.db
mv ledger.db ledger_corrupted_$(date +%Y%m%d).db
mv ledger_recovered.db ledger.db
```

Verifica di nuovo con `PRAGMA integrity_check;`. Se il recupero non riesce, ripristina dall'ultimo backup valido (sezione 4).

### Prevenzione

- L'installazione Docker ha `restart: unless-stopped` che evita shutdown improvvisi del container
- Fare backup regolari (sezione 3.4) garantisce sempre un punto di ripristino recente
