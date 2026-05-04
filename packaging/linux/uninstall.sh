#!/usr/bin/env bash
# =============================================================================
#  Spendif.ai — Linux Uninstaller (Ubuntu/Debian/Fedora/RHEL)
#  https://github.com/drake69/spendify
#
#  Removes all Spendif.ai components interactively.
#  No data is deleted without explicit confirmation.
#
#  Usage:
#    bash packaging/linux/uninstall.sh
#
#  For package-manager installs, prefer:
#    sudo apt remove spendifai     (Debian/Ubuntu)
#    sudo dnf remove spendifai     (Fedora/RHEL)
# =============================================================================

set -euo pipefail

_RED='\033[0;31m'; _YEL='\033[0;33m'; _GRN='\033[0;32m'
_CYA='\033[0;36m'; _BLD='\033[1m'; _RST='\033[0m'

info()  { echo -e "${_CYA}ℹ  $*${_RST}"; }
step()  { echo -e "${_CYA}${_BLD}▸  $*${_RST}"; }
ok()    { echo -e "${_GRN}✔  $*${_RST}"; }
warn()  { echo -e "${_YEL}⚠  $*${_RST}"; }

SPENDIFAI_HOME="$HOME/.spendifai"
INSTALL_PATH_FILE="$SPENDIFAI_HOME/install_path.txt"
DESKTOP_FILE="$HOME/.local/share/applications/spendifai.desktop"
SYSTEM_DESKTOP="/usr/share/applications/spendifai.desktop"

# Detect install dir
INSTALL_DIR="$HOME/.local/share/Spendif.ai"
if [[ -f "$INSTALL_PATH_FILE" ]]; then
  SAVED="$(cat "$INSTALL_PATH_FILE" | tr -d '[:space:]')"
  [[ -d "$SAVED" ]] && INSTALL_DIR="$SAVED"
fi

echo ""
echo -e "${_BLD}${_CYA}╔══════════════════════════════════════════════════════════════╗"
echo -e "║          Spendif.ai — Linux Uninstaller                      ║"
echo -e "╚══════════════════════════════════════════════════════════════╝${_RST}"
echo ""

confirm() {
  local msg="$1"
  read -p "$(echo -e "${_YEL}  ? ${_RST} $msg [y/N] ")" -n 1 -r
  echo
  [[ $REPLY =~ ^[yYsS]$ ]]
}

# ── 1. Check for package-manager install ─────────────────────────────────────
PKG_INSTALLED=false
if dpkg -l spendifai &>/dev/null 2>&1; then
  warn "Spendif.ai is installed via apt/dpkg."
  info "Use: sudo apt remove spendifai"
  info "For full cleanup: sudo apt remove spendifai && bash $0"
  PKG_INSTALLED=true
elif rpm -q spendifai &>/dev/null 2>&1; then
  warn "Spendif.ai is installed via dnf/rpm."
  info "Use: sudo dnf remove spendifai"
  info "For full cleanup: sudo dnf remove spendifai && bash $0"
  PKG_INSTALLED=true
fi

if [[ "$PKG_INSTALLED" == true ]]; then
  if ! confirm "Continue with manual cleanup anyway?"; then
    exit 0
  fi
fi

# ── 2. Kill running processes ────────────────────────────────────────────────
step "Checking for running processes..."
PIDS=$(pgrep -f "streamlit.*app.py" 2>/dev/null || true)
PIDS+=" $(pgrep -f "desktop.launcher" 2>/dev/null || true)"
PIDS=$(echo "$PIDS" | xargs)

if [[ -n "$PIDS" ]]; then
  warn "Found running processes: $PIDS"
  if confirm "Stop all Spendif.ai processes?"; then
    kill $PIDS 2>/dev/null || true
    sleep 1
    ok "Processes stopped"
  fi
else
  ok "No running processes"
fi

# ── 3. Remove .desktop launcher ─────────────────────────────────────────────
for df in "$DESKTOP_FILE" "$SYSTEM_DESKTOP"; do
  if [[ -f "$df" ]]; then
    rm -f "$df"
    ok "Removed: $df"
  fi
done
update-desktop-database "$HOME/.local/share/applications/" 2>/dev/null || true

# ── 4. Remove code directory ────────────────────────────────────────────────
# Check both script-install and package-install locations
for dir in "$INSTALL_DIR" "/opt/spendifai"; do
  if [[ -d "$dir" ]] && [[ -f "$dir/app.py" ]]; then
    step "Found code directory: $dir"
    VENV_INFO=""
    if [[ -d "$dir/.venv" ]]; then
      VENV_INFO=" (includes .venv: $(du -sh "$dir/.venv" 2>/dev/null | cut -f1))"
    fi
    if confirm "Remove code directory${VENV_INFO}?"; then
      if [[ "$dir" == /opt/* ]]; then
        sudo rm -rf "$dir"
      else
        rm -rf "$dir"
      fi
      ok "Code directory removed"
    fi
  fi
done

# ── 5. User data ────────────────────────────────────────────────────────────
if [[ -d "$SPENDIFAI_HOME" ]]; then
  DB_FILE="$SPENDIFAI_HOME/spendifai.db"
  DB_INFO=""
  if [[ -f "$DB_FILE" ]]; then
    DB_INFO=" (DB: $(du -sh "$DB_FILE" 2>/dev/null | cut -f1))"
  fi

  echo ""
  warn "User data directory: $SPENDIFAI_HOME${DB_INFO}"
  warn "This contains your transaction database, settings, and configuration."
  if confirm "Remove ALL user data (database, config)? THIS CANNOT BE UNDONE"; then
    MODELS_DIR="$SPENDIFAI_HOME/models"
    if [[ -d "$MODELS_DIR" ]] && [[ -n "$(ls -A "$MODELS_DIR" 2>/dev/null)" ]]; then
      MODEL_SIZE="$(du -sh "$MODELS_DIR" 2>/dev/null | cut -f1)"
      if confirm "Also remove downloaded AI models ($MODEL_SIZE)?"; then
        rm -rf "$SPENDIFAI_HOME"
        ok "All user data and models removed"
      else
        find "$SPENDIFAI_HOME" -mindepth 1 -maxdepth 1 ! -name "models" -exec rm -rf {} +
        ok "User data removed (models preserved in $MODELS_DIR)"
      fi
    else
      rm -rf "$SPENDIFAI_HOME"
      ok "User data removed"
    fi
  else
    info "User data preserved at $SPENDIFAI_HOME"
  fi
fi

# ── 6. System icon ──────────────────────────────────────────────────────────
ICON="/usr/share/icons/hicolor/256x256/apps/spendifai.png"
if [[ -f "$ICON" ]]; then
  sudo rm -f "$ICON" 2>/dev/null || true
  gtk-update-icon-cache -f -t /usr/share/icons/hicolor 2>/dev/null || true
  ok "System icon removed"
fi

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo -e "${_BLD}${_GRN}✔  Uninstall complete.${_RST}"
echo ""
