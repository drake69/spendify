# Spendify — Il tuo estratto conto unificato, sul tuo computer

> *Documento divulgativo — base per pagina web / landing page*

---

## Il problema che tutti conoscono ma nessuno risolve

Hai tre conti: un conto corrente, una carta di credito e un conto deposito. Ogni mese scarichi tre file dalla banca, li apri in Excel, provi ad incollarli insieme — e ogni volta ti perdi tra doppioni, date, segni degli importi che vanno a caso, e la certezza che qualcosa non torna.

Poi c'è il problema dei problemi: **l'addebito della carta sul conto corrente**. La spesa al supermercato compare sia nell'estratto conto della carta (come singola transazione) sia nel conto corrente (come addebito mensile aggregato). Sommando tutto, le tue spese sembrano il doppio di quelle reali.

Spendify risolve esattamente questo.

---

## Cos'è Spendify

Spendify è un registro finanziario personale che aggrega automaticamente gli estratti conto di banche diverse in un **unico ledger cronologico**, senza duplicati, senza errori di segno, senza abbonamenti mensili e senza inviare i tuoi dati a nessuno.

Funziona sul tuo computer. I tuoi dati rimangono sul tuo computer.

---

## Come funziona in tre passi

### 1. Scarica i file dalla tua banca
Esporta gli estratti conto in CSV o XLSX dal portale della tua banca. Non serve fare nulla di speciale — Spendify riconosce automaticamente il formato.

### 2. Trascinali in Spendify
Seleziona tutti i file insieme (anche da banche diverse, anche anni diversi) e clicca **Elabora**. Spendify:
- Rileva automaticamente che tipo di documento è (conto corrente, carta di credito, carta prepagata, conto deposito)
- Corregge il segno degli importi se necessario (alcune banche esportano le spese come numeri positivi)
- Elimina i doppi conteggi tra carta e conto
- Classifica ogni transazione con un'etichetta di categoria

### 3. Guarda dove vanno i tuoi soldi
Il ledger unificato ti mostra tutto in un posto solo. Con grafici, filtri, export, e la certezza che ogni euro viene contato una volta sola.

---

## Le cose che nessun altro fa automaticamente

### Riconciliazione carta–conto corrente
Quando la tua carta di credito addebita l'importo mensile sul conto corrente, Spendify riconosce la relazione e **rimuove il doppio conteggio** automaticamente. L'algoritmo usa una finestra temporale di ±45 giorni e tre fasi di matching (finestra scorrevole + subset sum per importi frazionati).

### Rilevamento giroconti interni
Un bonifico dal tuo conto corrente al conto deposito non è né una spesa né un'entrata: è un trasferimento interno. Spendify lo riconosce confrontando importi, date e nomi dei titolari nelle descrizioni — anche se i due file sono stati importati in momenti diversi.

### Deduplicazione idempotente
Ogni transazione ha un codice univoco calcolato sul contenuto (SHA-256). Se importi lo stesso file due volte, non accade nulla. Puoi reimportare tutta la storia senza paura.

### Classificazione ibrida
La categorizzazione usa un sistema a cascata in quattro livelli:
1. **Le tue regole** — pattern che hai definito tu (es: "CONAD" → Alimentari / Supermercati)
2. **Regex statiche** — pattern predefiniti per le categorie più comuni
3. **LLM** — per tutto il resto, il modello linguistico assegna categoria e sottocategoria
4. **Fallback** — se tutto il resto fallisce, la transazione viene messa in "Da rivedere"

---

## Privacy: i tuoi dati restano tuoi

### Modalità offline-first
Per default, Spendify usa **Ollama in locale**: un motore AI che gira sul tuo computer, senza connessione internet. I tuoi estratti conto non lasciano mai il tuo disco.

### Se vuoi usare OpenAI o Claude
Puoi farlo, ma Spendify prima **rimuove automaticamente** tutti i dati identificativi:
- IBAN → `<ACCOUNT_ID>`
- Numeri di carta → `<CARD_ID>`
- Codice fiscale → `<FISCAL_ID>`
- Il tuo nome → un nome fittizio

Solo dopo la sanitizzazione il testo viene inviato. Se per qualsiasi motivo il controllo fallisce, la chiamata viene bloccata — non degradata silenziosamente.

### Nessun cloud, nessun abbonamento
I dati sono in un database SQLite sul tuo computer. Puoi copiarlo, spostarlo, farne backup come qualsiasi altro file.

---

## Per chi è Spendify

### Per chi ha più conti in banche diverse
Se usi più di un conto bancario (conto corrente + carta di credito + conto deposito + conto trading) sai quanto è difficile avere una visione unificata. Spendify fa esattamente questo — senza dover fare nulla a mano.

### Per chi tiene i conti seriamente
Se usi Excel per tenere traccia delle spese, Spendify può sostituire quella routine: importi i file una volta, Spendify li unifica e li classifica, tu controlli e correggi solo le eccezioni.

### Per chi non si fida del cloud
Spendify non ha backend remoti obbligatori, non richiede un account, non invia nulla a nessuno per default. I tuoi dati bancari restano dove devono restare.

### Per i developer
Spendify è un progetto open source Python con un'architettura modulare e un test suite completo. È un punto di partenza interessante per chi vuole:
- Sperimentare pipeline LLM su dati strutturati
- Costruire integrazioni con banche specifiche
- Estendere il modello dati o la tassonomia
- Deployare su server per uso familiare o di piccolo team

---

## Funzionalità principali

| | |
|---|---|
| **Import multi-banca** | CSV e XLSX da qualsiasi banca italiana (e non solo) |
| **Auto-detect formato** | Nessuna configurazione manuale per ogni tipo di file |
| **Ledger unificato** | Tutte le transazioni in ordine cronologico, filtrabili per data / conto / categoria / contesto |
| **Classificazione automatica** | 15 categorie spese + 7 entrate con sottocategorie |
| **Tassonomia personalizzabile** | Aggiungi / modifica categorie e sottocategorie senza riavviare l'app |
| **Regole deterministiche** | Crea regole "ESSELUNGA sempre → Alimentari" applicate retroattivamente |
| **Analytics interattiva** | 7 grafici Plotly: andamento mensile, bilancio cumulato, torta spese per categoria, drill-down sottocategorie, top 10 commercianti |
| **Contesti di vita** | Dimensione ortogonale alla categoria: segmenta le spese per Lavoro / Vacanza / Quotidianità |
| **Check List** | Tabella pivot mese × conto: vedi a colpo d'occhio quali mesi non hai ancora importato |
| **Export** | HTML standalone (con grafici), CSV, XLSX |
| **LLM configurabile** | Ollama locale, OpenAI, Claude, Groq, Google AI Studio, LM Studio, qualsiasi API compatibile |
| **PII sanitization** | Protezione automatica IBAN / PAN / codice fiscale / nome prima di qualsiasi chiamata remota |

---

## Stack tecnologico

Spendify è costruito su tecnologie mature e senza dipendenze esotiche:

- **Python 3.13** + **pandas** per la pipeline dati
- **Streamlit** per l'interfaccia web (accessibile dal browser, senza installare nulla di speciale)
- **SQLite + SQLAlchemy** per la persistenza (un singolo file, portabile)
- **Pydantic v2** per la validazione degli schemi
- **Plotly** per i grafici
- **uv** come package manager (installazione in ~30 secondi)

Nessun framework LLM (no LangChain) — i backend AI usano direttamente gli SDK ufficiali, con un'interfaccia astratta comune.

---

## Installazione in un comando

L'unico prerequisito è **[Docker Desktop](https://www.docker.com/products/docker-desktop/)**.

**Mac / Linux:**
```bash
curl -fsSL https://raw.githubusercontent.com/drake69/spendify/main/installer/install.sh | bash
```

**Windows (PowerShell):**
```powershell
irm https://raw.githubusercontent.com/drake69/spendify/main/installer/install.ps1 | iex
```

Lo script scarica l'immagine da GitHub Container Registry, avvia il container e apre il browser su `http://localhost:8501` automaticamente.

Vai in **Impostazioni**, inserisci il tuo nome, aggiungi i tuoi conti — e importa.

> Per installazione nativa (Mac con Ollama) o server Linux/Windows con Docker+LLM locale → [Guida completa](installazione.md).

---

## Per i developer: cosa c'è dentro

### Pipeline modulare
```
File CSV/XLSX
    → Classificatore schema  (fingerprint SHA-256 colonne → LLM se schema nuovo)
    → Normalizzatore          (encoding, parse_amount Decimal, SHA-256 tx_id)
    → Riconciliatore RF-03    (carta–conto, 3 fasi)
    → Rilevatore RF-04        (giroconti interni, matching simbolico)
    → Description cleaner     (LLM, rumore → testo canonico)
    → Categorizzatore         (regole → regex → LLM → fallback)
    → Database                (SQLAlchemy, idempotente)
```

### Estensibilità
- **Nuovi backend LLM**: implementa `LLMBackend` (3 metodi) e registralo in `BackendFactory`
- **Nuovi formati bancari**: il Flow 2 li riconosce automaticamente via LLM senza modifiche al codice; lo schema viene salvato e riutilizzato nelle importazioni successive
- **Nuove categorie**: dalla pagina Tassonomia, senza toccare il codice
- **API REST**: la pipeline `process_file()` è completamente separata dall'UI — si può esporre via FastAPI senza modifiche

### Test suite
```bash
uv run pytest tests/ -v          # 184 test, zero dipendenze esterne
uv run pytest tests/ --cov=core  # coverage del layer di business logic
```

Tutti i test usano SQLite in-memory: nessun file, nessun servizio esterno, nessun mock LLM necessario per la suite base.

---

## Roadmap

- [ ] Supporto PDF (parsing nativo estratti PDF bancari)
- [ ] Budget mensile per categoria con alerting
- [ ] Importazione automatica via Open Banking (PSD2)
- [ ] API REST per integrazioni esterne
- [ ] Versione multi-utente con autenticazione
- [ ] App mobile (visualizzazione ledger e analytics)

---

## Contribuire

Spendify è open source. Le aree dove il contributo è più utile:

- **Nuovi formati bancari** — se la tua banca non viene riconosciuta automaticamente, puoi aprire una issue con un campione anonimizzato
- **Test** — la suite copre il layer di business logic ma non ancora l'UI
- **Internazionalizzazione** — l'architettura supporta già più lingue per le descrizioni; la UI è in italiano
- **Performance** — la categorizzazione batch è il collo di bottiglia con LLM locale; c'è margine per parallelizzazione

---

*Spendify non è un servizio cloud. È un programma che gira sul tuo computer. I tuoi dati bancari non vengono mai inviati a server di terzi, a meno che tu non scelga esplicitamente un backend LLM remoto — in quel caso, tutti i dati identificativi vengono rimossi prima dell'invio.*
