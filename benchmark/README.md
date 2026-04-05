# Spendif.ai — Benchmark

## Quick Start (zero-config)

Su una macchina qualsiasi — anche appena clonata o copiata su chiavetta USB.
Zero prerequisiti: serve solo internet. Lo script installa tutto da solo
(uv, Python, dipendenze, modelli GGUF).

### Benchmark completo (tutti i backend × classifier + categorizer) — CONSIGLIATO

**macOS / Linux:**
```bash
cd /path/to/sw_artifacts
bash benchmark/run_benchmark_full.sh
```

**Windows (PowerShell):**
```powershell
cd D:\sw_artifacts
powershell -ExecutionPolicy Bypass -File .\benchmark\run_benchmark_full.ps1
```

`run_benchmark_full.sh` / `run_benchmark_full.ps1` eseguono automaticamente:
1. Setup completo: scaricano i modelli GGUF mancanti + `ollama pull` dei modelli Ollama mancanti + rilevano vLLM
2. Benchmark **pipeline (classifier)** per ogni backend attivo (llama.cpp, Ollama se in esecuzione, vLLM se in esecuzione)
3. Benchmark **categorizer** per ogni backend attivo
4. La lista modelli viene letta da `benchmark/benchmark_models.csv`
5. `--runs N` si applica a entrambe le fasi

### Solo llama.cpp (skip altri backend)

```bash
bash benchmark/run_benchmark_full.sh --skip-ollama --skip-vllm
```

```powershell
powershell -ExecutionPolicy Bypass -File .\benchmark\run_benchmark_full.ps1 -SkipOllama -SkipVllm
```

> Su Parallels Desktop con cartella condivisa:
> ```powershell
> cd "\\Mac\Home\Documents\Progetti\PERSONALE\Spendif.ai\sw_artifacts"
> powershell -ExecutionPolicy Bypass -File .\benchmark\run_benchmark_full.ps1 -SkipOllama -SkipVllm
> ```

Entrambi fanno tutto in automatico:
1. Installano `uv` (se assente) — su Windows via `irm https://astral.sh/uv/install.ps1 | iex`
2. `uv` installa Python se non presente nel sistema
3. Creano `.venv` e sincronizzano le dipendenze (`uv sync`)
4. Scaricano i modelli GGUF da HuggingFace (se mancano)
5. Lanciano il benchmark su tutti i modelli GGUF con `llama.cpp`

### Opzioni

**`run_benchmark_full.sh` (macOS / Linux):**
```bash
bash benchmark/run_benchmark_full.sh                             # pipeline + categorizer, 1 run, tutti i backend
bash benchmark/run_benchmark_full.sh --benchmark pipeline        # solo pipeline
bash benchmark/run_benchmark_full.sh --benchmark categorizer     # solo categorizer
bash benchmark/run_benchmark_full.sh --benchmark both --runs 3   # entrambi, 3 run ciascuno
bash benchmark/run_benchmark_full.sh --setup-only                # solo download modelli, senza benchmark
bash benchmark/run_benchmark_full.sh --skip-ollama               # salta backend Ollama
bash benchmark/run_benchmark_full.sh --skip-vllm                 # salta backend vLLM
bash benchmark/run_benchmark_full.sh --skip-llama                # salta backend llama.cpp
bash benchmark/run_benchmark_full.sh --vllm-url http://host:8000/v1  # URL vLLM custom
bash benchmark/run_benchmark_full.sh --ollama-url http://host:11434   # URL Ollama custom
```

**`run_benchmark_full.ps1` (Windows PowerShell):**
```powershell
powershell -ExecutionPolicy Bypass -File .\benchmark\run_benchmark_full.ps1                            # default
powershell -ExecutionPolicy Bypass -File .\benchmark\run_benchmark_full.ps1 -Benchmark pipeline        # solo pipeline
powershell -ExecutionPolicy Bypass -File .\benchmark\run_benchmark_full.ps1 -Benchmark both -Runs 3    # entrambi, 3 run
powershell -ExecutionPolicy Bypass -File .\benchmark\run_benchmark_full.ps1 -SetupOnly                 # solo setup
powershell -ExecutionPolicy Bypass -File .\benchmark\run_benchmark_full.ps1 -SkipOllama               # salta Ollama
powershell -ExecutionPolicy Bypass -File .\benchmark\run_benchmark_full.ps1 -SkipVllm                 # salta vLLM
powershell -ExecutionPolicy Bypass -File .\benchmark\run_benchmark_full.ps1 -SkipLlama                # salta llama.cpp
powershell -ExecutionPolicy Bypass -File .\benchmark\run_benchmark_full.ps1 -VllmUrl http://host:8000/v1
powershell -ExecutionPolicy Bypass -File .\benchmark\run_benchmark_full.ps1 -OllamaUrl http://host:11434
```


## Architettura

```
benchmark/                        ← tutto il materiale di benchmark (root del repo)
│
│  ── ENTRY POINT ──────────────────────────────────────────────────────────
├── run_benchmark_full.sh         ← avvia benchmark completo — macOS/Linux
├── run_benchmark_full.ps1        ← avvia benchmark completo — Windows
│
│  ── MODULI PYTHON ─────────────────────────────────────────────────────────
├── benchmark_pipeline.py         ← benchmark classifier (schema + parsing)
├── benchmark_categorizer.py      ← benchmark categorizer (categorie)
├── aggregate_results.py          ← aggregatore statistico + modello OLS predittivo
├── generate_synthetic_files.py   ← genera i file sintetici di test
├── hw_monitor.py                 ← monitoraggio HW background (CPU + GPU)
├── monitor_benchmark.py          ← monitor avanzamento — Python cross-platform
│
│  ── SCRIPT AZURE ML ────────────────────────────────────────────────────────
├── azure_benchmark.py            ← benchmark su Azure ML (job remoto)
├── azure_run_cloud.sh            ← lancia azure_benchmark.py su cluster AML
│
│  ── SCRIPT OPERATIVI ──────────────────────────────────────────────────────
├── benchmark_models.csv          ← catalogo modelli (gguf + ollama)
├── .version                      ← versione YYYYMMDDHHMMSS-SHA7 (aggiornata da git hook)
├── monitor_benchmark.sh          ← monitor avanzamento — macOS/Linux
├── monitor_benchmark.ps1         ← monitor avanzamento — Windows
├── cleanup_benchmark.sh          ← pulizia file generati
├── diagnose.ps1                  ← diagnostica ambiente Windows (include GPU)
│
│  ── WORKFLOW MULTI-MACCHINA ────────────────────────────────────────────────
├── bench_push_usb.sh / .ps1      ← [dev]   copia codice + file sintetici → chiavetta USB
├── bench_load_usb.sh / .ps1      ← [bench] copia USB → ~/Desktop/spendif-ai (locale)
├── bench_save_usb.sh / .ps1      ← [bench] copia risultati + log locale → chiavetta USB
├── bench_pull_usb.sh / .ps1      ← [dev]   raccoglie CSV + log dalla chiavetta → dev
├── bench_push_ssh.sh / .ps1      ← [dev]   copia dev → host remoto via SSH (pre-run)
├── bench_pull_ssh.sh / .ps1      ← [dev]   raccoglie risultati host remoto → dev (post-run)
├── .rsync-bench-exclude          ← esclusioni condivise per gli script rsync
│
│  ── OUTPUT (gitignored) ────────────────────────────────────────────────────
├── results/                      ← CSV versionati per macchina (raccolti con pull)
│   └── <version>_<hostname>.csv  ← es. 20260404120000-a1b2c3d_bench-mac.csv
├── logs/                         ← log di esecuzione
│   ├── benchmark_YYYYMMDD_HHMMSS.log
│   ├── pipeline_YYYYMMDD_HHMMSS.log
│   └── categorizer_YYYYMMDD_HHMMSS.log
└── generated_files/              ← file sintetici input (generati PRIMA del bench)
    ├── manifest.csv
    └── *.csv, *.xlsx              ← file sintetici — generati con generate_synthetic_files.py
```

> **File sintetici**: devono essere generati esplicitamente PRIMA di eseguire il benchmark.
> NON vengono rigenerati automaticamente (per garantire determinismo tra run diversi):
> ```bash
> uv run python benchmark/generate_synthetic_files.py
> ```

## Tipi di benchmark

### Classifier (pipeline)

Misura la capacità dell'LLM di:
- Riconoscere lo schema del file (header, colonne)
- Parsare correttamente date, importi, tipo documento
- Rilevare la convenzione segni (dare/avere)

```bash
uv run python benchmark/benchmark_pipeline.py --runs 1 --backend local_llama_cpp \
  --model-path ~/.spendifai/models/qwen2.5-3b-instruct-q4_k_m.gguf
```

### Categorizer

Misura la capacità dell'LLM di assegnare le categorie corrette alle transazioni. Il classifier viene bypassato (usa ground truth) per isolare la performance di categorizzazione.

```bash
uv run python benchmark/benchmark_categorizer.py --runs 1 --backend local_llama_cpp \
  --model-path ~/.spendifai/models/qwen2.5-3b-instruct-q4_k_m.gguf
```

## Metriche

| Metrica | Classifier | Categorizer | Descrizione |
|---------|:----------:|:-----------:|-------------|
| header_match | x | | Schema header riconosciuto |
| rows_match | x | | Numero righe corretto |
| doc_type_match | x | | Tipo documento corretto |
| parse_rate | x | | % righe parsate |
| amount_accuracy | x | | Precisione importi |
| date_accuracy | x | | Precisione date |
| category_accuracy | | x | Categoria esatta |
| cat_fuzzy_accuracy | | x | Categoria fuzzy match |
| cat_fallback_rate | | x | % fallback (categoria default) |
| duration_seconds | x | x | Tempo di esecuzione |
| automation_score | x | x | Score composito |

## Full benchmark (tutti i backend × classifier + categorizer)

`run_benchmark_full.sh` / `run_benchmark_full.ps1` sono il punto di ingresso consigliato per eseguire un benchmark completo su tutti i backend disponibili.

### Cosa fa

1. **Setup automatico** — scarica i modelli GGUF mancanti da HuggingFace, esegue `ollama pull` per i modelli Ollama mancanti, rileva se vLLM è in esecuzione
2. **Benchmark pipeline** — esegue `benchmark_pipeline.py` per ogni backend attivo (llama.cpp, Ollama, vLLM)
3. **Benchmark categorizer** — esegue `benchmark_categorizer.py` per ogni backend attivo
4. **Lista modelli da CSV** — legge `benchmark/benchmark_models.csv` anziché array hardcoded
5. **`--runs N` unificato** — si applica a entrambe le fasi (pipeline e categorizer)

### Flags

| Flag (bash) | Flag (PS1) | Default | Descrizione |
|-------------|-----------|---------|-------------|
| `--benchmark pipeline\|categorizer\|both` | `-Benchmark` | `both` | Quale fase eseguire |
| `--runs N` | `-Runs N` | `1` | Numero di run per fase |
| `--setup-only` | `-SetupOnly` | off | Solo setup modelli, senza benchmark |
| `--skip-llama` | `-SkipLlama` | off | Salta backend llama.cpp |
| `--skip-ollama` | `-SkipOllama` | off | Salta backend Ollama |
| `--skip-vllm` | `-SkipVllm` | off | Salta backend vLLM |
| `--vllm-url URL` | `-VllmUrl URL` | `http://localhost:8000/v1` | URL server vLLM |
| `--ollama-url URL` | `-OllamaUrl URL` | `http://localhost:11434` | URL server Ollama |

### Note sui backend

- **llama.cpp** — sempre disponibile se i file GGUF sono presenti (scaricati automaticamente)
- **Ollama** — attivato solo se il server è in esecuzione al momento del lancio
- **vLLM** — attivato solo se il server è raggiungibile all'URL configurato; i modelli sono auto-rilevati dal server

## Catalogo modelli (benchmark_models.csv)

`benchmark/benchmark_models.csv` è la sorgente unica della lista modelli per tutti gli script di benchmark. Sostituisce gli array hardcoded nei vecchi script.

### Formato

```
name,gguf_file,gguf_repo,gguf_hf_url,ollama_tag,enabled
```

| Colonna | Descrizione |
|---------|-------------|
| `name` | Nome leggibile del modello |
| `gguf_file` | Nome file `.gguf` (se valorizzato → modello disponibile su llama.cpp) |
| `gguf_repo` | Repository HuggingFace da cui scaricare il file GGUF |
| `gguf_hf_url` | URL diretto HuggingFace per il download |
| `ollama_tag` | Tag Ollama (se valorizzato → modello disponibile su Ollama backend) |
| `enabled` | `true` / `false` — `false` salta il modello in tutti gli script |

### Modelli nel catalogo (11 modelli)

| Nome | GGUF | Ollama | Enabled |
|------|------|--------|---------|
| Qwen2.5-1.5B | `qwen2.5-1.5b-instruct-q4_k_m.gguf` | `qwen2.5:1.5b-instruct` | true |
| Gemma2-2B | `gemma-2-2b-it-Q4_K_M.gguf` | `gemma2:2b` | true |
| Qwen3.5-2B | `Qwen3.5-2B-Q4_K_M.gguf` | `qwen3.5:2b` | true |
| Qwen3.5-4B | `Qwen3.5-4B-Q4_K_M.gguf` | `qwen3.5:4b` | true |
| Gemma4-E2B Q3 | `gemma-4-E2B-it-Q3_K_M.gguf` | — | true |
| Gemma4-E2B Q4 | `gemma-4-E2B-it-Q4_K_M.gguf` | `gemma4:e2b` | true |
| Llama3.2-3B | `Llama-3.2-3B-Instruct-Q4_K_M.gguf` | `llama3.2:3b` | true |
| Qwen2.5-3B | `qwen2.5-3b-instruct-q4_k_m.gguf` | `qwen2.5:3b-instruct` | true |
| Phi3-mini-4k | `Phi-3-mini-4k-instruct-Q4_K_M.gguf` | `phi3:3.8b` | **false** |
| Qwen2.5-7B | `Qwen2.5-7B-Instruct-Q4_K_M.gguf` | `qwen2.5:7b-instruct` | true |
| Gemma3-12B | `gemma-3-12b-it-Q4_K_M.gguf` | `gemma3:12b` | true |

> **Phi3-mini-4k** ha `enabled=false`: context window 4096 potrebbe essere insufficiente su file sintetici lunghi. Il modello rimane nel CSV per riabilitazione futura.

### Come abilitare/disabilitare un modello

Per saltare un modello in tutti gli script, imposta `enabled=false` nella riga corrispondente:

```csv
Gemma3-12B,gemma-3-12b-it-Q4_K_M.gguf,...,gemma3:12b,false
```

Per aggiungere un nuovo modello, aggiungi una riga con `enabled=true`. Se `gguf_file` è vuoto, il modello non viene usato su llama.cpp; se `ollama_tag` è vuoto, non viene usato su Ollama.

**Nota:** vLLM non è nel CSV — i modelli serviti da vLLM vengono auto-rilevati dal server al runtime.

## Setup modelli

Il setup è automatico: `run_benchmark_full.sh` / `run_benchmark_full.ps1` gestiscono tutto in autonomia:
- Scaricano i modelli GGUF mancanti da HuggingFace
- Eseguono `ollama pull` per i modelli Ollama
- **Rilevano la GPU** e installano il wheel corretto di `llama-cpp-python`

Per solo setup senza benchmark:
```bash
bash benchmark/run_benchmark_full.sh --setup-only
```

---

## Installazione llama-cpp-python per GPU (automatica)

Lo step 2 di `run_benchmark_full.sh` / `run_benchmark_full.ps1` rileva la GPU **prima** di installare `llama-cpp-python` e installa automaticamente il wheel corretto:

| Piattaforma | Rilevamento | Wheel installato |
|-------------|-------------|-----------------|
| Apple Silicon (arm64 macOS) | `uname -m` == `arm64` | Standard PyPI wheel (Metal built-in) |
| NVIDIA + CUDA X.Y | `nvidia-smi` | `abetlen` pre-built wheel `cu121`..`cu125` (mappato alla versione ≤ rilevata) |
| AMD ROCm | `rocminfo` | Build from source (`CMAKE_ARGS=-DGGML_HIPBLAS=on`) |
| CPU / fallback | — | `abetlen` CPU-only wheel |

Il **limite dimensione modelli** usa la memoria GPU disponibile:
- **Metal** (Apple Silicon): 75% della RAM unificata
- **NVIDIA**: VRAM rilevata da `nvidia-smi`
- **CPU / ROCm**: RAM / 2 (invariato)

Il **SETUP SUMMARY** mostrato a fine setup include una riga `GPU: <descrizione>`.

Per verificare il supporto GPU a posteriori:
```bash
.venv/bin/python -c "from llama_cpp import llama_supports_gpu_offload; print(llama_supports_gpu_offload())"
```

---

## Context window auto-detect

Il benchmark rileva automaticamente la context window ottimale per ogni modello senza flag manuali:

- **llama.cpp** — legge `llama.context_length` dall'header GGUF senza caricare i pesi
- **Ollama** — interroga `/api/show` e legge il context del modello caricato
- **OpenAI / Claude** — lookup statico su `_KNOWN_CONTEXT` (es. gpt-4o=128k, claude-3-5=200k)
- **vLLM** — interroga `/v1/models`

**MIN_CTX = 4096**: i modelli con context window inferiore a 4096 token vengono saltati automaticamente. Il limite era 8000 — abbassato a 4096 per includere modelli come Phi3-mini-4k (che però è attualmente `enabled=false`).

Per forzare un valore specifico (es. limitare RAM):
```bash
uv run python benchmark/benchmark_pipeline.py --backend local_llama_cpp \
  --model-path ~/.spendifai/models/gemma-3-12b-it-Q4_K_M.gguf \
  --n-ctx 2048
```

`--n-ctx 0` (default) = auto-detect.

---

## Monitoraggio avanzamento (monitor_benchmark)

`benchmark/monitor_benchmark.sh` / `monitor_benchmark.ps1` / `monitor_benchmark.py` mostrano l'avanzamento del benchmark in tempo reale leggendo `results_all_runs.csv`.

### Funzionalità

- Progress bar per modello con percentuale completata, righe processate, elapsed e ETA
- Fase corrente (classifier / categorizer) rilevata dalla colonna `benchmark_type`
- Statistiche CPU e GPU live (via `HWMonitor.sample_once()`) e medie storiche dal CSV
- Refresh automatico ogni N secondi (configurabile)

### Opzioni

**macOS / Linux (`monitor_benchmark.sh`):**
```bash
bash benchmark/monitor_benchmark.sh                  # refresh ogni 5 s, tutti i modelli
bash benchmark/monitor_benchmark.sh --interval 10    # refresh ogni 10 s
bash benchmark/monitor_benchmark.sh --runs 3         # attende 3 run per modello
bash benchmark/monitor_benchmark.sh --total 100      # total righe attese
bash benchmark/monitor_benchmark.sh --once           # stampa snapshot e termina
bash benchmark/monitor_benchmark.sh --all            # mostra anche modelli completati
```

**Windows (`monitor_benchmark.ps1`):**
```powershell
powershell -ExecutionPolicy Bypass -File .\benchmark\monitor_benchmark.ps1
powershell -ExecutionPolicy Bypass -File .\benchmark\monitor_benchmark.ps1 -Interval 10
powershell -ExecutionPolicy Bypass -File .\benchmark\monitor_benchmark.ps1 -Runs 3
powershell -ExecutionPolicy Bypass -File .\benchmark\monitor_benchmark.ps1 -Once
```

**Python cross-platform (`monitor_benchmark.py`):**
```bash
uv run python benchmark/monitor_benchmark.py
uv run python benchmark/monitor_benchmark.py --interval 10 --runs 3 --once
```

---

## Monitoraggio HW (CPU + GPU)

Il modulo `benchmark/hw_monitor.py` (`HWMonitor`) campiona CPU e GPU **in background** ogni 0.5 s durante l'intero benchmark, restituendo medie più accurate rispetto ai vecchi campioni point-in-time (`_sample_cpu_load()` / `_sample_gpu_utilization()`, ora rimossi).

| Piattaforma | Metodo GPU | Note |
|-------------|-----------|------|
| macOS Apple Silicon | `ioreg` / AGXAccelerator → Device Utilization % | Nessun sudo richiesto |
| Linux NVIDIA | `nvidia-smi` → utilization % + power watts | Richiede driver NVIDIA |
| Linux AMD | `rocm-smi` → utilization % | Richiede ROCm |
| Fallback | — | GPU utilization = 0.0 |

`benchmark_pipeline.py` e `benchmark_categorizer.py` istanziano `HWMonitor` all'inizio del run e chiamano `stop()` alla fine per ottenere le medie. `monitor_benchmark` usa `HWMonitor.sample_once()` per le statistiche live.

### Diagnostica GPU (Windows)

`benchmark/diagnose.ps1` include un passo di rilevamento GPU (step 8/9): NVIDIA (`nvidia-smi` + CUDA), AMD (WMI), Intel Arc (oneAPI), Intel iGPU.

### Logging

Ogni esecuzione salva un log completo in `benchmark/logs/` (gitignored):

| Script | Log file |
|--------|----------|
| `run_benchmark_full.sh` | `benchmark/logs/benchmark_YYYYMMDD_HHMMSS.log` |
| `run_benchmark_full.sh` | `benchmark/logs/benchmark_YYYYMMDD_HHMMSS.log` |
| `benchmark_pipeline.py` | `benchmark/logs/pipeline_YYYYMMDD_HHMMSS.log` |
| `benchmark_categorizer.py` | `benchmark/logs/categorizer_YYYYMMDD_HHMMSS.log` |
| `diagnose.ps1` | `~/spendifai_diagnose_YYYYMMDD_HHMMSS.log` |

L'output va sia su console che su file (tee). I log non vengono sovrascritti — un file per ogni esecuzione con timestamp nel nome. Utili per:
- Troubleshooting errori su modelli specifici
- Confronto tra run diversi
- Audit tempi e warning

---

## Backend supportati

| Backend | Flag | Requisiti |
|---------|------|-----------|
| llama.cpp (locale) | `--backend local_llama_cpp` | File `.gguf` in `~/.spendifai/models/` |
| vLLM (locale/remoto) | `--backend vllm` | `vllm serve` in esecuzione |
| Ollama (locale) | `--backend local_ollama` | Ollama in esecuzione |
| OpenAI | `--backend openai` | `--api-key` o `$OPENAI_API_KEY` |
| Claude | `--backend claude` | `--api-key` o `$ANTHROPIC_API_KEY` |
| OpenAI-compatible | `--backend openai_compatible` | `--base-url` + `--api-key` |

### Usare vLLM

vLLM è un runtime ad alte prestazioni per LLM. Supporta guided JSON decoding nativo.

```bash
# 1. Installa vLLM (una volta)
pip install vllm

# 2. Lancia il server con un modello
vllm serve Qwen/Qwen2.5-3B-Instruct

# 3. Lancia il benchmark (auto-detect del modello servito)
uv run python benchmark/benchmark_pipeline.py --runs 1 --backend vllm

# Con URL e modello espliciti
uv run python benchmark/benchmark_pipeline.py --runs 1 --backend vllm \
  --base-url http://localhost:8000/v1 --model Qwen/Qwen2.5-3B-Instruct

# vLLM remoto (es. su GPU server)
uv run python benchmark/benchmark_pipeline.py --runs 1 --backend vllm \
  --base-url http://192.168.x.x:8000/v1
```

Vantaggi di vLLM rispetto a llama.cpp:
- **Continuous batching** — throughput molto più alto con richieste concorrenti
- **Guided decoding** — JSON schema enforcement nativo (no grammar)
- **GPU support** — CUDA, ROCm (ideale per GPU NVIDIA/AMD)
- **Modelli HuggingFace** — usa direttamente i modelli HF, senza conversione GGUF

## Modelli GGUF piccoli (inclusi in zero-config)

| Modello | Size | Quant | File |
|---------|------|-------|------|
| Qwen 2.5 1.5B Instruct | ~1.0 GB | Q4_K_M | `qwen2.5-1.5b-instruct-q4_k_m.gguf` |
| Gemma 2 2B IT | ~1.6 GB | Q4_K_M | `gemma-2-2b-it-Q4_K_M.gguf` |
| Llama 3.2 3B Instruct | ~1.9 GB | Q4_K_M | `Llama-3.2-3B-Instruct-Q4_K_M.gguf` |
| Qwen 2.5 3B Instruct | ~2.0 GB | Q4_K_M | `qwen2.5-3b-instruct-q4_k_m.gguf` |
| Phi-3 Mini 4K Instruct | ~2.2 GB | Q4_K_M | `Phi-3-mini-4k-instruct-Q4_K_M.gguf` |
| Qwen 3.5 2B | ~1.7 GB | Q4_K_M | `Qwen3.5-2B-Q4_K_M.gguf` |
| Qwen 3.5 4B | ~2.5 GB | Q4_K_M | `Qwen3.5-4B-Q4_K_M.gguf` |
| Gemma 4 E2B IT | ~2.7 GB | Q3_K_M | `gemma-4-E2B-it-Q3_K_M.gguf` |
| Gemma 4 E2B IT | ~3.1 GB | Q4_K_M | `gemma-4-E2B-it-Q4_K_M.gguf` |

Scaricati automaticamente da `run_benchmark_full.sh` / `run_benchmark_full.ps1`.
Fonte GGUF: `unsloth/gemma-4-E2B-it-GGUF` (HuggingFace). Richiede llama.cpp ≥ build con supporto architettura `gemma4`.

## Resume e deduplicazione

I risultati sono **append-only** in `results_all_runs.csv`. La chiave di resume è:

```
(run_id, filename, git_commit, git_branch, provider, model)
```

Se rilanci lo stesso benchmark con lo stesso modello e commit, le righe esistenti vengono skippate. Cambiando modello, commit, o hardware, vengono aggiunte nuove righe.

## Workflow collaborativo

```
Developer A (Mac M1)         GitHub              Developer B (Mac M4)
────────────────────        ────────            ────────────────────
git pull                    results_all_        git pull
  (prende righe di B)       runs.csv            (prende righe di A)
                            (cumulativo)
bash benchmark/run_benchmark_full.sh                bash benchmark/run_benchmark_full.sh
  resume: skip esistenti                          resume: skip esistenti
  aggiunge solo nuove                             aggiunge solo nuove

git push ───────────────► merge CSV ◄─────────── git push
```

Ogni riga include `runtime_os`, `runtime_cpu`, `runtime_ram_gb`, `runtime_gpu` — filtrabile per confrontare performance tra macchine diverse.

## Benchmark cross-platform (Mac remoto)

Per confrontare con hardware diverso via rete:

```bash
# Su Mac remoto: lancia llama-server
llama-server -m ~/.spendifai/models/gemma-3-12b-it-Q4_K_M.gguf \
  --host 0.0.0.0 --port 8080 -ngl 99 -c 4096

# Su Mac locale: punta al remoto
uv run python benchmark/benchmark_pipeline.py --runs 1 \
  --backend openai_compatible \
  --base-url http://192.168.x.x:8080/v1 \
  --model gemma-3-12b-it
```

---

## Versionamento risultati

Ogni run produce un CSV versionato in `benchmark/results/` con nome:

```
<YYYYMMDDHHMMSS>-<SHA7>_<hostname>.csv
```

Esempio: `20260404120000-a1b2c3d_bench-mac.csv`

La versione viene letta da `benchmark/.version`, un file di testo committato nel repo e
aggiornato automaticamente dal **post-commit hook** a ogni commit:

```sh
# .git/hooks/post-commit
TS=$(date +%Y%m%d%H%M%S)
SHA=$(git rev-parse --short HEAD)
printf '%s-%s\n' "$TS" "$SHA" > benchmark/.version
```

**Perché `.version` e non `git describe`?**
Su macchine bench senza git (chiavetta USB, host remoti senza repo clonato) non è
possibile eseguire `git rev-parse`. Il file `.version` viaggia con il codice e
garantisce la tracciabilità anche in assenza di git.

Se il file non esiste, lo script fa fallback a `git rev-parse --short HEAD`
(oppure `unknown` se nemmeno git è disponibile).

### Colonne aggiuntive nei CSV versionati

| Colonna | Descrizione |
|---------|-------------|
| `version` | Stringa da `.version` (es. `20260404120000-a1b2c3d`) |
| `runtime_hostname` | Hostname della macchina bench |
| `runtime_gpu_ram_gb` | VRAM GPU in GB (Apple: RAM unificata; NVIDIA: nvidia-smi) |
| `tokens_per_second` | Token/s = `total_tokens / duration_seconds` |

`benchmark/results/` è in `.gitignore` — i CSV rimangono locali sul bench e vengono
raccolti esplicitamente con `bench_pull_usb.sh` / `bench_pull_ssh.sh` (o `.ps1`).

---

## Workflow multi-macchina (USB e SSH)

Per girare il benchmark su una macchina diversa dalla dev (es. un Mac dedicato al bench,
un PC Windows, un server Linux), usa gli script in `benchmark/`.

> **Prerequisito**: generare i file sintetici PRIMA del push, se non già presenti:
> ```bash
> uv run python benchmark/generate_synthetic_files.py
> ```
> I file sintetici **non vengono rigenerati automaticamente** — questo garantisce
> che ogni macchina esegua esattamente gli stessi input (determinismo).

### USB — flusso completo (6 passi)

```
[dev]  bench_push_usb.sh --dest /Volumes/BENCH_USB
         └── copia codice + file sintetici (generated_files/) sulla chiavetta
              │
              ▼ (porta chiavetta alla macchina bench)
[bench] bench_load_usb.sh --from /Volumes/BENCH_USB
         └── copia USB → ~/Desktop/spendif-ai (disco locale)
              │
              ▼
[bench] run_benchmark_full.sh     ← gira su disco locale, NON su USB
         └── produce benchmark/results/<ver>_<host>.csv
              │
              ▼
[bench] bench_save_usb.sh --dest /Volumes/BENCH_USB
         └── copia risultati + log locale → chiavetta
              │
              ▼ (porta chiavetta al dev)
[dev]  bench_pull_usb.sh --from /Volumes/BENCH_USB
         └── raccoglie CSV + log dalla chiavetta → benchmark/results/ e benchmark/logs/
              │
              ▼
[dev]  aggregate_results.py --predict
         └── aggrega → documents/04_software_engineering/benchmark/results_all_runs.csv
```

```bash
# Passo 1 [dev] — copia codice + file sintetici sulla chiavetta
# Linux/macOS:
bash benchmark/bench_push_usb.sh --dest /Volumes/BENCH_USB
# Windows:
# powershell -ExecutionPolicy Bypass -File benchmark\bench_push_usb.ps1 -Dest E:\BENCH_USB

# Passo 2 [bench] — copia USB → disco locale  (lo script è SULLA chiavetta)
# Linux/macOS:
bash /Volumes/BENCH_USB/benchmark/bench_load_usb.sh --from /Volumes/BENCH_USB
# Windows:
# powershell -ExecutionPolicy Bypass -File E:\BENCH_USB\benchmark\bench_load_usb.ps1 -From E:\BENCH_USB

# Passo 3 [bench] — entra nella cartella e avvia il benchmark
# Linux/macOS:
cd ~/Desktop/spendif-ai && bash benchmark/run_benchmark_full.sh
# Windows:
# Set-Location $env:USERPROFILE\Desktop\spendif-ai; powershell -ExecutionPolicy Bypass -File benchmark\run_benchmark_full.ps1

# Passo 4 [bench] — salva risultati sulla chiavetta
# Linux/macOS:
bash benchmark/bench_save_usb.sh --dest /Volumes/BENCH_USB
# Windows:
# powershell -ExecutionPolicy Bypass -File benchmark\bench_save_usb.ps1 -Dest E:\BENCH_USB

# Passo 5 [dev] — raccoglie risultati dalla chiavetta
# Linux/macOS:
bash benchmark/bench_pull_usb.sh --from /Volumes/BENCH_USB
# Windows:
# powershell -ExecutionPolicy Bypass -File benchmark\bench_pull_usb.ps1 -From E:\BENCH_USB

# Passo 6 [dev] — aggrega
uv run python benchmark/aggregate_results.py --predict
```

Opzioni push: `--clean` / `-Clean` (cancella dest prima), `--dry-run` / `-DryRun`.

### SSH — flusso (3 passi, invariato)

```
[dev]  bench_push_ssh.sh --dest user@host:~/Desktop/spendif-ai
[bench] run_benchmark_full.sh  (già in loco, gira su disco locale)
[dev]  bench_pull_ssh.sh --from user@host:~/Desktop/spendif-ai
```

**Linux / macOS:**
```bash
# 1. Copia sul host remoto
bash benchmark/bench_push_ssh.sh --dest user@bench-host:~/Desktop/spendif-ai

# 2. Sul bench: esegui
ssh user@bench-host "bash ~/Desktop/spendif-ai/benchmark/run_benchmark_full.sh"
# Windows bench:
# ssh user@bench-host "powershell -ExecutionPolicy Bypass -File benchmark\run_benchmark_full.ps1"

# 3. Raccogli i risultati
bash benchmark/bench_pull_ssh.sh --from user@bench-host:~/Desktop/spendif-ai
```

**Windows (PowerShell):**
```powershell
# 1. Copia sul host remoto
powershell -ExecutionPolicy Bypass -File benchmark\bench_push_ssh.ps1 -Dest user@bench-host:~/Desktop/spendif-ai

# 2. Sul bench (via SSH — come sopra)

# 3. Raccogli
powershell -ExecutionPolicy Bypass -File benchmark\bench_pull_ssh.ps1 -From user@bench-host:~/Desktop/spendif-ai
```

Opzioni SSH aggiuntive: `--key`/`-Key PATH` (chiave privata), `--port`/`-Port N` (porta, default 22).

---

## Workflow C — Azure ML (cloud GPU)

Per eseguire il benchmark su cluster GPU Azure ML senza macchine locali dedicate.
Nessuna copia manuale (USB/SSH): i risultati vengono scaricati direttamente dal datastore AML.

### Prerequisiti one-time (setup risorse Azure)

```bash
az group create -n spendifai-rg -l westeurope
az ml workspace create -n spendifai-ml -g spendifai-rg
az acr create -n spendifaiacr -g spendifai-rg --sku Basic
az ml compute create -n gpu-t4-spot -g spendifai-rg \
  -w spendifai-ml --type AmlCompute \
  --size Standard_NC6s_v3 --min-instances 0 --max-instances 5 --tier low_priority
```

### Variabili d'ambiente (`.env` o shell)

| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `AZURE_SUBSCRIPTION_ID` | — | ID subscription Azure (obbligatorio) |
| `AZURE_RESOURCE_GROUP` | `spendifai-rg` | Resource group |
| `AZURE_ML_WORKSPACE` | `spendifai-ml` | Workspace Azure ML |
| `AZURE_ACR_NAME` | `spendifaiacr` | Azure Container Registry |
| `AZURE_COMPUTE_TARGET` | `cpu-bench` | Nome compute target AML |

### Flusso a 4 passi

```
[dev] azure_run_cloud.sh (o azure_benchmark.py)
  → (opzionale) build Docker image → push ACR
  → submit AML Job per ogni modello (conda o docker mode)
  → poll ogni 60 s fino a Completed
  → download results/ da AML datastore
      (azureml://datastores/workspaceblobdefault/paths/benchmarks/<job>/)
  → merge in benchmark/results_all_runs.csv (append-only, dedup per chiave)
  → git commit + PR automatica (verify_bench_csv.py in CI)
  → aggregate_results.py → dual-write
```

### Modalità di esecuzione

**Modalità `conda` (default, consigliata)** — nessun Docker locale richiesto; Azure ML costruisce l'ambiente da `docker/conda_benchmark.yml` sopra l'immagine curata `mcr.microsoft.com/azureml/curated/acft-hf-nlp-gpu:latest`.

**Modalità `docker`** — usa un'immagine pre-buildata su ACR (`spendifaiacr.azurecr.io/spendifai-bench:latest`). Richiede `--build` prima del primo run o dopo modifiche alle dipendenze.

### Comandi principali (`azure_benchmark.py`)

```bash
# Singolo modello, attende completamento
uv run python benchmark/azure_benchmark.py --model qwen2.5-3b

# Tutti i modelli dal registry (modalità conda — default)
uv run python benchmark/azure_benchmark.py --all-models

# Esplicitamente modalità conda
uv run python benchmark/azure_benchmark.py --all-models --mode conda

# Submit senza attendere il completamento
uv run python benchmark/azure_benchmark.py --model qwen2.5-3b --no-wait

# Lista job recenti (prefisso bench-)
uv run python benchmark/azure_benchmark.py --list

# Download e merge risultati da job completato
uv run python benchmark/azure_benchmark.py --download --job-name bench-qwen253b-202604041200

# Solo build + push Docker su ACR (modalità docker)
uv run python benchmark/azure_benchmark.py --build

# Modalità docker: build + submit
uv run python benchmark/azure_benchmark.py --build --all-models --mode docker
```

### Script orchestratore end-to-end (`azure_run_cloud.sh`)

`azure_run_cloud.sh` esegue i 5 step in sequenza: verifica prerequisiti (Azure CLI, Docker, GitHub CLI, `azure-ai-ml` SDK) → build & push Docker → submit job(s) → poll → download + PR.

```bash
bash benchmark/azure_run_cloud.sh                    # run completo (tutti i modelli)
bash benchmark/azure_run_cloud.sh --skip-build       # salta Docker build
bash benchmark/azure_run_cloud.sh --model qwen2.5-3b # singolo modello
```

### Note

- **Nessuna copia manuale**: a differenza di USB/SSH, i risultati vengono scaricati automaticamente dal datastore AML dopo il completamento del job.
- **Jobs paralleli**: ogni modello viene sottomesso come job separato su AML; più modelli girano in parallelo sul cluster.
- **GPU T4 spot**: il compute target `gpu-t4-spot` usa istanze low-priority (`Standard_NC6s_v3`) — costo ridotto, possibile prelazione.
- **Dedup append-only**: il merge in `results_all_runs.csv` usa la chiave `run_id + filename + git_commit + git_branch + provider + model + benchmark_type` — i duplicati vengono ignorati.
- **`verify_bench_csv.py` come guardrail CI**: la PR creata automaticamente da `azure_run_cloud.sh` triggerà il check CI che valida la struttura e la consistenza del CSV aggregato prima del merge.

---

### Cosa viene copiato (push USB/SSH)

| Incluso | Escluso |
|---------|---------|
| `benchmark/benchmark_*.py`, `run_benchmark_full.*` | `benchmark/results/` |
| `benchmark/benchmark_models.csv`, `benchmark/.version` | `benchmark/logs/` |
| `benchmark/generated_files/` (file sintetici) | `.git/`, `.venv/`, `.pytest_cache/`, `__pycache__/` |
| `core/`, `services/`, `db/`, `support/` | `ui/`, `docs/`, `api/`, `reports/` |
| `pyproject.toml`, `uv.lock` | |

### Cosa viene raccolto (pull)

| Workflow | File | Destinazione locale |
|----------|------|---------------------|
| USB / SSH | `benchmark/results/*.csv` | `benchmark/results/` |
| USB / SSH | `benchmark/logs/` | `benchmark/logs/` |
| Azure ML | `results/` dal datastore AML (`azureml://datastores/workspaceblobdefault/paths/benchmarks/<job>/`) | `benchmark/results/` (download diretto, no USB/SSH) |

---

## Aggregazione risultati e modello predittivo

`benchmark/aggregate_results.py` legge tutti i CSV in `benchmark/results/` (o un file
specificato) e produce:

1. **Tabella aggregata** per modello × macchina: `mean`, `median`, `std` delle metriche
   chiave (`duration_seconds`, `tokens_per_second`, `parse_rate`, `amount_accuracy`,
   `cat_fuzzy_accuracy`, …)

2. **Modello regressivo OLS** per stimare la durata a partire dalle caratteristiche
   HW/modello — separato per classifier (s/file) e categorizer (s/10 transazioni):

   ```
   duration = β0
             + β1 × param_B          (dimensione modello in miliardi di param)
             + β2 × quant_bits        (2.5 per Q2_K … 16 per F16)
             + β3 × gpu_offload       (1 se n_gpu_layers > 0)
             + β4 × cpu_ram_gb
             + β5 × gpu_ram_gb
             + β6 × n_threads
             + β7 × apple_silicon     (1 se CPU Apple)
   ```

   Con `statsmodels` (se installato): R², coefficienti, p-value, CI 95%.
   Fallback `numpy` (pinv robusto su matrici rank-deficient): CI 95% via t-critico.

3. **Previsioni esemplificative** (con `--predict`): stima della durata per
   configurazioni HW tipiche (Mac M3 Pro 36 GB, Linux RTX 3090, …).

### Uso

```bash
# Aggrega tutti i CSV in benchmark/results/ (auto-discovery)
uv run python benchmark/aggregate_results.py

# Con previsioni esemplificative
uv run python benchmark/aggregate_results.py --predict

# Solo classifier o solo categorizer
uv run python benchmark/aggregate_results.py --type classifier

# CSV specifico
uv run python benchmark/aggregate_results.py --csv benchmark/results/20260404-a1b2c3d_bench-mac.csv

# Salva report su file
uv run python benchmark/aggregate_results.py --predict --output report.txt

# Elenca i CSV disponibili
uv run python benchmark/aggregate_results.py --list
```

### Output dual-write

`aggregate_results.py` scrive il CSV aggregato su **due path in parallelo**:

| Path | Scopo |
|------|-------|
| `benchmark/results_all_runs.csv` | **Primario** — tracciato in `sw_artifacts`, verificato da `verify_bench_csv.py` in CI |
| `documents/04_software_engineering/benchmark/results_all_runs.csv` | Mirror per consultazione senza aprire il repo codice |

### Priorità sorgente dati

1. `--csv PATH` (override esplicito)
2. `benchmark/results/*.csv` (tutti i file versionati, ordinati per mtime)
3. `benchmark/results_all_runs.csv` (fallback CSV aggregato primario)

---

## Riferimenti

- Documentazione completa: `docs/developer_guide.md` § 10
- Azure ML benchmark: `benchmark/azure_benchmark.py`, `benchmark/azure_run_cloud.sh`
- Catalogo modelli: `benchmark/benchmark_models.csv`
- Strategia benchmark (architettura, OLS, workflow): `documents/04_software_engineering/09_benchmark_strategy.tex`
