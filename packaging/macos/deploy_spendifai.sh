#!/usr/bin/env bash
# =============================================================================
#  Spendif.ai — macOS Deploy Script
#  Installa o aggiorna Spendif.ai su macOS con opzionale copia del DB.
#
#  Uso:
#    bash deploy_spendifai.sh [opzioni]
#
#  Opzioni:
#    --install-dir DIR   Directory di installazione (default: ~/Applications/Spendif.ai)
#    --branch BRANCH     Branch git da usare (default: main)
#    --copy-db PATH      Copia il DB da un path locale (file .db o directory)
#    --copy-models PATH  Copia i modelli GGUF da un path locale (directory)
#    --no-llama-rebuild  Salta il rebuild llama-cpp-python (più veloce se già compilato)
#    --launch            Avvia l'app al termine dell'installazione
#    -h, --help          Mostra questo aiuto
#
#  Esempi:
#    # Installazione fresh
#    bash deploy_spendifai.sh
#
#    # Aggiornamento con copia DB da USB
#    bash deploy_spendifai.sh --copy-db /Volumes/USB/ledger.db
#
#    # Install completo con DB e modelli da backup
#    bash deploy_spendifai.sh --copy-db ~/Backup/ledger.db --copy-models ~/Backup/models/
#
#    # Install su directory custom, branch dev
#    bash deploy_spendifai.sh --install-dir ~/Dev/spendifai --branch geppi-rasnovich
# =============================================================================
set -euo pipefail

# ── Colori ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERR ]${NC}  $*"; exit 1; }
step()    { echo -e "\n${BOLD}${CYAN}──── $* ────${NC}"; }
success() { echo -e "${GREEN}✅  $*${NC}"; }

# ── Defaults ─────────────────────────────────────────────────────────────────
INSTALL_DIR="$HOME/Applications/Spendif.ai"
SPENDIFAI_HOME="$HOME/.spendifai"
REPO_URL="https://github.com/drake69/spendify.git"
BRANCH="main"
COPY_DB=""
COPY_MODELS=""
NO_LLAMA_REBUILD=false
LAUNCH=false

# ── Parsing argomenti ─────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --install-dir)  INSTALL_DIR="$2"; shift 2 ;;
        --branch)       BRANCH="$2"; shift 2 ;;
        --copy-db)      COPY_DB="$2"; shift 2 ;;
        --copy-models)  COPY_MODELS="$2"; shift 2 ;;
        --no-llama-rebuild) NO_LLAMA_REBUILD=true; shift ;;
        --launch)       LAUNCH=true; shift ;;
        -h|--help)
            sed -n '/^#  Uso:/,/^# =====/p' "$0" | grep -v "^# =====" | sed 's/^#  \?//'
            exit 0 ;;
        *) error "Opzione sconosciuta: $1 — usa --help" ;;
    esac
done

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}============================================================${NC}"
echo -e "${BOLD}  Spendif.ai — Deploy macOS${NC}"
echo -e "${BOLD}============================================================${NC}"
echo "  Install dir : $INSTALL_DIR"
echo "  Branch      : $BRANCH"
echo "  Copy DB     : ${COPY_DB:-—}"
echo "  Copy models : ${COPY_MODELS:-—}"
echo ""

# ── Step 1: Python ────────────────────────────────────────────────────────────
step "1/7  Verifica Python"
PYTHON=""
for candidate in python3.14 python3.13 python3; do
    if command -v "$candidate" &>/dev/null; then
        ver=$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 13 ]; then
            PYTHON="$candidate"; PY_VER="$ver"; break
        fi
    fi
done
[ -z "$PYTHON" ] && error "Python >= 3.13 non trovato.\n   Installa: brew install python@3.13"
success "Python $PY_VER ($PYTHON)"

# ── Step 2: uv ───────────────────────────────────────────────────────────────
step "2/7  Verifica uv"
if ! command -v uv &>/dev/null; then
    info "Installazione uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    command -v uv &>/dev/null || error "Installazione uv fallita — https://docs.astral.sh/uv/"
fi
success "uv $(uv --version | head -1)"

# ── Step 3: Clone / Update ───────────────────────────────────────────────────
step "3/7  Repository"
mkdir -p "$(dirname "$INSTALL_DIR")"
if [ -d "$INSTALL_DIR/.git" ]; then
    info "Repository esistente — aggiornamento..."
    cd "$INSTALL_DIR"
    git fetch --quiet origin
    git checkout "$BRANCH" --quiet 2>/dev/null || warn "Branch $BRANCH non trovato — uso branch corrente"
    git pull --ff-only origin "$BRANCH" 2>/dev/null || warn "git pull fallito — uso versione esistente"
else
    info "Clone da $REPO_URL (branch: $BRANCH)..."
    git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi
COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "n/a")
success "Repository aggiornato — commit $COMMIT"

# ── Step 4: Dipendenze + llama.cpp con Metal ─────────────────────────────────
step "4/7  Dipendenze Python"
cd "$INSTALL_DIR"
info "uv sync..."
uv sync --quiet

VENV="$INSTALL_DIR/.venv"
VENV_PYTHON="$VENV/bin/python3"

if [ "$NO_LLAMA_REBUILD" = false ]; then
    # Verifica versione llama-cpp-python installata vs ultima disponibile
    INSTALLED_VER=$("$VENV_PYTHON" -c "import llama_cpp; print(llama_cpp.__version__)" 2>/dev/null || echo "0.0.0")
    LATEST_VER=$(curl -s "https://pypi.org/pypi/llama-cpp-python/json" 2>/dev/null \
        | "$VENV_PYTHON" -c "
import sys, json
from packaging.version import Version
try:
    d = json.load(sys.stdin)
    print(sorted(d['releases'].keys(), key=lambda v: Version(v))[-1])
except: print('$INSTALLED_VER')
" 2>/dev/null || echo "$INSTALLED_VER")

    if [ "$INSTALLED_VER" != "$LATEST_VER" ]; then
        info "llama-cpp-python: $INSTALLED_VER → $LATEST_VER (build con Metal)..."
        CMAKE_ARGS="-DGGML_METAL=on" \
            "$VENV_PYTHON" -m pip install --quiet \
            "llama-cpp-python==$LATEST_VER" --upgrade --no-cache-dir \
            2>/dev/null || warn "Build llama-cpp-python fallito — uso versione esistente"
        INSTALLED_VER=$("$VENV_PYTHON" -c "import llama_cpp; print(llama_cpp.__version__)" 2>/dev/null || echo "?")
    fi
    success "llama-cpp-python $INSTALLED_VER (Metal)"
else
    INSTALLED_VER=$("$VENV_PYTHON" -c "import llama_cpp; print(llama_cpp.__version__)" 2>/dev/null || echo "?")
    info "llama-cpp-python $INSTALLED_VER (rebuild saltato)"
fi

# ── Step 5: Directories e .env ───────────────────────────────────────────────
step "5/7  Configurazione"
mkdir -p "$SPENDIFAI_HOME/models"
success "Directory $SPENDIFAI_HOME creata"

DB_PATH="$SPENDIFAI_HOME/ledger.db"
ENV_FILE="$INSTALL_DIR/.env"

if [ ! -f "$ENV_FILE" ]; then
    echo "SPENDIFAI_DB=sqlite:///$DB_PATH" > "$ENV_FILE"
    success ".env creato (DB: $DB_PATH)"
else
    info ".env già presente"
fi

# ── Step 6: Copia DB (opzionale) ─────────────────────────────────────────────
if [ -n "$COPY_DB" ]; then
    step "6/7  Copia DB"

    # Risolvi il path sorgente
    if [ -f "$COPY_DB" ]; then
        SRC_DB="$COPY_DB"
    elif [ -d "$COPY_DB" ] && [ -f "$COPY_DB/ledger.db" ]; then
        SRC_DB="$COPY_DB/ledger.db"
    else
        error "DB sorgente non trovato: $COPY_DB"
    fi

    # Backup del DB esistente se presente
    if [ -f "$DB_PATH" ]; then
        BACKUP="$DB_PATH.bak_$(date +%Y%m%d_%H%M%S)"
        cp "$DB_PATH" "$BACKUP"
        warn "DB esistente salvato in $BACKUP"
    fi

    cp "$SRC_DB" "$DB_PATH"
    SIZE=$(du -sh "$DB_PATH" | cut -f1)
    success "DB copiato: $SRC_DB → $DB_PATH ($SIZE)"
else
    step "6/7  DB"
    if [ -f "$DB_PATH" ]; then
        SIZE=$(du -sh "$DB_PATH" | cut -f1)
        info "DB esistente: $DB_PATH ($SIZE) — non modificato"
    else
        info "Nessun DB — verrà creato al primo avvio"
    fi
fi

# ── Step 7: Copia modelli (opzionale) ────────────────────────────────────────
if [ -n "$COPY_MODELS" ]; then
    step "7/7  Copia modelli GGUF"
    [ -d "$COPY_MODELS" ] || error "Directory modelli sorgente non trovata: $COPY_MODELS"

    GGUF_COUNT=$(find "$COPY_MODELS" -name "*.gguf" | wc -l | tr -d ' ')
    [ "$GGUF_COUNT" -eq 0 ] && warn "Nessun file .gguf trovato in $COPY_MODELS"

    info "Copia $GGUF_COUNT modelli GGUF da $COPY_MODELS..."
    rsync -ah --progress --include="*.gguf" --exclude="*" \
        "$COPY_MODELS/" "$SPENDIFAI_HOME/models/" 2>/dev/null \
        || cp "$COPY_MODELS"/*.gguf "$SPENDIFAI_HOME/models/" 2>/dev/null || true

    TOTAL=$(du -sh "$SPENDIFAI_HOME/models/" | cut -f1)
    success "$GGUF_COUNT modelli copiati ($TOTAL totale)"
else
    step "7/7  Modelli"
    MODEL_COUNT=$(find "$SPENDIFAI_HOME/models" -name "*.gguf" 2>/dev/null | wc -l | tr -d ' ')
    info "$MODEL_COUNT modelli già presenti in $SPENDIFAI_HOME/models/"
fi

# ── Launcher ─────────────────────────────────────────────────────────────────
LAUNCHER="$INSTALL_DIR/packaging/macos/Spendify.command"
chmod +x "$LAUNCHER" 2>/dev/null || true
if [ ! -L "$HOME/Applications/Spendif.ai.command" ]; then
    ln -sf "$LAUNCHER" "$HOME/Applications/Spendif.ai.command" 2>/dev/null || true
fi

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}============================================================${NC}"
echo -e "${BOLD}${GREEN}  ✅  Deploy completato!${NC}"
echo -e "${BOLD}${GREEN}============================================================${NC}"
echo ""
echo "  App        : $INSTALL_DIR"
echo "  DB         : $DB_PATH"
echo "  Modelli    : $SPENDIFAI_HOME/models/"
echo "  Commit     : $COMMIT"
echo ""
echo -e "  Per avviare:"
echo "    bash $INSTALL_DIR/start.sh"
echo "    oppure double-click: ~/Applications/Spendif.ai.command"
echo ""

if [ "$LAUNCH" = true ]; then
    info "Avvio Spendif.ai..."
    cd "$INSTALL_DIR"
    exec "$VENV/bin/streamlit" run app.py --server.headless true
else
    read -rp "Vuoi avviare Spendif.ai ora? (s/N) " ANSWER || ANSWER="n"
    if [[ "$ANSWER" =~ ^[sS]$ ]]; then
        cd "$INSTALL_DIR"
        exec "$VENV/bin/streamlit" run app.py --server.headless true
    fi
fi
