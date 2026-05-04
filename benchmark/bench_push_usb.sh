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

# ── Generate benchmark/.version (YYYYMMDDHHMMSS-sha7) ─────────────────────
# Written here (on dev machine, where git is available) so the bench machine
# can identify the code version without needing a .git directory.
# All model invocations on the bench machine read the same .version file
# → every CSV row gets an identical version string → monitor can filter
# by exact version to show only rows from the current bench push.
_PUSH_SHA=$(git -C "$PROJECT_ROOT" rev-parse --short HEAD 2>/dev/null || echo "unknown")
_PUSH_TS=$(date +"%Y%m%d%H%M%S")
_PUSH_VERSION="${_PUSH_TS}-${_PUSH_SHA}"
echo "${_PUSH_VERSION}" > "$SCRIPT_DIR/.version"

echo "=== bench_push_usb ==="
echo "  Source  : $PROJECT_ROOT"
echo "  Dest    : $DEST"
echo "  Version : ${_PUSH_VERSION}"
echo "  Exclude : $EXCLUDE_FILE"
[[ $CLEAN -eq 1 ]]   && echo "  Mode    : --clean (rsync --delete-excluded)"
[[ $DRY_RUN -eq 1 ]] && echo "  Mode    : --dry-run"
echo ""

mkdir -p "$DEST"

RSYNC_FLAGS=(-av --progress)
[[ $CLEAN -eq 1 ]]   && RSYNC_FLAGS+=(--delete --delete-excluded)
[[ $DRY_RUN -eq 1 ]] && RSYNC_FLAGS+=(--dry-run)

# IMPORTANTE: --include prima di --exclude-from (rsync: prima regola che fa match vince)
# - benchmark_models.csv    : incluso esplicitamente (escluso da *.csv globale)
# - generated_files/        : inclusa la cartella e tutto il contenuto diretto
# - generated_files/**      : inclusi tutti i file sintetici (*.csv, *.xlsx, manifest)
# - generated_files/benchmark/ e results_*.csv esclusi nel file esclusioni
rsync "${RSYNC_FLAGS[@]}" \
    --include='benchmark/benchmark_models.csv' \
    --include='benchmark/generated_files/' \
    --include='benchmark/generated_files/**' \
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
echo "  1) Copia dalla chiavetta al disco locale:"
echo "     Linux/macOS : bash $DEST/benchmark/bench_load_usb.sh --from $DEST"
echo "     Windows     : powershell -ExecutionPolicy Bypass -File $DEST\benchmark\bench_load_usb.ps1 -From $DEST"
echo "  2) Avvia il benchmark (già nella cartella giusta dopo bench_load_usb):"
echo "     Linux/macOS : cd ~/Desktop/spendif-ai && bash benchmark/run_benchmark_full.sh"
echo "     Windows     : Set-Location \$env:USERPROFILE\Desktop\spendif-ai; powershell -ExecutionPolicy Bypass -File benchmark\run_benchmark_full.ps1"
echo "  3) Salva i risultati sulla chiavetta:"
echo "     Linux/macOS : bash benchmark/bench_save_usb.sh --dest $DEST"
echo "     Windows     : powershell -ExecutionPolicy Bypass -File benchmark\bench_save_usb.ps1 -Dest $DEST"
echo ""
echo "Poi [dev] raccogli con:"
echo "  Linux / macOS : bash benchmark/bench_pull_usb.sh --from $DEST"
echo "  Windows       : powershell -ExecutionPolicy Bypass -File benchmark\bench_pull_usb.ps1 -From $DEST"
