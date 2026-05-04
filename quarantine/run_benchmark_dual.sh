#!/usr/bin/env bash
# Run benchmark_classifier.py across all models on BOTH llama.cpp and Ollama.
# Resume-safe: already-completed (run_id, filename, commit, branch, provider, model) are skipped.
#
# Usage: bash tests/run_benchmark_dual.sh [--runs N]
#
# Estimated time: ~5-10 hours for 10 models × 2 backends × 50 files × 1 run

set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON=".venv/bin/python"
BENCH="tests/benchmark_classifier.py"
RUNS="${1:-1}"
MODELS_DIR="$HOME/.spendifai/models"

# Strip --runs flag if passed as first arg
if [[ "${1:-}" == "--runs" ]]; then
    RUNS="${2:-1}"
fi

echo "============================================================"
echo "  DUAL BACKEND BENCHMARK (llama.cpp + Ollama)"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "  Runs per model: $RUNS"
echo "============================================================"

# ── Model pairs: (gguf_filename, ollama_model_name) ──────────────────────
declare -a GGUF_NAMES=(
    "qwen2.5-1.5b-instruct-q4_k_m.gguf"
    "gemma-2-2b-it-Q4_K_M.gguf"
    "Qwen3.5-2B-Q4_K_M.gguf"
    "Qwen3.5-4B-Q4_K_M.gguf"
    "gemma-4-E2B-it-Q4_K_M.gguf"
    "Llama-3.2-3B-Instruct-Q4_K_M.gguf"
    "qwen2.5-3b-instruct-q4_k_m.gguf"
    "Phi-3-mini-4k-instruct-Q4_K_M.gguf"
    "Qwen2.5-7B-Instruct-Q4_K_M.gguf"
    "gemma-3-12b-it-Q4_K_M.gguf"
)

declare -a OLLAMA_NAMES=(
    "qwen2.5:1.5b-instruct"
    "gemma2:2b"
    "qwen3.5:2b"
    "qwen3.5:4b"
    "gemma4:e2b"
    "llama3.2:3b"
    "qwen2.5:3b-instruct"
    "phi3:3.8b"
    "qwen2.5:7b-instruct"
    "gemma3:12b"
)

N_MODELS=${#GGUF_NAMES[@]}
STEP=0
TOTAL=$((N_MODELS * 2))

# ── Verify Ollama is reachable ───────────────────────────────────────────
echo ""
echo "[check] Verifying Ollama is reachable..."
if ! curl -sf http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo "ERROR: Ollama is not reachable. Start it with: ollama serve"
    exit 1
fi
echo "[check] Ollama OK"

# ── Phase 1: llama.cpp ───────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  PHASE 1/2: llama.cpp                                   ║"
echo "╚══════════════════════════════════════════════════════════╝"

for i in $(seq 0 $((N_MODELS - 1))); do
    STEP=$((STEP + 1))
    gguf="${MODELS_DIR}/${GGUF_NAMES[$i]}"
    name="${GGUF_NAMES[$i]}"

    echo ""
    echo "──────────────────────────────────────────────────────────"
    echo "  [$STEP/$TOTAL] llama.cpp: $name"
    echo "──────────────────────────────────────────────────────────"

    if [ -f "$gguf" ]; then
        $PYTHON $BENCH --runs "$RUNS" --backend local_llama_cpp --model-path "$gguf" || {
            echo "  [WARN] llama.cpp $name failed — skipping"
        }
    else
        echo "  [SKIP] $gguf not found"
    fi
done

# ── Phase 2: Ollama ──────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  PHASE 2/2: Ollama                                      ║"
echo "╚══════════════════════════════════════════════════════════╝"

for i in $(seq 0 $((N_MODELS - 1))); do
    STEP=$((STEP + 1))
    model="${OLLAMA_NAMES[$i]}"

    echo ""
    echo "──────────────────────────────────────────────────────────"
    echo "  [$STEP/$TOTAL] Ollama: $model"
    echo "──────────────────────────────────────────────────────────"

    # Check model is available in Ollama
    if ollama show "$model" > /dev/null 2>&1; then
        $PYTHON $BENCH --runs "$RUNS" --backend local_ollama --model "$model" || {
            echo "  [WARN] Ollama $model failed — skipping"
        }
    else
        echo "  [SKIP] $model not available in Ollama — pull it first"
    fi
done

echo ""
echo "============================================================"
echo "  ALL BENCHMARKS COMPLETE"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"
echo ""
echo "  Results: tests/generated_files/benchmark/results_all_runs.csv"
echo "  Summary: tests/generated_files/benchmark/summary_global.csv"
