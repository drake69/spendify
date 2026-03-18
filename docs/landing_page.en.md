# Spendify — Your unified bank statement, on your computer

> *Informational document — basis for web page / landing page*

---

## The problem everyone knows but nobody solves

You have three accounts: a current account, a credit card and a savings account. Every month you download three files from the bank, open them in Excel, try to paste them together — and every time you get lost among duplicates, dates, amounts with random signs, and the certainty that something doesn't add up.

Then there is the problem of all problems: **the card charge on the current account**. The supermarket purchase appears both in the card statement (as an individual transaction) and in the current account (as a monthly aggregated debit). Adding everything up, your expenses look twice as high as they really are.

Spendify solves exactly this.

---

## What Spendify is

Spendify is a personal financial register that automatically aggregates bank statements from different banks into a **single chronological ledger**, without duplicates, without sign errors, without monthly subscriptions and without sending your data to anyone.

It runs on your computer. Your data stays on your computer.

---

## How it works in three steps

### 1. Download the files from your bank
Export statements as CSV or XLSX from your bank's portal. No special steps required — Spendify automatically recognises the format.

### 2. Drag them into Spendify
Select all the files together (even from different banks, even different years) and click **Process**. Spendify:
- Automatically detects what type of document it is (current account, credit card, prepaid card, savings account)
- Corrects the sign of amounts if needed (some banks export expenses as positive numbers)
- Eliminates double-counting between card and account
- Classifies each transaction with a category label

### 3. See where your money goes
The unified ledger shows you everything in one place. With charts, filters, export, and the certainty that every euro is counted only once.

---

## The things no one else does automatically

### Card–current account reconciliation
When your credit card charges the monthly amount to the current account, Spendify recognises the relationship and **removes the double-counting** automatically. The algorithm uses a time window of ±45 days and three matching phases (sliding window + subset sum for fractional amounts).

### Internal transfer detection
A bank transfer from your current account to your savings account is neither an expense nor an income: it is an internal transfer. Spendify recognises it by comparing amounts, dates and account holder names in the descriptions — even if the two files were imported at different times.

### Idempotent deduplication
Each transaction has a unique code calculated on its content (SHA-256). If you import the same file twice, nothing happens. You can re-import your entire history without worry.

### Hybrid classification
Categorisation uses a four-level cascade system:
1. **Your rules** — patterns you have defined yourself (e.g.: "CONAD" → Food / Supermarkets)
2. **Static regex** — predefined patterns for the most common categories
3. **LLM** — for everything else, the language model assigns a category and subcategory
4. **Fallback** — if everything else fails, the transaction is placed in "To review"

---

## Privacy: your data stays yours

### Offline-first mode
By default, Spendify uses **Ollama locally**: an AI engine that runs on your computer, without an internet connection. Your bank statements never leave your disk.

### If you want to use OpenAI or Claude
You can, but Spendify first **automatically removes** all identifying data:
- IBAN → `<ACCOUNT_ID>`
- Card numbers → `<CARD_ID>`
- Tax identification number → `<FISCAL_ID>`
- Your name → a fictional name

Only after sanitisation is the text sent. If for any reason the check fails, the call is blocked — not silently degraded.

### No cloud, no subscription
The data is in a SQLite database on your computer. You can copy it, move it, back it up like any other file.

---

## Who Spendify is for

### For those with multiple accounts at different banks
If you use more than one bank account (current account + credit card + savings account + trading account) you know how difficult it is to have a unified view. Spendify does exactly this — without having to do anything manually.

### For those who take their finances seriously
If you use Excel to track expenses, Spendify can replace that routine: you import the files once, Spendify unifies and classifies them, you check and correct only the exceptions.

### For those who do not trust the cloud
Spendify has no mandatory remote backends, does not require an account, and sends nothing to anyone by default. Your banking data stays where it belongs.

### For developers
Spendify is an open source Python project with a modular architecture and a complete test suite. It is an interesting starting point for those who want to:
- Experiment with LLM pipelines on structured data
- Build integrations with specific banks
- Extend the data model or taxonomy
- Deploy on a server for family or small team use

---

## Main features

| | |
|---|---|
| **Multi-bank import** | CSV and XLSX from any Italian bank (and beyond) |
| **Auto-detect format** | No manual configuration for each file type |
| **Unified ledger** | All transactions in chronological order, filterable by date / account / category / context |
| **Automatic classification** | 15 expense categories + 7 income categories with subcategories |
| **Customisable taxonomy** | Add / modify categories and subcategories without restarting the app |
| **Deterministic rules** | Create "ESSELUNGA always → Food" rules applied retroactively |
| **Interactive analytics** | 7 Plotly charts: monthly trend, cumulative balance, expense pie chart by category, subcategory drill-down, top 10 merchants |
| **Life contexts** | A dimension orthogonal to category: segment expenses by Work / Holiday / Daily life |
| **Check List** | Month × account pivot table: see at a glance which months you have not yet imported |
| **Export** | Standalone HTML (with charts), CSV, XLSX |
| **Configurable LLM** | Local Ollama, OpenAI, Claude, Groq, Google AI Studio, LM Studio, any compatible API |
| **PII sanitisation** | Automatic IBAN / PAN / tax ID / name protection before any remote call |

---

## Technology stack

Spendify is built on mature technologies with no exotic dependencies:

- **Python 3.13** + **pandas** for the data pipeline
- **Streamlit** for the web interface (accessible from the browser, without installing anything special)
- **SQLite + SQLAlchemy** for persistence (a single, portable file)
- **Pydantic v2** for schema validation
- **Plotly** for charts
- **uv** as package manager (installation in ~30 seconds)

No LLM framework (no LangChain) — the AI backends use the official SDKs directly, with a common abstract interface.

---

## Installation in one command

The only prerequisite is **[Docker Desktop](https://www.docker.com/products/docker-desktop/)**.

**Mac / Linux:**
```bash
curl -fsSL https://raw.githubusercontent.com/drake69/spendify/main/installer/install.sh | bash
```

**Windows (PowerShell):**
```powershell
irm https://raw.githubusercontent.com/drake69/spendify/main/installer/install.ps1 | iex
```

The script downloads the image from GitHub Container Registry, starts the container and opens the browser at `http://localhost:8501` automatically.

Go to **Settings**, enter your name, add your accounts — and import.

> For native installation (Mac with Ollama) or Linux/Windows server with Docker+local LLM → [Full guide](installazione.en.md).

---

## For developers: what's inside

### Modular pipeline
```
CSV/XLSX file
    → Schema classifier  (column SHA-256 fingerprint → LLM if new schema)
    → Normaliser          (encoding, parse_amount Decimal, SHA-256 tx_id)
    → RF-03 Reconciler    (card–account, 3 phases)
    → RF-04 Detector      (internal transfers, symbolic matching)
    → Description cleaner (LLM, noise → canonical text)
    → Categoriser         (rules → regex → LLM → fallback)
    → Database            (SQLAlchemy, idempotent)
```

### Extensibility
- **New LLM backends**: implement `LLMBackend` (3 methods) and register it in `BackendFactory`
- **New bank formats**: Flow 2 recognises them automatically via LLM without code changes; the schema is saved and reused in subsequent imports
- **New categories**: from the Taxonomy page, without touching the code
- **REST API**: the `process_file()` pipeline is completely separate from the UI — it can be exposed via FastAPI without modifications

### Test suite
```bash
uv run pytest tests/ -v          # 184 tests, zero external dependencies
uv run pytest tests/ --cov=core  # coverage of the business logic layer
```

All tests use SQLite in-memory: no files, no external services, no LLM mock required for the base suite.

---

## Roadmap

- [ ] PDF support (native parsing of bank PDF statements)
- [ ] Monthly budget per category with alerting
- [ ] Automatic import via Open Banking (PSD2)
- [ ] REST API for external integrations
- [ ] Multi-user version with authentication
- [ ] Mobile app (ledger and analytics viewing)

---

## Contributing

Spendify is open source. The areas where contributions are most useful:

- **New bank formats** — if your bank is not recognised automatically, you can open an issue with an anonymised sample
- **Tests** — the suite covers the business logic layer but not yet the UI
- **Internationalisation** — the architecture already supports multiple languages for descriptions; the UI is in Italian
- **Performance** — batch categorisation is the bottleneck with a local LLM; there is room for parallelisation

---

*Spendify is not a cloud service. It is a program that runs on your computer. Your banking data is never sent to third-party servers, unless you explicitly choose a remote LLM backend — in that case, all identifying data is removed before sending.*
