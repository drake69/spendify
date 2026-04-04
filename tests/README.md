# Spendif.ai — Benchmark

## Quick Start (zero-config)

Su una macchina qualsiasi — anche appena clonata o copiata su chiavetta USB.
Zero prerequisiti: serve solo internet. Lo script installa tutto da solo
(uv, Python, dipendenze, modelli GGUF).

### Benchmark completo (tutti i backend × classifier + categorizer) — CONSIGLIATO

**macOS / Linux:**
```bash
cd /path/to/sw_artifacts
bash tests/run_benchmark_full.sh
```

**Windows (PowerShell):**
```powershell
cd D:\sw_artifacts
powershell -ExecutionPolicy Bypass -File .\tests\run_benchmark_full.ps1
```

`run_benchmark_full.sh` / `run_benchmark_full.ps1` eseguono automaticamente:
1. Setup completo: scaricano i modelli GGUF mancanti + `ollama pull` dei modelli Ollama mancanti + rilevano vLLM
2. Benchmark **pipeline (classifier)** per ogni backend attivo (llama.cpp, Ollama se in esecuzione, vLLM se in esecuzione)
3. Benchmark **categorizer** per ogni backend attivo
4. La lista modelli viene letta da `tests/benchmark_models.csv`
5. `--runs N` si applica a entrambe le fasi

### Solo llama.cpp (skip altri backend)

```bash
bash tests/run_benchmark_full.sh --skip-ollama --skip-vllm
```

```powershell
powershell -ExecutionPolicy Bypass -File .\tests\run_benchmark_full.ps1 -SkipOllama -SkipVllm
```

> Su Parallels Desktop con cartella condivisa:
> ```powershell
> cd "\\Mac\Home\Documents\Progetti\PERSONALE\Spendif.ai\sw_artifacts"
> powershell -ExecutionPolicy Bypass -File .\tests\run_benchmark_full.ps1 -SkipOllama -SkipVllm
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
bash tests/run_benchmark_full.sh                             # pipeline + categorizer, 1 run, tutti i backend
bash tests/run_benchmark_full.sh --benchmark pipeline        # solo pipeline
bash tests/run_benchmark_full.sh --benchmark categorizer     # solo categorizer
bash tests/run_benchmark_full.sh --benchmark both --runs 3   # entrambi, 3 run ciascuno
bash tests/run_benchmark_full.sh --setup-only                # solo download modelli, senza benchmark
bash tests/run_benchmark_full.sh --skip-ollama               # salta backend Ollama
bash tests/run_benchmark_full.sh --skip-vllm                 # salta backend vLLM
bash tests/run_benchmark_full.sh --skip-llama                # salta backend llama.cpp
bash tests/run_benchmark_full.sh --vllm-url http://host:8000/v1  # URL vLLM custom
bash tests/run_benchmark_full.sh --ollama-url http://host:11434   # URL Ollama custom
```

**`run_benchmark_full.ps1` (Windows PowerShell):**
```powershell
powershell -ExecutionPolicy Bypass -File .\tests\run_benchmark_full.ps1                            # default
powershell -ExecutionPolicy Bypass -File .\tests\run_benchmark_full.ps1 -Benchmark pipeline        # solo pipeline
powershell -ExecutionPolicy Bypass -File .\tests\run_benchmark_full.ps1 -Benchmark both -Runs 3    # entrambi, 3 run
powershell -ExecutionPolicy Bypass -File .\tests\run_benchmark_full.ps1 -SetupOnly                 # solo setup
powershell -ExecutionPolicy Bypass -File .\tests\run_benchmark_full.ps1 -SkipOllama               # salta Ollama
powershell -ExecutionPolicy Bypass -File .\tests\run_benchmark_full.ps1 -SkipVllm                 # salta vLLM
powershell -ExecutionPolicy Bypass -File .\tests\run_benchmark_full.ps1 -SkipLlama                # salta llama.cpp
powershell -ExecutionPolicy Bypass -File .\tests\run_benchmark_full.ps1 -VllmUrl http://host:8000/v1
powershell -ExecutionPolicy Bypass -File .\tests\run_benchmark_full.ps1 -OllamaUrl http://host:11434
```


## Architettura

```
tests/
├── run_benchmark_full.sh         ← ENTRY POINT full benchmark (tutti i backend × pipeline + categorizer) — macOS/Linux
├── run_benchmark_full.ps1        ← ENTRY POINT full benchmark (tutti i backend × pipeline + categorizer) — Windows
├── cleanup_benchmark.sh          ← pulizia file generati
├── benchmark_models.csv          ← catalogo modelli (sostituisce array hardcoded negli script)
├── benchmark_pipeline.py         ← benchmark classifier (schema + parsing)
├── benchmark_categorizer.py      ← benchmark categorizer (categorie)
├── hw_monitor.py                 ← monitoraggio HW background (CPU + GPU cross-platform)
├── monitor_benchmark.sh          ← monitor avanzamento benchmark (macOS/Linux)
├── monitor_benchmark.ps1         ← monitor avanzamento benchmark (Windows)
├── monitor_benchmark.py          ← monitor avanzamento benchmark (cross-platform Python)
├── diagnose.ps1                  ← diagnostica ambiente Windows (include GPU)
├── generate_synthetic_files.py   ← genera i file sintetici di test
├── logs/                         ← LOG di ogni esecuzione benchmark (gitignored)
│   ├── benchmark_YYYYMMDD_HHMMSS.log      ← log run_benchmark_full.sh
│   ├── pipeline_YYYYMMDD_HHMMSS.log       ← log benchmark_pipeline.py
│   └── categorizer_YYYYMMDD_HHMMSS.log    ← log benchmark_categorizer.py
└── generated_files/
    ├── manifest.csv              ← elenco file sintetici + ground truth
    ├── benchmark/
    │   ├── benchmark_config.json      ← config ultimo run pipeline
    │   ├── cat_benchmark_config.json  ← config ultimo run categorizer
    │   ├── results_all_runs.csv       ← RISULTATI CUMULATIVI (append-only)
    │   ├── summary_global.csv         ← aggregati per modello
    │   └── summary_variance.csv       ← analisi varianza tra run
    └── *.csv, *.xlsx                  ← file sintetici
```

## Tipi di benchmark

### Classifier (pipeline)

Misura la capacità dell'LLM di:
- Riconoscere lo schema del file (header, colonne)
- Parsare correttamente date, importi, tipo documento
- Rilevare la convenzione segni (dare/avere)

```bash
uv run python tests/benchmark_pipeline.py --runs 1 --backend local_llama_cpp \
  --model-path ~/.spendifai/models/qwen2.5-3b-instruct-q4_k_m.gguf
```

### Categorizer

Misura la capacità dell'LLM di assegnare le categorie corrette alle transazioni. Il classifier viene bypassato (usa ground truth) per isolare la performance di categorizzazione.

```bash
uv run python tests/benchmark_categorizer.py --runs 1 --backend local_llama_cpp \
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
4. **Lista modelli da CSV** — legge `tests/benchmark_models.csv` anziché array hardcoded
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

`tests/benchmark_models.csv` è la sorgente unica della lista modelli per tutti gli script di benchmark. Sostituisce gli array hardcoded nei vecchi script.

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
| Phi3-mini | `Phi-3-mini-4k-instruct-Q4_K_M.gguf` | `phi3:3.8b` | true |
| Qwen2.5-7B | `Qwen2.5-7B-Instruct-Q4_K_M.gguf` | `qwen2.5:7b-instruct` | true |
| Gemma3-12B | `gemma-3-12b-it-Q4_K_M.gguf` | `gemma3:12b` | true |

### Come abilitare/disabilitare un modello

Per saltare un modello in tutti gli script, imposta `enabled=false` nella riga corrispondente:

```csv
Gemma3-12B,gemma-3-12b-it-Q4_K_M.gguf,...,gemma3:12b,false
```

Per aggiungere un nuovo modello, aggiungi una riga con `enabled=true`. Se `gguf_file` è vuoto, il modello non viene usato su llama.cpp; se `ollama_tag` è vuoto, non viene usato su Ollama.

**Nota:** vLLM non è nel CSV — i modelli serviti da vLLM vengono auto-rilevati dal server al runtime.

## Setup modelli

Il setup è automatico: `run_benchmark_full.sh` scarica i modelli GGUF mancanti e fa `ollama pull` per i modelli Ollama. La lista modelli è in `tests/benchmark_models.csv`.

Per solo setup senza benchmark:
```bash
bash tests/run_benchmark_full.sh --setup-only
```

---

## Installazione llama-cpp-python per GPU

Il build default installato da `uv sync` usa CPU. Per sfruttare la GPU:

### NVIDIA (CUDA)
```bash
CMAKE_ARGS="-DGGML_CUDA=on" uv pip install llama-cpp-python --upgrade
```

### AMD (ROCm)
```bash
# Prerequisito: ROCm installato (sudo apt install rocm-dev hipblas-dev)
CMAKE_ARGS="-DGGML_HIPBLAS=on" uv pip install llama-cpp-python --upgrade
```

### Apple Silicon (Metal) — default su macOS
```bash
# Già abilitato automaticamente su Mac con chip Apple Silicon
uv pip install llama-cpp-python --upgrade
```

### Verificare il supporto GPU
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

Per forzare un valore specifico (es. limitare RAM):
```bash
uv run python tests/benchmark_pipeline.py --backend local_llama_cpp \
  --model-path ~/.spendifai/models/gemma-3-12b-it-Q4_K_M.gguf \
  --n-ctx 2048
```

`--n-ctx 0` (default) = auto-detect.

---

## Monitoraggio avanzamento (monitor_benchmark)

`tests/monitor_benchmark.sh` / `monitor_benchmark.ps1` / `monitor_benchmark.py` mostrano l'avanzamento del benchmark in tempo reale leggendo `results_all_runs.csv`.

### Funzionalità

- Progress bar per modello con percentuale completata, righe processate, elapsed e ETA
- Fase corrente (classifier / categorizer) rilevata dalla colonna `benchmark_type`
- Statistiche CPU e GPU live (via `HWMonitor.sample_once()`) e medie storiche dal CSV
- Refresh automatico ogni N secondi (configurabile)

### Opzioni

**macOS / Linux (`monitor_benchmark.sh`):**
```bash
bash tests/monitor_benchmark.sh                  # refresh ogni 5 s, tutti i modelli
bash tests/monitor_benchmark.sh --interval 10    # refresh ogni 10 s
bash tests/monitor_benchmark.sh --runs 3         # attende 3 run per modello
bash tests/monitor_benchmark.sh --total 100      # total righe attese
bash tests/monitor_benchmark.sh --once           # stampa snapshot e termina
bash tests/monitor_benchmark.sh --all            # mostra anche modelli completati
```

**Windows (`monitor_benchmark.ps1`):**
```powershell
powershell -ExecutionPolicy Bypass -File .\tests\monitor_benchmark.ps1
powershell -ExecutionPolicy Bypass -File .\tests\monitor_benchmark.ps1 -Interval 10
powershell -ExecutionPolicy Bypass -File .\tests\monitor_benchmark.ps1 -Runs 3
powershell -ExecutionPolicy Bypass -File .\tests\monitor_benchmark.ps1 -Once
```

**Python cross-platform (`monitor_benchmark.py`):**
```bash
uv run python tests/monitor_benchmark.py
uv run python tests/monitor_benchmark.py --interval 10 --runs 3 --once
```

---

## Monitoraggio HW (CPU + GPU)

Il modulo `tests/hw_monitor.py` (`HWMonitor`) campiona CPU e GPU **in background** ogni 0.5 s durante l'intero benchmark, restituendo medie più accurate rispetto ai vecchi campioni point-in-time (`_sample_cpu_load()` / `_sample_gpu_utilization()`, ora rimossi).

| Piattaforma | Metodo GPU | Note |
|-------------|-----------|------|
| macOS Apple Silicon | `ioreg` / AGXAccelerator → Device Utilization % | Nessun sudo richiesto |
| Linux NVIDIA | `nvidia-smi` → utilization % + power watts | Richiede driver NVIDIA |
| Linux AMD | `rocm-smi` → utilization % | Richiede ROCm |
| Fallback | — | GPU utilization = 0.0 |

`benchmark_pipeline.py` e `benchmark_categorizer.py` istanziano `HWMonitor` all'inizio del run e chiamano `stop()` alla fine per ottenere le medie. `monitor_benchmark` usa `HWMonitor.sample_once()` per le statistiche live.

### Diagnostica GPU (Windows)

`tests/diagnose.ps1` include un passo di rilevamento GPU (step 8/9): NVIDIA (`nvidia-smi` + CUDA), AMD (WMI), Intel Arc (oneAPI), Intel iGPU.

### Logging

Ogni esecuzione salva un log completo in `tests/logs/` (gitignored):

| Script | Log file |
|--------|----------|
| `run_benchmark_full.sh` | `tests/logs/benchmark_YYYYMMDD_HHMMSS.log` |
| `run_benchmark_full.sh` | `tests/logs/benchmark_YYYYMMDD_HHMMSS.log` |
| `benchmark_pipeline.py` | `tests/logs/pipeline_YYYYMMDD_HHMMSS.log` |
| `benchmark_categorizer.py` | `tests/logs/categorizer_YYYYMMDD_HHMMSS.log` |
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
uv run python tests/benchmark_pipeline.py --runs 1 --backend vllm

# Con URL e modello espliciti
uv run python tests/benchmark_pipeline.py --runs 1 --backend vllm \
  --base-url http://localhost:8000/v1 --model Qwen/Qwen2.5-3B-Instruct

# vLLM remoto (es. su GPU server)
uv run python tests/benchmark_pipeline.py --runs 1 --backend vllm \
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
bash tests/run_benchmark_full.sh                bash tests/run_benchmark_full.sh
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
uv run python tests/benchmark_pipeline.py --runs 1 \
  --backend openai_compatible \
  --base-url http://192.168.x.x:8080/v1 \
  --model gemma-3-12b-it
```

## Riferimenti

- Documentazione completa: `docs/developer_guide.md` § 10
- Azure ML benchmark: `tools/azure_benchmark.py`
- Catalogo modelli: `tests/benchmark_models.csv`
- Catalogo modelli: `tests/benchmark_models.csv`
# test
test
hook test
