#!/usr/bin/env bash
# =============================================================================
#  Spendif.ai — macOS Uninstaller
#  https://github.com/drake69/spendify
#
#  Removes all Spendif.ai components interactively.
#  No data is deleted without explicit confirmation.
#
#  Usage:
#    bash packaging/macos/uninstall.sh
#
#  Also invoked by: brew uninstall --cask spendifai (via cask uninstall stanza)
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

# Possible install locations
INSTALL_CANDIDATES=(
  "$HOME/Applications/Spendif.ai"
  "/opt/spendifai"
)

# Read actual install dir if available
if [[ -f "$INSTALL_PATH_FILE" ]]; then
  SAVED_DIR="$(cat "$INSTALL_PATH_FILE" | tr -d '[:space:]')"
  INSTALL_CANDIDATES=("$SAVED_DIR" "${INSTALL_CANDIDATES[@]}")
fi

APP_BUNDLE="/Applications/Spendif.ai.app"
DESKTOP_FILE="$HOME/.local/share/applications/spendifai.desktop"

echo ""
echo -e "${_BLD}${_CYA}╔══════════════════════════════════════════════════════════════╗"
echo -e "║          Spendif.ai — macOS Uninstaller                      ║"
echo -e "╚══════════════════════════════════════════════════════════════╝${_RST}"
echo ""

confirm() {
  local msg="$1"
  read -p "$(echo -e "${_YEL}  ? ${_RST} $msg [y/N] ")" -n 1 -r
  echo
  [[ $REPLY =~ ^[yYsS]$ ]]
}

# ── 1. Kill running processes ────────────────────────────────────────────────
step "Checking for running Spendif.ai processes..."
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

# ── 2. Remove .app bundle ───────────────────────────────────────────────────
if [[ -d "$APP_BUNDLE" ]]; then
  step "Found app bundle: $APP_BUNDLE"
  if confirm "Remove $APP_BUNDLE?"; then
    rm -rf "$APP_BUNDLE"
    ok "App bundle removed"
  fi
fi

# ── 3. Remove code directory ────────────────────────────────────────────────
for dir in "${INSTALL_CANDIDATES[@]}"; do
  if [[ -d "$dir" ]] && [[ -f "$dir/app.py" ]]; then
    step "Found code directory: $dir"
    VENV_SIZE=""
    if [[ -d "$dir/.venv" ]]; then
      VENV_SIZE=" (includes .venv: $(du -sh "$dir/.venv" 2>/dev/null | cut -f1))"
    fi
    if confirm "Remove code directory${VENV_SIZE}?"; then
      rm -rf "$dir"
      ok "Code directory removed"
    fi
    break
  fi
done

# ── 4. Remove Spotlight/Launchpad references ────────────────────────────────
if [[ -f "$DESKTOP_FILE" ]]; then
  rm -f "$DESKTOP_FILE"
  ok "Desktop launcher removed"
fi

# ── 5. User data (DB + config) ──────────────────────────────────────────────
if [[ -d "$SPENDIFAI_HOME" ]]; then
  DB_FILE="$SPENDIFAI_HOME/spendifai.db"
  DB_SIZE=""
  if [[ -f "$DB_FILE" ]]; then
    DB_SIZE=" (DB: $(du -sh "$DB_FILE" 2>/dev/null | cut -f1))"
  fi

  echo ""
  warn "User data directory: $SPENDIFAI_HOME${DB_SIZE}"
  warn "This contains your transaction database, settings, and configuration."
  if confirm "Remove ALL user data (database, config)? THIS CANNOT BE UNDONE"; then
    # Models are asked separately (they're big)
    MODELS_DIR="$SPENDIFAI_HOME/models"
    if [[ -d "$MODELS_DIR" ]] && [[ -n "$(ls -A "$MODELS_DIR" 2>/dev/null)" ]]; then
      MODEL_SIZE="$(du -sh "$MODELS_DIR" 2>/dev/null | cut -f1)"
      if confirm "Also remove downloaded AI models ($MODEL_SIZE)?"; then
        rm -rf "$SPENDIFAI_HOME"
        ok "All user data and models removed"
      else
        # Remove everything except models
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

# ── 6. Homebrew cask (informational) ────────────────────────────────────────
if command -v brew &>/dev/null; then
  if brew list --cask spendifai &>/dev/null 2>&1; then
    echo ""
    info "Spendif.ai is also installed via Homebrew."
    info "Run: brew uninstall --cask spendifai"
    info "For full cleanup: brew uninstall --cask --zap spendifai"
  fi
fi

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo -e "${_BLD}${_GRN}✔  Uninstall complete.${_RST}"
echo ""
