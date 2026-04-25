#!/bin/bash
# ============================================================
#  Spendify — Launch (double-click in Finder)
# ============================================================
cd "$(dirname "$0")/../.." || exit 1
export PATH="$HOME/.local/bin:$PATH"
uv run streamlit run app.py --server.headless true
