# Contributing to Spendif.ai

*Italian version: [CONTRIBUTING.md](CONTRIBUTING.md).*

Thanks for your interest! These guidelines describe how to report issues, propose features, and contribute code.

---

## Table of contents

1. [Priority system](#priority-system)
2. [Reporting a bug](#reporting-a-bug)
3. [Proposing a feature](#proposing-a-feature)
4. [Contributing code](#contributing-code)
5. [Translating Spendif.ai](#translating-spendifai)
6. [Development environment setup](#development-environment-setup)
7. [Conventions](#conventions)

---

## Priority system

Every issue carries a priority label. The system has 4 levels:

| Label | Color | Meaning | When we use it |
|-------|-------|---------|----------------|
| 🔴 **P0 · now** | Red | Blocks the beta or is a quick UX fix | Critical bugs, regressions, pre-release quick wins |
| 🟡 **P1 · v2.5** | Yellow | Completes the core workflow | Features that close existing usage loops |
| 🔵 **P2 · v3.0** | Blue | Significant product expansion | New modules, integrations, deep improvements |
| ⚪ **P3 · roadmap** | Gray | Long-term vision | Important ideas without a defined timeline |

### Practical rules

- **P0** → open a PR within the current sprint; if you can't, flag it in the issue comments
- **P1** → target release `v2.5`; milestone assigned when the issue is picked up
- **P2** → target release `v3.0`; can be moved up if an external contribution lands
- **P3** → no timeline; contributions welcome, but quick review is not guaranteed

### How to propose a priority change

Open a comment on the issue with your reasoning. The maintainer re-evaluates and updates the label.

---

## Reporting a bug

1. Search the [open issues](https://github.com/drake69/spendify/issues) — it may already exist
2. Open a new issue using the **Bug report** template
3. Include:
   - Spendif.ai version (or commit hash)
   - OS and Docker/Python version
   - Steps to reproduce
   - Expected vs actual behavior
   - Relevant logs (`docker compose logs spendifai`)

---

## Proposing a feature

1. Check the [backlog](https://github.com/drake69/spendify/issues) — it may already exist as a P2/P3 issue
2. Open an issue using the **Feature request** template
3. Describe:
   - The problem it solves (not just the solution)
   - Who benefits (end user, developer, ops)
   - Estimated impact on privacy, performance, complexity

---

## Contributing code

### Standard flow

```
fork → branch → commit → PR → review → merge
```

1. **Fork** the repository
2. Create a branch: `git checkout -b feat/feature-name` or `fix/bug-name`
3. Develop with tests — see [Setup](#development-environment-setup)
4. Open a **Pull Request** against `main`
5. Link the PR to the issue with `Closes #N` in the body

### What to expect from review

- Feedback within ~3 business days for P0/P1
- Feedback within ~1 week for P2/P3
- The PR is merged only if CI is green (tests + lint + Docker smoke test)

---

## Translating Spendif.ai

Translations are one of the most welcome contributions: all you need is a text editor and a few minutes. You don't need to be a developer.

### Current language status

| Language | Streamlit UI | Landing page (HTML) |
|----------|--------------|---------------------|
| 🇮🇹 Italiano | ✅ master (human-verified) | ✅ master (human-verified) |
| 🇬🇧 English | ✅ LLM-translated, partially verified | ✅ LLM-translated, partially verified |
| 🇩🇪 / 🇫🇷 / 🇪🇸 / 🇵🇹 / 🇳🇱 / 🇯🇵 / 🇵🇱 | ❌ not available | ✅ LLM-translated, **not yet human-verified** |

LLM-only translations are an imperfect baseline: they all need human eyes. If you're a native speaker or have skills in one of these languages, your contribution makes a difference.

### Translation architecture

Spendif.ai has **two areas** that require separate translations:

1. **Streamlit UI** — `sw_artifacts/ui/i18n/<lang>.json` — a flat key→string file per language. Loaded at runtime by the app. The key schema follows the convention `area.element` and `area.element.desc` for descriptions (e.g. `nav.import` + `nav.import.desc`).

2. **Landing page** — `sw_artifacts/index.<lang>.html` — a complete HTML file per language. **Schema in transition**: we're migrating these to a JSON system similar to `ui/i18n/` (tracked in `backlog.json` AI-14). Until that refactor lands, landing translations are done by editing the HTML directly — see the [Landing page (transitional)](#landing-page-transitional) section below.

### Translating the Streamlit UI (recommended — the easiest flow)

#### Step 1 — Pick a language

Example: you want to add German (`de`).

#### Step 2 — Copy the master

```bash
cp sw_artifacts/ui/i18n/en.json sw_artifacts/ui/i18n/de.json
```

Start from `en.json` as the master (it's the reference baseline). Never start from `it.json` unless you're Italian: you risk linguistic calques.

#### Step 3 — Edit the file

Open `sw_artifacts/ui/i18n/de.json` with your editor of choice. The structure is simple:

```json
{
  "_language_name": "Deutsch",
  "nav.import": "📥 Importieren",
  "nav.import.desc": "CSV/XLSX-Dateien aus deinen Bankkonten importieren",
  "nav.history": "📜 Importverlauf"
}
```

Rules:

- **Change `_language_name`** to the language name in the language itself (e.g. `Deutsch`, not `German`)
- **Keep keys unchanged** — they are identifiers, not text to translate
- **Keep emoji and formatting unchanged** where present — they are part of the design
- **Keep `{var}` or `%(name)s` substitutions** — they are placeholders for dynamic values
- **`.desc` keys** are longer descriptions used as hints or tooltips — don't skip them

#### Step 4 — Recommended editors for non-developers

If you're not familiar with JSON, any of these makes life easier:

- [**BabelEdit**](https://www.codeandweb.com/babeledit) — free for open source projects. Side-by-side EN/target language UI, highlights missing keys, flags double spaces.
- [**Tolgee Cloud**](https://tolgee.io/) — side-by-side web interface with built-in AI suggestions. Free tier is sufficient.
- [**VS Code**](https://code.visualstudio.com/) with the "JSON Tools" extension — for those who just want a solid editor.
- [**JSONedit**](https://tomeko.net/software/JSONedit/) — Windows desktop, free, lightweight.

#### Step 5 — Validate and PR

```bash
# Verify that the JSON is valid
python -c "import json; json.load(open('sw_artifacts/ui/i18n/de.json'))"
```

Open a PR following the standard flow ([Contributing code](#contributing-code)).

In the PR body include:
- The language added or updated
- How many keys you translated / verified
- Any non-obvious terminology decisions (e.g. "I rendered `ledger` as `Hauptbuch` because...")

### Landing page (transitional)

**Current workflow (transitional)**: edit `sw_artifacts/index.<lang>.html` directly. Open `index.en.html` alongside as a reference, change only visible text (NOT CSS, JS, or `href` URLs), save, PR.

Practical tips:

- Keep two editors or two tabs side by side: EN on the left, target language on the right
- Search for `<h1>`, `<h2>`, `<h3>`, `<p>`, `<title>`, `<meta>` to find translatable strings
- **Do NOT translate**: `<style>`, `<script>`, `href="..."`, shell snippets in `<code-body>` (curl/irm/bash), id tags (`id="install"`), tab labels (`🍎 macOS`), proper names (Spendify, Ollama, Streamlit, GitHub)
- Update `<html lang="...">` at the top

**Future workflow (post AI-14)**: we're working to bring landings onto a JSON system similar to `ui/i18n/`. Once it's ready, contributing a language will mean editing a single `i18n/landing/<lang>.json` file instead of an entire HTML. If you want to help unblock this refactor, see backlog item AI-14.

### Human review of existing LLM translations

Even just rereading and correcting an existing translation is a valuable contribution. If you find errors, awkward-sounding phrases, or wrong technical terms, open a PR with the fixes. Note in the commit message:

```
i18n(de): fix awkward LLM phrasings in nav and analytics sections
i18n(ja): correct technical term for "ledger" — 元帳 → 取引履歴
```

---

## Development environment setup

### Prerequisites

| Tool | Minimum version |
|------|-----------------|
| Python | 3.13 |
| uv | any |
| Docker Desktop | any (for local smoke test) |

### Installation

```bash
git clone https://github.com/drake69/spendify.git
cd spendifai
uv sync
cp .env.example .env

# Startup script (recommended)
./start.sh          # UI only (default)
./start.sh api      # REST API only
./start.sh all      # UI + API

# Or manually
uv run streamlit run app.py
```

### Running tests

```bash
# Tests with coverage
uv run pytest tests/ -v --cov=. --cov-report=term-missing

# A single module
uv run pytest tests/test_normalizer.py -v

# Local Docker smoke test
docker compose -f docker/docker-compose.yml build
docker compose -f docker/docker-compose.yml up -d
curl -sf http://localhost:8501/_stcore/health
docker compose -f docker/docker-compose.yml down -v
```

### Expected coverage thresholds

| Module | Minimum coverage |
|--------|------------------|
| `core/normalizer.py` | 100% |
| `core/description_cleaner.py` | 100% |
| `core/classifier.py` | ≥ 99% |
| All others | ≥ 80% |

---

## Conventions

### Commit messages

We follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add context filtering in the ledger
fix: correct amount parsing with European separator
test: add coverage for detect_internal_transfers
docs: update installation guide for Docker arm64
refactor: extract pairing logic into a separate function
chore: remove dead code in support/core_logic.py
```

### Branch naming

```
feat/crea-regola-da-ledger
fix/encoding-windows-1252
test/phase0-preprocessing
docs/guida-installazione-en
```

### Code style

- **Formatter**: `ruff format` (configured in `pyproject.toml`)
- **Linter**: `ruff check`
- **Type hints**: required for public functions in `core/` modules
- **Decimal**: monetary amounts always use `Decimal`, never `float`

```bash
# Local check before committing
uv run ruff check .
uv run ruff format --check .
```

---

## Questions?

Open a [Discussion](https://github.com/drake69/spendify/discussions) or write in the comments of the issue closest to your case.
