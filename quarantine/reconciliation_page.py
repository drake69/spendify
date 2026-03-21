"""Reconciliation page — replaced by Ledger (RF-08).

This page is no longer part of the main navigation. The reconciliation
logic is now handled automatically by the pipeline (RF-03).
Kept as a stub to avoid import errors from any cached references.
"""
import streamlit as st


def render_reconciliation_page(engine=None):
    st.info(
        "La riconciliazione avviene automaticamente durante l'import. "
        "Consulta la pagina Ledger per vedere le transazioni riconciliate."
    )
