import streamlit as st
import logging
import pandas as pd
import support.core_logic as core
from services.cleaning_service import clean_dataframe

logger = logging.getLogger(__name__)

STATE_KEY = "working_df"

def render_upload_page():
    st.header("📥 Caricamento Nuovi Estratti Conto")
    logger.debug("Rendering upload page")
    
    uploaded_file = st.file_uploader(
        "Carica PDF, CSV o Excel",
        type=["pdf", "csv", "xls", "xlsx"]
    )
    iban_input = st.text_input("IBAN manuale")

    if STATE_KEY not in st.session_state:
        st.session_state[STATE_KEY] = None

    if uploaded_file and st.session_state[STATE_KEY] is None:
        logger.info(f"Processing upload: {uploaded_file.name}")
        try:
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            status_text.text("Caricamento file...")
            progress_bar.progress(20)
            df, iban = _load_file(uploaded_file, iban_input)
            logger.info(f"File loaded successfully: {len(df)} rows, IBAN: {iban}")
            
            status_text.text("Pulizia dati...")
            progress_bar.progress(50)
            df = clean_dataframe(df)
            logger.info(f"Data cleaned: {len(df)} rows remaining")
            
            status_text.text("Preparazione schema...")
            progress_bar.progress(75)
            df = _ensure_editor_schema(df, iban)
            logger.debug(f"Schema prepared with columns: {df.columns.tolist()}")
            
            progress_bar.progress(100)
            st.session_state[STATE_KEY] = df
            status_text.text("Completato!")
            
            st.success(f"File caricato con {len(df)} righe. Vai alla pagina Revisione per modificare.")
            logger.info(f"Upload completed: {len(df)} rows stored in session state")
        except Exception as e:
            st.error(f"Errore nel caricamento: {e}")
            logger.error(f"Error processing upload '{uploaded_file.name}'", exc_info=True)


def _load_file(uploaded_file, iban_input):
    logger.debug(f"Loading file: {uploaded_file.name}")
    name = uploaded_file.name.lower()
    try:
        if name.endswith(".pdf"):
            logger.debug("Extracting PDF file")
            iban, df = core.load_pdf(uploaded_file)
        elif name.endswith((".xls", ".xlsx")):
            logger.debug("Reading Excel file")
            df = pd.read_excel(uploaded_file)
            iban = iban_input or "EXCEL_IMPORT"
        elif name.endswith(".csv"):
            logger.debug("Reading CSV file")
            df = pd.read_csv(uploaded_file, sep=None, engine="python")
            iban = iban_input or "CSV_IMPORT"
        else:
            logger.error(f"Unsupported file format: {name}")
            raise ValueError("Formato file non supportato")
        logger.info(f"File loaded: {len(df)} rows, IBAN: {iban}")

        df = core.review_csv(df)
        # df = core.apply_description_smart_cleaning(df)
        # df = core.apply_enhanced_categorization(df, st.session_state.history_df)
        # df = core.categorize_with_llm(df)

        return df, iban
    except Exception as e:
        logger.error(f"Failed to load file: {uploaded_file.name}", exc_info=True)
        raise


def _ensure_editor_schema(df, iban):
    logger.debug(f"Ensuring editor schema for IBAN: {iban}")
    df = df.copy()
    df["IBAN"] = iban
    df["Categoria"] = df.get("Categoria", "Varie da Identificare")
    df["Contesto"] = df.get("Contesto", "Altro")
    df["Richiede_Documento"] = df.get("Richiede_Documento", False)
    df["Stato_Riconciliazione"] = df.get("Stato_Riconciliazione", "OK")
    df["Categoria_Approvata"] = True
    df["Da_Eliminare"] = False
    logger.debug(f"Schema complete with {len(df.columns)} columns")
    return df