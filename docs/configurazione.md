# Spendif.ai — Manuale di Configurazione

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
   - [llama.cpp (locale, default)](#90-llamacpp-locale-default)
   - [Ollama (locale)](#91-ollama-locale)
   - [vLLM (server locale o remoto)](#92b-vllm-server-locale-o-remoto)
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
| **3 — Conti** | Conti bancari (nome + banca + tipo conto obbligatorio). Facoltativi: si può saltare con avviso e aggiungere in seguito dalle Impostazioni. |
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
| **Tipo conto** | ✅ Sì | Tipo di strumento finanziario (vedi tabella sotto) |

### Valori tipo conto

| Valore | Etichetta | Note |
|---|---|---|
| `bank_account` | Conto corrente | Flussi misti entrate/uscite |
| `credit_card` | Carta di credito | Forza `invert_sign=True` (spese positive nel CSV → uscite) |
| `debit_card` | Carta di debito | Comportamento segno identico a conto corrente |
| `prepaid_card` | Carta prepagata | Comportamento segno identico a carta di debito |
| `savings_account` | Conto risparmio | Prevalenza giroconti |
| `cash` | Contanti | Movimenti in contanti |

> Solo la **carta di credito** richiede un trattamento speciale (inversione del segno). Carta di debito e prepagata hanno comportamento del segno identico ma sono valori separati perché l'etichetta è chiara per l'utente.

Il tipo conto viene usato come vincolo nella classificazione dello schema file: biasa il rilevamento automatico del `doc_type` e, per le carte di credito, forza automaticamente l'inversione del segno degli importi. Il formato del file (colonna unica con segno, dare/avere, solo positivi) viene rilevato automaticamente dal classifier — l'utente deve solo indicare che tipo di strumento è.

### Perché definire i conti

- Nella pagina Import puoi **associare ogni file a un conto specifico** invece di affidarti al rilevamento automatico.
- Il nome del conto viene salvato con ogni transazione (`account_label`) ed è la chiave usata per la **Check List** (pivot mese × conto).
- Migliora la **deduplicazione**: transazioni dello stesso conto importate in sessioni diverse vengono riconosciute correttamente.

### Note operative

- Puoi importare senza conti definiti, ma il rilevamento automatico potrebbe assegnare nomi diversi allo stesso conto in importazioni successive.
- Elimina un conto solo se non ha transazioni associate, altrimenti le transazioni esistenti manterranno il vecchio `account_label`.

### Rinominare un conto

Rinominare un conto e sicuro: Spendif.ai ricalcola atomicamente l'ID (`tx_id`) di tutte le transazioni associate, perche `account_label` fa parte della chiave hash. Se il ricalcolo fallisce, viene eseguito il rollback e nessun dato cambia. Al termine, il campo `updated_at` di ogni transazione aggiornata riflette la data dell'operazione.

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
| **llama.cpp (locale, default)** | ✅ Totale | ✅ Gratuito | ⚡ Dipende dall'hardware | Buona (con modelli GGUF) |
| **Ollama (locale)** | ✅ Totale | ✅ Gratuito | ⚡ Dipende dall'hardware | Buona (con gemma3:12b) |
| **vLLM (server locale/remoto)** | ✅ Totale | ✅ Gratuito | ⚡⚡ Alta (GPU CUDA) | Alta |
| **OpenAI** | ⚠️ PII redatte | 💰 Pay-per-use | ⚡⚡ Alta | Alta |
| **Claude (Anthropic)** | ⚠️ PII redatte | 💰 Pay-per-use | ⚡⚡ Alta | Alta |
| **OpenAI-compatible** | ⚠️ PII redatte | Varia | Varia | Varia |

> **Nota:** `vllm_offline` (backend in-process senza server) è riservato al sistema di benchmark interno e non è selezionabile dall'utente nelle Impostazioni.

**Circuit breaker:** Se il backend configurato non risponde, Spendif.ai fa fallback automatico su Ollama locale. Se anche Ollama è offline, la transazione viene importata con `to_review=True` e descrizione grezza.

---

### 9.0 llama.cpp (locale, default)

La scelta predefinita per le **nuove installazioni**: nessun servizio esterno necessario. Spendif.ai carica il modello GGUF direttamente in memoria tramite `llama-cpp-python`, senza bisogno di Ollama o altri server.

**Requisiti hardware minimi (inferenza locale):**

| Componente | Minimo | Consigliato |
|---|---|---|
| **RAM libera** | 4 GB (modelli 3B) | 8 GB (modelli 7B) |
| **CPU** | Qualsiasi x86-64 o ARM64 degli ultimi 5 anni | Apple Silicon (M1+) o CPU con AVX2 |
| **GPU** | Non necessaria | Apple Metal o CUDA/ROCm (accelera 3-5x) |
| **VRAM** | — (CPU-only o memoria unificata) | >= dimensione modello (es. 8 GB per 7B Q4) |
| **Disco** | 2.5 GB per modello | 5-7 GB per modello 7B |

> **Nota:** Se l'hardware non soddisfa i requisiti minimi, usa un backend remoto (OpenAI, Claude) — vedi sezioni 9.2 e 9.3.

**Modelli GGUF suggeriti:**

| Modello | Dimensione | Classificatore | Categorizzatore | Note |
|---|---|---|---|---|
| `Qwen2.5-7B-Instruct-Q4_K_M` | ~4.4 GB | single-step | buono | **Consigliato** — miglior rapporto qualità/dimensione |
| `gemma-4-E2B-it-Q4_K_M` | ~3.1 GB | multi-step | buono | Gemma 4 — architettura recente, ottima per italiano |
| `gemma-4-E2B-it-Q3_K_M` | ~2.7 GB | multi-step | buono | Gemma 4 quantizzazione leggera, per 4-6 GB RAM |
| `Qwen3.5-2B-Q4_K_M` | ~1.7 GB | multi-step | buono | Qwen 3.5 2B — leggero, ottimo rapporto qualità/dimensione |
| `Qwen3.5-4B-Q4_K_M` | ~2.5 GB | multi-step | buono | Qwen 3.5 4B — bilanciato qualità/velocità |
| `Phi-3-mini-4k-instruct-Q4_K_M` | ~2.2 GB | multi-step | buono | Buona qualità per la dimensione |
| `qwen2.5-3b-instruct-q4_k_m` | ~2.0 GB | multi-step | discreto | Minimo funzionante per classificazione |
| `gemma-3-12b-it-Q4_K_M` | ~6.8 GB | single-step | ottimo | Migliore qualità, richiede >= 8 GB RAM |

> **single-step vs multi-step:** I modelli >= 7B classificano in un'unica chiamata LLM (più veloce). I modelli 2-4B usano il classificatore multi-step (3 chiamate sequenziali, stessa qualità finale). Configurabile in Impostazioni → `classifier_mode`.

> **Gemma 4 E2B:** richiede `llama-cpp-python` aggiornato all'ultima versione (`uv pip install --upgrade llama-cpp-python`). Scaricare da `unsloth/gemma-4-E2B-it-GGUF` su HuggingFace.

**Scaricare un modello dall'app:**

1. Vai su **⚙️ Impostazioni → 🤖 Configurazione LLM**
2. Seleziona il backend **llama.cpp (locale)**
3. Nella sezione **Scarica modello**, scegli un modello suggerito o incolla un URL diretto a un file `.gguf` su HuggingFace
4. Clicca **⬇️ Scarica** — una barra di progresso mostra i MB scaricati

I modelli vengono salvati in `~/.spendifai/models/`. Se nella cartella sono presenti più file `.gguf`, viene usato il primo in ordine alfabetico.

**Gestione modelli locali:**

La sezione **Modelli locali** mostra i file `.gguf` presenti nella cartella modelli, con il percorso e la dimensione. Puoi selezionare quale usare dal campo **Percorso modello**.

| Campo | Default | Descrizione |
|---|---|---|
| **Percorso modello** | *(primo .gguf in `~/.spendifai/models/`)* | Percorso al file `.gguf` da usare |
| **Finestra di contesto (n_ctx)** | 0 = auto-detect | Token massimi di contesto. `0` = rileva automaticamente dall'header GGUF |

> **Auto-detect context window:** lascia `n_ctx = 0` (default). Spendif.ai legge il valore nativo direttamente dall'header del file GGUF senza caricare i pesi — nessuna configurazione manuale richiesta. Impostando un valore specifico (es. `2048`) si limita la RAM usata a scapito del contesto disponibile.

> **Accelerazione GPU:** llama.cpp usa automaticamente Apple Metal su Mac con chip Apple Silicon, CUDA su Linux/Windows con GPU NVIDIA, ROCm per GPU AMD. Per AMD su Linux (ROCm): `CMAKE_ARGS="-DGGML_HIPBLAS=on" uv pip install llama-cpp-python --upgrade`. Se il modello non supporta il ruolo `system`, Spendif.ai fonde automaticamente il system prompt nel prompt utente.

> **Selezione automatica basata su VRAM:** al primo avvio, Spendif.ai rileva la VRAM disponibile via `nvidia-smi` (NVIDIA) o `rocm-smi` (AMD). Su sistemi con GPU dedicata, il modello scaricato automaticamente è dimensionato sulla VRAM, non sulla RAM di sistema, per evitare download inutili di modelli troppo grandi. Su macOS (memoria unificata) la VRAM coincide con la RAM.

> **Token usage tracking:** ogni chiamata LLM viene registrata nella tabella `llm_usage_log` con token di input/output, durata, backend, fase del processo (`caller`/`step`) e nome del file sorgente. Questo consente di analizzare il consumo reale di token e convergere su un valore ottimale di `n_ctx` basato su media ± intervallo di confidenza al 95%, stratificato per banca, lingua e fase di elaborazione. Per llama.cpp locale, un controllo pre-invio verifica che il prompt non ecceda la finestra di contesto, evitando troncamenti silenziosi.

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
In questo caso imposta URL: `http://localhost:11434` (o l'IP del container se Spendif.ai è a sua volta in Docker).

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
4. Dai un nome (es. `Spendif.ai`) e copia la chiave — **mostrata una sola volta**
5. Assicurati di avere credito nel tuo account (sezione *Billing*)

**Configurazione in Spendif.ai:**
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
4. Dai un nome (es. `Spendif.ai`) e copia la chiave
5. Aggiungi credito in **Billing → Add Credits** (minimo $5)

**Configurazione in Spendif.ai:**
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

### 9.2b vLLM (server locale o remoto)

Usa un server vLLM già in esecuzione, compatibile con l'API OpenAI. Adatto a macchine Linux con GPU NVIDIA per la massima velocità di inferenza locale.

**Avviare il server (una tantum per sessione):**

```bash
# Installa vLLM (Linux + CUDA)
pip install vllm

# Avvia il server con un modello HuggingFace
vllm serve Qwen/Qwen2.5-7B-Instruct --port 8000

# Verifica
curl http://localhost:8000/v1/models
```

**Configurazione in Spendif.ai:**

```
Backend LLM:  vLLM
Base URL:     http://localhost:8000/v1   (o IP remoto se il server è su un'altra macchina)
```

Il modello servito viene rilevato automaticamente dal server — non serve inserirlo manualmente.

| Campo | Default | Descrizione |
|---|---|---|
| **Base URL** | `http://localhost:8000/v1` | URL del server vLLM |

> **Performance:** vLLM usa continuous batching e ottimizzazioni CUDA avanzate — significativamente più veloce di llama.cpp e Ollama su GPU NVIDIA, specialmente per modelli ≥ 7B.

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

**Modelli Groq utili per Spendif.ai:**

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
4. In Spendif.ai:

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
| `llm_backend` | `local_llama_cpp` | llama.cpp locale |
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
[ ] 7. Vai in 📥 Import e carica il primo file movimenti
```
