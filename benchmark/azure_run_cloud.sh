#!/bin/bash
# ============================================================
#  Spendif.ai — Run All Benchmarks on Azure ML
#
#  One script to: build Docker, push to ACR, submit N jobs
#  (one per model), wait for completion, download results,
#  merge into local CSV, open PR.
#
#  Prerequisites:
#    1. Azure CLI:  brew install azure-cli && az login
#    2. Azure ML SDK: pip install azure-ai-ml azure-identity
#    3. Docker Desktop running
#    4. GitHub CLI: brew install gh && gh auth login
#
#  Azure resources needed (one-time setup):
#    - Resource Group: az group create -n spendifai-rg -l westeurope
#    - ML Workspace:   az ml workspace create -n spendifai-ml -g spendifai-rg
#    - ACR:            az acr create -n spendifaiacr -g spendifai-rg --sku Basic
#    - GPU Compute:    az ml compute create -n gpu-t4-spot -g spendifai-rg \
#                        -w spendifai-ml --type AmlCompute \
#                        --size Standard_NC6s_v3 --min-instances 0 \
#                        --max-instances 5 --tier low_priority
#
#  Usage:
#    # Full run (build + push + submit all + wait + download + PR)
#    bash tools/run_cloud_benchmarks.sh
#
#    # Skip Docker build (image already pushed)
#    bash tools/run_cloud_benchmarks.sh --skip-build
#
#    # Single model only
#    bash tools/run_cloud_benchmarks.sh --model qwen2.5-3b
#
#  Environment variables (.env or export):
#    AZURE_SUBSCRIPTION_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
#    AZURE_RESOURCE_GROUP=spendifai-rg
#    AZURE_ML_WORKSPACE=spendifai-ml
#    AZURE_ACR_NAME=spendifaiacr
#    AZURE_COMPUTE_TARGET=gpu-t4-spot
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

PYTHON="uv run python"
BENCH_CSV="benchmark/results_all_runs.csv"
SKIP_BUILD=false
SINGLE_MODEL=""

# ── Parse args ───────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-build)  SKIP_BUILD=true; shift ;;
        --model)       SINGLE_MODEL="$2"; shift 2 ;;
        *)             echo "Unknown: $1"; exit 1 ;;
    esac
done

echo ""
echo "============================================================"
echo "  Spendif.ai Cloud Benchmark Suite"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"

# ── Step 0: Verify prerequisites ─────────────────────────────────────────────
echo ""
echo "→ Step 0: Checking prerequisites..."

# Azure CLI
az account show > /dev/null 2>&1 || {
    echo "ERROR: Azure CLI not authenticated"
    echo "  Fix: az login"
    exit 1
}
ACCOUNT=$(az account show --query name -o tsv)
echo "  ✅ Azure CLI: $ACCOUNT"

# Docker
docker info > /dev/null 2>&1 || {
    echo "ERROR: Docker not running"
    echo "  Fix: Start Docker Desktop"
    exit 1
}
echo "  ✅ Docker: running"

# GitHub CLI
gh auth status > /dev/null 2>&1 || {
    echo "ERROR: GitHub CLI not authenticated"
    echo "  Fix: gh auth login"
    exit 1
}
echo "  ✅ GitHub CLI: authenticated"

# Azure ML SDK
$PYTHON -c "import azure.ai.ml" 2>/dev/null || {
    echo "ERROR: azure-ai-ml not installed"
    echo "  Fix: uv add azure-ai-ml azure-identity"
    exit 1
}
echo "  ✅ azure-ai-ml SDK: installed"

# ── Step 1: Build & push Docker ──────────────────────────────────────────────
if [ "$SKIP_BUILD" = false ]; then
    echo ""
    echo "→ Step 1: Building and pushing Docker image..."
    $PYTHON benchmark/azure_benchmark.py --build
else
    echo ""
    echo "→ Step 1: SKIPPED (--skip-build)"
fi

# ── Step 2: Submit jobs ──────────────────────────────────────────────────────
echo ""
echo "→ Step 2: Submitting benchmark jobs..."

if [ -n "$SINGLE_MODEL" ]; then
    echo "  Single model: $SINGLE_MODEL"
    $PYTHON benchmark/azure_benchmark.py --model "$SINGLE_MODEL" --skip-build
else
    echo "  All models from registry"
    $PYTHON benchmark/azure_benchmark.py --all-models --skip-build
fi

# ── Step 3: Wait for completion ──────────────────────────────────────────────
echo ""
echo "→ Step 3: Waiting for jobs to complete..."
echo "  Monitor progress:"
echo "    python benchmark/azure_benchmark.py --list"
echo "    Or: Azure ML Studio → Jobs"
echo ""
echo "  Press Ctrl+C to stop waiting (jobs continue on Azure)."
echo "  When ready, download manually with:"
echo "    python benchmark/azure_benchmark.py --download --job-name <name>"
echo ""

# Poll every 60s until all bench- jobs are complete
while true; do
    RUNNING=$($PYTHON -c "
from azure.ai.ml import MLClient
from azure.identity import DefaultAzureCredential
import os
ml = MLClient(DefaultAzureCredential(),
    os.environ.get('AZURE_SUBSCRIPTION_ID', ''),
    os.environ.get('AZURE_RESOURCE_GROUP', 'spendifai-rg'),
    os.environ.get('AZURE_ML_WORKSPACE', 'spendifai-ml'))
n = sum(1 for j in ml.jobs.list(max_results=50)
        if 'bench-' in (j.name or '') and j.status in ('Running', 'Queued', 'Preparing'))
print(n)
" 2>/dev/null || echo "?")

    if [ "$RUNNING" = "0" ]; then
        echo "  ✅ All jobs completed!"
        break
    fi
    echo "  ⏳ $RUNNING jobs still running... (checking again in 60s)"
    sleep 60
done

# ── Step 4: Download all results ─────────────────────────────────────────────
echo ""
echo "→ Step 4: Downloading results..."

JOB_NAMES=$($PYTHON -c "
from azure.ai.ml import MLClient
from azure.identity import DefaultAzureCredential
import os
ml = MLClient(DefaultAzureCredential(),
    os.environ.get('AZURE_SUBSCRIPTION_ID', ''),
    os.environ.get('AZURE_RESOURCE_GROUP', 'spendifai-rg'),
    os.environ.get('AZURE_ML_WORKSPACE', 'spendifai-ml'))
for j in ml.jobs.list(max_results=50):
    if 'bench-' in (j.name or '') and j.status == 'Completed':
        print(j.name)
" 2>/dev/null)

for job in $JOB_NAMES; do
    echo "  Downloading: $job"
    $PYTHON benchmark/azure_benchmark.py --download --job-name "$job" || true
done

# ── Step 5: Open PR ──────────────────────────────────────────────────────────
echo ""
echo "→ Step 5: Creating PR with results..."

BRANCH="bench/azure-$(date +%Y%m%d)"
git checkout -b "$BRANCH" 2>/dev/null || git checkout "$BRANCH"
git add "$BENCH_CSV"
git commit -m "bench: Azure ML results $(date +%Y-%m-%d) — $(echo "$JOB_NAMES" | wc -l | tr -d ' ') models"

git push -u origin "$BRANCH"
gh pr create \
    --title "bench: Azure ML benchmark results $(date +%Y-%m-%d)" \
    --body "$(cat <<EOF
## Benchmark Results

Automated benchmark run on Azure ML (GPU T4 spot instances).

**Jobs completed:** $(echo "$JOB_NAMES" | wc -l | tr -d ' ')
**Date:** $(date +%Y-%m-%d)

The CI will verify that the CSV is append-only (no existing rows modified).
EOF
)"

echo ""
echo "============================================================"
echo "  ✅ CLOUD BENCHMARK COMPLETE"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "  PR created — review and merge."
echo "============================================================"
