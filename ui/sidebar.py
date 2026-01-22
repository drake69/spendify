import streamlit as st
import support.core_logic as core

def render_sidebar():
    st.sidebar.title("🏦 Menu Finanze")

    menu = st.sidebar.radio(
        "Naviga tra le sezioni:",
        [
            "📥 Caricamento",
            "📝 Revisione",
            "📊 Analisi & Budget",
            "🔍 Riconciliazione Ricevute",
            "📋 Registro Caricamenti"
        ]
    )

    st.sidebar.divider()
    st.sidebar.subheader("🤖 Configurazione AI")

    ai_mode = st.sidebar.selectbox(
        "Motore AI Fallback",
        ["Nessuno", "Ollama (Locale)", "OpenAI"]
    )

    api_key = (
        st.sidebar.text_input("OpenAI API Key", type="password")
        if ai_mode == "OpenAI"
        else None
    )

    st.sidebar.divider()
    st.sidebar.subheader("🎯 Budget Mensile")

    budgets = {
        cat: st.sidebar.number_input(f"Limite {cat} (€)", 0, 5000, 500, step=50)
        for cat in core.DEFAULT_CATEGORIES
    }

    return menu, ai_mode, api_key, budgets