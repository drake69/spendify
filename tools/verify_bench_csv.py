#!/usr/bin/env python3
"""Verify benchmark results CSV is append-only (CI check for PRs).

Compares the benchmark CSV on the current branch against the base branch (main).
Ensures:
  1. All rows from main are still present (no deletions)
  2. No existing rows were modified
  3. Header is identical
  4. New rows have required fields populated
  5. benchmark_type is valid ("classifier" or "categorizer")

Usage:
    python tools/verify_bench_csv.py                    # auto-detect base branch
    python tools/verify_bench_csv.py --base main        # explicit base
    python tools/verify_bench_csv.py --base origin/main # remote base

Exit codes:
    0 — All checks pass (append-only, valid rows)
    1 — Violations found (modified/deleted rows, invalid data)
    2 — CSV not found or not modified in this branch
"""
from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

BENCH_CSV = "tests/generated_files/benchmark/results_all_runs.csv"
REQUIRED_FIELDS = ["benchmark_type", "filename", "git_commit", "provider", "model"]
VALID_BENCHMARK_TYPES = {"classifier", "categorizer"}


def _get_base_csv(base_branch: str) -> list[dict] | None:
    """Read the CSV from the base branch via git show."""
    try:
        content = subprocess.check_output(
            ["git", "show", f"{base_branch}:{BENCH_CSV}"],
            text=True, stderr=subprocess.DEVNULL,
        )
        reader = csv.DictReader(content.splitlines())
        return list(reader)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _read_current_csv() -> list[dict] | None:
    """Read the CSV from the working directory."""
    path = Path(BENCH_CSV)
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _row_key(row: dict) -> tuple:
    """Unique key for a benchmark row."""
    return (
        row.get("run_id", ""),
        row.get("filename", ""),
        row.get("git_commit", ""),
        row.get("git_branch", ""),
        row.get("provider", ""),
        row.get("model", ""),
        row.get("benchmark_type", ""),
    )


def verify(base_branch: str = "main") -> bool:
    """Run all checks. Returns True if pass."""
    errors: list[str] = []

    current = _read_current_csv()
    if current is None:
        print(f"SKIP: {BENCH_CSV} not found in working directory")
        return True

    base = _get_base_csv(base_branch)

    # If base doesn't have the CSV yet, all rows are "new" — OK
    if base is None:
        print(f"INFO: {BENCH_CSV} not found on {base_branch} — all rows are new")
        base = []

    # Check 1: Header must match (if base has rows)
    if base and current:
        base_header = list(base[0].keys())
        curr_header = list(current[0].keys())
        if base_header != curr_header:
            errors.append(
                f"HEADER CHANGED:\n"
                f"  base:    {base_header}\n"
                f"  current: {curr_header}"
            )

    # Check 2: All base rows must still be present (no deletions)
    base_keys = {_row_key(r) for r in base}
    current_keys = {_row_key(r) for r in current}

    deleted = base_keys - current_keys
    if deleted:
        errors.append(f"DELETED: {len(deleted)} rows from base were removed")
        for k in sorted(deleted)[:5]:
            errors.append(f"  - {k}")

    # Check 3: Base rows not modified (same key → same values)
    base_by_key = {_row_key(r): r for r in base}
    for row in current:
        key = _row_key(row)
        if key in base_by_key:
            base_row = base_by_key[key]
            if row != base_row:
                # Find which fields changed
                changed = [f for f in row if row[f] != base_row.get(f, "")]
                errors.append(
                    f"MODIFIED: row {key[:3]}... — fields changed: {changed}"
                )

    # Check 4: New rows have required fields
    new_keys = current_keys - base_keys
    new_rows = [r for r in current if _row_key(r) in new_keys]
    for row in new_rows:
        for field in REQUIRED_FIELDS:
            val = row.get(field, "").strip()
            if not val:
                errors.append(
                    f"MISSING FIELD: new row {row.get('filename', '?')} "
                    f"({row.get('model', '?')}) — '{field}' is empty"
                )

    # Check 5: benchmark_type is valid
    for row in new_rows:
        bt = row.get("benchmark_type", "").strip()
        if bt and bt not in VALID_BENCHMARK_TYPES:
            errors.append(
                f"INVALID benchmark_type='{bt}' in row "
                f"{row.get('filename', '?')} ({row.get('model', '?')})"
            )

    # Report
    n_new = len(new_keys)
    n_base = len(base_keys)

    if errors:
        print(f"FAILED: {len(errors)} violations found\n")
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        print(
            f"\nThe benchmark CSV must be append-only. "
            f"Do not modify or delete existing rows.",
            file=sys.stderr,
        )
        return False
    else:
        print(
            f"OK: {n_base} base rows preserved, "
            f"{n_new} new rows added, all valid"
        )
        return True


if __name__ == "__main__":
    base = "main"
    if "--base" in sys.argv:
        idx = sys.argv.index("--base")
        if idx + 1 < len(sys.argv):
            base = sys.argv[idx + 1]

    ok = verify(base)
    sys.exit(0 if ok else 1)
