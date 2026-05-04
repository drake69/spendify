#!/usr/bin/env bash
# Diagnostic report: shows backend setup, models, GPU, RAM — changes nothing.
#
# Usage:
#   bash benchmark/bench_report.sh
#   bash benchmark/bench_report.sh --vllm-url http://gpu:8000/v1
#   bash benchmark/bench_report.sh --ollama-url http://192.168.1.5:11434

[ -z "${BASH_VERSION:-}" ] && exec bash "$0" "$@"
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON=".venv/bin/python"
MODELS_DIR="$HOME/.spendifai/models"
MODELS_CSV="benchmark/benchmark_models.csv"
VLLM_URL="http://localhost:8000/v1"
OLLAMA_URL="http://localhost:11434"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --vllm-url)   VLLM_URL="$2"; shift 2 ;;
        --ollama-url) OLLAMA_URL="$2"; shift 2 ;;
        *)            shift ;;
    esac
done

echo "════════════════════════════════════════════════════════════"
echo "  SPENDIFY BENCH REPORT  —  $(date '+%Y-%m-%d %H:%M:%S')"
echo "════════════════════════════════════════════════════════════"

# ── System ───────────────────────────────────────────────────────────────
echo ""
echo "── SYSTEM"
echo "  Hostname : $(hostname)"
echo "  OS       : $(uname -srm)"
if [ "$(uname)" = "Darwin" ]; then
    _CPU=$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo "?")
else
    _CPU=$(grep -m1 "model name" /proc/cpuinfo 2>/dev/null | cut -d: -f2 | xargs || echo "?")
fi
echo "  CPU      : $_CPU"
if [ "$(uname)" = "Darwin" ]; then
    RAM_MB=$(( $(sysctl -n hw.memsize) / 1048576 ))
elif [ -f /proc/meminfo ]; then
    RAM_MB=$(awk '/MemTotal/ {printf "%d", $2/1024}' /proc/meminfo)
else
    RAM_MB=0
fi
[ "$RAM_MB" -gt 0 ] && echo "  RAM      : $((RAM_MB / 1024)) GB ($RAM_MB MB)"

# ── GPU ──────────────────────────────────────────────────────────────────
echo ""
echo "── GPU"
GPU_FOUND=false
if [ "$(uname)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
    echo "  Backend  : Apple Silicon (Metal)"
    echo "  Memory   : Unified — $((RAM_MB / 1024)) GB"
    GPU_FOUND=true
fi
if command -v nvidia-smi &>/dev/null; then
    nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>/dev/null | \
        while IFS= read -r line; do echo "  NVIDIA   : $line"; done
    CUDA_VER=$(nvidia-smi 2>/dev/null | grep -oE 'CUDA Version: [0-9]+\.[0-9]+' | grep -oE '[0-9]+\.[0-9]+' | head -1)
    [ -n "$CUDA_VER" ] && echo "  CUDA     : $CUDA_VER"
    GPU_FOUND=true
fi
if command -v rocm-smi &>/dev/null; then
    echo "  ROCm     : $(rocm-smi --version 2>/dev/null | head -1 || echo 'installed')"
    rocm-smi --showproductname 2>/dev/null | grep -i "card\|GPU" | while IFS= read -r line; do echo "  Device   : $line"; done
    GPU_FOUND=true
fi
if command -v vulkaninfo &>/dev/null; then
    VK_GPU=$(vulkaninfo --summary 2>/dev/null | grep -i "deviceName" | head -1 | sed 's/.*= *//')
    VK_API=$(vulkaninfo --summary 2>/dev/null | grep -i "apiVersion" | head -1 | sed 's/.*= *//')
    [ -n "$VK_GPU" ] && echo "  Vulkan   : $VK_GPU (API $VK_API)"
    GPU_FOUND=true
fi
[ "$GPU_FOUND" = false ] && echo "  (none detected)"

# ── Python ───────────────────────────────────────────────────────────────
echo ""
echo "── PYTHON"
if [ -f "$PYTHON" ]; then
    echo "  venv     : $($PYTHON --version 2>&1) ($PYTHON)"
else
    echo "  venv     : NOT FOUND ($PYTHON)"
fi

# ── llama-cpp-python ─────────────────────────────────────────────────────
echo ""
echo "── LLAMA-CPP-PYTHON"
if [ -f "$PYTHON" ] && $PYTHON -c "import llama_cpp" 2>/dev/null; then
    LLAMA_VER=$($PYTHON -c "import llama_cpp; print(llama_cpp.__version__)" 2>/dev/null || echo "?")
    echo "  Version  : $LLAMA_VER"
    # Check GPU backend support by looking for the backend shared libraries.
    # IMPORTANT: each backend has platform-specific extensions:
    #   - Linux  : .so   (libggml-vulkan.so, libggml-cuda.so, libggml-metal.so)
    #   - macOS  : .dylib (libggml-metal.dylib)
    # Must check BOTH extensions or the report will falsely claim "no support"
    # even when the backend is correctly compiled (we learned this the hard way
    # with Metal on macOS and Vulkan on some Linux distros).
    LLAMA_SO=$($PYTHON -c "import llama_cpp; print(llama_cpp.__file__)" 2>/dev/null || echo "")
    if [ -n "$LLAMA_SO" ]; then
        LIB_DIR="$(dirname "$LLAMA_SO")/lib"
        [ ! -d "$LIB_DIR" ] && LIB_DIR=$(dirname "$LLAMA_SO")
        HAS_VULKAN=0; { [ -f "$LIB_DIR/libggml-vulkan.so" ] || [ -f "$LIB_DIR/libggml-vulkan.dylib" ]; } && HAS_VULKAN=1
        HAS_CUDA=0;   { [ -f "$LIB_DIR/libggml-cuda.so" ]   || [ -f "$LIB_DIR/libggml-cuda.dylib" ]; }   && HAS_CUDA=1
        HAS_METAL=0;  { [ -f "$LIB_DIR/libggml-metal.so" ]  || [ -f "$LIB_DIR/libggml-metal.dylib" ]; }  && HAS_METAL=1
        [ "$HAS_VULKAN" -eq 1 ] && echo "  Vulkan   : YES" || echo "  Vulkan   : no"
        [ "$HAS_CUDA" -eq 1 ]   && echo "  CUDA     : YES" || echo "  CUDA     : no"
        [ "$HAS_METAL" -eq 1 ]  && echo "  Metal    : YES" || echo "  Metal    : no"
    fi
    # Compatibility check: does the build match the detected GPU?
    echo ""
    echo "  ── Compatibility"
    if [ "$(uname)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
        if [ "${HAS_METAL:-0}" -gt 0 ] 2>/dev/null; then
            echo "  ✓ Metal GPU detected and llama-cpp-python has Metal support"
        else
            echo "  ✗ Metal GPU detected but llama-cpp-python LACKS Metal support"
            echo "    Fix: source .venv/bin/activate && uv pip install 'llama-cpp-python>=0.3.0' --force-reinstall"
        fi
    elif command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
        if [ "${HAS_CUDA:-0}" -gt 0 ] 2>/dev/null; then
            echo "  ✓ NVIDIA GPU detected and llama-cpp-python has CUDA support"
        else
            echo "  ✗ NVIDIA GPU detected but llama-cpp-python LACKS CUDA support"
            echo "    Fix: source .venv/bin/activate && UV_EXTRA_INDEX_URL=https://abetlen.github.io/llama-cpp-python/whl/cu121 uv pip install 'llama-cpp-python>=0.3.0' --force-reinstall"
        fi
    elif command -v vulkaninfo &>/dev/null && vulkaninfo --summary &>/dev/null; then
        if [ "${HAS_VULKAN:-0}" -gt 0 ] 2>/dev/null; then
            echo "  ✓ Vulkan GPU detected and llama-cpp-python has Vulkan support"
        else
            echo "  ✗ Vulkan GPU detected but llama-cpp-python LACKS Vulkan support"
            echo "    Fix: source .venv/bin/activate && CMAKE_ARGS=\"-DGGML_VULKAN=ON\" uv pip install 'llama-cpp-python>=0.3.0' --no-binary llama-cpp-python --no-cache-dir --force-reinstall"
        fi
    elif command -v rocm-smi &>/dev/null; then
        if [ "${HAS_CUDA:-0}" -gt 0 ] 2>/dev/null; then
            echo "  ✓ ROCm GPU detected and llama-cpp-python has HIP/ROCm support"
        else
            echo "  ✗ ROCm GPU detected but llama-cpp-python LACKS HIP/ROCm support"
            echo "    Fix: source .venv/bin/activate && CMAKE_ARGS=\"-DGGML_HIPBLAS=on\" uv pip install 'llama-cpp-python>=0.3.0' --no-binary llama-cpp-python --force-reinstall"
        fi
    else
        echo "  - No GPU detected — CPU-only mode"
    fi
else
    echo "  Status   : NOT INSTALLED"
fi

# ── Detect backend availability ──────────────────────────────────────────
OLLAMA_UP=false
OLLAMA_VER=""
if command -v ollama &>/dev/null; then
    OLLAMA_VER=$(ollama --version 2>/dev/null || echo "?")
    curl -sf "$OLLAMA_URL/api/tags" > /dev/null 2>&1 && OLLAMA_UP=true
fi

VLLM_UP=false
VLLM_MODEL=""
VLLM_RESP=$(curl -sf "$VLLM_URL/models" 2>/dev/null || true)
if [ -n "$VLLM_RESP" ]; then
    VLLM_UP=true
    VLLM_MODEL=$($PYTHON -c "
import sys, json
d = json.loads(sys.stdin.read())
models = d.get('data', [])
print(models[0]['id'] if models else '')
" 2>/dev/null <<< "$VLLM_RESP" || echo "")
fi

# ── Benchmark models by backend (from CSV) ───────────────────────────────
echo ""
echo "── BENCHMARK MODELS ($MODELS_CSV)"
if [ ! -f "$MODELS_CSV" ]; then
    echo "  (CSV not found)"
else
    CSV_HEADER=$(head -1 "$MODELS_CSV")
    _csv_field() {
        local row="$1" header="$2" col="$3"
        local idx
        idx=$(echo "$header" | tr ',' '\n' | grep -n "^${col}$" | cut -d: -f1)
        [ -z "$idx" ] && echo "" && return
        echo "$row" | cut -d',' -f"$idx"
    }

    ENABLED=$(tail -n +2 "$MODELS_CSV" | grep -v '^#' | grep -v '^[[:space:]]*$' | grep -c ',true' || echo "0")
    TOTAL=$(tail -n +2 "$MODELS_CSV" | grep -v '^#' | grep -cv '^[[:space:]]*$' || echo "0")
    echo "  Enabled  : $ENABLED / $TOTAL models"

    # ── llama.cpp ──
    echo ""
    echo "  ── llama.cpp (GGUF → $MODELS_DIR)"
    printf "  %-30s %7s  %-6s  %s\n" "Model" "Size" "Status" "File"
    echo "  ──────────────────────────────────────────────────────────────────────"
    LLAMA_READY=0; LLAMA_MISS=0; LLAMA_SKIP=0
    while IFS= read -r row; do
        name=$(_csv_field "$row" "$CSV_HEADER" "name")
        gguf=$(_csv_field "$row" "$CSV_HEADER" "gguf_file")
        size=$(_csv_field "$row" "$CSV_HEADER" "size_mb")
        enabled=$(_csv_field "$row" "$CSV_HEADER" "enabled")
        [ -z "$gguf" ] && continue
        if [ "$enabled" != "true" ]; then
            printf "  %-30s %6sM  %-6s  %s\n" "$name" "$size" "skip" "$gguf"
            LLAMA_SKIP=$((LLAMA_SKIP + 1))
        elif [ -f "$MODELS_DIR/$gguf" ]; then
            printf "  %-30s %6sM  %-6s  %s\n" "$name" "$size" "✓ ok" "$gguf"
            LLAMA_READY=$((LLAMA_READY + 1))
        else
            printf "  %-30s %6sM  %-6s  %s\n" "$name" "$size" "✗ miss" "$gguf"
            LLAMA_MISS=$((LLAMA_MISS + 1))
        fi
    done < <(tail -n +2 "$MODELS_CSV" | grep -v '^#' | grep -v '^[[:space:]]*$')
    echo "  ──────────────────────────────────────────────────────────────────────"
    echo "  Ready: $LLAMA_READY  |  Missing: $LLAMA_MISS  |  Disabled: $LLAMA_SKIP"

    # ── Ollama ──
    echo ""
    echo "  ── Ollama ($OLLAMA_URL)"
    if [ -z "$OLLAMA_VER" ]; then
        echo "  Status   : NOT INSTALLED"
    elif [ "$OLLAMA_UP" = false ]; then
        echo "  Version  : $OLLAMA_VER"
        echo "  Status   : NOT REACHABLE"
    else
        echo "  Version  : $OLLAMA_VER"
        echo "  Status   : running"
        printf "  %-30s %7s  %-6s  %s\n" "Model" "Size" "Status" "Tag"
        echo "  ──────────────────────────────────────────────────────────────────────"
        OLL_READY=0; OLL_MISS=0; OLL_SKIP=0; OLL_NA=0
        while IFS= read -r row; do
            name=$(_csv_field "$row" "$CSV_HEADER" "name")
            tag=$(_csv_field "$row" "$CSV_HEADER" "ollama_tag")
            size=$(_csv_field "$row" "$CSV_HEADER" "size_mb")
            enabled=$(_csv_field "$row" "$CSV_HEADER" "enabled")
            if [ -z "$tag" ]; then
                printf "  %-30s %6sM  %-6s  %s\n" "$name" "$size" "n/a" "(no tag)"
                OLL_NA=$((OLL_NA + 1))
                continue
            fi
            if [ "$enabled" != "true" ]; then
                printf "  %-30s %6sM  %-6s  %s\n" "$name" "$size" "skip" "$tag"
                OLL_SKIP=$((OLL_SKIP + 1))
            elif ollama show "$tag" > /dev/null 2>&1; then
                printf "  %-30s %6sM  %-6s  %s\n" "$name" "$size" "✓ ok" "$tag"
                OLL_READY=$((OLL_READY + 1))
            else
                printf "  %-30s %6sM  %-6s  %s\n" "$name" "$size" "✗ miss" "$tag"
                OLL_MISS=$((OLL_MISS + 1))
            fi
        done < <(tail -n +2 "$MODELS_CSV" | grep -v '^#' | grep -v '^[[:space:]]*$')
        echo "  ──────────────────────────────────────────────────────────────────────"
        echo "  Ready: $OLL_READY  |  Missing: $OLL_MISS  |  Disabled: $OLL_SKIP  |  N/A: $OLL_NA"
    fi

    # ── vLLM ──
    echo ""
    echo "  ── vLLM ($VLLM_URL)"
    if [ "$VLLM_UP" = true ]; then
        echo "  Status   : running"
        echo "  Model    : ${VLLM_MODEL:-(none)}"
    else
        echo "  Status   : NOT REACHABLE"
    fi

    # ── vLLM offline ──
    echo ""
    echo "  ── vLLM offline (in-process)"
    VLLM_OFFLINE_OK=false
    VLLM_OFFLINE_REASON=""
    if [ -f "$PYTHON" ]; then
        VLLM_CHECK=$($PYTHON -c "
import sys
try:
    import vllm
except ImportError:
    print('not_installed'); sys.exit(0)
try:
    import torch
    if not torch.cuda.is_available():
        print('no_cuda'); sys.exit(0)
except ImportError:
    print('no_torch'); sys.exit(0)
print('ok')
" 2>/dev/null || echo "error")
        case "$VLLM_CHECK" in
            ok)
                VLLM_OFFLINE_OK=true
                VLLM_OFFLINE_REASON="importable + CUDA available"
                ;;
            not_installed)
                VLLM_OFFLINE_REASON="vllm NOT INSTALLED (pip install vllm — Linux/CUDA only)"
                ;;
            no_cuda)
                VLLM_OFFLINE_REASON="vllm installed but CUDA NOT AVAILABLE"
                ;;
            no_torch)
                VLLM_OFFLINE_REASON="torch not installed"
                ;;
            *)
                VLLM_OFFLINE_REASON="check failed"
                ;;
        esac
    else
        VLLM_OFFLINE_REASON="venv not found"
    fi

    if [ "$VLLM_OFFLINE_OK" = true ]; then
        echo "  Status   : available ($VLLM_OFFLINE_REASON)"
        VLLM_OFF_COUNT=0
        printf "  %-30s %7s  %-6s  %s\n" "Model" "Size" "Status" "HF Model ID"
        echo "  ──────────────────────────────────────────────────────────────────────"
        while IFS= read -r row; do
            name=$(_csv_field "$row" "$CSV_HEADER" "name")
            vllm_model=$(_csv_field "$row" "$CSV_HEADER" "vllm_model")
            size=$(_csv_field "$row" "$CSV_HEADER" "size_mb")
            enabled=$(_csv_field "$row" "$CSV_HEADER" "enabled")
            [ -z "$vllm_model" ] && continue
            if [ "$enabled" != "true" ]; then
                printf "  %-30s %6sM  %-6s  %s\n" "$name" "$size" "skip" "$vllm_model"
            else
                printf "  %-30s %6sM  %-6s  %s\n" "$name" "$size" "ready" "$vllm_model"
                VLLM_OFF_COUNT=$((VLLM_OFF_COUNT + 1))
            fi
        done < <(tail -n +2 "$MODELS_CSV" | grep -v '^#' | grep -v '^[[:space:]]*$')
        echo "  ──────────────────────────────────────────────────────────────────────"
        echo "  Ready: $VLLM_OFF_COUNT"
    else
        echo "  Status   : UNAVAILABLE — $VLLM_OFFLINE_REASON"
    fi
fi

echo ""
echo "════════════════════════════════════════════════════════════"
echo "  Report complete — no changes made."
echo "════════════════════════════════════════════════════════════"
