@echo off
title AI Finance - Setup Ambiente
echo ====================================================
echo   Configurazione Ambiente Personal Finance AI
echo ====================================================

echo [1/4] Creazione ambiente virtuale (venv)...
python -m venv venv

echo [2/4] Attivazione ambiente e aggiornamento pip...
call venv\Scripts\activate
python -m pip install --upgrade pip

echo [3/4] Installazione librerie (questo potrebbe richiedere un minuto)...
pip install streamlit pandas pdfplumber thefuzz openai langchain_community xlsxwriter plotly pytest openpyxl

echo [4/4] Esecuzione test di integrita...
pytest test_finance.py

echo.
echo ====================================================
echo   SETUP COMPLETATO CON SUCCESSO!
echo ====================================================
echo.
echo Per avviare l'applicazione:
echo 1. Digita: call venv\Scripts\activate
echo 2. Digita: streamlit run app.py
echo.
pause