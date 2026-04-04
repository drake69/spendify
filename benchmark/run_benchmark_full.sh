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

SW_VERSION=$(cat "benchmark/.version" 2>/dev/null || git rev-parse --short HEAD 2>/dev/null || echo "unknown")

echo "════════════════════════════════════════════════════════════"
echo "  SPENDIFY FULL BENCHMARK  —  $(date '+%Y-%m-%d %H:%M:%S')"
echo "  Version  : $SW_VERSION"
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

# GPU detection — determines which llama-cpp-python wheel to install
# Result: GPU_BACKEND = metal | cuda | rocm | cpu
#         GPU_LABEL   = human-readable description
#         CU_TAG      = wheel tag (cu121..cu125, only for cuda)
GPU_BACKEND="cpu"
GPU_LABEL="CPU-only"
CU_TAG=""
if [ "$SKIP_LLAMA" = false ]; then
    if [ "$(uname)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
        GPU_BACKEND="metal"
        GPU_LABEL="Apple Silicon (Metal)"
    elif command -v nvidia-smi &>/dev/null; then
        _CUDA_VER=$(nvidia-smi 2>/dev/null | grep -oE 'CUDA Version: [0-9]+\.[0-9]+' | grep -oE '[0-9]+\.[0-9]+' | head -1)
        _GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 | xargs)
        if [ -n "$_CUDA_VER" ]; then
            _CUDA_MAJOR=$(echo "$_CUDA_VER" | cut -d. -f1)
            _CUDA_MINOR=$(echo "$_CUDA_VER" | cut -d. -f2)
            _CUDA_NUM="${_CUDA_MAJOR}${_CUDA_MINOR}"
            # Map to closest supported wheel tag (≤ detected CUDA version)
            CU_TAG="cu121"
            for _v in 125 124 123 122 121; do
                if [ "$_CUDA_NUM" -ge "$_v" ] 2>/dev/null; then
                    CU_TAG="cu${_v}"; break
                fi
            done
            GPU_BACKEND="cuda"
            GPU_LABEL="NVIDIA $_GPU_NAME (CUDA $_CUDA_VER → wheel: $CU_TAG)"
        else
            GPU_BACKEND="cuda"
            CU_TAG="cu121"
            GPU_LABEL="NVIDIA (CUDA version unknown → wheel: $CU_TAG)"
        fi
    elif command -v rocm-smi &>/dev/null; then
        GPU_BACKEND="rocm"
        _ROCM_VER=$(rocm-smi --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+' | head -1)
        GPU_LABEL="AMD ROCm${_ROCM_VER:+ $_ROCM_VER} (build from source)"
    fi
    echo "[gpu] $GPU_LABEL"
fi

# Install deps + llama-cpp-python with correct GPU wheel
uv sync --no-install-package llama-cpp-python --quiet
if [ "$SKIP_LLAMA" = false ]; then
    case "$GPU_BACKEND" in
        metal)
            echo "[setup] Installing llama-cpp-python (Metal — standard PyPI wheel)..."
            uv pip install "llama-cpp-python>=0.3.0" --quiet
            ;;
        cuda)
            echo "[setup] Installing llama-cpp-python ($CU_TAG GPU wheel)..."
            UV_EXTRA_INDEX_URL="https://abetlen.github.io/llama-cpp-python/whl/$CU_TAG" \
                uv pip install "llama-cpp-python>=0.3.0" --quiet
            ;;
        rocm)
            echo "[setup] Building llama-cpp-python from source (HIPBLAS/ROCm)..."
            CMAKE_ARGS="-DGGML_HIPBLAS=on" uv pip install \
                "llama-cpp-python>=0.3.0" --no-binary llama-cpp-python --quiet
            ;;
        *)
            echo "[setup] Installing llama-cpp-python (CPU wheel)..."
            UV_EXTRA_INDEX_URL="https://abetlen.github.io/llama-cpp-python/whl/cpu" \
                uv pip install "llama-cpp-python>=0.3.0" --quiet
            ;;
    esac
fi
echo "[ok] Python env ready"

# ── Step 3a: llama.cpp — download missing GGUF ───────────────────────────
if [ "$SKIP_LLAMA" = false ]; then
    echo ""
    echo "── [3a/4] llama.cpp setup — checking GGUF models..."
    mkdir -p "$MODELS_DIR"

    # _hf_download <repo_id> <filename> --local-dir <dir>
    # Usa direttamente l'API Python di huggingface_hub — non dipende da
    # entry point CLI (huggingface-cli) che può mancare o cambiare path.
    _hf_download() {
        local repo_id="$1" filename="$2" local_dir="$4"
        uv run python - "$repo_id" "$filename" "$local_dir" <<'PYEOF'
import sys
from huggingface_hub import hf_hub_download
repo_id, filename, local_dir = sys.argv[1], sys.argv[2], sys.argv[3]
path = hf_hub_download(repo_id=repo_id, filename=filename, local_dir=local_dir)
print(f"  → {path}")
PYEOF
    }

    # Detect available RAM and GPU memory for model size filtering
    SYSTEM_RAM_MB=0
    if [ "$(uname)" = "Darwin" ]; then
        SYSTEM_RAM_MB=$(( $(sysctl -n hw.memsize) / 1048576 ))
    elif [ -f /proc/meminfo ]; then
        SYSTEM_RAM_MB=$(awk '/MemTotal/ {printf "%d", $2/1024}' /proc/meminfo)
    fi
    # Size limit: VRAM for NVIDIA, 75% unified RAM for Metal, RAM/2 for CPU
    case "$GPU_BACKEND" in
        metal)
            # Apple Silicon: unified memory — GPU and CPU share the same pool
            MAX_MODEL_MB=$(( SYSTEM_RAM_MB * 3 / 4 ))
            [ "$SYSTEM_RAM_MB" -gt 0 ] && \
                echo "[check] Unified RAM: $((SYSTEM_RAM_MB / 1024)) GB → max model: $((MAX_MODEL_MB / 1024)) GB"
            ;;
        cuda)
            # NVIDIA: use VRAM as the bottleneck
            _VRAM_MB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null \
                       | head -1 | tr -d ' ')
            if [ -n "$_VRAM_MB" ] && [ "$_VRAM_MB" -gt 0 ] 2>/dev/null; then
                MAX_MODEL_MB=$_VRAM_MB
                echo "[check] System RAM: $((SYSTEM_RAM_MB / 1024)) GB, GPU VRAM: $((_VRAM_MB / 1024)) GB → max model: $((MAX_MODEL_MB / 1024)) GB"
            else
                MAX_MODEL_MB=$(( SYSTEM_RAM_MB / 2 ))
                echo "[check] System RAM: $((SYSTEM_RAM_MB / 1024)) GB → max model: $((MAX_MODEL_MB / 1024)) GB (VRAM unknown)"
            fi
            ;;
        *)
            # CPU / ROCm: conservative RAM/2 heuristic
            MAX_MODEL_MB=$(( SYSTEM_RAM_MB / 2 ))
            [ "$SYSTEM_RAM_MB" -gt 0 ] && \
                echo "[check] System RAM: $((SYSTEM_RAM_MB / 1024)) GB → max model: $((MAX_MODEL_MB / 1024)) GB"
            ;;
    esac

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
            if _hf_download "$gguf_repo" "$gguf_file" --local-dir "$MODELS_DIR"; then
                GGUF_DOWNLOADED=$((GGUF_DOWNLOADED + 1))
            else
                echo "[WARN] Failed to download $gguf_file from $gguf_repo — skipping"
            fi
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
echo "  GPU        : $GPU_LABEL"
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
MIN_CTX=4096
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
echo "  Risultati : benchmark/results/  (CSV per-macchina versionati)"
echo "  Log       : $LOG_FILE"
echo ""
echo "Passo successivo — scegli il tuo metodo di trasferimento:"
echo ""
echo "  USB (copia risultati sulla chiavetta, poi raccogli dalla dev):"
echo "    Linux/macOS : bash benchmark/bench_save_usb.sh --dest /Volumes/BENCH_USB"
echo "    Windows     : powershell -ExecutionPolicy Bypass -File benchmark\bench_save_usb.ps1 -Dest E:\BENCH_USB"
echo ""
echo "  SSH (la dev raccoglie direttamente da questa macchina):"
echo "    Linux/macOS : bash benchmark/bench_pull_ssh.sh --from user@$(hostname):$(pwd)"
echo "    Windows     : powershell -ExecutionPolicy Bypass -File benchmark\bench_pull_ssh.ps1 -From user@$(hostname):$(pwd)"
