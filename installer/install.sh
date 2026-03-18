#!/usr/bin/env bash
# ── Spendify — Installer (Mac / Linux) ───────────────────────────────────────
# Uso:  curl -fsSL https://raw.githubusercontent.com/drake69/spendify/main/install.sh | bash
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

INSTALL_DIR="${SPENDIFY_INSTALL_DIR:-$HOME/spendify}"
COMPOSE_URL="https://raw.githubusercontent.com/drake69/spendify/main/docker/docker-compose.release.yml"
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

# ── 2. AI locale (Ollama + gemma3:12b) ───────────────────────────────────────
echo ""
echo -e "${BOLD}Vuoi usare l'AI locale? (Ollama + gemma3:12b)${RESET}"
echo -e "  • Nessuna API key richiesta — funziona completamente offline"
echo -e "  • Richiede: ~8 GB di spazio disco e almeno 8 GB di RAM libera"
echo -e "  • Prima volta: download del modello ~10-15 minuti"
echo -e "  • Alternativa: inserire una API key (OpenAI/Anthropic) dopo l'avvio"
echo ""

# Non interattivo (CI/pipe) → default No; interattivo → chiede
USE_OLLAMA=false
if [ -t 0 ]; then
    read -rp "  Installa AI locale? [s/N] " _reply
    case "${_reply,,}" in
        s|si|y|yes) USE_OLLAMA=true ;;
    esac
else
    info "Modalità non interattiva — AI locale non installata (aggiungere --profile ollama manualmente)."
fi

if $USE_OLLAMA; then
    warn "Il download del modello (~8 GB) partirà in background dopo l'avvio."
fi

# ── 3. Crea cartella di installazione ────────────────────────────────────────
echo ""
info "Cartella di installazione: $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

# ── 4. Scarica docker-compose.release.yml ────────────────────────────────────
info "Scarico la configurazione..."
curl -fsSL "$COMPOSE_URL" -o docker-compose.yml
success "Configurazione scaricata"

# ── 5. Pull immagine + avvio ──────────────────────────────────────────────────
PROFILE_ARGS=""
$USE_OLLAMA && PROFILE_ARGS="--profile ollama"

info "Scarico le immagini Docker (prima volta: ~500 MB, poi aggiornamenti incrementali)..."
# shellcheck disable=SC2086
docker compose $PROFILE_ARGS pull

info "Avvio Spendify..."
# shellcheck disable=SC2086
docker compose $PROFILE_ARGS up -d

# ── 6. Attendi che l'app sia pronta ───────────────────────────────────────────
info "Attendo che l'app sia pronta..."
for i in $(seq 1 30); do
    if curl -sf "$APP_URL/_stcore/health" >/dev/null 2>&1; then
        break
    fi
    sleep 2
done

if ! curl -sf "$APP_URL/_stcore/health" >/dev/null 2>&1; then
    warn "L'app non risponde entro 60s. Controlla i log con:\n  docker compose --project-directory $INSTALL_DIR logs -f"
else
    success "Spendify è in esecuzione!"
fi

# ── 7. Istruzioni finali ───────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}🚀 Apri il browser su: ${GREEN}$APP_URL${RESET}"
echo ""
if $USE_OLLAMA; then
    echo -e "${YELLOW}  AI locale in download — attendere il completamento (vedi log):${RESET}"
    echo -e "  ${BOLD}docker compose --project-directory $INSTALL_DIR logs -f ollama-init${RESET}"
    echo -e ""
    echo -e "  Poi in Spendify → ⚙️ Impostazioni → Backend LLM:"
    echo -e "    Tipo: Ollama   URL: ${BOLD}http://ollama:11434${RESET}   Modello: ${BOLD}gemma3:12b${RESET}"
    echo ""
fi
echo -e "  Fermare:        ${BOLD}docker compose --project-directory $INSTALL_DIR $PROFILE_ARGS down${RESET}"
echo -e "  Aggiornare:     ${BOLD}docker compose --project-directory $INSTALL_DIR $PROFILE_ARGS pull && docker compose --project-directory $INSTALL_DIR $PROFILE_ARGS up -d${RESET}"
echo -e "  Log:            ${BOLD}docker compose --project-directory $INSTALL_DIR logs -f${RESET}"
echo -e "  Disinstallare:  ${BOLD}curl -fsSL https://raw.githubusercontent.com/drake69/spendify/main/installer/uninstall.sh | bash${RESET}"
echo ""

# Apri browser automaticamente se possibile
if command -v open &>/dev/null; then
    open "$APP_URL" || true           # macOS
elif command -v xdg-open &>/dev/null; then
    xdg-open "$APP_URL" || true       # Linux (ignora errori in ambienti headless)
fi
