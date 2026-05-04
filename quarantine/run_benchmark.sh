#!/usr/bin/env bash
# Zero-config benchmark launcher for llama.cpp with small models.
#
# On a fresh clone: handles uv install, venv creation, dependency sync,
# model download, and benchmark execution — no manual steps needed.
#
# Usage:
#   bash tests/run_benchmark.sh [pipeline|categorizer|both] [--runs N] [--small-only] [extra args...]
#
# Examples:
#   bash tests/run_benchmark.sh                     # pipeline benchmark, 1 run, all small models
#   bash tests/run_benchmark.sh categorizer          # categorizer benchmark
#   bash tests/run_benchmark.sh both --runs 3        # both benchmarks, 3 runs each
#   bash tests/run_benchmark.sh pipeline --files 'CC-1*'  # pipeline, filter files

set -euo pipefail
cd "$(dirname "$0")/.."

MODELS_DIR="$HOME/.spendifai/models"
PYTHON=".venv/bin/python"

# ── Parse arguments ──────────────────────────────────────────────────────
BENCHMARK="pipeline"
RUNS=1
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        pipeline|categorizer|both)
            BENCHMARK="$1"; shift ;;
        --runs)
            RUNS="$2"; shift 2 ;;
        --small-only)
            shift ;;  # small-only is the default, accepted for compat
        *)
            EXTRA_ARGS+=("$1"); shift ;;
    esac
done

# ── Log file: tee all output to console + file ────────────────────────
LOG_DIR="tests/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/benchmark_$(date '+%Y%m%d_%H%M%S').log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "============================================================"
echo "  Spendif.ai Benchmark (zero-config)"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "  Mode: $BENCHMARK | Runs: $RUNS"
echo "  Log: $LOG_FILE"
echo "============================================================"

# ── Step 1: Ensure uv is available ───────────────────────────────────────
echo ""
echo "── [1/3] Checking uv..."
if ! command -v uv &>/dev/null; then
    echo "[setup] uv not found — installing..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Add to PATH for this session
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    if ! command -v uv &>/dev/null; then
        echo "ERROR: uv installation failed. Install manually: https://docs.astral.sh/uv/"
        exit 1
    fi
fi
echo "[ok] uv $(uv --version)"

# ── Step 2: Ensure venv + dependencies ───────────────────────────────────
echo ""
echo "── [2/3] Checking Python environment..."
if [ ! -d ".venv" ]; then
    echo "[setup] Creating virtual environment + installing dependencies..."
    uv sync
else
    # Ensure deps are up to date (fast no-op if already synced)
    echo "[setup] Syncing dependencies..."
    uv sync --quiet
fi
echo "[ok] Python env ready"

# ── Step 3: Ensure GGUF models (small only) ──────────────────────────────
echo ""
echo "── [3/3] Checking GGUF models..."
mkdir -p "$MODELS_DIR"

# Check available disk space (models need ~9 GB total)
REQUIRED_GB=9
EXISTING_MB=$(du -sm "$MODELS_DIR"/*.gguf 2>/dev/null | awk '{s+=$1} END {print s+0}')
NEEDED_GB=$(( (REQUIRED_GB * 1024 - EXISTING_MB) / 1024 ))
if [ "$NEEDED_GB" -lt 0 ]; then NEEDED_GB=0; fi

if [ "$(uname)" = "Darwin" ]; then
    FREE_GB=$(df -g "$MODELS_DIR" | awk 'NR==2 {print $4}')
else
    FREE_GB=$(df -BG "$MODELS_DIR" | awk 'NR==2 {gsub("G",""); print $4}')
fi
echo "[check] Disk space: ${FREE_GB} GB free (need ~${NEEDED_GB} GB for missing models)"
if [ "$FREE_GB" -lt "$NEEDED_GB" ]; then
    echo "ERROR: Spazio disco insufficiente. Servono ~${NEEDED_GB} GB, disponibili ${FREE_GB} GB."
    echo "       Libera spazio o usa un backend remoto (vllm, openai_compatible)."
    exit 1
fi

# Small models: name → HuggingFace repo + filename
declare -A SMALL_MODELS=(
    ["qwen2.5-1.5b-instruct-q4_k_m.gguf"]="Qwen/Qwen2.5-1.5B-Instruct-GGUF"
    ["gemma-2-2b-it-Q4_K_M.gguf"]="bartowski/gemma-2-2b-it-GGUF"
    ["Llama-3.2-3B-Instruct-Q4_K_M.gguf"]="bartowski/Llama-3.2-3B-Instruct-GGUF"
    ["qwen2.5-3b-instruct-q4_k_m.gguf"]="Qwen/Qwen2.5-3B-Instruct-GGUF"
    ["Phi-3-mini-4k-instruct-Q4_K_M.gguf"]="microsoft/Phi-3-mini-4k-instruct-gguf"
)

NEED_DOWNLOAD=false
for model_file in "${!SMALL_MODELS[@]}"; do
    if [ ! -f "$MODELS_DIR/$model_file" ]; then
        NEED_DOWNLOAD=true
        break
    fi
done

if [ "$NEED_DOWNLOAD" = true ]; then
    # Find hf download command
    HF_CMD=""
    if command -v hf &>/dev/null; then
        HF_CMD="hf download"
    elif command -v huggingface-cli &>/dev/null; then
        HF_CMD="huggingface-cli download"
    elif $PYTHON -c "import huggingface_hub" 2>/dev/null; then
        HF_CMD="$PYTHON -m huggingface_hub.commands.huggingface_cli download"
    else
        echo "[setup] Installing huggingface-cli..."
        uv pip install huggingface_hub --quiet
        HF_CMD="$PYTHON -m huggingface_hub.commands.huggingface_cli download"
    fi

    for model_file in "${!SMALL_MODELS[@]}"; do
        if [ ! -f "$MODELS_DIR/$model_file" ]; then
            repo="${SMALL_MODELS[$model_file]}"
            echo "[download] $model_file from $repo..."
            $HF_CMD "$repo" "$model_file" --local-dir "$MODELS_DIR" 2>&1 | tail -1
        fi
    done
fi

GGUF_COUNT=$(ls -1 "$MODELS_DIR"/*.gguf 2>/dev/null | wc -l | tr -d ' ')
echo "[ok] $GGUF_COUNT GGUF models available in $MODELS_DIR"

# ── Run benchmarks ───────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo "  Starting benchmarks..."
echo "============================================================"

run_pipeline() {
    local gguf="$1"
    local name
    name=$(basename "$gguf")
    echo ""
    echo "──────────────────────────────────────────────────────────"
    echo "  [pipeline] llama.cpp: $name"
    echo "──────────────────────────────────────────────────────────"
    $PYTHON tests/benchmark_classifier.py \
        --runs "$RUNS" \
        --backend local_llama_cpp \
        --model-path "$gguf" \
        ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} || {
        echo "  [WARN] $name failed — skipping"
    }
}

run_categorizer() {
    local gguf="$1"
    local name
    name=$(basename "$gguf")
    echo ""
    echo "──────────────────────────────────────────────────────────"
    echo "  [categorizer] llama.cpp: $name"
    echo "──────────────────────────────────────────────────────────"
    $PYTHON tests/benchmark_categorizer.py \
        --runs "$RUNS" \
        --backend local_llama_cpp \
        --model-path "$gguf" \
        ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} || {
        echo "  [WARN] $name failed — skipping"
    }
}

# Minimum context window required by Spendif.ai prompts (tokens)
MIN_CTX=8000

# Iterate over all GGUF models (sorted by size — smallest first)
for gguf in $(ls -1S -r "$MODELS_DIR"/*.gguf 2>/dev/null); do
    model_name=$(basename "$gguf")

    # Pre-flight: read n_ctx from GGUF metadata without loading the model
    n_ctx=$($PYTHON -c "
from core.llm_backends import LlamaCppBackend
ctx = LlamaCppBackend.read_gguf_context_length('$gguf')
print(ctx or 0)
" 2>/dev/null || echo "0")

    if [ "$n_ctx" -gt 0 ] && [ "$n_ctx" -lt "$MIN_CTX" ]; then
        echo ""
        echo "──────────────────────────────────────────────────────────"
        echo "  [SKIP] $model_name — n_ctx=$n_ctx < min=$MIN_CTX"
        echo "  Context window too small for Spendif.ai prompts."
        echo "──────────────────────────────────────────────────────────"
        continue
    fi

    if [ "$BENCHMARK" = "pipeline" ] || [ "$BENCHMARK" = "both" ]; then
        run_pipeline "$gguf"
    fi
    if [ "$BENCHMARK" = "categorizer" ] || [ "$BENCHMARK" = "both" ]; then
        run_categorizer "$gguf"
    fi
done

echo ""
echo "============================================================"
echo "  ALL BENCHMARKS COMPLETE"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"
echo ""
echo "  Results: tests/generated_files/benchmark/"
echo "  Log:     $LOG_FILE"
