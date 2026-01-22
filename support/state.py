import streamlit as st
from support.logging import setup_logging

logger = setup_logging()

def init_state():
    defaults = {
        "working_df": None,
        "history_df": None,
        "ai_mode": "Nessuno",
        "api_key": None
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value
            logger.info(f"Initialized session state: {key} = {value}")
        else:
            logger.debug(f"Session state key already exists: {key}")
    
    logger.info("Session state initialization complete")