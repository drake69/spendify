# Politica di sicurezza

*English version: [SECURITY.md](SECURITY.md).*

## Versioni supportate

| Versione | Supportata |
|----------|-----------|
| latest   | Sì        |
| < latest | No — aggiorna |

## Segnalazione di una vulnerabilità

Se scopri una vulnerabilità di sicurezza in Spendif.ai, **per favore non aprire una issue pubblica.**

Segnala invece in privato:

1. **GitHub Security Advisories** (preferito): vai alla [scheda Security](https://github.com/drake69/spendify/security/advisories) e clicca **"Report a vulnerability"**
2. **Email**: invia i dettagli al proprietario del repository tramite l'indirizzo elencato nel [profilo GitHub](https://github.com/drake69)

### Cosa includere

- Descrizione della vulnerabilità
- Passi per riprodurla
- Versione/i interessate
- Impatto potenziale
- Fix suggerito (se ne hai uno)

### Tempi di risposta

| Passaggio | Tempistica |
|-----------|-----------|
| Conferma di ricezione | Entro 48 ore |
| Valutazione iniziale | Entro 7 giorni |
| Rilascio patch | Entro 30 giorni (critica: entro 7 giorni) |
| Disclosure pubblico | Dopo il rilascio della patch |

## Scope

Sono **in scope**:

- Codice applicativo in `core/`, `services/`, `ui/`, `db/`, `api/`
- Workflow GitHub Actions (`.github/workflows/`)
- Configurazione, gestione dei secret e layer di PII-redaction applicato prima delle chiamate LLM remote

Sono **fuori scope**:

- Vulnerabilità in dipendenze upstream (segnalare al rispettivo progetto; tracciate via Dependabot / pip-audit)
- Attacchi di social engineering
- Attacchi denial of service

## Misure di sicurezza

Spendif.ai adotta le seguenti pratiche:

- **Analisi statica** — Bandit e pattern proibiti vengono eseguiti su ogni PR (workflow CI `security.yml`)
- **CodeQL** — analisi semantica con la query suite `security-and-quality` (pianificata, tracciata nel backlog)
- **Validazione input** — schemi Pydantic v2 ai confini delle API; controlli espliciti per campo nel service layer
- **Niente funzioni pericolose** — `eval()`, `exec()`, `subprocess shell=True`, `pickle.load()`, `yaml.load()` senza `SafeLoader` sono vietati e bloccati in CI
- **Ruff linting** — applicato in CI con le regole di sicurezza in stile bandit (`S`) abilitate
- **Gestione dipendenze** — `uv.lock` garantisce installazioni riproducibili; `pip-audit` segnala CVE note
- **PII redaction** — IBAN, numero di carta, codice fiscale e nome dell'intestatario vengono sostituiti automaticamente con placeholder prima di qualsiasi chiamata LLM remota. Il service layer rifiuta input non sanitizzati. Vedi `core/pii_redactor.py` e la sezione privacy della landing per la lista completa
- **AI local-first** — il backend LLM di default (`local_llama_cpp`) mantiene tutti i dati sulla macchina dell'utente; i backend remoti sono opt-in
- **Integrità dei prompt** — i file di prompt hanno hash SHA-256 verificati all'avvio così che modifiche non autorizzate vengano rilevate (pianificata, tracciata nel backlog)

## Riconoscimenti

Apprezziamo la disclosure responsabile. I ricercatori che segnalano vulnerabilità valide vengono accreditati nelle release notes (a meno che preferiscano l'anonimato).
