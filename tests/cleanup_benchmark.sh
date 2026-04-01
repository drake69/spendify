#!/usr/bin/env bash
# Cleanup benchmark artifacts.
#
# Usage:
#   bash tests/cleanup_benchmark.sh              # save + clean logs only
#   bash tests/cleanup_benchmark.sh --results    # + reset results CSV
#   bash tests/cleanup_benchmark.sh --models     # + delete GGUF + Ollama models
#   bash tests/cleanup_benchmark.sh --all        # nuke everything except this script

set -euo pipefail
cd "$(dirname "$0")/.."

BENCHMARK_DIR="tests/generated_files/benchmark"
MODELS_DIR="$HOME/.spendify/models"
DOCS_BENCHMARK_DIR="../documents/04_software_engineering/benchmark"

CLEAN_RESULTS=false
CLEAN_MODELS=false
CLEAN_ALL=false

for arg in "$@"; do
    case $arg in
        --results) CLEAN_RESULTS=true ;;
        --models)  CLEAN_MODELS=true ;;
        --all)     CLEAN_ALL=true; CLEAN_RESULTS=true; CLEAN_MODELS=true ;;
    esac
done

echo "============================================================"
echo "  Benchmark Cleanup"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"

# ── Step 0: kill running benchmarks ───────────────────────────────────────
echo ""
echo "── Stopping running benchmarks ──"
n_killed=0
for pid in $(pgrep -f "benchmark_pipeline\|benchmark_categorizer\|run_full_benchmark" 2>/dev/null); do
    kill "$pid" 2>/dev/null && n_killed=$((n_killed + 1))
done
[ $n_killed -gt 0 ] && echo "  Killed $n_killed process(es)" && sleep 2 || echo "  None running"

# ── Step 1: save results (commit + push) ──────────────────────────────────
echo ""
echo "── Saving results (commit + push) ──"

# sw_artifacts repo
if [ -f "$BENCHMARK_DIR/results_all_runs.csv" ]; then
    git add "$BENCHMARK_DIR"/results_all_runs.csv \
            "$BENCHMARK_DIR"/summary_*.csv \
            "$BENCHMARK_DIR"/benchmark_config.json \
            "$BENCHMARK_DIR"/cat_benchmark_config.json \
            "$BENCHMARK_DIR"/cat_results_*.csv 2>/dev/null || true
    if ! git diff --cached --quiet 2>/dev/null; then
        git commit -m "data(benchmark): results $(date '+%Y-%m-%d %H:%M')" && {
            echo "  sw_artifacts: committed"
            git push 2>/dev/null && echo "  sw_artifacts: pushed" || echo "  sw_artifacts: push failed (manual push needed)"
        }
    else
        echo "  sw_artifacts: no changes to commit"
    fi
fi

# documents repo
if [ -d "$DOCS_BENCHMARK_DIR" ]; then
    pushd "$DOCS_BENCHMARK_DIR/../.." > /dev/null
    git add 04_software_engineering/benchmark/results_all_runs.csv \
            04_software_engineering/benchmark/benchmark_config.json \
            04_software_engineering/benchmark/cat_benchmark_config.json 2>/dev/null || true
    if ! git diff --cached --quiet 2>/dev/null; then
        git commit -m "data(benchmark): results $(date '+%Y-%m-%d %H:%M')" && {
            echo "  documents: committed"
            git push 2>/dev/null && echo "  documents: pushed" || echo "  documents: push failed (manual push needed)"
        }
    fi
    popd > /dev/null
fi

# ── Step 2: clean logs and temp files ─────────────────────────────────────
echo ""
echo "── Cleaning logs and temp files ──"
n_logs=$(find "$BENCHMARK_DIR" -name "*.log" 2>/dev/null | wc -l | tr -d ' ')
rm -f "$BENCHMARK_DIR"/*.log
n_bak=$(find "$BENCHMARK_DIR" -name "*.bak" 2>/dev/null | wc -l | tr -d ' ')
rm -f "$BENCHMARK_DIR"/*.bak
echo "  Deleted $n_logs log(s), $n_bak backup(s)"

# ── Step 3: reset results ─────────────────────────────────────────────────
if [ "$CLEAN_RESULTS" = true ]; then
    echo ""
    echo "── Resetting results ──"
    for csv_dir in "$BENCHMARK_DIR" "$DOCS_BENCHMARK_DIR"; do
        if [ -f "$csv_dir/results_all_runs.csv" ]; then
            head -1 "$csv_dir/results_all_runs.csv" > "$csv_dir/results_all_runs.csv.tmp"
            mv "$csv_dir/results_all_runs.csv.tmp" "$csv_dir/results_all_runs.csv"
            echo "  Reset $csv_dir/results_all_runs.csv"
        fi
    done
    rm -f "$BENCHMARK_DIR"/results_run_*.csv
    rm -f "$BENCHMARK_DIR"/summary_*.csv
    rm -f "$BENCHMARK_DIR"/cat_results_*.csv
    echo "  Deleted per-run and summary CSVs"
fi

# ── Step 4: delete models ─────────────────────────────────────────────────
if [ "$CLEAN_MODELS" = true ]; then
    echo ""
    echo "── Deleting GGUF models ──"
    if [ -d "$MODELS_DIR" ]; then
        n_models=$(find "$MODELS_DIR" -name "*.gguf" 2>/dev/null | wc -l | tr -d ' ')
        size=$(du -sh "$MODELS_DIR" 2>/dev/null | cut -f1 || echo "?")
        rm -f "$MODELS_DIR"/*.gguf
        echo "  Deleted $n_models model(s) ($size freed)"
    fi

    echo ""
    echo "── Removing Ollama models ──"
    if command -v ollama &>/dev/null && curl -sf http://localhost:11434/api/tags > /dev/null 2>&1; then
        for model in qwen2.5:1.5b-instruct gemma2:2b llama3.2:3b qwen2.5:3b-instruct \
                     phi3:3.8b qwen2.5:7b-instruct gemma3:12b; do
            ollama rm "$model" 2>/dev/null && echo "  Removed $model" || true
        done
    else
        echo "  Ollama not running — skipping"
    fi
fi

# ── Step 5: nuke everything (--all) ───────────────────────────────────────
if [ "$CLEAN_ALL" = true ]; then
    echo ""
    echo "── Removing venv, generated files, caches ──"
    rm -rf .venv __pycache__ core/__pycache__ tests/__pycache__
    rm -rf tests/generated_files/benchmark/*.json
    rm -rf tests/generated_files/*.csv tests/generated_files/*.xlsx
    rm -f ledger.db
    echo "  Removed .venv, __pycache__, generated files, ledger.db"
    echo ""
    echo "  Only remaining: source code + this cleanup script"
    echo "  To restart: bash run_full_benchmark.sh"
fi

echo ""
echo "============================================================"
echo "  Cleanup complete"
echo "============================================================"
