# Spendify — Guida all'installazione, backup e ripristino

> Questo documento descrive come installare Spendify (con e senza Docker),
> come configurare l'ambiente, come fare un backup del database e come
> ripristinarlo in caso di problemi.

---

## Indice

1. [Prerequisiti](#1--prerequisiti)
2. [Installazione senza Docker (sviluppo / uso locale)](#2--installazione-senza-docker)
3. [Installazione con Docker Compose (produzione consigliata)](#3--installazione-con-docker-compose)
4. [Configurazione `.env`](#4--configurazione-env)
5. [Primo avvio con un database esistente](#5--primo-avvio-con-un-database-esistente)
6. [Backup del database](#6--backup-del-database)
7. [Ripristino del database](#7--ripristino-del-database)
8. [Aggiornare l'applicazione](#8--aggiornare-lapplicazione)
9. [Risoluzione problemi comuni](#9--risoluzione-problemi-comuni)

---

## Concetti Docker per chi parte da zero

Se non hai mai usato Docker, questi tre concetti ti basteranno per gestire Spendify:

| Concetto | Analogia | Cosa significa in pratica |
|----------|----------|--------------------------|
| **Image** (immagine) | Ricetta di cucina | Il "pacchetto" con tutto il codice e le dipendenze, costruito una volta sola con `docker compose build` |
| **Container** | Piatto cucinato | L'applicazione in esecuzione, creata dall'immagine. Si avvia, si ferma, si cancella senza perdere i dati (che stanno nel volume) |
| **Volume** | Quaderno esterno | La cartella persistente dove sta il database. Sopravvive anche se il container viene cancellato e ricreato |

**Flusso normale:**
```
build (una volta) → up (avvia) → down (ferma) → up (riavvia con stessi dati)
```

**Cosa NON cancella i tuoi dati:**
- `docker compose down` ✅ sicuro
- `docker compose up -d --build` ✅ sicuro (ricostruisce l'immagine, dati intatti)

**Cosa CANCELLA i dati:**
- `docker compose down -v` ⚠️ cancella i volumi — usare solo per reset completo

---

## 1 — Prerequisiti

### Installazione senza Docker

| Strumento | Versione minima | Note |
|-----------|----------------|------|
| Python    | 3.13           | `python --version` |
| uv        | qualsiasi      | `pip install uv` oppure `curl -Ls https://astral.sh/uv/install.sh \| sh` |
| Git       | qualsiasi      | per clonare il repository |

### Installazione con Docker

| Strumento | Versione minima | Note |
|-----------|----------------|------|
| Docker    | 24+            | `docker --version` |
| Docker Compose | v2 (plugin) | `docker compose version` |

> **Nota:** Docker Compose v2 è incluso in Docker Desktop e nelle versioni moderne di Docker Engine. Se il comando è `docker-compose` (con trattino) stai usando la v1 obsoleta — aggiorna Docker.

---

## 2 — Installazione senza Docker

### 2.1 — Clona il repository

```bash
git clone https://github.com/drake69/spendify.git spendify
cd spendify
```

### 2.2 — Crea l'ambiente virtuale e installa le dipendenze

```bash
uv sync
```

Questo comando crea automaticamente `.venv/` e installa tutte le dipendenze da `uv.lock`.

### 2.3 — Configura l'ambiente

```bash
cp .env.example .env
# Modifica .env con il tuo editor preferito
```

I campi obbligatori sono descritti nella [sezione 4](#4--configurazione-env).

### 2.4 — Avvia l'applicazione

```bash
uv run streamlit run app.py
```

L'app è disponibile su **http://localhost:8501**

> Il database `ledger.db` viene creato automaticamente nella directory corrente al primo avvio.

---

## 3 — Installazione con Docker Compose

### 3.1 — Clona il repository

```bash
git clone https://github.com/drake69/spendify.git spendify
cd spendify
```

### 3.2 — Configura l'ambiente

```bash
cp .env.example .env
# Modifica .env con il tuo editor preferito
```

### 3.3 — Costruisci e avvia i container

```bash
docker compose up -d --build
```

- `--build` forza la ricostruzione dell'immagine (necessario solo al primo avvio o dopo aggiornamenti)
- `-d` avvia in background (detached)

L'app è disponibile su **http://localhost:8501**

### 3.4 — Verifica lo stato

```bash
# Stato container
docker compose ps

# Log in tempo reale
docker compose logs -f spendify

# Healthcheck
docker inspect spendify_app --format='{{.State.Health.Status}}'
```

### 3.5 — Stop / riavvio

```bash
# Stop senza rimuovere i dati
docker compose down

# Stop e rimuovi SOLO i container (i volumi dati sono preservati)
docker compose down --remove-orphans

# ⚠️  Stop e rimuovi TUTTO inclusi i volumi (PERDITA DATI)
docker compose down -v   # usare solo se si vuole reset completo
```

### 3.6 — LLM (opzionale)

Il backend LLM è configurabile direttamente dall'interfaccia utente nella pagina **Impostazioni**.
Tutte le opzioni (Ollama, OpenAI, Groq, ecc.) si impostano da lì — non è necessario alcun parametro nel `.env`.

---

## 4 — Configurazione `.env`

Il file `.env` contiene solo i due parametri fondamentali. Copia `.env.example` in `.env`:

```bash
cp .env.example .env
```

| Parametro | Descrizione | Default |
|-----------|-------------|---------|
| `SPENDIFY_DB` | URI del database SQLite | `sqlite:///ledger.db` |
| `TAXONOMY_PATH` | Percorso del file YAML delle categorie | `taxonomy.yaml` |

```bash
# ── Database ──────────────────────────────────────────────────────────────────
SPENDIFY_DB=sqlite:///ledger.db

# ── Tassonomia ────────────────────────────────────────────────────────────────
TAXONOMY_PATH=taxonomy.yaml
```

> **Nota:** tutte le altre impostazioni (LLM backend, modello, chiavi API, privacy, lingua, formato date, ecc.)
> si configurano dall'interfaccia nella pagina **⚙️ Impostazioni** e vengono salvate nel database.

> **Sicurezza:** anche se il `.env` non contiene chiavi API, non aggiungerlo mai a git.
> Verificare che `.gitignore` contenga la riga `.env`.

---

## 5 — Primo avvio con un database esistente

Se hai già un `ledger.db` (ad esempio creato con l'installazione locale) e vuoi usarlo nel container Docker, devi **copiarlo nel volume** prima di avviare l'app.

> **Perché non basta copiare il file nella cartella?**
> Il container Docker non vede il filesystem del tuo Mac/Linux direttamente.
> Il database vive nel **volume** `spendify_data`, una cartella gestita da Docker.
> Per metterci qualcosa devi usare un container temporaneo come "ponte".

### 5.1 — Copia il DB nel volume (da file locale)

```bash
# 1. Assicurati che il container sia fermo
docker compose down

# 2. Copia il tuo ledger.db nel volume spendify_data
docker run --rm \
  -v spendify_data:/data \
  -v "/percorso/del/tuo/ledger.db":/source/ledger.db:ro \
  python:3.13-slim \
  cp /source/ledver.db /data/ledger.db
```

> Sostituisci `/percorso/del/tuo/ledger.db` con il path assoluto del tuo file.
> Esempio su Mac: `-v "/Users/mario/spendify/ledger.db":/source/ledger.db:ro`

### 5.2 — Verifica che il file sia arrivato

```bash
docker run --rm \
  -v spendify_data:/data \
  python:3.13-slim \
  ls -lh /data/
```

Dovresti vedere `ledger.db` con la dimensione corretta.

### 5.3 — Avvia l'app

```bash
docker compose up -d
```

---

## 6 — Backup del database

Il database di Spendify è un singolo file SQLite. Il backup consiste nel **copiare quel file** in un luogo sicuro.

### 6.1 — Posizione del database

| Modalità | Percorso |
|----------|---------|
| Installazione locale | `./ledger.db` (directory del progetto) |
| Docker Compose | volume Docker `spendify_data` → `/app/data/ledger.db` |

### 6.2 — Backup manuale (installazione locale)

```bash
# Copia con timestamp
cp ledger.db backups/ledger_$(date +%Y%m%d_%H%M%S).db
```

> **Importante:** per un backup coerente mentre l'app è in esecuzione, usare
> il comando `sqlite3` con l'API di backup online (evita corruzione da scritture concorrenti):

```bash
sqlite3 ledger.db ".backup backups/ledger_$(date +%Y%m%d_%H%M%S).db"
```

### 6.3 — Backup da Docker

Il metodo più semplice è copiare direttamente dal container in esecuzione:

```bash
mkdir -p backups
docker cp spendify_app:/app/data/ledger.db backups/ledger_$(date +%Y%m%d_%H%M%S).db
```

> `docker cp` copia un file dal filesystem del container all'host — non richiede
> container temporanei né comandi aggiuntivi.

Se il container è fermo, usa un container temporaneo come "ponte":

```bash
mkdir -p backups
docker run --rm \
  -v spendify_data:/data \
  -v "$(pwd)/backups":/backups \
  python:3.13-slim \
  cp /data/ledger.db /backups/ledger_backup.db
```

> ⚠️ `sqlite3` non è incluso in `python:3.13-slim`. Usare `cp` come sopra,
> oppure installarlo al volo: `python:3.13-slim sh -c "apt-get install -y sqlite3 && sqlite3 ..."`

### 6.4 — Backup automatico (crontab)

Aggiungere al crontab del server (`crontab -e`) per un backup giornaliero alle 03:00:

```cron
# Backup Spendify ogni giorno alle 3:00
0 3 * * * cd /path/to/spendify && docker cp spendify_app:/app/data/ledger.db backups/ledger_$(date +\%Y\%m\%d).db 2>&1 >> logs/backup.log

# Pulizia backup più vecchi di 30 giorni
0 4 * * * find /path/to/spendify/backups -name "ledger_*.db" -mtime +30 -delete
```

Per l'installazione locale (senza Docker):

```cron
0 3 * * * cd /path/to/spendify && sqlite3 ledger.db ".backup backups/ledger_$(date +\%Y\%m\%d).db" 2>&1 >> logs/backup.log
0 4 * * * find /path/to/spendify/backups -name "ledger_*.db" -mtime +30 -delete
```

### 6.5 — Cosa include il backup

Il file `ledger.db` contiene **tutto**:

- Tutte le transazioni importate
- Regole di categorizzazione (`category_rule`)
- Regole di descrizione (`description_rule`)
- Schemi dei documenti (colonne CSV/XLSX dei conti)
- Impostazioni utente (locale, formato date, contesti, ecc.)
- Tassonomia personalizzata (se modificata dall'app)
- Link di riconciliazione e giroconti

> Il file `taxonomy.yaml` contiene solo la tassonomia di default — se non è
> stata modificata dall'app, non è necessario includerlo nel backup.

---

## 7 — Ripristino del database

### 7.1 — Verifica integrità del backup prima di ripristinare

```bash
sqlite3 backups/ledger_20240101_030000.db "PRAGMA integrity_check;"
# Output atteso: "ok"
```

### 7.2 — Ripristino (installazione locale)

```bash
# 1. Ferma l'applicazione (Ctrl+C o pkill)
pkill -f "streamlit run app.py"

# 2. Crea un backup del DB corrente (per sicurezza)
cp ledger.db ledger_before_restore_$(date +%Y%m%d_%H%M%S).db

# 3. Ripristina dal backup scelto
cp backups/ledger_20240101_030000.db ledger.db

# 4. Riavvia l'applicazione
uv run streamlit run app.py
```

### 7.3 — Ripristino da Docker

```bash
# 1. Ferma il container
docker compose down

# 2. Copia il backup nel volume Docker
docker run --rm \
  -v spendify_data:/data \
  -v "$(pwd)/backups":/backups \
  python:3.13-slim \
  cp /backups/ledger_20240101_030000.db /data/ledger.db

# 3. Riavvia
docker compose up -d
```

Oppure usando `docker cp` (se il container è in esecuzione):

```bash
# 1. Metti il container in modalità manutenzione (stop + keep volume)
docker compose stop spendify

# 2. Avvia un container temporaneo sullo stesso volume per il ripristino
docker run --rm \
  -v spendify_data:/data \
  -v "$(pwd)/backups":/backups \
  python:3.13-slim \
  sh -c "cp /data/ledger.db /data/ledger_before_restore.db && cp /backups/ledger_20240101_030000.db /data/ledger.db"

# 3. Riavvia
docker compose start spendify
```

### 7.4 — Ripristino parziale (solo alcune tabelle)

Se si vuole recuperare solo le regole di categorizzazione da un backup senza sovrascrivere le transazioni:

```bash
# Apri il backup in sola lettura e copia la tabella category_rule nel DB attivo
sqlite3 ledger.db "
ATTACH DATABASE 'backups/ledger_20240101.db' AS bkp;
DELETE FROM category_rule;
INSERT INTO category_rule SELECT * FROM bkp.category_rule;
DETACH DATABASE bkp;
"
```

Stessa logica per altre tabelle: `description_rule`, `user_settings`, `taxonomy_category`, ecc.

---

## 8 — Aggiornare l'applicazione

### Installazione locale

```bash
git pull origin main
uv sync                              # aggiorna le dipendenze
pkill -f "streamlit run app.py"      # ferma l'istanza precedente
uv run streamlit run app.py          # riavvia
```

### Docker Compose

```bash
git pull origin main
docker compose down
docker compose up -d --build         # ricostruisce l'immagine con il nuovo codice
```

> Il database nel volume `spendify_data` non viene toccato dall'aggiornamento.
> Le migrazioni dello schema vengono applicate automaticamente all'avvio.

---

## 9 — Risoluzione problemi comuni

### L'app non si avvia / porta 8501 occupata

```bash
# Controlla cosa usa la porta
lsof -i :8501

# Forza la chiusura
pkill -f "streamlit run app.py"
# oppure su Docker:
docker compose down && docker compose up -d
```

### Errore "database is locked"

Il file DB è aperto da un altro processo. Soluzioni:

```bash
# Controlla i processi che tengono aperto il file
fuser ledger.db

# Forza la chiusura dell'app e riavvia
pkill -f "streamlit run app.py"
uv run streamlit run app.py
```

### Corruzione del database

```bash
# Verifica
sqlite3 ledger.db "PRAGMA integrity_check;"

# Se l'output non è "ok", tenta il recupero automatico
sqlite3 ledger.db ".recover" | sqlite3 ledger_recovered.db
mv ledger.db ledger_corrupted_$(date +%Y%m%d).db
mv ledger_recovered.db ledger.db
```

Se il recupero non riesce, ripristinare dall'ultimo backup valido (sezione 6).

### Il container Docker si riavvia continuamente

```bash
# Leggi i log per individuare l'errore
docker compose logs --tail=50 spendify

# Problemi comuni:
# - .env mancante o con valori errati
# - Volume non montato correttamente
# - Porta 8501 già in uso sull'host
```

### Memoria insufficiente per Ollama

Il modello `gemma3:12b` richiede ~8 GB di RAM. Usare un modello più leggero tramite la pagina **⚙️ Impostazioni**:

| Modello | RAM richiesta |
|---------|--------------|
| `gemma3:12b` | ~8 GB |
| `qwen2.5:7b` | ~5 GB |
| `llama3.2:3b` | ~3 GB |

---

*Documento generato per Spendify v2.4 — aggiornato al 2026-03-17*
