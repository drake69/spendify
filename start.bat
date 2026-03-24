@echo off
:: ──────────────────────────────────────────────
:: Spendify — Startup script (Windows)
:: ──────────────────────────────────────────────
setlocal enabledelayedexpansion
cd /d "%~dp0"

:: ── Pre-flight checks ──────────────────────────

:: Python 3.13+
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python non trovato. Installa Python ^>= 3.13.
    exit /b 1
)
for /f "tokens=*" %%v in ('python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"') do set PY_VER=%%v
for /f "tokens=1,2 delims=." %%a in ("%PY_VER%") do (
    set PY_MAJOR=%%a
    set PY_MINOR=%%b
)
if %PY_MAJOR% lss 3 (
    echo [ERROR] Python ^>= 3.13 richiesto ^(trovato %PY_VER%^).
    exit /b 1
)
if %PY_MAJOR% equ 3 if %PY_MINOR% lss 13 (
    echo [ERROR] Python ^>= 3.13 richiesto ^(trovato %PY_VER%^).
    exit /b 1
)
echo [INFO]  Python %PY_VER% OK

:: uv
where uv >nul 2>&1
if errorlevel 1 (
    echo [WARN]  uv non trovato. Installazione in corso...
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    where uv >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Installazione di uv fallita. Installa manualmente: https://docs.astral.sh/uv/
        exit /b 1
    )
)
echo [INFO]  uv OK

:: ── Setup ───────────────────────────────────────

:: .env
if not exist .env (
    if exist .env.example (
        copy .env.example .env >nul
        echo [INFO]  File .env creato da .env.example
    ) else (
        echo [WARN]  File .env.example non trovato — procedo senza .env
    )
)

:: Dipendenze
echo [INFO]  Sincronizzazione dipendenze...
uv sync --quiet

:: Attivazione virtualenv
if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] Virtualenv non trovato in .venv. Esegui 'uv sync' manualmente.
    exit /b 1
)
call .venv\Scripts\activate.bat
echo [INFO]  Virtualenv attivato (.venv)

:: ── Avvio ───────────────────────────────────────

set MODE=%~1
if "%MODE%"=="" set MODE=ui

if "%MODE%"=="ui" (
    echo [INFO]  Avvio Streamlit UI su http://localhost:8501
    streamlit run app.py --server.headless true
    goto :eof
)

if "%MODE%"=="api" (
    echo [INFO]  Avvio API server su http://localhost:8000
    uvicorn api.main:app --host 0.0.0.0 --port 8000
    goto :eof
)

if "%MODE%"=="all" (
    echo [INFO]  Avvio UI + API...
    start "Spendify-API" cmd /c "call .venv\Scripts\activate.bat && uvicorn api.main:app --host 0.0.0.0 --port 8000"
    echo [INFO]  API avviata su http://localhost:8000
    echo [INFO]  Avvio Streamlit UI su http://localhost:8501
    streamlit run app.py --server.headless true
    goto :eof
)

echo Uso: %~nx0 [ui^|api^|all]
echo   ui   — Solo interfaccia Streamlit (default)
echo   api  — Solo server API REST
echo   all  — Entrambi
exit /b 1
