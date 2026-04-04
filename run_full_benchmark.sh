#!/usr/bin/env bash
# Full benchmark: classifier + categorizer × llama.cpp + Ollama + OpenAI.
#
# Self-service: auto-downloads missing models, skips models that don't fit
# in RAM, auto-pulls Ollama models. Just clone, install deps, and run.
#
# Usage: bash run_full_benchmark.sh [OPENAI_API_KEY]
#   OPENAI_API_KEY is optional — if omitted, OpenAI phases are skipped.

set -euo pipefail
cd "$(dirname "$0")"

BENCH="tests/benchmark_pipeline.py"
CAT_BENCH="tests/benchmark_categorizer.py"
RUNS=1
MODELS_DIR="$HOME/.spendifai/models"
OPENAI_KEY="${1:-}"

# ── Environment bootstrap ────────────────────────────────────────────────
# Ensures Python venv + dependencies are ready. Zero manual steps.

if [ ! -d ".venv" ]; then
    echo "[setup] Creating virtual environment..."
    if command -v uv &>/dev/null; then
        uv venv .venv
    else
        python3 -m venv .venv
    fi
fi

PYTHON=".venv/bin/python"

# Install/sync dependencies
if command -v uv &>/dev/null; then
    echo "[setup] uv sync..."
    uv sync --quiet 2>/dev/null || uv pip install -r requirements.txt --quiet 2>/dev/null || true
else
    echo "[setup] pip install..."
    $PYTHON -m pip install -r requirements.txt --quiet 2>/dev/null || true
fi

# ── Utility functions ─────────────────────────────────────────────────────

get_ram_gb() {
    if [ "$(uname)" = "Darwin" ]; then
        sysctl -n hw.memsize 2>/dev/null | awk '{printf "%d", $1/1073741824}'
    else
        grep MemTotal /proc/meminfo 2>/dev/null | awk '{printf "%d", $2/1048576}'
    fi
}

get_file_size_gb() {
    local file="$1"
    if [ "$(uname)" = "Darwin" ]; then
        stat -f%z "$file" 2>/dev/null | awk '{printf "%.1f", $1/1073741824}'
    else
        stat --printf="%s" "$file" 2>/dev/null | awk '{printf "%.1f", $1/1073741824}'
    fi
}

can_fit_in_ram() {
    # Model needs ~1.5x file size in RAM (model + context + overhead)
    local file="$1"
    local ram_gb=$(get_ram_gb)
    local file_bytes
    if [ "$(uname)" = "Darwin" ]; then
        file_bytes=$(stat -f%z "$file" 2>/dev/null || echo 0)
    else
        file_bytes=$(stat --printf="%s" "$file" 2>/dev/null || echo 0)
    fi
    local needed_gb=$(echo "$file_bytes" | awk '{printf "%d", ($1/1073741824) * 1.5 + 2}')
    [ "$ram_gb" -ge "$needed_gb" ]
}

# ── GGUF model registry (repo → filename) ────────────────────────────────
# Format: "hf_repo|hf_filename|local_filename"
GGUF_MODELS=(
    "Qwen/Qwen2.5-1.5B-Instruct-GGUF|qwen2.5-1.5b-instruct-q4_k_m.gguf|qwen2.5-1.5b-instruct-q4_k_m.gguf"
    "bartowski/gemma-2-2b-it-GGUF|gemma-2-2b-it-Q4_K_M.gguf|gemma-2-2b-it-Q4_K_M.gguf"
    "bartowski/Llama-3.2-3B-Instruct-GGUF|Llama-3.2-3B-Instruct-Q4_K_M.gguf|Llama-3.2-3B-Instruct-Q4_K_M.gguf"
    "Qwen/Qwen2.5-3B-Instruct-GGUF|qwen2.5-3b-instruct-q4_k_m.gguf|qwen2.5-3b-instruct-q4_k_m.gguf"
    "microsoft/Phi-3-mini-4k-instruct-gguf|Phi-3-mini-4k-instruct-Q4_K_M.gguf|Phi-3-mini-4k-instruct-Q4_K_M.gguf"
    "Qwen/Qwen2.5-7B-Instruct-GGUF|qwen2.5-7b-instruct-q4_k_m.gguf|Qwen2.5-7B-Instruct-Q4_K_M.gguf"
    "google/gemma-3-12b-it-GGUF|gemma-3-12b-it-Q4_K_M.gguf|gemma-3-12b-it-Q4_K_M.gguf"
)

# Ollama equivalents (same order)
OLLAMA_MODELS=(
    "qwen2.5:1.5b-instruct"
    "gemma2:2b"
    "llama3.2:3b"
    "qwen2.5:3b-instruct"
    "phi3:3.8b"
    "qwen2.5:7b-instruct"
    "gemma3:12b"
)

# ── HF download helper ───────────────────────────────────────────────────

hf_download() {
    local repo="$1" file="$2" dest="$3"
    mkdir -p "$MODELS_DIR"
    if command -v huggingface-cli &>/dev/null; then
        huggingface-cli download "$repo" "$file" --local-dir "$MODELS_DIR" 2>&1 | tail -1
    elif $PYTHON -c "import huggingface_hub" 2>/dev/null; then
        $PYTHON -m huggingface_hub.commands.huggingface_cli download "$repo" "$file" --local-dir "$MODELS_DIR" 2>&1 | tail -1
    else
        echo "  [ERROR] huggingface-cli not found. Install: pip install huggingface_hub"
        return 1
    fi
}

# ── Startup ───────────────────────────────────────────────────────────────

RAM_GB=$(get_ram_gb)

echo "============================================================"
echo "  FULL BENCHMARK: llama.cpp + Ollama + OpenAI"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "  RAM: ${RAM_GB}GB"
echo "  OpenAI: $([ -n "$OPENAI_KEY" ] && echo 'YES' || echo 'SKIP (no key)')"
echo "============================================================"

# Generate synthetic files if needed
if [ ! -f "tests/generated_files/manifest.csv" ]; then
    echo "[setup] Generating synthetic files..."
    $PYTHON tests/generate_synthetic_files.py
fi

# ── Phase 1/6: Classifier — llama.cpp ─────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  PHASE 1/6: Classifier — llama.cpp                      ║"
echo "╚══════════════════════════════════════════════════════════╝"

for entry in "${GGUF_MODELS[@]}"; do
    IFS='|' read -r hf_repo hf_file local_file <<< "$entry"
    gguf="$MODELS_DIR/$local_file"

    # Auto-download if missing
    if [ ! -f "$gguf" ]; then
        echo ""
        echo "  [download] $local_file from $hf_repo..."
        hf_download "$hf_repo" "$hf_file" "$gguf" || {
            echo "  [SKIP] Download failed for $local_file"
            continue
        }
    fi

    # RAM check
    if ! can_fit_in_ram "$gguf"; then
        size_gb=$(get_file_size_gb "$gguf")
        echo ""
        echo "  [SKIP] $local_file (${size_gb}GB) — needs ~$((${size_gb%.*} * 2 + 2))GB RAM, available ${RAM_GB}GB"
        continue
    fi

    echo ""
    echo "── [$(date '+%H:%M:%S')] llama.cpp: $local_file ──"
    $PYTHON $BENCH --runs $RUNS --backend local_llama_cpp --model-path "$gguf" || {
        echo "  [WARN] $local_file failed — skipping"
    }
done

# ── Phase 2/6: Classifier — Ollama ────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  PHASE 2/6: Classifier — Ollama                         ║"
echo "╚══════════════════════════════════════════════════════════╝"

if curl -sf http://localhost:11434/api/tags > /dev/null 2>&1; then
    for model in "${OLLAMA_MODELS[@]}"; do
        # Auto-pull if not available
        if ! ollama show "$model" > /dev/null 2>&1; then
            echo ""
            echo "  [pull] $model..."
            ollama pull "$model" || {
                echo "  [SKIP] Pull failed for $model"
                continue
            }
        fi

        echo ""
        echo "── [$(date '+%H:%M:%S')] Ollama: $model ──"
        $PYTHON $BENCH --runs $RUNS --backend local_ollama --model "$model" || {
            echo "  [WARN] $model failed — skipping"
        }
    done
else
    echo "  [SKIP] Ollama not running — skipping all Ollama models"
fi

# ── Phase 3/6: Classifier — OpenAI ────────────────────────────────────────
if [ -n "$OPENAI_KEY" ]; then
    echo ""
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║  PHASE 3/6: Classifier — OpenAI gpt-4o-mini             ║"
    echo "╚══════════════════════════════════════════════════════════╝"

    $PYTHON $BENCH --runs $RUNS --backend openai --model gpt-4o-mini --api-key "$OPENAI_KEY" || {
        echo "  [WARN] OpenAI failed"
    }
fi

# ── Phase 4/6: Categorizer — llama.cpp ────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  PHASE 4/6: Categorizer — llama.cpp                     ║"
echo "╚══════════════════════════════════════════════════════════╝"

for entry in "${GGUF_MODELS[@]}"; do
    IFS='|' read -r hf_repo hf_file local_file <<< "$entry"
    gguf="$MODELS_DIR/$local_file"

    [ ! -f "$gguf" ] && continue
    can_fit_in_ram "$gguf" || continue

    echo ""
    echo "── [$(date '+%H:%M:%S')] Categorizer llama.cpp: $local_file ──"
    $PYTHON $CAT_BENCH --runs $RUNS --backend local_llama_cpp --model-path "$gguf" || {
        echo "  [WARN] Categorizer $local_file failed — skipping"
    }
done

# ── Phase 5/6: Categorizer — Ollama ───────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  PHASE 5/6: Categorizer — Ollama                        ║"
echo "╚══════════════════════════════════════════════════════════╝"

if curl -sf http://localhost:11434/api/tags > /dev/null 2>&1; then
    for model in "${OLLAMA_MODELS[@]}"; do
        ollama show "$model" > /dev/null 2>&1 || continue

        echo ""
        echo "── [$(date '+%H:%M:%S')] Categorizer Ollama: $model ──"
        $PYTHON $CAT_BENCH --runs $RUNS --backend local_ollama --model "$model" || {
            echo "  [WARN] Categorizer $model failed — skipping"
        }
    done
else
    echo "  [SKIP] Ollama not running"
fi

# ── Phase 6/6: Categorizer — OpenAI ──────────────────────────────────────
if [ -n "$OPENAI_KEY" ]; then
    echo ""
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║  PHASE 6/6: Categorizer — OpenAI gpt-4o-mini            ║"
    echo "╚══════════════════════════════════════════════════════════╝"

    $PYTHON $CAT_BENCH --runs $RUNS --backend openai --model gpt-4o-mini --api-key "$OPENAI_KEY" || {
        echo "  [WARN] Categorizer OpenAI failed"
    }
fi

echo ""
echo "============================================================"
echo "  ALL BENCHMARKS COMPLETE (Classifier + Categorizer)"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "  Results: tests/generated_files/benchmark/results_all_runs.csv"
echo "============================================================"
