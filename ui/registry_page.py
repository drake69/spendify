import streamlit as st
import support.core_logic as core

def render_registry_page(history_df):
    st.header("📋 Storico Documenti Caricati")

    if history_df is None or history_df.empty:
        st.info("Nessun dato caricato nel registro.")
        return

    upload_log = core.get_upload_log(history_df)

    st.dataframe(
        upload_log,
        use_container_width=True,
        hide_index=True
    )

    st.divider()
    if st.checkbox("Mostra Database Raw (Tutte le transazioni)"):
        st.dataframe(history_df, use_container_width=True)