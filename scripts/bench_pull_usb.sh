#!/usr/bin/env bash
# bench_pull_usb.sh — Raccoglie i risultati del benchmark dalla chiavetta USB → dev
#
# Cosa viene copiato (solo i risultati prodotti dal benchmark):
#   tests/results_archive/*.csv   → file versionati <version>_<hostname>.csv
#   tests/.version                → versione aggiornata dal bench (se cambiata)
#   tests/generated_files/benchmark/results_all_runs.csv  → legacy, se esiste
#
# Dopo il pull, esegui:
#   python tests/aggregate_results.py
#
# Uso:
#   bash scripts/bench_pull_usb.sh --from /Volumes/BENCH_USB
#   bash scripts/bench_pull_usb.sh --from /Volumes/BENCH_USB --dry-run
#
# Opzioni:
#   --from PATH   Percorso sorgente (chiavetta) [obbligatorio]
#   --dry-run     Mostra cosa verrebbe copiato senza farlo

set -euo pipefail

FROM=""
DRY_RUN=0

# ── Argomenti ─────────────────────────────────────────────────────────────
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
    echo "  --from PATH    Sorgente (es. /Volumes/BENCH_USB)"
    echo "  --dry-run      Mostra cosa verrebbe copiato"
    exit 1
fi

if [[ ! -d "$FROM" ]]; then
    echo "ERROR: sorgente non trovata: $FROM"
    exit 1
fi

# ── Risolvi root progetto ──────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ARCHIVE_DIR="$PROJECT_ROOT/tests/results_archive"
BENCH_DIR="$PROJECT_ROOT/tests/generated_files/benchmark"

echo "=== bench_pull_usb ==="
echo "  From   : $FROM"
echo "  Dest   : $PROJECT_ROOT"
[[ $DRY_RUN -eq 1 ]] && echo "  Mode   : --dry-run"
echo ""

RSYNC_FLAGS=(-av --progress)
[[ $DRY_RUN -eq 1 ]] && RSYNC_FLAGS+=(--dry-run)

mkdir -p "$ARCHIVE_DIR" "$BENCH_DIR"

# ── 1. Risultati versionati (results_archive/) ─────────────────────────────
SRC_ARCHIVE="$FROM/tests/results_archive/"
if [[ -d "$SRC_ARCHIVE" ]]; then
    echo "-- Raccolta results_archive/ --"
    rsync "${RSYNC_FLAGS[@]}" \
        --include='*.csv' \
        --exclude='*' \
        "$SRC_ARCHIVE" \
        "$ARCHIVE_DIR/"
else
    echo "WARN: $SRC_ARCHIVE non trovata (nessun risultato versionato)"
fi

# ── 2. Legacy results_all_runs.csv ─────────────────────────────────────────
SRC_LEGACY="$FROM/tests/generated_files/benchmark/results_all_runs.csv"
if [[ -f "$SRC_LEGACY" ]]; then
    echo ""
    echo "-- Raccolta results_all_runs.csv (legacy) --"
    rsync "${RSYNC_FLAGS[@]}" \
        "$SRC_LEGACY" \
        "$BENCH_DIR/"
fi

# ── 3. .version aggiornato sul bench ──────────────────────────────────────
SRC_VERSION="$FROM/tests/.version"
if [[ -f "$SRC_VERSION" ]]; then
    echo ""
    echo "-- Aggiornamento .version --"
    rsync "${RSYNC_FLAGS[@]}" \
        "$SRC_VERSION" \
        "$PROJECT_ROOT/tests/.version"
fi

echo ""
echo "=== Pull completato ==="

# Mostra i file raccolti
NEW_CSVS=()
while IFS= read -r -d '' f; do
    NEW_CSVS+=("$f")
done < <(find "$ARCHIVE_DIR" -name "*.csv" -print0 2>/dev/null | sort -z)

if [[ ${#NEW_CSVS[@]} -gt 0 ]]; then
    echo "  CSV in results_archive/ (${#NEW_CSVS[@]} totali):"
    for f in "${NEW_CSVS[@]}"; do
        sz="$(du -sh "$f" 2>/dev/null | cut -f1)"
        printf "    %-8s %s\n" "$sz" "$(basename "$f")"
    done
fi

echo ""
echo "Prossimo step: aggrega i risultati con:"
echo "  python tests/aggregate_results.py --predict"
