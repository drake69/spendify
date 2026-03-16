# ── Spendify — Dockerfile ────────────────────────────────────────────────────
# Multi-stage: installa dipendenze con uv, poi copia solo il necessario.
# Build:  docker build -t spendify .
# Run:    docker run -p 8501:8501 --env-file .env spendify

# ── Stage 1: dependency resolver ─────────────────────────────────────────────
FROM python:3.13-slim AS builder

# Installa uv (package manager veloce)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /build

# Copia solo i file di dipendenze per sfruttare la cache Docker
COPY pyproject.toml uv.lock ./

# Installa dipendenze in una venv isolata (senza dev/test)
RUN uv sync --frozen --no-dev --no-install-project

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.13-slim AS runtime

WORKDIR /app

# Copia la venv già risolta
COPY --from=builder /build/.venv /app/.venv

# Copia il codice sorgente
COPY . .

# Assicura che la venv sia usata per tutti i comandi
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Directory dove il DB e i log verranno montati come volume
VOLUME ["/app/data", "/app/logs"]

# Porta Streamlit
EXPOSE 8501

# Healthcheck: verifica che Streamlit risponda
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')" || exit 1

# Entrypoint
CMD ["streamlit", "run", "app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--browser.gatherUsageStats=false"]
