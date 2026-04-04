#!/usr/bin/env bash
# Benchmark progress monitor — macOS / Linux
#
# Usage:
#   bash tests/monitor_benchmark.sh                # aggiorna ogni 60s
#   bash tests/monitor_benchmark.sh --interval 30  # ogni 30s
#   bash tests/monitor_benchmark.sh --once         # snapshot singolo
#   bash tests/monitor_benchmark.sh --runs 3       # se lanciato con --runs 3
#   bash tests/monitor_benchmark.sh --all          # tutta la storia

set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON=".venv/bin/python"

if [ ! -f "$PYTHON" ]; then
    echo "[ERROR] .venv non trovato. Esegui prima: bash tests/run_benchmark.sh"
    exit 1
fi

exec $PYTHON tests/monitor_benchmark.py "$@"
