#!/usr/bin/env bash
# bench_push_ssh.sh — Copia il minimo indispensabile da dev → host remoto via SSH
#
# Identico a bench_push_usb.sh ma usa rsync over SSH invece di filesystem locale.
# L'host remoto non ha bisogno di git — tutto il necessario viene copiato.
#
# Uso:
#   bash scripts/bench_push_ssh.sh --dest user@bench-host:/home/user/spendif
#   bash scripts/bench_push_ssh.sh --dest user@192.168.1.50:~/spendif --clean
#
# Opzioni:
#   --dest HOST:PATH   Destinazione SSH (user@host:path) [obbligatorio]
#   --clean            Cancella il dest prima di copiare (rsync --delete)
#   --dry-run          Mostra cosa verrebbe copiato senza farlo
#   --key PATH         Chiave SSH da usare (default: ~/.ssh/id_rsa)
#   --port N           Porta SSH (default: 22)

set -euo pipefail

DEST=""
CLEAN=0
DRY_RUN=0
SSH_KEY=""
SSH_PORT=22

# ── Argomenti ─────────────────────────────────────────────────────────────
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
    echo "  --dest HOST:PATH   Es. user@bench-pc:~/spendif"
    echo "  --clean            Cancella dest prima di copiare"
    echo "  --dry-run          Mostra cosa verrebbe copiato"
    echo "  --key PATH         Chiave SSH (default: ~/.ssh/id_rsa)"
    echo "  --port N           Porta SSH (default: 22)"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=== bench_push_ssh ==="
echo "  Source : $PROJECT_ROOT"
echo "  Dest   : $DEST"
[[ $CLEAN -eq 1 ]]   && echo "  Mode   : --clean (rsync --delete)"
[[ $DRY_RUN -eq 1 ]] && echo "  Mode   : --dry-run"
echo ""

# ── SSH options ────────────────────────────────────────────────────────────
SSH_OPTS="-p $SSH_PORT -o StrictHostKeyChecking=accept-new"
[[ -n "$SSH_KEY" ]] && SSH_OPTS="$SSH_OPTS -i $SSH_KEY"

RSYNC_FLAGS=(-av --progress -e "ssh $SSH_OPTS")
[[ $CLEAN -eq 1 ]]   && RSYNC_FLAGS+=(--delete)
[[ $DRY_RUN -eq 1 ]] && RSYNC_FLAGS+=(--dry-run)

EXCLUDES=(
    --exclude='.git/'
    --exclude='__pycache__/'
    --exclude='*.pyc'
    --exclude='.DS_Store'
    --exclude='tests/results_archive/'
    --exclude='tests/generated_files/benchmark/'
    --exclude='tests/logs/'
    --exclude='tests/generated_files/results_deterministic.csv'
    --exclude='ui/'
    --exclude='docs/'
    --exclude='api/'
    --exclude='reports/'
    --exclude='.venv/'
    --exclude='.vscode/'
    --exclude='*.egg-info/'
)

rsync "${RSYNC_FLAGS[@]}" "${EXCLUDES[@]}" \
    "$PROJECT_ROOT/" \
    "$DEST/"

echo ""
echo "=== Push SSH completato ==="
echo ""
echo "Connettiti all'host e avvia il benchmark:"
HOST_PART="${DEST%%:*}"
PATH_PART="${DEST#*:}"
echo "  ssh $HOST_PART"
echo "  cd $PATH_PART"
echo "  bash tests/run_benchmark_full.sh"
echo ""
echo "Poi raccogli i risultati con:"
echo "  bash scripts/bench_pull_ssh.sh --from $DEST"
