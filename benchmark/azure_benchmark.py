#!/usr/bin/env python3
"""Spendif.ai Benchmark-as-a-Service on Azure ML (T-09d).

Builds and pushes the benchmark Docker image to ACR, submits Azure ML Jobs
for each model, and downloads results when complete.

Prerequisites:
    pip install azure-ai-ml azure-identity
    az login   # Azure CLI authenticated

Usage:
    # Single model
    python tools/azure_benchmark.py --model qwen2.5-3b

    # All models from registry
    python tools/azure_benchmark.py --all-models

    # Download results from completed job
    python tools/azure_benchmark.py --download --job-name bench-qwen25-3b-20260328

    # List running/completed jobs
    python tools/azure_benchmark.py --list

Environment variables (or .env):
    AZURE_SUBSCRIPTION_ID   — Azure subscription
    AZURE_RESOURCE_GROUP    — Resource group with ML workspace
    AZURE_ML_WORKSPACE      — Azure ML workspace name
    AZURE_ACR_NAME          — Azure Container Registry name (e.g. spendifaiacr)
    AZURE_COMPUTE_TARGET    — GPU compute (default: gpu-t4-spot)
"""
from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
import warnings
from datetime import datetime
from pathlib import Path

warnings.filterwarnings("ignore", message="urllib3.*charset_normalizer")
warnings.filterwarnings("ignore", category=DeprecationWarning)

# ── Config ───────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).parent.parent
BENCH_CSV = PROJECT_ROOT / "tests" / "generated_files" / "benchmark" / "results_all_runs.csv"
DOCKER_IMAGE = "spendifai-bench"
DOCKERFILE = "docker/Dockerfile.benchmark"

# Azure defaults (overridable via env)
def _get_subscription_id() -> str:
    """Get subscription ID from env var or az CLI (lazy, cached)."""
    sub = os.environ.get("AZURE_SUBSCRIPTION_ID", "")
    if not sub:
        try:
            sub = subprocess.check_output(
                ["az", "account", "show", "--query", "id", "-o", "tsv"],
                stderr=subprocess.DEVNULL, text=True,
            ).strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
    return sub

RESOURCE_GROUP = os.environ.get("AZURE_RESOURCE_GROUP", "spendifai-rg")
WORKSPACE = os.environ.get("AZURE_ML_WORKSPACE", "spendifai-ml")
ACR_NAME = os.environ.get("AZURE_ACR_NAME", "spendifaiacr")
COMPUTE_TARGET = os.environ.get("AZURE_COMPUTE_TARGET", "cpu-bench")


def _check_azure_cli():
    """Verify Azure CLI is installed and logged in."""
    try:
        subprocess.check_output(["az", "account", "show"], stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("ERROR: Azure CLI not authenticated. Run: az login")
        sys.exit(1)


def _check_azure_ml_sdk():
    """Verify azure-ai-ml SDK is installed."""
    try:
        import azure.ai.ml  # noqa: F401
    except ImportError:
        print("ERROR: azure-ai-ml not installed. Run: pip install azure-ai-ml azure-identity")
        sys.exit(1)


# ── Docker Build & Push ──────────────────────────────────────────────────────


def build_and_push_docker():
    """Build benchmark Docker image and push to ACR."""
    acr_url = f"{ACR_NAME}.azurecr.io"
    tag = f"{acr_url}/{DOCKER_IMAGE}:latest"

    print(f"→ Building Docker image: {tag}")
    subprocess.run(
        ["docker", "build", "-f", DOCKERFILE, "-t", tag, "."],
        cwd=PROJECT_ROOT, check=True,
    )

    print(f"→ Logging into ACR: {acr_url}")
    subprocess.run(
        ["az", "acr", "login", "--name", ACR_NAME],
        check=True,
    )

    print(f"→ Pushing image to ACR...")
    subprocess.run(["docker", "push", tag], check=True)
    print(f"  ✅ Image pushed: {tag}")
    return tag


# ── Azure ML Job ─────────────────────────────────────────────────────────────


def _create_conda_env_file() -> Path:
    """Create a conda environment YAML for Azure ML (no local Docker needed)."""
    conda_path = PROJECT_ROOT / "docker" / "conda_benchmark.yml"
    conda_path.parent.mkdir(parents=True, exist_ok=True)
    conda_path.write_text("""\
name: spendifai-bench
channels:
  - conda-forge
  - defaults
dependencies:
  - python=3.11
  - pip
  - pip:
    - llama-cpp-python
    - huggingface_hub
    - pandas
    - openpyxl
    - sqlalchemy
    - pydantic
    - pyyaml
    - chardet
""")
    return conda_path


def submit_job(model_id: str, runs: int = 1, compute: str | None = None,
               mode: str = "conda") -> str:
    """Submit an Azure ML Job for one model. Returns job name.

    mode="docker" — uses pre-built Docker image from ACR (requires docker build+push first)
    mode="conda"  — uses conda env file, Azure ML builds the image server-side (no Docker needed)
    """
    from azure.ai.ml import MLClient, command, Output
    from azure.ai.ml.entities import Environment
    from azure.identity import DefaultAzureCredential

    credential = DefaultAzureCredential()
    ml_client = MLClient(credential, _get_subscription_id(), RESOURCE_GROUP, WORKSPACE)

    compute_name = compute or COMPUTE_TARGET
    job_name = f"bench-{model_id.replace('.', '').replace('/', '-')}-{datetime.now().strftime('%Y%m%d%H%M')}"

    print(f"→ Submitting job: {job_name}")
    print(f"  Model: {model_id}")
    print(f"  Compute: {compute_name}")
    print(f"  Mode: {mode}")

    if mode == "docker":
        acr_url = f"{ACR_NAME}.azurecr.io"
        image = f"{acr_url}/{DOCKER_IMAGE}:latest"
        print(f"  Image: {image}")
        env = Environment(name="spendifai-bench-docker", image=image)
        job = command(
            name=job_name,
            display_name=f"Spendif.ai Benchmark — {model_id}",
            description=f"Classifier + categorizer benchmark for {model_id}",
            environment=env,
            compute=compute_name,
            command=f"/entrypoint.sh --model {model_id} --runs {runs}",
            outputs={
                "results": Output(
                    type="uri_folder",
                    path=f"azureml://datastores/workspaceblobdefault/paths/benchmarks/{job_name}/",
                ),
            },
            environment_variables={
                "RESULTS_DIR": "${{outputs.results}}",
                "MODELS_DIR": "/models",
                "HF_HOME": "/tmp/hf_cache",
            },
        )
    else:
        # Conda mode — no Docker needed locally
        conda_file = _create_conda_env_file()
        env = Environment(
            name="spendifai-bench-conda",
            description="Spendif.ai benchmark env (conda, no Docker)",
            conda_file=str(conda_file),
            image="mcr.microsoft.com/azureml/curated/acft-hf-nlp-gpu:latest",
        )
        job = command(
            name=job_name,
            display_name=f"Spendif.ai Benchmark — {model_id}",
            description=f"Classifier + categorizer benchmark for {model_id}",
            environment=env,
            compute=compute_name,
            code=str(PROJECT_ROOT),
            command=f"bash docker/benchmark_entrypoint.sh --model {model_id} --runs {runs}",
            outputs={
                "results": Output(
                    type="uri_folder",
                    path=f"azureml://datastores/workspaceblobdefault/paths/benchmarks/{job_name}/",
                ),
            },
            environment_variables={
                "RESULTS_DIR": "${{outputs.results}}",
                "MODELS_DIR": "/tmp/models",
                "HF_HOME": "/tmp/hf_cache",
                "PYTHONPATH": ".",
            },
        )

    returned_job = ml_client.jobs.create_or_update(job)
    print(f"  ✅ Job submitted: {returned_job.name}")
    print(f"  Studio URL: {returned_job.studio_url}")
    return returned_job.name, ml_client


def wait_and_download(job_name: str, ml_client=None, poll_interval: int = 30):
    """Poll job status until complete, then auto-download results."""
    import time

    if ml_client is None:
        from azure.ai.ml import MLClient
        from azure.identity import DefaultAzureCredential
        credential = DefaultAzureCredential()
        ml_client = MLClient(credential, _get_subscription_id(), RESOURCE_GROUP, WORKSPACE)

    terminal_states = {"Completed", "Failed", "Canceled", "CancelRequested"}
    last_status = ""

    while True:
        job = ml_client.jobs.get(job_name)
        status = job.status

        if status != last_status:
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"  [{ts}] {job_name}: {status}")
            last_status = status

        if status in terminal_states:
            break

        time.sleep(poll_interval)

    if status == "Completed":
        print(f"  ✅ Job completed — downloading results...")
        download_results(job_name)
    else:
        print(f"  ❌ Job ended with status: {status}")
        # Try to show error details
        if hasattr(job, 'error') and job.error:
            print(f"  Error: {job.error}")


def list_jobs():
    """List recent benchmark jobs."""
    from azure.ai.ml import MLClient
    from azure.identity import DefaultAzureCredential

    credential = DefaultAzureCredential()
    ml_client = MLClient(credential, _get_subscription_id(), RESOURCE_GROUP, WORKSPACE)

    print(f"{'Name':<45} {'Status':<12} {'Created':<20}")
    print("-" * 80)

    for job in ml_client.jobs.list(max_results=20):
        if "bench-" in (job.name or ""):
            created = job.creation_context.created_at.strftime("%Y-%m-%d %H:%M") if job.creation_context else "?"
            print(f"{job.name:<45} {job.status:<12} {created:<20}")


def download_results(job_name: str):
    """Download results CSV from a completed Azure ML Job and merge locally."""
    from azure.ai.ml import MLClient
    from azure.identity import DefaultAzureCredential

    credential = DefaultAzureCredential()
    ml_client = MLClient(credential, _get_subscription_id(), RESOURCE_GROUP, WORKSPACE)

    print(f"→ Downloading results from job: {job_name}")

    download_dir = PROJECT_ROOT / "tmp" / "azure_results" / job_name
    download_dir.mkdir(parents=True, exist_ok=True)

    ml_client.jobs.download(job_name, output_name="results", download_path=str(download_dir))

    # Find the CSV in downloaded output
    remote_csv = None
    for f in download_dir.rglob("results_all_runs.csv"):
        remote_csv = f
        break

    if remote_csv is None:
        print(f"  ERROR: results_all_runs.csv not found in {download_dir}")
        return

    # Merge with local CSV (append-only, dedup by resume key)
    _merge_csv(remote_csv, BENCH_CSV)
    print(f"  ✅ Results merged into {BENCH_CSV}")
    print(f"  Now: git add + commit + PR")


def _merge_csv(source: Path, target: Path):
    """Merge source CSV rows into target (append-only, dedup by resume key)."""
    # Load existing
    existing_keys: set[tuple] = set()
    existing_rows: list[dict] = []
    header: list[str] = []

    if target.exists():
        with open(target, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames or []
            for row in reader:
                key = (
                    row.get("run_id", ""),
                    row.get("filename", ""),
                    row.get("git_commit", ""),
                    row.get("git_branch", ""),
                    row.get("provider", ""),
                    row.get("model", ""),
                    row.get("benchmark_type", ""),
                )
                existing_keys.add(key)
                existing_rows.append(row)

    # Load new rows
    new_count = 0
    with open(source, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not header:
            header = reader.fieldnames or []
        for row in reader:
            key = (
                row.get("run_id", ""),
                row.get("filename", ""),
                row.get("git_commit", ""),
                row.get("git_branch", ""),
                row.get("provider", ""),
                row.get("model", ""),
                row.get("benchmark_type", ""),
            )
            if key not in existing_keys:
                existing_rows.append(row)
                existing_keys.add(key)
                new_count += 1

    # Write merged
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        writer.writerows(existing_rows)

    print(f"  Merged: {new_count} new rows added ({len(existing_rows)} total)")


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Spendif.ai Benchmark on Azure ML")
    parser.add_argument("--model", type=str, help="Model ID from registry (e.g. qwen2.5-3b)")
    parser.add_argument("--all-models", action="store_true", help="Run all models from registry")
    parser.add_argument("--runs", type=int, default=1, help="Number of runs per model")
    parser.add_argument("--compute", type=str, default=None, help="Azure ML compute target")
    parser.add_argument("--download", action="store_true", help="Download results from a job")
    parser.add_argument("--job-name", type=str, help="Job name for --download")
    parser.add_argument("--list", action="store_true", help="List recent benchmark jobs")
    parser.add_argument("--mode", choices=["docker", "conda"], default="conda",
                        help="Job mode: 'docker' (pre-built ACR image) or 'conda' (no Docker needed, default)")
    parser.add_argument("--build", action="store_true", help="Build and push Docker image only")
    parser.add_argument("--skip-build", action="store_true", help="Skip Docker build (use existing image)")
    parser.add_argument("--no-wait", action="store_true", help="Submit and exit without waiting for completion")
    parser.add_argument("--poll-interval", type=int, default=30, help="Polling interval in seconds (default: 30)")
    args = parser.parse_args()

    if args.list:
        _check_azure_cli()
        _check_azure_ml_sdk()
        list_jobs()
        return

    if args.download:
        _check_azure_cli()
        _check_azure_ml_sdk()
        if not args.job_name:
            print("ERROR: --job-name required with --download")
            sys.exit(1)
        download_results(args.job_name)
        return

    if args.build:
        _check_azure_cli()
        build_and_push_docker()
        return

    # Submit job(s)
    _check_azure_cli()
    _check_azure_ml_sdk()

    if args.mode == "docker" and not args.skip_build:
        build_and_push_docker()

    if args.all_models:
        sys.path.insert(0, str(PROJECT_ROOT))
        from config import get_all_models
        models = get_all_models()
        print(f"\n→ Submitting {len(models)} jobs...")
        jobs = []
        for m in models:
            name, client = submit_job(m.id, runs=args.runs, compute=args.compute, mode=args.mode)
            jobs.append((name, client))
        print(f"\n✅ {len(jobs)} jobs submitted")

        if not args.no_wait:
            print(f"\n→ Polling all jobs (interval: {args.poll_interval}s)...\n")
            for name, client in jobs:
                wait_and_download(name, ml_client=client, poll_interval=args.poll_interval)
        else:
            print("Monitor: python tools/azure_benchmark.py --list")
            print("Download: python tools/azure_benchmark.py --download --job-name <name>")

    elif args.model:
        name, client = submit_job(args.model, runs=args.runs, compute=args.compute, mode=args.mode)

        if not args.no_wait:
            print(f"\n→ Polling (interval: {args.poll_interval}s)...\n")
            wait_and_download(name, ml_client=client, poll_interval=args.poll_interval)

    else:
        print("ERROR: Specify --model <id>, --all-models, --build, --download, or --list")
        sys.exit(1)


if __name__ == "__main__":
    main()
