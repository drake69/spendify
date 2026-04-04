#!/usr/bin/env bash
# Guard: se invocato con sh invece di bash (es. su Ubuntu dove sh=dash),
# si ri-esegue automaticamente con bash.
[ -z "${BASH_VERSION:-}" ] && exec bash "$0" "$@"
# Full benchmark: classifier (pipeline) + categorizer × all active backends.
#
# Model catalogue: benchmark/benchmark_models.csv
#   gguf_file + gguf_hf_url   → llama.cpp  (empty = model not available on llama)
#   ollama_tag                 → Ollama     (empty = model not available on Ollama)
#   vLLM: auto-detected at runtime from the server (/v1/models)
#
# Auto-detects active backends:
#   llama.cpp  — always, if GGUF files are present (downloads missing ones)
#   Ollama     — if localhost:11434 is reachable (pulls missing models)
#   vLLM       — if localhost:8000/v1/models is reachable
#
# Usage:
#   bash benchmark/run_benchmark_full.sh                           # both phases, 1 run
#   bash benchmark/run_benchmark_full.sh --runs 3
#   bash benchmark/run_benchmark_full.sh --benchmark pipeline      # classifier only
#   bash benchmark/run_benchmark_full.sh --benchmark categorizer
#   bash benchmark/run_benchmark_full.sh --vllm-url http://gpu:8000/v1
#   bash benchmark/run_benchmark_full.sh --ollama-url http://192.168.1.5:11434
#   bash benchmark/run_benchmark_full.sh --skip-llama
#   bash benchmark/run_benchmark_full.sh --skip-ollama
#   bash benchmark/run_benchmark_full.sh --skip-vllm
#   bash benchmark/run_benchmark_full.sh --setup-only             # setup without running
#
# Estimated time: ~10-20h for all backends × 10 models × 50 files × 2 phases

set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON=".venv/bin/python"
MODELS_DIR="$HOME/.spendifai/models"
MODELS_CSV="benchmark/benchmark_models.csv"

# ── Parse arguments ──────────────────────────────────────────────────────
BENCHMARK="both"
RUNS=1
VLLM_URL="http://localhost:8000/v1"
OLLAMA_URL="http://localhost:11434"
SKIP_LLAMA=false
SKIP_OLLAMA=false
SKIP_VLLM=false
SETUP_ONLY=false
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --benchmark)   BENCHMARK="$2"; shift 2 ;;
        --runs)        RUNS="$2"; shift 2 ;;
        --vllm-url)    VLLM_URL="$2"; shift 2 ;;
        --ollama-url)  OLLAMA_URL="$2"; shift 2 ;;
        --skip-llama)  SKIP_LLAMA=true; shift ;;
        --skip-ollama) SKIP_OLLAMA=true; shift ;;
        --skip-vllm)   SKIP_VLLM=true; shift ;;
        --setup-only)  SETUP_ONLY=true; shift ;;
        *)             EXTRA_ARGS+=("$1"); shift ;;
    esac
done

# ── Log setup ─────────────────────────────────────────────────────────────
LOG_DIR="benchmark/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/benchmark_full_$(date '+%Y%m%d_%H%M%S').log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "════════════════════════════════════════════════════════════"
echo "  SPENDIFY FULL BENCHMARK  —  $(date '+%Y-%m-%d %H:%M:%S')"
echo "  Phases   : $BENCHMARK"
echo "  Runs     : $RUNS"
echo "  Models   : $MODELS_CSV"
echo "  Log      : $LOG_FILE"
echo "════════════════════════════════════════════════════════════"

# ── Helpers: read CSV ─────────────────────────────────────────────────────
# Returns field by column name from a CSV row
# Usage: _csv_field "$row" "$header_line" "column_name"
_csv_field() {
    local row="$1" header="$2" col="$3"
    local idx
    idx=$(echo "$header" | tr ',' '\n' | grep -n "^${col}$" | cut -d: -f1)
    [ -z "$idx" ] && echo "" && return
    echo "$row" | cut -d',' -f"$idx"
}

# Load enabled rows from CSV (skip header + commented/empty lines)
_read_models_csv() {
    tail -n +2 "$MODELS_CSV" | grep -v '^#' | grep -v '^[[:space:]]*$' | grep ',true'
}

CSV_HEADER=$(head -1 "$MODELS_CSV")

# ── Step 1: uv ────────────────────────────────────────────────────────────
echo ""
echo "── [1/4] Checking uv..."
if ! command -v uv &>/dev/null; then
    echo "[setup] Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    command -v uv &>/dev/null || { echo "ERROR: uv install failed"; exit 1; }
fi
echo "[ok] uv $(uv --version)"

# ── Step 2: venv + deps ───────────────────────────────────────────────────
echo ""
echo "── [2/4] Checking Python environment..."
if [ ! -d ".venv" ]; then
    echo "[setup] Creating venv..."
    uv sync
else
    uv sync --quiet
fi
echo "[ok] Python env ready"

# ── Step 3a: llama.cpp — download missing GGUF ───────────────────────────
if [ "$SKIP_LLAMA" = false ]; then
    echo ""
    echo "── [3a/4] llama.cpp setup — checking GGUF models..."
    mkdir -p "$MODELS_DIR"

    # Resolve HF download command.
    # Strategia: usa sempre "uv run huggingface-cli" se uv è disponibile —
    # garantisce che il comando venga eseguito nella venv del progetto
    # indipendentemente dal PATH e dalla versione di huggingface_hub installata.
    # Fallback a hf/huggingface-cli di sistema solo se uv non è presente.
    HF_CMD=""
    if command -v uv &>/dev/null; then
        # uv run usa la venv del progetto; se huggingface_hub non è installato
        # o manca il CLI, lo aggiunge prima di eseguire.
        if ! uv run huggingface-cli --help &>/dev/null 2>&1; then
            echo "[setup] Aggiungo huggingface_hub al progetto..."
            uv add huggingface_hub --quiet 2>/dev/null || \
                uv pip install "huggingface_hub[cli]" --quiet
        fi
        HF_CMD="uv run huggingface-cli download"
    elif command -v hf &>/dev/null; then
        HF_CMD="hf download"
    elif command -v huggingface-cli &>/dev/null; then
        HF_CMD="huggingface-cli download"
    elif [ -x ".venv/bin/huggingface-cli" ]; then
        HF_CMD=".venv/bin/huggingface-cli download"
    else
        echo "ERROR: huggingface-cli non trovato e uv non disponibile." >&2
        echo "       Installa uv (https://astral.sh/uv) oppure:" >&2
        echo "       pip install 'huggingface_hub[cli]'" >&2
        exit 1
    fi

    # Detect available RAM (MB) for size filtering
    SYSTEM_RAM_MB=0
    if [ "$(uname)" = "Darwin" ]; then
        SYSTEM_RAM_MB=$(( $(sysctl -n hw.memsize) / 1048576 ))
    elif [ -f /proc/meminfo ]; then
        SYSTEM_RAM_MB=$(awk '/MemTotal/ {printf "%d", $2/1024}' /proc/meminfo)
    fi
    # Rule of thumb: model needs ~2x file size in RAM (weights + KV cache)
    MAX_MODEL_MB=$(( SYSTEM_RAM_MB / 2 ))
    if [ "$SYSTEM_RAM_MB" -gt 0 ]; then
        echo "[check] System RAM: $((SYSTEM_RAM_MB / 1024)) GB → max model size: $((MAX_MODEL_MB / 1024)) GB"
    fi

    GGUF_DOWNLOADED=0
    GGUF_SKIPPED=0
    while IFS= read -r row; do
        gguf_file=$(_csv_field "$row" "$CSV_HEADER" "gguf_file")
        gguf_repo=$(_csv_field "$row" "$CSV_HEADER" "gguf_repo")
        name=$(_csv_field "$row" "$CSV_HEADER" "name")
        size_mb=$(_csv_field "$row" "$CSV_HEADER" "size_mb")
        [ -z "$gguf_file" ] && continue   # no GGUF for this model

        # Skip models too large for available RAM
        if [ -n "$size_mb" ] && [ "$MAX_MODEL_MB" -gt 0 ] && [ "$size_mb" -gt "$MAX_MODEL_MB" ]; then
            echo "[SKIP] $name ($gguf_file, ${size_mb}MB) — exceeds RAM limit (${MAX_MODEL_MB}MB)"
            continue
        fi

        dest="$MODELS_DIR/$gguf_file"
        if [ -f "$dest" ]; then
            GGUF_SKIPPED=$((GGUF_SKIPPED + 1))
        else
            echo "[download] $gguf_file  (from $gguf_repo)"
            $HF_CMD "$gguf_repo" "$gguf_file" --local-dir "$MODELS_DIR" 2>&1 | tail -2
            GGUF_DOWNLOADED=$((GGUF_DOWNLOADED + 1))
        fi
    done < <(_read_models_csv)

    GGUF_TOTAL=$(ls -1 "$MODELS_DIR"/*.gguf 2>/dev/null | wc -l | tr -d ' ')
    echo "[ok] $GGUF_TOTAL GGUF models in $MODELS_DIR ($GGUF_DOWNLOADED downloaded, $GGUF_SKIPPED already present)"
else
    echo ""
    echo "── [3a/4] llama.cpp setup — skipped (--skip-llama)"
fi

# ── Step 3b: Ollama — pull missing models ────────────────────────────────
if [ "$SKIP_OLLAMA" = false ]; then
    echo ""
    echo "── [3b/4] Ollama setup — checking models..."
    if curl -sf "$OLLAMA_URL/api/tags" > /dev/null 2>&1; then
        OLLAMA_PULLED=0
        OLLAMA_SKIPPED=0
        while IFS= read -r row; do
            tag=$(_csv_field "$row" "$CSV_HEADER" "ollama_tag")
            [ -z "$tag" ] && continue   # no Ollama tag for this model
            if ollama show "$tag" > /dev/null 2>&1; then
                echo "[ok]   $tag — already present"
                OLLAMA_SKIPPED=$((OLLAMA_SKIPPED + 1))
            else
                echo "[pull] $tag ..."
                OLLAMA_PULL_ARGS=()
                [ "$OLLAMA_URL" != "http://localhost:11434" ] && \
                    OLLAMA_PULL_ARGS+=(--insecure)
                OLLAMA_HOST="${OLLAMA_URL#http://}" OLLAMA_HOST="${OLLAMA_HOST%%/*}" \
                    ollama pull "$tag" ${OLLAMA_PULL_ARGS[@]+"${OLLAMA_PULL_ARGS[@]}"} || \
                    echo "  [WARN] pull failed for $tag — will skip in benchmark"
                OLLAMA_PULLED=$((OLLAMA_PULLED + 1))
            fi
        done < <(_read_models_csv)
        echo "[ok] Ollama setup done ($OLLAMA_PULLED pulled, $OLLAMA_SKIPPED already present)"
    else
        echo "[skip] Ollama not reachable on $OLLAMA_URL — skipping Ollama setup"
        SKIP_OLLAMA=true
    fi
else
    echo ""
    echo "── [3b/4] Ollama setup — skipped (--skip-ollama)"
fi

# ── Step 3c: vLLM — detect model ─────────────────────────────────────────
VLLM_MODEL=""
if [ "$SKIP_VLLM" = false ]; then
    echo ""
    echo "── [3c/4] vLLM — detecting served model..."
    VLLM_RESP=$(curl -sf "$VLLM_URL/models" 2>/dev/null || true)
    if [ -n "$VLLM_RESP" ]; then
        VLLM_MODEL=$(echo "$VLLM_RESP" | $PYTHON -c "
import sys, json
d = json.load(sys.stdin)
models = d.get('data', [])
print(models[0]['id'] if models else '')
" 2>/dev/null || true)
        if [ -n "$VLLM_MODEL" ]; then
            echo "[ok] vLLM serving: $VLLM_MODEL  ($VLLM_URL)"
        else
            echo "[skip] vLLM reachable but no model found"
            SKIP_VLLM=true
        fi
    else
        echo "[skip] vLLM not reachable on $VLLM_URL"
        SKIP_VLLM=true
    fi
else
    echo ""
    echo "── [3c/4] vLLM — skipped (--skip-vllm)"
fi

# ── Setup summary ─────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════════"
echo "  SETUP SUMMARY"
USE_LLAMA=false
USE_OLLAMA=false
USE_VLLM=false
[ "$SKIP_LLAMA" = false ] && ls "$MODELS_DIR"/*.gguf 2>/dev/null | grep -q . && USE_LLAMA=true
[ "$SKIP_OLLAMA" = false ] && USE_OLLAMA=true
[ "$SKIP_VLLM" = false ] && [ -n "$VLLM_MODEL" ] && USE_VLLM=true

[ "$USE_LLAMA"  = true ] && echo "  llama.cpp  : enabled" || echo "  llama.cpp  : DISABLED"
[ "$USE_OLLAMA" = true ] && echo "  Ollama     : enabled" || echo "  Ollama     : DISABLED"
[ "$USE_VLLM"   = true ] && echo "  vLLM       : enabled ($VLLM_MODEL)" || echo "  vLLM       : DISABLED"
echo "════════════════════════════════════════════════════════════"

if [ "$SETUP_ONLY" = true ]; then
    echo ""
    echo "  Setup complete (--setup-only). To run benchmarks omit the flag."
    exit 0
fi

if [ "$USE_LLAMA" = false ] && [ "$USE_OLLAMA" = false ] && [ "$USE_VLLM" = false ]; then
    echo "ERROR: No active backends. Aborting."
    exit 1
fi

# ── Run helper ────────────────────────────────────────────────────────────
MIN_CTX=8000
STEP=0

run_phase() {
    local phase="$1" label="$2"; shift 2
    local script
    [ "$phase" = "pipeline" ]    && script="benchmark/benchmark_pipeline.py"
    [ "$phase" = "categorizer" ] && script="benchmark/benchmark_categorizer.py"
    STEP=$((STEP + 1))
    echo ""
    echo "────────────────────────────────────────────────────────────"
    echo "  [step $STEP] [$phase] $label"
    echo "────────────────────────────────────────────────────────────"
    $PYTHON "$script" --runs "$RUNS" "$@" \
        ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} || \
        echo "  [WARN] $label [$phase] failed — skipping"
}

run_both() {
    local label="$1"; shift
    if [ "$BENCHMARK" = "pipeline" ] || [ "$BENCHMARK" = "both" ]; then
        run_phase pipeline "$label" "$@"
    fi
    if [ "$BENCHMARK" = "categorizer" ] || [ "$BENCHMARK" = "both" ]; then
        run_phase categorizer "$label" "$@"
    fi
}

# ── Step 4: Run benchmarks ────────────────────────────────────────────────
echo ""
echo "── [4/4] Running benchmarks..."

# ── llama.cpp ─────────────────────────────────────────────────────────────
if [ "$USE_LLAMA" = true ]; then
    echo ""
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║  BACKEND: llama.cpp                                     ║"
    echo "╚══════════════════════════════════════════════════════════╝"

    while IFS= read -r row; do
        gguf_file=$(_csv_field "$row" "$CSV_HEADER" "gguf_file")
        name=$(_csv_field "$row" "$CSV_HEADER" "name")
        size_mb=$(_csv_field "$row" "$CSV_HEADER" "size_mb")
        [ -z "$gguf_file" ] && continue
        gguf="$MODELS_DIR/$gguf_file"
        [ ! -f "$gguf" ] && { echo "  [SKIP] $name — file not found: $gguf_file"; continue; }

        # Skip models too large for available RAM
        if [ -n "$size_mb" ] && [ "$MAX_MODEL_MB" -gt 0 ] && [ "$size_mb" -gt "$MAX_MODEL_MB" ]; then
            echo "  [SKIP] $name — model ${size_mb}MB exceeds RAM limit (${MAX_MODEL_MB}MB)"
            continue
        fi

        n_ctx=$($PYTHON -c "
from core.llm_backends import LlamaCppBackend
ctx = LlamaCppBackend.read_gguf_context_length('$gguf')
print(ctx or 0)
" 2>/dev/null || echo "0")
        if [ "$n_ctx" -gt 0 ] && [ "$n_ctx" -lt "$MIN_CTX" ]; then
            echo "  [SKIP] $name — n_ctx=$n_ctx < min=$MIN_CTX"
            continue
        fi

        run_both "llama.cpp: $name ($gguf_file)" \
            --backend local_llama_cpp --model-path "$gguf"
    done < <(_read_models_csv)
fi

# ── Ollama ────────────────────────────────────────────────────────────────
if [ "$USE_OLLAMA" = true ]; then
    echo ""
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║  BACKEND: Ollama                                        ║"
    echo "╚══════════════════════════════════════════════════════════╝"

    while IFS= read -r row; do
        tag=$(_csv_field "$row" "$CSV_HEADER" "ollama_tag")
        name=$(_csv_field "$row" "$CSV_HEADER" "name")
        [ -z "$tag" ] && continue
        ollama show "$tag" > /dev/null 2>&1 || { echo "  [SKIP] $name ($tag) — not in Ollama"; continue; }

        OLLAMA_ARGS=(--backend local_ollama --model "$tag")
        [ "$OLLAMA_URL" != "http://localhost:11434" ] && OLLAMA_ARGS+=(--base-url "$OLLAMA_URL")
        run_both "Ollama: $name ($tag)" "${OLLAMA_ARGS[@]}"
    done < <(_read_models_csv)
fi

# ── vLLM ──────────────────────────────────────────────────────────────────
if [ "$USE_VLLM" = true ]; then
    echo ""
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║  BACKEND: vLLM                                          ║"
    echo "╚══════════════════════════════════════════════════════════╝"
    run_both "vLLM: $VLLM_MODEL" \
        --backend vllm --model "$VLLM_MODEL" --base-url "$VLLM_URL"
fi

# ── Done ──────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════════"
echo "  FULL BENCHMARK COMPLETE  —  $(date '+%Y-%m-%d %H:%M:%S')"
echo "  Steps completed : $STEP"
echo "════════════════════════════════════════════════════════════"
echo ""
echo "  Archive : benchmark/results/  (versioned per-run CSV)"
echo "  Legacy  : benchmark/generated_files/benchmark/results_all_runs.csv"
echo "  Summary : benchmark/generated_files/benchmark/summary_global.csv"
echo "  Log     : $LOG_FILE"
