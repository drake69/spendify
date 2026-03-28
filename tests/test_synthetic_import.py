#!/usr/bin/env python3
"""Test deterministic pipeline components against synthetic files with known ground truth.

Runs header detection, DataFrame loading, Phase-0 column analysis, and amount
parsing against every file listed in the synthetic manifest.  NO database writes,
NO LLM calls.  Writes per-file results to ``tests/generated_files/results_deterministic.csv``
and prints a summary table to stdout.

Usage:
    pytest tests/test_synthetic_import.py -v -s
"""
from __future__ import annotations

import csv
import subprocess
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Optional

import pandas as pd
import pytest

# -- Project imports -----------------------------------------------------------
from core.normalizer import (
    detect_skip_rows,
    parse_amount,
)
from core.orchestrator import load_raw_dataframe
from core.classifier import _run_step0_analysis

# -- Paths ---------------------------------------------------------------------
_TESTS_DIR = Path(__file__).resolve().parent
_GENERATED_DIR = _TESTS_DIR / "generated_files"
_MANIFEST_PATH = _GENERATED_DIR / "manifest.csv"
_RESULTS_PATH = _GENERATED_DIR / "results_deterministic.csv"
_GENERATOR_SCRIPT = _TESTS_DIR / "generate_synthetic_files.py"


# -- Data classes --------------------------------------------------------------

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
class FileResult:
    filename: str
    header_detected: int
    header_expected: int
    header_match: bool
    rows_detected: int
    rows_expected: int
    rows_match: bool
    split_detected: bool
    split_expected: bool
    split_match: bool
    amount_parse_rate: float
    n_footer_rows: int = 0


# -- Helpers -------------------------------------------------------------------

def _load_manifest() -> list[ManifestEntry]:
    """Read manifest.csv into a list of ManifestEntry."""
    entries: list[ManifestEntry] = []
    with open(_MANIFEST_PATH, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            entries.append(ManifestEntry(
                filename=row["filename"],
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
        pytest.fail(
            f"Synthetic file generation failed (rc={result.returncode}):\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


def _evaluate_file(entry: ManifestEntry) -> FileResult:
    """Run deterministic pipeline steps on a single synthetic file."""
    filepath = _GENERATED_DIR / entry.filename
    raw_bytes = filepath.read_bytes()

    # -- 1. Header detection ---------------------------------------------------
    detected_skip, _certain, _border = detect_skip_rows(raw_bytes, entry.filename)
    header_match = detected_skip == entry.n_header_rows

    # -- 2. DataFrame loading --------------------------------------------------
    # Use the pipeline's own detection (not ground truth) for realistic testing.
    step0 = None
    try:
        df, _enc, preprocess_info = load_raw_dataframe(raw_bytes, entry.filename)
    except Exception:
        return FileResult(
            filename=entry.filename,
            header_detected=detected_skip,
            header_expected=entry.n_header_rows,
            header_match=header_match,
            rows_detected=0,
            rows_expected=entry.n_data_rows,
            rows_match=False,
            split_detected=False,
            split_expected=entry.has_debit_credit_split,
            split_match=False,
            n_footer_rows=entry.n_footer_rows,
            amount_parse_rate=0.0,
        )

    rows_detected = len(df)
    # Allow footer rows to be included (footer stripping is best-effort)
    rows_match = (
        rows_detected == entry.n_data_rows
        or rows_detected == entry.n_data_rows + entry.n_footer_rows
    )

    # -- 3. Phase-0 column analysis (debit/credit split detection) -------------
    split_detected = False
    try:
        step0 = _run_step0_analysis(list(df.columns), df_raw=df)
        split_detected = step0.debit_col is not None and step0.credit_col is not None
    except Exception:
        pass
    split_match = split_detected == entry.has_debit_credit_split

    # -- 4. Amount parsing -----------------------------------------------------
    amount_parse_rate = 0.0
    if len(df) > 0 and step0 is not None:
        amount_cols: list[str] = []
        if step0.amount_col and step0.amount_col in df.columns:
            amount_cols = [step0.amount_col]
        elif step0.debit_col and step0.debit_col in df.columns:
            amount_cols = [step0.debit_col]
            if step0.credit_col and step0.credit_col in df.columns:
                amount_cols.append(step0.credit_col)

        if amount_cols:
            total_cells = 0
            parsed_cells = 0
            for col in amount_cols:
                for val in df[col]:
                    if pd.isna(val) or (isinstance(val, str) and val.strip() == ""):
                        continue  # empty cells are normal in split columns
                    total_cells += 1
                    parsed = parse_amount(val)
                    if parsed is not None:
                        parsed_cells += 1
            amount_parse_rate = parsed_cells / total_cells if total_cells > 0 else 0.0

    return FileResult(
        filename=entry.filename,
        header_detected=detected_skip,
        header_expected=entry.n_header_rows,
        header_match=header_match,
        rows_detected=rows_detected,
        rows_expected=entry.n_data_rows,
        rows_match=rows_match,
        split_detected=split_detected,
        split_expected=entry.has_debit_credit_split,
        split_match=split_match,
        amount_parse_rate=amount_parse_rate,
        n_footer_rows=entry.n_footer_rows,
    )


def _write_results_csv(results: list[FileResult]) -> None:
    """Write per-file results to CSV."""
    _GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    with open(_RESULTS_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "filename", "header_detected", "header_expected", "header_match",
            "rows_detected", "rows_expected", "rows_match",
            "split_detected", "split_expected", "split_match",
            "amount_parse_rate",
        ])
        for r in results:
            writer.writerow([
                r.filename, r.header_detected, r.header_expected,
                r.header_match,
                r.rows_detected, r.rows_expected, r.rows_match,
                r.split_detected, r.split_expected, r.split_match,
                f"{r.amount_parse_rate:.4f}",
            ])


def _print_summary(results: list[FileResult]) -> None:
    """Print a summary table to stdout."""
    n = len(results)
    if n == 0:
        print("\nNo results to summarize.")
        return

    header_ok = sum(1 for r in results if r.header_match)
    rows_ok = sum(1 for r in results if r.rows_match)
    split_ok = sum(1 for r in results if r.split_match)
    amt_rates = [r.amount_parse_rate for r in results if r.amount_parse_rate > 0]
    avg_amt = sum(amt_rates) / len(amt_rates) if amt_rates else 0.0

    header_pct = header_ok / n * 100
    rows_pct = rows_ok / n * 100
    split_pct = split_ok / n * 100
    amt_pct = avg_amt * 100

    # Weighted automation score
    automation_score = (
        0.30 * (header_ok / n)
        + 0.25 * (rows_ok / n)
        + 0.20 * (split_ok / n)
        + 0.25 * avg_amt
    ) * 100

    sep = "-" * 90
    print(f"\n{'=' * 90}")
    print("  SYNTHETIC IMPORT TEST -- DETERMINISTIC PIPELINE RESULTS")
    print(f"{'=' * 90}")
    print(f"  {'Filename':<40} {'Hdr':>3} {'exp':>3} {'ok':>3}  "
          f"{'Rows':>5} {'exp':>5} {'ok':>3}  "
          f"{'Split':>5} {'ok':>3}  {'Amt%':>6}")
    print(f"  {sep}")

    for r in results:
        hdr_flag = "Y" if r.header_match else "N"
        row_flag = "Y" if r.rows_match else "N"
        spl_flag = "Y" if r.split_match else "N"
        print(f"  {r.filename:<40} {r.header_detected:>3} {r.header_expected:>3} "
              f"  {hdr_flag}  {r.rows_detected:>5} {r.rows_expected:>5}   {row_flag}  "
              f"{'T' if r.split_detected else 'F':>5}   {spl_flag}  "
              f"{r.amount_parse_rate * 100:>5.1f}%")

    print(f"  {sep}")
    print(f"\n  AGGREGATE KPIs ({n} files):")
    print(f"    Header detection accuracy : {header_ok:>3}/{n} = {header_pct:5.1f}%")
    print(f"    Row count accuracy        : {rows_ok:>3}/{n} = {rows_pct:5.1f}%")
    print(f"    Split detection accuracy  : {split_ok:>3}/{n} = {split_pct:5.1f}%")
    print(f"    Amount parse rate (avg)   : {amt_pct:5.1f}%")
    print("    ---------------------------------------------")
    print(f"    AUTOMATION SCORE (weighted): {automation_score:5.1f}%")
    print("      (weights: header=0.30, rows=0.25, split=0.20, amount=0.25)")
    print(f"\n  Results written to: {_RESULTS_PATH}")
    print(f"{'=' * 90}\n")


# -- Test class ----------------------------------------------------------------

class TestSyntheticImportDeterministic:
    """Test deterministic pipeline components against synthetic files with known ground truth."""

    @pytest.fixture(scope="module", autouse=True)
    def ensure_files(self):
        """Generate synthetic files if not already present."""
        _ensure_generated_files()

    @pytest.fixture(scope="module")
    def manifest(self) -> list[ManifestEntry]:
        """Load the manifest."""
        _ensure_generated_files()
        return _load_manifest()

    @pytest.fixture(scope="module")
    def results(self, manifest: list[ManifestEntry]) -> list[FileResult]:
        """Run all files through the deterministic pipeline and collect results."""
        all_results: list[FileResult] = []
        for entry in manifest:
            r = _evaluate_file(entry)
            all_results.append(r)

        _write_results_csv(all_results)
        _print_summary(all_results)
        return all_results

    def test_header_detection_accuracy(self, results: list[FileResult]):
        """Header detection accuracy should exceed 80%."""
        n = len(results)
        assert n > 0, "No files in manifest"
        ok = sum(1 for r in results if r.header_match)
        accuracy = ok / n
        assert accuracy >= 0.80, (
            f"Header detection accuracy {accuracy:.1%} < 80% "
            f"({ok}/{n} files matched)"
        )

    def test_row_count_accuracy(self, results: list[FileResult]):
        """Row count accuracy (exact match or within footer tolerance).

        Baseline: 76% — footer stripping is best-effort.
        Target: raise to 90%+ as footer detection improves.
        """
        n = len(results)
        assert n > 0, "No files in manifest"
        # Accept exact match OR diff within ±footer rows
        ok = sum(
            1 for r in results
            if r.rows_match or abs(r.rows_detected - r.rows_expected) <= r.n_footer_rows
        )
        accuracy = ok / n
        assert accuracy >= 0.75, (
            f"Row count accuracy {accuracy:.1%} < 75% "
            f"({ok}/{n} files matched, with footer tolerance)"
        )

    def test_split_detection_accuracy(self, results: list[FileResult]):
        """Debit/credit split detection should exceed 85%."""
        n = len(results)
        assert n > 0, "No files in manifest"
        ok = sum(1 for r in results if r.split_match)
        accuracy = ok / n
        assert accuracy >= 0.85, (
            f"Split detection accuracy {accuracy:.1%} < 85% "
            f"({ok}/{n} files matched)"
        )

    def test_amount_parse_rate(self, results: list[FileResult]):
        """Average amount parse rate (for files with detected amount columns) should exceed 90%."""
        rates = [r.amount_parse_rate for r in results if r.amount_parse_rate > 0]
        assert len(rates) > 0, "No files had parseable amount columns"
        avg = sum(rates) / len(rates)
        assert avg >= 0.90, (
            f"Average amount parse rate {avg:.1%} < 90% "
            f"(across {len(rates)} files with amounts)"
        )

    def test_no_zero_row_files(self, results: list[FileResult]):
        """Every file should load at least some rows."""
        zero_files = [r.filename for r in results if r.rows_detected == 0]
        assert len(zero_files) == 0, (
            f"{len(zero_files)} file(s) loaded 0 rows: {zero_files[:10]}"
        )

    def test_manifest_completeness(self, manifest: list[ManifestEntry]):
        """Manifest should have a reasonable number of files."""
        assert len(manifest) >= 20, (
            f"Manifest has only {len(manifest)} entries, expected >= 20"
        )
