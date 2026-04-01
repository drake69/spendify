#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

PYTHON=".venv/bin/python"
BENCH="tests/benchmark_pipeline.py"
RUNS=1
MODELS_DIR="$HOME/.spendify/models"
OPENAI_KEY="$1"

echo "============================================================"
echo "  FULL BENCHMARK: llama.cpp + Ollama + OpenAI"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"

# ── Phase 1: llama.cpp (7 models) ────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  PHASE 1/3: llama.cpp (7 models)                        ║"
echo "╚══════════════════════════════════════════════════════════╝"

for gguf in \
    "$MODELS_DIR/qwen2.5-1.5b-instruct-q4_k_m.gguf" \
    "$MODELS_DIR/gemma-2-2b-it-Q4_K_M.gguf" \
    "$MODELS_DIR/Llama-3.2-3B-Instruct-Q4_K_M.gguf" \
    "$MODELS_DIR/qwen2.5-3b-instruct-q4_k_m.gguf" \
    "$MODELS_DIR/Phi-3-mini-4k-instruct-Q4_K_M.gguf" \
    "$MODELS_DIR/Qwen2.5-7B-Instruct-Q4_K_M.gguf" \
    "$MODELS_DIR/gemma-3-12b-it-Q4_K_M.gguf"; do
    if [ -f "$gguf" ]; then
        name=$(basename "$gguf")
        echo ""
        echo "── llama.cpp: $name ──"
        $PYTHON $BENCH --runs $RUNS --backend local_llama_cpp --model-path "$gguf" || {
            echo "  [WARN] $name failed — skipping"
        }
    fi
done

# ── Phase 2: Ollama (7 models) ───────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  PHASE 2/3: Ollama (7 models)                           ║"
echo "╚══════════════════════════════════════════════════════════╝"

for model in \
    "qwen2.5:1.5b-instruct" \
    "gemma2:2b" \
    "llama3.2:3b" \
    "qwen2.5:3b-instruct" \
    "phi3:3.8b" \
    "qwen2.5:7b-instruct" \
    "gemma3:12b"; do
    echo ""
    echo "── Ollama: $model ──"
    $PYTHON $BENCH --runs $RUNS --backend local_ollama --model "$model" || {
        echo "  [WARN] $model failed — skipping"
    }
done

# ── Phase 3: OpenAI ──────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  PHASE 3/3: OpenAI gpt-4o-mini                          ║"
echo "╚══════════════════════════════════════════════════════════╝"

$PYTHON $BENCH --runs $RUNS --backend openai --model gpt-4o-mini --api-key "$OPENAI_KEY" || {
    echo "  [WARN] OpenAI failed"
}

# ── Phase 4: Categorizer (same models, same backends) ────────────────────
CAT_BENCH="tests/benchmark_categorizer.py"

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  PHASE 4/6: Categorizer — llama.cpp (7 models)          ║"
echo "╚══════════════════════════════════════════════════════════╝"

for gguf in \
    "$MODELS_DIR/qwen2.5-1.5b-instruct-q4_k_m.gguf" \
    "$MODELS_DIR/gemma-2-2b-it-Q4_K_M.gguf" \
    "$MODELS_DIR/Llama-3.2-3B-Instruct-Q4_K_M.gguf" \
    "$MODELS_DIR/qwen2.5-3b-instruct-q4_k_m.gguf" \
    "$MODELS_DIR/Phi-3-mini-4k-instruct-Q4_K_M.gguf" \
    "$MODELS_DIR/Qwen2.5-7B-Instruct-Q4_K_M.gguf" \
    "$MODELS_DIR/gemma-3-12b-it-Q4_K_M.gguf"; do
    if [ -f "$gguf" ]; then
        name=$(basename "$gguf")
        echo ""
        echo "── Categorizer llama.cpp: $name ──"
        $PYTHON $CAT_BENCH --runs $RUNS --backend local_llama_cpp --model-path "$gguf" || {
            echo "  [WARN] Categorizer $name failed — skipping"
        }
    fi
done

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  PHASE 5/6: Categorizer — Ollama (7 models)             ║"
echo "╚══════════════════════════════════════════════════════════╝"

for model in \
    "qwen2.5:1.5b-instruct" \
    "gemma2:2b" \
    "llama3.2:3b" \
    "qwen2.5:3b-instruct" \
    "phi3:3.8b" \
    "qwen2.5:7b-instruct" \
    "gemma3:12b"; do
    echo ""
    echo "── Categorizer Ollama: $model ──"
    $PYTHON $CAT_BENCH --runs $RUNS --backend local_ollama --model "$model" || {
        echo "  [WARN] Categorizer $model failed — skipping"
    }
done

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  PHASE 6/6: Categorizer — OpenAI gpt-4o-mini            ║"
echo "╚══════════════════════════════════════════════════════════╝"

$PYTHON $CAT_BENCH --runs $RUNS --backend openai --model gpt-4o-mini --api-key "$OPENAI_KEY" || {
    echo "  [WARN] Categorizer OpenAI failed"
}

echo ""
echo "============================================================"
echo "  ALL BENCHMARKS COMPLETE (Classifier + Categorizer)"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"
