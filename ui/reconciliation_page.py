import streamlit as st
from services.history_service import save_history

def render_reconciliation_page(history_df):
    st.header("🔍 Riconciliazione Documenti")

    if history_df is None or history_df.empty:
        st.info("Database vuoto.")
        return

    needs_recon = history_df[history_df["Richiede_Documento"] == True]

    if needs_recon.empty:
        st.success("Tutte le transazioni critiche sono state riconciliate.")
        return

    st.info(f"Hai {len(needs_recon)} transazioni in attesa di giustificativo.")

    recon_editor = st.data_editor(
        needs_recon,
        column_config={
            "Link_Ricevuta": st.column_config.TextColumn("ID Ricevuta / Nome File"),
            "Stato_Riconciliazione": st.column_config.SelectboxColumn(
                options=["In Attesa Ricevuta", "Ricevuta Caricata", "Riconciliato"]
            ),
            "Categoria_Approvata": st.column_config.CheckboxColumn(disabled=True)
        },
        disabled=["Data_Valuta", "Descrizione", "Uscita", "IBAN"],
        key="recon_editor"
    )

    if st.button("💾 Aggiorna Stato Riconciliazioni", type="primary"):
        history_df.update(recon_editor)
        save_history(history_df)
        st.success("Stato riconciliazioni salvato!")

    