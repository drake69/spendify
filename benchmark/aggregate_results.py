#!/usr/bin/env python3
"""aggregate_results.py — Benchmark results aggregator + predictive model.

Legge tutti i CSV in benchmark/results/ (o un file specificato) e produce:
  1. Tabella aggregata per modello/macchina: mean, median, std delle metriche chiave
  2. Modello regressivo per stimare duration_seconds a partire da caratteristiche HW/modello
  3. Report testuale con coefficienti, R² e CI 95%

Usage:
    python benchmark/aggregate_results.py                     # legge tutti i CSV in results/
    python benchmark/aggregate_results.py --csv FILE.csv      # usa un CSV specifico
    python benchmark/aggregate_results.py --output report.txt # salva report su file
    python benchmark/aggregate_results.py --predict           # stampa anche previsioni
    python benchmark/aggregate_results.py --type classifier   # solo classifier o categorizer
"""
from __future__ import annotations

import argparse
import math
import sys
import warnings
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# ── Paths ─────────────────────────────────────────────────────────────────
_BENCHMARK_DIR = Path(__file__).resolve().parent          # benchmark/
_PROJECT_ROOT  = _BENCHMARK_DIR.parent                    # sw_artifacts/
_RESULTS_DIR   = _BENCHMARK_DIR / "results"               # benchmark/results/
# Primary: dentro sw_artifacts → tracciato da git, verificabile da verify_bench_csv.py
_BENCH_OUT     = _BENCHMARK_DIR / "results_all_runs.csv"
# Mirror: repo documents → consultabile senza aprire il repo codice
_DOCS_OUT      = (_PROJECT_ROOT.parent                    # Spendify/
                  / "documents"
                  / "04_software_engineering"
                  / "benchmark"
                  / "results_all_runs.csv")

# ── Quantization ordinal mapping ──────────────────────────────────────────
_QUANT_BITS: dict[str, float] = {
    "q2_k": 2.5, "q3_k": 3.0, "q3_k_s": 3.0, "q3_k_m": 3.0, "q3_k_l": 3.0,
    "q4_0": 4.0, "q4_1": 4.0, "q4_k": 4.0, "q4_k_s": 4.0, "q4_k_m": 4.0,
    "q5_0": 5.0, "q5_1": 5.0, "q5_k": 5.0, "q5_k_s": 5.0, "q5_k_m": 5.0,
    "q6_k": 6.0, "q8_0": 8.0, "q8_k": 8.0,
    "f16": 16.0, "fp16": 16.0, "f32": 32.0, "fp32": 32.0,
    "": 4.0,  # default
}


def _quant_bits(q: str) -> float:
    """Converte stringa quantizzazione in bits (ordinale per regressione)."""
    return _QUANT_BITS.get(str(q).lower().strip(), 4.0)


def _safe_float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def _is_apple_silicon(cpu_str: str) -> int:
    return 1 if "apple" in str(cpu_str).lower() else 0


# ── Data loading ─────────────────────────────────────────────────────────

_SKIP_CSV_NAMES = frozenset({
    "results_all_runs.csv",
    "results_merged.csv",
    "cat_results_all_runs.csv",
    "cat_results_detail.csv",
    "summary_variance.csv",
    "summary_global.csv",
    "benchmark_config.json",
    "cat_benchmark_config.json",
})


def _find_csvs(csv_override: Optional[str] = None) -> list[Path]:
    """Ritorna lista di CSV da usare.

    Esclude sempre i file aggregati/summary (results_all_runs.csv,
    results_merged.csv, ecc.) per evitare double-counting.
    Legge i file archivio per-run: <timestamp>_<hostname>.csv
    e results_run_*.csv scritti dai benchmark scripts.
    """
    if csv_override:
        p = Path(csv_override)
        if not p.exists():
            print(f"ERROR: file non trovato: {p}", file=sys.stderr)
            sys.exit(1)
        return [p]

    archive_csvs: list[Path] = [
        p for p in sorted(_RESULTS_DIR.glob("*.csv"))
        if p.name not in _SKIP_CSV_NAMES
    ] if _RESULTS_DIR.exists() else []

    if archive_csvs:
        return archive_csvs

    print("ERROR: nessun CSV trovato. Esegui prima un benchmark.", file=sys.stderr)
    sys.exit(1)


def _load_all(paths: list[Path]) -> pd.DataFrame:
    """Carica e concatena tutti i CSV."""
    frames: list[pd.DataFrame] = []
    for p in paths:
        try:
            df = pd.read_csv(p, low_memory=False)
            df["_source_file"] = p.name
            frames.append(df)
        except Exception as e:
            print(f"WARN: impossibile leggere {p.name}: {e}", file=sys.stderr)
    if not frames:
        print("ERROR: nessun CSV leggibile.", file=sys.stderr)
        sys.exit(1)
    return pd.concat(frames, ignore_index=True)


def _preprocess(df: pd.DataFrame) -> pd.DataFrame:
    """Pulizia + colonne derivate."""
    # Normalizza colonne mancanti
    for col in ["benchmark_type", "provider", "model", "quantization",
                "n_gpu_layers", "runtime_ram_gb", "runtime_gpu_ram_gb",
                "n_threads", "runtime_cpu", "runtime_hostname",
                "duration_seconds", "n_transactions",
                "tokens_per_second", "parameter_size",
                "cat_fuzzy_accuracy", "parse_rate", "amount_accuracy",
                "cat_exact_accuracy", "doc_type_match", "error"]:
        if col not in df.columns:
            df[col] = ""

    df["duration_seconds"] = pd.to_numeric(df["duration_seconds"], errors="coerce")
    df["n_transactions"] = pd.to_numeric(df["n_transactions"], errors="coerce").fillna(0)
    df["parameter_size"] = pd.to_numeric(df["parameter_size"], errors="coerce").fillna(0)
    df["n_gpu_layers"] = pd.to_numeric(df["n_gpu_layers"], errors="coerce").fillna(0)
    df["runtime_ram_gb"] = pd.to_numeric(df["runtime_ram_gb"], errors="coerce").fillna(0)
    df["runtime_gpu_ram_gb"] = pd.to_numeric(df["runtime_gpu_ram_gb"], errors="coerce").fillna(0)
    df["n_threads"] = pd.to_numeric(df["n_threads"], errors="coerce").fillna(0)
    df["tokens_per_second"] = pd.to_numeric(df["tokens_per_second"], errors="coerce")

    # Colonne derivate
    df["quant_bits"] = df["quantization"].apply(_quant_bits)
    df["gpu_offload"] = (df["n_gpu_layers"] > 0).astype(int)
    df["is_apple_silicon"] = df["runtime_cpu"].apply(_is_apple_silicon)

    # Per categorizer: duration per 10 transazioni
    mask_cat = (df["benchmark_type"] == "categorizer") & (df["n_transactions"] > 0)
    df["duration_per_10tx"] = np.nan
    df.loc[mask_cat, "duration_per_10tx"] = (
        df.loc[mask_cat, "duration_seconds"] / df.loc[mask_cat, "n_transactions"] * 10
    )

    # Rimuovi righe con errore o durata nulla
    df = df[df["error"].isna() | (df["error"] == "")]
    df = df[df["duration_seconds"] > 0]

    return df.reset_index(drop=True)


# ── Merge classifier + categorizer rows ─────────────────────────────────

# Join key: identifica univocamente un (file × run × modello × macchina)
_MERGE_KEY = [
    "run_id", "filename", "git_commit", "git_branch",
    "provider", "model", "runtime_hostname",
]

# Colonne che appartengono solo al classifier (vuote nelle righe categorizer)
_CLF_ONLY_COLS = [
    "header_detected", "header_expected", "header_match",
    "rows_detected", "rows_expected", "rows_match",
    "doc_type_detected", "doc_type_expected", "doc_type_match",
    "convention_detected", "convention_expected", "convention_match",
    "confidence_score",
    "n_parsed", "n_expected", "parse_rate",
    "amount_correct", "amount_total", "amount_accuracy",
    "date_correct", "date_total", "date_accuracy",
    "category_correct", "category_total", "category_accuracy",
    "phase0_sign_convention", "phase0_debit_col", "phase0_credit_col",
    "llm_debit_col", "llm_credit_col", "llm_invert_sign",
    "final_debit_col", "final_credit_col", "final_invert_sign",
    "classifier_mode",
    "step1_time_s", "step2_time_s", "step3_time_s",
    "step1_doc_type_match", "step2_date_col_match", "step2_amount_col_match",
    "classifier_duration_s",
]

# Colonne che appartengono solo al categorizer
_CAT_ONLY_COLS = [
    "n_transactions", "n_categorized",
    "n_correct_category", "n_correct_fuzzy",
    "n_fallback", "n_history", "n_rule", "n_llm",
    "cat_exact_accuracy", "cat_fuzzy_accuracy", "cat_fallback_rate",
    "cleaner_batch_size",
    "cat_duration_s",
]

# Colonne comuni (prese dal classifier come fonte primaria; il categorizer può avere
# valori diversi per duration_seconds / tokens — vengono renominate con suffisso)
_SHARED_COLS = [
    "version", "git_branch", "temperature", "parameter_size", "quantization",
    "n_ctx", "n_batch", "n_threads", "n_gpu_layers", "flash_attn",
    "runtime_os", "runtime_cpu", "runtime_ram_gb", "runtime_gpu",
    "runtime_gpu_cores", "runtime_gpu_ram_gb",
    "file_doc_type", "file_format", "file_amount_format",
    "file_n_header_rows", "file_n_data_rows", "file_n_footer_rows",
    "file_has_debit_credit_split", "file_has_borders",
    "file_n_income_rows", "file_n_expense_rows", "file_n_internal_transfers",
]


def merge_clf_cat(df: pd.DataFrame) -> pd.DataFrame:
    """Produce una riga per chiave (run_id, filename, commit, model, host)
    con colonne classifier + categorizer affiancate.

    - Le colonne comuni vengono prese dalla riga classifier (source of truth).
    - `duration_seconds` viene rinominato: classifier → `classifier_duration_s`,
      categorizer → `cat_duration_s`.
    - `prompt_tokens`, `completion_tokens`, `total_tokens`, `tokens_per_second`
      vengono suffissati: `_clf` e `_cat`.
    - Righe senza controparte (solo classifier o solo categorizer) vengono
      incluse con le colonne mancanti a NaN.
    """
    key_cols = [c for c in _MERGE_KEY if c in df.columns]

    clf = df[df["benchmark_type"] == "classifier"].copy()
    cat = df[df["benchmark_type"] == "categorizer"].copy()

    if clf.empty and cat.empty:
        return pd.DataFrame()

    # Rinomina duration e token per disambiguare
    for src, suffix in [(clf, "_clf"), (cat, "_cat")]:
        for col in ["duration_seconds", "prompt_tokens", "completion_tokens",
                    "total_tokens", "tokens_per_second", "cpu_load_avg",
                    "gpu_utilization_pct", "error"]:
            if col in src.columns:
                src.rename(columns={col: f"{col}{suffix}"}, inplace=True)

    # Drop benchmark_type prima del join
    clf = clf.drop(columns=["benchmark_type"], errors="ignore")
    cat = cat.drop(columns=["benchmark_type"], errors="ignore")

    # Outer join su key
    merged = pd.merge(clf, cat, on=key_cols, how="outer", suffixes=("_clf_dup", "_cat_dup"))

    # Risolvi colonne duplicate (_clf_dup vs _cat_dup): prendi il valore non-null
    dup_bases = set()
    for col in merged.columns:
        if col.endswith("_clf_dup") or col.endswith("_cat_dup"):
            dup_bases.add(col.replace("_clf_dup", "").replace("_cat_dup", ""))
    for base in dup_bases:
        col_a = f"{base}_clf_dup"
        col_b = f"{base}_cat_dup"
        if col_a in merged.columns and col_b in merged.columns:
            merged[base] = merged[col_a].combine_first(merged[col_b])
            merged.drop(columns=[col_a, col_b], inplace=True)
        elif col_a in merged.columns:
            merged.rename(columns={col_a: base}, inplace=True)
        elif col_b in merged.columns:
            merged.rename(columns={col_b: base}, inplace=True)

    # Backward compat: se i vecchi CSV non hanno classifier_duration_s,
    # usare duration_seconds_clf come fallback
    if "classifier_duration_s" not in merged.columns and "duration_seconds_clf" in merged.columns:
        merged["classifier_duration_s"] = merged["duration_seconds_clf"]
    if "cat_duration_s" not in merged.columns and "duration_seconds_cat" in merged.columns:
        merged["cat_duration_s"] = merged["duration_seconds_cat"]

    return merged.reset_index(drop=True)


# ── Aggregation ───────────────────────────────────────────────────────────

_AGG_METRICS_CLASSIFIER = [
    # prefer disambiguated column; fall back to duration_seconds if old CSV
    "classifier_duration_s", "duration_seconds",
    "tokens_per_second", "tokens_per_second_clf",
    "parse_rate", "amount_accuracy", "date_accuracy",
]
_AGG_METRICS_CATEGORIZER = [
    "cat_duration_s", "duration_seconds",
    "duration_per_10tx",
    "tokens_per_second", "tokens_per_second_cat",
    "cat_fuzzy_accuracy", "cat_exact_accuracy", "cat_fallback_rate",
]
_GROUP_COLS = ["benchmark_type", "provider", "model", "quantization",
               "runtime_hostname", "runtime_cpu"]


def _aggregate(df: pd.DataFrame, bench_type: str) -> pd.DataFrame:
    sub = df[df["benchmark_type"] == bench_type].copy()
    if sub.empty:
        return pd.DataFrame()

    metrics = _AGG_METRICS_CLASSIFIER if bench_type == "classifier" else _AGG_METRICS_CATEGORIZER
    # Mantieni solo metriche presenti nel dataframe
    metrics = [m for m in metrics if m in sub.columns]

    group_cols = [c for c in _GROUP_COLS if c in sub.columns]

    rows = []
    for key, grp in sub.groupby(group_cols, dropna=False):
        key_dict = dict(zip(group_cols, key if isinstance(key, tuple) else (key,)))
        key_dict["n_runs"] = len(grp)
        for m in metrics:
            col = grp[m].dropna()
            if col.empty:
                key_dict[f"{m}_mean"] = np.nan
                key_dict[f"{m}_median"] = np.nan
                key_dict[f"{m}_std"] = np.nan
            else:
                key_dict[f"{m}_mean"] = round(col.mean(), 4)
                key_dict[f"{m}_median"] = round(col.median(), 4)
                key_dict[f"{m}_std"] = round(col.std(), 4)
        rows.append(key_dict)

    return pd.DataFrame(rows)


# ── Regression model ──────────────────────────────────────────────────────

_FEATURE_COLS = ["parameter_size", "quant_bits", "gpu_offload",
                 "runtime_ram_gb", "runtime_gpu_ram_gb", "n_threads",
                 "is_apple_silicon"]
_FEATURE_LABELS = ["param_B", "quant_bits", "gpu_offload",
                   "cpu_ram_gb", "gpu_ram_gb", "n_threads",
                   "apple_silicon"]


def _regression(df: pd.DataFrame, target_col: str, bench_type: str) -> Optional[dict]:
    """OLS con CI 95% via statsmodels (o numpy fallback)."""
    sub = df[df["benchmark_type"] == bench_type].copy()
    sub = sub.dropna(subset=[target_col])

    feature_ok = [c for c in _FEATURE_COLS if c in sub.columns]
    if len(sub) < len(feature_ok) + 2:
        return None  # troppo pochi dati

    X = sub[feature_ok].fillna(0).values.astype(float)
    y = sub[target_col].values.astype(float)

    # Prova statsmodels (CI gratis)
    try:
        import statsmodels.api as sm  # type: ignore
        X_sm = sm.add_constant(X)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = sm.OLS(y, X_sm).fit()
        labels = ["intercept"] + [_FEATURE_LABELS[_FEATURE_COLS.index(c)] for c in feature_ok]
        return {
            "labels": labels,
            "coef": model.params,
            "ci_low": model.conf_int(alpha=0.05)[:, 0],
            "ci_high": model.conf_int(alpha=0.05)[:, 1],
            "p_values": model.pvalues,
            "r2": model.rsquared,
            "r2_adj": model.rsquared_adj,
            "n": int(model.nobs),
            "engine": "statsmodels",
        }
    except ImportError:
        pass

    # Fallback: numpy OLS manuale (usa pinv per robustezza su matrici rank-deficient)
    X_np = np.column_stack([np.ones(len(X)), X])
    try:
        coef, _, rank, _ = np.linalg.lstsq(X_np, y, rcond=None)
    except Exception:
        return None

    n, k = X_np.shape
    if n <= k:
        return None

    y_hat = X_np @ coef
    residuals = y - y_hat
    ss_res = float(np.sum(residuals ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    dof = n - rank  # gradi di libertà effettivi (rank <= k)
    if dof <= 0:
        return None
    mse = ss_res / dof

    # Pseudo-inversa per SE robusta (funziona anche su rank-deficient)
    XtXinv = np.linalg.pinv(X_np.T @ X_np)
    se_var = mse * np.diag(XtXinv)
    se = np.sqrt(np.clip(se_var, 0, None))

    # t critico 95%
    t_crit = 1.96
    try:
        from scipy import stats as _sp  # type: ignore
        t_crit = float(_sp.t.ppf(0.975, df=dof))
    except ImportError:
        pass

    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    labels = ["intercept"] + [_FEATURE_LABELS[_FEATURE_COLS.index(c)] for c in feature_ok]
    return {
        "labels": labels,
        "coef": coef,
        "ci_low": coef - t_crit * se,
        "ci_high": coef + t_crit * se,
        "p_values": None,
        "r2": r2,
        "r2_adj": 1 - (1 - r2) * (n - 1) / dof if ss_tot > 0 else float("nan"),
        "n": n,
        "engine": "numpy",
    }


# ── Predictions ───────────────────────────────────────────────────────────

def _predict_examples(reg: dict, feature_ok: list[str]) -> list[dict]:
    """Genera previsioni esemplificative per hardware tipici."""
    examples = [
        {"name": "Mac M3 Pro 36 GB, Q4_K_M 7B (offload)",
         "parameter_size": 7, "quant_bits": 4.0, "gpu_offload": 1,
         "runtime_ram_gb": 36, "runtime_gpu_ram_gb": 36, "n_threads": 8, "is_apple_silicon": 1},
        {"name": "Mac M2 16 GB, Q4_K_M 7B (offload)",
         "parameter_size": 7, "quant_bits": 4.0, "gpu_offload": 1,
         "runtime_ram_gb": 16, "runtime_gpu_ram_gb": 16, "n_threads": 8, "is_apple_silicon": 1},
        {"name": "Linux RTX 3090 24 GB, Q4_K_M 7B (GPU)",
         "parameter_size": 7, "quant_bits": 4.0, "gpu_offload": 1,
         "runtime_ram_gb": 32, "runtime_gpu_ram_gb": 24, "n_threads": 8, "is_apple_silicon": 0},
        {"name": "Linux CPU-only, Q4_K_M 7B",
         "parameter_size": 7, "quant_bits": 4.0, "gpu_offload": 0,
         "runtime_ram_gb": 32, "runtime_gpu_ram_gb": 0, "n_threads": 8, "is_apple_silicon": 0},
        {"name": "Mac M3 Pro 36 GB, Q8_0 13B (offload)",
         "parameter_size": 13, "quant_bits": 8.0, "gpu_offload": 1,
         "runtime_ram_gb": 36, "runtime_gpu_ram_gb": 36, "n_threads": 8, "is_apple_silicon": 1},
    ]
    coef = reg["coef"]  # includes intercept at index 0
    feature_indices = [_FEATURE_COLS.index(c) for c in feature_ok]

    results = []
    for ex in examples:
        x = [ex.get(c, 0) for c in feature_ok]
        predicted = coef[0] + sum(c * v for c, v in zip(coef[1:], x))
        results.append({"name": ex["name"], "predicted": max(0.0, predicted)})
    return results


# ── Formatting helpers ────────────────────────────────────────────────────

def _fmt_table(df: pd.DataFrame, max_col_width: int = 22) -> str:
    """Stampa DataFrame come tabella testuale."""
    if df.empty:
        return "  (nessun dato)\n"
    lines = []
    # Tronca nomi colonna
    cols = [str(c)[:max_col_width] for c in df.columns]
    # Larghezza colonne
    widths = [max(len(c), 6) for c in cols]
    for _, row in df.iterrows():
        for i, v in enumerate(row):
            widths[i] = max(widths[i], len(str(v)[:max_col_width]))
    # Header
    header = "  " + "  ".join(c.ljust(widths[i]) for i, c in enumerate(cols))
    lines.append(header)
    lines.append("  " + "  ".join("-" * w for w in widths))
    for _, row in df.iterrows():
        lines.append("  " + "  ".join(str(v)[:max_col_width].ljust(widths[i]) for i, v in enumerate(row)))
    return "\n".join(lines) + "\n"


def _fmt_regression(reg: dict, target_label: str, show_predictions: bool,
                    feature_ok: list[str]) -> str:
    lines = []
    lines.append(f"  Modello: OLS  |  Target: {target_label}  |  Engine: {reg['engine']}")
    lines.append(f"  R² = {reg['r2']:.4f}   R²_adj = {reg['r2_adj']:.4f}   N = {reg['n']}")
    lines.append("")
    # Coefficienti
    header = f"  {'Variabile':<20}  {'Coeff':>10}  {'CI 2.5%':>10}  {'CI 97.5%':>10}"
    if reg["p_values"] is not None:
        header += f"  {'p-value':>9}"
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))
    for i, lab in enumerate(reg["labels"]):
        row = f"  {lab:<20}  {reg['coef'][i]:>10.4f}  {reg['ci_low'][i]:>10.4f}  {reg['ci_high'][i]:>10.4f}"
        if reg["p_values"] is not None:
            pv = reg["p_values"][i]
            sig = "***" if pv < 0.001 else "** " if pv < 0.01 else "*  " if pv < 0.05 else "   "
            row += f"  {pv:>9.4f} {sig}"
        lines.append(row)

    if show_predictions:
        lines.append("")
        lines.append("  Previsioni esemplificative:")
        lines.append(f"  {'Configurazione':<55}  {'Stima':>8}")
        lines.append("  " + "-" * 65)
        for ex in _predict_examples(reg, feature_ok):
            lines.append(f"  {ex['name']:<55}  {ex['predicted']:>6.1f}s")
    return "\n".join(lines)


# ── Report generation ─────────────────────────────────────────────────────

def build_report(df: pd.DataFrame, bench_type_filter: Optional[str],
                 show_predictions: bool) -> str:
    parts: list[str] = []
    parts.append("=" * 70)
    parts.append("  Spendif.ai — Benchmark Aggregate Report")
    parts.append(f"  Sorgenti: {df['_source_file'].nunique()} CSV  |  Righe totali: {len(df)}")
    parts.append("=" * 70)

    bench_types = ["classifier", "categorizer"]
    if bench_type_filter:
        bench_types = [bench_type_filter]

    for bt in bench_types:
        sub = df[df["benchmark_type"] == bt]
        if sub.empty:
            continue

        parts.append("")
        parts.append(f"{'─'*70}")
        parts.append(f"  BENCHMARK TYPE: {bt.upper()}")
        parts.append(f"{'─'*70}")

        # ── Aggregated stats ──────────────────────────────────────────────
        agg = _aggregate(df, bt)
        if not agg.empty:
            parts.append("")
            parts.append("  Statistiche aggregate (per modello / macchina):")
            parts.append("")
            # Selezione colonne rilevanti per la stampa
            show_cols = [c for c in agg.columns if c not in ["runtime_cpu"]]
            parts.append(_fmt_table(agg[show_cols]))

        # ── Regression ────────────────────────────────────────────────────
        parts.append("")
        if bt == "classifier":
            target_col = "duration_seconds"
            target_label = "duration_seconds (s/file)"
        else:
            target_col = "duration_per_10tx"
            target_label = "duration_per_10tx (s/10 transazioni)"

        feature_ok = [c for c in _FEATURE_COLS if c in sub.columns and sub[c].notna().any()]
        reg = _regression(df, target_col, bt)

        parts.append(f"  Modello predittivo ({target_label}):")
        parts.append("")
        if reg is None:
            parts.append("  (dati insufficienti per la regressione — servono almeno 10 run su 2+ modelli)")
        else:
            parts.append(_fmt_regression(reg, target_label, show_predictions, feature_ok))

    # ── Riepilogo macchine viste ──────────────────────────────────────────
    parts.append("")
    parts.append(f"{'─'*70}")
    parts.append("  Macchine rilevate:")
    if "runtime_hostname" in df.columns:
        for host, grp in df.groupby("runtime_hostname"):
            os_val = grp["runtime_os"].iloc[0] if "runtime_os" in grp.columns else "?"
            cpu_val = grp["runtime_cpu"].iloc[0] if "runtime_cpu" in grp.columns else "?"
            ram_val = grp["runtime_ram_gb"].iloc[0] if "runtime_ram_gb" in grp.columns else "?"
            gpu_ram = grp["runtime_gpu_ram_gb"].iloc[0] if "runtime_gpu_ram_gb" in grp.columns else "?"
            parts.append(f"    {host}  |  {os_val}  |  {cpu_val}  |  RAM {ram_val}GB  |  GPU RAM {gpu_ram}GB")

    parts.append("")
    parts.append("=" * 70)
    return "\n".join(parts)


# ── CLI ───────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Aggrega e analizza i risultati del benchmark Spendif.ai"
    )
    parser.add_argument(
        "--csv", metavar="FILE",
        help="Usa questo CSV invece dei file in results/"
    )
    parser.add_argument(
        "--output", metavar="FILE",
        help="Salva il report su file (default: stampa su stdout)"
    )
    parser.add_argument(
        "--type", choices=["classifier", "categorizer"],
        dest="bench_type",
        help="Filtra per tipo benchmark (default: entrambi)"
    )
    parser.add_argument(
        "--predict", action="store_true",
        help="Mostra previsioni esemplificative dopo la regressione"
    )
    parser.add_argument(
        "--list", action="store_true",
        help="Elenca i CSV disponibili in results/ ed esci"
    )
    args = parser.parse_args()

    if args.list:
        if not _RESULTS_DIR.exists() or not list(_RESULTS_DIR.glob("*.csv")):
            print("Nessun CSV in results/. Esegui prima un benchmark.")
            sys.exit(0)
        print("CSV disponibili in results/:")
        for p in sorted(_RESULTS_DIR.glob("*.csv")):
            size_kb = p.stat().st_size // 1024
            print(f"  {p.name}  ({size_kb} KB)")
        sys.exit(0)

    paths = _find_csvs(args.csv)
    print(f"Caricamento {len(paths)} CSV...", file=sys.stderr)

    raw = _load_all(paths)
    df = _preprocess(raw)

    print(f"Righe valide: {len(df)}  |  Tipi: {df['benchmark_type'].value_counts().to_dict()}",
          file=sys.stderr)

    # ── Salva results_all_runs.csv (righe separate per tipo, cross-HW) ──────
    # Chiave di unicità: benchmark_type incluso — classifier e categorizer
    # restano su righe separate per compatibilità e analisi per-tipo.
    _DEDUP_KEYS = [
        "version", "runtime_hostname", "benchmark_type",
        "provider", "model", "quantization", "run_id", "filename",
    ]
    _MERGED_OUT = _BENCH_OUT.parent / "results_merged.csv"
    _DOCS_MERGED_OUT = _DOCS_OUT.parent / "results_merged.csv"
    try:
        # Legge l'esistente da _BENCH_OUT (primario in sw_artifacts) se presente
        _ref = _BENCH_OUT if _BENCH_OUT.exists() else _DOCS_OUT
        if _ref.exists():
            existing = pd.read_csv(_ref, low_memory=False)
            all_runs = pd.concat([existing, raw], ignore_index=True)
        else:
            all_runs = raw.copy()
        # Deduplica: se stessa chiave compare più volte, tieni l'ultima
        dedup_cols = [c for c in _DEDUP_KEYS if c in all_runs.columns]
        if dedup_cols:
            all_runs = all_runs.drop_duplicates(subset=dedup_cols, keep="last")
        all_runs = all_runs.sort_values(
            [c for c in ["version", "runtime_hostname", "run_id"] if c in all_runs.columns]
        ).reset_index(drop=True)
        # Scrivi primario
        _BENCH_OUT.parent.mkdir(parents=True, exist_ok=True)
        all_runs.to_csv(_BENCH_OUT, index=False)
        print(f"CSV aggregato   → {_BENCH_OUT}  ({len(all_runs)} righe, {len(raw)} nuove)")
        # Mirror in documents/
        try:
            _DOCS_OUT.parent.mkdir(parents=True, exist_ok=True)
            all_runs.to_csv(_DOCS_OUT, index=False)
            print(f"                → {_DOCS_OUT}  (mirror)")
        except Exception as e_docs:
            print(f"WARN: impossibile scrivere mirror documents/: {e_docs}", file=sys.stderr)
    except Exception as e:
        print(f"WARN: impossibile scrivere {_BENCH_OUT}: {e}", file=sys.stderr)
        all_runs = raw.copy()

    # ── Salva results_merged.csv (una riga per chiave, clf+cat affiancati) ──
    # Questo è il file da usare per analisi che correlano classifier e categorizer.
    try:
        merged_view = merge_clf_cat(all_runs)
        if not merged_view.empty:
            merged_view.to_csv(_MERGED_OUT, index=False)
            print(f"CSV merged      → {_MERGED_OUT}  ({len(merged_view)} righe)")
            try:
                _DOCS_MERGED_OUT.parent.mkdir(parents=True, exist_ok=True)
                merged_view.to_csv(_DOCS_MERGED_OUT, index=False)
                print(f"                → {_DOCS_MERGED_OUT}  (mirror)")
            except Exception as e_docs:
                print(f"WARN: impossibile scrivere mirror merged: {e_docs}", file=sys.stderr)
        else:
            print("WARN: merged view vuota — nessun CSV merged scritto.", file=sys.stderr)
    except Exception as e:
        print(f"WARN: impossibile produrre results_merged.csv: {e}", file=sys.stderr)

    report = build_report(df, args.bench_type, args.predict)

    if args.output:
        Path(args.output).write_text(report, encoding="utf-8")
        print(f"Report salvato in: {args.output}")
    else:
        print(report)


if __name__ == "__main__":
    main()
