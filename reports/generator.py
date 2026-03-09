"""Report generation (RF-09).

Produces LaTeX and standalone HTML reports from the database.
"""
from __future__ import annotations

import io
import os
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from db.models import Transaction
from db.repository import get_transactions
from support.logging import setup_logging

logger = setup_logging()

REPORTS_DIR = Path(__file__).parent


def _query_summary(
    session: Session,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> dict:
    """Build a summary dict from the transaction table."""
    filters = {}
    if date_from:
        filters["date_from"] = date_from
    if date_to:
        filters["date_to"] = date_to

    txs = get_transactions(session, filters=filters)

    # Exclude internal / settlement types from net balance
    excluded = {"internal_out", "internal_in", "card_settlement", "aggregate_debit"}
    net = sum(
        Decimal(str(tx.amount)) for tx in txs
        if tx.tx_type not in excluded
    )

    total_income = sum(
        Decimal(str(tx.amount)) for tx in txs
        if tx.tx_type not in excluded and Decimal(str(tx.amount)) > 0
    )
    total_expense = sum(
        Decimal(str(tx.amount)) for tx in txs
        if tx.tx_type not in excluded and Decimal(str(tx.amount)) < 0
    )

    # Group by category
    cat_totals: dict[str, Decimal] = {}
    for tx in txs:
        if tx.tx_type in excluded:
            continue
        cat = tx.category or "Altro"
        cat_totals[cat] = cat_totals.get(cat, Decimal(0)) + Decimal(str(tx.amount))

    return {
        "transactions": txs,
        "net": net,
        "total_income": total_income,
        "total_expense": total_expense,
        "by_category": cat_totals,
        "date_from": date_from,
        "date_to": date_to,
    }


def generate_html_report(
    session: Session,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> str:
    """Generate a standalone HTML report using Jinja2 template."""
    try:
        from jinja2 import Environment, FileSystemLoader
        import plotly.graph_objects as go
        import plotly

        summary = _query_summary(session, date_from, date_to)

        # Build chart data
        categories = list(summary["by_category"].keys())
        values = [float(v) for v in summary["by_category"].values()]

        fig = go.Figure(go.Bar(
            x=categories,
            y=values,
            marker_color=["green" if v >= 0 else "red" for v in values],
        ))
        fig.update_layout(title="Saldo per categoria", xaxis_title="Categoria", yaxis_title="€")
        chart_html = plotly.offline.plot(fig, include_plotlyjs="cdn", output_type="div")

        template_path = REPORTS_DIR / "template_report.html.j2"
        if template_path.exists():
            env = Environment(loader=FileSystemLoader(str(REPORTS_DIR)))
            template = env.get_template("template_report.html.j2")
            return template.render(
                summary=summary,
                chart_html=chart_html,
                generated_at=date.today().isoformat(),
            )
        else:
            # Minimal fallback HTML
            rows_html = "\n".join(
                f"<tr><td>{tx.date}</td><td>{tx.description[:60]}</td>"
                f"<td>{tx.amount}</td><td>{tx.category or ''}</td></tr>"
                for tx in summary["transactions"][:200]
            )
            return f"""<!DOCTYPE html><html><head><title>Spendify Report</title></head><body>
<h1>Spendify Report</h1>
<p>Period: {date_from or 'all'} – {date_to or 'present'}</p>
<p>Net balance: {summary['net']:.2f} €</p>
<p>Total income: {summary['total_income']:.2f} €</p>
<p>Total expense: {summary['total_expense']:.2f} €</p>
{chart_html}
<table border="1"><tr><th>Date</th><th>Description</th><th>Amount</th><th>Category</th></tr>
{rows_html}
</table></body></html>"""
    except Exception as exc:
        logger.error(f"generate_html_report failed: {exc}", exc_info=True)
        return f"<html><body><p>Report generation error: {exc}</p></body></html>"


def generate_csv_export(session: Session, filters: Optional[dict] = None) -> bytes:
    """Export transactions as CSV bytes."""
    import csv
    txs = get_transactions(session, filters=filters)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "date", "date_accounting", "amount", "currency",
        "description", "source_file", "doc_type", "account_label",
        "tx_type", "category", "subcategory", "category_confidence",
        "category_source", "reconciled", "to_review",
    ])
    for tx in txs:
        writer.writerow([
            tx.id, tx.date, tx.date_accounting, tx.amount, tx.currency,
            tx.description, tx.source_file, tx.doc_type, tx.account_label,
            tx.tx_type, tx.category, tx.subcategory, tx.category_confidence,
            tx.category_source, tx.reconciled, tx.to_review,
        ])
    return output.getvalue().encode("utf-8")


def generate_xlsx_export(session: Session, filters: Optional[dict] = None) -> bytes:
    """Export transactions as XLSX bytes."""
    import pandas as pd
    txs = get_transactions(session, filters=filters)
    data = [
        {
            "date": tx.date, "description": tx.description, "amount": float(tx.amount),
            "currency": tx.currency, "account_label": tx.account_label,
            "tx_type": tx.tx_type, "category": tx.category, "subcategory": tx.subcategory,
            "confidence": tx.category_confidence, "source": tx.category_source,
            "to_review": tx.to_review,
        }
        for tx in txs
    ]
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Transactions")
    return output.getvalue()
