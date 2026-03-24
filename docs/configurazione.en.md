# Spendify — Configuration Manual

> Complete reference for all settings available on the **⚙️ Impostazioni** page.
> Settings are persisted in the database (`ledger.db`) and take effect immediately on the next save.

---

## Table of contents

1. [Mandatory initial configuration](#1-mandatory-initial-configuration)
2. [Bank accounts](#2-bank-accounts)
3. [Account holders](#3-account-holders)
4. [Display format](#4-display-format)
5. [Description language](#5-description-language)
6. [Giroconti mode](#6-giroconti-mode)
7. [Life contexts](#7-life-contexts)
8. [Import test mode](#8-import-test-mode)
9. [LLM backend](#9-llm-backend)
   - [Ollama (local)](#91-ollama-local)
   - [OpenAI](#92-openai)
   - [Claude (Anthropic)](#93-claude-anthropic)
   - [OpenAI-compatible](#94-openai-compatible-groq-together-ai-etc)

---

## 1. Initial configuration — onboarding wizard

On first launch the app automatically shows the **onboarding wizard** (4 steps). There is no need to navigate to Impostazioni before importing: the wizard collects the minimum required data and writes it to the DB in a single atomic operation when you click "Inizia!".

| Step | What is configured |
|---|---|
| **1 — Language** | Taxonomy language (Italian, English, French, German, Spanish). Pre-selected from the browser language. Also sets date format and number separators. |
| **2 — Holders** | Account holder names (required). Used for PII redaction and internal transfer detection. |
| **3 — Accounts** | Bank accounts (name + bank). Optional: can be skipped and added later from Impostazioni. |
| **4 — Confirm** | Summary and "Inizia!" button — only here is data written to the DB. |

> **Updating from a previous version?** The wizard is skipped automatically if the database already contains data — the app opens normally.

After the wizard you can refine any setting from the **⚙️ Impostazioni** page at any time.

---

## 2. Bank accounts

**Path:** Impostazioni → 🏦 Conti bancari

Defines the current accounts, cards, and deposit accounts you own. Each account has:

| Field | Required | Description |
|---|---|---|
| **Account name** | ✅ Yes | Unique identifier (e.g. `Conto corrente BPER`, `Carta Visa BNL`) |
| **Bank** | No | Bank name for reference (does not affect processing) |

### Why define accounts

- On the Import page you can **associate each file with a specific account** instead of relying on automatic detection.
- The account name is saved with each transaction (`account_label`) and is the key used for the **Check List** (month × account pivot).
- Improves **deduplication**: transactions from the same account imported in different sessions are correctly recognised.

### Operational notes

- You can import without defined accounts, but automatic detection may assign different names to the same account in successive imports.
- Delete an account only if it has no associated transactions; otherwise existing transactions will retain the old `account_label`.

---

## 3. Account holders

**Path:** Impostazioni → 👤 Titolari del conto

### Field: Account holder names

List of account holder names, separated by commas.

```
Mario Rossi, Anna Bianchi
```

**Uses:**

1. **PII sanitisation** — Names are replaced with fictional aliases (e.g. `Carlo Brambilla`) before sending any text to remote LLM backends (OpenAI, Claude). The original data in the database is never modified.

2. **Transfer detection** — If you enable the *Usa nomi titolari per identificare giroconti* toggle, transactions whose description contains an account holder name are automatically marked as a transfer (🔄).

### Toggle: Use account holder names for transfers

| State | Behaviour |
|---|---|
| **Active** | Transfers with your name in the description → marked 🔄 as a transfer |
| **Inactive** | Transfer detection based on amount/date/account only (RF-04 Phase 1) |

> **Tip:** Also enter the name variants used by banks (surname-name, uppercase, without accents). Example: `Mario Rossi, ROSSI MARIO, Rossi M.`

---

## 4. Display format

**Path:** Impostazioni → Formato visualizzazione

Controls how dates and amounts are shown in Ledger, Analytics, and Review. Does not affect the database (which always uses ISO 8601 and Numeric).

### Date format

| Option | Example | Notes |
|---|---|---|
| `dd/mm/yyyy` | 31/12/2025 | **Default** — Italian standard |
| `yyyy-mm-dd` | 2025-12-31 | ISO 8601, suitable for export/CSV |
| `mm/dd/yyyy` | 12/31/2025 | US standard |

### Numeric separators

| Setting | Options | Default |
|---|---|---|
| **Decimal separator** | `,` (Italian/European) · `.` (English/US) | `,` |
| **Thousands separator** | `.` (Italian) · `,` (English) · ` ` (French) · none | `.` |

The page shows a **real-time preview** (e.g. `1.234,56 €`) before saving.

---

## 5. Description language

**Path:** Impostazioni → Lingua delle descrizioni

| Option | Code |
|---|---|
| Italiano | `it` |
| English | `en` |
| Français | `fr` |
| Deutsch | `de` |

This is passed to the LLM categoriser prompt to help it correctly interpret transaction descriptions. If your statements are in Italian, leave `it` (default).

> **Example:** A description like `"PAGAMENTO POS CONAD"` is interpreted differently by a model prompted in Italian compared to one prompted in English.

---

## 6. Giroconti mode

**Path:** Impostazioni → 🔄 Modalità Giroconti

Internal transfers (giroconti) between your own accounts are **always detected and always saved** to the database, regardless of the selected mode. This ensures reconciliation and data integrity. The mode controls **only the visibility** in views (Ledger, Analytics, Reports).

| Mode | Behaviour in views (Ledger, Analytics, Reports) |
|---|---|
| **Show (neutral)** | 🔄 rows are visible (grey/neutral), excluded from income/expense totals |
| **Exclude from views** | 🔄 rows do not appear in the UI (but remain in the database) |

The mode applies globally. You can override it for a single view using the *Nascondi giroconti* checkbox in Ledger.

> **Technical note:** internal transfers are marked as `internal_in`/`internal_out` in the ledger. Even in "Exclude" mode, they remain available for reconciliation and audit.

---

## 7. Life contexts

**Path:** Impostazioni → 🌍 Contesti di vita

Free list of labels to segment expenses by context (e.g. `Quotidianità`, `Lavoro`, `Vacanza`).

- Add/remove contexts freely
- Assign a context to each transaction from the **Ledger** (Context column)
- Use the Context filter in Analytics to compare periods (e.g. "how much did I spend on holiday vs daily life?")

**Default:** `Quotidianità`, `Lavoro`, `Vacanza`

---

## 8. Import test mode

**Path:** Impostazioni → 📥 Importazione

| Toggle | Behaviour |
|---|---|
| **Inactive** (default) | Processes all rows in the file |
| **Active** | Processes only the first **20 rows** per file |

Useful for:
- Verifying that the file format is correctly recognised before a full import
- Testing LLM classification on a sample without waiting for complete processing
- Debugging new bank formats

> ⚠️ Remember to deactivate it before the final import.

---

## 9. LLM backend

**Path:** Impostazioni → 🤖 Configurazione LLM

The LLM backend is used for:
- **Category classification** — assigns category/subcategory to each transaction
- **Counterpart extraction** — normalises the bank's raw description

| Backend | Privacy | Cost | Speed | Quality |
|---|---|---|---|---|
| **Ollama (local)** | ✅ Total | ✅ Free | ⚡ Depends on hardware | Good (with gemma3:12b) |
| **OpenAI** | ⚠️ PII redacted | 💰 Pay-per-use | ⚡⚡ High | High |
| **Claude (Anthropic)** | ⚠️ PII redacted | 💰 Pay-per-use | ⚡⚡ High | High |
| **OpenAI-compatible** | ⚠️ PII redacted | Varies | Varies | Varies |

**Circuit breaker:** If the configured backend does not respond, Spendify automatically falls back to local Ollama. If Ollama is also offline, the transaction is imported with `to_review=True` and the raw description.

---

### 9.1 Ollama (local)

The best choice for **total privacy**: no data leaves your computer.

**Installation (one-time):**

```bash
# macOS
brew install ollama

# Linux
curl -fsSL https://ollama.ai/install.sh | sh

# Windows
# Download the installer from https://ollama.ai/download
```

**Download the model:**
```bash
ollama pull gemma3:12b        # recommended (~8 GB)
ollama pull llama3.2:3b       # lightweight (~2 GB), lower quality
```

**Verify it is working:**
```bash
ollama list                   # shows downloaded models
curl http://localhost:11434   # should respond "Ollama is running"
```

| Field | Default | Description |
|---|---|---|
| **Ollama server URL** | `http://localhost:11434` | Change only if Ollama is running on a different host or in Docker |
| **Model** | `gemma3:12b` | Must match the output of `ollama list` exactly |

**Ollama on Docker** (example):
```yaml
# docker-compose.yml
services:
  ollama:
    image: ollama/ollama
    ports:
      - "11434:11434"
    volumes:
      - ollama_data:/root/.ollama
```
In this case set URL: `http://localhost:11434` (or the container's IP if Spendify is itself in Docker).

**Recommended models for categorisation quality:**

| Model | RAM required | Notes |
|---|---|---|
| `gemma3:12b` | ~8 GB | ✅ Recommended — excellent for Italian, fast on Apple Silicon |
| `qwen2.5:14b` | ~10 GB | Excellent multilingual quality, slower |
| `mistral:7b` | ~5 GB | Solid alternative, multilingual |
| `llama3.2:3b` | ~3 GB | Very fast, sufficient quality for simple categories |

> **Apple Silicon tip:** Models run on the integrated GPU (Metal) — gemma3:12b processes ~15–20 transactions per second on M2/M3.

---

### 9.2 OpenAI

**Where to register:** https://platform.openai.com

**How to get the API Key:**
1. Log in at https://platform.openai.com
2. Top-right menu → **API keys**
3. Click **+ Create new secret key**
4. Give it a name (e.g. `Spendify`) and copy the key — **shown only once**
5. Make sure you have credit in your account (the *Billing* section)

**Configuration in Spendify:**
```
Backend LLM:  OpenAI
API Key:      sk-proj-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
Model:        gpt-4o-mini
```

**Available models and indicative costs (March 2026):**

| Model | Input ($/1M tokens) | Output ($/1M tokens) | Notes |
|---|---|---|---|
| `gpt-4o-mini` | $0.15 | $0.60 | ✅ Recommended — excellent value for money |
| `gpt-4o` | $2.50 | $10.00 | High quality, ~15× higher cost |
| `gpt-4.1-mini` | $0.40 | $1.60 | More recent economical alternative |

> **Cost estimate:** 1000 transactions ≈ ~100k total tokens ≈ **$0.015** with gpt-4o-mini.

> **Privacy:** IBANs, card numbers, tax identification numbers, and account holder names are replaced with placeholders before sending. The text sent to OpenAI never contains identifying data.

---

### 9.3 Claude (Anthropic)

**Where to register:** https://console.anthropic.com

**How to get the API Key:**
1. Log in at https://console.anthropic.com
2. **API Keys** section (side menu)
3. Click **Create Key**
4. Give it a name (e.g. `Spendify`) and copy the key
5. Add credit in **Billing → Add Credits** (minimum $5)

**Configuration in Spendify:**
```
Backend LLM:  Claude (Anthropic)
API Key:      sk-ant-api03-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
Model:        claude-3-5-haiku-20241022
```

**Available models:**

| Model | Speed | Quality | Notes |
|---|---|---|---|
| `claude-3-5-haiku-20241022` | ⚡⚡⚡ | ⭐⭐⭐⭐ | ✅ Recommended — fast, economical, excellent quality |
| `claude-3-5-sonnet-20241022` | ⚡⚡ | ⭐⭐⭐⭐⭐ | Superior quality for ambiguous descriptions |
| `claude-opus-4-5` | ⚡ | ⭐⭐⭐⭐⭐ | Maximum quality, high cost |

> **Privacy:** same guarantees as OpenAI — PII redacted before sending.

---

### 9.4 OpenAI-compatible (Groq, Together AI, etc.)

Compatible with any API that exposes the `/v1/chat/completions` endpoint in the OpenAI format.

| Field | Example | Description |
|---|---|---|
| **Base URL** | `https://api.groq.com/openai/v1` | Provider base URL (without `/chat/completions`) |
| **API Key** | `gsk_...` | Provider key |
| **Model** | `gemma2-9b-it` | Exact model name as required by the provider |

---

#### Groq (recommended for those who want free + fast)

**Where to register:** https://console.groq.com

**How to get the API Key:**
1. Register at https://console.groq.com (free)
2. Side menu → **API Keys** → **Create API Key**
3. Copy the key (prefix `gsk_`)

**Configuration:**
```
Backend LLM:  OpenAI-compatible
Base URL:     https://api.groq.com/openai/v1
API Key:      gsk_xxxxxxxxxxxxxxxxxxxxxxxxxxxx
Model:        gemma2-9b-it
```

**Groq models useful for Spendify:**

| Model | Notes |
|---|---|
| `gemma2-9b-it` | ✅ Recommended — excellent for Italian, very fast |
| `llama-3.3-70b-versatile` | High quality, slightly slower |
| `llama-3.1-8b-instant` | Very fast, good quality |

> **Free tier:** ~14,400 requests/day, sufficient for personal use. Limit of 6000 tokens/minute.

---

#### Together AI

**Where to register:** https://api.together.ai

**How to get the API Key:**
1. Register and log in at https://api.together.ai
2. Go to **Settings → API Keys**
3. Create a new key

**Configuration:**
```
Backend LLM:  OpenAI-compatible
Base URL:     https://api.together.xyz/v1
API Key:      <your_api_key>
Model:        meta-llama/Llama-3.2-11B-Vision-Instruct-Turbo
```

---

#### Google AI Studio (Gemini)

**Where to register:** https://aistudio.google.com

**How to get the API Key:**
1. Go to https://aistudio.google.com
2. Click **Get API key** at the top right
3. **Create API key** → select or create a Google Cloud project

**Configuration:**
```
Backend LLM:  OpenAI-compatible
Base URL:     https://generativelanguage.googleapis.com/v1beta/openai
API Key:      AIza...
Model:        gemini-2.0-flash
```

**Gemini models:**

| Model | Notes |
|---|---|
| `gemini-2.0-flash` | ✅ Recommended — fast, high quality, generous free tier |
| `gemini-1.5-flash` | Economical alternative |

> **Free tier:** 1500 requests/day with gemini-2.0-flash — more than sufficient for personal use.

---

#### LM Studio (local alternative to Ollama)

LM Studio is a desktop app (macOS, Windows, Linux) for running models locally with a graphical interface.

**Download:** https://lmstudio.ai

**Configuration:**
1. Download and install LM Studio
2. Download a model from the *Discover* section
3. Start the local server: **Local Server** → **Start Server**
4. In Spendify:

```
Backend LLM:  OpenAI-compatible
Base URL:     http://localhost:1234/v1
API Key:      lm-studio   (any string, not verified)
Model:        (copy the exact name from the LM Studio panel)
```

---

## Default values

On first installation the database is initialised with these values:

| Key | Default | Description |
|---|---|---|
| `date_display_format` | `%d/%m/%Y` | Italian format `dd/mm/yyyy` |
| `amount_decimal_sep` | `,` | Italian decimal separator |
| `amount_thousands_sep` | `.` | Italian thousands separator |
| `description_language` | `it` | Italian |
| `giroconto_mode` | `neutral` | Transfers visible but neutral |
| `llm_backend` | `local_ollama` | Local Ollama |
| `ollama_base_url` | `http://localhost:11434` | Default Ollama port |
| `ollama_model` | `gemma3:12b` | Recommended model |
| `openai_model` | `gpt-4o-mini` | Economical OpenAI model |
| `anthropic_model` | `claude-3-5-haiku-20241022` | Economical Claude model |
| `import_test_mode` | `false` | Full import |
| `owner_names` | *(empty)* | **Must be configured before the first import** |
| `contexts` | `["Quotidianità","Lavoro","Vacanza"]` | Default contexts |

---

## Initial configuration checklist

```
[ ] 1. Launch the app → the onboarding wizard appears automatically
[ ] 2. Step 1: choose the taxonomy language
[ ] 3. Step 2: enter your name (and variants used by the bank)
[ ] 4. Step 3: add your bank accounts (or skip and add later)
[ ] 5. Step 4: click "Inizia!" to complete the setup
[ ] 6. (Optional) Go to ⚙️ Impostazioni → configure the LLM backend
[ ] 7. Go to 📥 Import and load the first bank statement
```
