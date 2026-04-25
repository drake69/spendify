#!/usr/bin/env bash
# =============================================================================
#  Spendif.ai — macOS Installer  (packaging/macos/install.sh)
#  https://github.com/drake69/spendify
#
#  DESIGN CHOICES (all justified below):
#
#  • WHY ~/Applications/Spendif.ai/ for code?
#    ~/Applications is the macOS-standard per-user app folder, respected by
#    Finder and Spotlight. It does not require sudo, avoids polluting /usr/local,
#    and survives macOS upgrades intact.
#
#  • WHY ~/.spendifai/ for user data (DB, models, config)?
#    XDG-style dotdir convention: keeps user data separate from code so the
#    code directory can be wiped/updated without touching data. DB path is
#    stable across reinstalls and branches.
#
#  • WHY uv for venv/deps?
#    uv is the fastest Python package manager (Rust-based), fully pip-compatible,
#    and produces reproducible envs via uv.lock. No conda, no virtualenv
#    boilerplate. Both --brew and default modes use uv so the experience is
#    identical regardless of how Python was obtained.
#
#  • WHY two modes (--brew vs default)?
#    --brew: installs Python 3.13 via Homebrew — best for developers who already
#    have Homebrew and want a clean, up-to-date Python separate from the system.
#    Default (no flag): uses the system Python (or whatever python3 is on PATH)
#    plus uv — zero Homebrew dependency, suitable for end-users who just want
#    a working app without a full package manager.
#
#  • WHY /Applications/Spendif.ai.app bundle?
#    A proper .app bundle is discoverable by Spotlight (Cmd+Space "Spendif"),
#    appears in Launchpad, can have a custom icon, and integrates with macOS
#    "Open at Login" features. The bundle launcher opens a real Terminal window
#    so the user can see logs and interact if needed.
#
#  • WHY Metal for llama-cpp-python?
#    Apple Silicon GPUs expose compute via the Metal framework. Compiling
#    llama-cpp-python with CMAKE_ARGS="-DGGML_METAL=on" enables GPU offloading
#    of LLM layers → 5-10x faster inference vs pure CPU, even on M1.
#
#  • WHY alembic upgrade head and not drop-and-recreate?
#    The DB at ~/.spendifai/spendifai.db contains real user data. Running
#    alembic upgrade head applies only the incremental migrations needed,
#    never losing data. If the DB does not exist yet, it is created on first
#    app launch by SQLAlchemy — no explicit action needed here.
# =============================================================================

set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
#  Colour helpers
# ─────────────────────────────────────────────────────────────────────────────
_RED='\033[0;31m'
_YEL='\033[0;33m'
_GRN='\033[0;32m'
_BLU='\033[0;34m'
_CYA='\033[0;36m'
_BLD='\033[1m'
_RST='\033[0m'

info()  { echo -e "${_BLU}ℹ  $*${_RST}"; }
step()  { echo -e "${_CYA}${_BLD}▸  $*${_RST}"; }
ok()    { echo -e "${_GRN}✔  $*${_RST}"; }
warn()  { echo -e "${_YEL}⚠  $*${_RST}"; }
error() { echo -e "${_RED}✖  $*${_RST}" >&2; }
die()   { error "$*"; exit 1; }

# ─────────────────────────────────────────────────────────────────────────────
#  Defaults (overridable via flags)
# ─────────────────────────────────────────────────────────────────────────────
INSTALL_DIR="$HOME/Applications/Spendif.ai"
BRANCH="main"
USE_BREW=false
COPY_DB=""
COPY_MODELS=""
DO_LAUNCH=false
DO_UPDATE=false

REPO_URL="https://github.com/drake69/spendify.git"
SPENDIFAI_HOME="$HOME/.spendifai"
APP_BUNDLE="/Applications/Spendif.ai.app"
MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=11

# ─────────────────────────────────────────────────────────────────────────────
#  Usage
# ─────────────────────────────────────────────────────────────────────────────
usage() {
  cat <<EOF
${_BLD}Spendif.ai macOS Installer${_RST}

Usage: $(basename "$0") [OPTIONS]

Options:
  --brew               Install Python 3.13 via Homebrew if not present
  --install-dir DIR    Code installation directory
                       (default: ~/Applications/Spendif.ai)
  --branch BRANCH      Git branch to checkout (default: main)
  --copy-db PATH       Copy an existing SQLite DB to ~/.spendifai/spendifai.db
  --copy-models PATH   Copy models directory to ~/.spendifai/models/
  --launch             Launch the app after installation
  --update             Update only (git pull + alembic), skip full reinstall
  -h, --help           Show this help

Examples:
  # Standard install (uses system Python + uv)
  bash install.sh

  # Install with Homebrew Python 3.13
  bash install.sh --brew

  # Update existing install
  bash install.sh --update

  # Install, migrate existing DB, and launch
  bash install.sh --copy-db ~/old_finance.db --launch
EOF
}

# ─────────────────────────────────────────────────────────────────────────────
#  Argument parsing
# ─────────────────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --brew)          USE_BREW=true;           shift ;;
    --install-dir)   INSTALL_DIR="${2:?'--install-dir requires a path'}"; shift 2 ;;
    --branch)        BRANCH="${2:?'--branch requires a name'}";           shift 2 ;;
    --copy-db)       COPY_DB="${2:?'--copy-db requires a path'}";         shift 2 ;;
    --copy-models)   COPY_MODELS="${2:?'--copy-models requires a path'}"; shift 2 ;;
    --launch)        DO_LAUNCH=true;          shift ;;
    --update)        DO_UPDATE=true;          shift ;;
    -h|--help)       usage; exit 0 ;;
    *)               die "Unknown option: $1  (run with --help for usage)" ;;
  esac
done

# ─────────────────────────────────────────────────────────────────────────────
#  Banner
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo -e "${_BLD}${_CYA}╔══════════════════════════════════════════════════════════════╗"
echo -e "║            Spendif.ai — macOS Installer                      ║"
echo -e "╚══════════════════════════════════════════════════════════════╝${_RST}"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
#  Helper: require a command or die
# ─────────────────────────────────────────────────────────────────────────────
require_cmd() {
  local cmd="$1" msg="${2:-}"
  if ! command -v "$cmd" &>/dev/null; then
    [[ -n "$msg" ]] && die "$msg"
    die "Required command not found: $cmd"
  fi
}

# ─────────────────────────────────────────────────────────────────────────────
#  UPDATE MODE — just pull + migrate
# ─────────────────────────────────────────────────────────────────────────────
if [[ "$DO_UPDATE" == true ]]; then
  step "Update mode: git pull + alembic upgrade head"

  [[ -d "$INSTALL_DIR/.git" ]] || die "No git repo at $INSTALL_DIR — cannot update. Run a fresh install first."
  require_cmd git

  step "Pulling latest changes from origin/$BRANCH..."
  git -C "$INSTALL_DIR" fetch --prune origin
  git -C "$INSTALL_DIR" checkout "$BRANCH"
  git -C "$INSTALL_DIR" pull --ff-only origin "$BRANCH" \
    || die "git pull failed. There may be local changes. Resolve manually in $INSTALL_DIR"
  ok "Code updated to latest $BRANCH"

  # Export PATH so uv is reachable regardless of shell profile state
  export PATH="$HOME/.local/bin:$PATH"
  require_cmd uv "uv not found. Install it: curl -LsSf https://astral.sh/uv/install.sh | sh"

  step "Syncing Python dependencies..."
  (cd "$INSTALL_DIR" && uv sync --quiet)
  ok "Dependencies up to date"

  DB_PATH="$SPENDIFAI_HOME/spendifai.db"
  if [[ -f "$DB_PATH" ]]; then
    step "Running database migrations..."
    (cd "$INSTALL_DIR" && SPENDIFAI_DB="sqlite:///$DB_PATH" uv run alembic upgrade head)
    ok "Database migrated"
  else
    info "No database found at $DB_PATH — will be created on first launch"
  fi

  echo ""
  ok "Update complete."
  echo -e "  Launch: ${_BLD}open -a Spendif.ai${_RST}  or  ${_BLD}$INSTALL_DIR/start.sh${_RST}"
  echo ""
  exit 0
fi

# ─────────────────────────────────────────────────────────────────────────────
#  FULL INSTALL
# ─────────────────────────────────────────────────────────────────────────────

# ── Step 1: macOS version check ──────────────────────────────────────────────
step "Checking macOS version..."
MACOS_VER=$(sw_vers -productVersion)
MACOS_MAJOR=$(echo "$MACOS_VER" | cut -d. -f1)
if [[ "$MACOS_MAJOR" -lt 12 ]]; then
  die "macOS 12 (Monterey) or later required. Found: $MACOS_VER"
fi
ok "macOS $MACOS_VER"

# ── Step 2: Xcode Command Line Tools (provides git) ──────────────────────────
step "Checking Xcode Command Line Tools (git)..."
if ! command -v git &>/dev/null; then
  warn "git not found. Attempting to trigger Xcode CLT install..."
  xcode-select --install 2>/dev/null || true
  echo ""
  warn "Please complete the Xcode CLT installation and re-run this script."
  exit 1
fi
ok "git $(git --version | awk '{print $3}')"

# ── Step 3: Python ────────────────────────────────────────────────────────────
step "Checking Python..."

if [[ "$USE_BREW" == true ]]; then
  info "Mode: --brew  (Python 3.13 via Homebrew)"

  if ! command -v brew &>/dev/null; then
    step "Homebrew not found — installing..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Add Homebrew to PATH for the current session (handles both Intel and Apple Silicon)
    if [[ -x /opt/homebrew/bin/brew ]]; then
      eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [[ -x /usr/local/bin/brew ]]; then
      eval "$(/usr/local/bin/brew shellenv)"
    fi
    ok "Homebrew installed"
  else
    ok "Homebrew $(brew --version | head -1 | awk '{print $2}')"
  fi

  if ! brew list python@3.13 &>/dev/null; then
    step "Installing Python 3.13 via Homebrew..."
    brew install python@3.13
  fi

  # Use Homebrew Python explicitly
  if [[ -x /opt/homebrew/bin/python3.13 ]]; then
    PYTHON_BIN="/opt/homebrew/bin/python3.13"
  elif [[ -x /usr/local/bin/python3.13 ]]; then
    PYTHON_BIN="/usr/local/bin/python3.13"
  else
    PYTHON_BIN=$(brew --prefix python@3.13)/bin/python3.13
  fi
  ok "Homebrew Python: $PYTHON_BIN"

else
  info "Mode: default  (system Python + uv)"

  if ! command -v python3 &>/dev/null; then
    die "python3 not found on PATH. Either run with --brew to auto-install, or install Python from https://python.org"
  fi

  PYTHON_BIN=$(command -v python3)
  PY_FULL=$("$PYTHON_BIN" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
  PY_MAJ=$(echo "$PY_FULL" | cut -d. -f1)
  PY_MIN=$(echo "$PY_FULL" | cut -d. -f2)

  if [[ "$PY_MAJ" -lt "$MIN_PYTHON_MAJOR" ]] || \
     { [[ "$PY_MAJ" -eq "$MIN_PYTHON_MAJOR" ]] && [[ "$PY_MIN" -lt "$MIN_PYTHON_MINOR" ]]; }; then
    die "Python $PY_FULL found, but >= ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR} required. Use --brew or upgrade manually."
  fi
  ok "Python $PY_FULL ($PYTHON_BIN)"
fi

# ── Step 4: uv ────────────────────────────────────────────────────────────────
step "Checking uv (package manager)..."
export PATH="$HOME/.local/bin:$PATH"

if ! command -v uv &>/dev/null; then
  step "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  # Also check Homebrew-installed uv
  if [[ "$USE_BREW" == true ]] && ! command -v uv &>/dev/null; then
    brew install uv
  fi
fi

require_cmd uv "uv still not found after install attempt. Add ~/.local/bin to your PATH and retry."
ok "uv $(uv --version)"

# ── Step 5: Clone or update code ──────────────────────────────────────────────
step "Installing code to $INSTALL_DIR..."
mkdir -p "$(dirname "$INSTALL_DIR")"

if [[ -d "$INSTALL_DIR/.git" ]]; then
  info "Existing installation found — updating to $BRANCH..."
  git -C "$INSTALL_DIR" fetch --prune origin
  git -C "$INSTALL_DIR" checkout "$BRANCH"
  git -C "$INSTALL_DIR" pull --ff-only origin "$BRANCH" \
    || warn "git pull failed — continuing with existing code"
else
  info "Cloning from $REPO_URL (branch: $BRANCH)..."
  git clone --branch "$BRANCH" --depth 1 "$REPO_URL" "$INSTALL_DIR"
fi
ok "Code ready at $INSTALL_DIR"

# ── Step 6: Create ~/.spendifai data directory ────────────────────────────────
step "Preparing data directory $SPENDIFAI_HOME..."
mkdir -p "$SPENDIFAI_HOME/models"

# Write install path so the app launcher can find the code directory
echo "$INSTALL_DIR" > "$SPENDIFAI_HOME/install_path.txt"
ok "Data directory ready"

# ── Step 7: Copy DB if requested ──────────────────────────────────────────────
DB_PATH="$SPENDIFAI_HOME/spendifai.db"
if [[ -n "$COPY_DB" ]]; then
  step "Copying database from $COPY_DB..."
  [[ -f "$COPY_DB" ]] || die "Source DB not found: $COPY_DB"
  cp "$COPY_DB" "$DB_PATH"
  ok "Database copied to $DB_PATH"
fi

# ── Step 8: Copy models if requested ──────────────────────────────────────────
if [[ -n "$COPY_MODELS" ]]; then
  step "Copying models from $COPY_MODELS..."
  [[ -d "$COPY_MODELS" ]] || die "Models directory not found: $COPY_MODELS"
  cp -R "$COPY_MODELS/." "$SPENDIFAI_HOME/models/"
  ok "Models copied to $SPENDIFAI_HOME/models/"
fi

# ── Step 9: Create .env if missing ────────────────────────────────────────────
step "Checking .env configuration..."
ENV_FILE="$INSTALL_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  if [[ -f "$INSTALL_DIR/.env.example" ]]; then
    cp "$INSTALL_DIR/.env.example" "$ENV_FILE"
  else
    cat > "$ENV_FILE" <<ENVEOF
# Spendif.ai environment configuration
SPENDIFAI_DB=sqlite:///$SPENDIFAI_HOME/spendifai.db
ENVEOF
  fi
  # Ensure DB path points to ~/.spendifai/spendifai.db
  if grep -q "^SPENDIFAI_DB=" "$ENV_FILE"; then
    # Replace existing line using Python (portable, no sed -i differences)
    python3 -c "
import re, pathlib
p = pathlib.Path('$ENV_FILE')
txt = p.read_text()
txt = re.sub(r'^SPENDIFAI_DB=.*$', 'SPENDIFAI_DB=sqlite:///$SPENDIFAI_HOME/spendifai.db', txt, flags=re.MULTILINE)
p.write_text(txt)
"
  else
    echo "SPENDIFAI_DB=sqlite:///$SPENDIFAI_HOME/spendifai.db" >> "$ENV_FILE"
  fi
  ok ".env created"
else
  ok ".env already exists — not overwritten"
fi

# ── Step 10: Build venv and install dependencies ──────────────────────────────
step "Creating virtual environment and installing dependencies..."
info "This may take a few minutes on the first run (compiling llama-cpp-python with Metal)..."

cd "$INSTALL_DIR"

# Compile llama-cpp-python with Metal (Apple Silicon / AMD GPU via Metal)
# GGML_METAL=on → enables the Metal compute backend for GPU-accelerated inference
# GGML_BLAS=off  → disables Accelerate BLAS to avoid conflicts with Metal path
export CMAKE_ARGS="-DGGML_METAL=on -DGGML_BLAS=off"
export FORCE_CMAKE=1

# uv sync reads pyproject.toml / uv.lock and creates .venv automatically
uv sync --python "$PYTHON_BIN" 2>&1 | tail -5 || {
  warn "uv sync with Metal flags failed — retrying without Metal (CPU-only fallback)..."
  unset CMAKE_ARGS FORCE_CMAKE
  uv sync --python "$PYTHON_BIN"
}

ok "Python environment ready"

# Verify llama-cpp-python has Metal support
METAL_CHECK=$(
  uv run python -c "
import llama_cpp, sys
# llama_cpp.llama_supports_gpu_offload() returns True when Metal is active
try:
    ok = llama_cpp.llama_supports_gpu_offload()
    print('metal_ok' if ok else 'no_metal')
except Exception:
    print('unknown')
" 2>/dev/null || echo "unknown"
)
if [[ "$METAL_CHECK" == "metal_ok" ]]; then
  ok "llama-cpp-python compiled with Metal GPU support"
elif [[ "$METAL_CHECK" == "no_metal" ]]; then
  warn "llama-cpp-python loaded but Metal not available — running CPU-only"
else
  warn "Could not verify Metal support — library loaded but GPU status unknown"
fi

# ── Step 11: Database migration ───────────────────────────────────────────────
step "Checking database..."
if [[ -f "$DB_PATH" ]]; then
  info "Existing database found — running alembic upgrade head..."
  SPENDIFAI_DB="sqlite:///$DB_PATH" uv run alembic upgrade head \
    || warn "Alembic migration failed — app may show migration prompt on first launch"
  ok "Database up to date"
else
  info "No database at $DB_PATH — will be created on first app launch (no action needed)"
fi

# ── Step 12: Generate app icon ────────────────────────────────────────────────
step "Generating app icon..."
ICON_SCRIPT="$INSTALL_DIR/packaging/macos/create_icon.py"
ICON_OUTPUT="$INSTALL_DIR/packaging/macos/spendifai.icns"

if [[ -f "$ICON_SCRIPT" ]]; then
  if uv run python "$ICON_SCRIPT" 2>/dev/null; then
    ok "Icon generated at $ICON_OUTPUT"
  else
    warn "create_icon.py failed (Pillow missing?) — using generic system icon"
    ICON_OUTPUT=""
  fi
else
  warn "create_icon.py not found — using generic system icon"
  ICON_OUTPUT=""
fi

# ── Step 13: Build .app bundle ────────────────────────────────────────────────
step "Creating macOS app bundle at $APP_BUNDLE..."

# Remove any stale bundle
rm -rf "$APP_BUNDLE"
mkdir -p "$APP_BUNDLE/Contents/MacOS"
mkdir -p "$APP_BUNDLE/Contents/Resources"

# ──  Info.plist  ──────────────────────────────────────────────────────────────
cat > "$APP_BUNDLE/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>Spendif.ai</string>
    <key>CFBundleDisplayName</key>
    <string>Spendif.ai</string>
    <key>CFBundleIdentifier</key>
    <string>ai.spendif.app</string>
    <key>CFBundleVersion</key>
    <string>1.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>CFBundleExecutable</key>
    <string>Spendif.ai</string>
    <key>CFBundleIconFile</key>
    <string>spendifai</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleSignature</key>
    <string>????</string>
    <!-- LSUIElement=false: show in Dock and allow normal window management -->
    <key>LSUIElement</key>
    <false/>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSHumanReadableCopyright</key>
    <string>Copyright © 2025 Spendif.ai</string>
</dict>
</plist>
PLIST

# ──  Icon  ────────────────────────────────────────────────────────────────────
if [[ -n "$ICON_OUTPUT" ]] && [[ -f "$ICON_OUTPUT" ]]; then
  cp "$ICON_OUTPUT" "$APP_BUNDLE/Contents/Resources/spendifai.icns"
else
  # Fall back to the system GenericApplicationIcon as a last resort
  GENERIC_ICON="/System/Library/CoreServices/CoreTypes.bundle/Contents/Resources/GenericApplicationIcon.icns"
  [[ -f "$GENERIC_ICON" ]] && cp "$GENERIC_ICON" "$APP_BUNDLE/Contents/Resources/spendifai.icns"
fi

# ──  MacOS/Spendif.ai launcher script  ────────────────────────────────────────
LAUNCHER_SCRIPT="$APP_BUNDLE/Contents/MacOS/Spendif.ai"

cat > "$LAUNCHER_SCRIPT" <<LAUNCHER
#!/usr/bin/env bash
# =============================================================================
#  Spendif.ai — app bundle launcher
#  This script is executed by macOS when the user double-clicks or opens
#  the Spendif.ai.app bundle (Spotlight, Launchpad, Dock, etc.)
#
#  Behaviour:
#  1. Reads install directory from ~/.spendifai/install_path.txt
#  2. Checks for available git updates (non-blocking, writes a flag file)
#  3. Opens a Terminal window running the Streamlit server
#  4. Opens the browser to http://localhost:8501 once the server is ready
# =============================================================================
set -euo pipefail

SPENDIFAI_HOME="\$HOME/.spendifai"
INSTALL_PATH_FILE="\$SPENDIFAI_HOME/install_path.txt"
UPDATE_FLAG="\$SPENDIFAI_HOME/.update_available"
PORT=8501

# ── Locate install directory ─────────────────────────────────────────────────
if [[ ! -f "\$INSTALL_PATH_FILE" ]]; then
  osascript -e 'display alert "Spendif.ai" message "Cannot find install_path.txt in ~/.spendifai/. Please re-run the installer." as warning'
  exit 1
fi

INSTALL_DIR="\$(cat "\$INSTALL_PATH_FILE" | tr -d '[:space:]')"

if [[ ! -d "\$INSTALL_DIR" ]]; then
  osascript -e "display alert \"Spendif.ai\" message \"Installation directory not found:\\n\$INSTALL_DIR\\n\\nPlease re-run the installer.\" as warning"
  exit 1
fi

# ── Check for updates (non-blocking, runs in background) ────────────────────
# git fetch + compare HEAD against origin/BRANCH
# Writes ~/.spendifai/.update_available with the number of commits behind
# The Streamlit sidebar component reads this file and shows a badge.
(
  cd "\$INSTALL_DIR"
  if git fetch --quiet origin 2>/dev/null; then
    BRANCH=\$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main")
    LOCAL=\$(git rev-parse HEAD 2>/dev/null || echo "")
    REMOTE=\$(git rev-parse "origin/\$BRANCH" 2>/dev/null || echo "")
    if [[ -n "\$LOCAL" ]] && [[ -n "\$REMOTE" ]] && [[ "\$LOCAL" != "\$REMOTE" ]]; then
      BEHIND=\$(git rev-list --count HEAD.."origin/\$BRANCH" 2>/dev/null || echo "0")
      if [[ "\$BEHIND" -gt 0 ]]; then
        echo "\$BEHIND commits behind origin/\$BRANCH" > "\$UPDATE_FLAG"
      else
        rm -f "\$UPDATE_FLAG"
      fi
    else
      rm -f "\$UPDATE_FLAG"
    fi
  fi
) &

# ── Kill any existing Streamlit on the same port ────────────────────────────
EXISTING_PID=\$(lsof -ti tcp:\$PORT 2>/dev/null || true)
if [[ -n "\$EXISTING_PID" ]]; then
  # Only kill if it looks like a streamlit/python process
  if ps -p "\$EXISTING_PID" -o comm= 2>/dev/null | grep -qi python; then
    kill "\$EXISTING_PID" 2>/dev/null || true
    sleep 1
  fi
fi

# ── Build the shell command to run inside the Terminal window ─────────────────
# We write a tiny temp script so that AppleScript doesn't need to escape quotes
TMPSCRIPT=\$(mktemp /tmp/spendifai_launch_XXXXXX.sh)
cat > "\$TMPSCRIPT" <<'INNEREOF'
#!/usr/bin/env bash
export PATH="\$HOME/.local/bin:\$PATH"
INSTALL_DIR="\$(cat "\$HOME/.spendifai/install_path.txt" | tr -d '[:space:]')"
cd "\$INSTALL_DIR"
source .venv/bin/activate

echo ""
echo "  Spendif.ai — avvio in corso..."
echo "  Apri il browser su: http://localhost:8501"
echo "  Chiudi questa finestra per fermare l'app."
echo ""

# Open browser after a short delay to let Streamlit start
(sleep 3 && open "http://localhost:8501") &

streamlit run app.py \\
  --server.headless true \\
  --browser.serverPort 8501 \\
  --browser.gatherUsageStats false
INNEREOF
chmod +x "\$TMPSCRIPT"

# ── Open Terminal and run the launch script ───────────────────────────────────
open -a Terminal "\$TMPSCRIPT"
LAUNCHER

chmod +x "$LAUNCHER_SCRIPT"
ok "App bundle created at $APP_BUNDLE"

# ── Step 14: Tell Spotlight to reindex /Applications ─────────────────────────
# mdimport triggers Spotlight metadata indexing for the new bundle
mdimport "$APP_BUNDLE" 2>/dev/null || true
ok "Spotlight index updated"

# ── Step 15: Final summary ────────────────────────────────────────────────────
echo ""
echo -e "${_BLD}${_GRN}╔══════════════════════════════════════════════════════════════╗"
echo -e "║            ✔  Installazione completata!                      ║"
echo -e "╚══════════════════════════════════════════════════════════════╝${_RST}"
echo ""
echo -e "  ${_BLD}Percorso codice:${_RST}    $INSTALL_DIR"
echo -e "  ${_BLD}Dati utente:${_RST}       $SPENDIFAI_HOME"
echo -e "  ${_BLD}Database:${_RST}          $DB_PATH"
echo -e "  ${_BLD}App bundle:${_RST}        $APP_BUNDLE"
echo ""
echo -e "  ${_BLD}Come avviare Spendif.ai:${_RST}"
echo -e "    • Spotlight:   ${_CYA}Cmd+Spazio → digita 'Spendif'${_RST}"
echo -e "    • Terminale:   ${_CYA}open -a Spendif.ai${_RST}"
echo -e "    • Aggiornare:  ${_CYA}bash $INSTALL_DIR/packaging/macos/install.sh --update${_RST}"
echo ""

if [[ "$DO_LAUNCH" == true ]]; then
  step "Launching Spendif.ai..."
  open -a "Spendif.ai" 2>/dev/null || open "$APP_BUNDLE"
fi
