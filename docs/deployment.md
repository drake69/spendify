# Spendify — Guida al deployment

> Questo documento descrive come installare, configurare e aggiornare Spendify.
> Per backup, ripristino e gestione del database → [database.md](database.md).
> Per installazione su Mac nativo, Linux con Ollama e Windows con llama.cpp → [installazione.md](installazione.md).

---

## Indice

1. [Installazione rapida (one-liner Docker)](#1--installazione-rapida-one-liner-docker)
2. [Installazione Docker Compose da repository](#2--installazione-docker-compose-da-repository)
3. [Installazione nativa (sviluppo / Mac)](#3--installazione-nativa-sviluppo--mac)
4. [Configurazione `.env`](#4--configurazione-env)
5. [Aggiornare l'applicazione](#5--aggiornare-lapplicazione)
6. [Comandi operativi Docker](#6--comandi-operativi-docker)
7. [Risoluzione problemi](#7--risoluzione-problemi)

---

## Concetti Docker per chi parte da zero

| Concetto | Analogia | Cosa significa in pratica |
|----------|----------|--------------------------|
| **Image** | Ricetta di cucina | Il pacchetto con tutto il codice e le dipendenze |
| **Container** | Piatto cucinato | L'app in esecuzione, creata dall'immagine |
| **Volume** | Quaderno esterno | La cartella persistente dove sta il database — sopravvive anche se il container viene cancellato |

**Cosa NON cancella i tuoi dati:**
- `docker compose down` ✅ sicuro
- `docker compose up -d --build` ✅ sicuro (ricostruisce l'immagine, dati intatti)

**Cosa CANCELLA i dati:**
- `docker compose down -v` ⚠️ cancella i volumi — usare solo per reset completo

---

## 1 — Installazione rapida (one-liner Docker)

L'unico prerequisito è **[Docker Desktop](https://www.docker.com/products/docker-desktop/)** installato e avviato.

**Mac / Linux:**
```bash
curl -fsSL https://raw.githubusercontent.com/drake69/spendify/main/install.sh | bash
```

**Windows (PowerShell):**
```powershell
irm https://raw.githubusercontent.com/drake69/spendify/main/install.ps1 | iex
```

Lo script crea la cartella `~/spendify/`, scarica l'immagine da GitHub Container Registry, avvia il container e apre il browser su **http://localhost:8501** automaticamente.

> **Aggiornamento:** `docker compose -C ~/spendify pull && docker compose -C ~/spendify up -d`

---

## 2 — Installazione Docker Compose da repository

Adatta a chi vuole modificare il codice o configurare profili LLM (Ollama, llama.cpp).

### 2.1 — Clona il repository

```bash
git clone https://github.com/drake69/spendify.git spendify
cd spendify
```

### 2.2 — Configura l'ambiente

```bash
cp .env.example .env
```

### 2.3 — Costruisci e avvia

```bash
docker compose up -d --build
```

- `--build` forza la ricostruzione dell'immagine (necessario al primo avvio o dopo aggiornamenti del codice)
- `-d` avvia in background

L'app è disponibile su **http://localhost:8501**

### 2.4 — Con LLM locale (opzionale)

```bash
# Ollama (Linux / server con GPU)
docker compose --profile ollama up -d

# llama.cpp (Windows / CPU)
docker compose --profile llama-cpp up -d
```

Per la configurazione completa dei backend LLM → [installazione.md](installazione.md).

---

## 3 — Installazione nativa (sviluppo / Mac)

### Prerequisiti

| Strumento | Versione minima |
|-----------|----------------|
| Python | 3.13 |
| uv | qualsiasi — `curl -Ls https://astral.sh/uv/install.sh \| sh` |

### Steps

```bash
git clone https://github.com/drake69/spendify.git spendify
cd spendify
uv sync
cp .env.example .env
uv run streamlit run app.py
```

L'app è disponibile su **http://localhost:8501**

> Il database `ledger.db` viene creato automaticamente nella cartella del progetto al primo avvio.

---

## 4 — Configurazione `.env`

Il file `.env` contiene solo due parametri. Tutte le altre impostazioni (LLM, API key, formato date, lingua, ecc.) si configurano dall'interfaccia nella pagina **⚙️ Impostazioni**.

```bash
cp .env.example .env
```

| Parametro | Descrizione | Default |
|-----------|-------------|---------|
| `SPENDIFY_DB` | URI del database SQLite | `sqlite:///ledger.db` |
| `TAXONOMY_PATH` | Percorso del file YAML delle categorie | `taxonomy.yaml` |

```dotenv
SPENDIFY_DB=sqlite:///ledger.db
TAXONOMY_PATH=taxonomy.yaml

# Solo per il profilo llama-cpp:
# LLAMA_MODEL=gemma-3-4b-it-Q4_K_M.gguf
```

> Non aggiungere mai `.env` a git — verificare che `.gitignore` contenga la riga `.env`.

---

## 5 — Aggiornare l'applicazione

### One-liner Docker

```bash
docker compose -C ~/spendify pull
docker compose -C ~/spendify up -d
```

### Docker Compose da repository

```bash
git pull origin main
docker compose down
docker compose up -d --build
```

### Nativa

```bash
git pull origin main
uv sync
pkill -f "streamlit run app.py"
uv run streamlit run app.py
```

> Le migrazioni del database vengono applicate automaticamente all'avvio — non è necessario alcun intervento manuale.

---

## 6 — Comandi operativi Docker

```bash
# Stato container
docker compose ps

# Log in tempo reale
docker compose logs -f spendify

# Healthcheck
docker inspect spendify_app --format='{{.State.Health.Status}}'

# Stop (dati intatti)
docker compose down

# Stop + rimuovi container orfani (dati intatti)
docker compose down --remove-orphans

# ⚠️  Reset completo inclusi i volumi (PERDITA DATI)
docker compose down -v
```

Per l'installazione one-liner aggiungere `-C ~/spendify` a ogni comando, es. `docker compose -C ~/spendify logs -f`.

---

## 7 — Risoluzione problemi

### L'app non si avvia / porta 8501 occupata

```bash
# Controlla cosa usa la porta
lsof -i :8501

# Nativa
pkill -f "streamlit run app.py"

# Docker
docker compose down && docker compose up -d
```

### Il container Docker si riavvia continuamente

```bash
docker compose logs --tail=50 spendify
```

Cause comuni:
- `.env` mancante o valori errati
- Volume non montato correttamente
- Porta 8501 già in uso

### Memoria insufficiente per Ollama

Il modello `gemma3:12b` richiede ~8 GB di RAM. Cambia modello dalla pagina **⚙️ Impostazioni**:

| Modello | RAM richiesta |
|---------|--------------|
| `gemma3:12b` | ~8 GB |
| `qwen2.5:7b` | ~5 GB |
| `llama3.2:3b` | ~3 GB |

### Problemi con il database

Errori tipo `database is locked`, corruzione del file, ripristino da backup → [database.md](database.md).
