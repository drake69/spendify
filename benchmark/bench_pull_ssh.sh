#!/usr/bin/env bash
# bench_pull_ssh.sh — Raccoglie risultati e log dal host remoto → dev via SSH
#
# Cosa viene copiato:
#   benchmark/results/*.csv   → CSV versionati <version>_<hostname>.csv
#   benchmark/logs/           → log per debug
#
# Uso:
#   bash benchmark/bench_pull_ssh.sh --from user@bench-host:/home/user/spendif
#   bash benchmark/bench_pull_ssh.sh --from user@192.168.1.50:~/spendif --dry-run
#
# Opzioni:
#   --from HOST:PATH   Sorgente SSH [obbligatorio]
#   --dry-run          Mostra cosa verrebbe copiato
#   --key PATH         Chiave SSH
#   --port N           Porta SSH (default: 22)

set -euo pipefail

FROM=""
DRY_RUN=0
SSH_KEY=""
SSH_PORT=22

while [[ $# -gt 0 ]]; do
    case "$1" in
        --from)    FROM="$2"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        --key)     SSH_KEY="$2"; shift 2 ;;
        --port)    SSH_PORT="$2"; shift 2 ;;
        *) echo "Opzione non riconosciuta: $1"; exit 1 ;;
    esac
done

if [[ -z "$FROM" ]]; then
    echo "Uso: $0 --from user@host:path [--dry-run] [--key PATH] [--port N]"
    echo ""
    echo "  --from HOST:PATH   Es. user@bench-pc:~/spendif"
    echo "  --dry-run          Mostra cosa verrebbe copiato"
    echo "  --key PATH         Chiave SSH"
    echo "  --port N           Porta SSH (default: 22)"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ARCHIVE_DIR="$PROJECT_ROOT/benchmark/results"
LOGS_DIR="$PROJECT_ROOT/benchmark/logs"

echo "=== bench_pull_ssh ==="
echo "  From  : $FROM"
echo "  Dest  : $PROJECT_ROOT"
[[ $DRY_RUN -eq 1 ]] && echo "  Mode  : --dry-run"
echo ""

SSH_OPTS="-p $SSH_PORT -o StrictHostKeyChecking=accept-new"
[[ -n "$SSH_KEY" ]] && SSH_OPTS="$SSH_OPTS -i $SSH_KEY"

RSYNC_FLAGS=(-av --progress -e "ssh $SSH_OPTS")
[[ $DRY_RUN -eq 1 ]] && RSYNC_FLAGS+=(--dry-run)

mkdir -p "$ARCHIVE_DIR" "$LOGS_DIR"

# ── 1. Risultati versionati ────────────────────────────────────────────────
echo "-- results/ --"
rsync "${RSYNC_FLAGS[@]}" \
    --include='*.csv' \
    --exclude='*' \
    "$FROM/benchmark/results/" \
    "$ARCHIVE_DIR/" || echo "  WARN: results non trovata sul remote"

# ── 2. Log per debug ───────────────────────────────────────────────────────
echo ""
echo "-- benchmark/logs/ --"
rsync "${RSYNC_FLAGS[@]}" \
    "$FROM/benchmark/logs/" \
    "$LOGS_DIR/" || echo "  WARN: logs/ non trovata sul remote"

echo ""
echo "=== Pull SSH completato ==="

if [[ $DRY_RUN -eq 0 ]]; then
    CSV_COUNT=$(find "$ARCHIVE_DIR" -name "*.csv" 2>/dev/null | wc -l | tr -d ' ')
    LOG_COUNT=$(find "$LOGS_DIR" -type f 2>/dev/null | wc -l | tr -d ' ')
    echo "  CSV in results/ : $CSV_COUNT"
    echo "  File in logs/           : $LOG_COUNT"
fi

echo ""
echo "Prossimo step:"
echo "  uv run python benchmark/aggregate_results.py --predict"
