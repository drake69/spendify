#!/usr/bin/env bash
# bench_push_usb.sh — Copia il minimo indispensabile da dev → chiavetta USB
#
# Cosa viene copiato (solo il necessario per girare il benchmark):
#   tests/benchmark_pipeline.py
#   tests/benchmark_categorizer.py
#   tests/run_benchmark_full.sh / .ps1
#   tests/benchmark_models.csv
#   tests/.version
#   tests/generated_files/  (file sintetici pre-generati)
#   core/                   (business logic)
#   services/               (service layer)
#   db/                     (ORM + repository)
#   support/                (utilities)
#   pyproject.toml + uv.lock
#
# Cosa NON viene copiato:
#   tests/results_archive/  (rimane sul bench, si raccoglie con bench_pull_usb.sh)
#   tests/generated_files/benchmark/  (output prodotto dal bench)
#   .git/                   (non serve su macchine senza git)
#   ui/                     (non usata dal benchmark)
#   docs/                   (non serve)
#
# Uso:
#   bash scripts/bench_push_usb.sh --dest /Volumes/BENCH_USB
#   bash scripts/bench_push_usb.sh --dest /Volumes/BENCH_USB --clean
#
# Opzioni:
#   --dest PATH   Percorso destinazione (chiavetta o cartella) [obbligatorio]
#   --clean       Cancella il dest prima di copiare (rsync --delete)
#   --dry-run     Mostra cosa verrebbe copiato senza farlo

set -euo pipefail

DEST=""
CLEAN=0
DRY_RUN=0

# ── Argomenti ─────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dest)   DEST="$2"; shift 2 ;;
        --clean)  CLEAN=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        *) echo "Opzione non riconosciuta: $1"; exit 1 ;;
    esac
done

if [[ -z "$DEST" ]]; then
    echo "Uso: $0 --dest PATH [--clean] [--dry-run]"
    echo ""
    echo "  --dest PATH    Destinazione (es. /Volumes/BENCH_USB)"
    echo "  --clean        Cancella dest prima di copiare"
    echo "  --dry-run      Mostra cosa verrebbe copiato"
    exit 1
fi

# ── Risolvi root progetto ──────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=== bench_push_usb ==="
echo "  Source : $PROJECT_ROOT"
echo "  Dest   : $DEST"
[[ $CLEAN -eq 1 ]]   && echo "  Mode   : --clean (rsync --delete)"
[[ $DRY_RUN -eq 1 ]] && echo "  Mode   : --dry-run"
echo ""

# ── Flags rsync ────────────────────────────────────────────────────────────
RSYNC_FLAGS=(-av --progress)
[[ $CLEAN -eq 1 ]]   && RSYNC_FLAGS+=(--delete)
[[ $DRY_RUN -eq 1 ]] && RSYNC_FLAGS+=(--dry-run)

# ── Esclusioni rsync ───────────────────────────────────────────────────────
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

mkdir -p "$DEST"

# ── Copia ──────────────────────────────────────────────────────────────────
rsync "${RSYNC_FLAGS[@]}" "${EXCLUDES[@]}" \
    "$PROJECT_ROOT/" \
    "$DEST/"

echo ""
echo "=== Push completato ==="
echo "  Contenuto $DEST:"
du -sh "$DEST"/* 2>/dev/null | sort -h | while read -r size path; do
    printf "  %-8s %s\n" "$size" "$(basename "$path")"
done
echo ""
echo "Prossimo step: sul bench esegui"
echo "  bash tests/run_benchmark_full.sh"
echo "  (o .ps1 su Windows)"
echo ""
echo "Poi raccogli i risultati con:"
echo "  bash scripts/bench_pull_usb.sh --from $DEST"
