import streamlit as st


def render_sidebar() -> str:
    """Render the sidebar and return the selected page key."""
    st.sidebar.title("🏦 Spendify")

    labels = {
        "📥 Import": "import",
        "📋 Ledger": "ledger",
        "📊 Analytics": "analytics",
        "🔍 Review": "review",
    }

    choice = st.sidebar.radio("Naviga", list(labels.keys()))

    st.sidebar.divider()
    st.sidebar.subheader("⚙️ Configurazione LLM")

    backend = st.sidebar.selectbox(
        "Backend LLM",
        ["local_ollama", "openai", "claude"],
        help="local_ollama = privacy-first (default)",
    )
    st.session_state["llm_backend"] = backend

    if backend == "openai":
        key = st.sidebar.text_input("OpenAI API Key", type="password",
                                    value=st.session_state.get("openai_api_key", ""))
        st.session_state["openai_api_key"] = key
    elif backend == "claude":
        key = st.sidebar.text_input("Anthropic API Key", type="password",
                                    value=st.session_state.get("anthropic_api_key", ""))
        st.session_state["anthropic_api_key"] = key

    st.sidebar.divider()
    st.sidebar.subheader("🔄 Modalità Giroconti")
    mode = st.sidebar.radio("Giroconti nel registro", ["neutral", "exclude"], index=0)
    st.session_state["giroconto_mode"] = mode

    return labels[choice]
