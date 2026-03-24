"""
Test end-to-end della pipeline di import CON LLM (Ollama locale).

- Genera file sintetici su disco in tests/generated_files/
- Salva un manifest CSV con i metadati di ogni file
- Esegue process_file() con LLM reale (Ollama) — NO DB
- Scrive i risultati in tests/generated_files/results.csv
- Stampa un report KPI finale

Famiglia fittizia (nomi usati per giroconti):
  Marco Ferri (45) — libero professionista
  Laura Gentili (43) — dipendente pubblica (moglie)
  Tommaso Ferri (7) — figlio
  Sofia Ferri (22) — figlia universitaria
  Nonna Rosa Gentili (74) — suocera pensionata

Eseguire con:
    uv run pytest tests/test_llm_import.py -v -s --tb=short -x
"""
from __future__ import annotations

import csv
import io
import json
import os
import random
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from core.categorizer import CategoryRule, TaxonomyConfig
from core.models import Confidence, DocumentType, GirocontoMode, SignConvention
from core.orchestrator import ProcessingConfig, process_file
from core.sanitizer import SanitizationConfig
from core.schemas import DocumentSchema

# ── Paths ──────────────────────────────────────────────────────────────────
_HERE = Path(__file__).parent
_OUTPUT_DIR = _HERE / "generated_files"
_MANIFEST_PATH = _OUTPUT_DIR / "manifest.csv"
_RESULTS_PATH = _OUTPUT_DIR / "results.csv"
_TAXONOMY_PATH = _HERE.parent / "taxonomy.yaml"

# ── Seed ───────────────────────────────────────────────────────────────────
random.seed(42)

# ── Family names (for giroconti detection) ─────────────────────────────────
FAMILY = {
    "padre": "Marco Ferri",
    "madre": "Laura Gentili",
    "figlio": "Tommaso Ferri",
    "figlia": "Sofia Ferri",
    "nonna": "Rosa Gentili",
}
OWNER_NAMES = list(FAMILY.values())

# ── Realistic Italian bank descriptions ────────────────────────────────────
_EXPENSES_CC = [
    "Pagam. POS - PAGAMENTO POS 42,60 EUR DEL 15.01.2026 A (ITA) ESSELUNGA MILANO VIA RIPAMONTI",
    "Pagam. POS - PAGAMENTO POS 18,50 EUR DEL 16.01.2026 A (ITA) BAR CENTRALE PIAZZA DUOMO",
    "Addebito SDD - ENEL SERVIZIO ELETTRICO NAZ. CL.IT98765",
    "Addebito SDD - TELECOM ITALIA S.P.A. FIBRA",
    "Addebito SDD - Wind Tre S.p.A. CL.4521879",
    "Disposizione - RIF:212524764 BEN. CONDOMINIO VIA ROMA 15 MILANO",
    "Pagam. POS - PAGAMENTO POS 77,00 EUR DEL 20.01.2026 A (ITA) FARMACIA DR ROSSI",
    "Addebito SDD - BANCA POPOLARE MUTUO FONDIARIO RAT.240",
    "Pagam. POS - PAGAMENTO POS 3,20 EUR DEL 21.01.2026 A (ITA) PANIFICIO TOSI",
    "Pagam. POS - PAGAMENTO POS 55,50 EUR DEL 22.01.2026 A (ITA) DECATHLON ASSAGO",
    "Addebito SDD - SARA ASSICURAZIONI RCA AUTO TG.FH123AB",
    "Pagam. POS - PAGAMENTO POS 9,90 EUR A (ITA) NETFLIX.COM",
    "PAGAMENTO CONTACTLESS CARTA **** 4521 CONAD CITY MILANO",
    "PRELIEVO ATM BANCOMAT FIL. 00456 MILANO LORETO",
    "Pagam. POS - PAGAMENTO POS 36,00 EUR A (ITA) RISTORANTE DA GINO",
    "Addebito SDD - HERA COMM S.R.L. ACQUA POTABILE",
    "Pagam. POS - PAGAMENTO POS 120,00 EUR A (ITA) STUDIO DENTISTICO DR VERDI",
    "Pagam. POS - PAGAMENTO POS 14,99 EUR A (ITA) SPOTIFY AB",
    "Disposizione - RIF:213003169 BEN. UNIVERSITA DEGLI STUDI DI PADOVA TASSE",
    "Pagam. POS - PAGAMENTO POS 50,00 EUR A (ITA) ZARA ITALIA SRL",
    "Pagam. POS - PAGAMENTO POS 1,20 EUR A (ITA) DISTRIBUTORE ENI Q8",
    "Pagam. POS - PAGAMENTO POS 65,00 EUR A (ITA) MEDIAWORLD CARUGATE",
    "PAGAMENTO CONTACTLESS CARTA **** 4521 LIDL ITALIA VIMODRONE",
    "Pagam. POS - PAGAMENTO POS 23,40 EUR A (ITA) TRENITALIA S.P.A.",
    "Addebito SDD - DELIVEROO ITALY SRL ORD.987654",
    "Pagam. POS - PAGAMENTO POS 8,50 EUR A (ITA) MACELLERIA BONI",
    "Addebito SDD - TARI COMUNE DI MILANO 2026",
    "Pagam. POS - PAGAMENTO POS 650,00 EUR A (ITA) OTTICA SALMOIRAGHI",
    "Pagam. POS - PAGAMENTO POS 45,00 EUR A (ITA) CENTRO DIAGNOSTICO LIFE",
    "Addebito SDD - ACI BOLLO AUTO TG.FH123AB",
]

_EXPENSES_CARD = [
    "AMAZON.IT MARKETPLACE ORDINE 402-1234567-8901234",
    "ESSELUNGA S.P.A. MILANO VIA RIPAMONTI 42",
    "AUTOSTRADA TELEPASS SPA PEDAGGIO A1 MI-BO",
    "BOOKING.COM BV HOTEL SEASIDE RIMINI 3 NOTTI",
    "RYANAIR LTD VOLO FR1234 MXP-FCO 2 PAX",
    "JUST EAT ITALY SRL ORDINE #987654",
    "GOOGLE *CLOUD STORAGE ABBONAMENTO MENSILE",
    "APPLE.COM/BILL ITUNES STORE",
    "ENI STATION 45678 CARBURANTE SELF 50L",
    "H&M HENNES & MAURITZ MILANO DUOMO",
    "McDONALD'S RISTORANTE 12345 HAPPY MEAL",
    "IKEA ITALIA RETAIL CARUGATE MI",
]

_INCOMES = [
    "ACCREDITO STIPENDIO MESE DI GENNAIO 2026 MINISTERO ISTRUZIONE",
    "Bonif. v/fav. - RIF:211072327 ORD. REGIONE LOMBARDIA RIMBORSO",
    "Bonif. v/fav. - RIF:213003169 ORD. STUDIO TECNICO ALFA SRL SALDO FATT.28/2025",
    "Bonif. v/fav. - RIF:210959215 ORD. BETA CONSULTING SPA ACCONTO PROGETTO",
    "ACCREDITO PENSIONE INPS MESE DI GENNAIO 2026",
    "Bonif. v/fav. - RIF:210567653 ORD. AGENZIA ENTRATE RIMBORSO 730",
]

# ── Giroconto descriptions (transfers between family members/accounts) ─────
def _giroconto_descriptions() -> list[tuple[str, str]]:
    """Return (description, direction) pairs for internal transfers."""
    return [
        # Marco ricarica carta Sofia
        (f"Disposizione - RIF:214001234 BEN. {FAMILY['figlia']} RICARICA POSTEPAY", "out"),
        (f"Bonif. v/fav. - RIF:214001234 ORD. {FAMILY['padre']} RICARICA POSTEPAY", "in"),
        # Marco ricarica carta Tommaso (tramite Laura)
        (f"Disposizione - RIF:214005678 BEN. {FAMILY['madre']} RICARICA CARTA FIGLIO", "out"),
        (f"Bonif. v/fav. - RIF:214005678 ORD. {FAMILY['padre']}", "in"),
        # Nonna manda soldi ai nipoti
        (f"Disposizione - RIF:214009012 BEN. {FAMILY['figlia']} REGALO COMPLEANNO", "out"),
        (f"Bonif. v/fav. - RIF:214009012 ORD. {FAMILY['nonna']}", "in"),
        # Laura gira soldi a Marco per spese professionali
        (f"Disposizione - RIF:214003456 BEN. {FAMILY['padre']} ANTICIPO COMMERCIALISTA", "out"),
        (f"Bonif. v/fav. - RIF:214003456 ORD. {FAMILY['madre']}", "in"),
        # Marco manda soldi alla nonna per badante
        (f"Disposizione - RIF:214007890 BEN. {FAMILY['nonna']} CONTRIBUTO BADANTE", "out"),
        (f"Bonif. v/fav. - RIF:214007890 ORD. {FAMILY['padre']}", "in"),
        # Giroconto tra conti propri
        (f"GIROCONTO DA CC INTESA A CC SELLA ORD. {FAMILY['padre']}", "out"),
        (f"GIROCONTO DA CC SELLA ORD. {FAMILY['padre']}", "in"),
    ]


_GIROCONTI = _giroconto_descriptions()

# ── Preheader templates ────────────────────────────────────────────────────
def _make_preheader(n_rows: int, bank: str, n_cols: int) -> list[list[str]]:
    if n_rows == 0:
        return []
    rows = []
    rows.append([""] * n_cols)
    rows.append([bank, ""] + [""] * (n_cols - 2))
    if n_rows >= 3:
        rows.append(["Conto: IT60X0306901783100000012345", ""] + [""] * (n_cols - 2))
    if n_rows >= 4:
        rows.append(["Periodo: 01/01/2026 - 28/02/2026", ""] + [""] * (n_cols - 2))
    # Fill remaining with empty rows
    while len(rows) < n_rows:
        rows.append([""] * n_cols)
    return rows[:n_rows]


def _make_footer(n_rows: int, n_cols: int) -> list[list[str]]:
    if n_rows == 0:
        return []
    rows = []
    if n_rows >= 1:
        r = [""] * n_cols
        r[0] = "Saldo finale:"
        if n_cols > 1:
            r[1] = "12.345,67"
        rows.append(r)
    while len(rows) < n_rows:
        rows.append([""] * n_cols)
    return rows[:n_rows]


# ── File spec ──────────────────────────────────────────────────────────────
@dataclass
class FileSpec:
    filename: str
    doc_type: str
    account_id: str
    bank: str
    fmt: str
    separator: str
    n_header_rows: int
    n_data_rows: int
    n_footer_rows: int
    amount_format: str
    date_format: str
    columns: list[str]
    amount_col: str | None = None
    debit_col: str | None = None
    credit_col: str | None = None
    description_col: str = "Descrizione"
    date_col: str = "Data operazione"
    date_val_col: str | None = None
    invert_sign: bool = False
    currency_col: str | None = None
    n_giroconti: int = 0  # number of giroconto transactions to include


# ── Registry ───────────────────────────────────────────────────────────────
SYNTHETIC_FILES: list[FileSpec] = [
    # CC-1: Intesa Sanpaolo — signed_single, with giroconti
    FileSpec("CC1_Intesa_signed_M.csv", "bank_account", "CC-1", "Intesa Sanpaolo",
             "csv", ";", 3, 80, 2, "signed_single", "%d/%m/%Y",
             ["Data operazione", "Data valuta", "Descrizione", "Importo", "Divisa"],
             amount_col="Importo", date_val_col="Data valuta", currency_col="Divisa",
             n_giroconti=6),

    # CC-2: Banca Sella — debit_credit_split, with giroconti
    FileSpec("CC2_Sella_dc_split_M.xlsx", "bank_account", "CC-2", "Banca Sella",
             "xlsx", "", 5, 60, 0, "debit_credit_split", "%d/%m/%Y",
             ["Data registrazione", "Valuta", "Causale", "Dare", "Avere", "Divisa"],
             debit_col="Dare", credit_col="Avere", description_col="Causale",
             date_col="Data registrazione", date_val_col="Valuta", currency_col="Divisa",
             n_giroconti=4),

    # CC-3: Banco BPM (nonna) — debit_credit_signed, with giroconti
    FileSpec("CC3_BPM_nonna_M.xlsx", "bank_account", "CC-3", "Banco BPM",
             "xlsx", "", 8, 40, 2, "debit_credit_signed", "%d/%m/%Y",
             ["Data contabile", "Data valuta", "Tipologia", "Entrate", "Uscite", "Divisa"],
             debit_col="Uscite", credit_col="Entrate", description_col="Tipologia",
             date_col="Data contabile", date_val_col="Data valuta", currency_col="Divisa",
             n_giroconti=3),

    # CC-3 with 14 header (MedioBanca case)
    FileSpec("CC3_BPM_14hdr_S.xlsx", "bank_account", "CC-3", "Banco BPM",
             "xlsx", "", 14, 30, 0, "debit_credit_signed", "%d/%m/%Y",
             ["Data contabile", "Data valuta", "Tipologia", "Entrate", "Uscite", "Divisa"],
             debit_col="Uscite", credit_col="Entrate", description_col="Tipologia",
             date_col="Data contabile", date_val_col="Data valuta", currency_col="Divisa",
             n_giroconti=2),

    # CRED-1: Amex — positive_only
    FileSpec("CRED1_Amex_positive_M.csv", "credit_card", "CRED-1", "American Express",
             "csv", ";", 3, 50, 0, "positive_only", "%d/%m/%Y",
             ["Data", "Descrizione operazione", "Importo", "Valuta"],
             amount_col="Importo", description_col="Descrizione operazione",
             date_col="Data", currency_col="Valuta", invert_sign=True),

    # CRED-2: Nexi — signed_single
    FileSpec("CRED2_Nexi_signed_M.xlsx", "credit_card", "CRED-2", "Nexi",
             "xlsx", "", 5, 50, 2, "signed_single", "%d/%m/%Y",
             ["Data mov.", "Descrizione", "Movimento", "EUR"],
             amount_col="Movimento", description_col="Descrizione",
             date_col="Data mov.", currency_col="EUR"),

    # RIC-1: PostePay Sofia — signed_single, with giroconti (riceve ricariche)
    FileSpec("RIC1_PostePay_sofia_S.csv", "prepaid_card", "RIC-1", "PostePay Evolution",
             "csv", ";", 0, 30, 0, "signed_single", "%d/%m/%Y",
             ["Data", "Dettaglio", "Importo"],
             amount_col="Importo", description_col="Dettaglio", date_col="Data",
             n_giroconti=3),

    # RIC-2: Hype Marco — debit_credit_split
    FileSpec("RIC2_Hype_marco_S.csv", "prepaid_card", "RIC-2", "Hype",
             "csv", ";", 0, 30, 0, "debit_credit_split", "%d/%m/%Y",
             ["Data operazione", "Descrizione", "Addebiti", "Accrediti"],
             debit_col="Addebiti", credit_col="Accrediti",
             description_col="Descrizione", date_col="Data operazione"),

    # RISP-1: Conto Arancio — signed_single, mostly giroconti
    FileSpec("RISP1_Arancio_M.csv", "savings", "RISP-1", "Conto Arancio",
             "csv", ";", 3, 20, 2, "signed_single", "%d/%m/%Y",
             ["Data", "Causale", "Importo", "Divisa"],
             amount_col="Importo", description_col="Causale",
             date_col="Data", currency_col="Divisa",
             n_giroconti=8),

    # Large file: CC-1, 200 rows
    FileSpec("CC1_Intesa_signed_L.csv", "bank_account", "CC-1", "Intesa Sanpaolo",
             "csv", ";", 3, 200, 0, "signed_single", "%d/%m/%Y",
             ["Data operazione", "Data valuta", "Descrizione", "Importo", "Divisa"],
             amount_col="Importo", date_val_col="Data valuta", currency_col="Divisa",
             n_giroconti=10),
]


# ── Amount generator ───────────────────────────────────────────────────────
def _random_amount(is_expense: bool) -> Decimal:
    if is_expense:
        return -Decimal(str(round(random.uniform(0.50, 2000.00), 2)))
    return Decimal(str(round(random.uniform(100.00, 5000.00), 2)))


def _format_amount_it(amount: Decimal) -> str:
    sign = "-" if amount < 0 else ""
    abs_val = abs(amount)
    int_part = int(abs_val)
    dec_str = f"{abs_val - int_part:.2f}"[2:]
    int_str = f"{int_part:,}".replace(",", ".") if int_part >= 1000 else str(int_part)
    return f"{sign}{int_str},{dec_str}"


# ── File generator ─────────────────────────────────────────────────────────
def generate_file(spec: FileSpec) -> bytes:
    base_date = date(2026, 2, 28)
    n_cols = len(spec.columns)

    # Decide which rows are giroconti
    giro_indices = set()
    if spec.n_giroconti > 0:
        giro_positions = random.sample(range(spec.n_data_rows), min(spec.n_giroconti, spec.n_data_rows))
        giro_indices = set(giro_positions)

    giro_idx = 0
    data_rows = []
    income_every = max(5, spec.n_data_rows // 6)

    for i in range(spec.n_data_rows):
        tx_date = base_date - timedelta(days=i)
        val_date = tx_date - timedelta(days=random.randint(0, 3))

        # Decide transaction type
        if i in giro_indices:
            # Giroconto
            giro_desc, giro_dir = _GIROCONTI[giro_idx % len(_GIROCONTI)]
            giro_idx += 1
            if giro_dir == "out":
                amount = -Decimal(str(round(random.uniform(50, 1000), 2)))
            else:
                amount = Decimal(str(round(random.uniform(50, 1000), 2)))
            desc = giro_desc
        elif i % income_every == 0 and spec.amount_format != "positive_only":
            amount = _random_amount(is_expense=False)
            desc = random.choice(_INCOMES)
        else:
            amount = _random_amount(is_expense=True)
            if spec.doc_type in ("credit_card", "prepaid_card"):
                desc = random.choice(_EXPENSES_CARD)
            else:
                desc = random.choice(_EXPENSES_CC)

        row: dict[str, str] = {}
        row[spec.date_col] = tx_date.strftime(spec.date_format)
        if spec.date_val_col and spec.date_val_col in spec.columns:
            row[spec.date_val_col] = val_date.strftime(spec.date_format)
        row[spec.description_col] = desc

        if spec.amount_format == "signed_single":
            row[spec.amount_col] = _format_amount_it(amount)
        elif spec.amount_format == "positive_only":
            row[spec.amount_col] = _format_amount_it(abs(amount))
        elif spec.amount_format == "debit_credit_split":
            if amount < 0:
                row[spec.debit_col] = _format_amount_it(abs(amount))
                row[spec.credit_col] = ""
            else:
                row[spec.debit_col] = ""
                row[spec.credit_col] = _format_amount_it(amount)
        elif spec.amount_format == "debit_credit_signed":
            if amount < 0:
                row[spec.debit_col] = _format_amount_it(amount)
                row[spec.credit_col] = ""
            else:
                row[spec.debit_col] = ""
                row[spec.credit_col] = _format_amount_it(amount)

        if spec.currency_col and spec.currency_col in spec.columns:
            row[spec.currency_col] = "EUR"

        ordered = [row.get(col, "") for col in spec.columns]
        data_rows.append(ordered)

    # Assemble
    preheader = _make_preheader(spec.n_header_rows, spec.bank, n_cols)
    footer = _make_footer(spec.n_footer_rows, n_cols)
    all_rows = preheader + [spec.columns] + data_rows + footer

    if spec.fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter=spec.separator, quoting=csv.QUOTE_MINIMAL)
        for row in all_rows:
            writer.writerow(row)
        return buf.getvalue().encode("utf-8")
    else:
        df = pd.DataFrame(all_rows)
        buf = io.BytesIO()
        df.to_excel(buf, index=False, header=False, engine="openpyxl")
        return buf.getvalue()


# ── Schema from spec ───────────────────────────────────────────────────────
def _schema_from_spec(spec: FileSpec) -> DocumentSchema:
    if spec.amount_format in ("signed_single", "positive_only"):
        sign_conv = SignConvention.signed_single
    else:
        sign_conv = SignConvention.debit_positive
    return DocumentSchema(
        doc_type=DocumentType(spec.doc_type),
        date_col=spec.date_col,
        date_accounting_col=spec.date_val_col,
        amount_col=spec.amount_col,
        debit_col=spec.debit_col,
        credit_col=spec.credit_col,
        description_col=spec.description_col,
        sign_convention=sign_conv,
        date_format=spec.date_format,
        account_label=f"{spec.bank} {spec.account_id}",
        confidence=Confidence.high,
        confidence_score=1.0,
        invert_sign=spec.invert_sign,
        currency_col=spec.currency_col,
        default_currency="EUR",
        skip_rows=spec.n_header_rows,
        delimiter=spec.separator if spec.fmt == "csv" else None,
    )


# ── KPI result ─────────────────────────────────────────────────────────────
@dataclass
class LLMImportKPI:
    filename: str
    doc_type: str
    expected_rows: int
    expected_giroconti: int
    # Schema detection
    schema_detected: bool = False
    confidence_score: float = 0.0
    schema_doc_type: str = ""
    schema_sign_convention: str = ""
    header_expected: int = 0
    header_detected: int = 0
    # Normalization
    rows_parsed: int = 0
    rows_skipped: int = 0
    rows_merged: int = 0
    giroconti_detected: int = 0
    # Categorization
    categorized: int = 0
    to_review: int = 0
    # Timing
    elapsed_s: float = 0.0
    # Errors
    errors: str = ""

    @property
    def automation_score(self) -> float:
        if self.expected_rows == 0:
            return 0.0
        return (self.rows_parsed / self.expected_rows) * 100.0

    @property
    def categorization_rate(self) -> float:
        if self.rows_parsed == 0:
            return 0.0
        return (self.categorized / self.rows_parsed) * 100.0


# ── Fixtures ───────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def output_dir() -> Path:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return _OUTPUT_DIR


@pytest.fixture(scope="session")
def taxonomy() -> TaxonomyConfig:
    return TaxonomyConfig.from_yaml(str(_TAXONOMY_PATH))


@pytest.fixture(scope="session")
def config() -> ProcessingConfig:
    return ProcessingConfig(
        llm_backend="local_ollama",
        ollama_base_url="http://localhost:11434",
        ollama_model="gemma3:12b",
        giroconto_mode=GirocontoMode.neutral,
        use_owner_names_for_giroconto=True,
        sanitize_config=SanitizationConfig(
            owner_names=OWNER_NAMES,
            description_language="it",
        ),
        description_language="it",
        max_transaction_amount=1_000_000.0,
        batch_size_llm=1,
        llm_timeout_s=120,
    )


@pytest.fixture(scope="session")
def generated_files(output_dir: Path) -> dict[str, tuple[bytes, FileSpec]]:
    """Generate all synthetic files, save to disk, write manifest."""
    files: dict[str, tuple[bytes, FileSpec]] = {}

    manifest_rows = []
    for spec in SYNTHETIC_FILES:
        raw_bytes = generate_file(spec)
        files[spec.filename] = (raw_bytes, spec)

        # Save to disk
        filepath = output_dir / spec.filename
        filepath.write_bytes(raw_bytes)

        manifest_rows.append({
            "filename": spec.filename,
            "doc_type": spec.doc_type,
            "account_id": spec.account_id,
            "bank": spec.bank,
            "format": spec.fmt,
            "separator": spec.separator,
            "n_rows_total": spec.n_header_rows + spec.n_data_rows + spec.n_footer_rows + 1,
            "n_header_rows": spec.n_header_rows,
            "n_data_rows": spec.n_data_rows,
            "n_footer_rows": spec.n_footer_rows,
            "n_giroconti": spec.n_giroconti,
            "amount_format": spec.amount_format,
            "columns": "|".join(spec.columns),
        })

    # Write manifest
    with open(_MANIFEST_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=manifest_rows[0].keys())
        writer.writeheader()
        writer.writerows(manifest_rows)

    print(f"\n📁 Generated {len(files)} files in {output_dir}")
    print(f"📋 Manifest: {_MANIFEST_PATH}")
    return files


# ── Collector for results ──────────────────────────────────────────────────
_kpi_results: list[LLMImportKPI] = []


# ── Parametrized test ──────────────────────────────────────────────────────
@pytest.mark.parametrize("spec", SYNTHETIC_FILES, ids=lambda s: s.filename)
def test_llm_import(
    spec: FileSpec,
    generated_files: dict,
    taxonomy: TaxonomyConfig,
    config: ProcessingConfig,
):
    """Run full import pipeline with LLM on a synthetic file."""
    raw_bytes, _ = generated_files[spec.filename]

    kpi = LLMImportKPI(
        filename=spec.filename,
        doc_type=spec.doc_type,
        expected_rows=spec.n_data_rows,
        expected_giroconti=spec.n_giroconti,
        header_expected=spec.n_header_rows,
    )

    t0 = time.time()
    try:
        result = process_file(
            raw_bytes=raw_bytes,
            filename=spec.filename,
            config=config,
            taxonomy=taxonomy,
            user_rules=[],
            known_schema=None,  # Flow 2: LLM classifies the schema
            existing_tx_ids_checker=lambda ids: set(),  # no DB dedup
        )

        kpi.elapsed_s = time.time() - t0
        kpi.schema_detected = result.doc_schema is not None
        if result.doc_schema:
            kpi.confidence_score = result.doc_schema.confidence_score
            kpi.schema_doc_type = result.doc_schema.doc_type.value
            kpi.schema_sign_convention = result.doc_schema.sign_convention.value
            kpi.header_detected = result.header_rows_skipped

        kpi.rows_parsed = len(result.transactions)
        kpi.rows_skipped = len(result.skipped_rows)
        kpi.rows_merged = result.merged_count
        kpi.giroconti_detected = result.internal_transfer_count

        kpi.categorized = sum(
            1 for tx in result.transactions
            if tx.get("category") and tx["category"] != "unknown"
        )
        kpi.to_review = sum(
            1 for tx in result.transactions
            if tx.get("to_review")
        )
        kpi.errors = "; ".join(result.errors) if result.errors else ""

    except Exception as e:
        kpi.elapsed_s = time.time() - t0
        kpi.errors = str(e)

    _kpi_results.append(kpi)

    # ── Soft assertions (collect, don't fail hard) ──
    # Schema should be detected
    assert kpi.schema_detected, f"Schema detection failed: {kpi.errors}"

    # At least 80% of rows parsed
    assert kpi.automation_score >= 80.0, (
        f"Low automation: {kpi.automation_score:.1f}% "
        f"(parsed={kpi.rows_parsed}/{kpi.expected_rows}, skipped={kpi.rows_skipped})"
    )


# ── Report + results CSV ──────────────────────────────────────────────────
def test_llm_report(output_dir: Path):
    """Print report and save results CSV. Must run AFTER all parametrized tests."""
    if not _kpi_results:
        pytest.skip("No KPI data (run parametrized tests first)")

    results = sorted(_kpi_results, key=lambda k: k.filename)

    # Write results CSV
    fieldnames = [
        "filename", "doc_type", "expected_rows", "rows_parsed", "rows_skipped",
        "rows_merged", "automation_score", "schema_detected", "confidence_score",
        "schema_doc_type", "schema_sign_convention", "header_expected",
        "header_detected", "expected_giroconti", "giroconti_detected",
        "categorized", "categorization_rate", "to_review", "elapsed_s", "errors",
    ]
    with open(_RESULTS_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for k in results:
            writer.writerow({
                "filename": k.filename,
                "doc_type": k.doc_type,
                "expected_rows": k.expected_rows,
                "rows_parsed": k.rows_parsed,
                "rows_skipped": k.rows_skipped,
                "rows_merged": k.rows_merged,
                "automation_score": f"{k.automation_score:.1f}",
                "schema_detected": k.schema_detected,
                "confidence_score": f"{k.confidence_score:.2f}",
                "schema_doc_type": k.schema_doc_type,
                "schema_sign_convention": k.schema_sign_convention,
                "header_expected": k.header_expected,
                "header_detected": k.header_detected,
                "expected_giroconti": k.expected_giroconti,
                "giroconti_detected": k.giroconti_detected,
                "categorized": k.categorized,
                "categorization_rate": f"{k.categorization_rate:.1f}",
                "to_review": k.to_review,
                "elapsed_s": f"{k.elapsed_s:.1f}",
                "errors": k.errors,
            })

    # Print report
    total_expected = sum(k.expected_rows for k in results)
    total_parsed = sum(k.rows_parsed for k in results)
    total_skipped = sum(k.rows_skipped for k in results)
    total_cat = sum(k.categorized for k in results)
    total_giro_exp = sum(k.expected_giroconti for k in results)
    total_giro_det = sum(k.giroconti_detected for k in results)
    total_time = sum(k.elapsed_s for k in results)
    avg_score = (total_parsed / total_expected * 100) if total_expected else 0
    avg_cat = (total_cat / total_parsed * 100) if total_parsed else 0

    print("\n")
    print("=" * 110)
    print("                         LLM IMPORT — AUTOMATION KPI REPORT")
    print("=" * 110)
    print(f"{'File':<35} {'Type':<12} {'Rows':>5} {'Parse':>5} {'Skip':>5} "
          f"{'Conf':>5} {'Hdr':>5} {'Giro':>5} {'Cat%':>6} {'Time':>6} {'Score':>7}")
    print("-" * 110)
    for k in results:
        hdr = "OK" if k.header_detected == k.header_expected else f"!{k.header_detected}"
        giro = f"{k.giroconti_detected}/{k.expected_giroconti}"
        print(
            f"{k.filename:<35} {k.doc_type:<12} {k.expected_rows:>5} {k.rows_parsed:>5} "
            f"{k.rows_skipped:>5} {k.confidence_score:>5.2f} {hdr:>5} {giro:>5} "
            f"{k.categorization_rate:>5.1f}% {k.elapsed_s:>5.1f}s {k.automation_score:>6.1f}%"
        )
        if k.errors:
            print(f"  ⚠️  {k.errors[:80]}")
    print("-" * 110)
    print(
        f"{'TOTALE':<35} {'':12} {total_expected:>5} {total_parsed:>5} "
        f"{total_skipped:>5} {'':>5} {'':>5} "
        f"{total_giro_det}/{total_giro_exp:<4} {avg_cat:>5.1f}% "
        f"{total_time:>5.1f}s {avg_score:>6.1f}%"
    )
    print("=" * 110)
    print(f"\n📊 File testati: {len(results)}")
    print(f"📈 Automation score medio: {avg_score:.1f}%")
    print(f"📝 Categorization rate: {avg_cat:.1f}%")
    print(f"🔄 Giroconti rilevati: {total_giro_det}/{total_giro_exp}")
    print(f"⏱️  Tempo totale: {total_time:.1f}s")
    print(f"\n📁 File generati: {_OUTPUT_DIR}")
    print(f"📋 Manifest: {_MANIFEST_PATH}")
    print(f"📊 Risultati: {_RESULTS_PATH}")
    print()
