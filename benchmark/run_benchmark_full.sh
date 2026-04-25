#!/usr/bin/env bash
# Guard: se invocato con sh invece di bash (es. su Ubuntu dove sh=dash),
# si ri-esegue automaticamente con bash.
[ -z "${BASH_VERSION:-}" ] && exec bash "$0" "$@"
# Full benchmark: classifier + categorizer × all active backends.
#
# Model catalogue: benchmark/benchmark_models.csv
#   gguf_file + gguf_hf_url   → llama.cpp  (empty = model not available on llama)
#   ollama_tag                 → Ollama     (empty = model not available on Ollama)
#   vLLM: auto-detected at runtime from the server (/v1/models)
#
# Auto-detects active backends:
#   llama.cpp     — always, if GGUF files are present (downloads missing ones)
#   Ollama        — if localhost:11434 is reachable (pulls missing models)
#   vLLM          — if localhost:8000/v1/models is reachable (server mode)
#   vLLM offline  — if `import vllm` succeeds + CUDA available (in-process, no server)
#
# Usage:
#   bash benchmark/run_benchmark_full.sh                           # both phases, 1 run
#   bash benchmark/run_benchmark_full.sh --runs 3
#   bash benchmark/run_benchmark_full.sh --benchmark classifier    # classifier only
#   bash benchmark/run_benchmark_full.sh --benchmark categorizer
#   bash benchmark/run_benchmark_full.sh --vllm-url http://gpu:8000/v1
#   bash benchmark/run_benchmark_full.sh --ollama-url http://192.168.1.5:11434
#   bash benchmark/run_benchmark_full.sh --skip-llama
#   bash benchmark/run_benchmark_full.sh --skip-ollama
#   bash benchmark/run_benchmark_full.sh --skip-vllm
#   bash benchmark/run_benchmark_full.sh --skip-vllm-offline
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
SKIP_OLLAMA=true   # Ollama non supporta schema complessi del classifier — usa llama.cpp
SKIP_VLLM=false
SKIP_VLLM_OFFLINE=false
SKIP_SYNC=false
SETUP_ONLY=false
SCENARIO=""       # categorizer-only: cold|nsi_warm|full_warm|country_with|country_without|all
COUNTRY=""        # categorizer-only: ISO country for country_with scenario
MAX_FILES=""      # limit files per model (e.g. 8 for quick scan)
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --benchmark)   BENCHMARK="$2"; shift 2 ;;
        --runs)        RUNS="$2"; shift 2 ;;
        --max-files)   MAX_FILES="$2"; shift 2 ;;
        --vllm-url)    VLLM_URL="$2"; shift 2 ;;
        --ollama-url)  OLLAMA_URL="$2"; shift 2 ;;
        --skip-llama)         SKIP_LLAMA=true; shift ;;
        --skip-ollama)        SKIP_OLLAMA=true; shift ;;
        --skip-vllm)          SKIP_VLLM=true; shift ;;
        --skip-vllm-offline)  SKIP_VLLM_OFFLINE=true; shift ;;
        --setup-only)         SETUP_ONLY=true; shift ;;
        --skip-sync)          SKIP_SYNC=true; shift ;;
        --scenario)    SCENARIO="$2"; shift 2 ;;   # categorizer only
        --country)     COUNTRY="$2"; shift 2 ;;    # categorizer only
        *)             EXTRA_ARGS+=("$1"); shift ;;
    esac
done

# ── Log setup ─────────────────────────────────────────────────────────────
LOG_DIR="benchmark/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/benchmark_full_$(date '+%Y%m%d_%H%M%S').log"
exec > >(tee -a "$LOG_FILE") 2>&1

SW_VERSION=$(bash benchmark/bench_guard.sh) || exit 1

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

# GPU detection — always run, determines GPU_BACKEND for VRAM sizing and llama wheel
# Result: GPU_BACKEND = metal | cuda | rocm | vulkan | cpu
#         GPU_LABEL   = human-readable description
#         CU_TAG      = wheel tag (cu121..cu125, only for cuda)
GPU_BACKEND="cpu"
GPU_LABEL="CPU-only"
CU_TAG=""
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
elif command -v rocm-smi &>/dev/null && command -v rocminfo &>/dev/null && rocminfo 2>/dev/null | grep -q "gfx9"; then
    # ROCm backend only if rocminfo works AND GPU is CDNA/gfx9xx (MI series).
    # RDNA GPUs (gfx10xx, gfx11xx) have rocm-smi but ROCm compute doesn't work → use Vulkan.
    GPU_BACKEND="rocm"
    _ROCM_VER=$(rocm-smi --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+' | head -1)
    GPU_LABEL="AMD ROCm${_ROCM_VER:+ $_ROCM_VER} (build from source)"
elif command -v vulkaninfo &>/dev/null && vulkaninfo --summary 2>/dev/null | grep -qi "deviceName.*AMD\|deviceName.*Radeon"; then
    GPU_BACKEND="vulkan"
    _VK_GPU=$(vulkaninfo --summary 2>/dev/null | grep -i "deviceName" | head -1 | sed 's/.*= *//')
    GPU_LABEL="AMD Vulkan: ${_VK_GPU:-unknown} (build from source)"
fi
echo "[gpu] $GPU_LABEL"

# Install deps — protect custom-compiled packages from uv sync.
# Strategy:
#   1. Backup all packages listed in benchmark/.custom_packages (with version)
#   2. Run uv sync (may remove/downgrade custom packages)
#   3. Compare: if uv installed a LOWER version → restore backup (custom build wins)
#              if uv installed same or HIGHER  → keep uv's version (upgrade wins)
#              if uv removed it entirely       → restore backup
_CUSTOM_BACKUP=".venv/_custom_backup"
_SITE_PKGS=$($PYTHON -c "import site; print(site.getsitepackages()[0])" 2>/dev/null || echo "")
_CUSTOM_LIST="benchmark/.custom_packages"

# Extract version from dist-info dir name: "llama_cpp_python-0.3.19.dist-info" → "0.3.19"
_pkg_version() {
    local pkg_under="$1"
    local search_dir="${2:-$_SITE_PKGS}"
    local di=$(ls -d "${search_dir}/${pkg_under}-"*.dist-info 2>/dev/null | head -1)
    [ -z "$di" ] && echo "" && return
    basename "$di" | sed "s/^${pkg_under}-//" | sed 's/\.dist-info$//'
}

# Compare versions: returns 0 if v1 >= v2, 1 if v1 < v2
_version_ge() {
    [ "$1" = "$2" ] && return 0
    local sorted_first
    sorted_first=$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -1)
    [ "$sorted_first" = "$2" ]
}

_backup_custom() {
    [ -z "$_SITE_PKGS" ] && return
    [ -f "$_CUSTOM_LIST" ] || return
    rm -rf "$_CUSTOM_BACKUP"
    mkdir -p "$_CUSTOM_BACKUP"
    local n=0
    while IFS= read -r pkg; do
        [ -z "$pkg" ] || [[ "$pkg" == \#* ]] && continue
        local pkg_under=$(echo "$pkg" | tr '-' '_')
        local ver=$(_pkg_version "$pkg_under")
        [ -z "$ver" ] && continue  # not installed, nothing to protect
        # Copy package dir + dist-info + .libs
        for d in "$_SITE_PKGS/${pkg_under}" \
                 "$_SITE_PKGS/${pkg_under}-${ver}.dist-info" \
                 "$_SITE_PKGS/${pkg_under}.libs"; do
            [ -e "$d" ] && cp -a "$d" "$_CUSTOM_BACKUP/"
        done
        echo "$pkg_under=$ver" >> "$_CUSTOM_BACKUP/_versions"
        n=$((n + 1))
    done < "$_CUSTOM_LIST"
    [ "$n" -gt 0 ] && echo "[sync] Backed up $n custom package(s)"
}

_restore_if_downgraded() {
    [ -f "$_CUSTOM_BACKUP/_versions" ] || return
    local restored=0 kept=0
    while IFS='=' read -r pkg_under old_ver || [ -n "$pkg_under" ]; do
        [ -z "$pkg_under" ] && continue
        [ -z "$old_ver" ] && continue
        local new_ver
        new_ver=$(_pkg_version "$pkg_under" "$_SITE_PKGS")
        local action=""
        if [ -z "$new_ver" ]; then
            action="removed → restore"
        elif _version_ge "$new_ver" "$old_ver"; then
            action="upgraded ${old_ver}→${new_ver} → keep"
            kept=$((kept + 1))
            continue
        else
            action="downgraded ${old_ver}→${new_ver} → restore"
        fi
        echo "[sync] $pkg_under: $action"
        # Remove whatever uv put in
        rm -rf "$_SITE_PKGS/${pkg_under}" \
               "$_SITE_PKGS/${pkg_under}-"*.dist-info \
               "$_SITE_PKGS/${pkg_under}.libs"
        # Restore backup
        for d in "$_CUSTOM_BACKUP/${pkg_under}" \
                 "$_CUSTOM_BACKUP/${pkg_under}-${old_ver}.dist-info" \
                 "$_CUSTOM_BACKUP/${pkg_under}.libs"; do
            [ -e "$d" ] && cp -a "$d" "$_SITE_PKGS/"
        done
        restored=$((restored + 1))
    done < "$_CUSTOM_BACKUP/_versions"
    rm -rf "$_CUSTOM_BACKUP"
    [ "$restored" -gt 0 ] && echo "[sync] Restored $restored custom package(s)"
    [ "$kept" -gt 0 ] && echo "[sync] Kept $kept uv-upgraded package(s)"
}

if [ "$SKIP_SYNC" = true ]; then
    echo "[sync] Skipped (--skip-sync) — using existing venv as-is"
else
    # Dry-run: check if uv sync would change custom packages
    _backup_custom
    if [ -f "$_CUSTOM_BACKUP/_versions" ]; then
        # Check what uv wants to do (dry-run)
        _dry_output=$(uv sync --inexact --dry-run 2>&1 || true)
        _would_change=false
        while IFS='=' read -r _pkg _ver || [ -n "$_pkg" ]; do
            [ -z "$_pkg" ] && continue
            # Convert underscores to hyphens for pip/uv naming
            _pkg_hyph=$(echo "$_pkg" | tr '_' '-')
            if echo "$_dry_output" | grep -qi "$_pkg_hyph"; then
                _would_change=true
                break
            fi
        done < "$_CUSTOM_BACKUP/_versions"
        if [ "$_would_change" = true ]; then
            echo ""
            echo "[WARN] uv sync would modify custom-compiled packages:"
            while IFS='=' read -r _pkg _ver || [ -n "$_pkg" ]; do
                [ -z "$_pkg" ] && continue
                _pkg_hyph=$(echo "$_pkg" | tr '_' '-')
                echo "$_dry_output" | grep -i "$_pkg_hyph" | sed 's/^/         /'
            done < "$_CUSTOM_BACKUP/_versions"
            echo ""
            read -r -p "Proceed with uv sync? (backup/restore will protect custom builds) [y/N] " _reply
            if [[ ! "$_reply" =~ ^[Yy]$ ]]; then
                echo "[sync] Aborted — using existing venv. Use --skip-sync to skip permanently."
                rm -rf "$_CUSTOM_BACKUP"
            else
                uv sync --inexact --quiet
                _restore_if_downgraded
            fi
        else
            # No custom packages affected, just sync
            uv sync --inexact --quiet
            rm -rf "$_CUSTOM_BACKUP"
        fi
    else
        # No custom packages to protect
        uv sync --inexact --quiet
    fi
fi

# Reinstall llama-cpp-python if uv sync removed it or if GPU backend doesn't match
if [ "$SKIP_LLAMA" = false ]; then
    _LLAMA_OK=false
    if $PYTHON -c "import llama_cpp" 2>/dev/null; then
        LLAMA_VER=$($PYTHON -c "import llama_cpp; print(llama_cpp.__version__)" 2>/dev/null || echo "unknown")
        _SO_DIR=$($PYTHON -c "import llama_cpp, os; print(os.path.dirname(llama_cpp.__file__))" 2>/dev/null || echo "")
        _LIB_DIR="${_SO_DIR}/lib"
        [ ! -d "$_LIB_DIR" ] && _LIB_DIR="$_SO_DIR"
        # Check for backend-specific shared libs (.so on Linux, .dylib on macOS).
        # Must check BOTH extensions — macOS uses .dylib, Linux uses .so.
        _has_lib() { [ -f "$_LIB_DIR/$1.so" ] || [ -f "$_LIB_DIR/$1.dylib" ]; }
        case "$GPU_BACKEND" in
            metal)  _has_lib libggml-metal  && _LLAMA_OK=true ;;
            cuda)   _has_lib libggml-cuda   && _LLAMA_OK=true ;;
            vulkan) _has_lib libggml-vulkan && _LLAMA_OK=true ;;
            rocm)   _has_lib libggml-cuda   && _LLAMA_OK=true ;;
            *)      _LLAMA_OK=true ;;  # CPU — any build is fine
        esac
        if [ "$_LLAMA_OK" = true ]; then
            echo "[ok] llama-cpp-python $LLAMA_VER (${GPU_BACKEND} support verified)"
        else
            echo ""
            echo "[WARN] llama-cpp-python $LLAMA_VER is installed but LACKS ${GPU_BACKEND} support."
            echo "       The benchmark will run on CPU only unless you reinstall."
            echo ""
            read -r -p "Reinstall llama-cpp-python with ${GPU_BACKEND} support? [y/N] " _REPLY
            [[ "$_REPLY" =~ ^[Yy]$ ]] || { echo "[skip] Keeping current build — benchmark will use CPU"; _LLAMA_OK=true; }
        fi
    fi
    if [ "$_LLAMA_OK" = false ]; then
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
            vulkan)
                echo "[setup] Building llama-cpp-python from source (Vulkan)..."
                CMAKE_ARGS="-DGGML_VULKAN=ON" uv pip install \
                    "llama-cpp-python>=0.3.0" --no-binary llama-cpp-python --no-cache-dir --quiet
                ;;
            *)
                echo "[setup] Installing llama-cpp-python (CPU wheel)..."
                UV_EXTRA_INDEX_URL="https://abetlen.github.io/llama-cpp-python/whl/cpu" \
                    uv pip install "llama-cpp-python>=0.3.0" --quiet
                ;;
        esac
    fi
fi
echo "[ok] Python env ready"

# ── Detect available RAM and GPU memory for model size filtering ─────────
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
                MAX_MODEL_MB=$(( SYSTEM_RAM_MB * 3 / 4 ))
                echo "[check] System RAM: $((SYSTEM_RAM_MB / 1024)) GB → max model: $((MAX_MODEL_MB / 1024)) GB (VRAM unknown)"
            fi
            ;;
        rocm)
            # AMD ROCm: use VRAM as the bottleneck (like NVIDIA/cuda)
            _VRAM_MB=$(rocm-smi --showmeminfo vram --csv 2>/dev/null \
                       | awk -F, 'NR>1 && $1+0==$1 {print int($2/1048576); exit}')
            if [ -z "$_VRAM_MB" ] || [ "$_VRAM_MB" -le 0 ] 2>/dev/null; then
                # Fallback: try rocm-smi --showmeminfo vram (non-CSV, MiB lines)
                _VRAM_MB=$(rocm-smi --showmeminfo vram 2>/dev/null \
                           | grep -i 'total' | grep -oE '[0-9]+' | head -1)
            fi
            if [ -n "$_VRAM_MB" ] && [ "$_VRAM_MB" -gt 0 ] 2>/dev/null; then
                MAX_MODEL_MB=$_VRAM_MB
                echo "[check] System RAM: $((SYSTEM_RAM_MB / 1024)) GB, GPU VRAM: $((_VRAM_MB / 1024)) GB → max model: $((MAX_MODEL_MB / 1024)) GB"
            else
                MAX_MODEL_MB=$(( SYSTEM_RAM_MB * 3 / 4 ))
                echo "[check] System RAM: $((SYSTEM_RAM_MB / 1024)) GB → max model: $((MAX_MODEL_MB / 1024)) GB (ROCm VRAM unknown)"
            fi
            ;;
        vulkan)
            # AMD Vulkan: VRAM = first memoryHeaps[0] size (DEVICE_LOCAL)
            # Format: "size   = 8321499136 (0x...) (7.75 GiB)"
            _VRAM_BYTES=$(vulkaninfo 2>/dev/null \
                       | awk '/memoryHeaps\[0\]:/{found=1; next} found && /size/{print $3; exit}' \
                       || true)
            if [ -n "$_VRAM_BYTES" ] && [ "$_VRAM_BYTES" -gt 0 ] 2>/dev/null; then
                _VRAM_MB=$(( _VRAM_BYTES / 1048576 ))
            else
                _VRAM_MB=""
            fi
            if [ -n "$_VRAM_MB" ] && [ "$_VRAM_MB" -gt 0 ] 2>/dev/null; then
                MAX_MODEL_MB=$_VRAM_MB
                echo "[check] System RAM: $((SYSTEM_RAM_MB / 1024)) GB, GPU VRAM: $((_VRAM_MB / 1024)) GB → max model: $((MAX_MODEL_MB / 1024)) GB"
            else
                MAX_MODEL_MB=$(( SYSTEM_RAM_MB * 3 / 4 ))
                echo "[check] System RAM: $((SYSTEM_RAM_MB / 1024)) GB → max model: $((MAX_MODEL_MB / 1024)) GB (Vulkan VRAM unknown)"
            fi
            ;;
        *)
            # CPU-only: conservative RAM/2 heuristic
            MAX_MODEL_MB=$(( SYSTEM_RAM_MB * 3 / 4 ))
            [ "$SYSTEM_RAM_MB" -gt 0 ] && \
                echo "[check] System RAM: $((SYSTEM_RAM_MB / 1024)) GB → max model: $((MAX_MODEL_MB / 1024)) GB"
            ;;
    esac

# ── Step 3a: llama.cpp — download missing GGUF ───────────────────────────
if [ "$SKIP_LLAMA" = false ]; then
    echo ""
    echo "── [3a/4] llama.cpp setup — checking GGUF models..."
    mkdir -p "$MODELS_DIR"

    # _hf_download <repo_id> <filename> --local-dir <dir>
    # NOTE: uses $PYTHON (direct .venv/bin/python) instead of "uv run python".
    # "uv run" triggers an implicit "uv sync" that replaces the GPU-compiled
    # llama-cpp-python wheel with the CPU-only one from the lockfile.
    # On macOS this was invisible (PyPI wheel already includes Metal), but on
    # Linux with CUDA/Vulkan/ROCm it silently killed GPU acceleration.
    _hf_download() {
        local repo_id="$1" filename="$2" local_dir="$4"
        $PYTHON - "$repo_id" "$filename" "$local_dir" <<'PYEOF'
import sys, os, requests
from pathlib import Path
from tqdm import tqdm

repo_id, filename, local_dir = sys.argv[1], sys.argv[2], sys.argv[3]
dest = Path(local_dir) / filename

# Se già scaricato salta
if dest.exists() and dest.stat().st_size > 0:
    print(f"  → {dest} (già presente)")
    sys.exit(0)

token = os.environ.get("HF_TOKEN", "")
headers = {"Authorization": f"Bearer {token}"} if token else {}
url = f"https://huggingface.co/{repo_id}/resolve/main/{filename}"

r = requests.get(url, stream=True, headers=headers, timeout=30)
r.raise_for_status()
total = int(r.headers.get("content-length", 0))

dest.parent.mkdir(parents=True, exist_ok=True)
tmp = dest.with_suffix(".tmp")
with open(tmp, "wb") as f, tqdm(
    total=total, unit="B", unit_scale=True, unit_divisor=1024,
    desc=filename[-40:], file=sys.stderr, dynamic_ncols=True,
) as bar:
    for chunk in r.iter_content(chunk_size=1024 * 1024):
        f.write(chunk)
        bar.update(len(chunk))
tmp.rename(dest)
print(f"  → {dest}")
PYEOF
    }

    # No bulk download — models are downloaded just-in-time in the run loop (step 4).
    # This avoids saturating disk on machines with limited space.
    GGUF_TOTAL=$(ls -1 "$MODELS_DIR"/*.gguf 2>/dev/null | wc -l | tr -d ' ')
    echo "[ok] $GGUF_TOTAL GGUF models already in $MODELS_DIR (others will be downloaded on demand)"
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

# ── Step 3d: vLLM offline — detect importability ─────────────────────────
VLLM_OFFLINE_MODELS_COUNT=0
if [ "$SKIP_VLLM_OFFLINE" = false ]; then
    echo ""
    echo "── [3d/5] vLLM offline — checking availability..."
    if $PYTHON -c "import vllm; import torch; assert torch.cuda.is_available()" 2>/dev/null; then
        # Count rows with vllm_model column non-empty
        VLLM_OFFLINE_MODELS_COUNT=$(tail -n +2 "$MODELS_CSV" | grep -v '^#' | grep -v '^[[:space:]]*$' | \
            awk -F',' -v hdr="$(head -1 "$MODELS_CSV")" '
            BEGIN {
                n=split(hdr,cols,",")
                for(i=1;i<=n;i++) if(cols[i]=="vllm_model") idx=i
            }
            { if(idx && $idx != "" && $(NF-1)=="true") c++ }
            END { print c+0 }' || echo "0")
        echo "[ok] vllm importable + CUDA available — $VLLM_OFFLINE_MODELS_COUNT models with vllm_model set"
        [ "$VLLM_OFFLINE_MODELS_COUNT" -eq 0 ] && {
            echo "[warn] nessun modello con colonna vllm_model in $MODELS_CSV — skipping"
            SKIP_VLLM_OFFLINE=true
        }
    else
        if ! $PYTHON -c "import vllm" 2>/dev/null; then
            echo "[skip] vllm non installato (pip install vllm — solo Linux/CUDA)"
        else
            echo "[skip] vllm installato ma CUDA non disponibile"
        fi
        SKIP_VLLM_OFFLINE=true
    fi
else
    echo ""
    echo "── [3d/5] vLLM offline — skipped (--skip-vllm-offline)"
fi

# ── Setup summary ─────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════════"
echo "  SETUP SUMMARY"
USE_LLAMA=false
USE_OLLAMA=false
USE_VLLM=false
USE_VLLM_OFFLINE=false
# --skip-llama skips only the install/download step, not the backend itself
ls "$MODELS_DIR"/*.gguf 2>/dev/null | grep -q . && USE_LLAMA=true
[ "$SKIP_OLLAMA" = false ] && USE_OLLAMA=true
[ "$SKIP_VLLM" = false ] && [ -n "$VLLM_MODEL" ] && USE_VLLM=true
[ "$SKIP_VLLM_OFFLINE" = false ] && [ "$VLLM_OFFLINE_MODELS_COUNT" -gt 0 ] && USE_VLLM_OFFLINE=true

[ "$USE_LLAMA"        = true ] && echo "  llama.cpp    : enabled" || echo "  llama.cpp    : DISABLED"
[ "$USE_OLLAMA"       = true ] && echo "  Ollama       : enabled" || echo "  Ollama       : DISABLED"
[ "$USE_VLLM"         = true ] && echo "  vLLM         : enabled ($VLLM_MODEL)" || echo "  vLLM         : DISABLED"
[ "$USE_VLLM_OFFLINE" = true ] && echo "  vLLM offline : enabled ($VLLM_OFFLINE_MODELS_COUNT models)" || echo "  vLLM offline : DISABLED"
echo "  GPU        : $GPU_LABEL"
echo "════════════════════════════════════════════════════════════"

if [ "$SETUP_ONLY" = true ]; then
    echo ""
    echo "  Setup complete (--setup-only). To run benchmarks omit the flag."
    exit 0
fi

if [ "$USE_LLAMA" = false ] && [ "$USE_OLLAMA" = false ] && [ "$USE_VLLM" = false ] && [ "$USE_VLLM_OFFLINE" = false ]; then
    echo "ERROR: No active backends. Aborting."
    exit 1
fi

# ── Run helper ────────────────────────────────────────────────────────────
MIN_CTX=4096
STEP=0

run_phase() {
    local phase="$1" label="$2"; shift 2
    local script
    [ "$phase" = "classifier" ]  && script="benchmark/benchmark_classifier.py"
    [ "$phase" = "categorizer" ] && script="benchmark/benchmark_categorizer.py"
    STEP=$((STEP + 1))
    echo ""
    echo "────────────────────────────────────────────────────────────"
    echo "  [step $STEP] [$phase] $label"
    echo "────────────────────────────────────────────────────────────"
    # --scenario and --country are categorizer-only args; don't pass to classifier
    local SCENARIO_ARGS=()
    if [ "$phase" = "categorizer" ]; then
        [ -n "$SCENARIO" ] && SCENARIO_ARGS+=(--scenario "$SCENARIO")
        [ -n "$COUNTRY"  ] && SCENARIO_ARGS+=(--country "$COUNTRY")
    fi
    local MAX_FILES_ARGS=()
    [ -n "$MAX_FILES" ] && MAX_FILES_ARGS+=(--max-files "$MAX_FILES")
    $PYTHON "$script" --runs "$RUNS" "$@" \
        ${MAX_FILES_ARGS[@]+"${MAX_FILES_ARGS[@]}"} \
        ${SCENARIO_ARGS[@]+"${SCENARIO_ARGS[@]}"} \
        ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} || \
        echo "  [WARN] $label [$phase] failed — skipping"
}

run_both() {
    local label="$1"; shift
    if [ "$BENCHMARK" = "classifier" ] || [ "$BENCHMARK" = "both" ]; then
        run_phase classifier "$label" "$@"
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

    _MIN_DISK_MB=16384  # 16 GB minimum free space

    while IFS= read -r row; do
        gguf_file=$(_csv_field "$row" "$CSV_HEADER" "gguf_file")
        gguf_repo=$(_csv_field "$row" "$CSV_HEADER" "gguf_repo")
        name=$(_csv_field "$row" "$CSV_HEADER" "name")
        size_mb=$(_csv_field "$row" "$CSV_HEADER" "size_mb")
        [ -z "$gguf_file" ] && continue
        gguf="$MODELS_DIR/$gguf_file"

        # Skip models too large for available RAM
        if [ -n "$size_mb" ] && [ "$MAX_MODEL_MB" -gt 0 ] && [ "$size_mb" -gt "$MAX_MODEL_MB" ]; then
            echo "  [SKIP] $name — model ${size_mb}MB exceeds RAM limit (${MAX_MODEL_MB}MB)"
            continue
        fi

        # ── Just-in-time download ─────────────────────────────────
        if [ ! -f "$gguf" ]; then
            # Check disk space before download: need 16 GB + model size
            _free_mb=$(df -m "$MODELS_DIR" | awk 'NR==2 {print $4}')
            _need_mb=$(( _MIN_DISK_MB + ${size_mb:-0} ))
            if [ -n "$_free_mb" ] && [ "$_free_mb" -lt "$_need_mb" ] 2>/dev/null; then
                echo "  [SKIP] $name — not enough disk space (${_free_mb}MB free, need ${_need_mb}MB)"
                continue
            fi
            echo "[download] $name ($gguf_file from $gguf_repo)"
            if ! _hf_download "$gguf_repo" "$gguf_file" --local-dir "$MODELS_DIR"; then
                echo "  [WARN] Failed to download $gguf_file — skipping"
                continue
            fi
        fi

        # ── Context check ─────────────────────────────────────────
        n_ctx=$($PYTHON -c "
from core.llm_backends import LlamaCppBackend
ctx = LlamaCppBackend.read_gguf_context_length('$gguf')
print(ctx or 0)
" 2>/dev/null || echo "0")
        if [ "$n_ctx" -gt 0 ] && [ "$n_ctx" -lt "$MIN_CTX" ]; then
            echo "  [SKIP] $name — n_ctx=$n_ctx < min=$MIN_CTX"
            continue
        fi

        # ── Run benchmark ─────────────────────────────────────────
        run_both "llama.cpp: $name ($gguf_file)" \
            --backend local_llama_cpp --model-path "$gguf"

        # ── Post-run cleanup ──────────────────────────────────────
        delete_after=$(_csv_field "$row" "$CSV_HEADER" "delete_after")
        if [ "$delete_after" = "true" ] && [ -f "$gguf" ]; then
            echo "[delete] $name — removing $gguf_file (delete_after=true)"
            rm -f "$gguf"
        fi

        # Auto-cleanup: delete model if disk space < 16 GB
        if [ -f "$gguf" ]; then
            _free_mb=$(df -m "$MODELS_DIR" | awk 'NR==2 {print $4}')
            if [ -n "$_free_mb" ] && [ "$_free_mb" -lt "$_MIN_DISK_MB" ] 2>/dev/null; then
                echo "[cleanup] Disk space low (${_free_mb}MB free < ${_MIN_DISK_MB}MB) — removing $gguf_file"
                rm -f "$gguf"
            fi
        fi
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

# ── vLLM (server) ─────────────────────────────────────────────────────────
if [ "$USE_VLLM" = true ]; then
    echo ""
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║  BACKEND: vLLM                                          ║"
    echo "╚══════════════════════════════════════════════════════════╝"
    run_both "vLLM: $VLLM_MODEL" \
        --backend vllm --model "$VLLM_MODEL" --base-url "$VLLM_URL"
fi

# ── vLLM offline (in-process, no server) ──────────────────────────────────
if [ "$USE_VLLM_OFFLINE" = true ]; then
    echo ""
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║  BACKEND: vLLM offline (in-process)                     ║"
    echo "╚══════════════════════════════════════════════════════════╝"

    while IFS= read -r row; do
        vllm_model=$(_csv_field "$row" "$CSV_HEADER" "vllm_model")
        name=$(_csv_field "$row" "$CSV_HEADER" "name")
        enabled=$(_csv_field "$row" "$CSV_HEADER" "enabled")
        [ -z "$vllm_model" ] && continue
        [ "$enabled" != "true" ] && { echo "  [SKIP] $name — disabled"; continue; }

        run_both "vLLM offline: $name ($vllm_model)" \
            --backend vllm_offline --model "$vllm_model"
    done < <(_read_models_csv)
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
