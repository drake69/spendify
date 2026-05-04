#!/usr/bin/env bash
# bench_push_ssh.sh — Copia il minimo indispensabile da dev → host remoto via SSH
#
# Identico a bench_push_usb.sh ma via rsync/SSH.
# Esclude automaticamente tutto ciò che è in .gitignore più:
#   .claude/, backup/, quarantine/, logs/, *.db, ui/, docker/, installer/, ...
#   (lista completa in benchmark/.rsync-bench-exclude)
#
# Uso:
#   bash benchmark/bench_push_ssh.sh --dest user@bench-host:/home/user/spendif
#   bash benchmark/bench_push_ssh.sh --dest user@192.168.1.50:~/Desktop/spendif-ai --clean
#
# Opzioni:
#   --dest HOST:PATH   Destinazione SSH [obbligatorio]
#   --clean            Cancella dest prima di copiare (rsync --delete)
#   --dry-run          Mostra cosa verrebbe copiato
#   --key PATH         Chiave SSH (default: ~/.ssh/id_rsa)
#   --port N           Porta SSH (default: 22)

set -euo pipefail

DEST=""
CLEAN=0
DRY_RUN=0
SSH_KEY=""
SSH_PORT=22

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dest)    DEST="$2"; shift 2 ;;
        --clean)   CLEAN=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        --key)     SSH_KEY="$2"; shift 2 ;;
        --port)    SSH_PORT="$2"; shift 2 ;;
        *) echo "Opzione non riconosciuta: $1"; exit 1 ;;
    esac
done

if [[ -z "$DEST" ]]; then
    echo "Uso: $0 --dest user@host:path [--clean] [--dry-run] [--key PATH] [--port N]"
    echo ""
    echo "  --dest HOST:PATH   Es. user@bench-pc:~/Desktop/spendif-ai"
    echo "  --clean            Cancella dest prima di copiare"
    echo "  --dry-run          Mostra cosa verrebbe copiato"
    echo "  --key PATH         Chiave SSH"
    echo "  --port N           Porta SSH (default: 22)"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
EXCLUDE_FILE="$SCRIPT_DIR/.rsync-bench-exclude"

if [[ ! -f "$EXCLUDE_FILE" ]]; then
    echo "ERROR: file esclusioni non trovato: $EXCLUDE_FILE"
    exit 1
fi

# ── Generate benchmark/.version (YYYYMMDDHHMMSS-sha7) ─────────────────────
_PUSH_SHA=$(git -C "$PROJECT_ROOT" rev-parse --short HEAD 2>/dev/null || echo "unknown")
_PUSH_TS=$(date +"%Y%m%d%H%M%S")
_PUSH_VERSION="${_PUSH_TS}-${_PUSH_SHA}"
echo "${_PUSH_VERSION}" > "$SCRIPT_DIR/.version"

echo "=== bench_push_ssh ==="
echo "  Source  : $PROJECT_ROOT"
echo "  Version : ${_PUSH_VERSION}"
echo "  Dest    : $DEST"
echo "  Exclude : $EXCLUDE_FILE"
[[ $CLEAN -eq 1 ]]   && echo "  Mode    : --clean (rsync --delete-excluded)"
[[ $DRY_RUN -eq 1 ]] && echo "  Mode    : --dry-run"
echo ""

SSH_OPTS="-p $SSH_PORT -o StrictHostKeyChecking=accept-new"
[[ -n "$SSH_KEY" ]] && SSH_OPTS="$SSH_OPTS -i $SSH_KEY"

RSYNC_FLAGS=(-av --progress -e "ssh $SSH_OPTS")
[[ $CLEAN -eq 1 ]]   && RSYNC_FLAGS+=(--delete --delete-excluded)
[[ $DRY_RUN -eq 1 ]] && RSYNC_FLAGS+=(--dry-run)

# IMPORTANTE: --include prima di --exclude-from (rsync: prima regola che fa match vince)
rsync "${RSYNC_FLAGS[@]}" \
    --include='benchmark/benchmark_models.csv' \
    --include='benchmark/generated_files/' \
    --include='benchmark/generated_files/**' \
    --exclude-from="$EXCLUDE_FILE" \
    "$PROJECT_ROOT/" \
    "$DEST/"

echo ""
echo "=== Push SSH completato ==="
HOST_PART="${DEST%%:*}"
PATH_PART="${DEST#*:}"
echo ""
echo "Connettiti e avvia il benchmark:"
echo "  ssh $HOST_PART"
echo "  cd $PATH_PART"
echo "  Linux / macOS : bash benchmark/run_benchmark_full.sh"
echo "  Windows       : powershell -ExecutionPolicy Bypass -File benchmark\run_benchmark_full.ps1"
echo ""
echo "Poi raccogli con:"
echo "  Linux / macOS : bash benchmark/bench_pull_ssh.sh --from $DEST"
echo "  Windows       : powershell -ExecutionPolicy Bypass -File benchmark\bench_pull_ssh.ps1 -From $DEST"
