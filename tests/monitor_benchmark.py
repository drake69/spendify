#!/usr/bin/env python3
r"""Benchmark progress monitor — cross-platform (Mac / Linux / Windows).

Reads results_all_runs.csv every N seconds and prints a live synopsis:
elapsed time, per-model progress, overall ETA, live CPU/GPU, pipeline phase.

Usage:
    # Mac / Linux
    .venv/bin/python tests/monitor_benchmark.py

    # Windows
    .venv\Scripts\python.exe tests\monitor_benchmark.py

    # Options
    --interval N      Refresh interval in seconds (default: 60)
    --runs N          Expected runs per model (default: 1)
    --total N         Override expected files per model (auto from manifest)
    --once            Print once and exit (no loop)
    --all             Show all historical data (default: current run_id only)
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────
_TESTS_DIR     = Path(__file__).resolve().parent
_PROJECT_ROOT  = _TESTS_DIR.parent
_BENCHMARK_DIR = _TESTS_DIR / "generated_files" / "benchmark"
_MANIFEST_PATH = _TESTS_DIR / "generated_files" / "manifest.csv"
_RESULTS_CSV   = _BENCHMARK_DIR / "results_all_runs.csv"

# ── HW monitor (optional — graceful fallback if unavailable) ───────────────
sys.path.insert(0, str(_PROJECT_ROOT))
try:
    from tests.hw_monitor import HWMonitor as _HWMonitor
    _hw = _HWMonitor()
    _gpu_source: str = getattr(_hw._gpu_sampler, "_source", "?")
except Exception:
    _hw = None          # type: ignore[assignment]
    _gpu_source = "n/a"

# ── Terminal helpers ───────────────────────────────────────────────────────
_IS_WIN = sys.platform == "win32"

def _clear() -> None:
    os.system("cls" if _IS_WIN else "clear")

def _bar(done: int, total: int, width: int = 16) -> str:
    if total == 0:
        return "░" * width
    ratio  = min(done / total, 1.0)          # cap at 100%
    filled = int(width * ratio)
    return "█" * filled + "░" * (width - filled)

def _fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    h, rem  = divmod(seconds, 3600)
    m, s    = divmod(rem, 60)
    if h:   return f"{h}h {m:02d}m {s:02d}s"
    if m:   return f"{m}m {s:02d}s"
    return  f"{s}s"

def _fmt_eta(remaining: int, rate_fps: float) -> str:
    if rate_fps <= 0 or remaining <= 0:
        return "—"
    return f"~{_fmt_duration(remaining / rate_fps)}"

# ── Data loading ───────────────────────────────────────────────────────────
def _load_manifest_count() -> int:
    if not _MANIFEST_PATH.exists():
        return 0
    with open(_MANIFEST_PATH, newline="", encoding="utf-8") as f:
        return sum(1 for _ in csv.DictReader(f))

def _load_results(current_only: bool = True) -> tuple[list[dict], float | None]:
    """Return (rows, csv_mtime). If current_only, keep only rows with the latest run_id."""
    if not _RESULTS_CSV.exists():
        return [], None

    rows: list[dict] = []
    try:
        with open(_RESULTS_CSV, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                rows.append(row)
    except Exception:
        return [], None

    mtime = _RESULTS_CSV.stat().st_mtime

    if current_only and rows:
        # Find the max run_id present in the file
        try:
            max_run = max(int(r.get("run_id", 0) or 0) for r in rows)
            rows = [r for r in rows if int(r.get("run_id", 0) or 0) == max_run]
        except (ValueError, TypeError):
            pass  # keep all if run_id is non-numeric

    return rows, mtime

# ── Snapshot ───────────────────────────────────────────────────────────────
def _snapshot(rows: list[dict], exp_per_model: int) -> dict:
    counts:    dict[tuple, int]  = defaultdict(int)
    durations: list[float]       = []
    cpu_samples: list[float]     = []
    gpu_samples: list[float]     = []
    phases: dict[str, int]       = defaultdict(int)

    for row in rows:
        key = (row.get("provider", "?"), row.get("model", "?"))
        counts[key] += 1
        try:
            d = float(row.get("duration_seconds") or 0)
            if d > 0:
                durations.append(d)
        except ValueError:
            pass
        try:
            c = float(row.get("cpu_load_avg") or 0)
            if c > 0:
                cpu_samples.append(c)
        except ValueError:
            pass
        try:
            g = float(row.get("gpu_utilization_pct") or 0)
            if g > 0:
                gpu_samples.append(g)
        except ValueError:
            pass
        bt = (row.get("benchmark_type") or "").strip()
        if bt:
            phases[bt] += 1

    total_done = sum(counts.values())
    n_models   = len(counts)
    total_exp  = n_models * exp_per_model if n_models else 0
    avg_dur    = sum(durations) / len(durations) if durations else 0
    rate_fpm   = (60 / avg_dur) if avg_dur > 0 else 0

    return {
        "counts":        dict(counts),
        "total_done":    total_done,
        "total_exp":     total_exp,
        "exp_per_model": exp_per_model,
        "avg_dur_s":     avg_dur,
        "rate_fpm":      rate_fpm,
        "rate_fps":      rate_fpm / 60,
        "cpu_avg_hist":  sum(cpu_samples) / len(cpu_samples) if cpu_samples else 0.0,
        "gpu_avg_hist":  sum(gpu_samples) / len(gpu_samples) if gpu_samples else 0.0,
        "phases":        dict(phases),
    }

# ── Report ─────────────────────────────────────────────────────────────────
def _print_report(snap: dict, elapsed_s: float, interval: int,
                  live_cpu: float = 0.0, live_gpu: float = 0.0) -> None:
    counts       = snap["counts"]
    total_done   = snap["total_done"]
    total_exp    = snap["total_exp"]
    exp_per      = snap["exp_per_model"]
    rate_fpm     = snap["rate_fpm"]
    rate_fps     = snap["rate_fps"]
    avg_dur_s    = snap["avg_dur_s"]
    remaining    = max(0, total_exp - total_done)
    cpu_hist     = snap["cpu_avg_hist"]
    gpu_hist     = snap["gpu_avg_hist"]
    phases       = snap["phases"]

    W = 72

    print("═" * W)
    print(f"  BENCHMARK MONITOR  —  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    rate_str = f"{rate_fpm:.1f} file/min  ({avg_dur_s:.1f}s/file)" if rate_fpm > 0 else "—"
    eta_str  = _fmt_eta(remaining, rate_fps)
    print(f"  Elapsed : {_fmt_duration(elapsed_s)}")
    print(f"  Rate    : {rate_str}")
    print(f"  ETA     : {eta_str}")

    # ── Pipeline phase ─────────────────────────────────────────────────────
    if phases:
        phase_parts = [f"{k}: {v}" for k, v in sorted(phases.items())]
        # Highlight the most recent phase (highest count when only one active)
        active = max(phases, key=lambda k: phases[k]) if phases else "?"
        print(f"  Phase   : {active}  ({', '.join(phase_parts)} rows)")

    # ── HW stats ───────────────────────────────────────────────────────────
    if live_cpu > 0 or live_gpu > 0:
        cpu_bar  = _bar(int(live_cpu), 100, 10)
        gpu_bar  = _bar(int(live_gpu), 100, 10)
        live_str = f"CPU {live_cpu:5.1f}%  {cpu_bar}  |  GPU {live_gpu:5.1f}%  {gpu_bar}  [{_gpu_source}]"
        print(f"  Live HW : {live_str}")
    if cpu_hist > 0 or gpu_hist > 0:
        hist_str = f"CPU avg {cpu_hist:.1f}%  |  GPU avg {gpu_hist:.1f}%  (da righe completate)"
        print(f"  HW avg  : {hist_str}")

    print("═" * W)

    if not counts:
        print("  Nessun risultato — benchmark non ancora avviato o CSV vuoto.")
        print("═" * W)
        return

    # Column widths
    col_b, col_m, col_d = 12, 34, 5

    print(f"  {'Backend':<{col_b}}{'Model':<{col_m}}{'Done':>{col_d}}   {'Progress (cap 100%)'}")
    print("  " + "─" * (W - 2))

    def _sort_key(item: tuple) -> tuple:
        (prov, mdl), cnt = item
        if exp_per > 0:
            if 0 < cnt < exp_per: return (0, prov, mdl)   # running
            if cnt >= exp_per:    return (1, prov, mdl)   # done
        return (2, prov, mdl)                              # waiting

    for (prov, mdl), cnt in sorted(counts.items(), key=_sort_key):
        bar = _bar(cnt, exp_per)
        pct = f"{min(100*cnt//exp_per, 100):3d}%" if exp_per else "  ?%"
        tag = ""
        if exp_per > 0:
            if 0 < cnt < exp_per: tag = " ← in corso"
            elif cnt == 0:        tag = " ○ in attesa"
        print(f"  {prov:<{col_b}}{mdl:<{col_m}}{cnt:>{col_d}}   {bar} {pct}{tag}")

    print("  " + "─" * (W - 2))

    tot_bar = _bar(total_done, total_exp, 24)
    tot_pct = f"{min(100*total_done//total_exp, 100):3d}%" if total_exp else "?"
    print(f"  TOTALE   {total_done} / {total_exp}  {tot_bar} {tot_pct}")
    print("═" * W)
    if interval > 0:
        print(f"  Aggiornamento ogni {interval}s  —  Ctrl+C per uscire")


# ── Main ───────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark progress monitor")
    parser.add_argument("--interval", type=int,  default=60,    help="Refresh interval seconds (default: 60)")
    parser.add_argument("--runs",     type=int,  default=1,     help="Expected runs per model (default: 1)")
    parser.add_argument("--total",    type=int,  default=0,     help="Expected files per model (0 = auto from manifest)")
    parser.add_argument("--once",     action="store_true",      help="Print once and exit")
    parser.add_argument("--all",      action="store_true",      help="Show all historical data, not just current run_id")
    args = parser.parse_args()

    manifest_count = _load_manifest_count()
    exp_per_model  = (args.total if args.total > 0 else manifest_count) * args.runs
    current_only   = not args.all

    start_time = time.monotonic()

    def _run_once() -> None:
        rows, _ = _load_results(current_only=current_only)
        snap     = _snapshot(rows, exp_per_model)
        elapsed  = time.monotonic() - start_time
        # Live HW sample (taken just before printing for freshness)
        live_cpu, live_gpu = _hw.sample_once() if _hw is not None else (0.0, 0.0)
        _clear()
        _print_report(snap, elapsed, 0 if args.once else args.interval,
                      live_cpu=live_cpu, live_gpu=live_gpu)

    if args.once:
        _run_once()
        return

    try:
        while True:
            _run_once()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n  Monitor fermato.")


if __name__ == "__main__":
    main()
