#!/usr/bin/env bash
# Cleanup benchmark artifacts.
#
# Usage:
#   bash tests/cleanup_benchmark.sh              # clean logs and temp files only
#   bash tests/cleanup_benchmark.sh --results    # also reset results CSV (fresh start)
#   bash tests/cleanup_benchmark.sh --models     # also delete downloaded GGUF models
#   bash tests/cleanup_benchmark.sh --all        # everything (logs + results + models)

set -euo pipefail
cd "$(dirname "$0")/.."

BENCHMARK_DIR="tests/generated_files/benchmark"
MODELS_DIR="$HOME/.spendify/models"
DOCS_BENCHMARK_DIR="../documents/04_software_engineering/benchmark"

CLEAN_RESULTS=false
CLEAN_MODELS=false

for arg in "$@"; do
    case $arg in
        --results) CLEAN_RESULTS=true ;;
        --models)  CLEAN_MODELS=true ;;
        --all)     CLEAN_RESULTS=true; CLEAN_MODELS=true ;;
    esac
done

echo "============================================================"
echo "  Benchmark Cleanup"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"

# ── Step 0: save results before cleaning ──────────────────────────────────
echo ""
echo "── Saving results (commit + push) ──"

# sw_artifacts repo
if git diff --quiet "$BENCHMARK_DIR" 2>/dev/null; then
    echo "  sw_artifacts: no changes to commit"
else
    git add "$BENCHMARK_DIR"/results_all_runs.csv \
            "$BENCHMARK_DIR"/summary_*.csv \
            "$BENCHMARK_DIR"/benchmark_config.json \
            "$BENCHMARK_DIR"/cat_benchmark_config.json \
            "$BENCHMARK_DIR"/cat_results_*.csv 2>/dev/null
    git commit -m "data(benchmark): results $(date '+%Y-%m-%d %H:%M')" && {
        echo "  sw_artifacts: committed"
        git push 2>/dev/null && echo "  sw_artifacts: pushed" || echo "  sw_artifacts: push failed (manual push needed)"
    }
fi

# documents repo
if [ -d "$DOCS_BENCHMARK_DIR" ]; then
    pushd "$DOCS_BENCHMARK_DIR/.." > /dev/null
    if git diff --quiet benchmark/ 2>/dev/null; then
        echo "  documents: no changes to commit"
    else
        git add benchmark/results_all_runs.csv \
                benchmark/benchmark_config.json \
                benchmark/cat_benchmark_config.json 2>/dev/null
        git commit -m "data(benchmark): results $(date '+%Y-%m-%d %H:%M')" && {
            echo "  documents: committed"
            git push 2>/dev/null && echo "  documents: pushed" || echo "  documents: push failed (manual push needed)"
        }
    fi
    popd > /dev/null
fi

# ── Always: clean log files ───────────────────────────────────────────────
echo ""
echo "── Cleaning log files ──"
n_logs=$(find "$BENCHMARK_DIR" -name "*.log" 2>/dev/null | wc -l | tr -d ' ')
rm -f "$BENCHMARK_DIR"/*.log
echo "  Deleted $n_logs log file(s)"

# ── Always: clean backup files ────────────────────────────────────────────
n_bak=$(find "$BENCHMARK_DIR" -name "*.bak" 2>/dev/null | wc -l | tr -d ' ')
rm -f "$BENCHMARK_DIR"/*.bak
echo "  Deleted $n_bak backup file(s)"

# ── Always: kill any running benchmark processes ──────────────────────────
n_killed=0
for pid in $(pgrep -f "benchmark_pipeline\|benchmark_categorizer\|run_full_benchmark" 2>/dev/null); do
    kill "$pid" 2>/dev/null && n_killed=$((n_killed + 1))
done
if [ $n_killed -gt 0 ]; then
    echo "  Killed $n_killed running benchmark process(es)"
    sleep 2
fi

# ── Optional: reset results ───────────────────────────────────────────────
if [ "$CLEAN_RESULTS" = true ]; then
    echo ""
    echo "── Resetting results ──"
    for csv_dir in "$BENCHMARK_DIR" "$DOCS_BENCHMARK_DIR"; do
        if [ -f "$csv_dir/results_all_runs.csv" ]; then
            # Keep header only
            head -1 "$csv_dir/results_all_runs.csv" > "$csv_dir/results_all_runs.csv.tmp"
            mv "$csv_dir/results_all_runs.csv.tmp" "$csv_dir/results_all_runs.csv"
            echo "  Reset $csv_dir/results_all_runs.csv (header only)"
        fi
    done
    rm -f "$BENCHMARK_DIR"/results_run_*.csv
    rm -f "$BENCHMARK_DIR"/summary_*.csv
    rm -f "$BENCHMARK_DIR"/cat_results_*.csv
    echo "  Deleted per-run and summary CSVs"
fi

# ── Optional: delete models ──────────────────────────────────────────────
if [ "$CLEAN_MODELS" = true ]; then
    echo ""
    echo "── Deleting GGUF models ──"
    if [ -d "$MODELS_DIR" ]; then
        n_models=$(find "$MODELS_DIR" -name "*.gguf" 2>/dev/null | wc -l | tr -d ' ')
        size=$(du -sh "$MODELS_DIR" 2>/dev/null | cut -f1)
        rm -f "$MODELS_DIR"/*.gguf
        echo "  Deleted $n_models model(s) ($size freed)"
    else
        echo "  No models directory found"
    fi

    echo ""
    echo "── Removing Ollama models ──"
    if command -v ollama &>/dev/null && curl -sf http://localhost:11434/api/tags > /dev/null 2>&1; then
        for model in qwen2.5:1.5b-instruct gemma2:2b llama3.2:3b qwen2.5:3b-instruct \
                     phi3:3.8b qwen2.5:7b-instruct gemma3:12b; do
            if ollama show "$model" > /dev/null 2>&1; then
                ollama rm "$model" 2>/dev/null && echo "  Removed Ollama: $model"
            fi
        done
    else
        echo "  Ollama not running — skipping"
    fi
fi

echo ""
echo "============================================================"
echo "  Cleanup complete"
echo "============================================================"
