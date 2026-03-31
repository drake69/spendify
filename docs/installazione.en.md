# Spendify — Installation Guide

> Scenarios covered:
> - **Mac One-Click** — automatic installation (recommended, zero configuration)
> - **Mac** — manual native installation (maximum LLM performance with llama.cpp)
> - **Linux native** — direct installation with local llama.cpp
> - **Linux Docker** — Docker with Ollama as a separate container (alternative)
> - **Windows** — Docker with llama.cpp server as a separate container
> - **One-liner** — guided installation from scratch with Docker (AI option included, recommended for non-technical users)

---

## How LLM configuration works

> **The LLM backend, server URL, API keys and model are configured from within the app** on the ⚙️ **Settings** page — not in the `.env` file.
>
> The `.env` file contains only the database path and the taxonomy path.

The configuration is saved in the database (`user_settings`) and persists across restarts. On first launch the app uses **llama.cpp** as the default backend — no external service needed.

---

## ❓ Does the LLM model need to be downloaded before using the app?

**No, with the one-click installation the model is downloaded automatically on first launch.** The app detects your hardware (RAM, GPU) and downloads the optimal model:

| RAM | Model | Size |
|-----|-------|------|
| 4 GB | Qwen2.5-1.5B | 1.1 GB |
| 8 GB | Qwen2.5-3B | 2.1 GB |
| 12 GB | Qwen2.5-7B | 4.7 GB |
| 16+ GB | Gemma-3-12B | 6.8 GB |

> **Note:** the app works without an active LLM. Import, ledger, rules, analytics and reports are always available. If the LLM is unreachable, transactions receive the category "Other" and `to_review=True`.

---

## 🍎 Mac One-Click — Automatic installation (recommended)

**Prerequisites:** macOS 12+, Python 3.11+, internet connection

1. Download `install_spendify.command` from the [repository](https://github.com/drake69/spendify)
2. **Double-click** the file in Finder
3. The script:
   - Checks Python and installs `uv` (package manager)
   - Downloads Spendify to `~/Applications/Spendify/`
   - Installs all dependencies
   - Detects your hardware and recommends the optimal LLM model
4. On the first import, the model is downloaded automatically

**To launch Spendify every day:** double-click `Spendify.command` in `~/Applications/Spendify/packaging/macos/`

**Minimum HW:** 4 GB RAM, Apple Silicon or Intel with AVX2. Metal GPU acceleration is automatic.

---

## 🍎 Mac — Manual installation

### Why native on Mac?

llama.cpp on Mac uses **Metal (Apple Silicon)** acceleration automatically. Inside Docker this acceleration is not available → inference is 5-10x slower.

### Prerequisites

- **Python >= 3.13** — check with `python3 --version`. On macOS: `brew install python@3.13`
- **Git**

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

### Step 5 — Download the LLM model

The model is loaded directly by llama.cpp (built into Spendify) — **no external service to install**.

```bash
# Download a GGUF model (once)
# Choose based on your available RAM:

# RAM >= 16 GB (recommended):
uv run huggingface-cli download google/gemma-3-12b-it-GGUF gemma-3-12b-it-Q4_K_M.gguf \
    --local-dir ~/.spendify/models

# RAM 8 GB:
uv run huggingface-cli download Qwen/Qwen2.5-7B-Instruct-GGUF qwen2.5-7b-instruct-q4_k_m.gguf \
    --local-dir ~/.spendify/models

# RAM 4 GB:
uv run huggingface-cli download Qwen/Qwen2.5-3B-Instruct-GGUF qwen2.5-3b-instruct-q4_k_m.gguf \
    --local-dir ~/.spendify/models
```

> **The model is downloaded only once** into `~/.spendify/models/` and reused on every subsequent launch. Alternatively, you can download the model directly from the app in ⚙️ Settings → Download model.

### Step 6 — Start the app

```bash
# Startup script (recommended) — checks prerequisites, activates virtualenv and starts
./start.sh          # UI only (default)
./start.sh api      # REST API only
./start.sh all      # UI + API

# Or manually
uv run streamlit run app.py
```

The app is available at **http://localhost:8501**

### Step 7 — Verify the LLM backend in the app

Go to ⚙️ **Settings** → **LLM Backend** section:
- Backend: `llama.cpp (local, zero-config)` ← already selected by default
- Model path: the downloaded `.gguf` file is detected automatically

> **Ollama alternative:** if you prefer Ollama, install it (`brew install ollama`), download a model (`ollama pull gemma3:12b`), and select `Ollama (local)` in settings.

### Quick start for subsequent launches

```bash
./start.sh              # no service to start — llama.cpp is built in
```

---

## 🐧 Linux — Native installation

Same procedure as Mac. llama.cpp automatically supports NVIDIA GPUs (CUDA) if drivers are installed, and CPUs with AVX2. Recommended if you want to avoid Docker.

### Prerequisites

- **Python >= 3.13** — check with `python3 --version`. On Ubuntu/Debian: `sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt install python3.13 python3.13-venv`
- **Git** — `sudo apt install git`
- **curl** — `sudo apt install curl`

### Step 1 — Clone the repository

```bash
git clone https://github.com/drake69/spendify.git spendify
cd spendify
```

### Step 2 — Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc   # or reopen the terminal
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

### Step 5 — Download the LLM model

```bash
# Download a GGUF model (once — choose based on your RAM):

# RAM >= 16 GB (recommended):
uv run huggingface-cli download google/gemma-3-12b-it-GGUF gemma-3-12b-it-Q4_K_M.gguf \
    --local-dir ~/.spendify/models

# RAM 8 GB:
uv run huggingface-cli download Qwen/Qwen2.5-7B-Instruct-GGUF qwen2.5-7b-instruct-q4_k_m.gguf \
    --local-dir ~/.spendify/models

# RAM 4 GB:
uv run huggingface-cli download Qwen/Qwen2.5-3B-Instruct-GGUF qwen2.5-3b-instruct-q4_k_m.gguf \
    --local-dir ~/.spendify/models
```

> If you have an NVIDIA GPU with CUDA drivers, llama.cpp uses it automatically to accelerate inference.

### Step 6 — Start the app

```bash
./start.sh          # UI only (default)
./start.sh api      # REST API only
./start.sh all      # UI + API
```

The app is available at **http://localhost:8501**

### Step 7 — Verify the LLM backend in the app

Go to ⚙️ **Settings** → **LLM Backend** section:
- Backend: `llama.cpp (local, zero-config)` ← already selected by default
- Model path: detected automatically from `~/.spendify/models/`

> **Ollama alternative:** install with `curl -fsSL https://ollama.com/install.sh | sh`, download a model (`ollama pull gemma3:12b`), and select `Ollama (local)` in settings.

### Quick start for subsequent launches

```bash
./start.sh              # no service to start
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

> **With the one-liner installation** (`install.sh`) the model is downloaded automatically — this step is not required. The command above is for repository-based installations.

### Step 5 — Download the model (once)

> **Note:** if you used `install.sh` with local AI, the download is already running in the background via the `ollama-init` container. Check with: `docker compose --project-directory ~/spendify logs -f ollama-init`

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
| Mac native | `./start.sh` |
| Linux native | `./start.sh` |
| Linux + Ollama Docker | `docker compose --profile ollama up -d` |
| Windows + llama.cpp Docker | `docker compose --profile llama-cpp up -d` |
| Windows + external LM Studio | `docker compose up -d` |

---

## ❓ Frequently asked questions

**Does the model need to be re-downloaded on every launch?**
No. It is saved only once:
- llama.cpp (native) → `~/.spendify/models/`
- Native Ollama → `~/.ollama/models/`
- Ollama Docker → volume `spendify_ollama_models`
- llama.cpp Docker → `./models/` folder

**Can I change the model after the first launch?**
Yes. Go to ⚙️ Settings and select a different model. For llama.cpp, download the new GGUF into `~/.spendify/models/`. For Ollama: `ollama pull <model>` (native) or `docker compose exec ollama ollama pull <model>` (Docker).

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

**How do I completely uninstall Spendify?**
Use the interactive uninstall script:
```bash
curl -fsSL https://raw.githubusercontent.com/drake69/spendify/main/installer/uninstall.sh | bash
# Windows:
# irm https://raw.githubusercontent.com/drake69/spendify/main/installer/uninstall.ps1 | iex
```
The script asks separately whether to remove: database, GGUF models (`~/.spendify/models/`), Ollama models, Docker images, installation folder, and shows instructions for uninstalling Docker Desktop.
