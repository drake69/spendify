#!/bin/bash
echo "🚀 Avvio installazione AI Finance..."
python3 -m venv venv
source venv/bin/activate
pip install streamlit pandas pdfplumber thefuzz openai langchain_community xlsxwriter plotly pytest openpyxl
echo "✅ Installazione completata."
echo "Esegui 'source venv/bin/activate && streamlit run app.py' per iniziare."