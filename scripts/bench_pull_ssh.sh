#!/usr/bin/env bash
# bench_pull_ssh.sh — Raccoglie i risultati del benchmark da host remoto → dev via SSH
#
# Uso:
#   bash scripts/bench_pull_ssh.sh --from user@bench-host:/home/user/spendif
#   bash scripts/bench_pull_ssh.sh --from user@192.168.1.50:~/spendif --dry-run
#
# Opzioni:
#   --from HOST:PATH   Sorgente SSH (user@host:path) [obbligatorio]
#   --dry-run          Mostra cosa verrebbe copiato senza farlo
#   --key PATH         Chiave SSH da usare (default: ~/.ssh/id_rsa)
#   --port N           Porta SSH (default: 22)

set -euo pipefail

FROM=""
DRY_RUN=0
SSH_KEY=""
SSH_PORT=22

# ── Argomenti ─────────────────────────────────────────────────────────────
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
ARCHIVE_DIR="$PROJECT_ROOT/tests/results_archive"
BENCH_DIR="$PROJECT_ROOT/tests/generated_files/benchmark"

echo "=== bench_pull_ssh ==="
echo "  From   : $FROM"
echo "  Dest   : $PROJECT_ROOT"
[[ $DRY_RUN -eq 1 ]] && echo "  Mode   : --dry-run"
echo ""

SSH_OPTS="-p $SSH_PORT -o StrictHostKeyChecking=accept-new"
[[ -n "$SSH_KEY" ]] && SSH_OPTS="$SSH_OPTS -i $SSH_KEY"

RSYNC_FLAGS=(-av --progress -e "ssh $SSH_OPTS")
[[ $DRY_RUN -eq 1 ]] && RSYNC_FLAGS+=(--dry-run)

mkdir -p "$ARCHIVE_DIR" "$BENCH_DIR"

# ── 1. Risultati versionati ────────────────────────────────────────────────
echo "-- Raccolta results_archive/ --"
rsync "${RSYNC_FLAGS[@]}" \
    --include='*.csv' \
    --exclude='*' \
    "$FROM/tests/results_archive/" \
    "$ARCHIVE_DIR/" || echo "WARN: results_archive non trovata sul remote"

# ── 2. Legacy results_all_runs.csv ─────────────────────────────────────────
echo ""
echo "-- Raccolta results_all_runs.csv (legacy) --"
rsync "${RSYNC_FLAGS[@]}" \
    "$FROM/tests/generated_files/benchmark/results_all_runs.csv" \
    "$BENCH_DIR/" 2>/dev/null || echo "WARN: results_all_runs.csv non trovato"

# ── 3. .version ───────────────────────────────────────────────────────────
echo ""
echo "-- Aggiornamento .version --"
rsync "${RSYNC_FLAGS[@]}" \
    "$FROM/tests/.version" \
    "$PROJECT_ROOT/tests/.version" 2>/dev/null || echo "WARN: .version non trovato"

echo ""
echo "=== Pull SSH completato ==="

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
echo "Prossimo step:"
echo "  python tests/aggregate_results.py --predict"
