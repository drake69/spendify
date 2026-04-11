#!/usr/bin/env python3
"""
benchmark_stats.py — Statistiche minime di accuratezza per fase e modello.

Legge results_all_runs.csv (o tutti i CSV nella cartella results/) e produce
una tabella riassuntiva per modello × fase con le metriche chiave.

Uso:
    python benchmark_stats.py                          # default: results/results_all_runs.csv
    python benchmark_stats.py results/results_all_runs.csv
    python benchmark_stats.py results/*.csv            # merge di più file
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import math

import pandas as pd

# Importa tabella gruppi di equivalenza dal modulo condiviso
sys.path.insert(0, str(Path(__file__).parent))
from accuracy_groups import commit_to_group, UnknownCommitError


# ── Metriche per fase ─────────────────────────────────────────────────
CLASSIFIER_METRICS = {
    "header_match":     ("header_match",     "Header %"),
    "rows_match":       ("rows_match",       "Rows %"),
    "doc_type_match":   ("doc_type_match",   "DocType %"),
    "convention_match": ("convention_match", "Conv %"),
    "amount_accuracy":  ("amount_accuracy",  "Amount %"),
    "date_accuracy":    ("date_accuracy",    "Date %"),
    "parse_rate":       ("parse_rate",       "Parse %"),
}

CATEGORIZER_METRICS = {
    "cat_exact_accuracy": ("cat_exact_accuracy", "Exact %"),
    "cat_fuzzy_accuracy": ("cat_fuzzy_accuracy", "Fuzzy %"),
    "cat_fallback_rate":  ("cat_fallback_rate",  "Fallback %"),
}

PERF_METRICS = {
    "duration_seconds":   ("duration_seconds",   "Dur(s)"),
    "tokens_per_second":  ("tokens_per_second",  "Tok/s"),
}


def load_data(paths: list[Path]) -> pd.DataFrame:
    """Carica e concatena CSV, deduplicando per header ripetuti."""
    frames = []
    for p in paths:
        try:
            df = pd.read_csv(p, low_memory=False)
            frames.append(df)
        except Exception as e:
            print(f"⚠ Errore leggendo {p}: {e}", file=sys.stderr)
    if not frames:
        sys.exit("Nessun dato trovato.")
    df = pd.concat(frames, ignore_index=True)
    # Rimuovi righe che sono header duplicati (da concat di file con header)
    if "benchmark_type" in df.columns:
        df = df[df["benchmark_type"] != "benchmark_type"]
    return df


def coerce_numeric(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Converte colonne in numeric, ignorando errori."""
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def compute_stats(df: pd.DataFrame, metrics: dict, group_cols: list[str]) -> pd.DataFrame:
    """Calcola media ± std per ogni metrica, raggruppando per group_cols."""
    all_cols = [m[0] for m in metrics.values()]
    existing = [c for c in all_cols if c in df.columns]
    if not existing:
        return pd.DataFrame()

    df = coerce_numeric(df, existing)
    agg_funcs = {}
    for col in existing:
        agg_funcs[col] = ["count", "mean", "std", "min", "max"]

    grouped = df.groupby(group_cols, dropna=False).agg(agg_funcs)
    # Flatten multi-level columns
    grouped.columns = [f"{c}_{stat}" for c, stat in grouped.columns]
    grouped = grouped.reset_index()
    return grouped


MIN_N_SIGNIFICANT = 10  # flag models with N < this


# t-values for CI 95% (two-tailed, alpha=0.05) by df=n-1
# For df > 120, use 1.96 (normal approximation)
_T95 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571,
    6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
    11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131,
    16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086,
    25: 2.060, 30: 2.042, 40: 2.021, 50: 2.009, 60: 2.000,
    80: 1.990, 100: 1.984, 120: 1.980,
}


def _t_value(df: int) -> float:
    """Lookup t-value for CI 95% given degrees of freedom."""
    if df in _T95:
        return _T95[df]
    # Find closest lower key
    keys = sorted(_T95.keys())
    for k in reversed(keys):
        if k <= df:
            return _T95[k]
    return 1.96  # fallback for large df


def ci95_half_width(std: float, n: int) -> float:
    """CI 95% half-width using t distribution (no scipy dependency)."""
    if n < 2 or pd.isna(std) or std == 0:
        return 0.0
    t_val = _t_value(n - 1)
    return t_val * std / math.sqrt(n)


def format_pct(val, decimals=1) -> str:
    if pd.isna(val):
        return "—"
    return f"{val * 100:.{decimals}f}%"


def format_num(val, decimals=1) -> str:
    if pd.isna(val):
        return "—"
    return f"{val:.{decimals}f}"


def print_phase_table(df: pd.DataFrame, phase: str, metrics: dict,
                      group_cols: list[str]) -> None:
    """Stampa tabella formattata per una fase."""
    subset = df[df["benchmark_type"] == phase] if "benchmark_type" in df.columns else df
    if subset.empty:
        print(f"\n  Nessun dato per fase: {phase}\n")
        return

    stats = compute_stats(subset, metrics, group_cols)
    if stats.empty:
        print(f"\n  Nessuna metrica disponibile per fase: {phase}\n")
        return

    # Build display table
    rows = []
    for _, row in stats.iterrows():
        model = row.get("model", "?")
        provider = row.get("provider", "?")
        n = 0
        cells = {"provider": provider, "model": model}
        # Extra group columns (commit, host, etc.)
        for gc in group_cols:
            if gc not in ("provider", "model") and gc in row.index:
                cells[gc] = str(row.get(gc, ""))

        _ci_widths = []  # collect CI half-widths for sorting
        for key, (col, label) in metrics.items():
            count_col = f"{col}_count"
            mean_col = f"{col}_mean"
            std_col = f"{col}_std"

            if mean_col in row.index:
                cnt = int(row.get(count_col, 0)) if pd.notna(row.get(count_col, 0)) else 0
                n = max(n, cnt)
                mean_val = row[mean_col]
                std_val = row.get(std_col, float("nan"))

                if "duration" in col or "tokens" in col:
                    cells[label] = format_num(mean_val)
                else:
                    hw = ci95_half_width(std_val, cnt)
                    _ci_widths.append(hw)
                    if hw > 0:
                        cells[label] = f"{format_pct(mean_val)}±{hw*100:.0f}"
                    else:
                        cells[label] = format_pct(mean_val)

        # Flag for low N
        sig = "" if n >= MIN_N_SIGNIFICANT else " ⚠"
        cells["N"] = f"{n}{sig}"
        cells["_ci_width"] = max(_ci_widths) if _ci_widths else 0.0
        cells["_n"] = n
        rows.append(cells)

    if not rows:
        return

    # Sort: by extra group cols first, then by first meaningful accuracy metric descending.
    # "Meaningful" = first metric where not all values are identical (e.g. skip Header % if all 100%).
    extra_cols = [gc for gc in group_cols if gc not in ("provider", "model")]
    metric_labels = [m[1] for m in metrics.values()]

    # Find first metric with variance
    sort_metric = metric_labels[0]  # fallback
    for label in metric_labels:
        vals = set()
        for r in rows:
            v = r.get(label, "")
            vals.add(v)
        if len(vals) > 1:
            sort_metric = label
            break

    def _sort_key(r):
        # Extra cols ascending, then accuracy descending, then CI width ascending (tighter = better)
        extra = tuple(str(r.get(k, "")) for k in extra_cols)
        # Parse percentage string like "86.0%±35" → 86.0
        raw = r.get(sort_metric, "0")
        try:
            num = float(str(raw).split("%")[0].replace("—", "-999"))
        except (ValueError, IndexError):
            num = -999
        ci = r.get("_ci_width", 0.0)
        return (*extra, -num, ci)  # descending accuracy, ascending CI width

    rows.sort(key=_sort_key)

    # Print
    all_labels = ["provider", "model"] + extra_cols + ["N"] + [m[1] for m in metrics.values()]
    # Add perf metrics
    for key, (col, label) in PERF_METRICS.items():
        if any(label in r for r in rows):
            all_labels.append(label)

    # Compute column widths
    widths = {}
    for label in all_labels:
        w = len(label)
        for r in rows:
            w = max(w, len(str(r.get(label, ""))))
        widths[label] = w + 2

    # Header
    header_line = "".join(label.ljust(widths.get(label, 10)) for label in all_labels if label in widths)

    phase_upper = phase.upper()
    print(f"\n{'═' * 80}")
    print(f"  {phase_upper}")
    print(f"{'═' * 80}")
    print(f"  {header_line}")
    print(f"  {'─' * len(header_line)}")

    prev_group = None
    for r in rows:
        # Separator between different groups
        if extra_cols:
            cur_group = tuple(str(r.get(k, "")) for k in extra_cols)
            if prev_group is not None and cur_group != prev_group:
                print(f"  {'─' * len(header_line)}")
            prev_group = cur_group
        line = "".join(str(r.get(label, "—")).ljust(widths.get(label, 10)) for label in all_labels if label in widths)
        print(f"  {line}")

    # Legend
    has_low_n = any(r.get("_n", 0) < MIN_N_SIGNIFICANT for r in rows)
    print(f"\n  ±N = CI 95% half-width (t di Student)  |  Sorted by {sort_metric} desc, CI width asc")
    if has_low_n:
        print(f"  ⚠ = N < {MIN_N_SIGNIFICANT} — risultato non statisticamente significativo")
    print()


def _trim_outliers(series: pd.Series, lo: float = 0.05, hi: float = 0.95) -> pd.Series:
    """Remove values below lo and above hi percentiles."""
    if series.dropna().empty:
        return series
    q_lo = series.quantile(lo)
    q_hi = series.quantile(hi)
    return series.where((series >= q_lo) & (series <= q_hi))


def print_perf_table(df: pd.DataFrame, group_cols: list[str]) -> None:
    """Stampa tabella performance con sec/schema e sec/10tx (trimmed p5-p95)."""
    perf_cols = [c for c in ["duration_seconds", "tokens_per_second", "n_transactions"]
                 if c in df.columns]
    if "duration_seconds" not in df.columns:
        return

    df = coerce_numeric(df, perf_cols)

    # Per il categorizer, calcola sec/10tx per ogni riga
    cat_mask = df["benchmark_type"] == "categorizer" if "benchmark_type" in df.columns else pd.Series(False, index=df.index)
    df = df.copy()
    df["sec_per_10tx"] = float("nan")
    if "n_transactions" in df.columns:
        valid = cat_mask & (df["n_transactions"] > 0) & (df["duration_seconds"] > 0)
        df.loc[valid, "sec_per_10tx"] = df.loc[valid, "duration_seconds"] / df.loc[valid, "n_transactions"] * 10

    agg_cols = ["duration_seconds", "tokens_per_second", "sec_per_10tx"]
    agg_cols = [c for c in agg_cols if c in df.columns]

    # Trim outliers (p5-p95) per group before aggregation
    trimmed_frames = []
    for _key, _grp in df.groupby(group_cols + ["benchmark_type"], dropna=False):
        _grp = _grp.copy()
        for c in agg_cols:
            if c in _grp.columns:
                _grp[c] = _trim_outliers(_grp[c])
        trimmed_frames.append(_grp)
    df_trimmed = pd.concat(trimmed_frames, ignore_index=True) if trimmed_frames else df

    stats = df_trimmed.groupby(group_cols + ["benchmark_type"], dropna=False).agg(
        **{f"{c}_mean": (c, "mean") for c in agg_cols},
        **{f"{c}_count": (c, "count") for c in agg_cols},
    ).reset_index()

    if stats.empty:
        return

    extra_cols = [gc for gc in group_cols if gc not in ("provider", "model")]
    sort_cols = extra_cols + ["provider", "model", "benchmark_type"]
    stats = stats.sort_values(sort_cols).reset_index(drop=True)

    print(f"\n{'═' * 80}")
    print(f"  PERFORMANCE")
    print(f"{'═' * 80}")
    hdr = f"  {'provider':<12}{'model':<40}"
    for ec in extra_cols:
        hdr += f"{ec:<14}"
    hdr += f"{'phase':<14}{'N':<6}{'s/schema':<10}{'s/10tx':<10}{'tok/s':<10}"
    print(hdr)
    print(f"  {'─' * len(hdr)}")

    for _, row in stats.iterrows():
        provider = row.get("provider", "?")
        model = row.get("model", "?")
        phase = row.get("benchmark_type", "?")
        n = int(row.get("duration_seconds_count", 0))
        dur = row.get("duration_seconds_mean", float("nan"))
        tps = row.get("tokens_per_second_mean", float("nan"))
        s10tx = row.get("sec_per_10tx_mean", float("nan"))

        # s/schema only for classifier, s/10tx only for categorizer
        s_schema = format_num(dur) if phase == "classifier" else "—"
        s_10tx = format_num(s10tx) if phase == "categorizer" and pd.notna(s10tx) else "—"
        tok_s = format_num(tps) if pd.notna(tps) and tps > 0 else "—"

        line = f"  {provider:<12}{model:<40}"
        for ec in extra_cols:
            line += f"{str(row.get(ec, '')):<14}"
        line += f"{phase:<14}{n:<6}{s_schema:<10}{s_10tx:<10}{tok_s:<10}"
        print(line)

    print(f"\n  s/schema = avg seconds per file (classifier)  |  s/10tx = avg seconds per 10 transactions (categorizer)  |  trimmed p5-p95")
    print()


def main():
    parser = argparse.ArgumentParser(description="Benchmark accuracy stats")
    parser.add_argument("files", nargs="*", default=None,
                        help="CSV file(s) to analyze (default: all run archives in results/)")
    parser.add_argument("--by-host", action="store_true",
                        help="Raggruppa anche per hostname")
    parser.add_argument("--by-commit", action="store_true",
                        help="Raggruppa anche per git commit/version")
    parser.add_argument("--by-group", action="store_true",
                        help="Raggruppa per gruppo di equivalenza accuratezza (G0-G6)")
    args = parser.parse_args()

    if args.files:
        paths = [Path(f) for f in args.files]
    else:
        # Default: tutti i file archivio nella cartella results/
        # (pattern: YYYYMMDD*_hostname*.csv — esclude aggregati)
        results_dir = Path(__file__).parent / "results"
        SKIP = {"results_all_runs.csv", "results_run_01.csv", "results_merged.csv",
                "summary_global.csv", "summary_variance.csv", "cat_results_detail.csv",
                "benchmark_config.json", "cat_benchmark_config.json"}
        paths = sorted(
            p for p in results_dir.glob("20*.csv")
            if p.name not in SKIP
        )
    paths = [p for p in paths if p.exists()]
    if not paths:
        sys.exit("Nessun file CSV trovato. Specifica percorsi o lancia dalla dir benchmark/.")

    df = load_data(paths)
    print(f"\n  Caricati {len(df)} record da {len(paths)} file\n")

    # Show unique models
    if "model" in df.columns:
        models = df["model"].unique()
        print(f"  Modelli trovati: {len(models)}")
        for m in sorted(models):
            count = len(df[df["model"] == m])
            print(f"    • {m} ({count} record)")

    # Aggiungi colonna acc_group se richiesto
    if args.by_group and "git_commit" in df.columns:
        unknown = set()
        def _safe_group(c):
            try:
                return commit_to_group(c)
            except UnknownCommitError:
                unknown.add(c)
                return "UNKNOWN"
        df["acc_group"] = df["git_commit"].apply(_safe_group)
        if unknown:
            print(f"\n  ⚠ Commit non censiti in accuracy_groups.py: {', '.join(sorted(unknown))}")
            print(f"    Aggiorna benchmark/accuracy_groups.py prima di confrontare risultati.\n")
            sys.exit(1)

    group_cols = ["provider", "model"]
    if args.by_group:
        group_cols.append("acc_group")
    elif args.by_commit:
        # Usa git_commit (short hash) come separatore
        if "git_commit" in df.columns:
            group_cols.append("git_commit")
        elif "version" in df.columns:
            group_cols.append("version")
    if args.by_host:
        # Load machine name mapping if available
        _names_file = Path(__file__).parent / "machine_names.csv"
        _hostname_to_name: dict[str, str] = {}
        if _names_file.exists():
            with open(_names_file, encoding="utf-8") as _f:
                import csv as _csv
                for _row in _csv.DictReader(_f):
                    _hostname_to_name[_row["hostname"]] = _row["machine_name"]

        if "runtime_hostname" in df.columns and _hostname_to_name:
            df["machine"] = df["runtime_hostname"].map(
                lambda h: _hostname_to_name.get(h, h)
            )
            group_cols.append("machine")
        elif "runtime_cpu" in df.columns and "runtime_gpu" in df.columns:
            df["hw"] = df["runtime_cpu"].astype(str).fillna("?") + " / " + df["runtime_gpu"].astype(str).fillna("?")
            group_cols.append("hw")
        elif "runtime_hostname" in df.columns:
            group_cols.append("runtime_hostname")

    # Classifier stats
    print_phase_table(df, "classifier", CLASSIFIER_METRICS, group_cols)

    # Categorizer stats
    print_phase_table(df, "categorizer", CATEGORIZER_METRICS, group_cols)

    # Performance
    print_perf_table(df, group_cols)

    # Summary table
    print_summary_table(df, group_cols)


def print_summary_table(df: pd.DataFrame, group_cols: list[str]) -> None:
    """Tabella riassuntiva: accuracy + performance per modello, ordinata per Exact % desc."""
    needed = ["benchmark_type", "provider", "model", "duration_seconds"]
    if not all(c in df.columns for c in needed):
        return

    acc_cols = ["amount_accuracy", "date_accuracy", "parse_rate",
                "cat_exact_accuracy", "cat_fuzzy_accuracy", "cat_fallback_rate"]
    perf_cols = ["duration_seconds", "tokens_per_second", "n_transactions"]
    all_num = acc_cols + perf_cols
    df = coerce_numeric(df.copy(), [c for c in all_num if c in df.columns])

    # Compute sec_per_10tx per row
    cat_mask = df["benchmark_type"] == "categorizer"
    df["sec_per_10tx"] = float("nan")
    if "n_transactions" in df.columns:
        valid = cat_mask & (df["n_transactions"] > 0) & (df["duration_seconds"] > 0)
        df.loc[valid, "sec_per_10tx"] = df.loc[valid, "duration_seconds"] / df.loc[valid, "n_transactions"] * 10

    extra_cols = [gc for gc in group_cols if gc not in ("provider", "model")]

    # Aggregate classifier
    clf = df[df["benchmark_type"] == "classifier"]
    clf_raw_counts = clf.groupby(group_cols, dropna=False).agg(
        clf_n_raw=("duration_seconds", "count"),
    ).reset_index() if not clf.empty else pd.DataFrame()
    clf_stats = clf.groupby(group_cols, dropna=False).agg(
        clf_n=("duration_seconds", "count"),
        clf_amount=("amount_accuracy", "mean"),
        clf_date=("date_accuracy", "mean"),
        clf_parse=("parse_rate", "mean"),
        clf_s_schema=("duration_seconds", "mean"),
        clf_tok_s=("tokens_per_second", "mean"),
    ).reset_index() if not clf.empty else pd.DataFrame()
    if not clf_stats.empty and not clf_raw_counts.empty:
        clf_stats = pd.merge(clf_stats, clf_raw_counts, on=group_cols, how="left")

    # Aggregate categorizer (with trimming)
    cat = df[df["benchmark_type"] == "categorizer"]
    if not cat.empty:
        # Raw count before trimming (= files actually run)
        cat_raw_counts = cat.groupby(group_cols, dropna=False).agg(
            cat_n_raw=("duration_seconds", "count"),
        ).reset_index()

        trimmed_frames = []
        for _key, _grp in cat.groupby(group_cols, dropna=False):
            _grp = _grp.copy()
            for c in ["sec_per_10tx", "duration_seconds"]:
                if c in _grp.columns:
                    _grp[c] = _trim_outliers(_grp[c])
            trimmed_frames.append(_grp)
        cat_trimmed = pd.concat(trimmed_frames, ignore_index=True)

        cat_stats = cat_trimmed.groupby(group_cols, dropna=False).agg(
            cat_n=("duration_seconds", "count"),
            cat_exact=("cat_exact_accuracy", "mean"),
            cat_exact_std=("cat_exact_accuracy", "std"),
            cat_fuzzy=("cat_fuzzy_accuracy", "mean"),
            cat_fallback=("cat_fallback_rate", "mean"),
            cat_s_10tx=("sec_per_10tx", "mean"),
            cat_tok_s=("tokens_per_second", "mean"),
        ).reset_index()
        # Merge raw counts
        cat_stats = pd.merge(cat_stats, cat_raw_counts, on=group_cols, how="left")
    else:
        cat_stats = pd.DataFrame()

    # Merge
    if clf_stats.empty and cat_stats.empty:
        return
    if not clf_stats.empty and not cat_stats.empty:
        merged = pd.merge(clf_stats, cat_stats, on=group_cols, how="outer")
    elif not clf_stats.empty:
        merged = clf_stats
    else:
        merged = cat_stats

    if merged.empty:
        return

    # Build rows
    rows = []
    for _, row in merged.iterrows():
        model = row.get("model", "?")
        provider = row.get("provider", "?")
        r = {"provider": provider, "model": model}
        for gc in extra_cols:
            if gc in row.index:
                r[gc] = str(row.get(gc, ""))

        # Classifier
        clf_n = int(row.get("clf_n", 0)) if pd.notna(row.get("clf_n")) else 0
        clf_n_raw = int(row.get("clf_n_raw", 0)) if pd.notna(row.get("clf_n_raw")) else 0
        if clf_n > 0:
            r["Clf N"] = f"{clf_n}/{clf_n_raw}" if clf_n < clf_n_raw else str(clf_n)
        else:
            r["Clf N"] = "—"
        r["Amt %"] = format_pct(row.get("clf_amount")) if clf_n > 0 else "—"
        r["s/schema"] = format_num(row.get("clf_s_schema")) if clf_n > 0 else "—"

        # Categorizer
        cat_n = int(row.get("cat_n", 0)) if pd.notna(row.get("cat_n")) else 0
        cat_n_raw = int(row.get("cat_n_raw", 0)) if pd.notna(row.get("cat_n_raw")) else 0
        cat_exact_val = row.get("cat_exact")
        cat_exact_std = row.get("cat_exact_std", float("nan"))
        hw = ci95_half_width(cat_exact_std, cat_n) if cat_n >= 2 else 0.0

        sig = "" if cat_n >= MIN_N_SIGNIFICANT else " ⚠" if cat_n > 0 else ""
        if cat_n > 0:
            if cat_n < cat_n_raw:
                r["Cat N"] = f"{cat_n}/{cat_n_raw}{sig}"
            else:
                r["Cat N"] = f"{cat_n}{sig}"
        else:
            r["Cat N"] = "—"

        if cat_n > 0 and pd.notna(cat_exact_val):
            if hw > 0:
                r["Exact %"] = f"{format_pct(cat_exact_val)}±{hw*100:.0f}"
            else:
                r["Exact %"] = format_pct(cat_exact_val)
        else:
            r["Exact %"] = "—"
        r["Fuzzy %"] = format_pct(row.get("cat_fuzzy")) if cat_n > 0 else "—"
        # Err % = categorizzato ma sbagliato (1 - fuzzy - fallback)
        _fuzzy = row.get("cat_fuzzy")
        _fb = row.get("cat_fallback")
        if cat_n > 0 and pd.notna(_fuzzy) and pd.notna(_fb):
            _err = 1.0 - _fuzzy - _fb
            r["Err %"] = format_pct(max(0.0, _err))
        else:
            r["Err %"] = "—"
        r["Fb %"] = format_pct(row.get("cat_fallback")) if cat_n > 0 else "—"
        r["s/10tx"] = format_num(row.get("cat_s_10tx")) if cat_n > 0 and pd.notna(row.get("cat_s_10tx")) else "—"

        # For sorting
        r["_exact"] = cat_exact_val if pd.notna(cat_exact_val) else -1
        r["_ci"] = hw
        rows.append(r)

    # Sort by extra cols, then exact % desc, CI asc
    rows.sort(key=lambda r: (
        tuple(str(r.get(k, "")) for k in extra_cols),
        -r.get("_exact", -1),
        r.get("_ci", 0),
    ))

    # Print
    labels = ["provider", "model"] + extra_cols + [
        "Clf N", "Amt %", "s/schema",
        "Cat N", "Exact %", "Fuzzy %", "Err %", "Fb %", "s/10tx",
    ]
    widths = {}
    for label in labels:
        w = len(label)
        for r in rows:
            w = max(w, len(str(r.get(label, ""))))
        widths[label] = w + 2

    header_line = "".join(label.ljust(widths.get(label, 10)) for label in labels)

    print(f"\n{'═' * 80}")
    print(f"  SUMMARY — accuracy + performance per modello")
    print(f"{'═' * 80}")
    print(f"  {header_line}")
    print(f"  {'─' * len(header_line)}")

    prev_group = None
    for r in rows:
        if extra_cols:
            cur_group = tuple(str(r.get(k, "")) for k in extra_cols)
            if prev_group is not None and cur_group != prev_group:
                print(f"  {'─' * len(header_line)}")
            prev_group = cur_group
        line = "".join(str(r.get(label, "—")).ljust(widths.get(label, 10)) for label in labels)
        print(f"  {line}")

    has_low_n = any("⚠" in str(r.get("Cat N", "")) for r in rows)
    print(f"\n  Clf = classifier  |  Cat = categorizer  |  Fb = fallback  |  ±N = CI 95%  |  trimmed p5-p95")
    if has_low_n:
        print(f"  ⚠ = N < {MIN_N_SIGNIFICANT} — non statisticamente significativo")
    print()


if __name__ == "__main__":
    main()
