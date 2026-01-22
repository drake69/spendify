import streamlit as st
import plotly.express as px
import support.core_logic as core

def render_analysis_page(history_df, budgets):
    st.header("📊 Analisi Spese e Controllo Budget")

    if history_df is None or history_df.empty:
        st.warning("Il database è vuoto. Carica un estratto conto per iniziare.")
        return

    sum_df = core.get_monthly_summary(history_df)

    if sum_df.empty:
        st.warning("Dati insufficienti per l'analisi.")
        return

    # Preparazione periodo
    sum_df["Periodo"] = (
        sum_df["Anno"].astype(str)
        + "-"
        + sum_df["Mese"].astype(str).str.zfill(2)
    )

    # 📈 Grafico
    fig = px.bar(
        sum_df,
        x="Periodo",
        y="Uscita",
        color="Categoria",
        barmode="group",
        title="Andamento Mensile per Categoria"
    )
    st.plotly_chart(fig, use_container_width=True)

    # 🚨 Budget
    st.subheader("🚨 Controllo Limiti di Spesa")
    alerts = core.check_budget_alerts(sum_df, budgets)

    if not alerts.empty:
        st.table(alerts)
    else:
        st.success("Tutte le spese sono entro i limiti impostati!")

    # 📥 Export
    st.divider()
    st.download_button(
        "📥 Scarica Report Excel Completo",
        core.export_to_excel(history_df),
        file_name="report_finanze.xlsx"
    )