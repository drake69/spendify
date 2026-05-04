"""Tests for Excel border detection on synthetic files with borders.

Verifies that detect_bordered_region() correctly identifies the table
region in XLSX files where the transaction table is enclosed in cell borders,
while preheader and footer rows remain outside the bordered rectangle.
"""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from core.normalizer import detect_bordered_region, detect_header_row_excel

GENERATED_DIR = Path(__file__).parent / "generated_files"
MANIFEST = GENERATED_DIR / "manifest.csv"


def _load_manifest() -> list[dict]:
    """Load the manifest and return rows as dicts."""
    if not MANIFEST.exists():
        pytest.skip("Manifest not found — run generate_synthetic_files.py first")
    with open(MANIFEST, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _bordered_xlsx_entries() -> list[dict]:
    """Return only bordered XLSX entries from manifest."""
    return [r for r in _load_manifest() if r.get("has_borders") == "true"]


def _non_bordered_xlsx_entries() -> list[dict]:
    """Return non-bordered XLSX entries."""
    return [
        r for r in _load_manifest()
        if r["format"] == "xlsx" and r.get("has_borders") != "true"
    ]


# ── Test: bordered files are detected ────────────────────────────────────


class TestBorderDetection:
    """Verify detect_bordered_region on bordered synthetic XLSX files."""

    @pytest.fixture(scope="class")
    def bordered_entries(self) -> list[dict]:
        entries = _bordered_xlsx_entries()
        if not entries:
            pytest.skip("No bordered XLSX files in manifest")
        return entries

    def test_all_bordered_files_detected(self, bordered_entries):
        """Every bordered XLSX must return a non-None region."""
        for entry in bordered_entries:
            fname = entry["filename"]
            fpath = GENERATED_DIR / fname
            with open(fpath, "rb") as f:
                raw = f.read()
            region = detect_bordered_region(raw, fname)
            assert region is not None, (
                f"{fname}: detect_bordered_region returned None"
            )

    def test_bordered_region_starts_at_header(self, bordered_entries):
        """The bordered region must start at the header row (after preheader)."""
        for entry in bordered_entries:
            fname = entry["filename"]
            n_header = int(entry["n_header_rows"])
            fpath = GENERATED_DIR / fname
            with open(fpath, "rb") as f:
                raw = f.read()
            region = detect_bordered_region(raw, fname)
            assert region is not None
            r1, r2, c1, c2 = region
            assert r1 == n_header, (
                f"{fname}: region starts at row {r1}, expected {n_header} "
                f"(n_header_rows={n_header})"
            )

    def test_bordered_region_covers_all_data(self, bordered_entries):
        """The bordered region must include header + all data rows.

        Note: detect_bordered_region scans max 60 rows by default, so files
        where preheader + header + data > 60 rows will have a truncated region.
        We accept this: the region must cover at least min(expected, scan_limit).
        """
        MAX_SCAN = 60  # default max_scan_rows in detect_bordered_region
        for entry in bordered_entries:
            fname = entry["filename"]
            n_header = int(entry["n_header_rows"])
            n_data = int(entry["n_data_rows"])
            fpath = GENERATED_DIR / fname
            with open(fpath, "rb") as f:
                raw = f.read()
            region = detect_bordered_region(raw, fname)
            assert region is not None
            r1, r2, c1, c2 = region
            region_rows = r2 - r1 + 1
            expected_rows = n_data + 1  # +1 for header
            # Truncation: if total rows exceed scan limit, region is truncated
            max_possible = min(expected_rows, MAX_SCAN - n_header)
            assert region_rows >= max_possible, (
                f"{fname}: region has {region_rows} rows, expected >= {max_possible} "
                f"(1 header + {n_data} data, scan_limit={MAX_SCAN})"
            )

    def test_bordered_region_minimum_columns(self, bordered_entries):
        """The bordered region must have at least 3 columns."""
        for entry in bordered_entries:
            fname = entry["filename"]
            fpath = GENERATED_DIR / fname
            with open(fpath, "rb") as f:
                raw = f.read()
            region = detect_bordered_region(raw, fname)
            assert region is not None
            r1, r2, c1, c2 = region
            n_cols = c2 - c1 + 1
            assert n_cols >= 3, (
                f"{fname}: region has {n_cols} columns, expected >= 3"
            )

    def test_header_detection_certain_with_borders(self, bordered_entries):
        """detect_header_row_excel must return certain=True for bordered files."""
        for entry in bordered_entries:
            fname = entry["filename"]
            fpath = GENERATED_DIR / fname
            with open(fpath, "rb") as f:
                raw = f.read()
            skip_rows, certain, border_region = detect_header_row_excel(raw, fname)
            assert certain is True, (
                f"{fname}: header detection not certain (skip_rows={skip_rows})"
            )
            assert border_region is not None, (
                f"{fname}: header detection returned no border_region"
            )


# ── Test: non-bordered files ─────────────────────────────────────────────


class TestNonBorderedXlsx:
    """Verify that non-bordered XLSX files return None from border detection."""

    @pytest.fixture(scope="class")
    def non_bordered_entries(self) -> list[dict]:
        entries = _non_bordered_xlsx_entries()
        if not entries:
            pytest.skip("No non-bordered XLSX files in manifest")
        return entries

    def test_no_border_region_for_non_bordered(self, non_bordered_entries):
        """Non-bordered XLSX must return None from detect_bordered_region."""
        for entry in non_bordered_entries:
            fname = entry["filename"]
            fpath = GENERATED_DIR / fname
            with open(fpath, "rb") as f:
                raw = f.read()
            region = detect_bordered_region(raw, fname)
            assert region is None, (
                f"{fname}: expected None but got region {region}"
            )


# ── Test: CSV files (no borders possible) ────────────────────────────────


class TestCsvNoBorders:
    """Verify that CSV files always return None from border detection."""

    def test_csv_always_none(self):
        """detect_bordered_region on a CSV must return None."""
        entries = [r for r in _load_manifest() if r["format"] == "csv"]
        if not entries:
            pytest.skip("No CSV files in manifest")
        for entry in entries[:5]:  # sample 5
            fname = entry["filename"]
            fpath = GENERATED_DIR / fname
            with open(fpath, "rb") as f:
                raw = f.read()
            region = detect_bordered_region(raw, fname)
            assert region is None, (
                f"{fname} (CSV): expected None but got region {region}"
            )
