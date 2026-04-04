#!/usr/bin/env bash
# bench_pull_usb.sh — Raccoglie risultati e log del benchmark dalla chiavetta → dev
#
# Cosa viene copiato:
#   tests/results_archive/*.csv   → CSV versionati <version>_<hostname>.csv
#   tests/logs/                   → log per debug
#
# Uso:
#   bash benchmark/bench_pull_usb.sh --from /Volumes/BENCH_USB
#   bash benchmark/bench_pull_usb.sh --from /Volumes/BENCH_USB --dry-run
#
# Opzioni:
#   --from PATH   Sorgente (chiavetta) [obbligatorio]
#   --dry-run     Mostra cosa verrebbe copiato senza farlo

set -euo pipefail

FROM=""
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --from)    FROM="$2"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        *) echo "Opzione non riconosciuta: $1"; exit 1 ;;
    esac
done

if [[ -z "$FROM" ]]; then
    echo "Uso: $0 --from PATH [--dry-run]"
    echo ""
    echo "  --from PATH   Sorgente (es. /Volumes/BENCH_USB)"
    echo "  --dry-run     Mostra cosa verrebbe copiato"
    exit 1
fi

if [[ ! -d "$FROM" ]]; then
    echo "ERROR: sorgente non trovata: $FROM"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ARCHIVE_DIR="$PROJECT_ROOT/tests/results_archive"
LOGS_DIR="$PROJECT_ROOT/tests/logs"

echo "=== bench_pull_usb ==="
echo "  From  : $FROM"
echo "  Dest  : $PROJECT_ROOT"
[[ $DRY_RUN -eq 1 ]] && echo "  Mode  : --dry-run"
echo ""

RSYNC_FLAGS=(-av --progress)
[[ $DRY_RUN -eq 1 ]] && RSYNC_FLAGS+=(--dry-run)

mkdir -p "$ARCHIVE_DIR" "$LOGS_DIR"

# ── 1. Risultati versionati ────────────────────────────────────────────────
SRC_ARCHIVE="$FROM/tests/results_archive/"
echo "-- results_archive/ --"
if [[ -d "$SRC_ARCHIVE" ]]; then
    rsync "${RSYNC_FLAGS[@]}" \
        --include='*.csv' \
        --exclude='*' \
        "$SRC_ARCHIVE" \
        "$ARCHIVE_DIR/"
else
    echo "  WARN: $SRC_ARCHIVE non trovata"
fi

# ── 2. Log per debug ───────────────────────────────────────────────────────
SRC_LOGS="$FROM/tests/logs/"
echo ""
echo "-- tests/logs/ --"
if [[ -d "$SRC_LOGS" ]]; then
    rsync "${RSYNC_FLAGS[@]}" \
        "$SRC_LOGS" \
        "$LOGS_DIR/"
else
    echo "  WARN: $SRC_LOGS non trovata"
fi

echo ""
echo "=== Pull completato ==="

if [[ $DRY_RUN -eq 0 ]]; then
    CSV_COUNT=$(find "$ARCHIVE_DIR" -name "*.csv" 2>/dev/null | wc -l | tr -d ' ')
    LOG_COUNT=$(find "$LOGS_DIR" -type f 2>/dev/null | wc -l | tr -d ' ')
    echo "  CSV in results_archive/ : $CSV_COUNT"
    echo "  File in logs/           : $LOG_COUNT"
fi

echo ""
echo "Prossimo step:"
echo "  uv run python tests/aggregate_results.py --predict"
