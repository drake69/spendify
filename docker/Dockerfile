# ── Spendify — Dockerfile ────────────────────────────────────────────────────
# Single-stage build con uv: evita problemi di symlink nei multi-stage copy.
# Build:  docker build -t spendify .
# Run:    docker run -p 8501:8501 --env-file .env spendify

# Pinned to 3.13 to match pyproject.toml `requires-python = ">=3.12,<3.14"`.
# The upper bound exists because the Linux .deb/.rpm bundle uses
# `--system-site-packages` against system PyGObject + pycairo, which on
# Ubuntu 24.04 / Fedora 42 are built against Python 3.12. Keeping the cap
# aligned across native bundles and the Docker image avoids drift.
FROM python:3.13-slim

# Build tools required by llama-cpp-python (compiled from source via scikit-build)
RUN apt-get update -qq && \
    apt-get install -y -qq --no-install-recommends gcc g++ cmake && \
    rm -rf /var/lib/apt/lists/*

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

# Entrypoint — uv run attiva automaticamente la venv e risolve il symlink Python
CMD ["uv", "run", "streamlit", "run", "app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--browser.gatherUsageStats=false"]
