# Spendify — Manuale di Configurazione

> Riferimento completo per tutte le impostazioni disponibili nella pagina **⚙️ Impostazioni**.
> Le impostazioni sono persistite nel database (`ledger.db`) e si applicano immediatamente al prossimo salvataggio.

---

## Indice

1. [Prima configurazione obbligatoria](#1-prima-configurazione-obbligatoria)
2. [Conti bancari](#2-conti-bancari)
3. [Titolari del conto](#3-titolari-del-conto)
4. [Formato visualizzazione](#4-formato-visualizzazione)
5. [Lingua delle descrizioni](#5-lingua-delle-descrizioni)
6. [Modalità Giroconti](#6-modalità-giroconti)
7. [Contesti di vita](#7-contesti-di-vita)
8. [Modalità test importazione](#8-modalità-test-importazione)
9. [Backend LLM](#9-backend-llm)
   - [Ollama (locale)](#91-ollama-locale)
   - [OpenAI](#92-openai)
   - [Claude (Anthropic)](#93-claude-anthropic)
   - [OpenAI-compatible](#94-openai-compatible-groq-together-ai-ecc)

---

## 1. Prima configurazione — wizard di onboarding

Al primo avvio l'app mostra automaticamente il **wizard di onboarding** (4 step). Non è necessario andare nelle Impostazioni prima di importare: il wizard raccoglie i dati minimi essenziali e li scrive nel DB in un'unica operazione atomica al clic su "Inizia!".

| Step | Cosa si configura |
|---|---|
| **1 — Lingua** | Lingua della tassonomia di default (italiano, inglese, francese, tedesco, spagnolo). Pre-selezionata dalla lingua del browser. Influenza anche il formato data e i separatori numerici. |
| **2 — Titolari** | Nomi dei titolari del conto (obbligatori). Usati per PII redaction e rilevamento giroconti. |
| **3 — Conti** | Conti bancari (nome + banca). Facoltativi: si può saltare con avviso e aggiungere in seguito dalle Impostazioni. |
| **4 — Conferma** | Riepilogo e pulsante "Inizia!" — solo qui i dati vengono scritti nel DB. |

> **Installazioni esistenti:** se il database contiene già dati (aggiornamento da versione precedente), il wizard viene saltato automaticamente e l'app si apre direttamente.

Dopo il wizard puoi affinare qualsiasi impostazione dalla pagina **⚙️ Impostazioni** in qualsiasi momento.

---

## 2. Conti bancari

**Percorso:** Impostazioni → 🏦 Conti bancari

Definisce i conti correnti, carte e depositi che possiedi. Ogni conto ha:

| Campo | Obbligatorio | Descrizione |
|---|---|---|
| **Nome conto** | ✅ Sì | Identificativo univoco (es. `Conto corrente BPER`, `Carta Visa BNL`) |
| **Banca** | No | Nome della banca per riferimento (non influenza l'elaborazione) |

### Perché definire i conti

- Nella pagina Import puoi **associare ogni file a un conto specifico** invece di affidarti al rilevamento automatico.
- Il nome del conto viene salvato con ogni transazione (`account_label`) ed è la chiave usata per la **Check List** (pivot mese × conto).
- Migliora la **deduplicazione**: transazioni dello stesso conto importate in sessioni diverse vengono riconosciute correttamente.

### Note operative

- Puoi importare senza conti definiti, ma il rilevamento automatico potrebbe assegnare nomi diversi allo stesso conto in importazioni successive.
- Elimina un conto solo se non ha transazioni associate, altrimenti le transazioni esistenti manterranno il vecchio `account_label`.

---

## 3. Titolari del conto

**Percorso:** Impostazioni → 👤 Titolari del conto

### Campo: Nomi titolari

Lista di nomi dei titolari dei conti, separati da virgola.

```
Mario Rossi, Anna Bianchi
```

**Utilizzi:**

1. **Sanitizzazione PII** — I nomi vengono sostituiti con alias fittizi (es. `Carlo Brambilla`) prima di inviare qualsiasi testo a backend LLM remoti (OpenAI, Claude). Il dato originale nel database non viene mai modificato.

2. **Rilevamento giroconti** — Se attivi il toggle *Usa nomi titolari per identificare giroconti*, le transazioni la cui descrizione contiene un nome titolare vengono marcate automaticamente come giroconto (🔄).

### Toggle: Usa nomi titolari per giroconti

| Stato | Comportamento |
|---|---|
| **Attivo** | Bonifici con il tuo nome in descrizione → marcati 🔄 giroconto |
| **Disattivo** | Rilevamento giroconti solo per importo/data/conto (RF-04 Fase 1) |

> **Consiglio:** Inserisci anche le varianti del nome usate dalle banche (cognome-nome, maiuscolo, senza accenti). Esempio: `Mario Rossi, ROSSI MARIO, Rossi M.`

---

## 4. Formato visualizzazione

**Percorso:** Impostazioni → Formato visualizzazione

Controlla come date e importi vengono mostrati nel Ledger, Analytics e Review. Non influenza il database (che usa sempre ISO 8601 e Numeric).

### Formato data

| Opzione | Esempio | Note |
|---|---|---|
| `dd/mm/yyyy` | 31/12/2025 | **Default** — standard italiano |
| `yyyy-mm-dd` | 2025-12-31 | ISO 8601, adatto a export/CSV |
| `mm/dd/yyyy` | 12/31/2025 | Standard US |

### Separatori numerici

| Impostazione | Opzioni | Default |
|---|---|---|
| **Separatore decimali** | `,` (italiano/europeo) · `.` (inglese/US) | `,` |
| **Separatore migliaia** | `.` (italiano) · `,` (inglese) · ` ` (francese) · nessuno | `.` |

La pagina mostra un'**anteprima in tempo reale** (es. `1.234,56 €`) prima di salvare.

---

## 5. Lingua delle descrizioni

**Percorso:** Impostazioni → Lingua delle descrizioni

| Opzione | Codice |
|---|---|
| Italiano | `it` |
| English | `en` |
| Français | `fr` |
| Deutsch | `de` |

Viene passata al prompt del categorizzatore LLM per aiutarlo a interpretare correttamente le descrizioni delle transazioni. Se le tue rendicontazioni sono in italiano, lascia `it` (default).

> **Esempio:** Una descrizione come `"PAGAMENTO POS CONAD"` viene interpretata diversamente da un modello istruito in italiano rispetto a uno istruito in inglese.

---

## 6. Modalità Giroconti

**Percorso:** Impostazioni → 🔄 Modalità Giroconti

I giroconti (trasferimenti interni tra tuoi conti) vengono **sempre rilevati e sempre salvati** nel database, indipendentemente dalla modalità scelta. Questo garantisce la riconciliazione e l'integrità dei dati. La modalità controlla **solo la visibilità** nelle viste (Ledger, Analytics, Report).

| Modalità | Comportamento nelle viste (Ledger, Analytics, Report) |
|---|---|
| **Mostra (neutral)** | Le righe 🔄 sono visibili (grigie/neutre), escluse dai totali entrate/uscite |
| **Escludi dalle viste** | Le righe 🔄 non compaiono nelle schermate (ma restano nel database) |

La modalità si applica globalmente. Puoi sovrascriverla per singola vista usando il checkbox *Nascondi giroconti* nel Ledger.

> **Nota tecnica:** i giroconti sono marcati come `internal_in`/`internal_out` nel ledger. Anche con modalità "Escludi", restano disponibili per riconciliazione e audit.

---

## 7. Contesti di vita

**Percorso:** Impostazioni → 🌍 Contesti di vita

Lista libera di etichette per segmentare le spese per contesto (es. `Quotidianità`, `Lavoro`, `Vacanza`).

- Aggiungi/rimuovi contesti liberamente
- Assegna un contesto a ogni transazione dal **Ledger** (colonna Contesto)
- Usa il filtro Contesto in Analytics per confrontare i periodi (es. "quanto ho speso in vacanza vs quotidianità?")

**Default:** `Quotidianità`, `Lavoro`, `Vacanza`

---

## 8. Modalità test importazione

**Percorso:** Impostazioni → 📥 Importazione

| Toggle | Comportamento |
|---|---|
| **Disattivo** (default) | Elabora tutte le righe del file |
| **Attivo** | Elabora solo le prime **20 righe** per file |

Utile per:
- Verificare che il formato del file venga riconosciuto correttamente prima di un import completo
- Testare la classificazione LLM su un campione senza attendere l'elaborazione completa
- Debug di nuovi formati bancari

> ⚠️ Ricordati di disattivarlo prima dell'import definitivo.

---

## 9. Backend LLM

**Percorso:** Impostazioni → 🤖 Configurazione LLM

Il backend LLM viene usato per:
- **Classificazione categorie** — assegna categoria/sottocategoria a ogni transazione
- **Estrazione controparte** — normalizza la descrizione grezza della banca

| Backend | Privacy | Costo | Velocità | Qualità |
|---|---|---|---|---|
| **Ollama (locale)** | ✅ Totale | ✅ Gratuito | ⚡ Dipende dall'hardware | Buona (con gemma3:12b) |
| **OpenAI** | ⚠️ PII redatte | 💰 Pay-per-use | ⚡⚡ Alta | Alta |
| **Claude (Anthropic)** | ⚠️ PII redatte | 💰 Pay-per-use | ⚡⚡ Alta | Alta |
| **OpenAI-compatible** | ⚠️ PII redatte | Varia | Varia | Varia |

**Circuit breaker:** Se il backend configurato non risponde, Spendify fa fallback automatico su Ollama locale. Se anche Ollama è offline, la transazione viene importata con `to_review=True` e descrizione grezza.

---

### 9.1 Ollama (locale)

La scelta migliore per la **privacy totale**: nessun dato lascia il tuo computer.

**Installazione (una tantum):**

```bash
# macOS
brew install ollama

# Linux
curl -fsSL https://ollama.ai/install.sh | sh

# Windows
# Scarica l'installer da https://ollama.ai/download
```

**Scaricare il modello:**
```bash
ollama pull gemma3:12b        # consigliato (~8 GB)
ollama pull llama3.2:3b       # leggero (~2 GB), qualità inferiore
```

**Verificare che funzioni:**
```bash
ollama list                   # mostra i modelli scaricati
curl http://localhost:11434   # deve rispondere "Ollama is running"
```

| Campo | Default | Descrizione |
|---|---|---|
| **URL server Ollama** | `http://localhost:11434` | Cambia solo se Ollama gira su un altro host o in Docker |
| **Modello** | `gemma3:12b` | Deve corrispondere esattamente all'output di `ollama list` |

**Ollama su Docker** (esempio):
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
In questo caso imposta URL: `http://localhost:11434` (o l'IP del container se Spendify è a sua volta in Docker).

**Modelli consigliati per qualità categorizzazione:**

| Modello | RAM richiesta | Note |
|---|---|---|
| `gemma3:12b` | ~8 GB | ✅ Consigliato — ottimo per italiano, veloce su Apple Silicon |
| `qwen2.5:14b` | ~10 GB | Ottima qualità multilingua, più lento |
| `mistral:7b` | ~5 GB | Alternativa solida, multilingua |
| `llama3.2:3b` | ~3 GB | Velocissimo, qualità sufficiente per categorie semplici |

> **Tip Apple Silicon:** I modelli girano sulla GPU integrata (Metal) — gemma3:12b elabora ~15-20 transazioni al secondo su M2/M3.

---

### 9.2 OpenAI

**Dove registrarsi:** https://platform.openai.com

**Come ottenere la API Key:**
1. Accedi su https://platform.openai.com
2. Menu in alto a destra → **API keys**
3. Clicca **+ Create new secret key**
4. Dai un nome (es. `Spendify`) e copia la chiave — **mostrata una sola volta**
5. Assicurati di avere credito nel tuo account (sezione *Billing*)

**Configurazione in Spendify:**
```
Backend LLM:  OpenAI
API Key:      sk-proj-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
Modello:      gpt-4o-mini
```

**Modelli disponibili e costi indicativi (marzo 2026):**

| Modello | Input ($/1M token) | Output ($/1M token) | Note |
|---|---|---|---|
| `gpt-4o-mini` | $0.15 | $0.60 | ✅ Consigliato — ottimo rapporto qualità/prezzo |
| `gpt-4o` | $2.50 | $10.00 | Alta qualità, costo ~15× superiore |
| `gpt-4.1-mini` | $0.40 | $1.60 | Alternativa economica più recente |

> **Stima costi:** 1000 transazioni ≈ ~100k token totali ≈ **$0.015** con gpt-4o-mini.

> **Privacy:** IBAN, numeri carta, codice fiscale e nomi titolari vengono sostituiti con placeholder prima dell'invio. Il testo inviato a OpenAI non contiene mai dati identificativi.

---

### 9.3 Claude (Anthropic)

**Dove registrarsi:** https://console.anthropic.com

**Come ottenere la API Key:**
1. Accedi su https://console.anthropic.com
2. Sezione **API Keys** (menu laterale)
3. Clicca **Create Key**
4. Dai un nome (es. `Spendify`) e copia la chiave
5. Aggiungi credito in **Billing → Add Credits** (minimo $5)

**Configurazione in Spendify:**
```
Backend LLM:  Claude (Anthropic)
API Key:      sk-ant-api03-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
Modello:      claude-3-5-haiku-20241022
```

**Modelli disponibili:**

| Modello | Velocità | Qualità | Note |
|---|---|---|---|
| `claude-3-5-haiku-20241022` | ⚡⚡⚡ | ⭐⭐⭐⭐ | ✅ Consigliato — veloce, economico, ottima qualità |
| `claude-3-5-sonnet-20241022` | ⚡⚡ | ⭐⭐⭐⭐⭐ | Qualità superiore per descrizioni ambigue |
| `claude-opus-4-5` | ⚡ | ⭐⭐⭐⭐⭐ | Massima qualità, costo elevato |

> **Privacy:** stesse garanzie di OpenAI — PII redatte prima dell'invio.

---

### 9.4 OpenAI-compatible (Groq, Together AI, ecc.)

Compatibile con qualsiasi API che esponga l'endpoint `/v1/chat/completions` nel formato OpenAI.

| Campo | Esempio | Descrizione |
|---|---|---|
| **Base URL** | `https://api.groq.com/openai/v1` | URL base del provider (senza `/chat/completions`) |
| **API Key** | `gsk_...` | Chiave del provider |
| **Modello** | `gemma2-9b-it` | Nome modello esatto come richiesto dal provider |

---

#### Groq (consigliato per chi vuole gratuito + veloce)

**Dove registrarsi:** https://console.groq.com

**Come ottenere la API Key:**
1. Registrati su https://console.groq.com (gratuito)
2. Menu laterale → **API Keys** → **Create API Key**
3. Copia la chiave (prefisso `gsk_`)

**Configurazione:**
```
Backend LLM:  OpenAI-compatible
Base URL:     https://api.groq.com/openai/v1
API Key:      gsk_xxxxxxxxxxxxxxxxxxxxxxxxxxxx
Modello:      gemma2-9b-it
```

**Modelli Groq utili per Spendify:**

| Modello | Note |
|---|---|
| `gemma2-9b-it` | ✅ Consigliato — ottimo per italiano, molto veloce |
| `llama-3.3-70b-versatile` | Alta qualità, leggermente più lento |
| `llama-3.1-8b-instant` | Velocissimo, qualità buona |

> **Tier gratuito:** ~14.400 richieste/giorno, sufficiente per uso personale. Limite di 6000 token/minuto.

---

#### Together AI

**Dove registrarsi:** https://api.together.ai

**Come ottenere la API Key:**
1. Registrati e accedi su https://api.together.ai
2. Vai su **Settings → API Keys**
3. Crea una nuova chiave

**Configurazione:**
```
Backend LLM:  OpenAI-compatible
Base URL:     https://api.together.xyz/v1
API Key:      <tua_api_key>
Modello:      meta-llama/Llama-3.2-11B-Vision-Instruct-Turbo
```

---

#### Google AI Studio (Gemini)

**Dove registrarsi:** https://aistudio.google.com

**Come ottenere la API Key:**
1. Vai su https://aistudio.google.com
2. Clicca **Get API key** in alto a destra
3. **Create API key** → seleziona o crea un progetto Google Cloud

**Configurazione:**
```
Backend LLM:  OpenAI-compatible
Base URL:     https://generativelanguage.googleapis.com/v1beta/openai
API Key:      AIza...
Modello:      gemini-2.0-flash
```

**Modelli Gemini:**

| Modello | Note |
|---|---|
| `gemini-2.0-flash` | ✅ Consigliato — veloce, alta qualità, tier gratuito generoso |
| `gemini-1.5-flash` | Alternativa economica |

> **Tier gratuito:** 1500 richieste/giorno con gemini-2.0-flash — più che sufficiente per uso personale.

---

#### LM Studio (alternativa locale a Ollama)

LM Studio è un'app desktop (macOS, Windows, Linux) per eseguire modelli localmente con interfaccia grafica.

**Download:** https://lmstudio.ai

**Configurazione:**
1. Scarica e installa LM Studio
2. Scarica un modello dalla sezione *Discover*
3. Avvia il server locale: **Local Server** → **Start Server**
4. In Spendify:

```
Backend LLM:  OpenAI-compatible
Base URL:     http://localhost:1234/v1
API Key:      lm-studio   (qualsiasi stringa, non verificata)
Modello:      (copia il nome esatto dal pannello di LM Studio)
```

---

## Valori di default

Alla prima installazione il database viene inizializzato con questi valori:

| Chiave | Default | Descrizione |
|---|---|---|
| `date_display_format` | `%d/%m/%Y` | Formato italiano `dd/mm/yyyy` |
| `amount_decimal_sep` | `,` | Separatore decimali italiano |
| `amount_thousands_sep` | `.` | Separatore migliaia italiano |
| `description_language` | `it` | Italiano |
| `giroconto_mode` | `neutral` | Giroconti visibili ma neutri |
| `llm_backend` | `local_ollama` | Ollama locale |
| `ollama_base_url` | `http://localhost:11434` | Porta default Ollama |
| `ollama_model` | `gemma3:12b` | Modello raccomandato |
| `openai_model` | `gpt-4o-mini` | Modello OpenAI economico |
| `anthropic_model` | `claude-3-5-haiku-20241022` | Modello Claude economico |
| `import_test_mode` | `false` | Import completo |
| `owner_names` | *(vuoto)* | **Da configurare prima del primo import** |
| `contexts` | `["Quotidianità","Lavoro","Vacanza"]` | Contesti predefiniti |

---

## Checklist prima configurazione

```
[ ] 1. Avvia l'app → il wizard di onboarding appare automaticamente
[ ] 2. Step 1: scegli la lingua della tassonomia
[ ] 3. Step 2: inserisci il tuo nome (e varianti usate dalla banca)
[ ] 4. Step 3: aggiungi i tuoi conti bancari (o salta e aggiungili dopo)
[ ] 5. Step 4: clicca "Inizia!" per completare la configurazione
[ ] 6. (Opzionale) Vai in ⚙️ Impostazioni → configura il backend LLM
[ ] 7. Vai in 📥 Import e carica il primo estratto conto
```
