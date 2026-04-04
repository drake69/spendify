#!/bin/bash
# ============================================================
#  Spendif.ai — macOS One-Click Installer
#  Double-click this file in Finder to install Spendif.ai.
# ============================================================
set -euo pipefail

INSTALL_DIR="$HOME/Applications/Spendif.ai"
SPENDIFAI_HOME="$HOME/.spendifai"
REPO_URL="https://github.com/drake69/spendify.git"
MIN_PYTHON="3.11"

echo ""
echo "============================================================"
echo "  Spendif.ai — Installazione macOS"
echo "============================================================"
echo ""

# ── 1. Check Python ──────────────────────────────────────────
echo "→ Verifica Python..."
if ! command -v python3 &>/dev/null; then
    echo "❌ Python 3 non trovato. Installalo da https://python.org o via:"
    echo "   brew install python@3.13"
    echo ""
    read -p "Premi Invio per chiudere..." _
    exit 1
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]); then
    echo "❌ Python $PY_VERSION trovato, serve >= $MIN_PYTHON"
    echo "   Aggiorna: brew install python@3.13"
    read -p "Premi Invio per chiudere..." _
    exit 1
fi
echo "  ✅ Python $PY_VERSION"

# ── 2. Install uv (if missing) ──────────────────────────────
echo "→ Verifica uv (package manager)..."
if ! command -v uv &>/dev/null; then
    echo "  Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi
echo "  ✅ uv $(uv --version 2>/dev/null || echo 'installed')"

# ── 3. Clone / Update repo ──────────────────────────────────
echo "→ Installazione Spendif.ai in $INSTALL_DIR..."
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "  Aggiornamento..."
    cd "$INSTALL_DIR"
    git pull --ff-only || echo "  ⚠️  git pull fallito — uso versione esistente"
else
    echo "  Download..."
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# ── 4. Install dependencies ─────────────────────────────────
echo "→ Installazione dipendenze..."
uv sync --quiet 2>/dev/null || uv sync

# ── 5. Create .spendifai directory ────────────────────────────
echo "→ Creazione $SPENDIFAI_HOME..."
mkdir -p "$SPENDIFAI_HOME/models"

# ── 6. Create .env if missing ────────────────────────────────
if [ ! -f "$INSTALL_DIR/.env" ]; then
    echo "SPENDIFAI_DB=sqlite:///$SPENDIFAI_HOME/ledger.db" > "$INSTALL_DIR/.env"
    echo "  ✅ .env creato (DB in $SPENDIFAI_HOME/ledger.db)"
fi

# ── 7. Create launcher script ───────────────────────────────
LAUNCHER="$INSTALL_DIR/packaging/macos/Spendif.ai.command"
chmod +x "$LAUNCHER" 2>/dev/null || true

# Also create a symlink in ~/Applications for easy access
if [ ! -L "$HOME/Applications/Spendif.ai.command" ]; then
    ln -sf "$LAUNCHER" "$HOME/Applications/Spendif.ai.command" 2>/dev/null || true
fi

# ── 8. Detect HW and show recommendation ────────────────────
echo ""
echo "→ Rilevamento hardware..."
RAM_GB=$(python3 -c "
import subprocess
out = subprocess.check_output(['sysctl', '-n', 'hw.memsize'], text=True)
print(int(out.strip()) // (1024**3))
")
GPU=$(python3 -c "
import subprocess
print(subprocess.check_output(['sysctl', '-n', 'machdep.cpu.brand_string'], text=True).strip())
")
echo "  RAM: ${RAM_GB} GB | GPU: ${GPU}"

# Get recommended model
RECOMMENDED=$(cd "$INSTALL_DIR" && uv run python -c "
from config import get_recommended_model
m = get_recommended_model(${RAM_GB})
if m:
    print(f'{m.name}|{m.size_mb}|{m.filename}')
else:
    print('none|0|none')
")
MODEL_NAME=$(echo "$RECOMMENDED" | cut -d'|' -f1)
MODEL_SIZE=$(echo "$RECOMMENDED" | cut -d'|' -f2)

echo "  Modello consigliato: $MODEL_NAME ($MODEL_SIZE MB)"

# ── Done ─────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo "  ✅ Installazione completata!"
echo ""
echo "  Per avviare Spendif.ai:"
echo "    • Double-click Spendif.ai.command in ~/Applications/Spendif.ai/packaging/macos/"
echo "    • Oppure da terminale: cd $INSTALL_DIR && uv run streamlit run app.py"
echo ""
echo "  Al primo avvio il modello LLM verrà scaricato automaticamente."
echo "============================================================"
echo ""
read -p "Vuoi avviare Spendif.ai ora? (s/N) " LAUNCH
if [[ "$LAUNCH" =~ ^[sS]$ ]]; then
    cd "$INSTALL_DIR"
    uv run streamlit run app.py
fi
