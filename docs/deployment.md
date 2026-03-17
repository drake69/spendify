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

Il database di Spendify è un singolo file SQLite. Il backup consiste nel **copiare quel file** in un posto sicuro.

### 6.1 — Posizione del database

| Modalità | Percorso |
|----------|---------|
| Installazione locale | `./ledger.db` (nella cartella del progetto) |
| Docker Compose | volume Docker → `/app/data/ledger.db` dentro il container |

### 6.2 — Backup (installazione locale)

```bash
# 1. Crea la cartella di backup se non esiste ancora
#    Puoi scegliere qualunque percorso, ad esempio ./backups o ~/Desktop/spendify-backup
mkdir -p <CARTELLA_BACKUP>

# 2. Copia il DB con un nome che include data e ora
cp ledger.db <CARTELLA_BACKUP>/ledger_$(date +%Y%m%d_%H%M%S).db
```

> **`<CARTELLA_BACKUP>`** — percorso a tua scelta dove salvare i backup.
> Esempi: `./backups` · `~/Desktop/spendify-backup` · `/mnt/nas/spendify`

### 6.3 — Backup da Docker

Il metodo più diretto usa `docker cp`, che copia un file direttamente dal container in esecuzione all'host senza container aggiuntivi:

```bash
# 1. Crea la cartella di backup se non esiste ancora
mkdir -p <CARTELLA_BACKUP>

# 2. Copia il DB dal container all'host
docker cp spendify_app:/app/data/ledger.db <CARTELLA_BACKUP>/ledger_$(date +%Y%m%d_%H%M%S).db
```

> **`<CARTELLA_BACKUP>`** — percorso a tua scelta. Esempio: `./backups`
> **`spendify_app`** — nome del container, definito nel `docker-compose.yml` alla riga `container_name`.
> Il container deve essere **in esecuzione** per usare `docker cp`.

Se il container è fermo, usa un container temporaneo come "ponte":

```bash
# 1. Trova il nome esatto del volume con:
docker volume ls | grep spendify
# L'output sarà qualcosa come: angry-wozniak_spendify_data  oppure  spendify_spendify_data
# Il prefisso dipende dal nome della cartella da cui hai lanciato docker compose

# 2. Crea la cartella di backup se non esiste ancora
mkdir -p <CARTELLA_BACKUP>

# 3. Copia dal volume all'host tramite container temporaneo
docker run --rm \
  -v <NOME_VOLUME>:/data \
  -v "<CARTELLA_BACKUP>":/backups \
  python:3.13-slim \
  cp /data/ledger.db /backups/ledger_backup.db
```

> **`<NOME_VOLUME>`** — nome del volume trovato al passo 1. Esempio: `angry-wozniak_spendify_data`
> **`<CARTELLA_BACKUP>`** — percorso **assoluto** della cartella di backup sull'host.
> Esempio su Mac: `/Users/mario/backups` — non usare `$(pwd)/backups` perché richiede che la cartella esista già.

### 6.4 — Backup automatico (crontab)

```cron
# Sostituisci i segnaposto con i tuoi valori:
#   <PERCORSO_PROGETTO>  = cartella dove hai clonato Spendify
#                          es. /home/mario/spendify  oppure  /Users/mario/Documents/spendify
#   <CARTELLA_BACKUP>    = cartella dove salvare i backup (deve esistere)
#                          es. /home/mario/backups

# Backup ogni giorno alle 03:00
0 3 * * * docker cp spendify_app:/app/data/ledger.db <CARTELLA_BACKUP>/ledger_$(date +\%Y\%m\%d).db

# Cancella i backup più vecchi di 30 giorni
0 4 * * * find <CARTELLA_BACKUP> -name "ledger_*.db" -mtime +30 -delete
```

Per l'installazione locale (senza Docker):

```cron
# Sostituisci <PERCORSO_PROGETTO> e <CARTELLA_BACKUP> come sopra
0 3 * * * cd <PERCORSO_PROGETTO> && cp ledger.db <CARTELLA_BACKUP>/ledger_$(date +\%Y\%m\%d).db
0 4 * * * find <CARTELLA_BACKUP> -name "ledger_*.db" -mtime +30 -delete
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

### 7.1 — Ripristino (installazione locale)

```bash
# 1. Ferma l'applicazione (Ctrl+C o pkill)
pkill -f "streamlit run app.py"

# 2. Fai un backup del DB attuale (per sicurezza)
cp ledger.db ledger_before_restore_$(date +%Y%m%d_%H%M%S).db

# 3. Sostituisci il DB con il backup scelto
#    <FILE_BACKUP> = percorso del file da ripristinare
#    Esempio: ./backups/ledger_20260316_030000.db
cp <FILE_BACKUP> ledger.db

# 4. Riavvia l'applicazione
uv run streamlit run app.py
```

### 7.2 — Ripristino da Docker

```bash
# 1. Ferma il container
docker compose down

# 2. Trova il nome del volume (se non lo ricordi)
docker volume ls | grep spendify

# 3. Crea una cartella temporanea e mettici il file da ripristinare
#    <FILE_BACKUP> = percorso del tuo file di backup sull'host
#    Esempio: /Users/mario/backups/ledger_20260316.db
mkdir -p /tmp/spendify-restore
cp <FILE_BACKUP> /tmp/spendify-restore/ledger.db

# 4. Copia il backup nel volume tramite container temporaneo
#    <NOME_VOLUME> = trovato al passo 2, es. angry-wozniak_spendify_data
docker run --rm \
  -v <NOME_VOLUME>:/data \
  -v /tmp/spendify-restore:/source:ro \
  python:3.13-slim \
  cp /source/ledger.db /data/ledger.db

# 5. Riavvia
docker compose up -d
```

### 7.3 — Ripristino parziale (solo alcune tabelle)

Se vuoi recuperare solo le regole di categorizzazione da un backup senza sovrascrivere le transazioni, usa `sqlite3` (richiede installazione locale di sqlite3 sull'host):

```bash
# <FILE_BACKUP> = percorso del backup da cui estrarre le regole
sqlite3 ledger.db "
ATTACH DATABASE '<FILE_BACKUP>' AS bkp;
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
