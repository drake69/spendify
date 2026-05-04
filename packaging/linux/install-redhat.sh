#!/usr/bin/env bash
# =============================================================================
#  Spendif.ai — Red Hat / Fedora / CentOS Installer
#  https://github.com/drake69/spendify
#
#  Tested on: Fedora 39+, RHEL 9+, Rocky Linux 9+, AlmaLinux 9+
#
#  Uses dnf (Fedora/RHEL 9+) with yum fallback for CentOS 7/8.
#  Same architecture as the Debian installer (see install-debian.sh for
#  detailed design choices).
#
#  Usage:
#    bash install-redhat.sh [OPTIONS]
#
#  Options:
#    --branch BRANCH   Git branch (default: main)
#    --launch          Launch after install
#    --update          Update only (git pull + uv sync)
#    -h, --help        Show help
# =============================================================================

set -euo pipefail

# ── Colours ──────────────────────────────────────────────────────────────────
_RED='\033[0;31m'; _YEL='\033[0;33m'; _GRN='\033[0;32m'
_CYA='\033[0;36m'; _BLD='\033[1m'; _RST='\033[0m'

info()  { echo -e "${_CYA}ℹ  $*${_RST}"; }
step()  { echo -e "${_CYA}${_BLD}▸  $*${_RST}"; }
ok()    { echo -e "${_GRN}✔  $*${_RST}"; }
warn()  { echo -e "${_YEL}⚠  $*${_RST}"; }
die()   { echo -e "${_RED}✖  $*${_RST}" >&2; exit 1; }

# ── Defaults ─────────────────────────────────────────────────────────────────
INSTALL_DIR="$HOME/.local/share/Spendif.ai"
SPENDIFAI_HOME="$HOME/.spendifai"
REPO_URL="https://github.com/drake69/spendify.git"
BRANCH="main"
DO_LAUNCH=false
DO_UPDATE=false
MIN_PYTHON=3.11

# ── Detect package manager ──────────────────────────────────────────────────
if command -v dnf &>/dev/null; then
  PKG_MGR="dnf"
elif command -v yum &>/dev/null; then
  PKG_MGR="yum"
else
  die "Neither dnf nor yum found. This installer requires a Red Hat-based distro."
fi

# ── Argument parsing ─────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --branch)  BRANCH="${2:?'--branch requires a name'}"; shift 2 ;;
    --launch)  DO_LAUNCH=true; shift ;;
    --update)  DO_UPDATE=true; shift ;;
    -h|--help)
      echo "Usage: $(basename "$0") [--branch BRANCH] [--launch] [--update] [-h]"
      exit 0
      ;;
    *) die "Unknown option: $1" ;;
  esac
done

# ── Banner ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${_BLD}${_CYA}╔══════════════════════════════════════════════════════════════╗"
echo -e "║       Spendif.ai — Red Hat/Fedora Installer                  ║"
echo -e "╚══════════════════════════════════════════════════════════════╝${_RST}"
echo ""

# ── Update mode ──────────────────────────────────────────────────────────────
if [[ "$DO_UPDATE" == true ]]; then
  step "Update mode: git pull + uv sync"
  [[ -d "$INSTALL_DIR/.git" ]] || die "No git repo at $INSTALL_DIR — run a fresh install first."
  export PATH="$HOME/.local/bin:$PATH"
  cd "$INSTALL_DIR"
  git fetch --prune origin
  git checkout "$BRANCH"
  git pull --ff-only origin "$BRANCH"
  uv sync --extra desktop --quiet
  ok "Update complete."
  exit 0
fi

# ── Step 1: System packages ─────────────────────────────────────────────────
step "Installing system dependencies (may prompt for sudo)..."
sudo $PKG_MGR install -y -q \
  git \
  python3 \
  python3-devel \
  python3-pip \
  python3-gobject \
  webkit2gtk4.1 \
  gobject-introspection-devel \
  gcc \
  cmake \
  pkg-config \
  curl \
  || {
    # Fallback: some RHEL/CentOS versions use different package names
    warn "Some packages failed — trying alternative names..."
    sudo $PKG_MGR install -y -q \
      git python3 python3-devel python3-pip \
      python3-gobject webkit2gtk3 \
      gobject-introspection-devel gcc cmake curl
  }
ok "System packages installed"

# ── Step 2: Python version check ────────────────────────────────────────────
step "Checking Python version..."
PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJ=$(echo "$PY_VER" | cut -d. -f1)
PY_MIN=$(echo "$PY_VER" | cut -d. -f2)
MIN_MAJ=$(echo "$MIN_PYTHON" | cut -d. -f1)
MIN_MIN=$(echo "$MIN_PYTHON" | cut -d. -f2)

if [[ "$PY_MAJ" -lt "$MIN_MAJ" ]] || { [[ "$PY_MAJ" -eq "$MIN_MAJ" ]] && [[ "$PY_MIN" -lt "$MIN_MIN" ]]; }; then
  die "Python $PY_VER found, need >= $MIN_PYTHON. Install a newer version via dnf or pyenv."
fi
ok "Python $PY_VER"

# ── Step 3: uv ──────────────────────────────────────────────────────────────
step "Checking uv..."
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv &>/dev/null; then
  step "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
ok "uv $(uv --version)"

# ── Step 4: Clone / update code ─────────────────────────────────────────────
step "Installing code to $INSTALL_DIR..."
mkdir -p "$(dirname "$INSTALL_DIR")"
if [[ -d "$INSTALL_DIR/.git" ]]; then
  info "Existing installation found — updating..."
  cd "$INSTALL_DIR"
  git fetch --prune origin
  git checkout "$BRANCH"
  git pull --ff-only origin "$BRANCH" || warn "git pull failed — using existing code"
else
  git clone --branch "$BRANCH" --depth 1 "$REPO_URL" "$INSTALL_DIR"
fi
ok "Code ready at $INSTALL_DIR"

# ── Step 5: Data directory ──────────────────────────────────────────────────
step "Preparing data directory..."
mkdir -p "$SPENDIFAI_HOME/models"
echo "$INSTALL_DIR" > "$SPENDIFAI_HOME/install_path.txt"
ok "Data directory: $SPENDIFAI_HOME"

# ── Step 6: .env ─────────────────────────────────────────────────────────────
step "Checking .env..."
ENV_FILE="$INSTALL_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  cat > "$ENV_FILE" <<EOF
SPENDIFAI_DB=sqlite:///$SPENDIFAI_HOME/spendifai.db
LLM_BACKEND=local_llama_cpp
EOF
  ok ".env created"
else
  ok ".env already exists"
fi

# ── Step 7: Python dependencies ──────────────────────────────────────────────
step "Installing Python dependencies (this may take a few minutes)..."
cd "$INSTALL_DIR"

# Detect NVIDIA GPU for CUDA-enabled llama-cpp-python
if command -v nvidia-smi &>/dev/null; then
  GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
  if [[ -n "$GPU_NAME" ]]; then
    ok "NVIDIA GPU detected: $GPU_NAME"
    export CMAKE_ARGS="-DGGML_CUDA=on"
    export FORCE_CMAKE=1
  fi
fi

# Detect AMD ROCm
if command -v rocm-smi &>/dev/null; then
  ok "AMD ROCm detected"
  export CMAKE_ARGS="-DGGML_HIPBLAS=on"
  export FORCE_CMAKE=1
fi

uv sync --extra desktop --quiet 2>&1 | tail -5 || {
  warn "uv sync with GPU flags failed — retrying CPU-only..."
  unset CMAKE_ARGS FORCE_CMAKE
  uv sync --extra desktop --quiet
}
ok "Python environment ready"

# ── Step 8: Download AI model ────────────────────────────────────────────────
step "Downloading recommended AI model..."
info "One-time download (1-7 GB depending on RAM)."

MODEL_PATH=$(cd "$INSTALL_DIR" && uv run python -c "
import sys, os
sys.path.insert(0, '.')
from core.model_manager import ensure_model_available
path = ensure_model_available(progress_callback=lambda pct, msg: print(f'  {pct:.0%}  {msg}', flush=True))
print(path or '')
" 2>&1 | tail -1)

if [[ -n "$MODEL_PATH" && "$MODEL_PATH" != "None" ]]; then
  ok "AI model ready: $(basename "$MODEL_PATH")"
  if grep -q "^LLAMA_CPP_MODEL_PATH=" "$ENV_FILE" 2>/dev/null; then
    sed -i "s|^LLAMA_CPP_MODEL_PATH=.*|LLAMA_CPP_MODEL_PATH=$MODEL_PATH|" "$ENV_FILE"
  else
    echo "LLAMA_CPP_MODEL_PATH=$MODEL_PATH" >> "$ENV_FILE"
  fi
  ok "llama.cpp configured as default backend"
else
  warn "Model download failed — the app will retry on first launch"
fi

# ── Step 9: Create .desktop launcher ─────────────────────────────────────────
step "Creating desktop launcher..."
DESKTOP_FILE="$HOME/.local/share/applications/spendifai.desktop"
mkdir -p "$(dirname "$DESKTOP_FILE")"

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Spendif.ai
Comment=Personal finance manager with local AI
Exec=bash -c 'export PATH="\$HOME/.local/bin:\$PATH" && cd $INSTALL_DIR && uv run python -m desktop.launcher'
Icon=$INSTALL_DIR/packaging/macos/spendifai_256.png
Terminal=false
Categories=Office;Finance;
StartupNotify=true
StartupWMClass=spendifai
EOF

update-desktop-database "$HOME/.local/share/applications/" 2>/dev/null || true
ok "Desktop launcher created: $DESKTOP_FILE"

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo -e "${_BLD}${_GRN}╔══════════════════════════════════════════════════════════════╗"
echo -e "║            ✔  Installation complete!                         ║"
echo -e "╚══════════════════════════════════════════════════════════════╝${_RST}"
echo ""
echo -e "  ${_BLD}Code:${_RST}      $INSTALL_DIR"
echo -e "  ${_BLD}Data:${_RST}      $SPENDIFAI_HOME"
echo -e "  ${_BLD}Launch:${_RST}    Search 'Spendif' in Activities, or:"
echo -e "             ${_CYA}cd $INSTALL_DIR && uv run python -m desktop.launcher${_RST}"
echo ""

if [[ "$DO_LAUNCH" == true ]]; then
  step "Launching Spendif.ai..."
  cd "$INSTALL_DIR" && uv run python -m desktop.launcher &
fi
