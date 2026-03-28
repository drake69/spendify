# Spendify — Guida all'installazione

> Scenari coperti:
> - **Mac One-Click** — installazione automatica (consigliata, zero configurazione)
> - **Mac** — installazione manuale nativa (massime prestazioni LLM)
> - **Linux nativo** — installazione diretta con Ollama locale
> - **Linux Docker** — Docker con Ollama come container separato
> - **Windows** — Docker con llama.cpp server come container separato
> - **One-liner** — installazione guidata da zero con Docker (opzione AI inclusa, consigliata per utenti non tecnici)

---

## Come funziona la configurazione LLM

> **Il backend LLM, l'URL del server, le API key e il modello si configurano dall'app stessa** nella pagina ⚙️ **Impostazioni** — non nel file `.env`.
>
> Il file `.env` contiene solo il percorso del database e della tassonomia.

La configurazione viene salvata nel database (`user_settings`) e persiste tra i riavvii. Al primo avvio l'app usa Ollama su `localhost:11434` come default.

---

## ❓ Il modello LLM va scaricato prima di usare l'app?

**No, con l'installazione one-click il modello viene scaricato automaticamente al primo avvio.** L'app rileva l'hardware (RAM, GPU) e scarica il modello ottimale:

| RAM | Modello | Dimensione |
|-----|---------|-----------|
| 4 GB | Qwen2.5-1.5B | 1.1 GB |
| 8 GB | Qwen2.5-3B | 2.1 GB |
| 12 GB | Qwen2.5-7B | 4.7 GB |
| 16+ GB | Gemma-3-12B | 6.8 GB |

> **Nota:** l'app funziona anche senza LLM attivo. Import, ledger, regole, analisi e report sono sempre disponibili. Se il LLM non è raggiungibile, le transazioni ricevono categoria "Altro" e `to_review=True`.

---

## 🍎 Mac One-Click — Installazione automatica (consigliata)

**Prerequisiti:** macOS 12+, Python 3.11+, connessione internet

1. Scarica `install_spendify.command` dal [repository](https://github.com/drake69/spendify)
2. **Double-click** sul file in Finder
3. Lo script:
   - Verifica Python e installa `uv` (package manager)
   - Scarica Spendify in `~/Applications/Spendify/`
   - Installa tutte le dipendenze
   - Rileva il tuo hardware e consiglia il modello LLM ottimale
4. Al primo import, il modello viene scaricato automaticamente

**Per avviare Spendify ogni giorno:** double-click su `Spendify.command` in `~/Applications/Spendify/packaging/macos/`

**HW minimo:** 4 GB RAM, Apple Silicon o Intel con AVX2. GPU Metal accelerata automaticamente.

---

## 🍎 Mac — Installazione manuale

### Perché nativa su Mac?

Ollama su Mac usa l'accelerazione **Metal (Apple Silicon)** o **OpenCL (Intel)**. Dentro Docker questa accelerazione non è disponibile → inferenza 5-10x più lenta.

### Prerequisiti

- **Python >= 3.13** — verifica con `python3 --version`. Su macOS: `brew install python@3.13`
- **Git**

### Step 1 — Clona il repository

```bash
git clone https://github.com/drake69/spendify.git spendify
cd spendify
```

### Step 2 — Installa uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.zshrc   # oppure riapri il terminale
```

### Step 3 — Installa le dipendenze

```bash
uv sync
```

### Step 4 — Configura il file .env

```bash
cp .env.example .env
# Il file .env non richiede modifiche per un'installazione locale standard
```

### Step 5 — Installa Ollama e scarica il modello

```bash
# Installa Ollama (una tantum)
brew install ollama
# oppure scarica da https://ollama.com

# Avvia il server Ollama in background
ollama serve &

# Scarica il modello (una tantum — ~8 GB per gemma3:12b, ~2 GB per gemma3:4b)
ollama pull gemma3:12b
# Versione più leggera per Mac con RAM < 16 GB:
# ollama pull gemma3:4b
```

> **Il modello viene scaricato una sola volta** in `~/.ollama/models/` e riutilizzato ad ogni avvio successivo.

### Step 6 — Avvia l'app

```bash
# Script di avvio (consigliato) — verifica prerequisiti, attiva il virtualenv e avvia
./start.sh          # solo UI (default)
./start.sh api      # solo REST API
./start.sh all      # UI + API

# Oppure manualmente
uv run streamlit run app.py
```

L'app è disponibile su **http://localhost:8501**

### Step 7 — Configura il backend LLM nell'app

Vai su ⚙️ **Impostazioni** → sezione **Backend LLM**:
- Backend: `Ollama (locale)`
- URL: `http://localhost:11434`
- Modello: `gemma3:12b` (o il modello che hai scaricato)

### Avvio rapido successivo

```bash
ollama serve &          # se non è già in esecuzione
./start.sh              # oppure: uv run streamlit run app.py
```

---

## 🐧 Linux — Installazione nativa

Stessa procedura del Mac, ma senza accelerazione Metal. Consigliata se hai una GPU NVIDIA con driver CUDA o se vuoi evitare Docker.

### Prerequisiti

- **Python >= 3.13** — verifica con `python3 --version`. Su Ubuntu/Debian: `sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt install python3.13 python3.13-venv`
- **Git** — `sudo apt install git`
- **curl** — `sudo apt install curl`

### Step 1 — Clona il repository

```bash
git clone https://github.com/drake69/spendify.git spendify
cd spendify
```

### Step 2 — Installa uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc   # oppure riapri il terminale
```

### Step 3 — Installa le dipendenze

```bash
uv sync
```

### Step 4 — Configura il file .env

```bash
cp .env.example .env
# Il file .env non richiede modifiche per un'installazione locale standard
```

### Step 5 — Installa Ollama e scarica il modello

```bash
# Installa Ollama (una tantum)
curl -fsSL https://ollama.com/install.sh | sh

# Avvia il server Ollama
ollama serve &

# Scarica il modello (una tantum — ~8 GB per gemma3:12b, ~2 GB per gemma3:4b)
ollama pull gemma3:12b
# Versione più leggera per sistemi con RAM < 16 GB:
# ollama pull gemma3:4b
```

> Se hai una GPU NVIDIA, Ollama la usa automaticamente con i driver CUDA installati.

### Step 6 — Avvia l'app

```bash
./start.sh          # solo UI (default)
./start.sh api      # solo REST API
./start.sh all      # UI + API
```

L'app è disponibile su **http://localhost:8501**

### Step 7 — Configura il backend LLM nell'app

Vai su ⚙️ **Impostazioni** → sezione **Backend LLM**:
- Backend: `Ollama (locale)`
- URL: `http://localhost:11434`
- Modello: `gemma3:12b` (o il modello che hai scaricato)

### Avvio rapido successivo

```bash
ollama serve &          # se non è già in esecuzione
./start.sh
```

---

## 🐧 Linux — Docker con Ollama container

Questa configurazione avvia due container:
- **`spendify_app`** — l'applicazione web
- **`spendify_ollama`** — il server LLM Ollama

### Step 1 — Clona il repository

```bash
git clone https://github.com/drake69/spendify.git spendify
cd spendify
```

### Step 2 — Prepara il file .env

```bash
cp .env.example .env
# Nessuna modifica necessaria per l'installazione base
```

### Step 3 — (Opzionale) Abilita GPU NVIDIA

Installa il toolkit e decommenta le righe GPU in `docker-compose.yml` nella sezione `ollama`:

```bash
sudo apt-get install -y nvidia-container-toolkit
sudo systemctl restart docker
```

Poi in `docker-compose.yml` decommenta:
```yaml
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
```

### Step 4 — Avvia i container

```bash
docker compose --profile ollama up -d
```

> **Con l'installazione one-liner** (`install.sh`) il modello viene scaricato automaticamente — questo step non è necessario. Il comando sopra è per installazioni da repository.

### Step 5 — Scarica il modello (una tantum)

> **Nota:** se hai usato `install.sh` con AI locale, il download è già avviato in background dal container `ollama-init`. Controlla con: `docker compose --project-directory ~/spendify logs -f ollama-init`

```bash
# ~8 GB di download, qualche minuto di attesa
docker compose exec ollama ollama pull gemma3:12b

# Versione più leggera:
# docker compose exec ollama ollama pull gemma3:4b
```

> Il modello viene salvato nel volume Docker `spendify_ollama_models` e **persiste tra i riavvii**. Non serve riscaricarlo.

### Step 6 — Configura il backend nell'app

Vai su ⚙️ **Impostazioni** → sezione **Backend LLM**:
- Backend: `Ollama (locale)`
- URL: `http://ollama:11434` ← nome del servizio Docker, non `localhost`
- Modello: `gemma3:12b`

### Comandi utili

```bash
docker compose --profile ollama down        # stop
docker compose --profile ollama up -d       # riavvio
docker compose logs -f                      # logs
docker compose exec ollama ollama list      # modelli scaricati
```

---

## 🪟 Windows — Docker con llama.cpp server

Su Windows usiamo **llama.cpp server** come backend LLM perché funziona in Docker senza configurazioni GPU complesse ed è compatibile con l'API OpenAI (già supportata da Spendify).

### Prerequisiti Windows

- **Docker Desktop** installato e avviato (backend WSL2)
- **Git** per Windows: https://git-scm.com/download/win

### Step 1 — Clona il repository

Apri PowerShell o Git Bash:

```powershell
git clone https://github.com/drake69/spendify.git spendify
cd spendify
```

### Step 2 — Scarica il modello LLM (una tantum)

Scarica un file **GGUF** da HuggingFace. Consigliato per CPU:

| Modello | Dimensione | RAM necessaria |
|---------|-----------|----------------|
| [gemma-3-4b-it-Q4_K_M.gguf](https://huggingface.co/bartowski/gemma-3-4b-it-GGUF/resolve/main/gemma-3-4b-it-Q4_K_M.gguf) | ~2.5 GB | 6 GB |
| [gemma-3-12b-it-Q4_K_M.gguf](https://huggingface.co/bartowski/gemma-3-12b-it-GGUF/resolve/main/gemma-3-12b-it-Q4_K_M.gguf) | ~7.5 GB | 12 GB |

Crea la cartella `models` e mettici il file scaricato:

```powershell
mkdir models
# Sposta il file .gguf nella cartella models\
```

### Step 3 — Prepara il file .env

```powershell
copy .env.example .env
```

Apri `.env` e decommenta la riga `LLAMA_MODEL` con il nome del file scaricato:

```env
LLAMA_MODEL=gemma-3-4b-it-Q4_K_M.gguf
```

### Step 4 — Avvia i container

```powershell
docker compose --profile llama-cpp up -d
```

Il primo avvio richiede qualche minuto (Docker scarica le immagini).

### Step 5 — Configura il backend nell'app

Vai su ⚙️ **Impostazioni** → sezione **Backend LLM**:
- Backend: `OpenAI-compatible`
- URL: `http://llama-cpp:8080/v1` ← nome del servizio Docker, non `localhost`
- API Key: `none`
- Modello: nome del file GGUF senza `.gguf`, es. `gemma-3-4b-it-Q4_K_M`

### Comandi utili Windows

```powershell
docker compose --profile llama-cpp down     # stop
docker compose --profile llama-cpp up -d    # riavvio
docker compose logs -f                      # logs
docker compose ps                           # stato container
```

### Alternativa Windows — LM Studio (no Docker, interfaccia grafica)

1. Scarica **LM Studio**: https://lmstudio.ai
2. Nella scheda **Discover** cerca e scarica `gemma-3-4b-it`
3. Vai su **Local Server** → premi **Start Server** (porta `1234`)
4. Avvia Spendify con Docker normale (senza profile):
   ```powershell
   docker compose up -d
   ```
5. Nell'app ⚙️ Impostazioni → Backend LLM:
   - Backend: `OpenAI-compatible`
   - URL: `http://host.docker.internal:1234/v1`
   - API Key: `lm-studio`
   - Modello: `gemma-3-4b-it` (o il nome mostrato in LM Studio)

---

## 💾 Backup e ripristino del database

Il database è salvato nel volume Docker `spendify_data` e persiste tra i riavvii e gli aggiornamenti dell'app.

Per backup, ripristino, spostamento su un altro computer e ispezione diretta → **[Guida database](database.md)**.

---

## 🔁 Riepilogo comandi di avvio

| Scenario | Comando |
|----------|---------|
| Mac nativo | `./start.sh` |
| Linux nativo | `./start.sh` |
| Linux + Ollama Docker | `docker compose --profile ollama up -d` |
| Windows + llama.cpp Docker | `docker compose --profile llama-cpp up -d` |
| Windows + LM Studio esterno | `docker compose up -d` |

---

## ❓ Domande frequenti

**Il modello va riscaricato ad ogni avvio?**
No. Viene salvato una volta sola:
- Ollama nativo → `~/.ollama/models/`
- Ollama Docker → volume `spendify_ollama_models`
- llama.cpp → cartella `./models/`

**Posso cambiare modello dopo il primo avvio?**
Sì. Vai in ⚙️ Impostazioni, cambia il nome del modello e salva. Per Ollama scarica prima il modello: `ollama pull <modello>` (nativo) o `docker compose exec ollama ollama pull <modello>` (Docker).

**Posso usare OpenAI o Anthropic invece di un LLM locale?**
Sì. In ⚙️ Impostazioni seleziona `OpenAI` o `Anthropic` e inserisci la API key. Nessun container LLM necessario.

**L'URL del server LLM cambia tra installazione nativa e Docker?**
Sì:
- LLM nativo sull'host → `http://localhost:11434` (o `1234` per LM Studio)
- LLM in container Docker → usa il **nome del servizio** (`http://ollama:11434` o `http://llama-cpp:8080`)
- LLM sull'host, app in Docker → `http://host.docker.internal:11434`

**Posso fare il backup anche con l'installazione one-liner?**
Sì. L'installazione one-liner usa lo stesso volume Docker `spendify_data`. → [Guida database](database.md)

**Posso spostare i dati su un altro computer?**
Sì. → [Spostare il database](database.md#6--spostare-il-database-su-un-altro-computer)

**Come disinstallo Spendify completamente?**
Usa lo script di disinstallazione interattivo:
```bash
curl -fsSL https://raw.githubusercontent.com/drake69/spendify/main/installer/uninstall.sh | bash
# Windows:
# irm https://raw.githubusercontent.com/drake69/spendify/main/installer/uninstall.ps1 | iex
```
Lo script chiede separatamente se rimuovere: database, modelli Ollama, immagine llama.cpp + files GGUF, immagini Docker, cartella di installazione, e mostra le istruzioni per disinstallare Docker Desktop.
