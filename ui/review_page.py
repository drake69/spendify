import streamlit as st
import logging
import support.core_logic as core
from services.history_service import save_history

# Configure logger with more detail
logger = logging.getLogger(__name__)

CATEGORIE = list(core.DEFAULT_CATEGORIES.keys())
CONTESTI = core.DEFAULT_CONTEXTS
STATE_KEY = "working_df"
EDITOR_KEY = "review_editor"


def render_review_page(history_df):
    """Renderizza la pagina di revisione estratti conto"""
    st.header("📝 Revisione Estratti Conto")
    logger.debug("Rendering review page")
    
    # Verifica che ci sia un file caricato
    if STATE_KEY not in st.session_state or st.session_state[STATE_KEY] is None:
        logger.warning("No file loaded in session state")
        st.info("Nessun file caricato. Vai alla pagina Caricamento.")
        return
    
    logger.debug(f"Working dataframe shape: {st.session_state[STATE_KEY].shape}")
    
    # Data editor - gestisce automaticamente lo stato tramite la key
    edited_df = st.data_editor(
        st.session_state[STATE_KEY],
        key=EDITOR_KEY,
        width="stretch",
        num_rows="dynamic",
        column_config=_editor_columns()
    )
    
    logger.debug(f"Edited dataframe shape: {edited_df.shape}")
    
    # Bottone di salvataggio
    if st.button("💾 Salva nel Database", type="primary"):
        try:
            logger.info("Save button clicked")
            
            # Usa edited_df direttamente (contiene le ultime modifiche)
            df_to_save = edited_df.copy()
            rows_before = len(df_to_save)
            
            # Rimuovi le righe marcate per eliminazione
            df_to_save = df_to_save[df_to_save["Da_Eliminare"] != True]
            removed_rows = rows_before - len(df_to_save)
            logger.info(f"Removed {removed_rows} rows marked for deletion")
            
            # Rimuovi duplicati rispetto allo storico
            final_df = core.remove_duplicates(df_to_save, history_df)
            logger.info(f"Deduplication completed: {len(final_df)} rows remaining")
            
            # Salva nel database
            save_history(final_df)
            logger.info("History saved successfully to database")
            
            # Feedback all'utente
            st.success("Database aggiornato con successo!")
            st.balloons()
            
            # Pulisci il session state dopo il salvataggio
            st.session_state[STATE_KEY] = None
            
        except Exception as e:
            logger.error(f"Error during save operation: {str(e)}", exc_info=True)
            st.error("Errore durante il salvataggio. Contattare l'amministratore.")


def _editor_columns():
    """Configurazione delle colonne per il data editor"""
    logger.debug("Building editor columns configuration")
    return {
        "Categoria_Approvata": st.column_config.CheckboxColumn("Approvata"),
        "Categoria": st.column_config.SelectboxColumn("Categoria", options=CATEGORIE),
        "Contesto": st.column_config.SelectboxColumn("Contesto", options=CONTESTI),
        "Richiede_Documento": st.column_config.CheckboxColumn("Richiede Documento", disabled=True),
        "Stato_Riconciliazione": st.column_config.SelectboxColumn(
            "Stato",
            options=["OK", "In Attesa Ricevuta", "Riconciliato"]
        ),
        "Da_Eliminare": st.column_config.CheckboxColumn("Elimina")
    }