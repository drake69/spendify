#!/usr/bin/env bash
# Setup all benchmark models (Ollama + GGUF) on a new machine.
# Usage: bash tests/setup_benchmark_models.sh [--skip-ollama] [--skip-gguf] [--small-only]

set -euo pipefail

MODELS_DIR="$HOME/.spendify/models"
SKIP_OLLAMA=false
SKIP_GGUF=false
SMALL_ONLY=false  # only models <= 3B (for 4-8GB RAM machines)

for arg in "$@"; do
    case $arg in
        --skip-ollama) SKIP_OLLAMA=true ;;
        --skip-gguf)   SKIP_GGUF=true ;;
        --small-only)  SMALL_ONLY=true ;;
    esac
done

echo "============================================================"
echo "  Spendify Benchmark — Model Setup"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "  Models dir: $MODELS_DIR"
echo "============================================================"

# ── Ollama models ────────────────────────────────────────────────────────
if [ "$SKIP_OLLAMA" = false ]; then
    echo ""
    echo "── Ollama models ──────────────────────────────────────────"

    # Check Ollama is installed
    if ! command -v ollama &>/dev/null; then
        echo "[WARN] Ollama not installed. Install from https://ollama.com"
        echo "       macOS: brew install ollama"
        echo "       Linux: curl -fsSL https://ollama.com/install.sh | sh"
        echo "       Windows: https://ollama.com/download/windows"
    else
        # Small models (always)
        echo "[pull] phi3:3.8b..."
        ollama pull phi3:3.8b

        echo "[pull] qwen2.5:7b-instruct..."
        ollama pull qwen2.5:7b-instruct

        echo "[pull] deepseek-r1:8b..."
        ollama pull deepseek-r1:8b

        if [ "$SMALL_ONLY" = false ]; then
            echo "[pull] gemma3:12b..."
            ollama pull gemma3:12b

            echo "[pull] qwen2.5:14b-instruct..."
            ollama pull qwen2.5:14b-instruct

            echo "[pull] mannix/llama3-12b..."
            ollama pull mannix/llama3-12b

            # Only if enough RAM (>= 48GB)
            RAM_GB=$(
                if [ "$(uname)" = "Darwin" ]; then
                    sysctl -n hw.memsize 2>/dev/null | awk '{printf "%d", $1/1073741824}'
                else
                    grep MemTotal /proc/meminfo 2>/dev/null | awk '{printf "%d", $2/1048576}'
                fi
            )
            if [ "${RAM_GB:-0}" -ge 48 ]; then
                echo "[pull] llama3.1:70b (RAM=$RAM_GB GB >= 48GB)..."
                ollama pull llama3.1:70b
            else
                echo "[skip] llama3.1:70b — requires >= 48GB RAM (you have ${RAM_GB}GB)"
            fi
        fi
    fi
fi

# ── GGUF models for llama.cpp ────────────────────────────────────────────
if [ "$SKIP_GGUF" = false ]; then
    echo ""
    echo "── GGUF models (llama.cpp) ──────────────────────────────"

    mkdir -p "$MODELS_DIR"

    # Find hf download command
    HF_CMD=""
    if command -v hf &>/dev/null; then
        HF_CMD="hf download"
    elif command -v huggingface-cli &>/dev/null; then
        HF_CMD="huggingface-cli download"
    else
        # Try via Python
        PYTHON="${PYTHON:-.venv/bin/python}"
        if $PYTHON -c "import huggingface_hub" 2>/dev/null; then
            HF_CMD="$PYTHON -m huggingface_hub.commands.huggingface_cli download"
        else
            echo "[WARN] huggingface-cli not found. Install with: pip install huggingface_hub"
            echo "       Or: brew install huggingface-cli"
            SKIP_GGUF=true
        fi
    fi

    if [ "$SKIP_GGUF" = false ]; then
        # Small models (always) — fit in 4GB RAM
        echo "[download] Qwen2.5-1.5B-Instruct Q4_K_M (1.1 GB)..."
        $HF_CMD Qwen/Qwen2.5-1.5B-Instruct-GGUF qwen2.5-1.5b-instruct-q4_k_m.gguf \
            --local-dir "$MODELS_DIR" 2>&1 | tail -1

        echo "[download] gemma-2-2b-it Q4_K_M (1.6 GB)..."
        $HF_CMD bartowski/gemma-2-2b-it-GGUF gemma-2-2b-it-Q4_K_M.gguf \
            --local-dir "$MODELS_DIR" 2>&1 | tail -1

        echo "[download] Llama-3.2-3B-Instruct Q4_K_M (1.9 GB)..."
        $HF_CMD bartowski/Llama-3.2-3B-Instruct-GGUF Llama-3.2-3B-Instruct-Q4_K_M.gguf \
            --local-dir "$MODELS_DIR" 2>&1 | tail -1

        echo "[download] Qwen2.5-3B-Instruct Q4_K_M (2.0 GB)..."
        $HF_CMD Qwen/Qwen2.5-3B-Instruct-GGUF qwen2.5-3b-instruct-q4_k_m.gguf \
            --local-dir "$MODELS_DIR" 2>&1 | tail -1

        echo "[download] Phi-3-mini-4k-instruct Q4_K_M (2.2 GB)..."
        $HF_CMD microsoft/Phi-3-mini-4k-instruct-gguf Phi-3-mini-4k-instruct-Q4_K_M.gguf \
            --local-dir "$MODELS_DIR" 2>&1 | tail -1

        if [ "$SMALL_ONLY" = false ]; then
            echo "[download] Qwen2.5-7B-Instruct Q4_K_M (4.4 GB)..."
            $HF_CMD Qwen/Qwen2.5-7B-Instruct-GGUF qwen2.5-7b-instruct-q4_k_m.gguf \
                --local-dir "$MODELS_DIR" 2>&1 | tail -1

            echo "[download] gemma-3-12b-it Q4_K_M (6.8 GB)..."
            $HF_CMD google/gemma-3-12b-it-GGUF gemma-3-12b-it-Q4_K_M.gguf \
                --local-dir "$MODELS_DIR" 2>&1 | tail -1
        fi
    fi
fi

# ── Summary ──────────────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo "  Setup complete!"
echo "============================================================"

if [ "$SKIP_GGUF" = false ]; then
    echo ""
    echo "  GGUF models in $MODELS_DIR:"
    ls -lhS "$MODELS_DIR"/*.gguf 2>/dev/null || echo "  (none)"
fi

if [ "$SKIP_OLLAMA" = false ] && command -v ollama &>/dev/null; then
    echo ""
    echo "  Ollama models:"
    ollama list 2>/dev/null || echo "  (Ollama not running)"
fi

echo ""
echo "  Next: bash tests/run_all_benchmarks.sh"
