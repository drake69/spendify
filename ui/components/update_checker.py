"""Update checker component for Spendif.ai sidebar.

Checks ~/.spendifai/.update_available (written by the app launcher)
and shows a warning badge at the top of the sidebar if an update is available.
The check is non-blocking: if the file doesn't exist, silently skips.

The launcher script (packaging/macos/install.sh → MacOS/Spendif.ai) runs a
background ``git fetch`` every time the app opens and writes this file when the
local branch is behind origin.  Content example: "3 commits behind origin/main"

Usage:
    from ui.components.update_checker import render_update_warning
    render_update_warning()   # call at top of render_sidebar()
"""

from __future__ import annotations

import time
from pathlib import Path

import streamlit as st

# Path where the launcher writes the update flag
_UPDATE_FLAG: Path = Path.home() / ".spendifai" / ".update_available"

# How long (seconds) to cache the flag-file check between Streamlit reruns.
# Avoids hitting the filesystem on every rerun (Streamlit can rerun many times
# per second on interaction) while keeping the badge reasonably fresh.
_CACHE_TTL: float = 300.0   # 5 minutes

# Session-state keys
_SK_LAST_CHECK  = "_update_checker_last_check"
_SK_UPDATE_MSG  = "_update_checker_message"   # None → no update available


def _read_flag() -> str | None:
    """
    Read the update flag file and return its content, or None if absent.

    Returns:
        A non-empty string with the update description
        (e.g. "3 commits behind origin/main"), or None.
    """
    try:
        if _UPDATE_FLAG.is_file():
            content = _UPDATE_FLAG.read_text(encoding="utf-8").strip()
            return content if content else None
        return None
    except OSError:
        # Permission error, race condition with launcher, etc. — ignore silently.
        return None


def _refresh_cache() -> None:
    """
    Re-read the flag file and update session-state cache.
    Called when the cache TTL has expired.
    """
    st.session_state[_SK_LAST_CHECK] = time.time()
    st.session_state[_SK_UPDATE_MSG] = _read_flag()


def render_update_warning() -> None:
    """
    Render an update warning in the Streamlit sidebar if an update is available.

    Call this at the very top of ``render_sidebar()``, before any other widget.
    It is a no-op when no update flag is present.

    The function uses ``st.session_state`` to cache the flag-file check for
    ``_CACHE_TTL`` seconds, avoiding redundant filesystem reads on every rerun.
    """
    now = time.time()
    last_check: float = st.session_state.get(_SK_LAST_CHECK, 0.0)

    if (now - last_check) >= _CACHE_TTL:
        _refresh_cache()

    update_msg: str | None = st.session_state.get(_SK_UPDATE_MSG)

    if update_msg is None:
        # No update available — silent no-op
        return

    # Build the human-readable warning
    # update_msg is e.g. "3 commits behind origin/main"
    detail = f"({update_msg})" if update_msg else ""
    st.sidebar.warning(
        f"🔔 **Aggiornamento disponibile** {detail}\n\n"
        "Per aggiornare, esegui da Terminale:\n"
        "```\n"
        "bash ~/Applications/Spendif.ai/packaging/macos/install.sh --update\n"
        "```\n"
        "poi riavvia l'app.",
        icon=None,
    )
