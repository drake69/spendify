import pandas as pd
import os
from support.logging import setup_logging
from support.globals import DB_PATH, DEFAULT_CATEGORIES, DEFAULT_CONTEXTS

logger = setup_logging()


def load_history():
    try:
        if os.path.exists(DB_PATH):
            logger.info(f"Loading history from {DB_PATH}")
            df = pd.read_csv(DB_PATH)
            for col in ["Categoria_Approvata", "Richiede_Documento"]:
                if col in df.columns:
                    df[col] = df[col].astype(bool)
            logger.info(f"Successfully loaded {len(df)} records")
            return df
        logger.warning(f"Database file {DB_PATH} not found, returning empty DataFrame")
        return pd.DataFrame()
    except Exception as e:
        logger.error(f"Error loading history: {e}", exc_info=True)
        raise

def save_history(df):
    try:
        logger.info(f"Saving {len(df)} records to {DB_PATH}")
        df.to_csv(DB_PATH, index=False)
        logger.info("History saved successfully")
    except Exception as e:
        logger.error(f"Error saving history: {e}", exc_info=True)
        raise

def build_kb(history_df):
    if history_df.empty:
        return DEFAULT_CATEGORIES, DEFAULT_CONTEXTS
    return (
        history_df[history_df["Categoria_Approvata"] == True]
        .set_index("Descrizione")["Categoria"]
        .to_dict()
    )


def remove_duplicates(new_df, history_df):
    logger.info(f"Removing duplicates: {len(new_df)} new + {len(history_df)} history rows")
    if history_df.empty:
        logger.info("History empty, returning new_df as-is")
        return new_df
    combined = pd.concat([history_df, new_df], ignore_index=True)
    combined = combined.sort_values('Categoria_Approvata', ascending=False)
    before_count = len(combined)
    # Use actual column names from your dataframe
    dedup_cols = [col for col in ['Data_Operazione', 'Controparte','Entrata','Uscita'] if col in combined.columns]
    result = combined.drop_duplicates(subset=dedup_cols, keep='first') if dedup_cols else combined
    logger.info(f"Duplicates removed: {before_count - len(result)} rows")
    return result
