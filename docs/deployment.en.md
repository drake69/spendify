# Spendify — Deployment Guide

> This document describes how to install, configure and update Spendify.
> For backup, restore and database management → [database.en.md](database.en.md).
> For installation on native Mac, Linux with Ollama and Windows with llama.cpp → [installazione.en.md](installazione.en.md).

---

## Table of contents

1. [Quick installation (one-liner Docker)](#1--quick-installation-one-liner-docker)
2. [Docker Compose installation from repository](#2--docker-compose-installation-from-repository)
3. [Native installation (development / Mac)](#3--native-installation-development--mac)
4. [`.env` configuration](#4--env-configuration)
5. [Updating the application](#5--updating-the-application)
6. [Docker operational commands](#6--docker-operational-commands)
7. [Troubleshooting](#7--troubleshooting)
8. [Uninstall](#8--uninstall)

---

## Docker concepts for beginners

| Concept | Analogy | What it means in practice |
|---------|---------|--------------------------|
| **Image** | Cooking recipe | The package containing all the code and dependencies |
| **Container** | Cooked dish | The running app, created from the image |
| **Volume** | External notebook | The persistent folder where the database lives — survives even if the container is deleted |

**What does NOT delete your data:**
- `docker compose down` ✅ safe
- `docker compose up -d --build` ✅ safe (rebuilds the image, data intact)

**What DELETES data:**
- `docker compose down -v` ⚠️ deletes the volumes — use only for a full reset

---

## 1 — Quick installation (one-liner Docker)

The only prerequisite is **[Docker Desktop](https://www.docker.com/products/docker-desktop/)** installed and running.

**Mac / Linux:**
```bash
curl -fsSL https://raw.githubusercontent.com/drake69/spendify/main/installer/install.sh | bash
```

**Windows (PowerShell):**
```powershell
irm https://raw.githubusercontent.com/drake69/spendify/main/installer/install.ps1 | iex
```

The script creates the `~/spendify/` folder, downloads the image from GitHub Container Registry, starts the container and opens the browser at **http://localhost:8501** automatically.

> **Local AI included (optional):** the script asks whether to install Ollama with the `gemma3:12b` model — downloaded automatically in the background on first start (~8 GB, ~10–15 minutes). Alternatively you can configure an external API key (OpenAI/Anthropic) from the ⚙️ Settings page.

> **Update:** `docker compose --project-directory ~/spendify pull && docker compose --project-directory ~/spendify up -d`

> **Uninstall:** `curl -fsSL https://raw.githubusercontent.com/drake69/spendify/main/installer/uninstall.sh | bash`

---

## 2 — Docker Compose installation from repository

Suitable for those who want to modify the code or configure LLM profiles (Ollama, llama.cpp).

### 2.1 — Clone the repository

```bash
git clone https://github.com/drake69/spendify.git spendify
cd spendify
```

### 2.2 — Configure the environment

```bash
cp .env.example .env
```

### 2.3 — Build and start

```bash
docker compose up -d --build
```

- `--build` forces the image to be rebuilt (required on first launch or after code updates)
- `-d` starts in the background

The app is available at **http://localhost:8501**

> The REST API is available at **http://localhost:8000** · Interactive docs: **http://localhost:8000/docs**

### 2.4 — With local LLM (optional)

```bash
# Ollama (Linux / server with GPU)
docker compose --profile ollama up -d

# llama.cpp (Windows / CPU)
docker compose --profile llama-cpp up -d
```

For complete LLM backend configuration → [installazione.en.md](installazione.en.md).

---

## 3 — Native installation (development / Mac)

### Prerequisites

| Tool | Minimum version |
|------|----------------|
| Python | 3.13 |
| uv | any — `curl -Ls https://astral.sh/uv/install.sh \| sh` |

### Steps

```bash
git clone https://github.com/drake69/spendify.git spendify
cd spendify
uv sync
cp .env.example .env
uv run streamlit run app.py
```

The app is available at **http://localhost:8501**

To also start the REST API in development:

```bash
uv run uvicorn api.main:app --host 0.0.0.0 --port 8000
```

> The `ledger.db` database is created automatically in the project folder on first launch.

---

## 4 — `.env` configuration

The `.env` file contains only two parameters. All other settings (LLM, API keys, date format, language, etc.) are configured from the interface on the **⚙️ Settings** page.

```bash
cp .env.example .env
```

| Parameter | Description | Default |
|-----------|-------------|---------|
| `SPENDIFY_DB` | SQLite database URI | `sqlite:///ledger.db` |
| `TAXONOMY_PATH` | Path to the categories YAML file | `taxonomy.yaml` |

```dotenv
SPENDIFY_DB=sqlite:///ledger.db
TAXONOMY_PATH=taxonomy.yaml

# Only for the llama-cpp profile:
# LLAMA_MODEL=gemma-3-4b-it-Q4_K_M.gguf
```

> Never add `.env` to git — verify that `.gitignore` contains the line `.env`.

---

## 5 — Updating the application

### One-liner Docker

```bash
docker compose --project-directory ~/spendify pull
docker compose --project-directory ~/spendify up -d
```

### Docker Compose from repository

```bash
git pull origin main
docker compose down
docker compose up -d --build
```

### Native

```bash
git pull origin main
uv sync
pkill -f "streamlit run app.py"
uv run streamlit run app.py
```

> Database migrations are applied automatically on startup — no manual intervention is required.

---

## 6 — Docker operational commands

```bash
# Container status
docker compose ps

# Real-time logs
docker compose logs -f spendify

# Healthcheck
docker inspect spendify_app --format='{{.State.Health.Status}}'

# Stop (data intact)
docker compose down

# Stop + remove orphan containers (data intact)
docker compose down --remove-orphans

# ⚠️  Full reset including volumes (DATA LOSS)
docker compose down -v
```

For the one-liner installation, add `--project-directory ~/spendify` to every command, e.g. `docker compose --project-directory ~/spendify logs -f`.

---

## 7 — Troubleshooting

### The app does not start / port 8501 is busy

```bash
# Check what is using the port
lsof -i :8501

# Native
pkill -f "streamlit run app.py"

# Docker
docker compose down && docker compose up -d
```

### The Docker container keeps restarting

```bash
docker compose logs --tail=50 spendify
```

Common causes:
- `.env` missing or incorrect values
- Volume not mounted correctly
- Port 8501 already in use

### Insufficient memory for Ollama

The `gemma3:12b` model requires ~8 GB of RAM. Change the model from the **⚙️ Settings** page:

| Model | Required RAM |
|-------|-------------|
| `gemma3:12b` | ~8 GB |
| `qwen2.5:7b` | ~5 GB |
| `llama3.2:3b` | ~3 GB |

### Database issues

Errors such as `database is locked`, file corruption, restore from backup → [database.en.md](database.en.md).

---

## 8 — Uninstall

The uninstall scripts interactively remove all Spendify components. **No data is deleted without explicit confirmation.**

**Mac / Linux:**
```bash
curl -fsSL https://raw.githubusercontent.com/drake69/spendify/main/installer/uninstall.sh | bash
```

**Windows (PowerShell):**
```powershell
irm https://raw.githubusercontent.com/drake69/spendify/main/installer/uninstall.ps1 | iex
```

The script asks separately for each component:
| What | Detail |
|------|--------|
| Transaction database | Volumes `spendify_data` and `spendify_logs` |
| Ollama models | Volume `ollama_models` (~8 GB) |
| llama.cpp + models/ folder | `llama.cpp:server` image + GGUF files |
| Docker images | `ghcr.io/drake69/spendify` + `ollama/ollama` (~500 MB–1 GB) |
| Installation folder | `~/spendify/` (or `SPENDIFY_INSTALL_DIR`) |
| Docker Desktop removal guide | Step-by-step guide for macOS / Linux / Windows |
