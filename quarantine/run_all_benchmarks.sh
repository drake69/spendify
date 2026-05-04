#!/usr/bin/env bash
# Run benchmark_classifier.py across all available llama.cpp GGUF models.
# Each run appends to the same results_all_runs.csv (resume-safe via git commit key).

set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON=".venv/bin/python"
BENCH="tests/benchmark_classifier.py"
RUNS=1
MODELS_DIR="$HOME/.spendifai/models"

echo "============================================================"
echo "  FULL MODEL BENCHMARK SUITE (llama.cpp)"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"

# ── llama.cpp models ─────────────────────────────────────────────────────
GGUF_FILES=(
    "$MODELS_DIR/gemma-3-12b-it-Q4_K_M.gguf"
    "$MODELS_DIR/Qwen2.5-7B-Instruct-Q4_K_M.gguf"
    "$MODELS_DIR/Phi-3-mini-4k-instruct-Q4_K_M.gguf"
    "$MODELS_DIR/gemma-4-E2B-it-Q4_K_M.gguf"
    "$MODELS_DIR/gemma-4-E2B-it-Q3_K_M.gguf"
    "$MODELS_DIR/gemma-2-2b-it-Q4_K_M.gguf"
    "$MODELS_DIR/Qwen_Qwen3.5-2B-Q4_K_M.gguf"
    "$MODELS_DIR/Qwen_Qwen3.5-4B-Q4_K_M.gguf"
    "$MODELS_DIR/qwen2.5-1.5b-instruct-q4_k_m.gguf"
    "$MODELS_DIR/qwen2.5-3b-instruct-q4_k_m.gguf"
    "$MODELS_DIR/Llama-3.2-3B-Instruct-Q4_K_M.gguf"
)

for gguf in "${GGUF_FILES[@]}"; do
    if [ -f "$gguf" ]; then
        name=$(basename "$gguf")
        echo ""
        echo "──────────────────────────────────────────────────────────"
        echo "  llama.cpp: $name"
        echo "──────────────────────────────────────────────────────────"
        $PYTHON $BENCH --runs $RUNS --backend local_llama_cpp --model-path "$gguf" || {
            echo "  [WARN] $name failed — skipping"
        }
    else
        echo "  [SKIP] $gguf not found"
    fi
done

echo ""
echo "============================================================"
echo "  ALL BENCHMARKS COMPLETE"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"
