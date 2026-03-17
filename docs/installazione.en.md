# Spendify — Installation Guide

> Three scenarios covered:
> - **Mac** — native installation (recommended, maximum LLM performance)
> - **Linux** — Docker with Ollama as a separate container
> - **Windows** — Docker with llama.cpp server as a separate container

---

## How LLM configuration works

> **The LLM backend, server URL, API keys and model are configured from within the app** on the ⚙️ **Settings** page — not in the `.env` file.
>
> The `.env` file contains only the database path and the taxonomy path.

The configuration is saved in the database (`user_settings`) and persists across restarts. On first launch the app uses Ollama on `localhost:11434` as default.

---

## ❓ Does the LLM model need to be downloaded before using the app?

**Yes, once.** The app does not download models automatically — the LLM server must have the model available before starting operations that require it (categorisation, description cleaning).

> **Note:** the app works without an active LLM. Import, ledger, rules, analytics and reports are always available. If the LLM is unreachable, transactions receive the category "Other" and `to_review=True`.

---

## 🍎 Mac — Native installation

### Why native on Mac?

Ollama on Mac uses **Metal (Apple Silicon)** or **OpenCL (Intel)** acceleration. Inside Docker this acceleration is not available → inference is 5-10x slower.

### Step 1 — Clone the repository

```bash
git clone https://github.com/drake69/spendify.git spendify
cd spendify
```

### Step 2 — Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.zshrc   # or reopen the terminal
```

### Step 3 — Install dependencies

```bash
uv sync
```

### Step 4 — Configure the .env file

```bash
cp .env.example .env
# The .env file requires no changes for a standard local installation
```

### Step 5 — Install Ollama and download the model

```bash
# Install Ollama (once)
brew install ollama
# or download from https://ollama.com

# Start the Ollama server in the background
ollama serve &

# Download the model (once — ~8 GB for gemma3:12b, ~2 GB for gemma3:4b)
ollama pull gemma3:12b
# Lighter version for Macs with < 16 GB RAM:
# ollama pull gemma3:4b
```

> **The model is downloaded only once** into `~/.ollama/models/` and reused on every subsequent launch.

### Step 6 — Start the app

```bash
uv run streamlit run app.py
```

The app is available at **http://localhost:8501**

### Step 7 — Configure the LLM backend in the app

Go to ⚙️ **Settings** → **LLM Backend** section:
- Backend: `Ollama (local)`
- URL: `http://localhost:11434`
- Model: `gemma3:12b` (or the model you downloaded)

### Quick start for subsequent launches

```bash
ollama serve &          # if not already running
uv run streamlit run app.py
```

---

## 🐧 Linux — Docker with Ollama container

This configuration starts two containers:
- **`spendify_app`** — the web application
- **`spendify_ollama`** — the Ollama LLM server

### Step 1 — Clone the repository

```bash
git clone https://github.com/drake69/spendify.git spendify
cd spendify
```

### Step 2 — Prepare the .env file

```bash
cp .env.example .env
# No changes needed for the base installation
```

### Step 3 — (Optional) Enable NVIDIA GPU

Install the toolkit and uncomment the GPU lines in `docker-compose.yml` under the `ollama` section:

```bash
sudo apt-get install -y nvidia-container-toolkit
sudo systemctl restart docker
```

Then in `docker-compose.yml` uncomment:
```yaml
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
```

### Step 4 — Start the containers

```bash
docker compose --profile ollama up -d
```

### Step 5 — Download the model (once)

```bash
# ~8 GB download, a few minutes of waiting
docker compose exec ollama ollama pull gemma3:12b

# Lighter version:
# docker compose exec ollama ollama pull gemma3:4b
```

> The model is saved in the Docker volume `spendify_ollama_models` and **persists across restarts**. No need to download it again.

### Step 6 — Configure the backend in the app

Go to ⚙️ **Settings** → **LLM Backend** section:
- Backend: `Ollama (local)`
- URL: `http://ollama:11434` ← Docker service name, not `localhost`
- Model: `gemma3:12b`

### Useful commands

```bash
docker compose --profile ollama down        # stop
docker compose --profile ollama up -d       # restart
docker compose logs -f                      # logs
docker compose exec ollama ollama list      # downloaded models
```

---

## 🪟 Windows — Docker with llama.cpp server

On Windows we use **llama.cpp server** as the LLM backend because it works in Docker without complex GPU configuration and is compatible with the OpenAI API (already supported by Spendify).

### Windows prerequisites

- **Docker Desktop** installed and running (WSL2 backend)
- **Git** for Windows: https://git-scm.com/download/win

### Step 1 — Clone the repository

Open PowerShell or Git Bash:

```powershell
git clone https://github.com/drake69/spendify.git spendify
cd spendify
```

### Step 2 — Download the LLM model (once)

Download a **GGUF** file from HuggingFace. Recommended for CPU:

| Model | Size | Required RAM |
|-------|------|-------------|
| [gemma-3-4b-it-Q4_K_M.gguf](https://huggingface.co/bartowski/gemma-3-4b-it-GGUF/resolve/main/gemma-3-4b-it-Q4_K_M.gguf) | ~2.5 GB | 6 GB |
| [gemma-3-12b-it-Q4_K_M.gguf](https://huggingface.co/bartowski/gemma-3-12b-it-GGUF/resolve/main/gemma-3-12b-it-Q4_K_M.gguf) | ~7.5 GB | 12 GB |

Create the `models` folder and place the downloaded file there:

```powershell
mkdir models
# Move the .gguf file into the models\ folder
```

### Step 3 — Prepare the .env file

```powershell
copy .env.example .env
```

Open `.env` and uncomment the `LLAMA_MODEL` line with the name of the downloaded file:

```env
LLAMA_MODEL=gemma-3-4b-it-Q4_K_M.gguf
```

### Step 4 — Start the containers

```powershell
docker compose --profile llama-cpp up -d
```

The first launch takes a few minutes (Docker downloads the images).

### Step 5 — Configure the backend in the app

Go to ⚙️ **Settings** → **LLM Backend** section:
- Backend: `OpenAI-compatible`
- URL: `http://llama-cpp:8080/v1` ← Docker service name, not `localhost`
- API Key: `none`
- Model: GGUF filename without `.gguf`, e.g. `gemma-3-4b-it-Q4_K_M`

### Useful Windows commands

```powershell
docker compose --profile llama-cpp down     # stop
docker compose --profile llama-cpp up -d    # restart
docker compose logs -f                      # logs
docker compose ps                           # container status
```

### Windows alternative — LM Studio (no Docker, graphical interface)

1. Download **LM Studio**: https://lmstudio.ai
2. In the **Discover** tab search for and download `gemma-3-4b-it`
3. Go to **Local Server** → press **Start Server** (port `1234`)
4. Start Spendify with standard Docker (without profile):
   ```powershell
   docker compose up -d
   ```
5. In the app ⚙️ Settings → LLM Backend:
   - Backend: `OpenAI-compatible`
   - URL: `http://host.docker.internal:1234/v1`
   - API Key: `lm-studio`
   - Model: `gemma-3-4b-it` (or the name shown in LM Studio)

---

## 💾 Database backup and restore

The database is saved in the Docker volume `spendify_data` and persists across restarts and app updates.

For backup, restore, moving to another computer and direct inspection → **[Database guide](database.en.md)**.

---

## 🔁 Start command summary

| Scenario | Command |
|----------|---------|
| Native Mac | `uv run streamlit run app.py` |
| Linux + Ollama Docker | `docker compose --profile ollama up -d` |
| Windows + llama.cpp Docker | `docker compose --profile llama-cpp up -d` |
| Windows + external LM Studio | `docker compose up -d` |

---

## ❓ Frequently asked questions

**Does the model need to be re-downloaded on every launch?**
No. It is saved only once:
- Native Ollama → `~/.ollama/models/`
- Ollama Docker → volume `spendify_ollama_models`
- llama.cpp → `./models/` folder

**Can I change the model after the first launch?**
Yes. Go to ⚙️ Settings, change the model name and save. For Ollama, download the model first: `ollama pull <model>` (native) or `docker compose exec ollama ollama pull <model>` (Docker).

**Can I use OpenAI or Anthropic instead of a local LLM?**
Yes. In ⚙️ Settings select `OpenAI` or `Anthropic` and enter the API key. No LLM container required.

**Does the LLM server URL change between native and Docker installation?**
Yes:
- LLM running natively on the host → `http://localhost:11434` (or `1234` for LM Studio)
- LLM in a Docker container → use the **service name** (`http://ollama:11434` or `http://llama-cpp:8080`)
- LLM on the host, app in Docker → `http://host.docker.internal:11434`

**Can I make a backup with the one-liner installation too?**
Yes. The one-liner installation uses the same Docker volume `spendify_data`. → [Database guide](database.en.md)

**Can I move my data to another computer?**
Yes. → [Moving the database](database.en.md#6--moving-the-database-to-another-computer)
