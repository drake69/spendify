"""
Debug pipeline runner — completely independent from the Streamlit GUI.

Usage:
    uv run python tools/debug_pipeline.py <file> [options]

Examples:
    uv run python tools/debug_pipeline.py estratto.csv
    uv run python tools/debug_pipeline.py estratto.csv --backend openai
    uv run python tools/debug_pipeline.py estratto.csv --step classify
    uv run python tools/debug_pipeline.py estratto.csv --step full --giroconto exclude

Steps:
    load      Load file and print raw columns + first rows (no LLM)
    classify  Run Flow 2 document classifier only, print resulting schema
    full      Run the full pipeline (classify + normalize + categorize)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Ensure project root is on PYTHONPATH when run from any directory
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

import pandas as pd

from core.categorizer import TaxonomyConfig
from core.classifier import classify_document
from core.models import GirocontoMode
from core.orchestrator import ProcessingConfig, load_raw_dataframe, process_file
from core.sanitizer import SanitizationConfig
from core.llm_backends import BackendFactory, OllamaBackend
from support.logging import setup_logging

logger = setup_logging()

TAXONOMY_PATH = os.getenv("TAXONOMY_PATH", str(_ROOT / "taxonomy.yaml"))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_taxonomy() -> TaxonomyConfig:
    p = Path(TAXONOMY_PATH)
    if p.exists():
        return TaxonomyConfig.from_yaml(str(p))
    return TaxonomyConfig(
        expenses={"Altro": ["Spese non classificate"]},
        income={"Altro entrate": ["Entrate non classificate"]},
    )


def _build_config(args: argparse.Namespace) -> ProcessingConfig:
    owner_names = [n.strip() for n in os.getenv("OWNER_NAMES", "").split(",") if n.strip()]
    return ProcessingConfig(
        llm_backend=args.backend,
        giroconto_mode=GirocontoMode(args.giroconto),
        sanitize_config=SanitizationConfig(owner_names=owner_names),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        claude_model=os.getenv("CLAUDE_MODEL", "claude-3-5-haiku-20241022"),
        ollama_model=os.getenv("OLLAMA_MODEL", "gemma3:12b"),
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
    )


def _hr(title: str = "") -> None:
    width = 72
    if title:
        pad = (width - len(title) - 2) // 2
        print(f"\n{'─' * pad} {title} {'─' * (width - pad - len(title) - 2)}")
    else:
        print("─" * width)


def _print_json(data: dict | list) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


# ── Steps ─────────────────────────────────────────────────────────────────────

def step_load(raw_bytes: bytes, filename: str) -> pd.DataFrame:
    _hr("LOAD")
    df, encoding, info = load_raw_dataframe(raw_bytes, filename)
    print(f"File       : {filename}")
    print(f"Encoding   : {encoding}")
    print(f"Shape      : {df.shape[0]} rows × {df.shape[1]} columns")
    print(f"Columns    : {df.columns.tolist()}")
    if info.skipped_rows:
        print(f"Pre-header rows stripped: {info.skipped_rows}")
    if info.dropped_columns:
        print(f"Low-variability columns dropped: {info.dropped_columns}")
    _hr("First 5 rows")
    print(df.head(5).to_string(index=False))
    return df


def step_classify(df: pd.DataFrame, filename: str, config: ProcessingConfig) -> None:
    _hr("CLASSIFY (Flow 2)")
    backend = BackendFactory.create(config.llm_backend, **_backend_kwargs(config))
    fallback = _get_fallback(config)

    schema = classify_document(
        df_raw=df,
        llm_backend=backend,
        source_name=filename,
        sanitize=True,
        sanitize_config=config.sanitize_config,
        fallback_backend=fallback,
    )

    if schema is None:
        print("❌  Classification returned None (check logs above for details)")
        return

    _hr("DocumentSchema")
    _print_json(schema.model_dump())
    _hr()
    print(f"doc_type        : {schema.doc_type}")
    print(f"confidence      : {schema.confidence}")
    print(f"date_col        : {schema.date_col!r}")
    print(f"date_format     : {schema.date_format!r}")
    print(f"amount_col      : {schema.amount_col!r}")
    print(f"debit_col       : {schema.debit_col!r}")
    print(f"credit_col      : {schema.credit_col!r}")
    print(f"description_col : {schema.description_col!r}")
    print(f"sign_convention : {schema.sign_convention}")
    print(f"account_label   : {schema.account_label!r}")


def step_full(raw_bytes: bytes, filename: str, config: ProcessingConfig) -> None:
    _hr("FULL PIPELINE")
    taxonomy = _load_taxonomy()

    result = process_file(
        raw_bytes=raw_bytes,
        filename=filename,
        config=config,
        taxonomy=taxonomy,
        user_rules=[],
        known_schema=None,
    )

    _hr("ImportResult summary")
    print(f"source_name      : {result.source_name}")
    print(f"flow_used        : {result.flow_used}")
    print(f"batch_sha256     : {result.batch_sha256[:16]}…")
    print(f"transactions     : {len(result.transactions)}")
    print(f"reconciliations  : {len(result.reconciliations)}")
    print(f"transfer_links   : {len(result.transfer_links)}")
    print(f"errors           : {result.errors or '—'}")

    if result.doc_schema:
        _hr("DocumentSchema used")
        print(f"  doc_type        : {result.doc_schema.doc_type}")
        print(f"  confidence      : {result.doc_schema.confidence}")
        print(f"  date_col        : {result.doc_schema.date_col!r}")
        print(f"  amount_col      : {result.doc_schema.amount_col!r}")
        print(f"  description_col : {result.doc_schema.description_col!r}")
        print(f"  sign_convention : {result.doc_schema.sign_convention}")

    if result.transactions:
        _hr(f"Transactions ({len(result.transactions)} total)")
        for tx in result.transactions[:20]:
            cat = f"{tx.get('category','?')} / {tx.get('subcategory','?')}"
            review = " ⚠ review" if tx.get("to_review") else ""
            print(
                f"  {str(tx.get('date','')):<12} "
                f"{str(tx.get('amount','')):<12} "
                f"{(tx.get('description') or '')[:40]:<42} "
                f"[{tx.get('tx_type','?')}] "
                f"{cat}{review}"
            )
        if len(result.transactions) > 20:
            print(f"  … and {len(result.transactions) - 20} more")

    if result.errors:
        _hr("Errors")
        for e in result.errors:
            print(f"  ✗ {e}")


# ── Backend helpers ───────────────────────────────────────────────────────────

def _backend_kwargs(config: ProcessingConfig) -> dict:
    kwargs: dict = {"timeout": config.llm_timeout_s}
    if config.llm_backend == "local_ollama":
        kwargs["base_url"] = config.ollama_base_url
        kwargs["model"] = config.ollama_model
    elif config.llm_backend == "openai":
        kwargs["model"] = config.openai_model
    elif config.llm_backend == "claude":
        kwargs["model"] = config.claude_model
    elif config.llm_backend == "local_llama_cpp":
        kwargs.pop("timeout", None)
        if config.llama_cpp_model_path:
            kwargs["model_path"] = config.llama_cpp_model_path
        kwargs["n_gpu_layers"] = config.llama_cpp_n_gpu_layers
    return kwargs


def _get_fallback(config: ProcessingConfig) -> OllamaBackend | None:
    b = OllamaBackend(base_url=config.ollama_base_url, model=config.ollama_model)
    return b if b.is_available() else None


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Spendify debug pipeline runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("file", help="Path to CSV or XLSX file to process")
    parser.add_argument(
        "--step",
        choices=["load", "classify", "full"],
        default="full",
        help="Pipeline step to run (default: full)",
    )
    parser.add_argument(
        "--backend",
        choices=["local_llama_cpp", "local_ollama", "openai", "claude"],
        default=os.getenv("LLM_BACKEND", "local_llama_cpp"),
        help="LLM backend to use (default: $LLM_BACKEND or local_llama_cpp)",
    )
    parser.add_argument(
        "--giroconto",
        choices=["neutral", "exclude", "highlight"],
        default="neutral",
        help="Giroconto mode (default: neutral)",
    )
    args = parser.parse_args()

    file_path = Path(args.file)
    if not file_path.exists():
        sys.exit(f"File not found: {file_path}")

    raw_bytes = file_path.read_bytes()
    filename = file_path.name
    config = _build_config(args)

    print(f"\nSpendify debug pipeline")
    print(f"  file    : {file_path.resolve()}")
    print(f"  step    : {args.step}")
    print(f"  backend : {args.backend}")

    if args.step == "load":
        step_load(raw_bytes, filename)

    elif args.step == "classify":
        df = step_load(raw_bytes, filename)
        step_classify(df, filename, config)

    elif args.step == "full":
        step_load(raw_bytes, filename)
        df, _, _info = load_raw_dataframe(raw_bytes, filename)
        step_classify(df, filename, config)
        step_full(raw_bytes, filename, config)

    print()


if __name__ == "__main__":
    main()
