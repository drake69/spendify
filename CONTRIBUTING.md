# Contribuire a Spendif.ai

Grazie per l'interesse! Queste linee guida descrivono come segnalare problemi, proporre funzionalità e contribuire codice.

---

## Indice

1. [Sistema di priorità](#sistema-di-priorità)
2. [Segnalare un bug](#segnalare-un-bug)
3. [Proporre una funzionalità](#proporre-una-funzionalità)
4. [Contribuire codice](#contribuire-codice)
5. [Tradurre Spendif.ai](#tradurre-spendifai)
6. [Setup ambiente di sviluppo](#setup-ambiente-di-sviluppo)
7. [Convenzioni](#convenzioni)

---

## Sistema di priorità

Ogni issue ha una label di priorità. Il sistema è a 4 livelli:

| Label | Colore | Significato | Quando la usiamo |
|-------|--------|-------------|-----------------|
| 🔴 **P0 · ora** | Rosso | Blocca il beta o è un fix UX rapido | Bug critici, regressioni, quick win pre-release |
| 🟡 **P1 · v2.5** | Giallo | Completa il core workflow | Funzionalità che chiudono loop di utilizzo esistenti |
| 🔵 **P2 · v3.0** | Blu | Espansione significativa del prodotto | Nuovi moduli, integrazioni, miglioramenti profondi |
| ⚪ **P3 · roadmap** | Grigio | Visione a lungo termine | Idee importanti ma senza timeline definita |

### Regole pratiche

- **P0** → apri una PR entro la sprint corrente; se non puoi, segnalalo nel commento dell'issue
- **P1** → target release `v2.5`; milestone assegnata quando la issue viene presa in carico
- **P2** → target release `v3.0`; può essere anticipata se arriva un contributo esterno
- **P3** → nessuna timeline; benvenuti i contributi, ma non è garantita la review rapida

### Come proporre un cambio di priorità

Apri un commento sull'issue con la motivazione. Il maintainer rivaluta e aggiorna la label.

---

## Segnalare un bug

1. Cerca nelle [issue aperte](https://github.com/drake69/spendify/issues) — potrebbe esistere già
2. Apri una nuova issue con il template **Bug report**
3. Includi:
   - Versione Spendif.ai (o commit hash)
   - OS e versione Docker/Python
   - Passi per riprodurre
   - Comportamento atteso vs effettivo
   - Log rilevanti (`docker compose logs spendifai`)

---

## Proporre una funzionalità

1. Controlla il [backlog](https://github.com/drake69/spendify/issues) — potrebbe esistere già come issue P2/P3
2. Apri una issue con il template **Feature request**
3. Descrivi:
   - Il problema che risolve (non solo la soluzione)
   - Chi ne beneficia (utente finale, sviluppatore, ops)
   - Impatto stimato su privacy, performance, complessità

---

## Contribuire codice

### Flusso standard

```
fork → branch → commit → PR → review → merge
```

1. **Fork** del repository
2. Crea un branch: `git checkout -b feat/nome-feature` o `fix/nome-bug`
3. Sviluppa con test — vedi [Setup](#setup-ambiente-di-sviluppo)
4. Apri una **Pull Request** verso `main`
5. Collega la PR all'issue con `Closes #N` nel body

### Cosa aspettarsi dalla review

- Feedback entro ~3 giorni lavorativi per P0/P1
- Feedback entro ~1 settimana per P2/P3
- La PR viene mergiata solo se la CI è verde (test + lint + Docker smoke test)

---

## Tradurre Spendif.ai

Le traduzioni sono uno dei contributi più graditi: ti bastano un editor di testo e qualche minuto. Non serve essere developer.

### Stato attuale delle lingue

| Lingua | UI Streamlit | Landing page (HTML) |
|--------|--------------|---------------------|
| 🇮🇹 Italiano | ✅ master (verifica umana) | ✅ master (verifica umana) |
| 🇬🇧 English | ✅ tradotta da LLM, parzialmente verificata | ✅ tradotta da LLM, parzialmente verificata |
| 🇩🇪 / 🇫🇷 / 🇪🇸 / 🇵🇹 / 🇳🇱 / 🇯🇵 / 🇵🇱 | ❌ non disponibile | ✅ tradotta da LLM, **non ancora verificata da umano** |

Le traduzioni LLM-only sono una baseline imperfetta: c'è bisogno di occhi umani su tutte. Se sei madrelingua o hai competenze in una di queste lingue, il tuo contributo fa la differenza.

### Architettura delle traduzioni

Spendif.ai ha **due aree** che richiedono traduzioni separate:

1. **UI Streamlit** — `sw_artifacts/ui/i18n/<lang>.json` — un file flat key→string per ogni lingua. Caricato a runtime dall'app. Lo schema chiavi segue la convenzione `area.elemento` e `area.elemento.desc` per le descrizioni (es. `nav.import` + `nav.import.desc`).

2. **Landing page** — `sw_artifacts/index.<lang>.html` — un file HTML completo per ogni lingua. **Schema in transizione**: stiamo migrando anche queste a un sistema JSON simile a `ui/i18n/` (tracked in `backlog.json` AI-14). Fino a quel refactor, le traduzioni della landing si fanno editando direttamente l'HTML — vedi sezione [Landing page (transizione)](#landing-page-transizione) qui sotto.

### Tradurre la UI Streamlit (raccomandato — il flusso più semplice)

#### Passo 1 — Scegli la lingua

Esempio: vuoi aggiungere il tedesco (`de`).

#### Passo 2 — Copia il master

```bash
cp sw_artifacts/ui/i18n/en.json sw_artifacts/ui/i18n/de.json
```

Parti da `en.json` come master (è la baseline di riferimento). Mai partire da `it.json` se non sei italiano: rischi calchi linguistici.

#### Passo 3 — Modifica il file

Apri `sw_artifacts/ui/i18n/de.json` con un editor a tua scelta. La struttura è semplice:

```json
{
  "_language_name": "Deutsch",
  "nav.import": "📥 Importieren",
  "nav.import.desc": "CSV/XLSX-Dateien aus deinen Bankkonten importieren",
  "nav.history": "📜 Importverlauf"
}
```

Regole:

- **Cambia `_language_name`** con il nome della lingua nella lingua stessa (es. `Deutsch`, non `German`)
- **Mantieni le chiavi invariate** — sono identificatori, non testo da tradurre
- **Mantieni emoji e formattazione** dove presenti — fanno parte del design
- **Mantieni le sostituzioni `{var}` o `%(name)s`** — sono placeholder per valori dinamici
- **Le chiavi `.desc`** sono descrizioni più lunghe usate come hint o tooltip — non saltarle

#### Passo 4 — Editor consigliati per non-developer

Se non sei abituato a JSON, qualsiasi di questi semplifica la vita:

- [**BabelEdit**](https://www.codeandweb.com/babeledit) — gratis per progetti open source. UI side-by-side EN/target lingua, evidenzia chiavi mancanti, segnala doppi spazi.
- [**Tolgee Cloud**](https://tolgee.io/) — interfaccia web side-by-side, suggerimenti AI integrati. Free tier sufficiente.
- [**VS Code**](https://code.visualstudio.com/) con estensione "JSON Tools" — per chi vuole solo un editor solido.
- [**JSONedit**](https://tomeko.net/software/JSONedit/) — Windows desktop, gratuito, leggero.

#### Passo 5 — Verifica e PR

```bash
# Verifica che il JSON sia valido
python -c "import json; json.load(open('sw_artifacts/ui/i18n/de.json'))"
```

Apri una PR seguendo il flusso standard ([Contribuire codice](#contribuire-codice)).

Nel body della PR includi:
- La lingua aggiunta o aggiornata
- Quante chiavi hai tradotto / verificato
- Eventuali decisioni terminologiche non ovvie (es. "ho reso `ledger` come `Hauptbuch` perché...")

### Landing page (transizione)

**Workflow attuale (transitorio)**: edita direttamente `sw_artifacts/index.<lang>.html`. Apri in parallelo `index.en.html` come riferimento, modifica solo i testi visibili (NON CSS, JS o `href` URL), salva, PR.

Suggerimenti pratici:

- Tieni due editor o due tab affiancati: EN a sinistra, lingua target a destra
- Cerca `<h1>`, `<h2>`, `<h3>`, `<p>`, `<title>`, `<meta>` per trovare le stringhe traducibili
- **NON tradurre**: `<style>`, `<script>`, `href="..."`, snippet shell nei `<code-body>` (curl/irm/bash), tag id (`id="install"`), label nei tab (`🍎 macOS`), nomi propri (Spendify, Ollama, Streamlit, GitHub)
- Aggiorna `<html lang="...">` in alto

**Workflow futuro (post AI-14)**: stiamo lavorando per portare anche le landing su un sistema JSON simile a `ui/i18n/`. Quando sarà pronto, contribuire una lingua significherà editare un solo file `i18n/landing/<lang>.json` invece di un HTML intero. Se vuoi aiutare a sbloccare questo refactor, vedi backlog item AI-14.

### Verifica umana di traduzioni LLM esistenti

Anche solo rileggere e correggere una traduzione esistente è un contributo prezioso. Se trovi errori, frasi che suonano artificiali, o termini tecnici sbagliati, apri una PR con i fix. Indica nel commit message:

```
i18n(de): fix awkward LLM phrasings in nav and analytics sections
i18n(ja): correct technical term for "ledger" — 元帳 → 取引履歴
```

---

## Setup ambiente di sviluppo

### Prerequisiti

| Strumento | Versione minima |
|-----------|----------------|
| Python | 3.13 |
| uv | qualsiasi |
| Docker Desktop | qualsiasi (per smoke test locale) |

### Installazione

```bash
git clone https://github.com/drake69/spendify.git
cd spendifai
uv sync
cp .env.example .env

# Script di avvio (consigliato)
./start.sh          # solo UI (default)
./start.sh api      # solo REST API
./start.sh all      # UI + API

# Oppure manualmente
uv run streamlit run app.py
```

### Eseguire i test

```bash
# Test con coverage
uv run pytest tests/ -v --cov=. --cov-report=term-missing

# Solo un modulo
uv run pytest tests/test_normalizer.py -v

# Docker smoke test locale
docker compose -f docker/docker-compose.yml build
docker compose -f docker/docker-compose.yml up -d
curl -sf http://localhost:8501/_stcore/health
docker compose -f docker/docker-compose.yml down -v
```

### Soglie di coverage attese

| Modulo | Coverage minima |
|--------|----------------|
| `core/normalizer.py` | 100% |
| `core/description_cleaner.py` | 100% |
| `core/classifier.py` | ≥ 99% |
| Tutti gli altri | ≥ 80% |

---

## Convenzioni

### Commit messages

Seguiamo [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: aggiungi filtraggio per contesto nel ledger
fix: correggi parsing importi con separatore europeo
test: aggiungi coverage per detect_internal_transfers
docs: aggiorna guida installazione per Docker arm64
refactor: estrai logica di pairing in funzione separata
chore: rimuovi dead code in support/core_logic.py
```

### Branch naming

```
feat/crea-regola-da-ledger
fix/encoding-windows-1252
test/phase0-preprocessing
docs/guida-installazione-en
```

### Stile codice

- **Formatter**: `ruff format` (configurato in `pyproject.toml`)
- **Linter**: `ruff check`
- **Type hints**: obbligatori per funzioni pubbliche nei moduli `core/`
- **Decimal**: gli importi monetari usano sempre `Decimal`, mai `float`

```bash
# Check locale prima del commit
uv run ruff check .
uv run ruff format --check .
```

---

## Domande?

Apri una [Discussion](https://github.com/drake69/spendify/discussions) o scrivi nei commenti dell'issue più vicina al tuo caso.
