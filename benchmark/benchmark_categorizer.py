#!/usr/bin/env python3
"""Benchmark: N runs of categorization pipeline on synthetic files.

Measures LLM variability for the categorization step by repeating the same
normalized input N times. The classifier step is bypassed — we use the ground
truth schema directly — so this benchmark isolates categorization accuracy.

Usage:
    python benchmark/benchmark_categorizer.py [--runs N] [--files PATTERN]

    --runs N           Number of runs (default: 10)
    --files PAT        Glob pattern to filter files (default: *_S_*)
    --backend NAME     LLM backend (default: from user settings or local_llama_cpp)
    --model MODEL      Model name for Ollama/OpenAI/Claude (default: from user settings)
    --model-path PATH  GGUF path for llama-cpp
    --scenario SC      Benchmark scenario (default: cold):
                         cold          — pure LLM, no warm data
                         nsi_warm      — NSI + taxonomy_map
                         cross_warm    — leave-one-out: history from all GT files
                                         *except* the current one (realistic warm-up)
                         full_warm     — history from ALL GT files (upper bound)
                         country_with  — nsi_warm + country ranking
                         country_without — nsi_warm without country ranking
                         all           — all scenarios sequentially
"""
from __future__ import annotations

import argparse
import csv
import os
import subprocess
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
from core.llm_backends import LlamaCppBackend, DEFAULT_GGUF_MODELS
from db.taxonomy_defaults import TAXONOMY_DEFAULTS
from services.nsi_taxonomy_service import NsiTaxonomyService

# ── Paths ─────────────────────────────────────────────────────────────────
_TESTS_DIR = Path(__file__).resolve().parent
_GENERATED_DIR = _TESTS_DIR / "generated_files"
_MANIFEST_PATH = _GENERATED_DIR / "manifest.csv"
_BENCHMARK_DIR = _TESTS_DIR / "results"
# Shared results file in documents repo (cross-HW, pushed/pulled via git)
_DOCS_BENCHMARK_DIR = PROJECT_ROOT.parent / "documents" / "04_software_engineering" / "benchmark"
_RESULTS_ARCHIVE_DIR = _TESTS_DIR / "results"
_ARCHIVE_CSV_PATH: Path | None = None  # Set by main() before run loop

N_RUNS_DEFAULT = 10
_FILES_DEFAULT = "*"

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
    cpu_load_avg: float = 0.0
    gpu_utilization_pct: float = 0.0
    cleaner_batch_size: int = 30
    error: str = ""
    cat_duration_s: float = 0.0
    # C-08-bench: NSI / cascade metrics
    scenario: str = "cold"         # cold | nsi_warm | full_warm | country_with | country_without
    n_nsi: int = 0                 # tx resolved by NSI → taxonomy_map
    nsi_accuracy: float = 0.0     # exact accuracy among NSI-resolved tx
    nsi_coverage_pct: float = 0.0 # n_nsi / n_transactions
    taxonomy_map_hit_pct: float = 0.0  # n_nsi / total NSI matches (map hit rate)
    # Per-direction breakdown (expense vs income)
    n_expense: int = 0
    n_income: int = 0
    n_expense_correct: int = 0
    n_income_correct: int = 0
    n_expense_correct_fuzzy: int = 0
    n_income_correct_fuzzy: int = 0
    n_expense_fallback: int = 0
    n_income_fallback: int = 0
    expense_exact_accuracy: float = 0.0
    income_exact_accuracy: float = 0.0
    expense_fuzzy_accuracy: float = 0.0
    income_fuzzy_accuracy: float = 0.0
    # Token usage
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


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
    """Verifica che i file sintetici esistano — NON li genera automaticamente.

    I file sintetici devono essere generati esplicitamente dall'utente prima
    di avviare il benchmark. Generarli a ogni run comprometterebbe il
    determinismo: variazioni nei risultati non sarebbero attribuibili solo a
    HW / runtime / modello, ma anche a differenze nell'input.

    Per generare i file sintetici:
        uv run python benchmark/generate_synthetic_files.py
    """
    if _MANIFEST_PATH.exists():
        return
    print()
    print("ERROR: File sintetici non trovati.")
    print(f"       Manifest atteso: {_MANIFEST_PATH}")
    print()
    print("  I file sintetici devono essere generati PRIMA del benchmark")
    print("  e restare IMMUTATI tra le run per garantire il determinismo")
    print("  (le differenze nei risultati devono dipendere solo da HW /")
    print("  runtime / modello, non dall'input).")
    print()
    print("  Generali con:")
    print("    uv run python benchmark/generate_synthetic_files.py")
    print()
    sys.exit(1)


def _ensure_llamacpp_model(model_path_override: str | None) -> str | None:
    """Ensure a GGUF model is available for llama.cpp, downloading if needed.

    Returns the model path to use (None = use default detection).
    """
    if model_path_override and Path(model_path_override).exists():
        return model_path_override

    # Check if any .gguf already exists in default dir
    models_dir = Path.home() / ".spendifai" / "models"
    existing = sorted(models_dir.glob("*.gguf")) if models_dir.exists() else []
    if existing:
        chosen = str(existing[0])
        print(f"[check] llama.cpp: using existing model {Path(chosen).name}")
        return chosen

    # Auto-download first suggested model
    first_key = next(iter(DEFAULT_GGUF_MODELS))
    info = DEFAULT_GGUF_MODELS[first_key]
    print(f"[check] llama.cpp: no model found, downloading {first_key} ({info['size_gb']} GB)...")
    dest = LlamaCppBackend.download_model(info["url"])
    print(f"[check] llama.cpp: downloaded → {dest}")
    return dest


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

def _read_version() -> str:
    """Read version from benchmark/.version (portable, no git needed).
    Format: YYYYMMDDHHMMSS-sha7  e.g. 20260404143022-09e24c2
    Falls back to git rev-parse if .version missing."""
    version_file = _TESTS_DIR / ".version"
    if version_file.exists():
        return version_file.read_text().strip()
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=PROJECT_ROOT, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        return f"{ts}-{sha}"
    except Exception:
        return datetime.now().strftime("%Y%m%d%H%M%S") + "-unknown"


def _detect_gpu_ram_gb(cpu_str: str = "", ram_gb_str: str = "0") -> str:
    """Detect GPU/VRAM in GB. Cross-platform.
    - Apple Silicon: unified memory → returns runtime_ram_gb (shared pool)
    - NVIDIA: nvidia-smi
    - Windows discrete/integrated: wmic
    - Fallback: '0'
    """
    import platform
    # Apple Silicon: unified memory shared between CPU and GPU
    if "apple" in cpu_str.lower() or platform.processor() == "arm":
        return ram_gb_str  # same as system RAM
    # NVIDIA
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            text=True, timeout=5
        )
        mb = int(out.strip().split("\n")[0])
        return str(round(mb / 1024, 1))
    except Exception:
        pass
    # Windows: wmic
    try:
        out = subprocess.check_output(
            ["wmic", "path", "Win32_VideoController", "get", "AdapterRAM", "/value"],
            text=True, timeout=5
        )
        for line in out.splitlines():
            if "=" in line:
                val = line.split("=", 1)[1].strip()
                if val.isdigit() and int(val) > 0:
                    return str(round(int(val) / (1024 ** 3), 1))
    except Exception:
        pass
    # Linux: /sys
    try:
        import glob
        for path in glob.glob("/sys/class/drm/card*/device/mem_info_vram_total"):
            with open(path) as f:
                val = int(f.read().strip())
                if val > 0:
                    return str(round(val / (1024 ** 3), 1))
    except Exception:
        pass
    return "0"


def _collect_llm_metadata(config: ProcessingConfig, backend) -> dict[str, str]:
    """Collect LLM provider, model, and parameters for benchmark metadata."""
    # Version info — read from .version first (portable, no git needed on bench machines)
    _version = _read_version()
    _git_sha = _version.split("-")[-1] if "-" in _version else _version
    _git_branch = "unknown"
    try:
        _git_branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=PROJECT_ROOT, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        pass

    meta: dict[str, str] = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "git_commit": _git_sha,
        "git_branch": _git_branch,
        "version": _version,
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

    # ── Runtime HW (where Spendif.ai runs) ────────────────────────────────
    from hw_detect import detect_hw
    _hw = detect_hw()
    meta["runtime_os"] = _hw["runtime_os"]
    meta["runtime_hostname"] = _hw["runtime_hostname"]
    meta["runtime_cpu"] = _hw["runtime_cpu"]
    meta["runtime_ram_gb"] = _hw["runtime_ram_gb"]
    meta["runtime_gpu"] = _hw["runtime_gpu"]
    meta["runtime_gpu_cores"] = _hw["runtime_gpu_cores"]
    meta["runtime_gpu_ram_gb"] = _detect_gpu_ram_gb(
        meta.get("runtime_cpu", ""),
        meta.get("runtime_ram_gb", "0")
    )

    # ── LLM HW (where the model runs) ────────────────────────────────
    if "ollama" in meta["provider"].lower():
        ollama_url = getattr(config, "ollama_base_url", None) or "http://localhost:11434"
        is_local = "localhost" in ollama_url or "127.0.0.1" in ollama_url
        meta["llm_host"] = ollama_url
        meta["llm_hw"] = "same as runtime" if is_local else f"remote ({ollama_url})"
        # Ollama inference params (defaults)
        from core.llm_backends import OllamaBackend as _OllamaBackend
        _detected_ctx = _OllamaBackend.fetch_context_length(
            getattr(config, "ollama_model", ""),
            getattr(config, "ollama_base_url", "http://localhost:11434"),
        )
        meta["n_ctx"] = str(_detected_ctx) if _detected_ctx else "unknown"
        meta["n_batch"] = "512"
        meta["n_threads"] = "auto"
        meta["n_gpu_layers"] = "all"
        meta["flash_attn"] = "auto"
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
            model_params = getattr(llm_obj, "model_params", None)
            if model_params and hasattr(model_params, "flash_attn"):
                meta["flash_attn"] = str(model_params.flash_attn)
            else:
                meta["flash_attn"] = "?"
    elif "vllm" in meta["provider"].lower():
        meta["llm_host"] = getattr(config, "vllm_base_url", "http://localhost:8000/v1")
        meta["llm_hw"] = "same as runtime" if "localhost" in meta["llm_host"] or "127.0.0.1" in meta["llm_host"] else "remote"
    elif "openai" in meta["provider"].lower() or "claude" in meta["provider"].lower():
        meta["llm_host"] = "cloud API"
        meta["llm_hw"] = "cloud"
    else:
        meta["llm_host"] = "unknown"
        meta["llm_hw"] = "unknown"

    return meta


def _write_config_json(meta: dict[str, str], n_runs: int, n_files: int) -> None:
    """Write benchmark config metadata to JSON in both local and documents repo."""
    meta_out = {**meta, "n_runs": n_runs, "n_files": n_files, "benchmark_type": "categorizer"}
    for target_dir in (_BENCHMARK_DIR, _DOCS_BENCHMARK_DIR):
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / "cat_benchmark_config.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(meta_out, f, indent=2, ensure_ascii=False)
    print(f"[output] Config: {_DOCS_BENCHMARK_DIR / 'cat_benchmark_config.json'}")


# ── C-08-bench: warm scenario fixtures ───────────────────────────────────

def _build_real_taxonomy_map(taxonomy: TaxonomyConfig) -> dict[str, tuple[str, str]]:
    """Build OSM tag → (categoria, sottocategoria) from osm_to_spendifai_map.json.

    Uses the real static mapping file validated against the user's taxonomy — the same
    logic that NsiTaxonomyService uses as static fallback during onboarding.
    No DB connection, no LLM call required.

    Replaces the old _SYNTHETIC_TAXONOMY_MAP hardcoded dict.
    """
    try:
        # Bypass __init__ to avoid requiring a DB engine — _collect_osm_tags and
        # _static_map only use class-level path constants, not self.engine/_Session.
        svc = NsiTaxonomyService.__new__(NsiTaxonomyService)
        tags = svc._collect_osm_tags()
        result = svc._static_map(tags, taxonomy)
        print(f"[taxonomy_map] Loaded {len(result)} OSM tag mappings from osm_to_spendifai_map.json")
        return result
    except Exception as exc:
        print(f"[WARN] _build_real_taxonomy_map failed: {exc} — warm scenarios will use empty map")
        return {}


class _SyntheticHistoryCache:
    """Benchmark-only history cache pre-populated from ground truth.

    Mimics the HistoryCache.lookup() interface so it can be passed directly
    to categorize_batch() without a DB session.
    """

    def __init__(self, ground_truth: list[GroundTruthRow]) -> None:
        # description_raw → (category, subcategory) from ground truth
        self._lookup_dict: dict[str, tuple[str, str]] = {}
        for row in ground_truth:
            if row.expected_category and "/" in row.expected_category:
                cat, sub = row.expected_category.split("/", 1)
                self._lookup_dict[row.description_raw] = (cat.strip(), sub.strip())

    def lookup(self, description: str) -> tuple[str | None, str | None, float]:
        pair = self._lookup_dict.get(description)
        if pair:
            return pair[0], pair[1], 1.0  # confidence=1.0: ground truth is certain
        return None, None, 0.0

    @property
    def _cache(self) -> dict:
        """Expose internal cache as DescriptionProfile dict — used by get_top_associations_text."""
        from core.history_engine import DescriptionProfile
        result = {}
        for desc, (cat, sub) in self._lookup_dict.items():
            result[desc] = DescriptionProfile(
                description=desc,
                associations=[],
                total_validated=5,
                homogeneity=1.0,
                confidence=1.0,
                top_category=cat,
                top_subcategory=sub,
            )
        return result


# ── Main evaluation ──────────────────────────────────────────────────────

from tests.hw_monitor import HWMonitor


def _evaluate_file(
    entry: ManifestEntry,
    ground_truth: list[GroundTruthRow],
    backend,
    taxonomy: TaxonomyConfig,
    run_id: int,
    cleaner_batch_size: int = 30,
    scenario: str = "cold",
    taxonomy_map: dict[str, tuple[str, str]] | None = None,
    history_cache=None,
    user_rules: list | None = None,
    user_country: str | None = None,
) -> tuple[CatRunResult, list[CatDetailRow]]:
    """Normalize a file with its ground truth schema, then categorize and compare."""
    filepath = _GENERATED_DIR / entry.filename
    t_start = time.time()
    hw = HWMonitor(interval=0.5)
    hw.start()

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
        scenario=scenario,
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
            account_type=entry.doc_type,
            classifier_mode="auto",
        )

        if schema is None:
            error_result.duration_seconds = time.time() - t_start
            hw.stop()
            error_result.error = "classify_document returned None (no schema)"
            return error_result, []

        # 3. Normalize using the schema (bypass classifier variability)
        transactions, skipped_rows, merge_count = _normalize_df_with_schema(
            df, schema, entry.filename,
        )

        if not transactions:
            error_result.duration_seconds = time.time() - t_start
            hw.stop()
            error_result.error = f"No transactions after normalization (skipped={len(skipped_rows)})"
            return error_result, []

        n_tx = len(transactions)

        # 3b. Clean descriptions: extract counterpart name (as in production)
        # Reset cumulative token counter before categorizer LLM calls
        if hasattr(backend, 'reset_cumulative_usage'):
            backend.reset_cumulative_usage()
        from core.description_cleaner import clean_descriptions_batch
        transactions = clean_descriptions_batch(
            transactions,
            llm_backend=backend,
            fallback_backend=None,
            batch_size=cleaner_batch_size,
            source_name=entry.filename,
            sanitize_config=None,
        )

        # 3c. Patch synthetic history cache with cleaned descriptions.
        #     _SyntheticHistoryCache is built from description_raw (GT), but
        #     categorize_batch receives the LLM-cleaned description (merchant name).
        #     We pair by position (same row order) to add clean→category mappings.
        if history_cache is not None and isinstance(history_cache, _SyntheticHistoryCache):
            n_patched = 0
            for i, tx in enumerate(transactions):
                if i >= len(ground_truth):
                    break
                gt_row = ground_truth[i]
                clean_desc = tx.get("description", "")
                if clean_desc and gt_row.expected_category and "/" in gt_row.expected_category:
                    cat, sub = gt_row.expected_category.split("/", 1)
                    history_cache._lookup_dict[clean_desc] = (cat.strip(), sub.strip())
                    n_patched += 1
            if n_patched:
                print(f"  [history_cache] patched {n_patched} clean descriptions for {entry.filename}")

        # 4. Categorize (warm fixtures injected per scenario)
        cat_results = categorize_batch(
            transactions=transactions,
            taxonomy=taxonomy,
            user_rules=user_rules or [],
            llm_backend=backend,
            sanitize_config=None,
            fallback_backend=None,
            description_language="it",
            batch_size=20,
            source_name=entry.filename,
            history_cache=history_cache,
            taxonomy_map=taxonomy_map,
            user_country=user_country,
        )

        # Sample HW after categorization
        # HW sampling handled by background HWMonitor thread

        # 5. Compare with ground truth
        n_compare = min(n_tx, len(ground_truth))
        n_correct = 0
        n_correct_fuzzy = 0
        n_fallback = 0
        n_history = 0
        n_rule = 0
        n_llm = 0
        n_nsi = 0
        n_nsi_correct = 0
        n_categorized = 0
        # Per-direction counters
        n_expense = n_income = 0
        n_expense_correct = n_income_correct = 0
        n_expense_correct_fuzzy = n_income_correct_fuzzy = 0
        n_expense_fallback = n_income_fallback = 0
        detail_rows: list[CatDetailRow] = []

        for i in range(n_compare):
            cr = cat_results[i]
            gt = ground_truth[i]
            expected = gt.expected_category

            actual_full = f"{cr.category}/{cr.subcategory}"
            is_fb = _is_fallback_category(cr.category)
            exact = _category_exact_match(cr, expected)
            fuzzy = _category_fuzzy_match(cr, expected)

            # Determine direction from transaction amount
            try:
                _amt = float(str(transactions[i].get("amount", 0)).replace(",", "."))
            except (ValueError, TypeError):
                _amt = 0.0
            is_expense = _amt < 0

            if exact:
                n_correct += 1
            if fuzzy:
                n_correct_fuzzy += 1
            if is_fb:
                n_fallback += 1
            else:
                n_categorized += 1

            # Per-direction tracking
            if is_expense:
                n_expense += 1
                if exact:
                    n_expense_correct += 1
                if fuzzy:
                    n_expense_correct_fuzzy += 1
                if is_fb:
                    n_expense_fallback += 1
            else:
                n_income += 1
                if exact:
                    n_income_correct += 1
                if fuzzy:
                    n_income_correct_fuzzy += 1
                if is_fb:
                    n_income_fallback += 1

            if cr.source == CategorySource.history:
                n_history += 1
            elif cr.source == CategorySource.rule:
                n_rule += 1
            elif cr.source == CategorySource.llm:
                n_llm += 1
            elif cr.source == CategorySource.nsi:
                n_nsi += 1
                if exact:
                    n_nsi_correct += 1

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
        hw_stats = hw.stop()

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
            cpu_load_avg=hw_stats.cpu_avg,
            gpu_utilization_pct=hw_stats.gpu_avg,
            cleaner_batch_size=cleaner_batch_size,
            scenario=scenario,
            n_nsi=n_nsi,
            nsi_accuracy=n_nsi_correct / n_nsi if n_nsi > 0 else 0.0,
            nsi_coverage_pct=n_nsi / n_compare if n_compare > 0 else 0.0,
            taxonomy_map_hit_pct=n_nsi / n_compare if n_compare > 0 else 0.0,
            # Per-direction breakdown
            n_expense=n_expense,
            n_income=n_income,
            n_expense_correct=n_expense_correct,
            n_income_correct=n_income_correct,
            n_expense_correct_fuzzy=n_expense_correct_fuzzy,
            n_income_correct_fuzzy=n_income_correct_fuzzy,
            n_expense_fallback=n_expense_fallback,
            n_income_fallback=n_income_fallback,
            expense_exact_accuracy=n_expense_correct / n_expense if n_expense > 0 else 0.0,
            income_exact_accuracy=n_income_correct / n_income if n_income > 0 else 0.0,
            expense_fuzzy_accuracy=n_expense_correct_fuzzy / n_expense if n_expense > 0 else 0.0,
            income_fuzzy_accuracy=n_income_correct_fuzzy / n_income if n_income > 0 else 0.0,
            # Token usage (cleaner + categorizer combined)
            prompt_tokens=backend.cumulative_usage.get("prompt_tokens", 0) if hasattr(backend, "cumulative_usage") else 0,
            completion_tokens=backend.cumulative_usage.get("completion_tokens", 0) if hasattr(backend, "cumulative_usage") else 0,
            total_tokens=backend.cumulative_usage.get("total_tokens", 0) if hasattr(backend, "cumulative_usage") else 0,
        ), detail_rows

    except Exception as e:
        error_result.duration_seconds = time.time() - t_start
        hw.stop()
        error_result.error = f"{type(e).__name__}: {e}"
        return error_result, []


# ── CSV output ────────────────────────────────────────────────────────────

# Shared CSV header — same file as classifier benchmark (results_all_runs.csv)
_CSV_HEADER = [
    "benchmark_type",  # "classifier" or "categorizer"
    "run_id", "filename",
    "git_commit", "git_branch", "version",
    "provider", "model", "temperature", "parameter_size", "quantization",
    # Inference parameters (for reproducibility & performance analysis)
    "n_ctx", "n_batch", "n_threads", "n_gpu_layers", "flash_attn",
    # Runtime HW
    "runtime_os", "runtime_cpu", "runtime_ram_gb", "runtime_gpu", "runtime_gpu_cores",
    "runtime_hostname",
    "runtime_gpu_ram_gb",
    # File characteristics (from manifest — self-contained row)
    "file_doc_type", "file_format", "file_amount_format",
    "file_n_header_rows", "file_n_data_rows", "file_n_footer_rows",
    "file_has_debit_credit_split", "file_has_borders",
    "file_n_income_rows", "file_n_expense_rows", "file_n_internal_transfers",
    # Classifier results (empty for categorizer rows)
    "header_detected", "header_expected", "header_match",
    "rows_detected", "rows_expected", "rows_match",
    "doc_type_detected", "doc_type_expected", "doc_type_match",
    "convention_detected", "convention_expected", "convention_match",
    "confidence_score",
    "n_parsed", "n_expected", "parse_rate",
    "amount_correct", "amount_total", "amount_accuracy",
    "date_correct", "date_total", "date_accuracy",
    "category_correct", "category_total", "category_accuracy",
    # Categorizer results
    "n_transactions", "n_categorized",
    "n_correct_category", "n_correct_fuzzy",
    "n_fallback", "n_history", "n_rule", "n_llm",
    "cat_exact_accuracy", "cat_fuzzy_accuracy", "cat_fallback_rate",
    # C-08-bench: NSI / cascade scenario metrics
    "scenario", "n_nsi", "nsi_accuracy", "nsi_coverage_pct", "taxonomy_map_hit_pct",
    # Per-direction breakdown
    "n_expense", "n_income",
    "n_expense_correct", "n_income_correct",
    "n_expense_correct_fuzzy", "n_income_correct_fuzzy",
    "n_expense_fallback", "n_income_fallback",
    "expense_exact_accuracy", "income_exact_accuracy",
    "expense_fuzzy_accuracy", "income_fuzzy_accuracy",
    # Common
    "duration_seconds",
    # HW stress (sampled during file processing)
    "cpu_load_avg", "gpu_utilization_pct",
    # Token usage (for cost tracking with remote APIs)
    "prompt_tokens", "completion_tokens", "total_tokens",
    "tokens_per_second",
    # Phase 0 → LLM → merge traceability (empty for categorizer rows)
    "phase0_sign_convention", "phase0_debit_col", "phase0_credit_col",
    "llm_debit_col", "llm_credit_col", "llm_invert_sign",
    "final_debit_col", "final_credit_col", "final_invert_sign",
    # Multi-step classifier diagnostics (empty for categorizer rows)
    "classifier_mode",
    "step1_time_s", "step2_time_s", "step3_time_s",
    "step1_doc_type_match", "step2_date_col_match", "step2_amount_col_match",
    "error",
    # Timing aliases (for cross-script column alignment)
    "classifier_duration_s",
    "cat_duration_s",
    "cleaner_batch_size",
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
        "categorizer",  # benchmark_type
        r.run_id, r.filename,
        _LLM_META.get("git_commit", ""),
        _LLM_META.get("git_branch", ""),
        _LLM_META.get("version", ""),
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
        _LLM_META.get("runtime_gpu_cores", ""),
        _LLM_META.get("runtime_hostname", ""),
        _LLM_META.get("runtime_gpu_ram_gb", ""),
        # File characteristics (empty for categorizer standalone rows)
        "", "", "",  # file_doc_type, file_format, file_amount_format
        "", "", "",  # file_n_header_rows, file_n_data_rows, file_n_footer_rows
        "", "",      # file_has_debit_credit_split, file_has_borders
        "", "", "",  # file_n_income_rows, file_n_expense_rows, file_n_internal_transfers
        # Classifier columns (empty for categorizer rows)
        "", "", "",  # header_detected, header_expected, header_match
        "", "", "",  # rows_detected, rows_expected, rows_match
        "", "", "",  # doc_type_detected, doc_type_expected, doc_type_match
        "", "", "",  # convention_detected, convention_expected, convention_match
        "",          # confidence_score
        "", "", "",  # n_parsed, n_expected, parse_rate
        "", "", "",  # amount_correct, amount_total, amount_accuracy
        "", "", "",  # date_correct, date_total, date_accuracy
        "", "", "",  # category_correct, category_total, category_accuracy
        # Categorizer results
        r.n_transactions, r.n_categorized,
        r.n_correct_category, r.n_correct_fuzzy,
        r.n_fallback, r.n_history, r.n_rule, r.n_llm,
        f"{r.category_accuracy:.4f}", f"{r.fuzzy_accuracy:.4f}", f"{r.fallback_rate:.4f}",
        # C-08-bench: NSI / cascade scenario metrics
        r.scenario,
        r.n_nsi,
        f"{r.nsi_accuracy:.4f}",
        f"{r.nsi_coverage_pct:.4f}",
        f"{r.taxonomy_map_hit_pct:.4f}",
        # Per-direction breakdown
        r.n_expense, r.n_income,
        r.n_expense_correct, r.n_income_correct,
        r.n_expense_correct_fuzzy, r.n_income_correct_fuzzy,
        r.n_expense_fallback, r.n_income_fallback,
        f"{r.expense_exact_accuracy:.4f}", f"{r.income_exact_accuracy:.4f}",
        f"{r.expense_fuzzy_accuracy:.4f}", f"{r.income_fuzzy_accuracy:.4f}",
        # Common
        f"{r.duration_seconds:.2f}",
        f"{r.cpu_load_avg:.2f}", f"{r.gpu_utilization_pct:.1f}",
        # Token usage
        r.prompt_tokens, r.completion_tokens, r.total_tokens,
        f"{r.total_tokens / r.duration_seconds:.1f}" if r.duration_seconds > 0 and r.total_tokens > 0 else "0",
        # Phase 0 → LLM → merge traceability (empty for categorizer rows)
        "", "", "",  # phase0_sign_convention, phase0_debit_col, phase0_credit_col
        "", "", "",  # llm_debit_col, llm_credit_col, llm_invert_sign
        "", "", "",  # final_debit_col, final_credit_col, final_invert_sign
        # Multi-step classifier diagnostics (empty for categorizer rows)
        "", "", "", "", "", "", "",
        r.error,
        # Timing aliases
        "",                           # classifier_duration_s (empty for categorizer rows)
        f"{r.duration_seconds:.2f}",  # cat_duration_s
        r.cleaner_batch_size,         # cleaner_batch_size
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


def _init_archive_path() -> Path:
    """Return a fresh (non-existing) path for this run's archive CSV.
    Format: results/<version>_<hostname>.csv
    Adds _2, _3 suffix if file already exists."""
    version = _read_version()
    hostname = __import__("socket").gethostname()
    # sanitize hostname for filesystem
    safe_host = hostname.replace(" ", "_").replace("/", "_")
    _RESULTS_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    base = _RESULTS_ARCHIVE_DIR / f"{version}_{safe_host}.csv"
    if not base.exists():
        return base
    i = 2
    while True:
        candidate = _RESULTS_ARCHIVE_DIR / f"{version}_{safe_host}_{i}.csv"
        if not candidate.exists():
            return candidate
        i += 1


def _init_archive_csv() -> None:
    """Write CSV header to archive file. Called once at startup before the run loop."""
    if _ARCHIVE_CSV_PATH is None:
        return
    with open(_ARCHIVE_CSV_PATH, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(_CSV_HEADER)


def _append_result_to_archive(result: "CatRunResult") -> None:
    """Append a single result row to the archive CSV immediately (per-file flush)."""
    if _ARCHIVE_CSV_PATH is None:
        return
    with open(_ARCHIVE_CSV_PATH, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(_result_to_row(result))


def _write_all_runs_csv(all_results: list[CatRunResult]) -> None:
    """Append new results to shared all-runs CSV in both local and documents repo."""
    for target_dir in (_BENCHMARK_DIR, _DOCS_BENCHMARK_DIR):
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / "results_all_runs.csv"
        write_header = not path.exists() or path.stat().st_size == 0
        # Migrate: if existing header has fewer columns, rewrite with padded rows
        if not write_header:
            _migrate_csv_header(path)
        with open(path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if write_header:
                writer.writerow(_CSV_HEADER)
            for r in all_results:
                writer.writerow(_result_to_row(r))
    print(f"[output] All runs (shared): {_DOCS_BENCHMARK_DIR / 'results_all_runs.csv'}")

    # Archive CSV is written incrementally (per-file) via _append_result_to_archive().
    if _ARCHIVE_CSV_PATH is not None:
        print(f"[output] Archive: {_ARCHIVE_CSV_PATH}  ({len(all_results)} rows, written incrementally)")


def _migrate_csv_header(path: Path) -> None:
    """If existing CSV has fewer columns than current _CSV_HEADER, rewrite with padded rows."""
    with open(path, "r", newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        old_header = next(reader, None)
        if old_header is None or old_header == _CSV_HEADER:
            return
        if len(old_header) >= len(_CSV_HEADER):
            return
        old_set = set(old_header)
        new_cols = [(i, col) for i, col in enumerate(_CSV_HEADER) if col not in old_set]
        if not new_cols:
            return
        rows = list(reader)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(_CSV_HEADER)
        for row in rows:
            padded = list(row)
            for offset, (insert_idx, _) in enumerate(new_cols):
                padded.insert(insert_idx, "")
            writer.writerow(padded)
    print(f"[migrate] {path.name}: added {len(new_cols)} columns ({', '.join(c for _, c in new_cols)})")


def _write_detail_csv(all_details: list[CatDetailRow]) -> None:
    """Append new detail rows to detail CSV (creates with header if missing)."""
    path = _BENCHMARK_DIR / "cat_results_detail.csv"
    write_header = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if write_header:
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
    total_nsi = sum(r.n_nsi for r in all_results)
    total_llm = sum(r.n_llm for r in all_results)
    total_fallback = sum(r.n_fallback for r in all_results)

    print()
    print(f"  Source breakdown ({total_tx} total transactions across all runs):")
    if total_tx > 0:
        print(f"    Rule:     {total_rule:>6} ({total_rule / total_tx:>6.1%})")
        print(f"    History:  {total_history:>6} ({total_history / total_tx:>6.1%})")
        if total_nsi > 0:
            nsi_acc = sum(r.nsi_accuracy * r.n_nsi for r in all_results if r.n_nsi > 0)
            nsi_acc_avg = nsi_acc / total_nsi if total_nsi > 0 else 0.0
            print(f"    NSI:      {total_nsi:>6} ({total_nsi / total_tx:>6.1%})  accuracy={nsi_acc_avg:.1%}")
        print(f"    LLM:      {total_llm:>6} ({total_llm / total_tx:>6.1%})")
        print(f"    Fallback: {total_fallback:>6} ({total_fallback / total_tx:>6.1%})")

    # Per-direction breakdown
    total_expense = sum(r.n_expense for r in all_results)
    total_income = sum(r.n_income for r in all_results)
    if total_expense > 0 or total_income > 0:
        print()
        print(f"  Direction breakdown:")
        if total_expense > 0:
            exp_exact = sum(r.n_expense_correct for r in all_results)
            exp_fuzzy = sum(r.n_expense_correct_fuzzy for r in all_results)
            exp_fb = sum(r.n_expense_fallback for r in all_results)
            print(f"    Expense:  {total_expense:>6} tx | exact={exp_exact / total_expense:.1%} fuzzy={exp_fuzzy / total_expense:.1%} fallback={exp_fb / total_expense:.1%}")
        if total_income > 0:
            inc_exact = sum(r.n_income_correct for r in all_results)
            inc_fuzzy = sum(r.n_income_correct_fuzzy for r in all_results)
            inc_fb = sum(r.n_income_fallback for r in all_results)
            print(f"    Income:   {total_income:>6} tx | exact={inc_exact / total_income:.1%} fuzzy={inc_fuzzy / total_income:.1%} fallback={inc_fb / total_income:.1%}")

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


# ── Tee writer: duplicate stdout to console + log file ───────────────────

class _TeeWriter:
    """Write to both console and a log file simultaneously."""

    def __init__(self, log_path: Path):
        self._file = open(log_path, "w", encoding="utf-8")
        self._console = sys.__stdout__

    def write(self, text: str) -> int:
        self._console.write(text)
        self._file.write(text)
        return len(text)

    def flush(self) -> None:
        self._console.flush()
        self._file.flush()


# ── Main ──────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark: N runs of categorization pipeline on synthetic files."
    )
    parser.add_argument("--runs", type=int, default=N_RUNS_DEFAULT,
                        help=f"Number of runs (default: {N_RUNS_DEFAULT})")
    parser.add_argument("--files", type=str, default=_FILES_DEFAULT,
                        help=f"Glob pattern to filter files (default: {_FILES_DEFAULT})")
    parser.add_argument("--max-files", type=int, default=None,
                        help="Max number of files to process (e.g. 8 for quick scan)")
    parser.add_argument("--backend", type=str, default=None,
                        help="LLM backend override (e.g. 'local_llama_cpp', 'local_ollama')")
    parser.add_argument("--model-path", type=str, default=None,
                        help="Model path for llama-cpp backend (e.g. path to .gguf file)")
    parser.add_argument("--n-ctx", type=int, default=0,
                        help="Context window size in tokens for llama-cpp (0 = auto-detect from GGUF, default: 0)")
    parser.add_argument("--model", type=str, default=None,
                        help="Model name override (e.g. 'phi3:3.8b', 'gpt-4o-mini', 'claude-3-5-haiku-latest')")
    parser.add_argument("--api-key", type=str, default=None,
                        help="API key for remote backends (OpenAI, Claude, OpenAI-compatible)")
    parser.add_argument("--base-url", type=str, default=None,
                        help="Base URL for Ollama or OpenAI-compatible backends")
    parser.add_argument("--cleaner-batch-size", type=int, default=30,
                        help="Batch size for counterpart extraction (default: 30, use 1 for strict ordering)")
    parser.add_argument(
        "--scenario",
        type=str,
        default="cold",
        choices=["cold", "nsi_warm", "cross_warm", "full_warm", "country_with", "country_without", "all"],
        help=(
            "Benchmark scenario (default: cold). "
            "cold=no warm data; nsi_warm=NSI+taxonomy_map; "
            "cross_warm=leave-one-out history (all GT files except current); "
            "full_warm=history from ALL files (upper bound); "
            "country_with/without=NSI±country ranking; "
            "all=run all scenarios sequentially."
        ),
    )
    parser.add_argument("--country", type=str, default="IT",
                        help="ISO country code for country_with scenario (default: IT)")
    args = parser.parse_args()

    n_runs = args.runs
    file_pattern = args.files
    if file_pattern and not ("*" in file_pattern or "?" in file_pattern):
        file_pattern = f"*{file_pattern}*"

    # ── Log file: tee stdout+stderr to file ──────────────────────
    log_dir = Path(__file__).resolve().parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"categorizer_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    _tee = _TeeWriter(log_file)
    sys.stdout = _tee
    sys.stderr = _tee

    # Startup
    print(f"\n{'=' * 60}")
    print(f"  Spendif.ai Categorizer Benchmark")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Log: {log_file}")
    print(f"{'=' * 60}")

    backend_override = args.backend
    model_path_override = getattr(args, 'model_path', None)
    n_ctx_override = getattr(args, 'n_ctx', 0)
    model_override = args.model
    api_key_override = args.api_key
    base_url_override = args.base_url

    if not backend_override or backend_override == "local_ollama":
        print("\n[check] Verifying Ollama is reachable...")
        if not _check_ollama():
            print("ERROR: Ollama is not reachable at http://localhost:11434")
            print("       Start Ollama before running this benchmark.")
            sys.exit(1)
        print("[check] Ollama OK")
    elif backend_override in ("openai", "claude", "openai_compatible"):
        if not api_key_override:
            env_key = {
                "openai": "OPENAI_API_KEY",
                "claude": "ANTHROPIC_API_KEY",
                "openai_compatible": "COMPAT_API_KEY",
            }.get(backend_override, "")
            api_key_override = os.environ.get(env_key, "")
            if not api_key_override:
                print(f"ERROR: --api-key required for backend '{backend_override}'")
                print(f"       Or set environment variable {env_key}")
                sys.exit(1)
            print(f"[check] Using API key from ${env_key}")
        print(f"\n[check] Backend: {backend_override} (remote API)")
    elif backend_override == "local_llama_cpp":
        model_path_override = _ensure_llamacpp_model(model_path_override)
        print(f"\n[check] Backend: local_llama_cpp")
    elif backend_override == "vllm":
        vllm_url = base_url_override or "http://localhost:8000/v1"
        print(f"\n[check] Backend: vllm ({vllm_url})")
    elif backend_override == "vllm_offline":
        print(f"\n[check] Backend: vllm_offline (in-process, model={model_override or '?'})")
    else:
        print(f"\n[check] Backend: {backend_override} (skipping Ollama check)")

    print("[check] Verifica file sintetici (devono essere pre-generati)...")
    _ensure_generated_files()
    print(f"[check] File sintetici OK  ({_MANIFEST_PATH})")

    # Load manifest and ground truth
    manifest = _load_manifest(file_pattern)
    if args.max_files and len(manifest) > args.max_files:
        # Select 1 file per doc_type × format, then fill remaining slots
        seen_types: set[tuple[str, str]] = set()
        selected: list = []
        remainder: list = []
        for entry in manifest:
            key = (entry.doc_type, entry.fmt)
            if key not in seen_types:
                seen_types.add(key)
                selected.append(entry)
            else:
                remainder.append(entry)
        manifest = (selected + remainder)[:args.max_files]
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
    config.llama_cpp_n_ctx = n_ctx_override  # 0 = auto-detect from GGUF
    if model_override:
        config.ollama_model = model_override
        config.openai_model = model_override
        config.claude_model = model_override
        config.compat_model = model_override
        config.vllm_model = model_override
        config.vllm_offline_model = model_override
    if api_key_override:
        if backend_override == "openai":
            config.openai_api_key = api_key_override
        elif backend_override == "claude":
            config.anthropic_api_key = api_key_override
        elif backend_override == "openai_compatible":
            config.compat_api_key = api_key_override
        elif backend_override == "vllm":
            config.vllm_api_key = api_key_override
    if base_url_override:
        if backend_override == "local_ollama":
            config.ollama_base_url = base_url_override
        elif backend_override == "openai_compatible":
            config.compat_base_url = base_url_override
        elif backend_override == "vllm":
            config.vllm_base_url = base_url_override
    backend = _build_backend(config)

    # ── Collect LLM metadata ─────────────────────────────────────────────
    llm_meta = _collect_llm_metadata(config, backend)
    _LLM_META.update(llm_meta)

    global _ARCHIVE_CSV_PATH
    _ARCHIVE_CSV_PATH = _init_archive_path()
    _init_archive_csv()
    print(f"[archive] {_ARCHIVE_CSV_PATH.name}")

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

    # ── Context window check ─────────────────────────────────────────
    _MIN_CTX = 8000
    _n_ctx_str = llm_meta.get("n_ctx", "0")
    try:
        _n_ctx_val = int(_n_ctx_str)
    except (ValueError, TypeError):
        _n_ctx_val = 0
    if 0 < _n_ctx_val < _MIN_CTX:
        print(f"\n[SKIP] n_ctx={_n_ctx_val} < minimum={_MIN_CTX}")
        print(f"       Context window too small for Spendif.ai prompts. Skipping this model.")
        return

    # Resume: scan local results/ directory for already-completed categorizer runs.
    # Reads from archive files <timestamp>_<hostname>.csv written by _write_all_runs_csv().
    # Does NOT read results_all_runs.csv — that is the cross-HW aggregated file
    # produced by aggregate_results.py and is NOT guaranteed to be present locally.
    _completed: set[tuple] = set()
    _SKIP_NAMES = {"results_all_runs.csv", "cat_results_detail.csv",
                   "cat_results_all_runs.csv", "summary_variance.csv",
                   "summary_global.csv"}
    _local_csvs = [
        p for p in sorted(_RESULTS_ARCHIVE_DIR.glob("*.csv"))
        if p.name not in _SKIP_NAMES
    ]
    for _csv_path in _local_csvs:
        try:
            with open(_csv_path, encoding="utf-8") as _f:
                _reader = csv.DictReader(_f)
                for _row in _reader:
                    if _row.get("benchmark_type", "") not in ("categorizer", "full"):
                        continue  # skip classifier rows
                    # Resume key usa acc_group invece di git_commit
                    from accuracy_groups import commit_to_group
                    _acc_group = commit_to_group(
                        _row.get("git_commit", ""), strict=False
                    )
                    _key = (
                        int(_row.get("run_id", 0)),
                        _row.get("filename", ""),
                        str(_row.get("cleaner_batch_size", "30")),
                        _acc_group,
                        _row.get("provider", ""),
                        _row.get("model", ""),
                    )
                    _completed.add(_key)
        except Exception as _exc:
            print(f"[resume] Warning: could not read {_csv_path.name}: {_exc}")
    if _completed:
        print(f"[resume] {len(_completed)} completed steps found in {len(_local_csvs)} local CSVs — skipping them")

    # ── Build warm fixtures per scenario ─────────────────────────────────
    scenarios_to_run: list[str] = (
        ["cold", "nsi_warm", "cross_warm", "full_warm", "country_with", "country_without"]
        if args.scenario == "all"
        else [args.scenario]
    )

    # Build real taxonomy_map once (used by all warm scenarios)
    _needs_warm = any(sc != "cold" for sc in scenarios_to_run)
    _real_taxonomy_map: dict[str, tuple[str, str]] = (
        _build_real_taxonomy_map(taxonomy) if _needs_warm else {}
    )

    def _warm_kwargs_for_scenario(
        sc: str,
        gt_all: dict[str, list[GroundTruthRow]],
        current_filename: str | None = None,
    ) -> dict:
        """Return kwargs for _evaluate_file based on scenario.

        Scenarios:
          cold          — no warm data; pure LLM baseline.
          nsi_warm      — NSI lookup + taxonomy_map; no history.
          cross_warm    — leave-one-out: history from all GT files *except*
                          current_filename. Simulates a real user who has
                          validated past transactions but has never seen this
                          specific file. Requires current_filename.
          full_warm     — history from ALL GT files including current file.
                          Upper bound: 100% coverage by construction.
          country_with  — nsi_warm + country ranking (args.country).
          country_without — nsi_warm without country ranking.
        """
        if sc == "cold":
            return {"taxonomy_map": None, "history_cache": None, "user_rules": [], "user_country": None}
        if sc in ("nsi_warm", "country_with", "country_without"):
            return {
                "taxonomy_map": _real_taxonomy_map,
                "history_cache": None,
                "user_rules": [],
                "user_country": args.country if sc == "country_with" else None,
            }
        if sc == "cross_warm":
            # Leave-one-out: exclude GT rows belonging to the current file.
            # Models a user with prior validated history but zero knowledge of
            # this specific file. Measures realistic warm-up benefit.
            cross_gt: list[GroundTruthRow] = []
            for fname, rows in gt_all.items():
                if fname != current_filename:
                    cross_gt.extend(rows)
            return {
                "taxonomy_map": _real_taxonomy_map,
                "history_cache": _SyntheticHistoryCache(cross_gt),
                "user_rules": [],
                "user_country": None,
            }
        if sc == "full_warm":
            # Build combined history from all ground truth entries
            all_gt: list[GroundTruthRow] = []
            for rows in gt_all.values():
                all_gt.extend(rows)
            return {
                "taxonomy_map": _real_taxonomy_map,
                "history_cache": _SyntheticHistoryCache(all_gt),
                "user_rules": [],
                "user_country": None,
            }
        return {}

    # Run benchmark
    all_results: list[CatRunResult] = []
    all_details: list[CatDetailRow] = []
    total_start = time.time()
    total_steps = n_runs * n_files * len(scenarios_to_run)
    skipped_steps = 0
    completed_steps = 0

    for scenario in scenarios_to_run:
        # cross_warm builds a per-file leave-one-out cache — computed inside the
        # file loop below. For all other scenarios, build once per scenario.
        warm_kwargs = (
            {} if scenario == "cross_warm"
            else _warm_kwargs_for_scenario(scenario, ground_truth_map)
        )
        print(f"\n[scenario] {scenario.upper()}")
        if scenario == "cross_warm":
            print("  history_cache: leave-one-out (built per file)")
            tm = _real_taxonomy_map
            print(f"  taxonomy_map: {len(tm)} OSM tags" if tm else "  taxonomy_map: none")
        elif scenario != "cold":
            tm = warm_kwargs.get("taxonomy_map")
            hc = warm_kwargs.get("history_cache")
            print(f"  taxonomy_map: {len(tm)} OSM tags" if tm else "  taxonomy_map: none")
            print(f"  history_cache: {len(hc._lookup_dict)} descriptions" if hc else "  history_cache: none")
            if warm_kwargs.get("user_country"):
                print(f"  country: {warm_kwargs['user_country']}")

        for run_id in range(1, n_runs + 1):
            run_results: list[CatRunResult] = []
            run_start = time.time()

            for file_idx, entry in enumerate(manifest, 1):
                # For cross_warm: build leave-one-out cache per file
                if scenario == "cross_warm":
                    warm_kwargs = _warm_kwargs_for_scenario(
                        scenario, ground_truth_map, current_filename=entry.filename
                    )
                    hc = warm_kwargs.get("history_cache")
                    n_cross = len(hc._lookup_dict) if hc else 0
                    print(f"  [{entry.filename}] cross_warm cache: {n_cross} descriptions from other files")

                # Resume key usa acc_group per cross-commit equivalence
                from accuracy_groups import commit_to_group
                _resume_key = (
                    run_id, entry.filename,
                    str(args.cleaner_batch_size),
                    commit_to_group(
                        _LLM_META.get("git_commit", ""), strict=False
                    ),
                    _LLM_META.get("provider", ""),
                    _LLM_META.get("model", ""),
                )
                if scenario == "cold" and _resume_key in _completed:
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
                    f"[{scenario}] Run {run_id}/{n_runs} File {file_idx}/{n_files} "
                    f"| ETA {eta_min:02d}:{eta_sec:02d} | "
                    f"{entry.filename}",
                    end="", flush=True,
                )

                gt = ground_truth_map.get(entry.filename, [])
                result, details = _evaluate_file(
                    entry, gt, backend, taxonomy, run_id,
                    cleaner_batch_size=args.cleaner_batch_size,
                    scenario=scenario,
                    **warm_kwargs,
                )
                run_results.append(result)
                all_details.extend(details)
                _append_result_to_archive(result)

                nsi_info = f" nsi={result.n_nsi}({result.nsi_coverage_pct:.0%})" if result.n_nsi > 0 else ""
                status = "OK" if not result.error else f"ERR: {result.error[:40]}"
                print(
                    f"\r  [{scenario}][Run {run_id}/{n_runs}][File {file_idx}/{n_files}] "
                    f"{entry.filename} "
                    f"{result.duration_seconds:.1f}s "
                    f"exact={result.category_accuracy:.0%} "
                    f"fuzzy={result.fuzzy_accuracy:.0%} "
                    f"fb={result.fallback_rate:.0%} "
                    f"r/h/nsi/l={result.n_rule}/{result.n_history}/{result.n_nsi}/{result.n_llm}"
                    f"{nsi_info} "
                    f"[{status}]"
                    + " " * 10
                )

            run_duration = time.time() - run_start
            all_results.extend(run_results)

            n_ok = sum(1 for r in run_results if not r.error)
            n_err = sum(1 for r in run_results if r.error)
            avg_exact = sum(r.category_accuracy for r in run_results) / len(run_results) if run_results else 0
            avg_fuzzy = sum(r.fuzzy_accuracy for r in run_results) / len(run_results) if run_results else 0
            print(
                f"  --- [{scenario}] Run {run_id} complete: {run_duration:.0f}s, "
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
