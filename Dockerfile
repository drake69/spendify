# ── Spendify — Dockerfile ────────────────────────────────────────────────────
# Single-stage build con uv: evita problemi di symlink nei multi-stage copy.
# Build:  docker build -t spendify .
# Run:    docker run -p 8501:8501 --env-file .env spendify

FROM python:3.13-slim

# Installa uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Non scaricare Python separato: usa quello di sistema già presente nell'image
ENV UV_PYTHON_DOWNLOADS=0
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Copia solo i file di dipendenze per sfruttare la cache Docker
COPY pyproject.toml uv.lock ./

# Installa dipendenze nella venv (senza dev/test)
RUN uv sync --frozen --no-dev --no-install-project

# Copia il codice sorgente
COPY . .

# Aggiungi la venv al PATH
ENV PATH="/app/.venv/bin:$PATH"

# Directory dove il DB e i log verranno montati come volume
VOLUME ["/app/data", "/app/logs"]

# Porta Streamlit
EXPOSE 8501

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')" || exit 1

# Entrypoint
CMD ["python", "-m", "streamlit", "run", "app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--browser.gatherUsageStats=false"]
