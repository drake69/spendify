#!/usr/bin/env bash
# bench_save_usb.sh — Copia i risultati del benchmark dal bench → chiavetta USB
#
# Da eseguire SUL BENCH dopo aver completato il benchmark.
# Copia benchmark/results/ e benchmark/logs/ sulla chiavetta,
# poi dalla dev si raccolgono con bench_pull_usb.sh.
#
# Flusso completo USB:
#   [dev]   bash benchmark/bench_push_usb.sh --dest /Volumes/BENCH_USB
#   [bench] cp -r /Volumes/BENCH_USB ~/spendif && cd ~/spendif
#   [bench] bash benchmark/run_benchmark_full.sh
#   [bench] bash benchmark/bench_save_usb.sh --dest /Volumes/BENCH_USB  ← questo script
#   [dev]   bash benchmark/bench_pull_usb.sh --from /Volumes/BENCH_USB
#
# Uso:
#   bash benchmark/bench_save_usb.sh --dest /Volumes/BENCH_USB
#   bash benchmark/bench_save_usb.sh --dest /Volumes/BENCH_USB --dry-run
#
# Opzioni:
#   --dest PATH   Percorso chiavetta [obbligatorio]
#   --dry-run     Mostra cosa verrebbe copiato senza farlo

set -euo pipefail
[ -z "${BASH_VERSION:-}" ] && exec bash "$0" "$@"

DEST=""
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dest)    DEST="$2"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        *) echo "Opzione non riconosciuta: $1"; exit 1 ;;
    esac
done

if [[ -z "$DEST" ]]; then
    echo "Uso: $0 --dest PATH [--dry-run]"
    echo ""
    echo "  --dest PATH   Chiavetta o cartella destinazione (es. /Volumes/BENCH_USB)"
    echo "  --dry-run     Mostra cosa verrebbe copiato"
    exit 1
fi

if [[ ! -d "$DEST" ]]; then
    echo "ERROR: destinazione non trovata: $DEST"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RESULTS_DIR="$PROJECT_ROOT/benchmark/results"
LOGS_DIR="$PROJECT_ROOT/benchmark/logs"

echo "=== bench_save_usb ==="
echo "  From  : $PROJECT_ROOT"
echo "  Dest  : $DEST"
[[ $DRY_RUN -eq 1 ]] && echo "  Mode  : --dry-run"
echo ""

RSYNC_FLAGS=(-av --progress)
[[ $DRY_RUN -eq 1 ]] && RSYNC_FLAGS+=(--dry-run)

# ── 1. Risultati ────────────────────────────────────────────────────────────
DEST_RESULTS="$DEST/benchmark/results"
echo "-- benchmark/results/ --"
if [[ -d "$RESULTS_DIR" ]] && compgen -G "$RESULTS_DIR/*.csv" > /dev/null 2>&1; then
    mkdir -p "$DEST_RESULTS"
    rsync "${RSYNC_FLAGS[@]}" \
        --include='*.csv' \
        --exclude='*' \
        "$RESULTS_DIR/" \
        "$DEST_RESULTS/"
else
    echo "  WARN: nessun CSV trovato in $RESULTS_DIR"
    echo "  Hai eseguito il benchmark? (bash benchmark/run_benchmark_full.sh)"
fi

# ── 2. Log ──────────────────────────────────────────────────────────────────
DEST_LOGS="$DEST/benchmark/logs"
echo ""
echo "-- benchmark/logs/ --"
if [[ -d "$LOGS_DIR" ]]; then
    mkdir -p "$DEST_LOGS"
    rsync "${RSYNC_FLAGS[@]}" \
        "$LOGS_DIR/" \
        "$DEST_LOGS/"
else
    echo "  WARN: $LOGS_DIR non trovata"
fi

echo ""
echo "=== Salvataggio completato ==="

if [[ $DRY_RUN -eq 0 ]]; then
    CSV_COUNT=$(find "$DEST_RESULTS" -name "*.csv" 2>/dev/null | wc -l | tr -d ' ')
    LOG_COUNT=$(find "$DEST_LOGS" -type f 2>/dev/null | wc -l | tr -d ' ')
    echo "  CSV salvati : $CSV_COUNT"
    echo "  Log salvati : $LOG_COUNT"
fi

echo ""
echo "Ora sulla dev esegui:"
echo "  bash benchmark/bench_pull_usb.sh --from $DEST"
echo ""
