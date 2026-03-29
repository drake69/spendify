#!/bin/bash
# ── Spendify Benchmark Entrypoint ────────────────────────────────────────────
# Downloads model if needed, runs classifier + categorizer benchmarks.
#
# Usage (inside container):
#   /entrypoint.sh --model qwen2.5-3b --runs 1
#   /entrypoint.sh --model-path /models/my-model.gguf --runs 1
#   /entrypoint.sh --all-models --runs 1
#
# Environment:
#   MODELS_DIR=/models          — where GGUF files are stored
#   RESULTS_DIR=/app/results    — where results CSV is written
#   SYNTHETIC_DIR=/app/tests/generated_files — synthetic test files (mounted or built-in)
set -euo pipefail

MODELS_DIR="${MODELS_DIR:-/models}"
RESULTS_DIR="${RESULTS_DIR:-/app/results}"
if command -v uv &>/dev/null; then
    PYTHON="uv run python"
else
    PYTHON="python"
fi

echo "============================================================"
echo "  Spendify Benchmark (Docker)"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "  GPU: $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null || echo 'none')"
echo "  RAM: $(free -g 2>/dev/null | awk '/Mem:/{print $2}' || echo '?') GB"
echo "============================================================"

# ── Parse arguments ──────────────────────────────────────────────────────────
MODEL_ID=""
MODEL_PATH=""
ALL_MODELS=false
RUNS=1
SKIP_CATEGORIZER=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --model)       MODEL_ID="$2"; shift 2 ;;
        --model-path)  MODEL_PATH="$2"; shift 2 ;;
        --all-models)  ALL_MODELS=true; shift ;;
        --runs)        RUNS="$2"; shift 2 ;;
        --skip-categorizer) SKIP_CATEGORIZER=true; shift ;;
        *)             echo "Unknown arg: $1"; exit 1 ;;
    esac
done

# ── Ensure synthetic files exist ─────────────────────────────────────────────
if [ ! -f tests/generated_files/manifest.csv ]; then
    echo "→ Generating synthetic test files..."
    $PYTHON tests/generate_synthetic_files.py
fi

# ── Download model if needed ─────────────────────────────────────────────────
download_model() {
    local model_id="$1"
    echo "→ Resolving model: $model_id" >&2

    # Use model_manager to get repo + filename from registry
    local info
    info=$($PYTHON -c "
from config import get_all_models
for m in get_all_models():
    if m.id == '$model_id':
        print(f'{m.repo}|{m.filename}')
        break
else:
    print('NOT_FOUND')
")

    if [ "$info" = "NOT_FOUND" ]; then
        echo "ERROR: Model '$model_id' not found in registry" >&2
        exit 1
    fi

    local repo=$(echo "$info" | cut -d'|' -f1)
    local filename=$(echo "$info" | cut -d'|' -f2)
    local dest="$MODELS_DIR/$filename"

    if [ -f "$dest" ]; then
        echo "  Model already present: $dest" >&2
    else
        echo "  Downloading $filename from $repo..." >&2
        $PYTHON -c "
from huggingface_hub import hf_hub_download
hf_hub_download('$repo', '$filename', local_dir='$MODELS_DIR', local_dir_use_symlinks=False)
print('  Download complete')
" >&2
    fi
    echo "$dest"
}

# ── Run benchmark for one model ───────────────────────────────────────────���──
run_one_model() {
    local gguf_path="$1"
    local model_name=$(basename "$gguf_path" .gguf)

    echo ""
    echo "──────────────────────────────────────────────────────────"
    echo "  Benchmarking: $model_name"
    echo "──────────────────────────────────────────────────────────"

    # Classifier benchmark
    echo "  → Classifier benchmark..."
    $PYTHON tests/benchmark_pipeline.py \
        --runs "$RUNS" \
        --backend local_llama_cpp \
        --model-path "$gguf_path" \
    || echo "  [WARN] Classifier benchmark failed for $model_name"

    # Categorizer benchmark
    if [ "$SKIP_CATEGORIZER" = false ]; then
        echo "  → Categorizer benchmark..."
        $PYTHON tests/benchmark_categorizer.py \
            --runs "$RUNS" \
            --backend local_llama_cpp \
            --model-path "$gguf_path" \
        || echo "  [WARN] Categorizer benchmark failed for $model_name"
    fi
}

# ── Main ─────────────────────────────────────────────────────────────────────

if [ "$ALL_MODELS" = true ]; then
    echo "→ Running all models from registry..."
    MODEL_IDS=$($PYTHON -c "
from config import get_all_models
for m in get_all_models():
    print(m.id)
")
    for mid in $MODEL_IDS; do
        gguf=$(download_model "$mid")
        run_one_model "$gguf"
    done

elif [ -n "$MODEL_ID" ]; then
    gguf=$(download_model "$MODEL_ID")
    run_one_model "$gguf"

elif [ -n "$MODEL_PATH" ]; then
    if [ ! -f "$MODEL_PATH" ]; then
        echo "ERROR: Model file not found: $MODEL_PATH"
        exit 1
    fi
    run_one_model "$MODEL_PATH"

else
    echo "ERROR: Specify --model <id>, --model-path <path>, or --all-models"
    exit 1
fi

# ── Copy results to output directory ─────────────────────────────────────────
echo ""
echo "→ Copying results to $RESULTS_DIR..."
mkdir -p "$RESULTS_DIR"
cp tests/generated_files/benchmark/*.csv "$RESULTS_DIR/" 2>/dev/null || true

echo ""
echo "============================================================"
echo "  BENCHMARK COMPLETE"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "  Results: $RESULTS_DIR/results_all_runs.csv"
echo "============================================================"
