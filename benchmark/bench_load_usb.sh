#!/usr/bin/env bash
# bench_load_usb.sh — Copia il progetto dalla chiavetta USB → disco locale del bench
#
# Da eseguire SUL BENCH prima di avviare il benchmark.
# Copia tutto il necessario dalla chiavetta a una cartella locale,
# così il benchmark gira su disco veloce anziché su USB.
#
# Flusso completo USB:
#   [dev]   bash benchmark/bench_push_usb.sh --dest /Volumes/BENCH_USB
#   [bench] bash /Volumes/BENCH_USB/benchmark/bench_load_usb.sh --from /Volumes/BENCH_USB
#   [bench] cd ~/Desktop/spendif-ai && bash benchmark/run_benchmark_full.sh
#   [bench] bash benchmark/bench_save_usb.sh --dest /Volumes/BENCH_USB
#   [dev]   bash benchmark/bench_pull_usb.sh --from /Volumes/BENCH_USB
#
# Uso:
#   bash /MOUNT/benchmark/bench_load_usb.sh --from /Volumes/BENCH_USB
#   bash /MOUNT/benchmark/bench_load_usb.sh --from /Volumes/BENCH_USB --local ~/Desktop/spendif-ai
#   bash /MOUNT/benchmark/bench_load_usb.sh --from /Volumes/BENCH_USB --dry-run
#
# Opzioni:
#   --from PATH    Sorgente (chiavetta montata) [obbligatorio]
#   --local PATH   Cartella locale destinazione  (default: ~/Desktop/spendif-ai)
#   --dry-run      Mostra cosa verrebbe copiato senza farlo

set -euo pipefail
[ -z "${BASH_VERSION:-}" ] && exec bash "$0" "$@"

FROM=""
LOCAL_DIR="$HOME/Desktop/spendif-ai"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --from)    FROM="$2";      shift 2 ;;
        --local)   LOCAL_DIR="$2"; shift 2 ;;
        --dry-run) DRY_RUN=1;      shift ;;
        *) echo "Opzione non riconosciuta: $1"; exit 1 ;;
    esac
done

if [[ -z "$FROM" ]]; then
    echo "Uso: $0 --from PATH [--local PATH] [--dry-run]"
    echo ""
    echo "  --from PATH    Chiavetta montata (es. /Volumes/BENCH_USB, /media/usb)"
    echo "  --local PATH   Cartella locale bench  (default: ~/Desktop/spendif-ai)"
    echo "  --dry-run      Mostra cosa verrebbe copiato"
    exit 1
fi

if [[ ! -d "$FROM" ]]; then
    echo "ERROR: sorgente non trovata: $FROM"
    exit 1
fi

echo "=== bench_load_usb ==="
echo "  From  : $FROM"
echo "  Local : $LOCAL_DIR"
[[ $DRY_RUN -eq 1 ]] && echo "  Mode  : --dry-run"
echo ""

RSYNC_FLAGS=(-av --progress)
[[ $DRY_RUN -eq 1 ]] && RSYNC_FLAGS+=(--dry-run)

mkdir -p "$LOCAL_DIR"

# Copia tutto tranne:
# - benchmark/results/  (output del bench, non servono in ingresso)
# - benchmark/logs/     (log del bench, non servono in ingresso)
rsync "${RSYNC_FLAGS[@]}" \
    --exclude='benchmark/results/' \
    --exclude='benchmark/logs/' \
    --exclude='.git/' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    "$FROM/" \
    "$LOCAL_DIR/"

echo ""
echo "=== Caricamento completato ==="
if [[ $DRY_RUN -eq 0 ]]; then
    echo "  Dimensione locale:"
    du -sh "$LOCAL_DIR" 2>/dev/null | awk '{print "    " $1 "  " $2}'
fi
echo ""
echo "Ora avvia il benchmark:"
echo "  cd $LOCAL_DIR"
echo "  Linux/macOS : bash benchmark/run_benchmark_full.sh"
echo "  Windows     : powershell -ExecutionPolicy Bypass -File benchmark\run_benchmark_full.ps1"
echo ""
echo "Al termine salva i risultati sulla chiavetta:"
echo "  Linux/macOS : bash benchmark/bench_save_usb.sh --dest $FROM"
echo "  Windows     : powershell -ExecutionPolicy Bypass -File benchmark\bench_save_usb.ps1 -Dest $FROM"
echo ""
