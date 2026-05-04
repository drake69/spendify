"""Spendif.ai — Native desktop launcher.

Starts a Streamlit server in a subprocess, shows a pywebview native window,
and auto-downloads the recommended LLM model on first run.

Usage (dev):
    cd sw_artifacts
    uv run python -m desktop.launcher

The same entry point is used by the PyInstaller-built .app / .exe.
"""
from __future__ import annotations

import atexit
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from threading import Thread

import webview  # pywebview

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_SPENDIFAI_HOME = Path.home() / ".spendifai"
_SPLASH_HTML = Path(__file__).parent / "splash.html"


def _resolve_app_dir() -> Path:
    """Find the directory containing app.py.

    Order: PyInstaller bundle → parent of desktop/ → known install dirs.
    """
    # PyInstaller sets sys._MEIPASS in --onedir / --onefile mode
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate = Path(meipass)
        if (candidate / "app.py").exists():
            return candidate

    # Running from source: desktop/ is a child of the repo root
    repo_root = Path(__file__).resolve().parent.parent
    if (repo_root / "app.py").exists():
        return repo_root

    # Fallback: known install locations
    for p in [
        _SPENDIFAI_HOME / "repo",
        Path.home() / "Applications" / "Spendif.ai",
    ]:
        if (p / "app.py").exists():
            return p

    # Last resort: cwd
    return Path.cwd()


# ---------------------------------------------------------------------------
# Network helpers
# ---------------------------------------------------------------------------

def _get_free_port() -> int:
    """Bind to port 0 and return the OS-assigned port number."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, timeout: float = 30.0) -> bool:
    """Poll until a TCP connection to *port* succeeds or *timeout* expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.2)
    return False


# ---------------------------------------------------------------------------
# First-run setup: model download + .env configuration
# ---------------------------------------------------------------------------

def _ensure_first_run_setup(
    app_dir: Path,
    window: webview.Window | None = None,
) -> None:
    """Download the recommended LLM model and write .env if needed.

    On first launch ``~/.spendifai/models/`` is empty; this function calls
    the existing ``ensure_model_available()`` which downloads the best GGUF
    model for the detected hardware.  Progress is relayed to the splash
    screen via the pywebview JS bridge.
    """
    # Make sure the home dir exists
    _SPENDIFAI_HOME.mkdir(parents=True, exist_ok=True)

    # Add app_dir to sys.path so we can import core/config modules
    app_str = str(app_dir)
    if app_str not in sys.path:
        sys.path.insert(0, app_str)

    def _js(call: str) -> None:
        if window:
            try:
                window.evaluate_js(call)
            except Exception:
                pass

    # Import from the app itself
    from core.model_manager import ensure_model_available  # noqa: E402

    def _on_progress(pct: float, msg: str) -> None:
        _js(f"updateStatus({msg!r})")
        _js(f"updateProgress({pct})")

    _js("updateStatus('Checking AI model...')")
    model_path = ensure_model_available(progress_callback=_on_progress)

    # Write / update .env so the Streamlit app uses llama.cpp by default
    env_file = app_dir / ".env"
    env_lines: list[str] = []
    if env_file.exists():
        env_lines = env_file.read_text(encoding="utf-8").splitlines()

    def _set_env(key: str, value: str) -> None:
        for i, line in enumerate(env_lines):
            if line.strip().startswith(f"{key}="):
                env_lines[i] = f"{key}={value}"
                return
        env_lines.append(f"{key}={value}")

    _set_env("LLM_BACKEND", "local_llama_cpp")
    if model_path:
        _set_env("LLAMA_CPP_MODEL_PATH", model_path)
    _set_env(
        "SPENDIFAI_DB",
        f"sqlite:///{_SPENDIFAI_HOME / 'ledger.db'}",
    )

    env_file.write_text("\n".join(env_lines) + "\n", encoding="utf-8")

    _js("hideProgress()")
    _js("updateStatus('Starting application...')")


# ---------------------------------------------------------------------------
# Subprocess management
# ---------------------------------------------------------------------------

_procs: list[subprocess.Popen] = []


def _start_streamlit(port: int, app_dir: Path) -> subprocess.Popen:
    """Launch ``streamlit run app.py`` in a child process."""
    cmd = [
        sys.executable, "-m", "streamlit", "run",
        str(app_dir / "app.py"),
        "--server.port", str(port),
        "--server.headless", "true",
        "--server.fileWatcherType", "none",
        "--browser.gatherUsageStats", "false",
    ]
    env = os.environ.copy()
    env["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"

    proc = subprocess.Popen(cmd, env=env, cwd=str(app_dir))
    _procs.append(proc)
    return proc


def _cleanup() -> None:
    """Terminate all child processes (called on exit)."""
    for p in _procs:
        if p.poll() is None:
            p.terminate()
    for p in _procs:
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()


atexit.register(_cleanup)

if sys.platform != "win32":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    app_dir = _resolve_app_dir()
    port = _get_free_port()

    # Create the native window showing the splash screen
    window = webview.create_window(
        title="Spendif.ai",
        url=str(_SPLASH_HTML),
        width=1400,
        height=900,
        min_size=(1024, 700),
    )

    def _on_shown() -> None:
        """Background thread: setup → start Streamlit → navigate."""
        # 1. First-run setup (model download, .env)
        _ensure_first_run_setup(app_dir, window)

        # 2. Start Streamlit
        _start_streamlit(port, app_dir)

        # 3. Wait for Streamlit to become ready
        if _wait_for_port(port):
            window.load_url(f"http://127.0.0.1:{port}")
        else:
            window.load_html(
                "<html><body style='font-family:sans-serif;padding:2em'>"
                "<h2>Startup failed</h2>"
                "<p>Streamlit did not respond within 30 seconds.</p>"
                "<p>Try restarting the application.</p>"
                "</body></html>"
            )

    # webview.start() blocks until the window is closed.
    # _on_shown runs in a background thread after the window is visible.
    webview.start(_on_shown, debug=("--debug" in sys.argv))

    # Window closed → clean up
    _cleanup()
