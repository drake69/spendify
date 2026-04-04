#!/usr/bin/env bash
# bench_push_usb.sh — Copia il minimo indispensabile da dev → chiavetta USB
#
# Esclude automaticamente tutto ciò che è in .gitignore più:
#   .claude/, backup/, quarantine/, logs/, *.db, ui/, docker/, installer/, ...
#   (lista completa in benchmark/.rsync-bench-exclude)
#
# Uso:
#   bash benchmark/bench_push_usb.sh --dest /Volumes/BENCH_USB
#   bash benchmark/bench_push_usb.sh --dest /Volumes/BENCH_USB --clean
#   bash benchmark/bench_push_usb.sh --dest /Volumes/BENCH_USB --dry-run
#
# Opzioni:
#   --dest PATH   Percorso destinazione [obbligatorio]
#   --clean       Cancella dest prima di copiare (rsync --delete)
#   --dry-run     Mostra cosa verrebbe copiato senza farlo

set -euo pipefail

DEST=""
CLEAN=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dest)    DEST="$2"; shift 2 ;;
        --clean)   CLEAN=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        *) echo "Opzione non riconosciuta: $1"; exit 1 ;;
    esac
done

if [[ -z "$DEST" ]]; then
    echo "Uso: $0 --dest PATH [--clean] [--dry-run]"
    echo ""
    echo "  --dest PATH   Destinazione (es. /Volumes/BENCH_USB)"
    echo "  --clean       Cancella dest prima di copiare"
    echo "  --dry-run     Mostra cosa verrebbe copiato"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
EXCLUDE_FILE="$SCRIPT_DIR/.rsync-bench-exclude"

if [[ ! -f "$EXCLUDE_FILE" ]]; then
    echo "ERROR: file esclusioni non trovato: $EXCLUDE_FILE"
    exit 1
fi

echo "=== bench_push_usb ==="
echo "  Source  : $PROJECT_ROOT"
echo "  Dest    : $DEST"
echo "  Exclude : $EXCLUDE_FILE"
[[ $CLEAN -eq 1 ]]   && echo "  Mode    : --clean (rsync --delete-excluded)"
[[ $DRY_RUN -eq 1 ]] && echo "  Mode    : --dry-run"
echo ""

mkdir -p "$DEST"

RSYNC_FLAGS=(-av --progress)
[[ $CLEAN -eq 1 ]]   && RSYNC_FLAGS+=(--delete --delete-excluded)
[[ $DRY_RUN -eq 1 ]] && RSYNC_FLAGS+=(--dry-run)

# IMPORTANTE: --include prima di --exclude-from, altrimenti *.csv blocca il CSV
rsync "${RSYNC_FLAGS[@]}" \
    --include='benchmark/benchmark_models.csv' \
    --exclude-from="$EXCLUDE_FILE" \
    "$PROJECT_ROOT/" \
    "$DEST/"

echo ""
echo "=== Push completato ==="
if [[ $DRY_RUN -eq 0 ]]; then
    echo "  Dimensione dest:"
    du -sh "$DEST" 2>/dev/null | awk '{print "    " $1 "  " $2}'
fi
echo ""
echo "Sul bench esegui:"
echo "  bash benchmark/run_benchmark_full.sh"
echo ""
echo "Poi raccogli con:"
echo "  bash benchmark/bench_pull_usb.sh --from $DEST"
