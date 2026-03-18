# Contribuire a Spendify

Grazie per l'interesse! Queste linee guida descrivono come segnalare problemi, proporre funzionalità e contribuire codice.

---

## Indice

1. [Sistema di priorità](#sistema-di-priorità)
2. [Segnalare un bug](#segnalare-un-bug)
3. [Proporre una funzionalità](#proporre-una-funzionalità)
4. [Contribuire codice](#contribuire-codice)
5. [Setup ambiente di sviluppo](#setup-ambiente-di-sviluppo)
6. [Convenzioni](#convenzioni)

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
   - Versione Spendify (o commit hash)
   - OS e versione Docker/Python
   - Passi per riprodurre
   - Comportamento atteso vs effettivo
   - Log rilevanti (`docker compose logs spendify`)

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
cd spendify
uv sync
cp .env.example .env
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
