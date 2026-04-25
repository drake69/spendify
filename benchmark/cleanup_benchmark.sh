#!/usr/bin/env bash
# Cleanup benchmark artifacts, models, venv and generated files.
# Reads model list from benchmark/benchmark_models.csv (no hardcoded names).
#
# Livelli (cumulativi):
#   (default)    — salva risultati + pulisce log, .pyc, __pycache__
#   --results    — + reset results_all_runs.csv (mantiene solo header)
#   --models     — + cancella GGUF da ~/.spendifai/models/ + ollama rm
#   --generated  — + cancella file sintetici (benchmark/generated_files/)
#   --venv       — + cancella .venv
#   --all        — tutto quanto (equiv. a tutti i flag sopra)
#   --dry-run    — mostra cosa verrebbe fatto senza eseguire
#
# Usage:
#   bash benchmark/cleanup_benchmark.sh                # soft cleanup
#   bash benchmark/cleanup_benchmark.sh --models       # + rimuovi modelli
#   bash benchmark/cleanup_benchmark.sh --all          # full reset
#   bash benchmark/cleanup_benchmark.sh --all --dry-run

set -euo pipefail
cd "$(dirname "$0")/.."

BENCHMARK_DIR="benchmark/results"
MODELS_DIR="$HOME/.spendifai/models"
MODELS_CSV="benchmark/benchmark_models.csv"
DOCS_BENCHMARK_DIR="../documents/04_software_engineering/benchmark"

CLEAN_RESULTS=false
CLEAN_MODELS=false
CLEAN_GENERATED=false
CLEAN_VENV=false
DRY_RUN=false

for arg in "$@"; do
    case $arg in
        --results)   CLEAN_RESULTS=true ;;
        --models)    CLEAN_MODELS=true ;;
        --generated) CLEAN_GENERATED=true ;;
        --venv)      CLEAN_VENV=true ;;
        --all)       CLEAN_RESULTS=true; CLEAN_MODELS=true
                     CLEAN_GENERATED=true; CLEAN_VENV=true ;;
        --dry-run)   DRY_RUN=true ;;
    esac
done

_run() {
    if [ "$DRY_RUN" = true ]; then
        echo "  [dry-run] $*"
    else
        "$@"
    fi
}

echo "════════════════════════════════════════════════════════════"
echo "  BENCHMARK CLEANUP  —  $(date '+%Y-%m-%d %H:%M:%S')"
[ "$DRY_RUN" = true ] && echo "  MODE: DRY RUN (nessuna modifica)"
echo "════════════════════════════════════════════════════════════"

# ── Step 0: ferma benchmark in corso ─────────────────────────────────────
echo ""
echo "── [0] Stopping running benchmarks..."
n_killed=0
for pid in $(pgrep -f "benchmark_classifier\|benchmark_categorizer\|run_benchmark_full\|run_all_benchmarks" 2>/dev/null || true); do
    _run kill "$pid" 2>/dev/null && n_killed=$((n_killed + 1))
done
if [ $n_killed -gt 0 ]; then
    echo "  Killed $n_killed process(es)"
    [ "$DRY_RUN" = false ] && sleep 2
else
    echo "  None running"
fi

# ── Step 1: salva risultati (commit + push) ───────────────────────────────
echo ""
echo "── [1] Saving results..."
if [ "$DRY_RUN" = false ] && [ -f "$BENCHMARK_DIR/results_all_runs.csv" ]; then
    git add "$BENCHMARK_DIR"/results_all_runs.csv \
            "$BENCHMARK_DIR"/summary_*.csv \
            "$BENCHMARK_DIR"/benchmark_config.json \
            "$BENCHMARK_DIR"/cat_benchmark_config.json 2>/dev/null || true
    if ! git diff --cached --quiet 2>/dev/null; then
        git commit -m "data(benchmark): results $(date '+%Y-%m-%d %H:%M')" && {
            echo "  Committed"
            git push 2>/dev/null && echo "  Pushed" || echo "  Push failed — esegui: git push"
        }
    else
        echo "  No changes to commit"
    fi
    # documents repo (se esiste)
    if [ -d "$DOCS_BENCHMARK_DIR" ]; then
        pushd "$DOCS_BENCHMARK_DIR/../.." > /dev/null
        git add 04_software_engineering/benchmark/ 2>/dev/null || true
        if ! git diff --cached --quiet 2>/dev/null; then
            git commit -m "data(benchmark): results $(date '+%Y-%m-%d %H:%M')" && git push 2>/dev/null || true
            echo "  documents repo: committed + pushed"
        fi
        popd > /dev/null
    fi
else
    echo "  [skip] No results to save (dry-run or CSV not found)"
fi

# ── Step 2: log, .pyc, __pycache__ ───────────────────────────────────────
echo ""
echo "── [2] Cleaning logs and caches..."

# Logs
n_logs=$(find benchmark/logs -name "*.log" 2>/dev/null | wc -l | tr -d ' ')
if [ "$n_logs" -gt 0 ]; then
    _run rm -f benchmark/logs/*.log
    echo "  Deleted $n_logs log file(s) from benchmark/logs/"
fi
n_blogs=$(find "$BENCHMARK_DIR" -name "*.log" 2>/dev/null | wc -l | tr -d ' ')
n_bak=$(find "$BENCHMARK_DIR" -name "*.bak" 2>/dev/null | wc -l | tr -d ' ')
[ "$n_blogs" -gt 0 ] && _run rm -f "$BENCHMARK_DIR"/*.log
[ "$n_bak"   -gt 0 ] && _run rm -f "$BENCHMARK_DIR"/*.bak
[ "$((n_blogs + n_bak))" -gt 0 ] && echo "  Deleted $n_blogs benchmark log(s), $n_bak backup(s)"

# Python caches
n_pycache=$(find . -type d -name "__pycache__" -not -path "./.venv/*" 2>/dev/null | wc -l | tr -d ' ')
if [ "$n_pycache" -gt 0 ]; then
    _run find . -type d -name "__pycache__" -not -path "./.venv/*" -exec rm -rf {} + 2>/dev/null || true
    _run find . -name "*.pyc" -not -path "./.venv/*" -delete 2>/dev/null || true
    echo "  Removed $n_pycache __pycache__ dir(s) + .pyc files"
fi

echo "  [ok]"

# ── Step 3: reset results ─────────────────────────────────────────────────
if [ "$CLEAN_RESULTS" = true ]; then
    echo ""
    echo "── [3] Resetting benchmark results..."
    for dir in "$BENCHMARK_DIR" "$DOCS_BENCHMARK_DIR"; do
        csv="$dir/results_all_runs.csv"
        [ -f "$csv" ] || continue
        rows=$(( $(wc -l < "$csv") - 1 ))
        if [ "$DRY_RUN" = false ]; then
            head -1 "$csv" > "$csv.tmp" && mv "$csv.tmp" "$csv"
        fi
        echo "  Reset $csv ($rows rows deleted)"
    done
    _run rm -f "$BENCHMARK_DIR"/results_run_*.csv
    _run rm -f "$BENCHMARK_DIR"/summary_*.csv
    _run rm -f "$BENCHMARK_DIR"/cat_results_*.csv
    echo "  Deleted per-run and summary CSVs"
fi

# ── Step 4: modelli ───────────────────────────────────────────────────────
if [ "$CLEAN_MODELS" = true ]; then
    echo ""
    echo "── [4a] Deleting GGUF models ($MODELS_DIR)..."
    if [ -d "$MODELS_DIR" ]; then
        n_gguf=$(find "$MODELS_DIR" -name "*.gguf" 2>/dev/null | wc -l | tr -d ' ')
        size=$(du -sh "$MODELS_DIR"/*.gguf 2>/dev/null | awk '{s+=$1} END {print s"M"}' || echo "?")
        if [ "$n_gguf" -gt 0 ]; then
            _run rm -f "$MODELS_DIR"/*.gguf
            echo "  Deleted $n_gguf GGUF file(s)"
        else
            echo "  No GGUF files found"
        fi
    else
        echo "  $MODELS_DIR not found — skip"
    fi

    echo ""
    echo "── [4b] Removing Ollama models (from $MODELS_CSV)..."
    if command -v ollama &>/dev/null && curl -sf http://localhost:11434/api/tags > /dev/null 2>&1; then
        if [ -f "$MODELS_CSV" ]; then
            header=$(head -1 "$MODELS_CSV")
            tag_col=$(echo "$header" | tr ',' '\n' | grep -n "^ollama_tag$" | cut -d: -f1)
            enabled_col=$(echo "$header" | tr ',' '\n' | grep -n "^enabled$" | cut -d: -f1)
            while IFS=',' read -r line; do
                [[ "$line" =~ ^# ]] && continue
                tag=$(echo "$line" | cut -d',' -f"$tag_col" | tr -d '[:space:]')
                enabled=$(echo "$line" | cut -d',' -f"$enabled_col" | tr -d '[:space:]')
                [ -z "$tag" ] || [ "$enabled" != "true" ] && continue
                if ollama show "$tag" > /dev/null 2>&1; then
                    if [ "$DRY_RUN" = true ]; then
                        echo "  [dry-run] ollama rm $tag"
                    else
                        ollama rm "$tag" 2>/dev/null && echo "  Removed $tag" || echo "  [warn] Failed to remove $tag"
                    fi
                else
                    echo "  [skip] $tag not in Ollama"
                fi
            done < <(tail -n +2 "$MODELS_CSV" | grep -v '^[[:space:]]*$')
        else
            echo "  $MODELS_CSV not found — cannot read model list"
        fi
    else
        echo "  Ollama not running — skip (run 'ollama serve' first to remove models)"
    fi
fi

# ── Step 5: file sintetici ────────────────────────────────────────────────
if [ "$CLEAN_GENERATED" = true ]; then
    echo ""
    echo "── [5] Deleting generated files (benchmark/generated_files/)..."
    n_csv=$(find benchmark/generated_files -maxdepth 1 -name "*.csv" -o -name "*.xlsx" 2>/dev/null | wc -l | tr -d ' ')
    _run rm -f benchmark/generated_files/*.csv benchmark/generated_files/*.xlsx
    _run rm -f "$BENCHMARK_DIR"/*.json
    echo "  Deleted $n_csv synthetic file(s) + benchmark JSON configs"
fi

# ── Step 6: .venv ─────────────────────────────────────────────────────────
if [ "$CLEAN_VENV" = true ]; then
    echo ""
    echo "── [6] Removing .venv..."
    if [ -d ".venv" ]; then
        size=$(du -sh .venv 2>/dev/null | cut -f1 || echo "?")
        _run rm -rf .venv
        echo "  Removed .venv ($size freed)"
    else
        echo "  .venv not found — skip"
    fi
fi

# ── Summary ───────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════════"
echo "  Cleanup complete  —  $(date '+%Y-%m-%d %H:%M:%S')"
[ "$DRY_RUN" = true ] && echo "  (dry-run — nothing was deleted)"
echo "════════════════════════════════════════════════════════════"
echo ""
if [ "$CLEAN_VENV" = true ] && [ "$DRY_RUN" = false ]; then
    echo "  Per rieseguire il benchmark: bash benchmark/run_benchmark_full.sh"
fi
