#!/usr/bin/env bash
# ── Spendify — Installer (Mac / Linux) ───────────────────────────────────────
# Uso:  curl -fsSL https://raw.githubusercontent.com/drake69/spendify/main/install.sh | bash
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

INSTALL_DIR="${SPENDIFY_INSTALL_DIR:-$HOME/spendify}"
COMPOSE_URL="https://raw.githubusercontent.com/drake69/spendify/main/docker-compose.release.yml"
APP_URL="http://localhost:8501"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${BOLD}[spendify]${RESET} $*"; }
success() { echo -e "${GREEN}✅ $*${RESET}"; }
warn()    { echo -e "${YELLOW}⚠️  $*${RESET}"; }
error()   { echo -e "${RED}❌ $*${RESET}" >&2; exit 1; }

echo ""
echo -e "${BOLD}╔══════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║        Spendify — Installer          ║${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════╝${RESET}"
echo ""

# ── 1. Verifica Docker ────────────────────────────────────────────────────────
info "Verifico Docker..."
if ! command -v docker &>/dev/null; then
    error "Docker non trovato.\n\nInstalla Docker Desktop da: https://www.docker.com/products/docker-desktop/\nPoi riavvia questo script."
fi

if ! docker info &>/dev/null 2>&1; then
    error "Docker non è in esecuzione.\n\nAvvia Docker Desktop e riprova."
fi

success "Docker trovato: $(docker --version)"

# ── 2. Crea cartella di installazione ────────────────────────────────────────
info "Cartella di installazione: $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

# ── 3. Scarica docker-compose.release.yml ────────────────────────────────────
info "Scarico la configurazione..."
curl -fsSL "$COMPOSE_URL" -o docker-compose.yml
success "Configurazione scaricata"

# ── 4. Pull immagine + avvio ──────────────────────────────────────────────────
info "Scarico l'immagine Spendify (prima volta: ~500 MB, poi aggiornamenti incrementali)..."
docker compose pull

info "Avvio Spendify..."
docker compose up -d

# ── 5. Attendi che l'app sia pronta ───────────────────────────────────────────
info "Attendo che l'app sia pronta..."
for i in $(seq 1 30); do
    if curl -sf "$APP_URL/_stcore/health" >/dev/null 2>&1; then
        break
    fi
    sleep 2
done

if ! curl -sf "$APP_URL/_stcore/health" >/dev/null 2>&1; then
    warn "L'app non risponde entro 60s. Controlla i log con:\n  docker compose -C $INSTALL_DIR logs -f"
else
    success "Spendify è in esecuzione!"
fi

# ── 6. Apri il browser ────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}🚀 Apri il browser su: ${GREEN}$APP_URL${RESET}"
echo ""
echo -e "  Fermare:    ${BOLD}docker compose -C $INSTALL_DIR down${RESET}"
echo -e "  Aggiornare: ${BOLD}docker compose -C $INSTALL_DIR pull && docker compose -C $INSTALL_DIR up -d${RESET}"
echo -e "  Log:        ${BOLD}docker compose -C $INSTALL_DIR logs -f${RESET}"
echo ""

# Apri browser automaticamente se possibile
if command -v open &>/dev/null; then
    open "$APP_URL"           # macOS
elif command -v xdg-open &>/dev/null; then
    xdg-open "$APP_URL"       # Linux
fi
