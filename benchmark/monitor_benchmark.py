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
    --models N        Override total model/backend pairs (auto from benchmark_models.csv)
    --once            Print once and exit (no loop)
    --all             Show all historical data (default: current run_id only)
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────
_TESTS_DIR      = Path(__file__).resolve().parent
_PROJECT_ROOT   = _TESTS_DIR.parent
_BENCHMARK_DIR  = _TESTS_DIR / "results"
_MANIFEST_PATH  = _TESTS_DIR / "generated_files" / "manifest.csv"
_RESULTS_CSV    = _BENCHMARK_DIR / "results_all_runs.csv"
_RESULTS_ARCHIVE_DIR = _TESTS_DIR / "results"
_MODELS_CSV     = _TESTS_DIR / "benchmark_models.csv"

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
def _ollama_running() -> bool:
    """Quick check — returns True if Ollama is reachable on localhost:11434."""
    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
        return True
    except Exception:
        return False

def _count_expected_models() -> int:
    """Count enabled model/backend pairs from benchmark_models.csv.

    Rules (same as run_benchmark_full.sh/.ps1):
    - +1 per row with gguf_file set       → llama.cpp backend
    - +1 per row with ollama_tag set      → Ollama backend (only if Ollama is reachable)
    Returns 0 if the CSV is missing or unreadable.
    """
    if not _MODELS_CSV.exists():
        return 0
    ollama_up: bool | None = None   # lazy-check once
    count = 0
    try:
        with open(_MODELS_CSV, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if (row.get("enabled") or "").strip().lower() != "true":
                    continue
                if (row.get("gguf_file") or "").strip():
                    count += 1
                if (row.get("ollama_tag") or "").strip():
                    if ollama_up is None:
                        ollama_up = _ollama_running()
                    if ollama_up:
                        count += 1
    except Exception:
        return 0
    return count

def _load_manifest_count() -> int:
    if not _MANIFEST_PATH.exists():
        return 0
    with open(_MANIFEST_PATH, newline="", encoding="utf-8") as f:
        return sum(1 for _ in csv.DictReader(f))

_SKIP_CSV_NAMES = {
    "results_all_runs.csv", "cat_results_detail.csv",
    "cat_results_all_runs.csv", "summary_variance.csv",
    "results_run_01.csv", "summary_global.csv",
}

def _find_archive_csvs(csv_override: Path | None = None) -> list[Path]:
    """Return all per-run archive CSVs to aggregate.

    Reads every archive file in the results directory; session isolation is
    handled by the current_only / version filter in _load_results(), not here.
    After the offline aggregator runs it deletes old archive files, so only
    the current session's files survive — keeping the list naturally short.

    Returns files sorted by mtime ascending (oldest first).
    Priority: explicit --csv override (single file) > all archive CSVs.
    """
    if csv_override and csv_override.exists():
        return [csv_override]

    if not _RESULTS_ARCHIVE_DIR.exists():
        return []

    candidates = [
        p for p in _RESULTS_ARCHIVE_DIR.glob("*.csv")
        if p.name not in _SKIP_CSV_NAMES
    ]
    return sorted(candidates, key=lambda p: p.stat().st_mtime)


def _load_results(current_only: bool = True, paths: list[Path] | None = None) -> tuple[list[dict], float | None]:
    """Return (rows, latest_mtime) aggregated across all archive CSVs.

    Reads all per-run archive files and merges their rows.  Deduplicates on
    (benchmark_type, provider, model, filename, run_id) so that a row present
    in multiple archives (e.g. after a re-run) is counted only once.
    The returned mtime is that of the most recently modified file.

    If current_only, keep only rows from the latest bench session.

    Session identification strategy (in priority order):
    1. version field with format YYYYMMDDHHMMSS-sha  → filter by exact max version.
    2. Fallback: rows whose version date (YYYYMMDD) matches the most recent version date.
    3. Last resort: max run_id (old behaviour for CSVs without version field).
    """
    targets = paths if paths is not None else _find_archive_csvs()
    if not targets:
        return [], None

    rows: list[dict] = []
    seen: set[tuple] = set()
    latest_mtime: float | None = None

    for target in targets:
        if not target.exists():
            continue
        mtime = target.stat().st_mtime
        if latest_mtime is None or mtime > latest_mtime:
            latest_mtime = mtime
        try:
            with open(target, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    dedup_key = (
                        row.get("benchmark_type", ""),
                        row.get("provider", ""),
                        row.get("model", ""),
                        row.get("filename", ""),
                        row.get("run_id", ""),
                        row.get("scenario", ""),
                    )
                    if dedup_key not in seen:
                        seen.add(dedup_key)
                        rows.append(row)
        except Exception:
            continue

    mtime = latest_mtime

    if current_only and rows:
        # Collect version strings that look like timestamps: start with YYYYMMDD digits
        ts_versions = [
            (r.get("version") or "").strip()
            for r in rows
            if len((r.get("version") or "").strip()) >= 8
            and (r.get("version") or "").strip()[:8].isdigit()
        ]

        if ts_versions:
            max_ver  = max(ts_versions)          # lex max = most recent timestamp
            max_date = max_ver[:8]               # YYYYMMDD portion

            # If a significant fraction share the exact max version,
            # the .version file was present → filter by exact match (strict).
            # Otherwise (each invocation has unique ts) → filter by date only.
            exact_count = sum(1 for v in ts_versions if v == max_ver)
            if exact_count >= len(ts_versions) * 0.5:
                # .version file scenario: all rows of this push share the same string
                rows = [r for r in rows
                        if (r.get("version") or "").strip() == max_ver]
            else:
                # No .version file: group by same calendar day as the latest row
                rows = [r for r in rows
                        if (r.get("version") or "").strip()[:8] == max_date]
        else:
            # Fallback: filter by max run_id (old CSV format without version)
            try:
                max_run = max(int(r.get("run_id", 0) or 0) for r in rows)
                rows = [r for r in rows if int(r.get("run_id", 0) or 0) == max_run]
            except (ValueError, TypeError):
                pass

    return rows, mtime

# ── Snapshot ───────────────────────────────────────────────────────────────
def _snapshot(rows: list[dict], exp_per_model: int, n_models_total: int = 0) -> dict:
    counts:    dict[tuple, int]  = defaultdict(int)
    # Per-phase counts: {"classifier": {(prov, mdl): n}, "categorizer": {...}}
    counts_by_phase: dict[str, dict[tuple, int]] = defaultdict(lambda: defaultdict(int))
    durations: list[float]       = []
    cpu_samples: list[float]     = []
    gpu_samples: list[float]     = []
    phases: dict[str, int]       = defaultdict(int)
    # Inference params: one sample per model key (constant across files)
    infer_params: dict[tuple, dict] = {}

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
            counts_by_phase[bt][key] += 1
        # Capture inference params once per model key (values are constant)
        if key not in infer_params:
            p: dict[str, str] = {}
            for field in ("n_gpu_layers", "n_threads", "flash_attn"):
                v = (row.get(field) or "").strip()
                if v:
                    p[field] = v
            if p:
                infer_params[key] = p

    total_done   = sum(counts.values())
    n_models     = len(counts)
    # Use declared total if provided, otherwise fall back to models seen so far
    n_models_eff = n_models_total if n_models_total > 0 else n_models
    total_exp    = n_models_eff * exp_per_model if n_models_eff else 0
    avg_dur      = sum(durations) / len(durations) if durations else 0
    rate_fpm   = (60 / avg_dur) if avg_dur > 0 else 0
    # TODO(backlog): ETA deve riflettere il bench COMPLETO, non il modello corrente.
    # avg_dur è la media globale di tutti i file completati (inclusi modelli già finiti,
    # potenzialmente più veloci di quelli rimanenti). Soluzione: calcolare rate_fpm
    # separatamente per il modello in esecuzione (ultimi N file dello stesso model key)
    # e usare quel rate per proiettare i file rimanenti. Il totale atteso deve includere
    # entrambe le fasi (classifier + categorizer) per ogni modello.
    # Rif: GitHub issue #XX — "monitor: ETA deve essere del bench completo"

    # Determine the run_id being displayed
    run_ids: set[int] = set()
    for row in rows:
        try:
            run_ids.add(int(row.get("run_id", 0) or 0))
        except (ValueError, TypeError):
            pass
    current_run_id = max(run_ids) if run_ids else 0

    return {
        "counts":           dict(counts),
        "counts_by_phase":  {ph: dict(c) for ph, c in counts_by_phase.items()},
        "total_done":       total_done,
        "total_exp":        total_exp,
        "n_models_eff":     n_models_eff,
        "exp_per_model":    exp_per_model,
        "avg_dur_s":        avg_dur,
        "rate_fpm":         rate_fpm,
        "rate_fps":         rate_fpm / 60,
        "cpu_avg_hist":     sum(cpu_samples) / len(cpu_samples) if cpu_samples else 0.0,
        "gpu_avg_hist":     sum(gpu_samples) / len(gpu_samples) if gpu_samples else 0.0,
        "phases":           dict(phases),
        "run_id":           current_run_id,
        "infer_params":     infer_params,
    }

# ── Report ─────────────────────────────────────────────────────────────────
def _print_report(snap: dict, elapsed_s: float, interval: int,
                  live_cpu: float = 0.0, live_gpu: float = 0.0,
                  csv_active: bool = True) -> None:
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
    run_id       = snap["run_id"]
    infer_params = snap.get("infer_params", {})

    W = 72

    print("═" * W)
    run_label = f"run_id={run_id}" if run_id > 0 else "run_id=?"
    stale_tag = "" if csv_active else "  ⚠ DATI PRECEDENTI"
    print(f"  BENCHMARK MONITOR  —  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  [{run_label}]{stale_tag}")
    n_models_eff = snap["n_models_eff"]
    rate_str = f"{rate_fpm:.1f} file/min  ({avg_dur_s:.1f}s/file)" if rate_fpm > 0 else "—"
    eta_str  = _fmt_eta(remaining, rate_fps)
    models_str = f"{n_models_eff} pair(s)" if n_models_eff else "?"
    print(f"  Elapsed : {_fmt_duration(elapsed_s)}")
    print(f"  Models  : {models_str}  ×  {exp_per if exp_per else '?'} file  =  {total_exp if total_exp else '?'} totali")
    print(f"  Rate    : {rate_str}")
    print(f"  ETA     : {eta_str}")
    if not csv_active:
        print(f"  !! CSV non aggiornato dal avvio monitor — benchmark non ancora attivo")

    # ── Pipeline phase ─────────────────────────────────────────────────────
    if phases:
        phase_parts = [f"{k}: {v}" for k, v in sorted(phases.items())]
        # Highlight the most recent phase (highest count when only one active)
        active = max(phases, key=lambda k: phases[k]) if phases else "?"
        print(f"  Phase   : {active}  ({', '.join(phase_parts)} rows)")

    # ── HW stats ───────────────────────────────────────────────────────────
    if _hw is not None:
        cpu_bar  = _bar(int(live_cpu), 100, 10)
        gpu_bar  = _bar(int(live_gpu), 100, 10)
        cpu_s    = f"{live_cpu:5.1f}%" if _gpu_source != "n/a" or live_cpu > 0 else "  N/A "
        gpu_s    = f"{live_gpu:5.1f}%" if "none" not in _gpu_source else "  N/A "
        live_str = f"CPU {cpu_s}  {cpu_bar}  |  GPU {gpu_s}  {gpu_bar}  [{_gpu_source}]"
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
    col_b, col_m, col_d = 12, 30, 5

    counts_by_phase = snap.get("counts_by_phase", {})

    # Canonical phase order; fall back to sorted keys for unknown phases
    _PHASE_ORDER = ["classifier", "categorizer"]
    present_phases = [ph for ph in _PHASE_ORDER if ph in counts_by_phase]
    present_phases += [ph for ph in sorted(counts_by_phase) if ph not in _PHASE_ORDER]

    def _sort_key(item: tuple) -> tuple:
        (prov, mdl), cnt = item
        if exp_per > 0:
            if 0 < cnt < exp_per: return (0, prov, mdl)   # running
            if cnt >= exp_per:    return (1, prov, mdl)   # done
        return (2, prov, mdl)                              # waiting

    def _ip_tag(prov: str, mdl: str) -> str:
        ip = infer_params.get((prov, mdl), {})
        if not ip:
            return ""
        parts = []
        gl = ip.get("n_gpu_layers", "")
        if gl:
            parts.append(f"gpu={gl}")
        nt = ip.get("n_threads", "")
        if nt:
            parts.append(f"thr={nt}")
        fa = ip.get("flash_attn", "")
        if fa and fa.lower() not in ("", "false", "0"):
            parts.append("flash")
        return f"  [{', '.join(parts)}]" if parts else ""

    if present_phases:
        for ph in present_phases:
            ph_counts = counts_by_phase[ph]
            ph_done   = sum(ph_counts.values())
            # Union of all known models to show waiting rows too
            all_keys  = counts.keys() | ph_counts.keys()
            ph_exp    = n_models_eff * exp_per if n_models_eff and exp_per else 0

            ph_bar = _bar(ph_done, ph_exp, 20)
            ph_pct = f"{min(100*ph_done//ph_exp, 100):3d}%" if ph_exp else "  ?%"
            print(f"  ── {ph.upper():<12}  {ph_done:>4} / {ph_exp if ph_exp else '?':>4}  {ph_bar} {ph_pct}")
            print(f"  {'Backend':<{col_b}}{'Model':<{col_m}}{'Done':>{col_d}}   Progress")
            print("  " + "─" * (W - 2))

            # Sort by phase-specific count so a model with 50 classifier + 9
            # categorizer rows (59 total) is still shown as "in corso" for the
            # categorizer phase — not prematurely marked "done" because its
            # global count (59) crossed exp_per (50).
            def _ph_sort_key(item: tuple) -> tuple:
                (prov, mdl), cnt = item   # cnt = ph_counts.get(k, 0)
                if exp_per > 0:
                    if 0 < cnt < exp_per: return (0, prov, mdl)   # running
                    if cnt >= exp_per:    return (1, prov, mdl)   # done
                return (2, prov, mdl)                              # waiting

            for (prov, mdl), _ in sorted(
                {k: ph_counts.get(k, 0) for k in all_keys}.items(),
                key=_ph_sort_key,
            ):
                cnt  = ph_counts.get((prov, mdl), 0)
                bar  = _bar(cnt, exp_per)
                pct  = f"{min(100*cnt//exp_per, 100):3d}%" if exp_per else "  ?%"
                tag  = ""
                if exp_per > 0:
                    if 0 < cnt < exp_per: tag = " ← in corso"
                    elif cnt == 0:        tag = " ○ in attesa"
                tag += _ip_tag(prov, mdl)
                print(f"  {prov:<{col_b}}{mdl:<{col_m}}{cnt:>{col_d}}   {bar} {pct}{tag}")

            print("  " + "─" * (W - 2))
    else:
        # Fallback: no phase info, single table
        print(f"  {'Backend':<{col_b}}{'Model':<{col_m}}{'Done':>{col_d}}   Progress")
        print("  " + "─" * (W - 2))
        for (prov, mdl), cnt in sorted(counts.items(), key=_sort_key):
            bar = _bar(cnt, exp_per)
            pct = f"{min(100*cnt//exp_per, 100):3d}%" if exp_per else "  ?%"
            tag = ""
            if exp_per > 0:
                if 0 < cnt < exp_per: tag = " ← in corso"
                elif cnt == 0:        tag = " ○ in attesa"
            tag += _ip_tag(prov, mdl)
            print(f"  {prov:<{col_b}}{mdl:<{col_m}}{cnt:>{col_d}}   {bar} {pct}{tag}")
        print("  " + "─" * (W - 2))

    tot_bar = _bar(total_done, total_exp, 20)
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
    parser.add_argument("--models",   type=int,  default=0,     help="Total model/backend pairs expected (0 = auto from benchmark_models.csv)")
    parser.add_argument("--once",     action="store_true",      help="Print once and exit")
    parser.add_argument("--all",      action="store_true",      help="Show all historical data, not just current run_id")
    parser.add_argument("--csv",      type=Path, default=None,
                        help="Path CSV da monitorare (default: auto-detect da results_archive/)")
    args = parser.parse_args()

    manifest_count = _load_manifest_count()
    exp_per_model  = (args.total if args.total > 0 else manifest_count) * args.runs
    current_only   = not args.all

    # Auto-detect expected model/backend pairs if not explicitly declared
    n_models_total = args.models if args.models > 0 else _count_expected_models()

    start_time    = time.monotonic()
    _csv_override = [args.csv] if args.csv else None  # explicit override (single file)
    # Record the latest mtime across all archive CSVs at startup → detect new writes
    _initial_paths = _csv_override or _find_archive_csvs()
    start_mtime    = (
        max((p.stat().st_mtime for p in _initial_paths if p.exists()), default=None)
        if _initial_paths else None
    )

    if _initial_paths:
        print(f"  Monitoring: {len(_initial_paths)} archive file(s) in {_RESULTS_ARCHIVE_DIR.name}/")
    else:
        print("  Waiting for benchmark to start...")

    def _run_once() -> None:
        # Re-evaluate archive list on every tick to catch newly written files
        live_paths = _csv_override or _find_archive_csvs()
        rows, csv_mtime = _load_results(current_only=current_only, paths=live_paths)
        snap     = _snapshot(rows, exp_per_model, n_models_total=n_models_total)
        elapsed  = time.monotonic() - start_time
        # "active" if any archive file is newer than the newest one at startup
        csv_active = (csv_mtime is not None and
                      (start_mtime is None or csv_mtime > start_mtime))
        # Live HW sample (taken just before printing for freshness)
        live_cpu, live_gpu = _hw.sample_once() if _hw is not None else (0.0, 0.0)
        _clear()
        _print_report(snap, elapsed, 0 if args.once else args.interval,
                      live_cpu=live_cpu, live_gpu=live_gpu,
                      csv_active=csv_active)

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
