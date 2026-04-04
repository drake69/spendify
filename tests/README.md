# Spendify — Benchmark

## Quick Start (zero-config)

Su una macchina qualsiasi — anche appena clonata o copiata su chiavetta USB.
Zero prerequisiti: serve solo internet. Lo script installa tutto da solo
(uv, Python, dipendenze, modelli GGUF).

**macOS / Linux:**
```bash
cd /path/to/sw_artifacts
bash tests/run_benchmark.sh
```

**Windows (PowerShell):**
```powershell
cd D:\sw_artifacts        # o il percorso della chiavetta/copia
powershell -ExecutionPolicy Bypass -File .\tests\run_benchmark.ps1
```

> Su Parallels Desktop con cartella condivisa:
> ```powershell
> cd "\\Mac\Home\Documents\Progetti\PERSONALE\Spendify\sw_artifacts"
> powershell -ExecutionPolicy Bypass -File .\tests\run_benchmark.ps1
> ```

Entrambi fanno tutto in automatico:
1. Installano `uv` (se assente) — su Windows via `irm https://astral.sh/uv/install.ps1 | iex`
2. `uv` installa Python se non presente nel sistema
3. Creano `.venv` e sincronizzano le dipendenze (`uv sync`)
4. Scaricano i modelli GGUF da HuggingFace (se mancano)
5. Lanciano il benchmark su tutti i modelli GGUF con `llama.cpp`

### Opzioni

**macOS / Linux:**
```bash
bash tests/run_benchmark.sh                         # pipeline, 1 run
bash tests/run_benchmark.sh categorizer              # solo categorizer
bash tests/run_benchmark.sh both                     # entrambi
bash tests/run_benchmark.sh both --runs 3            # 3 run ciascuno
bash tests/run_benchmark.sh pipeline --files 'CC-1*' # con filtro file
```

**Windows (PowerShell):**
```powershell
powershell -ExecutionPolicy Bypass -File .\tests\run_benchmark.ps1                                        # pipeline, 1 run
powershell -ExecutionPolicy Bypass -File .\tests\run_benchmark.ps1 categorizer                            # solo categorizer
powershell -ExecutionPolicy Bypass -File .\tests\run_benchmark.ps1 both -Runs 3                           # 3 run ciascuno
powershell -ExecutionPolicy Bypass -File .\tests\run_benchmark.ps1 -Backend vllm                          # vLLM
powershell -ExecutionPolicy Bypass -File .\tests\run_benchmark.ps1 pipeline -ExtraArgs '--files','CC-1*'  # con filtro
```

## Architettura

```
tests/
├── run_benchmark.sh              ← ENTRY POINT zero-config (macOS/Linux)
├── run_benchmark.ps1             ← ENTRY POINT zero-config (Windows)
├── run_all_benchmarks.sh         ← tutti i modelli GGUF (llama.cpp)
├── run_benchmark_dual.sh         ← llama.cpp + Ollama, stessi modelli
├── setup_benchmark_models.sh     ← scarica modelli senza benchmark
├── cleanup_benchmark.sh          ← pulizia file generati
├── benchmark_pipeline.py         ← benchmark classifier (schema + parsing)
├── benchmark_categorizer.py      ← benchmark categorizer (categorie)
├── hw_monitor.py                 ← monitoraggio HW background (CPU + GPU cross-platform)
├── diagnose.ps1                  ← diagnostica ambiente Windows (include GPU)
├── generate_synthetic_files.py   ← genera i file sintetici di test
├── logs/                         ← LOG di ogni esecuzione benchmark (gitignored)
│   ├── benchmark_YYYYMMDD_HHMMSS.log   ← log run_benchmark.sh
│   ├── pipeline_YYYYMMDD_HHMMSS.log    ← log benchmark_pipeline.py
│   └── categorizer_YYYYMMDD_HHMMSS.log ← log benchmark_categorizer.py
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
  --model-path ~/.spendify/models/qwen2.5-3b-instruct-q4_k_m.gguf
```

### Categorizer

Misura la capacità dell'LLM di assegnare le categorie corrette alle transazioni. Il classifier viene bypassato (usa ground truth) per isolare la performance di categorizzazione.

```bash
uv run python tests/benchmark_categorizer.py --runs 1 --backend local_llama_cpp \
  --model-path ~/.spendify/models/qwen2.5-3b-instruct-q4_k_m.gguf
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

## Setup modelli + Dual benchmark (llama.cpp + Ollama)

Per girare lo stesso modello su entrambi i backend e confrontare i risultati:

```bash
cd ~/Documents/Progetti/PERSONALE/Spendify/sw_artifacts

# Prima volta (o nuova macchina): scarica tutti i modelli
bash tests/setup_benchmark_models.sh

# Solo modelli piccoli (≤3B, per macchine con ≤8GB RAM)
bash tests/setup_benchmark_models.sh --small-only

# Solo GGUF (senza Ollama pull)
bash tests/setup_benchmark_models.sh --skip-ollama

# Poi lancia il dual benchmark
bash tests/run_benchmark_dual.sh

# Con più run per ridurre la varianza
bash tests/run_benchmark_dual.sh --runs 3
```

`setup_benchmark_models.sh` fa `ollama pull` per ogni modello Ollama **e** `huggingface-cli download` per ogni GGUF. Se un modello è già presente lo skippa. `run_benchmark_dual.sh` non scarica nulla — se un file GGUF o un modello Ollama manca mostra `[SKIP]` e continua.

### Modelli dual benchmark

| GGUF (llama.cpp) | Ollama | Size |
|------------------|--------|------|
| `qwen2.5-1.5b-instruct-q4_k_m.gguf` | `qwen2.5:1.5b-instruct` | ~1.0 GB |
| `gemma-2-2b-it-Q4_K_M.gguf` | `gemma2:2b` | ~1.6 GB |
| `Qwen3.5-2B-Q4_K_M.gguf` | `qwen3.5:2b` | ~1.7 GB |
| `Qwen3.5-4B-Q4_K_M.gguf` | `qwen3.5:4b` | ~2.5 GB |
| `gemma-4-E2B-it-Q4_K_M.gguf` | `gemma4:e2b` | ~3.1 GB |
| `Llama-3.2-3B-Instruct-Q4_K_M.gguf` | `llama3.2:3b` | ~1.9 GB |
| `qwen2.5-3b-instruct-q4_k_m.gguf` | `qwen2.5:3b-instruct` | ~2.0 GB |
| `Phi-3-mini-4k-instruct-Q4_K_M.gguf` | `phi3:3.8b` | ~2.2 GB |
| `Qwen2.5-7B-Instruct-Q4_K_M.gguf` | `qwen2.5:7b-instruct` | ~4.4 GB |
| `gemma-3-12b-it-Q4_K_M.gguf` | `gemma3:12b` | ~6.8 GB |

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
  --model-path ~/.spendify/models/gemma-3-12b-it-Q4_K_M.gguf \
  --n-ctx 2048
```

`--n-ctx 0` (default) = auto-detect.

---

## Monitoraggio HW (CPU + GPU)

Il modulo `tests/hw_monitor.py` (`HWMonitor`) campiona CPU e GPU **in background** ogni 0.5 s durante l'intero benchmark, restituendo medie più accurate rispetto ai vecchi campioni point-in-time (`_sample_cpu_load()` / `_sample_gpu_utilization()`, ora rimossi).

| Piattaforma | Metodo GPU | Note |
|-------------|-----------|------|
| macOS Apple Silicon | `ioreg` / AGXAccelerator → Device Utilization % | Nessun sudo richiesto |
| Linux NVIDIA | `nvidia-smi` → utilization % + power watts | Richiede driver NVIDIA |
| Linux AMD | `rocm-smi` → utilization % | Richiede ROCm |
| Fallback | — | GPU utilization = 0.0 |

`benchmark_pipeline.py` e `benchmark_categorizer.py` istanziano `HWMonitor` all'inizio del run e chiamano `stop()` alla fine per ottenere le medie.

### Diagnostica GPU (Windows)

`tests/diagnose.ps1` include un passo di rilevamento GPU (step 8/9): NVIDIA (`nvidia-smi` + CUDA), AMD (WMI), Intel Arc (oneAPI), Intel iGPU.

### Logging

Ogni esecuzione salva un log completo in `tests/logs/` (gitignored):

| Script | Log file |
|--------|----------|
| `run_benchmark.sh` | `tests/logs/benchmark_YYYYMMDD_HHMMSS.log` |
| `benchmark_pipeline.py` | `tests/logs/pipeline_YYYYMMDD_HHMMSS.log` |
| `benchmark_categorizer.py` | `tests/logs/categorizer_YYYYMMDD_HHMMSS.log` |
| `diagnose.ps1` | `~/spendify_diagnose_YYYYMMDD_HHMMSS.log` |

L'output va sia su console che su file (tee). I log non vengono sovrascritti — un file per ogni esecuzione con timestamp nel nome. Utili per:
- Troubleshooting errori su modelli specifici
- Confronto tra run diversi
- Audit tempi e warning

---

## Backend supportati

| Backend | Flag | Requisiti |
|---------|------|-----------|
| llama.cpp (locale) | `--backend local_llama_cpp` | File `.gguf` in `~/.spendify/models/` |
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

Scaricati automaticamente da `run_benchmark.sh` / `run_benchmark.ps1`.
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
bash tests/run_benchmark.sh                     bash tests/run_benchmark.sh
  resume: skip esistenti                          resume: skip esistenti
  aggiunge solo nuove                             aggiunge solo nuove

git push ───────────────► merge CSV ◄─────────── git push
```

Ogni riga include `runtime_os`, `runtime_cpu`, `runtime_ram_gb`, `runtime_gpu` — filtrabile per confrontare performance tra macchine diverse.

## Benchmark cross-platform (Mac remoto)

Per confrontare con hardware diverso via rete:

```bash
# Su Mac remoto: lancia llama-server
llama-server -m ~/.spendify/models/gemma-3-12b-it-Q4_K_M.gguf \
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
- Modelli setup: `tests/setup_benchmark_models.sh`
