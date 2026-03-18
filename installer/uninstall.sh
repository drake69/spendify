#!/usr/bin/env bash
# ── Spendify — Disinstallatore (Mac / Linux) ─────────────────────────────────
# Uso:  curl -fsSL https://raw.githubusercontent.com/drake69/spendify/main/installer/uninstall.sh | bash
#       oppure: bash ~/spendify/uninstall.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

INSTALL_DIR="${SPENDIFY_INSTALL_DIR:-$HOME/spendify}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${BOLD}[spendify]${RESET} $*"; }
success() { echo -e "${GREEN}✅ $*${RESET}"; }
warn()    { echo -e "${YELLOW}⚠️  $*${RESET}"; }
error()   { echo -e "${RED}❌ $*${RESET}" >&2; exit 1; }

ask() {
    # ask <question> — legge s/y (true) o qualsiasi altra cosa (false)
    # In modalità non interattiva ritorna sempre false (default conservativo)
    if [ ! -t 0 ]; then echo "false"; return; fi
    read -rp "  $1 [s/N] " _r
    case "${_r,,}" in s|si|y|yes) echo "true" ;; *) echo "false" ;; esac
}

echo ""
echo -e "${BOLD}╔══════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║      Spendify — Disinstallatore      ║${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════╝${RESET}"
echo ""

# ── 1. Verifica Docker ────────────────────────────────────────────────────────
if ! command -v docker &>/dev/null || ! docker info &>/dev/null 2>&1; then
    warn "Docker non trovato o non in esecuzione — salto lo stop dei container."
    DOCKER_OK=false
else
    DOCKER_OK=true
fi

# ── 2. Verifica cartella installazione ───────────────────────────────────────
if [ ! -f "$INSTALL_DIR/docker-compose.yml" ]; then
    warn "Nessuna installazione trovata in: $INSTALL_DIR"
    warn "Imposta SPENDIFY_INSTALL_DIR se hai installato in una cartella diversa."
    COMPOSE_FOUND=false
else
    info "Installazione trovata in: $INSTALL_DIR"
    COMPOSE_FOUND=true
fi

# ── 3. Scelte utente ──────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}Cosa vuoi rimuovere?${RESET}"
echo ""

REMOVE_DB=$(ask    "Eliminare il database delle transazioni? (i tuoi dati finanziari)")
REMOVE_OLLAMA=$(ask "Eliminare i modelli Ollama (~8 GB su disco)?")
REMOVE_DIR=$(ask   "Eliminare la cartella di installazione ($INSTALL_DIR)?")
REMOVE_DOCKER=$(ask "Mostrare istruzioni per rimuovere Docker Desktop?")

echo ""

# ── 4. Ferma e rimuovi i container ───────────────────────────────────────────
if $COMPOSE_FOUND && $DOCKER_OK; then
    info "Fermo i container Spendify..."

    # Profili possibili: base + ollama
    PROFILE_ARGS=""
    if docker volume ls --format '{{.Name}}' 2>/dev/null | grep -q "spendify_ollama_models"; then
        PROFILE_ARGS="--profile ollama"
    fi

    # shellcheck disable=SC2086
    docker compose --project-directory "$INSTALL_DIR" $PROFILE_ARGS down 2>/dev/null || true
    success "Container fermati e rimossi"
fi

# ── 5. Rimuovi volumi selezionati ─────────────────────────────────────────────
if $DOCKER_OK; then
    if $REMOVE_DB; then
        info "Rimuovo il database (volume spendify_data e spendify_logs)..."
        docker volume rm spendify_spendify_data 2>/dev/null && success "Volume spendify_data rimosso" || warn "Volume spendify_data non trovato (già rimosso?)"
        docker volume rm spendify_spendify_logs 2>/dev/null && success "Volume spendify_logs rimosso" || warn "Volume spendify_logs non trovato"
    fi

    if $REMOVE_OLLAMA; then
        info "Rimuovo i modelli Ollama (volume ollama_models, ~8 GB)..."
        docker volume rm spendify_ollama_models 2>/dev/null && success "Volume ollama_models rimosso" || warn "Volume ollama_models non trovato (mai installato?)"
    fi
fi

# ── 6. Rimuovi la cartella di installazione ───────────────────────────────────
if $REMOVE_DIR && [ -d "$INSTALL_DIR" ]; then
    info "Rimuovo la cartella $INSTALL_DIR..."
    rm -rf "$INSTALL_DIR"
    success "Cartella rimossa"
fi

# ── 7. Istruzioni rimozione Docker ───────────────────────────────────────────
if $REMOVE_DOCKER; then
    echo ""
    echo -e "${BOLD}── Come rimuovere Docker Desktop ──────────────────────────────${RESET}"
    case "$(uname -s)" in
        Darwin)
            echo -e "  ${BOLD}macOS:${RESET}"
            echo -e "  1. Apri Docker Desktop → icona nel menu bar → Troubleshoot → Uninstall"
            echo -e "     oppure manualmente:"
            echo -e "     sudo rm -rf /Applications/Docker.app"
            echo -e "     rm -rf ~/Library/Group\\ Containers/group.com.docker"
            echo -e "     rm -rf ~/Library/Containers/com.docker.docker"
            echo -e "     rm -rf ~/.docker"
            ;;
        Linux)
            echo -e "  ${BOLD}Linux (Ubuntu/Debian):${RESET}"
            echo -e "  sudo apt-get purge docker-ce docker-ce-cli containerd.io docker-compose-plugin"
            echo -e "  sudo rm -rf /var/lib/docker /var/lib/containerd"
            echo ""
            echo -e "  ${BOLD}Linux (Fedora/RHEL):${RESET}"
            echo -e "  sudo dnf remove docker-ce docker-ce-cli containerd.io docker-compose-plugin"
            echo -e "  sudo rm -rf /var/lib/docker /var/lib/containerd"
            ;;
        *)
            echo -e "  Visita: https://docs.docker.com/engine/install/linux-postinstall/#uninstall-docker-engine"
            ;;
    esac
    echo ""
fi

# ── 8. Riepilogo ──────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}── Riepilogo ───────────────────────────────────────────────────${RESET}"
$COMPOSE_FOUND  && success "Container Spendify rimossi"  || true
$REMOVE_DB      && success "Database transazioni rimosso" || info "Database transazioni conservato"
$REMOVE_OLLAMA  && success "Modelli Ollama rimossi"       || info "Modelli Ollama conservati"
$REMOVE_DIR     && success "Cartella $INSTALL_DIR rimossa" || info "Cartella $INSTALL_DIR conservata"
echo ""
echo -e "  Per reinstallare:"
echo -e "  ${BOLD}curl -fsSL https://raw.githubusercontent.com/drake69/spendify/main/installer/install.sh | bash${RESET}"
echo ""
