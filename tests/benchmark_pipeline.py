#!/usr/bin/env python3
"""Benchmark: N runs of full pipeline on synthetic files.

Measures LLM variability by repeating the same deterministic input N times.
Since the LLM (Ollama) is probabilistic, results may differ between runs.
This script quantifies that variance.

Usage:
    python tests/benchmark_pipeline.py [--runs N] [--files PATTERN]

    --runs N       Number of runs (default: 10)
    --files PAT    Glob pattern to filter files (default: all)
"""
from __future__ import annotations

import argparse
import csv
import fnmatch
import math
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

# ── Ensure project root on sys.path ──────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.normalizer import detect_skip_rows
from core.orchestrator import ProcessingConfig, _build_backend, load_raw_dataframe, _normalize_df_with_schema
from core.classifier import classify_document

# ── Paths ─────────────────────────────────────────────────────────────────
_TESTS_DIR = Path(__file__).resolve().parent
_GENERATED_DIR = _TESTS_DIR / "generated_files"
_MANIFEST_PATH = _GENERATED_DIR / "manifest.csv"
_BENCHMARK_DIR = _GENERATED_DIR / "benchmark"
_GENERATOR_SCRIPT = _TESTS_DIR / "generate_synthetic_files.py"

N_RUNS_DEFAULT = 10

# ── Amount-format to SignConvention mapping ───────────────────────────────
_AMOUNT_FORMAT_TO_SIGN_CONVENTION = {
    "signed_single": "signed_single",
    "positive_only": "signed_single",
    "debit_credit_split": "debit_positive",
    "debit_credit_signed": "credit_negative",
}


# ── Data classes ──────────────────────────────────────────────────────────

@dataclass
class ManifestEntry:
    filename: str
    doc_type: str
    account_id: str
    bank: str
    fmt: str
    separator: str
    n_rows_total: int
    n_header_rows: int
    n_data_rows: int
    n_footer_rows: int
    amount_format: str
    has_debit_credit_split: bool
    column_names: list[str]


@dataclass
class GroundTruthRow:
    row_num: int
    date: str
    amount: float
    description_raw: str
    tx_type: str
    is_internal_transfer: bool
    expected_category: str


@dataclass
class RunFileResult:
    run_id: int
    filename: str
    header_detected: int
    header_expected: int
    header_match: int
    rows_detected: int
    rows_expected: int
    rows_match: int
    doc_type_detected: str
    doc_type_expected: str
    doc_type_match: int
    convention_detected: str
    convention_expected: str
    convention_match: int
    confidence_score: float
    n_parsed: int
    n_expected: int
    parse_rate: float
    amount_correct: int
    amount_total: int
    amount_accuracy: float
    date_correct: int
    date_total: int
    date_accuracy: float
    category_correct: int
    category_total: int
    category_accuracy: float
    duration_seconds: float
    error: str = ""


# ── Helpers ───────────────────────────────────────────────────────────────

def _load_manifest(file_pattern: Optional[str] = None) -> list[ManifestEntry]:
    """Read manifest.csv into a list of ManifestEntry, optionally filtered."""
    entries: list[ManifestEntry] = []
    with open(_MANIFEST_PATH, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            fn = row["filename"]
            if file_pattern and not fnmatch.fnmatch(fn, file_pattern):
                continue
            entries.append(ManifestEntry(
                filename=fn,
                doc_type=row["doc_type"],
                account_id=row["account_id"],
                bank=row["bank"],
                fmt=row["format"],
                separator=row["separator"],
                n_rows_total=int(row["n_rows_total"]),
                n_header_rows=int(row["n_header_rows"]),
                n_data_rows=int(row["n_data_rows"]),
                n_footer_rows=int(row["n_footer_rows"]),
                amount_format=row["amount_format"],
                has_debit_credit_split=row["has_debit_credit_split"].strip().lower() == "true",
                column_names=row["column_names"].split("|"),
            ))
    return entries


def _load_ground_truth(filename: str) -> list[GroundTruthRow]:
    """Load .expected.csv for a given file."""
    expected_path = _GENERATED_DIR / f"{Path(filename).stem}.expected.csv"
    if not expected_path.exists():
        return []
    rows: list[GroundTruthRow] = []
    with open(expected_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(GroundTruthRow(
                row_num=int(row["row_num"]),
                date=row["date"],
                amount=float(row["amount"]),
                description_raw=row["description_raw"],
                tx_type=row["tx_type"],
                is_internal_transfer=row["is_internal_transfer"].strip().lower() == "true",
                expected_category=row["expected_category"],
            ))
    return rows


def _ensure_generated_files() -> None:
    """Generate synthetic files if they do not exist yet."""
    if _MANIFEST_PATH.exists():
        return
    print(f"\n[setup] Generating synthetic files via {_GENERATOR_SCRIPT} ...")
    result = subprocess.run(
        [sys.executable, str(_GENERATOR_SCRIPT)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"ERROR: Synthetic file generation failed (rc={result.returncode})")
        print(f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
        sys.exit(1)


def _collect_llm_metadata(config: ProcessingConfig, backend) -> dict[str, str]:
    """Collect LLM provider, model, and parameters for benchmark metadata."""
    # Git commit info
    _git_sha = "unknown"
    _git_branch = "unknown"
    try:
        _git_sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=PROJECT_ROOT, text=True
        ).strip()
        _git_branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=PROJECT_ROOT, text=True
        ).strip()
    except Exception:
        pass

    meta: dict[str, str] = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "git_commit": _git_sha,
        "git_branch": _git_branch,
        "provider": "unknown",
        "model": "unknown",
        "temperature": "default",
        "llm_timeout_s": str(getattr(config, "llm_timeout_s", "?")),
    }

    # Get provider and model from backend
    backend_class = type(backend).__name__
    meta["provider"] = backend_class.replace("Backend", "").lower()

    if hasattr(backend, "model"):
        meta["model"] = str(backend.model)
    elif hasattr(backend, "_model"):
        meta["model"] = str(backend._model)

    if hasattr(backend, "temperature"):
        meta["temperature"] = str(backend.temperature)
    elif hasattr(backend, "_temperature"):
        meta["temperature"] = str(backend._temperature)

    # Try to get Ollama model info (version, parameter size, quantization)
    if "ollama" in meta["provider"].lower():
        try:
            import urllib.request
            import json
            req = urllib.request.Request(
                f"http://localhost:11434/api/show",
                data=json.dumps({"name": meta["model"]}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                info = json.loads(resp.read())
                details = info.get("details", {})
                meta["parameter_size"] = details.get("parameter_size", "?")
                meta["quantization"] = details.get("quantization_level", "?")
                meta["family"] = details.get("family", "?")
        except Exception:
            meta["parameter_size"] = "?"
            meta["quantization"] = "?"
            meta["family"] = "?"

    # ── Runtime HW (where Spendify runs) ────────────────────────────────
    import platform
    meta["runtime_os"] = f"{platform.system()} {platform.release()} {platform.machine()}"
    try:
        import subprocess as _sp
        ver = _sp.check_output(["sw_vers", "-productVersion"], text=True).strip()
        meta["runtime_os"] = f"macOS {ver}"
    except Exception:
        meta["runtime_os"] = platform.platform()
    try:
        import subprocess as _sp
        cpu = _sp.check_output(
            ["sysctl", "-n", "machdep.cpu.brand_string"], text=True
        ).strip()
        meta["runtime_cpu"] = cpu
    except Exception:
        meta["runtime_cpu"] = platform.processor() or "unknown"
    try:
        import subprocess as _sp
        ram_bytes = int(_sp.check_output(["sysctl", "-n", "hw.memsize"], text=True).strip())
        meta["runtime_ram_gb"] = str(round(ram_bytes / (1024**3)))
    except Exception:
        meta["runtime_ram_gb"] = "?"
    try:
        import subprocess as _sp
        gpu_info = _sp.check_output(
            ["system_profiler", "SPDisplaysDataType"], text=True
        )
        for line in gpu_info.splitlines():
            if "Chipset Model" in line:
                meta["runtime_gpu"] = line.split(":")[-1].strip()
                break
        for line in gpu_info.splitlines():
            if "Total Number of Cores" in line:
                meta["runtime_gpu_cores"] = line.split(":")[-1].strip()
                break
    except Exception:
        meta["gpu"] = "?"
        meta["runtime_gpu"] = "?"
        meta["runtime_gpu_cores"] = "?"

    # ── LLM HW (where the model runs) ────────────────────────────────
    # For local backends (Ollama, llama-cpp) → same as runtime
    # For remote Ollama → query the remote host
    # For cloud APIs → "cloud"
    if "ollama" in meta["provider"].lower():
        # Check if Ollama is local or remote
        ollama_url = getattr(config, "ollama_base_url", None) or "http://localhost:11434"
        is_local = "localhost" in ollama_url or "127.0.0.1" in ollama_url
        meta["llm_host"] = ollama_url
        if is_local:
            meta["llm_hw"] = "same as runtime"
        else:
            meta["llm_hw"] = f"remote ({ollama_url})"
        # Ollama inference params (defaults — user cannot change via API)
        meta["n_ctx"] = "2048"  # Ollama default
        meta["n_batch"] = "512"  # Ollama default
        meta["n_threads"] = "auto"  # Ollama auto-detects
        meta["n_gpu_layers"] = "all"  # Ollama offloads all by default on Metal
        meta["flash_attn"] = "auto"  # Ollama manages internally
    elif "llamacpp" in meta["provider"].lower() or "llama" in meta["provider"].lower():
        meta["llm_host"] = "localhost (in-process)"
        meta["llm_hw"] = "same as runtime"
        # Capture llama-cpp-python inference parameters
        if hasattr(backend, "_llm"):
            llm_obj = backend._llm
            meta["n_ctx"] = str(getattr(llm_obj, "n_ctx", "?")())
            meta["n_batch"] = str(getattr(llm_obj, "n_batch", "?"))
            meta["n_threads"] = str(getattr(llm_obj, "_n_threads", "?"))
            meta["n_gpu_layers"] = str(getattr(llm_obj, "_n_gpu_layers", "?"))
            # flash_attn from model params
            model_params = getattr(llm_obj, "model_params", None)
            if model_params and hasattr(model_params, "flash_attn"):
                meta["flash_attn"] = str(model_params.flash_attn)
            else:
                meta["flash_attn"] = "?"
    elif "openai" in meta["provider"].lower() or "claude" in meta["provider"].lower():
        meta["llm_host"] = "cloud API"
        meta["llm_hw"] = "cloud"
    else:
        meta["llm_host"] = "unknown"
        meta["llm_hw"] = "unknown"

    return meta


def _write_llm_metadata(meta: dict[str, str], n_runs: int, n_files: int) -> None:
    """Write LLM metadata to a JSON file in the benchmark directory."""
    import json
    meta_out = {**meta, "n_runs": n_runs, "n_files": n_files}
    path = _BENCHMARK_DIR / "benchmark_config.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta_out, f, indent=2, ensure_ascii=False)
    print(f"[output] Config: {path}")


def _check_ollama() -> bool:
    """Check if Ollama is reachable."""
    try:
        import urllib.request
        req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def _normalize_category(cat: str) -> str:
    """Extract main category (before '/') and lowercase."""
    if not cat:
        return ""
    return cat.split("/")[0].strip().lower()


def _compare_dates(parsed_date, expected_date_str: str) -> bool:
    """Compare a parsed date (could be str, datetime, date) to expected YYYY-MM-DD."""
    if parsed_date is None:
        return False
    if hasattr(parsed_date, "strftime"):
        parsed_str = parsed_date.strftime("%Y-%m-%d")
    else:
        parsed_str = str(parsed_date).strip()[:10]
    return parsed_str == expected_date_str


def _compare_amounts(parsed_amount, expected_amount: float, tolerance: float = 0.01) -> bool:
    """Compare parsed amount to expected amount within tolerance."""
    if parsed_amount is None:
        return False
    try:
        return abs(float(parsed_amount) - expected_amount) <= tolerance
    except (ValueError, TypeError):
        return False


# ── Main evaluation ──────────────────────────────────────────────────────

def _evaluate_file(
    entry: ManifestEntry,
    ground_truth: list[GroundTruthRow],
    backend,
    run_id: int,
) -> RunFileResult:
    """Run the full pipeline on a single file and compare to ground truth."""
    filepath = _GENERATED_DIR / entry.filename
    t_start = time.time()

    convention_expected = _AMOUNT_FORMAT_TO_SIGN_CONVENTION.get(
        entry.amount_format, entry.amount_format
    )

    # Defaults for error case
    error_result = RunFileResult(
        run_id=run_id,
        filename=entry.filename,
        header_detected=0, header_expected=entry.n_header_rows, header_match=0,
        rows_detected=0, rows_expected=entry.n_data_rows, rows_match=0,
        doc_type_detected="ERROR", doc_type_expected=entry.doc_type, doc_type_match=0,
        convention_detected="ERROR", convention_expected=convention_expected, convention_match=0,
        confidence_score=0.0,
        n_parsed=0, n_expected=entry.n_data_rows, parse_rate=0.0,
        amount_correct=0, amount_total=0, amount_accuracy=0.0,
        date_correct=0, date_total=0, date_accuracy=0.0,
        category_correct=0, category_total=0, category_accuracy=0.0,
        duration_seconds=0.0,
    )

    try:
        raw_bytes = filepath.read_bytes()

        # 1. Header detection
        detected_skip, _certain = detect_skip_rows(raw_bytes, entry.filename)
        header_match = 1 if detected_skip == entry.n_header_rows else 0

        # 2. Load raw DataFrame
        df, encoding, preprocess_info = load_raw_dataframe(raw_bytes, entry.filename)
        rows_detected = len(df)
        rows_match = 1 if abs(rows_detected - entry.n_data_rows) <= 2 else 0

        # 3. Classify via LLM (pass account_type from manifest, like the app does)
        schema = classify_document(
            df_raw=df,
            llm_backend=backend,
            source_name=entry.filename,
            sanitize=True,
            header_certain=preprocess_info.header_certain,
            account_type=entry.doc_type,
        )

        if schema is None:
            error_result.header_detected = detected_skip
            error_result.header_match = header_match
            error_result.rows_detected = rows_detected
            error_result.rows_match = rows_match
            error_result.doc_type_detected = "NONE"
            error_result.convention_detected = "NONE"
            error_result.duration_seconds = time.time() - t_start
            error_result.error = "classify_document returned None"
            return error_result

        # Extract detected values
        doc_type_detected = schema.doc_type.value if hasattr(schema.doc_type, "value") else str(schema.doc_type)
        convention_detected = schema.sign_convention.value if hasattr(schema.sign_convention, "value") else str(schema.sign_convention)
        confidence_score = getattr(schema, "confidence_score", 0.0)

        # doc_type matching
        doc_type_match = 1 if doc_type_detected == entry.doc_type else 0

        # Convention matching
        if entry.amount_format == "positive_only":
            convention_match = 1 if (
                convention_detected == "signed_single" and getattr(schema, "invert_sign", False)
            ) else 0
        else:
            convention_match = 1 if convention_detected == convention_expected else 0

        # 4. Normalize
        transactions, skipped_rows, merge_count = _normalize_df_with_schema(
            df, schema, entry.filename,
        )

        n_parsed = len(transactions)
        parse_rate = n_parsed / entry.n_data_rows if entry.n_data_rows > 0 else 0.0

        # 5. Compare vs ground truth
        amount_correct = 0
        amount_total = 0
        date_correct = 0
        date_total = 0
        category_correct = 0
        category_total = 0

        if ground_truth and transactions:
            # Match transactions by position (both are ordered)
            n_compare = min(len(transactions), len(ground_truth))
            for i in range(n_compare):
                tx = transactions[i]
                gt = ground_truth[i]

                # Amount comparison
                amount_total += 1
                if _compare_amounts(tx.get("amount"), gt.amount):
                    amount_correct += 1

                # Date comparison
                date_total += 1
                if _compare_dates(tx.get("date"), gt.date):
                    date_correct += 1

                # Category comparison (if category is present in transaction)
                tx_category = tx.get("category", "") or ""
                if gt.expected_category:
                    category_total += 1
                    if _normalize_category(tx_category) == _normalize_category(gt.expected_category):
                        category_correct += 1

        amount_accuracy = amount_correct / amount_total if amount_total > 0 else 0.0
        date_accuracy = date_correct / date_total if date_total > 0 else 0.0
        category_accuracy = category_correct / category_total if category_total > 0 else 0.0

        duration = time.time() - t_start

        return RunFileResult(
            run_id=run_id,
            filename=entry.filename,
            header_detected=detected_skip,
            header_expected=entry.n_header_rows,
            header_match=header_match,
            rows_detected=rows_detected,
            rows_expected=entry.n_data_rows,
            rows_match=rows_match,
            doc_type_detected=doc_type_detected,
            doc_type_expected=entry.doc_type,
            doc_type_match=doc_type_match,
            convention_detected=convention_detected,
            convention_expected=convention_expected,
            convention_match=convention_match,
            confidence_score=confidence_score,
            n_parsed=n_parsed,
            n_expected=entry.n_data_rows,
            parse_rate=parse_rate,
            amount_correct=amount_correct,
            amount_total=amount_total,
            amount_accuracy=amount_accuracy,
            date_correct=date_correct,
            date_total=date_total,
            date_accuracy=date_accuracy,
            category_correct=category_correct,
            category_total=category_total,
            category_accuracy=category_accuracy,
            duration_seconds=duration,
        )

    except Exception as e:
        error_result.duration_seconds = time.time() - t_start
        error_result.error = f"{type(e).__name__}: {e}"
        return error_result


# ── CSV output ────────────────────────────────────────────────────────────

_CSV_HEADER = [
    "run_id", "filename",
    "git_commit", "git_branch",
    "provider", "model", "temperature", "parameter_size", "quantization",
    # Inference parameters (for reproducibility & performance analysis)
    "n_ctx", "n_batch", "n_threads", "n_gpu_layers", "flash_attn",
    # Runtime HW
    "runtime_os", "runtime_cpu", "runtime_ram_gb", "runtime_gpu",
    # Results
    "header_detected", "header_expected", "header_match",
    "rows_detected", "rows_expected", "rows_match",
    "doc_type_detected", "doc_type_expected", "doc_type_match",
    "convention_detected", "convention_expected", "convention_match",
    "confidence_score",
    "n_parsed", "n_expected", "parse_rate",
    "amount_correct", "amount_total", "amount_accuracy",
    "date_correct", "date_total", "date_accuracy",
    "category_correct", "category_total", "category_accuracy",
    "duration_seconds", "error",
]

# Filled at runtime by main()
_LLM_META: dict[str, str] = {}


def _result_to_row(r: RunFileResult) -> list:
    return [
        r.run_id, r.filename,
        _LLM_META.get("git_commit", ""),
        _LLM_META.get("git_branch", ""),
        _LLM_META.get("provider", ""),
        _LLM_META.get("model", ""),
        _LLM_META.get("temperature", ""),
        _LLM_META.get("parameter_size", ""),
        _LLM_META.get("quantization", ""),
        # Inference parameters
        _LLM_META.get("n_ctx", ""),
        _LLM_META.get("n_batch", ""),
        _LLM_META.get("n_threads", ""),
        _LLM_META.get("n_gpu_layers", ""),
        _LLM_META.get("flash_attn", ""),
        # Runtime HW
        _LLM_META.get("runtime_os", ""),
        _LLM_META.get("runtime_cpu", ""),
        _LLM_META.get("runtime_ram_gb", ""),
        _LLM_META.get("runtime_gpu", ""),
        r.header_detected, r.header_expected, r.header_match,
        r.rows_detected, r.rows_expected, r.rows_match,
        r.doc_type_detected, r.doc_type_expected, r.doc_type_match,
        r.convention_detected, r.convention_expected, r.convention_match,
        f"{r.confidence_score:.4f}",
        r.n_parsed, r.n_expected, f"{r.parse_rate:.4f}",
        r.amount_correct, r.amount_total, f"{r.amount_accuracy:.4f}",
        r.date_correct, r.date_total, f"{r.date_accuracy:.4f}",
        r.category_correct, r.category_total, f"{r.category_accuracy:.4f}",
        f"{r.duration_seconds:.2f}", r.error,
    ]


def _write_run_csv(results: list[RunFileResult], run_id: int) -> None:
    """Write per-file results for a single run."""
    path = _BENCHMARK_DIR / f"results_run_{run_id:02d}.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(_CSV_HEADER)
        for r in results:
            writer.writerow(_result_to_row(r))


def _write_all_runs_csv(all_results: list[RunFileResult]) -> None:
    """Append new results to all-runs CSV (creates with header if missing)."""
    path = _BENCHMARK_DIR / "results_all_runs.csv"
    write_header = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(_CSV_HEADER)
        for r in all_results:
            writer.writerow(_result_to_row(r))


def _compute_variance(all_results: list[RunFileResult]) -> tuple[list[dict], list[dict]]:
    """Compute per-file and global variance metrics.

    Returns (per_file_rows, global_rows).
    """
    metrics = [
        "header_match", "rows_match", "doc_type_match", "convention_match",
        "confidence_score", "parse_rate", "amount_accuracy", "date_accuracy",
        "category_accuracy",
    ]

    # Group by filename
    by_file: dict[str, list[RunFileResult]] = {}
    for r in all_results:
        by_file.setdefault(r.filename, []).append(r)

    per_file_rows: list[dict] = []
    global_values: dict[str, list[float]] = {m: [] for m in metrics}

    for filename, results in sorted(by_file.items()):
        for metric in metrics:
            values = [getattr(r, metric) for r in results]
            float_values = [float(v) for v in values]
            n = len(float_values)
            mean = sum(float_values) / n if n > 0 else 0.0
            std = math.sqrt(sum((v - mean) ** 2 for v in float_values) / n) if n > 1 else 0.0
            mn = min(float_values) if float_values else 0.0
            mx = max(float_values) if float_values else 0.0
            cv = (std / mean * 100) if mean > 0 else 0.0

            per_file_rows.append({
                "filename": filename,
                "metric": metric,
                "mean": mean,
                "std": std,
                "min": mn,
                "max": mx,
                "cv_pct": cv,
            })
            global_values[metric].extend(float_values)

    global_rows: list[dict] = []
    for metric in metrics:
        values = global_values[metric]
        n = len(values)
        mean = sum(values) / n if n > 0 else 0.0
        std = math.sqrt(sum((v - mean) ** 2 for v in values) / n) if n > 1 else 0.0
        mn = min(values) if values else 0.0
        mx = max(values) if values else 0.0
        cv = (std / mean * 100) if mean > 0 else 0.0
        global_rows.append({
            "metric": metric,
            "mean": mean,
            "std": std,
            "min": mn,
            "max": mx,
            "cv_pct": cv,
        })

    return per_file_rows, global_rows


def _write_variance_csv(per_file_rows: list[dict]) -> None:
    path = _BENCHMARK_DIR / "summary_variance.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["filename", "metric", "mean", "std", "min", "max", "cv(%)"])
        for row in per_file_rows:
            writer.writerow([
                row["filename"], row["metric"],
                f"{row['mean']:.4f}", f"{row['std']:.4f}",
                f"{row['min']:.4f}", f"{row['max']:.4f}",
                f"{row['cv_pct']:.1f}",
            ])


def _write_global_csv(global_rows: list[dict]) -> None:
    path = _BENCHMARK_DIR / "summary_global.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["metric", "mean", "std", "min", "max", "cv(%)"])
        for row in global_rows:
            writer.writerow([
                row["metric"],
                f"{row['mean']:.4f}", f"{row['std']:.4f}",
                f"{row['min']:.4f}", f"{row['max']:.4f}",
                f"{row['cv_pct']:.1f}",
            ])


# ── Console output ────────────────────────────────────────────────────────

_METRIC_LABELS = {
    "header_match": "Header accuracy",
    "rows_match": "Row count accuracy",
    "doc_type_match": "Doc type accuracy",
    "convention_match": "Convention accuracy",
    "confidence_score": "Confidence score",
    "parse_rate": "Parse rate",
    "amount_accuracy": "Amount accuracy",
    "date_accuracy": "Date accuracy",
    "category_accuracy": "Category accuracy",
}


def _print_summary(global_rows: list[dict], n_runs: int, n_files: int, total_time: float) -> None:
    """Print the final summary table."""
    print()
    print("+" + "=" * 74 + "+")
    print(f"|{'BENCHMARK RESULTS':^74}|")
    print(f"|{f'{n_runs} runs x {n_files} files — total {total_time:.0f}s':^74}|")
    print("+" + "=" * 74 + "+")
    print(f"| {'Metric':<22}| {'Mean':>6} | {'Std':>6} | {'Min':>6} | {'Max':>6} | {'CV%':>6} |")
    print("+" + "-" * 22 + "+" + "-" * 8 + "+" + "-" * 8 + "+" + "-" * 8 + "+" + "-" * 8 + "+" + "-" * 8 + "+")

    for row in global_rows:
        label = _METRIC_LABELS.get(row["metric"], row["metric"])
        print(
            f"| {label:<22}"
            f"| {row['mean']:>6.2f} "
            f"| {row['std']:>6.2f} "
            f"| {row['min']:>6.2f} "
            f"| {row['max']:>6.2f} "
            f"| {row['cv_pct']:>5.1f} |"
        )

    # Automation score: weighted mean of all metric means
    weights = {
        "header_match": 0.10,
        "rows_match": 0.10,
        "doc_type_match": 0.20,
        "convention_match": 0.15,
        "confidence_score": 0.10,
        "parse_rate": 0.10,
        "amount_accuracy": 0.10,
        "date_accuracy": 0.05,
        "category_accuracy": 0.10,
    }
    score_mean = sum(row["mean"] * weights.get(row["metric"], 0) for row in global_rows)
    score_std = sum(row["std"] * weights.get(row["metric"], 0) for row in global_rows)
    score_min = sum(row["min"] * weights.get(row["metric"], 0) for row in global_rows)
    score_max = sum(row["max"] * weights.get(row["metric"], 0) for row in global_rows)
    score_cv = (score_std / score_mean * 100) if score_mean > 0 else 0.0

    print("+" + "-" * 22 + "+" + "-" * 8 + "+" + "-" * 8 + "+" + "-" * 8 + "+" + "-" * 8 + "+" + "-" * 8 + "+")
    print(
        f"| {'AUTOMATION SCORE':<22}"
        f"| {score_mean:>6.2f} "
        f"| {score_std:>6.2f} "
        f"| {score_min:>6.2f} "
        f"| {score_max:>6.2f} "
        f"| {score_cv:>5.1f} |"
    )
    print("+" + "=" * 74 + "+")

    # Classify metrics by variability
    deterministic = [r for r in global_rows if r["cv_pct"] == 0.0]
    variable = [r for r in global_rows if r["cv_pct"] > 0.0]

    print()
    if deterministic:
        labels = [_METRIC_LABELS.get(r["metric"], r["metric"]) for r in deterministic]
        print(f"  Deterministic (CV=0%): {', '.join(labels)}")
        print("    -> algorithm-only, no LLM variance")
    if variable:
        labels_cv = [(
            _METRIC_LABELS.get(r["metric"], r["metric"]),
            r["cv_pct"],
        ) for r in variable]
        labels_cv.sort(key=lambda x: -x[1])
        parts = [f"{label} ({cv:.1f}%)" for label, cv in labels_cv]
        print(f"  Variable (CV>0%): {', '.join(parts)}")
        print("    -> LLM-dependent, variance expected")

    print(f"\n  Output dir: {_BENCHMARK_DIR}")
    print()


# ── Main ──────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark: N runs of full pipeline on synthetic files."
    )
    parser.add_argument("--runs", type=int, default=N_RUNS_DEFAULT,
                        help=f"Number of runs (default: {N_RUNS_DEFAULT})")
    parser.add_argument("--files", type=str, default=None,
                        help="Glob pattern to filter files (e.g. 'CC-1*')")
    parser.add_argument("--backend", type=str, default=None,
                        help="LLM backend override (e.g. 'local_llama_cpp', 'local_ollama')")
    parser.add_argument("--model-path", type=str, default=None,
                        help="Model path for llama-cpp backend (e.g. path to .gguf file)")
    parser.add_argument("--model", type=str, default=None,
                        help="Model name override for Ollama (e.g. 'phi3:3.8b', 'gemma3:12b')")
    args = parser.parse_args()

    n_runs = args.runs
    file_pattern = args.files
    if file_pattern and not ("*" in file_pattern or "?" in file_pattern):
        file_pattern = f"*{file_pattern}*"

    # Startup checks
    print(f"\n{'=' * 60}")
    print(f"  Spendify Pipeline Benchmark")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 60}")

    backend_override = args.backend
    model_path_override = getattr(args, 'model_path', None)
    model_override = args.model

    if not backend_override or backend_override == "local_ollama":
        print("\n[check] Verifying Ollama is reachable...")
        if not _check_ollama():
            print("ERROR: Ollama is not reachable at http://localhost:11434")
            print("       Start Ollama before running this benchmark.")
            sys.exit(1)
        print("[check] Ollama OK")
    else:
        print(f"\n[check] Backend: {backend_override} (skipping Ollama check)")

    print("[check] Verifying synthetic files...")
    _ensure_generated_files()
    if not _MANIFEST_PATH.exists():
        print(f"ERROR: Manifest not found at {_MANIFEST_PATH}")
        sys.exit(1)
    print("[check] Synthetic files OK")

    # Load manifest and ground truth
    manifest = _load_manifest(file_pattern)
    if not manifest:
        print(f"ERROR: No files matched pattern '{file_pattern}'")
        sys.exit(1)

    ground_truth_map: dict[str, list[GroundTruthRow]] = {}
    for entry in manifest:
        ground_truth_map[entry.filename] = _load_ground_truth(entry.filename)

    n_files = len(manifest)
    print(f"\n[config] Runs: {n_runs}, Files: {n_files}")
    if file_pattern:
        print(f"[config] File filter: {file_pattern}")

    # Create output dir
    _BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)

    # Build LLM backend once (reused across runs)
    config = ProcessingConfig()
    if backend_override:
        config.llm_backend = backend_override
    if model_path_override and backend_override == "local_llama_cpp":
        config.llama_cpp_model_path = model_path_override
    if model_override:
        config.ollama_model = model_override  # works for Ollama backend
    backend = _build_backend(config)

    # ── Collect LLM metadata ─────────────────────────────────────────────
    llm_meta = _collect_llm_metadata(config, backend)
    _LLM_META.update(llm_meta)  # populate module-level dict for CSV rows

    print(f"\n[config] Provider: {llm_meta['provider']}")
    print(f"[config] Model: {llm_meta['model']}")
    print(f"[config] Temperature: {llm_meta['temperature']}")
    if llm_meta.get("parameter_size", "?") != "?":
        print(f"[config] Parameters: {llm_meta['parameter_size']}, Quant: {llm_meta['quantization']}")
    print(f"[config] Timeout: {llm_meta['llm_timeout_s']}s")
    print(f"[runtime] OS: {llm_meta.get('runtime_os', '?')}")
    print(f"[runtime] CPU: {llm_meta.get('runtime_cpu', '?')}")
    print(f"[runtime] RAM: {llm_meta.get('runtime_ram_gb', '?')} GB")
    print(f"[runtime] GPU: {llm_meta.get('runtime_gpu', '?')} ({llm_meta.get('runtime_gpu_cores', '?')} cores)")
    print(f"[llm] Host: {llm_meta.get('llm_host', '?')}")
    print(f"[llm] HW: {llm_meta.get('llm_hw', '?')}")
    print(f"[inference] n_ctx: {llm_meta.get('n_ctx', '?')}, n_batch: {llm_meta.get('n_batch', '?')}, n_threads: {llm_meta.get('n_threads', '?')}, n_gpu_layers: {llm_meta.get('n_gpu_layers', '?')}, flash_attn: {llm_meta.get('flash_attn', '?')}")

    # Resume: load already-completed (run_id, filename, git_commit, git_branch, provider, model) tuples
    _completed: set[tuple] = set()
    _prev_results: list[RunFileResult] = []
    _all_runs_path = _BENCHMARK_DIR / "results_all_runs.csv"
    if _all_runs_path.exists():
        with open(_all_runs_path, encoding="utf-8") as _f:
            _reader = csv.DictReader(_f)
            for _row in _reader:
                _key = (
                    int(_row.get("run_id", 0)),
                    _row.get("filename", ""),
                    _row.get("git_commit", ""),
                    _row.get("git_branch", ""),
                    _row.get("provider", ""),
                    _row.get("model", ""),
                )
                _completed.add(_key)
        if _completed:
            print(f"[resume] Found {len(_completed)} completed steps — skipping them")

    # Run benchmark
    all_results: list[RunFileResult] = list(_prev_results)
    total_start = time.time()
    total_steps = n_runs * n_files
    skipped_steps = 0
    completed_steps = 0

    for run_id in range(1, n_runs + 1):
        run_results: list[RunFileResult] = []
        run_start = time.time()

        for file_idx, entry in enumerate(manifest, 1):
            # Check resume key: (run_id, filename, git_commit, git_branch, provider, model)
            _resume_key = (
                run_id, entry.filename,
                _LLM_META.get("git_commit", ""),
                _LLM_META.get("git_branch", ""),
                _LLM_META.get("provider", ""),
                _LLM_META.get("model", ""),
            )
            if _resume_key in _completed:
                skipped_steps += 1
                completed_steps += 1
                continue

            completed_steps += 1
            pct = completed_steps / total_steps
            elapsed = time.time() - total_start
            eta = (elapsed / completed_steps) * (total_steps - completed_steps) if completed_steps > 0 else 0
            eta_min, eta_sec = divmod(int(eta), 60)
            bar_len = 30
            filled = int(bar_len * pct)
            bar = "█" * filled + "░" * (bar_len - filled)

            print(
                f"\r  {bar} {pct:5.1%} | "
                f"Run {run_id}/{n_runs} File {file_idx}/{n_files} "
                f"| ETA {eta_min:02d}:{eta_sec:02d} | "
                f"{entry.filename}",
                end="", flush=True,
            )

            gt = ground_truth_map.get(entry.filename, [])
            result = _evaluate_file(entry, gt, backend, run_id)
            run_results.append(result)

            status = "OK" if not result.error else f"ERR: {result.error[:40]}"
            print(
                f"\r  [Run {run_id}/{n_runs}] [File {file_idx}/{n_files}] "
                f"{entry.filename} "
                f"{result.duration_seconds:.1f}s "
                f"dt={'Y' if result.doc_type_match else 'N'} "
                f"cv={'Y' if result.convention_match else 'N'} "
                f"conf={result.confidence_score:.2f} "
                f"rows={result.n_parsed}/{result.n_expected} "
                f"[{status}]"
                + " " * 20
            )

        run_duration = time.time() - run_start
        _write_run_csv(run_results, run_id)
        all_results.extend(run_results)

        # Run summary
        n_ok = sum(1 for r in run_results if not r.error)
        n_err = sum(1 for r in run_results if r.error)
        avg_dt = sum(r.doc_type_match for r in run_results) / len(run_results) if run_results else 0
        avg_cv = sum(r.convention_match for r in run_results) / len(run_results) if run_results else 0
        print(
            f"  --- Run {run_id} complete: {run_duration:.0f}s, "
            f"{n_ok} OK / {n_err} errors, "
            f"doc_type={avg_dt:.0%}, convention={avg_cv:.0%}"
        )
        print()

    total_time = time.time() - total_start

    # Write all outputs
    _write_llm_metadata(llm_meta, n_runs, n_files)
    _write_all_runs_csv(all_results)
    per_file_rows, global_rows = _compute_variance(all_results)
    _write_variance_csv(per_file_rows)
    _write_global_csv(global_rows)

    # Print summary
    _print_summary(global_rows, n_runs, n_files, total_time)

    # Count errors
    n_errors = sum(1 for r in all_results if r.error)
    if n_errors > 0:
        print(f"  WARNING: {n_errors} file/run combinations had errors.")
        error_files = set(r.filename for r in all_results if r.error)
        for fn in sorted(error_files):
            errs = [r for r in all_results if r.filename == fn and r.error]
            print(f"    {fn}: {len(errs)}/{n_runs} runs failed — {errs[0].error[:60]}")
        print()


if __name__ == "__main__":
    main()
