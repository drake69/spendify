#!/usr/bin/env bash
# =============================================================================
#  Spendif.ai — .deb package builder
#  https://github.com/drake69/spendify
#
#  Produces: build/spendifai_<version>_amd64.deb
#
#  DESIGN CHOICES:
#
#  • WHY a "repo + postinst" .deb instead of a fat PyInstaller bundle?
#    The app has ~40 Python dependencies (pandas, streamlit, llama-cpp, etc.)
#    totalling 500 MB+ when frozen. A repo-style .deb ships only the source
#    code (~5 MB), then postinst runs `uv sync` to install deps into a local
#    venv. This matches how VS Code, Signal, and other desktop apps package
#    for Linux: the .deb is a thin wrapper that bootstraps the real install.
#
#  • WHY /opt/spendifai?
#    FHS 3.0 designates /opt for "add-on application software packages" that
#    are self-contained and don't integrate into /usr. Spendif.ai has its own
#    venv, its own config, and its own data dir — /opt is the correct choice.
#
#  • WHY postinst and not preinst?
#    postinst runs after dpkg has unpacked all files into /opt/spendifai.
#    We need the code present to run `uv sync` (reads pyproject.toml).
#    postinst also creates the .desktop file, downloads the model, and
#    writes the .env — all of which need the code in place.
#
#  • WHY Depends: python3, git, curl (not uv)?
#    uv is not in any distro repo. postinst installs it via the official
#    bootstrap script (curl | sh). Declaring it as a Depends would make
#    the package uninstallable.
#
#  USAGE:
#    cd sw_artifacts
#    bash packaging/linux/build-deb.sh [--version X.Y.Z]
#
#  PREREQUISITES:
#    dpkg-deb (part of dpkg, pre-installed on all Debian/Ubuntu)
#    fakeroot (optional, for correct file ownership without sudo)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# ── Defaults ─────────────────────────────────────────────────────────────────
VERSION=""
ARCH="amd64"

# ── Parse args ───────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) VERSION="$2"; shift 2 ;;
    --arch)    ARCH="$2";    shift 2 ;;
    *)         echo "Unknown arg: $1"; exit 1 ;;
  esac
done

# Read version from VERSION file if not specified
if [[ -z "$VERSION" ]]; then
  if [[ -f "${REPO_ROOT}/VERSION" ]]; then
    VERSION="$(tr -d '[:space:]' < "${REPO_ROOT}/VERSION")"
  else
    VERSION="0.0.0"
  fi
fi

echo "▸ Building spendifai_${VERSION}_${ARCH}.deb"

# ── Build directory ──────────────────────────────────────────────────────────
BUILD_DIR="${REPO_ROOT}/build/deb"
PKG_ROOT="${BUILD_DIR}/spendifai_${VERSION}_${ARCH}"
INSTALL_ROOT="${PKG_ROOT}/opt/spendifai"

rm -rf "${PKG_ROOT}"
mkdir -p "${INSTALL_ROOT}"
mkdir -p "${PKG_ROOT}/DEBIAN"
mkdir -p "${PKG_ROOT}/usr/share/applications"
mkdir -p "${PKG_ROOT}/usr/share/icons/hicolor/256x256/apps"

# ── Copy application code ────────────────────────────────────────────────────
echo "▸ Copying application files..."

# Copy only the directories and files needed at runtime
APP_DIRS=(api config core db desktop nsi prompts reports services support ui)
for d in "${APP_DIRS[@]}"; do
  if [[ -d "${REPO_ROOT}/${d}" ]]; then
    cp -r "${REPO_ROOT}/${d}" "${INSTALL_ROOT}/${d}"
  fi
done

# Top-level files
for f in app.py pyproject.toml VERSION .env.example; do
  [[ -f "${REPO_ROOT}/${f}" ]] && cp "${REPO_ROOT}/${f}" "${INSTALL_ROOT}/${f}"
done

# uv.lock for reproducible installs
[[ -f "${REPO_ROOT}/uv.lock" ]] && cp "${REPO_ROOT}/uv.lock" "${INSTALL_ROOT}/uv.lock"

# Icon
ICON_SRC="${REPO_ROOT}/packaging/macos/spendifai_256.png"
if [[ -f "$ICON_SRC" ]]; then
  cp "$ICON_SRC" "${PKG_ROOT}/usr/share/icons/hicolor/256x256/apps/spendifai.png"
  cp "$ICON_SRC" "${INSTALL_ROOT}/spendifai.png"
fi

# Remove __pycache__ and .pyc
find "${INSTALL_ROOT}" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "${INSTALL_ROOT}" -name "*.pyc" -delete 2>/dev/null || true

echo "✔ Application files copied"

# ── DEBIAN/control ───────────────────────────────────────────────────────────
cat > "${PKG_ROOT}/DEBIAN/control" <<EOF
Package: spendifai
Version: ${VERSION}
Section: finance
Priority: optional
Architecture: ${ARCH}
Depends: python3 (>= 3.11), python3-venv, python3-dev, python3-gi, gir1.2-webkit2-4.1, git, curl, gcc, cmake, pkg-config
Installed-Size: $(du -sk "${INSTALL_ROOT}" | cut -f1)
Maintainer: Luigi Corsaro <lcorsaro69@gmail.com>
Homepage: https://github.com/drake69/spendify
Description: Personal finance manager with local AI categorisation
 Spendif.ai aggregates bank statements (CSV/XLSX) into a unified ledger
 with automatic categorisation via local LLM (llama.cpp). Features include
 card-account reconciliation, internal transfer detection, budget tracking,
 and interactive analytics. Runs fully offline with no cloud dependency.
EOF

# ── DEBIAN/postinst ──────────────────────────────────────────────────────────
cat > "${PKG_ROOT}/DEBIAN/postinst" <<'POSTINST'
#!/bin/bash
# =============================================================================
#  Spendif.ai — post-installation script
#  Runs after dpkg unpacks files to /opt/spendifai
# =============================================================================
set -e

INSTALL_DIR="/opt/spendifai"
SPENDIFAI_HOME="$HOME/.spendifai"
ENV_FILE="$INSTALL_DIR/.env"

echo ""
echo "  Spendif.ai — post-install setup"
echo ""

# ── 1. Install uv if not present ────────────────────────────────────────────
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv &>/dev/null; then
  echo "  ▸ Installing uv (Python package manager)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
echo "  ✔ uv ready"

# ── 2. Create Python venv + install dependencies ────────────────────────────
echo "  ▸ Installing Python dependencies (may take a few minutes)..."
cd "$INSTALL_DIR"

# Detect NVIDIA GPU
CMAKE_EXTRA=""
if command -v nvidia-smi &>/dev/null; then
  GPU=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
  if [ -n "$GPU" ]; then
    echo "  ✔ NVIDIA GPU: $GPU"
    export CMAKE_ARGS="-DGGML_CUDA=on"
    export FORCE_CMAKE=1
  fi
fi

uv sync --extra desktop --quiet 2>&1 | tail -3 || {
  echo "  ⚠ GPU build failed — retrying CPU-only..."
  unset CMAKE_ARGS FORCE_CMAKE
  uv sync --extra desktop --quiet
}
echo "  ✔ Python environment ready"

# ── 3. Create data directory ────────────────────────────────────────────────
mkdir -p "$SPENDIFAI_HOME/models"
echo "$INSTALL_DIR" > "$SPENDIFAI_HOME/install_path.txt"

# ── 4. Create .env ──────────────────────────────────────────────────────────
if [ ! -f "$ENV_FILE" ]; then
  cat > "$ENV_FILE" <<EOF
SPENDIFAI_DB=sqlite:///$SPENDIFAI_HOME/spendifai.db
LLM_BACKEND=local_llama_cpp
EOF
fi

# ── 5. Download AI model ────────────────────────────────────────────────────
echo "  ▸ Downloading recommended AI model..."
MODEL_PATH=$(cd "$INSTALL_DIR" && uv run python -c "
import sys, os
sys.path.insert(0, '.')
from core.model_manager import ensure_model_available
path = ensure_model_available(progress_callback=lambda pct, msg: print(f'    {pct:.0%}  {msg}', flush=True))
print(path or '')
" 2>&1 | tail -1)

if [ -n "$MODEL_PATH" ] && [ "$MODEL_PATH" != "None" ]; then
  echo "  ✔ AI model: $(basename "$MODEL_PATH")"
  if grep -q "^LLAMA_CPP_MODEL_PATH=" "$ENV_FILE" 2>/dev/null; then
    sed -i "s|^LLAMA_CPP_MODEL_PATH=.*|LLAMA_CPP_MODEL_PATH=$MODEL_PATH|" "$ENV_FILE"
  else
    echo "LLAMA_CPP_MODEL_PATH=$MODEL_PATH" >> "$ENV_FILE"
  fi
else
  echo "  ⚠ Model download failed — will retry on first launch"
fi

# ── 6. Update icon cache ────────────────────────────────────────────────────
if command -v gtk-update-icon-cache &>/dev/null; then
  gtk-update-icon-cache -f -t /usr/share/icons/hicolor 2>/dev/null || true
fi
if command -v update-desktop-database &>/dev/null; then
  update-desktop-database /usr/share/applications 2>/dev/null || true
fi

echo ""
echo "  ✔ Spendif.ai installation complete!"
echo "    Launch: search 'Spendif' in Activities, or run:"
echo "    cd /opt/spendifai && uv run python -m desktop.launcher"
echo ""
POSTINST

chmod 0755 "${PKG_ROOT}/DEBIAN/postinst"

# ── DEBIAN/prerm ─────────────────────────────────────────────────────────────
cat > "${PKG_ROOT}/DEBIAN/prerm" <<'PRERM'
#!/bin/bash
# Clean up venv on uninstall (keep user data in ~/.spendifai)
set -e
rm -rf /opt/spendifai/.venv 2>/dev/null || true
echo "  Spendif.ai removed. User data preserved in ~/.spendifai/"
echo "  To remove all data: rm -rf ~/.spendifai"
PRERM

chmod 0755 "${PKG_ROOT}/DEBIAN/prerm"

# ── .desktop file ────────────────────────────────────────────────────────────
cat > "${PKG_ROOT}/usr/share/applications/spendifai.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=Spendif.ai
Comment=Personal finance manager with local AI categorisation
Exec=bash -c 'export PATH="$HOME/.local/bin:$PATH" && cd /opt/spendifai && uv run python -m desktop.launcher'
Icon=spendifai
Terminal=false
Categories=Office;Finance;
StartupNotify=true
StartupWMClass=spendifai
Keywords=finance;budget;bank;expense;
DESKTOP

# ── Set permissions ──────────────────────────────────────────────────────────
# /opt/spendifai needs to be writable for uv sync (creates .venv inside)
find "${INSTALL_ROOT}" -type f -exec chmod 644 {} +
find "${INSTALL_ROOT}" -type d -exec chmod 755 {} +
# Python files need exec for shebangs
find "${INSTALL_ROOT}" -name "*.py" -exec chmod 644 {} +
chmod 644 "${PKG_ROOT}/usr/share/applications/spendifai.desktop"

# ── Build .deb ───────────────────────────────────────────────────────────────
DEB_PATH="${REPO_ROOT}/build/spendifai_${VERSION}_${ARCH}.deb"

echo "▸ Building .deb package..."
if command -v fakeroot &>/dev/null; then
  fakeroot dpkg-deb --build "${PKG_ROOT}" "${DEB_PATH}"
else
  dpkg-deb --build "${PKG_ROOT}" "${DEB_PATH}"
fi

DEB_SIZE=$(du -h "${DEB_PATH}" | cut -f1)
echo "✔ Package built: ${DEB_PATH} (${DEB_SIZE})"
echo ""
echo "  Install:   sudo dpkg -i ${DEB_PATH}"
echo "  Or:        sudo apt install ./${DEB_PATH}"
echo "  Uninstall: sudo apt remove spendifai"
echo ""

# ── Cleanup ──────────────────────────────────────────────────────────────────
rm -rf "${PKG_ROOT}"
echo "✔ Build directory cleaned up"
