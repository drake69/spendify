#!/usr/bin/env python3
"""Benchmark: N runs of categorization pipeline on synthetic files.

Measures LLM variability for the categorization step by repeating the same
normalized input N times. The classifier step is bypassed — we use the ground
truth schema directly — so this benchmark isolates categorization accuracy.

Usage:
    python tests/benchmark_categorizer.py [--runs N] [--files PATTERN]

    --runs N           Number of runs (default: 10)
    --files PAT        Glob pattern to filter files (default: *_S_*)
    --backend NAME     LLM backend (default: from user settings or local_ollama)
    --model MODEL      Model name for Ollama (default: from user settings)
    --model-path PATH  GGUF path for llama-cpp
"""
from __future__ import annotations

import argparse
import csv
import fnmatch
import json
import math
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

from core.categorizer import (
    TaxonomyConfig,
    categorize_batch,
    CategoryRule,
    CategorizationResult,
    _DEFAULT_FALLBACK_EXPENSE,
    _DEFAULT_FALLBACK_INCOME,
)
from core.models import CategorySource
from core.orchestrator import ProcessingConfig, _build_backend, load_raw_dataframe, _normalize_df_with_schema
from core.classifier import classify_document
from db.taxonomy_defaults import TAXONOMY_DEFAULTS

# ── Paths ─────────────────────────────────────────────────────────────────
_TESTS_DIR = Path(__file__).resolve().parent
_GENERATED_DIR = _TESTS_DIR / "generated_files"
_MANIFEST_PATH = _GENERATED_DIR / "manifest.csv"
_BENCHMARK_DIR = _GENERATED_DIR / "benchmark"
_GENERATOR_SCRIPT = _TESTS_DIR / "generate_synthetic_files.py"

N_RUNS_DEFAULT = 10
_FILES_DEFAULT = "*_S_*"

# Fallback category names (Italian defaults)
_FALLBACK_CATEGORIES = {_DEFAULT_FALLBACK_EXPENSE[0], _DEFAULT_FALLBACK_INCOME[0]}


# ── Taxonomy from defaults ────────────────────────────────────────────────

def _build_taxonomy_from_defaults(lang: str = "it") -> TaxonomyConfig:
    """Build a TaxonomyConfig from the built-in Italian defaults (no DB needed)."""
    defaults = TAXONOMY_DEFAULTS[lang]
    expenses = {
        entry["category"]: entry["subcategories"]
        for entry in defaults["expenses"]
    }
    income = {
        entry["category"]: entry["subcategories"]
        for entry in defaults["income"]
    }
    return TaxonomyConfig(expenses=expenses, income=income)


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
class CatRunResult:
    run_id: int
    filename: str
    n_transactions: int
    n_categorized: int          # category != None and != fallback
    n_correct_category: int     # matches expected_category (exact)
    n_correct_fuzzy: int        # matches at top-level (e.g. "Alimentari" matches "Alimentari/Supermercato")
    n_fallback: int             # assigned to fallback category ("Altro")
    n_history: int              # assigned by history lookup
    n_rule: int                 # assigned by deterministic rule
    n_llm: int                  # assigned by LLM
    category_accuracy: float    # n_correct / n_transactions
    fuzzy_accuracy: float       # n_correct_fuzzy / n_transactions
    fallback_rate: float        # n_fallback / n_transactions
    duration_seconds: float
    error: str = ""


@dataclass
class CatDetailRow:
    run_id: int
    filename: str
    tx_index: int
    description: str
    amount: str
    expected_category: str
    actual_category: str
    actual_subcategory: str
    actual_full: str
    exact_match: bool
    fuzzy_match: bool
    is_fallback: bool
    source: str
    confidence: str


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
    import subprocess
    print(f"\n[setup] Generating synthetic files via {_GENERATOR_SCRIPT} ...")
    result = subprocess.run(
        [sys.executable, str(_GENERATOR_SCRIPT)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"ERROR: Synthetic file generation failed (rc={result.returncode})")
        print(f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
        sys.exit(1)


def _check_ollama() -> bool:
    """Check if Ollama is reachable."""
    try:
        import urllib.request
        req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def _is_fallback_category(category: str) -> bool:
    """Check if the category is a fallback category."""
    return category in _FALLBACK_CATEGORIES


def _category_exact_match(result: CategorizationResult, expected: str) -> bool:
    """Exact match: f'{result.category}/{result.subcategory}' == expected."""
    if not expected:
        return False
    actual = f"{result.category}/{result.subcategory}"
    return actual.strip().lower() == expected.strip().lower()


def _category_fuzzy_match(result: CategorizationResult, expected: str) -> bool:
    """Fuzzy match: result.category == expected.split('/')[0] (top-level only)."""
    if not expected:
        return False
    expected_top = expected.split("/")[0].strip().lower()
    return result.category.strip().lower() == expected_top


# ── LLM & hardware metadata ──────────────────────────────────────────────

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

    # Try to get Ollama model info
    if "ollama" in meta["provider"].lower():
        try:
            import urllib.request
            req = urllib.request.Request(
                "http://localhost:11434/api/show",
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

    # ── Hardware & OS info ─────────────────────────────────────────────
    import platform
    meta["os"] = f"{platform.system()} {platform.release()} {platform.machine()}"
    try:
        import subprocess as _sp
        ver = _sp.check_output(["sw_vers", "-productVersion"], text=True).strip()
        meta["os_version"] = f"macOS {ver}"
    except Exception:
        meta["os_version"] = platform.platform()
    try:
        import subprocess as _sp
        cpu = _sp.check_output(
            ["sysctl", "-n", "machdep.cpu.brand_string"], text=True
        ).strip()
        meta["cpu"] = cpu
    except Exception:
        meta["cpu"] = platform.processor() or "unknown"
    try:
        import subprocess as _sp
        ram_bytes = int(_sp.check_output(["sysctl", "-n", "hw.memsize"], text=True).strip())
        meta["ram_gb"] = str(round(ram_bytes / (1024**3)))
    except Exception:
        meta["ram_gb"] = "?"
    try:
        import subprocess as _sp
        gpu_info = _sp.check_output(
            ["system_profiler", "SPDisplaysDataType"], text=True
        )
        for line in gpu_info.splitlines():
            if "Chipset Model" in line:
                meta["gpu"] = line.split(":")[-1].strip()
                break
        for line in gpu_info.splitlines():
            if "Total Number of Cores" in line:
                meta["gpu_cores"] = line.split(":")[-1].strip()
                break
    except Exception:
        meta["gpu"] = "?"
        meta["gpu_cores"] = "?"

    return meta


def _write_config_json(meta: dict[str, str], n_runs: int, n_files: int) -> None:
    """Write benchmark config metadata to JSON."""
    meta_out = {**meta, "n_runs": n_runs, "n_files": n_files, "benchmark_type": "categorizer"}
    path = _BENCHMARK_DIR / "cat_benchmark_config.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta_out, f, indent=2, ensure_ascii=False)
    print(f"[output] Config: {path}")


# ── Main evaluation ──────────────────────────────────────────────────────

def _evaluate_file(
    entry: ManifestEntry,
    ground_truth: list[GroundTruthRow],
    backend,
    taxonomy: TaxonomyConfig,
    run_id: int,
) -> tuple[CatRunResult, list[CatDetailRow]]:
    """Normalize a file with its ground truth schema, then categorize and compare."""
    filepath = _GENERATED_DIR / entry.filename
    t_start = time.time()

    error_result = CatRunResult(
        run_id=run_id,
        filename=entry.filename,
        n_transactions=0,
        n_categorized=0,
        n_correct_category=0,
        n_correct_fuzzy=0,
        n_fallback=0,
        n_history=0,
        n_rule=0,
        n_llm=0,
        category_accuracy=0.0,
        fuzzy_accuracy=0.0,
        fallback_rate=0.0,
        duration_seconds=0.0,
    )

    try:
        raw_bytes = filepath.read_bytes()

        # 1. Load raw DataFrame with ground truth skip_rows
        df, encoding, preprocess_info = load_raw_dataframe(
            raw_bytes, entry.filename,
            skip_rows_override=entry.n_header_rows,
        )

        # 2. Classify to get schema (needed for normalize step)
        #    We use LLM classification here because we need the DocumentSchema
        #    to normalize properly. The benchmark measures categorization, not
        #    classification — so classification errors are logged but we proceed.
        schema = classify_document(
            df_raw=df,
            llm_backend=backend,
            source_name=entry.filename,
            sanitize=True,
            header_certain=preprocess_info.header_certain,
        )

        if schema is None:
            error_result.duration_seconds = time.time() - t_start
            error_result.error = "classify_document returned None (no schema)"
            return error_result, []

        # 3. Normalize using the schema (bypass classifier variability)
        transactions, skipped_rows, merge_count = _normalize_df_with_schema(
            df, schema, entry.filename,
        )

        if not transactions:
            error_result.duration_seconds = time.time() - t_start
            error_result.error = f"No transactions after normalization (skipped={len(skipped_rows)})"
            return error_result, []

        n_tx = len(transactions)

        # 4. Categorize using LLM
        cat_results = categorize_batch(
            transactions=transactions,
            taxonomy=taxonomy,
            user_rules=[],
            llm_backend=backend,
            sanitize_config=None,
            fallback_backend=None,
            description_language="it",
            batch_size=20,
            source_name=entry.filename,
            history_cache=None,
        )

        # 5. Compare with ground truth
        n_compare = min(n_tx, len(ground_truth))
        n_correct = 0
        n_correct_fuzzy = 0
        n_fallback = 0
        n_history = 0
        n_rule = 0
        n_llm = 0
        n_categorized = 0
        detail_rows: list[CatDetailRow] = []

        for i in range(n_compare):
            cr = cat_results[i]
            gt = ground_truth[i]
            expected = gt.expected_category

            actual_full = f"{cr.category}/{cr.subcategory}"
            is_fb = _is_fallback_category(cr.category)
            exact = _category_exact_match(cr, expected)
            fuzzy = _category_fuzzy_match(cr, expected)

            if exact:
                n_correct += 1
            if fuzzy:
                n_correct_fuzzy += 1
            if is_fb:
                n_fallback += 1
            else:
                n_categorized += 1

            if cr.source == CategorySource.history:
                n_history += 1
            elif cr.source == CategorySource.rule:
                n_rule += 1
            elif cr.source == CategorySource.llm:
                n_llm += 1

            detail_rows.append(CatDetailRow(
                run_id=run_id,
                filename=entry.filename,
                tx_index=i,
                description=transactions[i].get("description", "")[:80],
                amount=str(transactions[i].get("amount", "")),
                expected_category=expected,
                actual_category=cr.category,
                actual_subcategory=cr.subcategory,
                actual_full=actual_full,
                exact_match=exact,
                fuzzy_match=fuzzy,
                is_fallback=is_fb,
                source=cr.source.value,
                confidence=cr.confidence.value,
            ))

        duration = time.time() - t_start

        return CatRunResult(
            run_id=run_id,
            filename=entry.filename,
            n_transactions=n_compare,
            n_categorized=n_categorized,
            n_correct_category=n_correct,
            n_correct_fuzzy=n_correct_fuzzy,
            n_fallback=n_fallback,
            n_history=n_history,
            n_rule=n_rule,
            n_llm=n_llm,
            category_accuracy=n_correct / n_compare if n_compare > 0 else 0.0,
            fuzzy_accuracy=n_correct_fuzzy / n_compare if n_compare > 0 else 0.0,
            fallback_rate=n_fallback / n_compare if n_compare > 0 else 0.0,
            duration_seconds=duration,
        ), detail_rows

    except Exception as e:
        error_result.duration_seconds = time.time() - t_start
        error_result.error = f"{type(e).__name__}: {e}"
        return error_result, []


# ── CSV output ────────────────────────────────────────────────────────────

_CSV_HEADER = [
    "run_id", "filename",
    "git_commit", "git_branch",
    "provider", "model", "temperature", "parameter_size", "quantization",
    "n_transactions", "n_categorized",
    "n_correct_category", "n_correct_fuzzy",
    "n_fallback", "n_history", "n_rule", "n_llm",
    "category_accuracy", "fuzzy_accuracy", "fallback_rate",
    "duration_seconds", "error",
]

_DETAIL_CSV_HEADER = [
    "run_id", "filename", "git_commit", "git_branch", "tx_index",
    "description", "amount",
    "expected_category", "actual_category", "actual_subcategory", "actual_full",
    "exact_match", "fuzzy_match", "is_fallback",
    "source", "confidence",
]

# Filled at runtime by main()
_LLM_META: dict[str, str] = {}


def _result_to_row(r: CatRunResult) -> list:
    return [
        r.run_id, r.filename,
        _LLM_META.get("git_commit", ""),
        _LLM_META.get("git_branch", ""),
        _LLM_META.get("provider", ""),
        _LLM_META.get("model", ""),
        _LLM_META.get("temperature", ""),
        _LLM_META.get("parameter_size", ""),
        _LLM_META.get("quantization", ""),
        r.n_transactions, r.n_categorized,
        r.n_correct_category, r.n_correct_fuzzy,
        r.n_fallback, r.n_history, r.n_rule, r.n_llm,
        f"{r.category_accuracy:.4f}", f"{r.fuzzy_accuracy:.4f}", f"{r.fallback_rate:.4f}",
        f"{r.duration_seconds:.2f}", r.error,
    ]


def _detail_to_row(d: CatDetailRow) -> list:
    return [
        d.run_id, d.filename,
        _LLM_META.get("git_commit", ""),
        _LLM_META.get("git_branch", ""),
        d.tx_index,
        d.description, d.amount,
        d.expected_category, d.actual_category, d.actual_subcategory, d.actual_full,
        d.exact_match, d.fuzzy_match, d.is_fallback,
        d.source, d.confidence,
    ]


def _write_all_runs_csv(all_results: list[CatRunResult]) -> None:
    """Write all runs concatenated."""
    path = _BENCHMARK_DIR / "cat_results_all_runs.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(_CSV_HEADER)
        for r in all_results:
            writer.writerow(_result_to_row(r))
    print(f"[output] All runs: {path}")


def _write_detail_csv(all_details: list[CatDetailRow]) -> None:
    """Write per-transaction detail CSV."""
    path = _BENCHMARK_DIR / "cat_results_detail.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(_DETAIL_CSV_HEADER)
        for d in all_details:
            writer.writerow(_detail_to_row(d))
    print(f"[output] Detail: {path}")


# ── Variance computation ──────────────────────────────────────────────────

def _compute_variance(all_results: list[CatRunResult]) -> tuple[list[dict], list[dict]]:
    """Compute per-file and global variance metrics.

    Returns (per_file_rows, global_rows).
    """
    metrics = [
        "category_accuracy", "fuzzy_accuracy", "fallback_rate",
    ]

    by_file: dict[str, list[CatRunResult]] = {}
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


# ── Console output ────────────────────────────────────────────────────────

_METRIC_LABELS = {
    "category_accuracy": "Exact accuracy",
    "fuzzy_accuracy": "Fuzzy accuracy",
    "fallback_rate": "Fallback rate",
}


def _print_summary(
    global_rows: list[dict],
    all_results: list[CatRunResult],
    n_runs: int,
    n_files: int,
    total_time: float,
) -> None:
    """Print the final summary table."""
    print()
    print("+" + "=" * 74 + "+")
    print(f"|{'CATEGORIZER BENCHMARK RESULTS':^74}|")
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
    print("+" + "=" * 74 + "+")

    # Source breakdown across all results
    total_tx = sum(r.n_transactions for r in all_results)
    total_rule = sum(r.n_rule for r in all_results)
    total_history = sum(r.n_history for r in all_results)
    total_llm = sum(r.n_llm for r in all_results)
    total_fallback = sum(r.n_fallback for r in all_results)

    print()
    print(f"  Source breakdown ({total_tx} total transactions across all runs):")
    if total_tx > 0:
        print(f"    Rule:     {total_rule:>6} ({total_rule / total_tx:>6.1%})")
        print(f"    History:  {total_history:>6} ({total_history / total_tx:>6.1%})")
        print(f"    LLM:      {total_llm:>6} ({total_llm / total_tx:>6.1%})")
        print(f"    Fallback: {total_fallback:>6} ({total_fallback / total_tx:>6.1%})")

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
        description="Benchmark: N runs of categorization pipeline on synthetic files."
    )
    parser.add_argument("--runs", type=int, default=N_RUNS_DEFAULT,
                        help=f"Number of runs (default: {N_RUNS_DEFAULT})")
    parser.add_argument("--files", type=str, default=_FILES_DEFAULT,
                        help=f"Glob pattern to filter files (default: {_FILES_DEFAULT})")
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

    # Startup
    print(f"\n{'=' * 60}")
    print(f"  Spendify Categorizer Benchmark")
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

    # Build taxonomy from Italian defaults (no DB)
    taxonomy = _build_taxonomy_from_defaults("it")
    print(f"[config] Taxonomy: {len(taxonomy.expenses)} expense + {len(taxonomy.income)} income categories (Italian defaults)")

    # Build LLM backend once (reused across runs)
    config = ProcessingConfig()
    if backend_override:
        config.llm_backend = backend_override
    if model_path_override and backend_override == "local_llama_cpp":
        config.llama_cpp_model_path = model_path_override
    if model_override:
        config.ollama_model = model_override
    backend = _build_backend(config)

    # ── Collect LLM metadata ─────────────────────────────────────────────
    llm_meta = _collect_llm_metadata(config, backend)
    _LLM_META.update(llm_meta)

    print(f"\n[config] Provider: {llm_meta['provider']}")
    print(f"[config] Model: {llm_meta['model']}")
    print(f"[config] Temperature: {llm_meta['temperature']}")
    if llm_meta.get("parameter_size", "?") != "?":
        print(f"[config] Parameters: {llm_meta['parameter_size']}, Quant: {llm_meta['quantization']}")
    print(f"[config] Timeout: {llm_meta['llm_timeout_s']}s")
    print(f"[hardware] OS: {llm_meta.get('os_version', llm_meta.get('os', '?'))}")
    print(f"[hardware] CPU: {llm_meta.get('cpu', '?')}")
    print(f"[hardware] RAM: {llm_meta.get('ram_gb', '?')} GB")
    print(f"[hardware] GPU: {llm_meta.get('gpu', '?')} ({llm_meta.get('gpu_cores', '?')} cores)")

    # Run benchmark
    all_results: list[CatRunResult] = []
    all_details: list[CatDetailRow] = []
    total_start = time.time()

    for run_id in range(1, n_runs + 1):
        run_results: list[CatRunResult] = []
        run_start = time.time()

        for file_idx, entry in enumerate(manifest, 1):
            print(
                f"  [Run {run_id:>{len(str(n_runs))}}/{n_runs}] "
                f"[File {file_idx:>{len(str(n_files))}}/{n_files}] "
                f"{entry.filename}...",
                end="", flush=True,
            )

            gt = ground_truth_map.get(entry.filename, [])
            result, details = _evaluate_file(entry, gt, backend, taxonomy, run_id)
            run_results.append(result)
            all_details.extend(details)

            status = "OK" if not result.error else f"ERR: {result.error[:40]}"
            print(
                f" {result.duration_seconds:.1f}s "
                f"exact={result.category_accuracy:.0%} "
                f"fuzzy={result.fuzzy_accuracy:.0%} "
                f"fb={result.fallback_rate:.0%} "
                f"r/h/l={result.n_rule}/{result.n_history}/{result.n_llm} "
                f"[{status}]"
            )

        run_duration = time.time() - run_start
        all_results.extend(run_results)

        # Run summary
        n_ok = sum(1 for r in run_results if not r.error)
        n_err = sum(1 for r in run_results if r.error)
        avg_exact = sum(r.category_accuracy for r in run_results) / len(run_results) if run_results else 0
        avg_fuzzy = sum(r.fuzzy_accuracy for r in run_results) / len(run_results) if run_results else 0
        print(
            f"  --- Run {run_id} complete: {run_duration:.0f}s, "
            f"{n_ok} OK / {n_err} errors, "
            f"exact={avg_exact:.0%}, fuzzy={avg_fuzzy:.0%}"
        )
        print()

    total_time = time.time() - total_start

    # Write all outputs
    _write_config_json(llm_meta, n_runs, n_files)
    _write_all_runs_csv(all_results)
    _write_detail_csv(all_details)
    per_file_rows, global_rows = _compute_variance(all_results)

    # Print summary
    _print_summary(global_rows, all_results, n_runs, n_files, total_time)

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
