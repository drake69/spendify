#!/usr/bin/env bash
# ──────────────────────────────────────────────
# Spendify — Startup script (macOS / Linux)
# ──────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── Pre-flight checks ──────────────────────────

# Python 3.13+ — cerca il più recente compatibile
PYTHON=""
for candidate in python3.14 python3.13 python3; do
    if command -v "$candidate" &>/dev/null; then
        ver=$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 13 ]; then
            PYTHON="$candidate"
            PY_VER="$ver"
            break
        fi
    fi
done
if [ -z "$PYTHON" ]; then
    error "Python >= 3.13 non trovato. Installa Python >= 3.13."
fi
info "Python $PY_VER OK ($PYTHON)"

# uv
if ! command -v uv &>/dev/null; then
    warn "uv non trovato. Installazione in corso..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    if ! command -v uv &>/dev/null; then
        error "Installazione di uv fallita. Installa manualmente: https://docs.astral.sh/uv/"
    fi
fi
info "uv $(uv --version | head -1) OK"

# ── Setup ───────────────────────────────────────

# .env
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        cp .env.example .env
        info "File .env creato da .env.example"
    else
        warn "File .env.example non trovato — procedo senza .env"
    fi
fi

# Dipendenze
info "Sincronizzazione dipendenze..."
uv sync --quiet

# Attivazione virtualenv
VENV_DIR="$SCRIPT_DIR/.venv"
if [ ! -d "$VENV_DIR" ]; then
    error "Virtualenv non trovato in $VENV_DIR. Esegui 'uv sync' manualmente."
fi
export PATH="$VENV_DIR/bin:$PATH"
export VIRTUAL_ENV="$VENV_DIR"
info "Virtualenv attivato ($VENV_DIR)"

# ── Avvio ───────────────────────────────────────

MODE="${1:-ui}"

case "$MODE" in
    ui)
        info "Avvio Streamlit UI su http://localhost:8501"
        exec "$VENV_DIR/bin/streamlit" run app.py --server.headless true
        ;;
    api)
        info "Avvio API server su http://localhost:8000"
        exec "$VENV_DIR/bin/uvicorn" api.main:app --host 0.0.0.0 --port 8000
        ;;
    all)
        info "Avvio UI + API..."
        "$VENV_DIR/bin/uvicorn" api.main:app --host 0.0.0.0 --port 8000 &
        API_PID=$!
        trap "kill $API_PID 2>/dev/null" EXIT
        info "API avviata (PID $API_PID) su http://localhost:8000"
        info "Avvio Streamlit UI su http://localhost:8501"
        "$VENV_DIR/bin/streamlit" run app.py --server.headless true
        ;;
    *)
        echo "Uso: $0 [ui|api|all]"
        echo "  ui   — Solo interfaccia Streamlit (default)"
        echo "  api  — Solo server API REST"
        echo "  all  — Entrambi"
        exit 1
        ;;
esac
