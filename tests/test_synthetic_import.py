"""
Test di importazione su file sintetici con KPI di automazione.

Genera file CSV/XLSX in-memory con metadati noti, esegue la pipeline
(load → normalize) SENZA DB e SENZA LLM, confronta i KPI risultanti
con i valori attesi.

Eseguire con:
    uv run pytest tests/test_synthetic_import.py -v -s --tb=short
"""
from __future__ import annotations

import csv
import io
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
import pytest

from core.models import Confidence, DocumentType, SignConvention
from core.orchestrator import (
    SkippedRow,
    load_raw_dataframe,
    _normalize_df_with_schema,
)
from core.schemas import DocumentSchema

# ── Seed for reproducibility ───────────────────────────────────────────────
random.seed(42)

# ── Realistic Italian bank descriptions ────────────────────────────────────
_EXPENSE_DESCRIPTIONS = [
    "Pagam. POS - PAGAMENTO POS 42,60 EUR DEL 15.01.2026 A (ITA) ESSELUNGA MILANO",
    "Pagam. POS - PAGAMENTO POS 18,50 EUR DEL 16.01.2026 A (ITA) BAR CENTRALE",
    "Addebito SDD - ENEL SERVIZIO ELETTRICO NAZ.",
    "Addebito SDD - TELECOM ITALIA S.P.A.",
    "Addebito SDD - Wind Tre S.p.A. CL.4521879",
    "Disposizione - RIF:212524764 BEN. CONDOMINIO VIA ROMA 15",
    "Pagam. POS - PAGAMENTO POS 77,00 EUR DEL 20.01.2026 A (ITA) FARMACIA DR ROSSI",
    "Addebito SDD - AMERICAN EXPRESS SERVICES EUR",
    "Pagam. POS - PAGAMENTO POS 3,20 EUR DEL 21.01.2026 A (ITA) PANIFICIO TOSI",
    "Pagam. POS - PAGAMENTO POS 55,50 EUR DEL 22.01.2026 A (ITA) DECATHLON ASSAGO",
    "Disposizione - RIF:212183808 BEN. MARIO BIANCHI",
    "Addebito SDD - BANCA POPOLARE MUTUO 12345678",
    "Pagam. POS - PAGAMENTO POS 9,90 EUR DEL 23.01.2026 A (ITA) NETFLIX.COM",
    "PAGAMENTO CONTACTLESS CARTA **** 4521 CONAD CITY",
    "PRELIEVO ATM BANCOMAT FIL. 00456 MILANO",
    "Pagam. POS - PAGAMENTO POS 36,00 EUR DEL 24.01.2026 A (ITA) RISTORANTE DA GINO",
    "Addebito SDD - HERA COMM S.R.L. ACQUA",
    "Pagam. POS - PAGAMENTO POS 120,00 EUR DEL 25.01.2026 A (ITA) DENTISTA DR VERDI",
    "RICARICA POSTEPAY DA CONTO 00112233",
    "Pagam. POS - PAGAMENTO POS 14,99 EUR DEL 26.01.2026 A (ITA) SPOTIFY AB",
    "Disposizione - RIF:213003169 BEN. UNIVERSITA' DEGLI STUDI",
    "Pagam. POS - PAGAMENTO POS 50,00 EUR DEL 27.01.2026 A (ITA) ZARA ITALIA SRL",
    "Addebito SDD - SARA ASSICURAZIONI RCA AUTO",
    "Pagam. POS - PAGAMENTO POS 1,20 EUR DEL 28.01.2026 A (ITA) DISTRIBUTORE Q8",
    "Pagam. POS - PAGAMENTO POS 65,00 EUR DEL 29.01.2026 A (ITA) MEDIAWORLD CARUGATE",
    "Disposizione - RIF:210567653 BEN. AG. IMMOBILIARE CASA MIA",
    "PAGAMENTO CONTACTLESS CARTA **** 4521 LIDL ITALIA",
    "Pagam. POS - PAGAMENTO POS 23,40 EUR DEL 30.01.2026 A (ITA) TRENITALIA S.P.A.",
    "Addebito SDD - DELIVEROO ITALY SRL",
    "Pagam. POS - PAGAMENTO POS 8,50 EUR DEL 31.01.2026 A (ITA) MACELLERIA BONI",
]

_INCOME_DESCRIPTIONS = [
    "Bonif. v/fav. - RIF:211072327 ORD. REGIONE LOMBARDIA",
    "ACCREDITO STIPENDIO MESE DI GENNAIO 2026",
    "Bonif. v/fav. - RIF:213003169 ORD. STUDIO CORSARO SRL",
    "Bonif. v/fav. - RIF:210959215 ORD. CLIENTE ALFA SPA",
    "ACCREDITO PENSIONE INPS MESE DI GENNAIO 2026",
    "GIROCONTO DA CC 00112233 A CC 44556677",
    "Bonif. v/fav. - RIF:210567653 ORD. AGENZIA ENTRATE RIMBORSO",
]

_CARD_DESCRIPTIONS = [
    "AMAZON.IT MARKETPLACE - ORDINE 402-1234567",
    "ESSELUNGA S.P.A. MILANO VIA RIPAMONTI",
    "AUTOSTRADA TELEPASS SPA PEDAGGIO A1",
    "BOOKING.COM BV HOTEL SEASIDE RIMINI",
    "RYANAIR LTD VOLO FR1234 MXP-FCO",
    "JUST EAT ITALY SRL ORDINE #987654",
    "GOOGLE *CLOUD STORAGE",
    "APPLE.COM/BILL ITUNES STORE",
    "ENI STATION 45678 CARBURANTE",
    "H&M HENNES & MAURITZ MILANO DUOMO",
    "McDONALD'S RISTORANTE 12345",
    "IKEA ITALIA RETAIL CARUGATE MI",
]

# ── Preheader templates ────────────────────────────────────────────────────
_PREHEADER_TEMPLATES = {
    0: [],
    3: [
        ["Intesa Sanpaolo S.p.A.", "", "", "", "", ""],
        ["Conto: IT60X0306901783123456789012", "", "", "", "", ""],
        ["Periodo: 01/01/2026 - 28/02/2026", "", "", "", "", ""],
    ],
    5: [
        ["", "", "", "", "", ""],
        ["BANCA SELLA S.P.A.", "", "", "", "", ""],
        ["Filiale di Milano - Cod. 04561", "", "", "", "", ""],
        ["IBAN: IT40S0306901783100000000123", "", "", "", "", ""],
        ["Estratto conto dal 01/01/2026 al 28/02/2026", "", "", "", "", ""],
    ],
    8: [
        ["", "", "", "", "", ""],
        ["BANCO BPM S.P.A.", "", "", "", "", ""],
        ["Intestatario: ROSA BIANCHI", "", "", "", "", ""],
        ["Cod. Fiscale: BNCRSO50A41F205Z", "", "", "", "", ""],
        ["Conto: 000012345678", "", "", "", "", ""],
        ["IBAN: IT76P0503401633000000012345", "", "", "", "", ""],
        ["Periodo: 01/01/2026 - 28/02/2026", "", "", "", "", ""],
        ["", "", "", "", "", ""],
    ],
    14: [
        ["", "", "", "", "", "", ""],
        ["Prova", "76587658", "", "", "", "", ""],
        ["data", "2026-07-06", "", "", "", "", ""],
        ["conto", "milano", "", "", "", "", ""],
        ["note", "test", "", "", "", "", ""],
        ["", "", "", "", "", "", ""],
        ["data", "2026-07-06", "", "", "", "", ""],
        ["conto", "milano", "", "", "", "", ""],
        ["note", "test", "", "", "", "", ""],
        ["", "", "", "", "", "", ""],
        ["", "", "", "", "", "", ""],
        ["", "", "", "", "", "", ""],
        ["", "", "", "", "", "", ""],
        ["", "", "", "", "", "", ""],
    ],
}

_FOOTER_TEMPLATES = {
    0: [],
    2: [
        ["", "", "Saldo finale:", "", "12.345,67", "EUR"],
        ["", "", "Totale movimenti: 46", "", "", ""],
    ],
    3: [
        ["", "", "", "", "", ""],
        ["", "", "Saldo finale:", "", "8.901,23", "EUR"],
        ["", "", "Totale movimenti: 30", "", "", ""],
    ],
}


# ── File spec dataclass ────────────────────────────────────────────────────
@dataclass
class FileSpec:
    """Specification for a single synthetic file."""
    filename: str
    doc_type: str            # "bank_account", "credit_card", etc.
    account_id: str          # CC-1, CRED-1, etc.
    bank: str
    fmt: str                 # "csv" or "xlsx"
    separator: str           # ";" or "," (only for CSV)
    n_header_rows: int
    n_data_rows: int
    n_footer_rows: int
    amount_format: str       # "signed_single", "debit_credit_split", "debit_credit_signed", "positive_only"
    date_format: str         # "%d/%m/%Y"
    columns: list[str]       # header column names
    amount_col: str | None = None
    debit_col: str | None = None
    credit_col: str | None = None
    description_col: str = "Descrizione"
    date_col: str = "Data operazione"
    date_val_col: str | None = None
    invert_sign: bool = False
    currency_col: str | None = None


# ── Registry: ~30 synthetic file specs ─────────────────────────────────────
SYNTHETIC_FILES: list[FileSpec] = [
    # ── CC-1: Intesa Sanpaolo — signed_single ──
    FileSpec("CC1_Intesa_signed_S.csv", "bank_account", "CC-1", "Intesa Sanpaolo",
             "csv", ";", 3, 30, 2, "signed_single", "%d/%m/%Y",
             ["Data operazione", "Data valuta", "Descrizione", "Importo", "Divisa"],
             amount_col="Importo", date_val_col="Data valuta", currency_col="Divisa"),
    FileSpec("CC1_Intesa_signed_M.csv", "bank_account", "CC-1", "Intesa Sanpaolo",
             "csv", ";", 3, 100, 2, "signed_single", "%d/%m/%Y",
             ["Data operazione", "Data valuta", "Descrizione", "Importo", "Divisa"],
             amount_col="Importo", date_val_col="Data valuta", currency_col="Divisa"),
    FileSpec("CC1_Intesa_signed_L.csv", "bank_account", "CC-1", "Intesa Sanpaolo",
             "csv", ";", 3, 300, 0, "signed_single", "%d/%m/%Y",
             ["Data operazione", "Data valuta", "Descrizione", "Importo", "Divisa"],
             amount_col="Importo", date_val_col="Data valuta", currency_col="Divisa"),

    # ── CC-2: Banca Sella — debit_credit_split (both positive) ──
    FileSpec("CC2_Sella_dc_split_S.csv", "bank_account", "CC-2", "Banca Sella",
             "csv", ",", 5, 30, 0, "debit_credit_split", "%d/%m/%Y",
             ["Data registrazione", "Valuta", "Causale", "Dare", "Avere", "Divisa"],
             debit_col="Dare", credit_col="Avere", description_col="Causale",
             date_col="Data registrazione", date_val_col="Valuta", currency_col="Divisa"),
    FileSpec("CC2_Sella_dc_split_M.xlsx", "bank_account", "CC-2", "Banca Sella",
             "xlsx", "", 5, 80, 3, "debit_credit_split", "%d/%m/%Y",
             ["Data registrazione", "Valuta", "Causale", "Dare", "Avere", "Divisa"],
             debit_col="Dare", credit_col="Avere", description_col="Causale",
             date_col="Data registrazione", date_val_col="Valuta", currency_col="Divisa"),

    # ── CC-3: Banco BPM — debit_credit_signed (negative debits, like MedioBanca) ──
    FileSpec("CC3_BPM_dc_signed_S.xlsx", "bank_account", "CC-3", "Banco BPM",
             "xlsx", "", 8, 30, 2, "debit_credit_signed", "%d/%m/%Y",
             ["Data contabile", "Data valuta", "Tipologia", "Entrate", "Uscite", "Divisa"],
             debit_col="Uscite", credit_col="Entrate", description_col="Tipologia",
             date_col="Data contabile", date_val_col="Data valuta", currency_col="Divisa"),
    FileSpec("CC3_BPM_dc_signed_M.xlsx", "bank_account", "CC-3", "Banco BPM",
             "xlsx", "", 8, 100, 2, "debit_credit_signed", "%d/%m/%Y",
             ["Data contabile", "Data valuta", "Tipologia", "Entrate", "Uscite", "Divisa"],
             debit_col="Uscite", credit_col="Entrate", description_col="Tipologia",
             date_col="Data contabile", date_val_col="Data valuta", currency_col="Divisa"),
    FileSpec("CC3_BPM_dc_signed_L.csv", "bank_account", "CC-3", "Banco BPM",
             "csv", ";", 8, 300, 0, "debit_credit_signed", "%d/%m/%Y",
             ["Data contabile", "Data valuta", "Tipologia", "Entrate", "Uscite", "Divisa"],
             debit_col="Uscite", credit_col="Entrate", description_col="Tipologia",
             date_col="Data contabile", date_val_col="Data valuta", currency_col="Divisa"),

    # ── CC with 14 header rows (MedioBanca case) ──
    FileSpec("CC3_BPM_14hdr_S.xlsx", "bank_account", "CC-3", "Banco BPM",
             "xlsx", "", 14, 30, 0, "debit_credit_signed", "%d/%m/%Y",
             ["Data contabile", "Data valuta", "Tipologia", "Entrate", "Uscite", "Divisa"],
             debit_col="Uscite", credit_col="Entrate", description_col="Tipologia",
             date_col="Data contabile", date_val_col="Data valuta", currency_col="Divisa"),

    # ── CC with 0 header rows ──
    FileSpec("CC1_Intesa_nohdr_S.csv", "bank_account", "CC-1", "Intesa Sanpaolo",
             "csv", ";", 0, 30, 0, "signed_single", "%d/%m/%Y",
             ["Data operazione", "Data valuta", "Descrizione", "Importo", "Divisa"],
             amount_col="Importo", date_val_col="Data valuta", currency_col="Divisa"),

    # ── CRED-1: Amex — positive_only (card expenses are positive) ──
    FileSpec("CRED1_Amex_positive_S.csv", "credit_card", "CRED-1", "American Express",
             "csv", ";", 3, 30, 0, "positive_only", "%d/%m/%Y",
             ["Data", "Descrizione operazione", "Importo", "Valuta"],
             amount_col="Importo", description_col="Descrizione operazione",
             date_col="Data", currency_col="Valuta", invert_sign=True),
    FileSpec("CRED1_Amex_positive_M.xlsx", "credit_card", "CRED-1", "American Express",
             "xlsx", "", 3, 80, 2, "positive_only", "%d/%m/%Y",
             ["Data", "Descrizione operazione", "Importo", "Valuta"],
             amount_col="Importo", description_col="Descrizione operazione",
             date_col="Data", currency_col="Valuta", invert_sign=True),
    FileSpec("CRED1_Amex_positive_L.csv", "credit_card", "CRED-1", "American Express",
             "csv", ";", 3, 300, 0, "positive_only", "%d/%m/%Y",
             ["Data", "Descrizione operazione", "Importo", "Valuta"],
             amount_col="Importo", description_col="Descrizione operazione",
             date_col="Data", currency_col="Valuta", invert_sign=True),

    # ── CRED-2: Nexi — signed_single ──
    FileSpec("CRED2_Nexi_signed_S.csv", "credit_card", "CRED-2", "Nexi",
             "csv", ";", 5, 30, 0, "signed_single", "%d/%m/%Y",
             ["Data mov.", "Descrizione", "Movimento", "EUR"],
             amount_col="Movimento", description_col="Descrizione",
             date_col="Data mov.", currency_col="EUR"),
    FileSpec("CRED2_Nexi_signed_M.xlsx", "credit_card", "CRED-2", "Nexi",
             "xlsx", "", 5, 80, 2, "signed_single", "%d/%m/%Y",
             ["Data mov.", "Descrizione", "Movimento", "EUR"],
             amount_col="Movimento", description_col="Descrizione",
             date_col="Data mov.", currency_col="EUR"),

    # ── RIC-1: PostePay — signed_single ──
    FileSpec("RIC1_PostePay_signed_S.csv", "prepaid_card", "RIC-1", "PostePay",
             "csv", ";", 0, 30, 0, "signed_single", "%d/%m/%Y",
             ["Data", "Dettaglio", "Importo"],
             amount_col="Importo", description_col="Dettaglio", date_col="Data"),

    # ── RIC-2: Hype — debit_credit_split ──
    FileSpec("RIC2_Hype_dc_S.csv", "prepaid_card", "RIC-2", "Hype",
             "csv", ",", 0, 30, 0, "debit_credit_split", "%d/%m/%Y",
             ["Data operazione", "Descrizione", "Addebiti", "Accrediti"],
             debit_col="Addebiti", credit_col="Accrediti",
             description_col="Descrizione", date_col="Data operazione"),

    # ── RIC-3: Revolut — signed_single ──
    FileSpec("RIC3_Revolut_signed_S.csv", "prepaid_card", "RIC-3", "Revolut",
             "csv", ";", 0, 30, 0, "signed_single", "%d/%m/%Y",
             ["Data completamento", "Descrizione", "Importo", "Valuta"],
             amount_col="Importo", description_col="Descrizione",
             date_col="Data completamento", currency_col="Valuta"),

    # ── RISP-1: Conto Arancio — signed_single ──
    FileSpec("RISP1_Arancio_signed_S.csv", "savings", "RISP-1", "Conto Arancio",
             "csv", ";", 3, 30, 2, "signed_single", "%d/%m/%Y",
             ["Data", "Causale", "Importo", "Divisa"],
             amount_col="Importo", description_col="Causale",
             date_col="Data", currency_col="Divisa"),
    FileSpec("RISP1_Arancio_signed_M.xlsx", "savings", "RISP-1", "Conto Arancio",
             "xlsx", "", 3, 80, 2, "signed_single", "%d/%m/%Y",
             ["Data", "Causale", "Importo", "Divisa"],
             amount_col="Importo", description_col="Causale",
             date_col="Data", currency_col="Divisa"),

    # ── Mixed: CSV semicolon with large header ──
    FileSpec("CC2_Sella_bighdr_M.csv", "bank_account", "CC-2", "Banca Sella",
             "csv", ";", 8, 80, 3, "debit_credit_split", "%d/%m/%Y",
             ["Data registrazione", "Valuta", "Causale", "Dare", "Avere", "Divisa"],
             debit_col="Dare", credit_col="Avere", description_col="Causale",
             date_col="Data registrazione", date_val_col="Valuta", currency_col="Divisa"),

    # ── Edge: very small file (30 rows) with big header (14) ──
    FileSpec("CC1_Intesa_14hdr_S.csv", "bank_account", "CC-1", "Intesa Sanpaolo",
             "csv", ";", 14, 30, 0, "signed_single", "%d/%m/%Y",
             ["Data operazione", "Data valuta", "Descrizione", "Importo", "Divisa"],
             amount_col="Importo", date_val_col="Data valuta", currency_col="Divisa"),
]


# ── Synthetic file generator ───────────────────────────────────────────────
def _random_amount(is_expense: bool) -> Decimal:
    """Generate a plausible transaction amount."""
    if is_expense:
        return -Decimal(str(round(random.uniform(0.50, 2000.00), 2)))
    return Decimal(str(round(random.uniform(100.00, 5000.00), 2)))


def _random_date(base: date, offset: int) -> date:
    return base - timedelta(days=offset)


def _format_amount_italian(amount: Decimal) -> str:
    """Format a Decimal as Italian locale string (1.234,56)."""
    sign = "-" if amount < 0 else ""
    abs_val = abs(amount)
    int_part = int(abs_val)
    dec_part = abs_val - int_part
    dec_str = f"{dec_part:.2f}"[2:]  # "56"
    if int_part >= 1000:
        int_str = f"{int_part:,}".replace(",", ".")
    else:
        int_str = str(int_part)
    return f"{sign}{int_str},{dec_str}"


def generate_synthetic_file(spec: FileSpec) -> bytes:
    """Generate synthetic CSV or XLSX bytes from a FileSpec."""
    base_date = date(2026, 2, 28)
    n_cols = len(spec.columns)

    # ── Build data rows ──
    data_rows = []
    income_every = max(5, spec.n_data_rows // 6)  # ~1/6 income rows

    for i in range(spec.n_data_rows):
        is_income = (i % income_every == 0) and spec.amount_format != "positive_only"
        tx_date = _random_date(base_date, i)
        val_date = tx_date - timedelta(days=random.randint(0, 3))
        amount = _random_amount(is_expense=not is_income)

        if is_income:
            desc = random.choice(_INCOME_DESCRIPTIONS)
        elif spec.doc_type == "credit_card":
            desc = random.choice(_CARD_DESCRIPTIONS)
        else:
            desc = random.choice(_EXPENSE_DESCRIPTIONS)

        row: dict[str, str] = {}
        row[spec.date_col] = tx_date.strftime(spec.date_format)
        if spec.date_val_col and spec.date_val_col in spec.columns:
            row[spec.date_val_col] = val_date.strftime(spec.date_format)
        row[spec.description_col] = desc

        if spec.amount_format == "signed_single":
            row[spec.amount_col] = _format_amount_italian(amount)
        elif spec.amount_format == "positive_only":
            # Card: all amounts positive (expenses)
            row[spec.amount_col] = _format_amount_italian(abs(amount))
        elif spec.amount_format == "debit_credit_split":
            # Both positive, mutually exclusive
            if amount < 0:
                row[spec.debit_col] = _format_amount_italian(abs(amount))
                row[spec.credit_col] = ""
            else:
                row[spec.debit_col] = ""
                row[spec.credit_col] = _format_amount_italian(amount)
        elif spec.amount_format == "debit_credit_signed":
            # Debits negative in Uscite, credits positive in Entrate
            if amount < 0:
                row[spec.debit_col] = _format_amount_italian(amount)  # negative
                row[spec.credit_col] = ""
            else:
                row[spec.debit_col] = ""
                row[spec.credit_col] = _format_amount_italian(amount)  # positive

        if spec.currency_col and spec.currency_col in spec.columns:
            row[spec.currency_col] = "EUR"

        # Build ordered row
        ordered = [row.get(col, "") for col in spec.columns]
        data_rows.append(ordered)

    # ── Build preheader ──
    preheader = _PREHEADER_TEMPLATES.get(spec.n_header_rows, [])
    if not preheader and spec.n_header_rows > 0:
        # Generate generic preheader
        preheader = []
        for j in range(spec.n_header_rows):
            r = [""] * n_cols
            if j == 1:
                r[0] = spec.bank
            elif j == 2:
                r[0] = f"Conto: {spec.account_id}"
            preheader.append(r)
    # Pad preheader rows to match column count
    preheader = [r[:n_cols] + [""] * max(0, n_cols - len(r)) for r in preheader]

    # ── Build footer ──
    footer = _FOOTER_TEMPLATES.get(spec.n_footer_rows, [])
    footer = [r[:n_cols] + [""] * max(0, n_cols - len(r)) for r in footer]

    # ── Assemble full file ──
    all_rows = preheader + [spec.columns] + data_rows + footer

    if spec.fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter=spec.separator, quoting=csv.QUOTE_MINIMAL)
        for row in all_rows:
            writer.writerow(row)
        return buf.getvalue().encode("utf-8")
    else:
        # XLSX
        df = pd.DataFrame(all_rows)
        buf = io.BytesIO()
        df.to_excel(buf, index=False, header=False, engine="openpyxl")
        return buf.getvalue()


# ── Build DocumentSchema from FileSpec ─────────────────────────────────────
def _schema_from_spec(spec: FileSpec) -> DocumentSchema:
    """Build a DocumentSchema from a FileSpec (known ground truth)."""
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
class ImportKPI:
    filename: str
    expected_rows: int
    expected_header: int
    detected_header: int
    rows_parsed: int
    rows_skipped: int
    rows_merged: int
    skip_reasons: dict[str, int] = field(default_factory=dict)

    @property
    def automation_score(self) -> float:
        if self.expected_rows == 0:
            return 100.0
        return (self.rows_parsed / self.expected_rows) * 100.0

    @property
    def header_ok(self) -> bool:
        return self.detected_header == self.expected_header


# Global collector for the report
_kpi_results: list[ImportKPI] = []


# ── Parametrized test ──────────────────────────────────────────────────────
@pytest.mark.parametrize("spec", SYNTHETIC_FILES, ids=lambda s: s.filename)
def test_synthetic_import(spec: FileSpec):
    """Test load + normalize pipeline on a synthetic file."""
    raw_bytes = generate_synthetic_file(spec)

    # Step 1: Load raw DataFrame (header detection)
    df, encoding, preprocess = load_raw_dataframe(raw_bytes, spec.filename)

    # Step 2: Build known schema
    schema = _schema_from_spec(spec)

    # Step 3: Normalize
    transactions, skipped, merge_count = _normalize_df_with_schema(
        df, schema, spec.filename
    )

    # ── Collect KPIs ──
    skip_reasons: dict[str, int] = {}
    for s in skipped:
        skip_reasons[s.reason] = skip_reasons.get(s.reason, 0) + 1

    kpi = ImportKPI(
        filename=spec.filename,
        expected_rows=spec.n_data_rows,
        expected_header=spec.n_header_rows,
        detected_header=preprocess.skipped_rows,
        rows_parsed=len(transactions),
        rows_skipped=len(skipped),
        rows_merged=merge_count,
        skip_reasons=skip_reasons,
    )
    _kpi_results.append(kpi)

    # ── Assertions ──
    # Header detection
    assert kpi.header_ok, (
        f"Header mismatch: expected {spec.n_header_rows}, detected {preprocess.skipped_rows}"
    )

    # All data rows parsed (footer rows are expected to be skipped as date_nan)
    assert kpi.rows_parsed == spec.n_data_rows, (
        f"Rows parsed: {kpi.rows_parsed}/{spec.n_data_rows} "
        f"(skipped={kpi.rows_skipped}: {kpi.skip_reasons})"
    )

    # Only footer rows should be skipped (as date_nan)
    unexpected_skips = kpi.rows_skipped - spec.n_footer_rows
    assert unexpected_skips <= 0, (
        f"Unexpected skipped rows: {unexpected_skips} "
        f"(total skipped={kpi.rows_skipped}, footer={spec.n_footer_rows}, "
        f"reasons: {kpi.skip_reasons})"
    )

    # All transactions have required fields
    for tx in transactions:
        assert tx["date"] is not None, f"tx missing date: {tx}"
        assert tx["amount"] is not None, f"tx missing amount: {tx}"
        assert isinstance(tx["amount"], Decimal), f"amount not Decimal: {type(tx['amount'])}"

    # Score must be 100%
    assert kpi.automation_score == 100.0, (
        f"Score: {kpi.automation_score:.1f}% — "
        f"parsed={kpi.rows_parsed}, expected={kpi.expected_rows}"
    )


# ── Final report ───────────────────────────────────────────────────────────
def test_automation_report():
    """Print the automation KPI report (must run AFTER all parametrized tests)."""
    if not _kpi_results:
        pytest.skip("No KPI data collected (run parametrized tests first)")

    # Sort by filename
    results = sorted(_kpi_results, key=lambda k: k.filename)

    # Compute totals
    total_expected = sum(k.expected_rows for k in results)
    total_parsed = sum(k.rows_parsed for k in results)
    total_skipped = sum(k.rows_skipped for k in results)
    total_header_ok = sum(1 for k in results if k.header_ok)
    avg_score = (total_parsed / total_expected * 100) if total_expected else 0

    # Print report
    print("\n")
    print("=" * 85)
    print("                    AUTOMATION KPI REPORT")
    print("=" * 85)
    print(f"{'File':<40} {'Rows':>6} {'Parsed':>7} {'Skip':>5} {'Hdr':>5} {'Score':>8}")
    print("-" * 85)
    for k in results:
        hdr_mark = "OK" if k.header_ok else f"!{k.detected_header}"
        print(
            f"{k.filename:<40} {k.expected_rows:>6} {k.rows_parsed:>7} "
            f"{k.rows_skipped:>5} {hdr_mark:>5} {k.automation_score:>7.1f}%"
        )
        if k.skip_reasons:
            for reason, count in k.skip_reasons.items():
                print(f"  {'':40} skip reason: {reason} × {count}")
    print("-" * 85)
    print(
        f"{'TOTALE':<40} {total_expected:>6} {total_parsed:>7} "
        f"{total_skipped:>5} {total_header_ok:>3}/{len(results):>1} {avg_score:>7.1f}%"
    )
    print("=" * 85)
    print(f"\nFile testati: {len(results)}")
    print(f"Header detection corretti: {total_header_ok}/{len(results)}")
    print(f"Score automazione medio: {avg_score:.1f}%")
    print()
