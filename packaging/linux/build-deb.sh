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
# Postinst runs as ROOT, with $HOME=/root. Anything user-specific (venv,
# model download, ~/.spendifai) belongs in a script that runs at FIRST
# USER LAUNCH instead — postinst here only installs uv system-wide so
# every desktop user can use it. The .desktop Exec line spawns the
# launch.sh wrapper which performs the per-user setup the first time.
cat > "${PKG_ROOT}/DEBIAN/postinst" <<'POSTINST'
#!/bin/bash
# =============================================================================
#  Spendif.ai — post-installation (root context, minimal)
#  Only system-wide setup. Per-user setup runs at first launch via launch.sh.
# =============================================================================
set -e

echo ""
echo "  Spendif.ai — post-install"
echo ""

# ── 1. System-wide uv install ───────────────────────────────────────────────
# Place uv in /usr/local/bin so EVERY user (not just root) has it on PATH.
# astral.sh/uv/install.sh respects UV_INSTALL_DIR.
if ! [ -x /usr/local/bin/uv ]; then
  echo "  ▸ Installing uv to /usr/local/bin..."
  TMP_UV_DIR=$(mktemp -d)
  curl -LsSf https://astral.sh/uv/install.sh | \
    env XDG_CONFIG_HOME=/tmp UV_INSTALL_DIR=/usr/local/bin sh -s -- --no-modify-path 2>&1 | tail -3
  # Fallback if the env-driven install ignored UV_INSTALL_DIR (older curl?):
  if ! [ -x /usr/local/bin/uv ] && [ -x /root/.local/bin/uv ]; then
    cp /root/.local/bin/uv /usr/local/bin/uv
    chmod 0755 /usr/local/bin/uv
  fi
  rm -rf "$TMP_UV_DIR"
fi
if [ -x /usr/local/bin/uv ]; then
  echo "  ✔ uv: $(/usr/local/bin/uv --version 2>&1 | head -1)"
else
  echo "  ⚠ uv install failed — user will be prompted to install on first launch."
fi

# ── 2. Refresh icon + desktop caches ────────────────────────────────────────
if command -v gtk-update-icon-cache &>/dev/null; then
  gtk-update-icon-cache -f -t /usr/share/icons/hicolor 2>/dev/null || true
fi
if command -v update-desktop-database &>/dev/null; then
  update-desktop-database /usr/share/applications 2>/dev/null || true
fi

echo ""
echo "  ✔ Spendif.ai installed."
echo "    On first launch the app will set up a per-user Python venv in"
echo "    ~/.spendifai/.venv and download the recommended AI model (~3 GB)."
echo "    Launch: search 'Spendif' in Activities, or run /opt/spendifai/launch.sh"
echo ""
POSTINST

chmod 0755 "${PKG_ROOT}/DEBIAN/postinst"

# ── DEBIAN/prerm ─────────────────────────────────────────────────────────────
# Per-user venv lives in ~/.spendifai/.venv (created by launch.sh on first
# run), not under /opt — so there is nothing for prerm to clean other than
# remnants of the old layout (≤ 0.1.0 used /opt/spendifai/.venv).
cat > "${PKG_ROOT}/DEBIAN/prerm" <<'PRERM'
#!/bin/bash
set -e
# Legacy layout cleanup (pre-launch.sh installs put the venv under /opt)
rm -rf /opt/spendifai/.venv 2>/dev/null || true
echo "  Spendif.ai removed."
echo "  Per-user data preserved in ~/.spendifai/ — wipe with:"
echo "    bash /opt/spendifai/cleanup.sh   (if available)  OR  rm -rf ~/.spendifai"
PRERM

chmod 0755 "${PKG_ROOT}/DEBIAN/prerm"

# ── /opt/spendifai/launch.sh — per-user first-launch + run wrapper ──────────
# Runs as the USER (invoked by the .desktop file via gnome-shell, not as root).
# On first launch: creates per-user venv in ~/.spendifai/.venv, syncs deps,
# then execs the pywebview launcher. On subsequent launches: just execs.
cat > "${INSTALL_ROOT}/launch.sh" <<'LAUNCH'
#!/bin/bash
# =============================================================================
#  Spendif.ai — user-space launcher (Linux)
#  Sets up ~/.spendifai/.venv on first run, then execs the pywebview launcher.
#  /opt/spendifai contains read-only source code; nothing user-specific lives
#  there. All per-user state goes in ~/.spendifai/.
# =============================================================================
set -eo pipefail        # pipefail so `... | tail` stops swallowing uv errors

APP_DIR="/opt/spendifai"
USER_HOME_DIR="$HOME/.spendifai"
VENV_DIR="$USER_HOME_DIR/.venv"
LOG_FILE="$USER_HOME_DIR/launch.log"

mkdir -p "$USER_HOME_DIR"
exec > >(tee -a "$LOG_FILE") 2>&1
echo "=== launch.sh $(date -Iseconds) ==="

# ── 1. Find uv ──────────────────────────────────────────────────────────────
UV=""
for candidate in /usr/local/bin/uv "$HOME/.local/bin/uv" /usr/bin/uv; do
  if [ -x "$candidate" ]; then UV="$candidate"; break; fi
done
if [ -z "$UV" ]; then
  echo "uv not found — installing in user home"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  UV="$HOME/.local/bin/uv"
fi
echo "uv: $UV"

# ── 2. Create user venv on first launch ─────────────────────────────────────
if [ ! -d "$VENV_DIR" ]; then
  echo "First launch — creating $VENV_DIR"
  echo "This compiles llama-cpp-python natively (3-8 min on arm64; faster on amd64)."

  # Detect NVIDIA GPU (best-effort, falls back silently)
  if command -v nvidia-smi &>/dev/null; then
    export CMAKE_ARGS="-DGGML_CUDA=on"
    export FORCE_CMAKE=1
  fi

  # Use the project pyproject.toml but point uv at a venv outside /opt.
  # We do NOT pass --quiet — silent compile feels like a hung script and the
  # `set -o pipefail` above would only help if we piped at all. Verbose stderr
  # makes uv errors visible in launch.log when something breaks.
  cd "$APP_DIR"
  if ! UV_PROJECT_ENVIRONMENT="$VENV_DIR" "$UV" sync --extra desktop; then
    echo "GPU build failed (or first attempt errored), retrying CPU-only..."
    unset CMAKE_ARGS FORCE_CMAKE
    rm -rf "$VENV_DIR"        # nuke a possibly-partial venv before retry
    UV_PROJECT_ENVIRONMENT="$VENV_DIR" "$UV" sync --extra desktop
  fi

  if [ ! -x "$VENV_DIR/bin/python" ]; then
    echo "FATAL: venv created but $VENV_DIR/bin/python is missing."
    echo "Check this log and ~/.spendifai/launch.log for uv errors."
    exit 1
  fi
  echo "venv ready: $VENV_DIR"
fi

# ── 3. Seed .env if missing (writable in USER_HOME, not in /opt) ────────────
ENV_FILE="$USER_HOME_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
  cat > "$ENV_FILE" <<EOF
SPENDIFAI_DB=sqlite:///$USER_HOME_DIR/ledger.db
LLM_BACKEND=local_llama_cpp
EOF
fi

# ── 4. Launch the pywebview app ─────────────────────────────────────────────
cd "$APP_DIR"
exec "$VENV_DIR/bin/python" -m desktop.launcher
LAUNCH
chmod 0755 "${INSTALL_ROOT}/launch.sh"

# ── .desktop file ────────────────────────────────────────────────────────────
cat > "${PKG_ROOT}/usr/share/applications/spendifai.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=Spendif.ai
Comment=Personal finance manager with local AI categorisation
Exec=/opt/spendifai/launch.sh
Icon=spendifai
Terminal=false
Categories=Office;Finance;
StartupNotify=true
StartupWMClass=spendifai
Keywords=finance;budget;bank;expense;
DESKTOP

# ── Set permissions ──────────────────────────────────────────────────────────
# /opt/spendifai is read-only source code; per-user venv lives in
# ~/.spendifai/.venv (created by launch.sh on first run).
find "${INSTALL_ROOT}" -type f -exec chmod 644 {} +
find "${INSTALL_ROOT}" -type d -exec chmod 755 {} +
# launch.sh MUST be executable — it's the .desktop file's Exec target.
# (The generic 0644 find above clobbers the chmod inside the heredoc.)
chmod 0755 "${INSTALL_ROOT}/launch.sh"
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
