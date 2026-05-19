"""Spendif.ai — Native desktop launcher.

Starts a Streamlit server in a subprocess, shows a pywebview native window,
and auto-downloads the recommended LLM model on first run.

Usage (dev):
    cd sw_artifacts
    uv run python -m desktop.launcher

The same entry point is used by the PyInstaller-built .app / .exe.
"""
from __future__ import annotations

import sys

# ---------------------------------------------------------------------------
# Re-execution guard (PyInstaller bundle only)
# ---------------------------------------------------------------------------
# When frozen, `sys.executable` is the SpendifAi binary itself. If the
# launcher does `subprocess.Popen([sys.executable, "-m", "streamlit", ...])`
# the child re-enters launcher.py and would open a *second* pywebview
# window (and a third, fourth…). Detect the `-m <module>` invocation at the
# very top of the file — before any heavy import or log redirect — and
# forward to the requested module's CLI, then exit.
if getattr(sys, "frozen", False) and len(sys.argv) >= 3 and sys.argv[1] == "-m":
    _mod = sys.argv[2]
    # Re-shape argv so the dispatched module sees `argv = [<mod>, …rest]`.
    sys.argv = [_mod] + sys.argv[3:]
    if _mod == "streamlit":
        from streamlit.web.cli import main as _st_main
        _st_main()
    else:
        import runpy
        runpy.run_module(_mod, run_name="__main__", alter_sys=True)
    sys.exit(0)

import atexit
import os
import signal
import socket
import subprocess
import time
import traceback
from pathlib import Path
from threading import Thread

# ---------------------------------------------------------------------------
# Persistent logging — REDIRECT STDOUT/STDERR TO A FILE BEFORE ANY OTHER WORK
# ---------------------------------------------------------------------------
# Rationale: in the PyInstaller `--windowed` bundle (console=False) stdout
# and stderr are routed to /dev/null on macOS. Any crash before the webview
# window is shown is then completely invisible — no stack trace anywhere,
# no crash report (Python exceptions are not native crashes).
#
# We redirect both streams to a per-user log file BEFORE importing anything
# heavy that could blow up at import time (e.g. webview, streamlit deps),
# so even a `ModuleNotFoundError` or `ImportError` is captured. The file is
# truncated on every launch — it's a diagnostic log, not an audit trail.

_LOG_DIR = Path.home() / "Library" / "Logs" if sys.platform == "darwin" else Path.home() / ".spendifai"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_LOG_FILE = _LOG_DIR / "spendifai-launcher.log"

try:
    _log_fh = open(_LOG_FILE, "w", buffering=1, encoding="utf-8")  # line-buffered
    sys.stdout = _log_fh
    sys.stderr = _log_fh
    # First line so we can `tail -f` and verify the redirect worked.
    print(f"=== Spendif.ai launcher boot — {time.strftime('%Y-%m-%d %H:%M:%S')} ===", flush=True)
    print(f"argv: {sys.argv}", flush=True)
    print(f"executable: {sys.executable}", flush=True)
    print(f"frozen (PyInstaller): {getattr(sys, 'frozen', False)}", flush=True)
    print(f"_MEIPASS: {getattr(sys, '_MEIPASS', None)}", flush=True)
except Exception as _e:
    # If we can't even open the log file, write a marker beside the user home.
    # This is the last resort before truly silent failure.
    try:
        with open(Path.home() / "spendifai-launcher-bootstrap-error.txt", "w") as _e_fh:
            _e_fh.write(f"could not open log file: {_e}\n")
    except Exception:
        pass

try:
    import webview  # pywebview
    print(f"imported webview from: {getattr(webview, '__file__', '?')}", flush=True)
except Exception:
    print("FATAL: import webview failed", flush=True)
    traceback.print_exc()
    raise

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_SPENDIFAI_HOME = Path.home() / ".spendifai"


def _resolve_splash_html() -> Path:
    """Find splash.html across the two layouts.

    - Source mode: `desktop/launcher.py` next to `desktop/splash.html`.
    - PyInstaller bundle: launcher.py is the top-level script, so
      `Path(__file__).parent` collapses to `_MEIPASS` and splash.html is
      then under `_MEIPASS/desktop/splash.html`.
    """
    candidates = [
        Path(__file__).parent / "splash.html",                 # source / dev
        Path(getattr(sys, "_MEIPASS", "")) / "desktop" / "splash.html",  # bundle
        Path(__file__).parent / "desktop" / "splash.html",     # safety net
    ]
    for c in candidates:
        if c and c.exists():
            return c
    # If none exist we still return the first one so the rest of the code
    # can fail predictably with a clear log line rather than crashing.
    return candidates[0]


_SPLASH_HTML = _resolve_splash_html()


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
# First-run setup
# ---------------------------------------------------------------------------
# Split into two phases:
#   1. _bootstrap_env() — synchronous, super fast. Creates ~/.spendifai/,
#      writes a minimal .env so Streamlit can start (DB path + LLM backend
#      placeholder). Must run BEFORE Streamlit subprocess so app.py can
#      load_dotenv() without surprises.
#   2. _download_model_bg() — runs in a daemon thread. Downloads the
#      recommended GGUF, writes progress (with ETA) to a status file the
#      Streamlit UI polls, and updates .env with LLAMA_CPP_MODEL_PATH on
#      completion. The wizard / app keep running in parallel.

_MODEL_STATUS_FILE = _SPENDIFAI_HOME / "model_download.status"
_INSTANCE_LOCK_FILE = _SPENDIFAI_HOME / "launcher.lock"


# ---------------------------------------------------------------------------
# Single-instance lock — clean up orphaned subprocesses from a previous run
# ---------------------------------------------------------------------------
#
# The cleanup at window-close (_cleanup) handles the happy path. But on
# force-quit (⌘Q via Dock, Activity Monitor's Force Quit, SIGKILL, hard crash,
# or a pywebview.start() that never returns) atexit does not run and the
# Streamlit child — which lives in its own process group (start_new_session=
# True) — survives the launcher. Over time this accumulates several orphan
# instances holding the LLM in RAM each.
#
# Pattern: write a lock file with launcher PID + child PGID after spawning
# Streamlit; delete it in _cleanup. On startup, if the file is present, the
# previous shutdown was dirty → kill the recorded tree before continuing.


def _pid_alive(pid: int) -> bool:
    """True if pid refers to a live process. PID 0/1 always returns False."""
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but is owned by another user — counts as alive.
        return True


def _kill_pid_tree(pid: int) -> None:
    """Best-effort terminate of the process tree rooted at pid. Never raises."""
    if not _pid_alive(pid):
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, timeout=5,
            )
            return
        try:
            pgid = os.getpgid(pid)
        except ProcessLookupError:
            return
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return
        # Brief grace period then escalate.
        for _ in range(20):
            time.sleep(0.1)
            if not _pid_alive(pid):
                return
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    except Exception as exc:
        print(f"_kill_pid_tree: failed for pid={pid}: {exc!r}", flush=True)


def _kill_previous_instance() -> None:
    """If a lock file from a previous dirty shutdown is present, kill the
    recorded process tree(s) and remove the file. No-op on clean start."""
    if not _INSTANCE_LOCK_FILE.exists():
        return
    try:
        import json
        payload = json.loads(_INSTANCE_LOCK_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"_kill_previous_instance: lock file unreadable ({exc!r}) — removing", flush=True)
        try:
            _INSTANCE_LOCK_FILE.unlink()
        except OSError:
            pass
        return

    my_pid = os.getpid()
    for key in ("child_pid", "launcher_pid"):
        pid = int(payload.get(key) or 0)
        if pid and pid != my_pid:
            print(f"_kill_previous_instance: killing stale {key}={pid}", flush=True)
            _kill_pid_tree(pid)

    try:
        _INSTANCE_LOCK_FILE.unlink()
    except OSError:
        pass


def _write_instance_lock(child: subprocess.Popen) -> None:
    """Persist launcher PID + Streamlit child PID so the next launch can sweep
    them if this shutdown does not run _cleanup (force quit / crash)."""
    import json
    payload = {
        "launcher_pid": os.getpid(),
        "child_pid": child.pid,
        "started_at": datetime_now_iso(),
    }
    try:
        _INSTANCE_LOCK_FILE.write_text(json.dumps(payload), encoding="utf-8")
    except OSError as exc:
        print(f"_write_instance_lock: could not write lock ({exc!r})", flush=True)


def _clear_instance_lock() -> None:
    """Remove the lock file. Called at the end of _cleanup on a clean exit."""
    try:
        _INSTANCE_LOCK_FILE.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        print(f"_clear_instance_lock: could not remove lock ({exc!r})", flush=True)


def datetime_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _bootstrap_env(app_dir: Path) -> None:
    """Synchronous bootstrap — fast. Makes Streamlit startable immediately.

    The .env is written to ``~/.spendifai/.env`` (user-writable on every OS);
    on the Linux .deb / .rpm bundle ``app_dir`` is ``/opt/spendifai`` which is
    read-only. We also seed ``os.environ`` directly so the Streamlit
    subprocess inherits the values without needing to find the .env file.
    """
    _SPENDIFAI_HOME.mkdir(parents=True, exist_ok=True)
    app_str = str(app_dir)
    if app_str not in sys.path:
        sys.path.insert(0, app_str)

    defaults = {
        "LLM_BACKEND": "local_llama_cpp",
        "SPENDIFAI_DB": f"sqlite:///{_SPENDIFAI_HOME / 'ledger.db'}",
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)

    env_file = _SPENDIFAI_HOME / ".env"
    try:
        env_lines: list[str] = (
            env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
        )

        def _set_env(key: str, value: str) -> None:
            for i, line in enumerate(env_lines):
                if line.strip().startswith(f"{key}="):
                    env_lines[i] = f"{key}={value}"
                    return
            env_lines.append(f"{key}={value}")

        for key, value in defaults.items():
            _set_env(key, value)
        env_file.write_text("\n".join(env_lines) + "\n", encoding="utf-8")
        print(f"_bootstrap_env: wrote .env at {env_file}", flush=True)
    except OSError as exc:
        # Non-fatal: os.environ has already been seeded above.
        print(f"_bootstrap_env: could not persist .env ({exc}) — env vars set in-process", flush=True)


def _write_status(payload: dict) -> None:
    """Atomic write of the model-download status file (UI polls this)."""
    import json
    tmp = _MODEL_STATUS_FILE.with_suffix(".status.tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(_MODEL_STATUS_FILE)


def _download_model_bg(app_dir: Path) -> None:
    """Background thread: download the LLM model, update status + .env.

    Writes ``~/.spendifai/model_download.status`` continuously so the
    Streamlit UI can show a live ETA banner. On success, updates
    ``LLAMA_CPP_MODEL_PATH`` in .env. On failure, writes an error status.
    """
    import json
    start = time.monotonic()
    print("_download_model_bg: starting", flush=True)

    def progress_cb(pct: float, msg: str = "") -> None:
        elapsed = time.monotonic() - start
        eta_remaining = None
        # Estimate ETA only when pct is meaningful (≥2%) — earlier values
        # are dominated by HTTP setup time and would mislead the user.
        if pct >= 0.02 and pct < 1.0:
            eta_total = elapsed / pct
            eta_remaining = max(0, int(eta_total - elapsed))
        _write_status({
            "pct": round(pct, 4),
            "msg": msg or "Scaricando il modello AI...",
            "elapsed_s": int(elapsed),
            "eta_remaining_s": eta_remaining,
            "done": False,
            "error": None,
            "ts": time.time(),
        })

    # Wrap _on_progress with a Streamlit-side message we control
    def _on_progress(pct: float, msg: str = "") -> None:
        progress_cb(pct, msg)

    try:
        # Ensure imports work — the launcher's _bootstrap_env added app_dir to sys.path
        from core.model_manager import ensure_model_available  # noqa: E402
        _on_progress(0.0, "Avvio download modello AI...")
        model_path = ensure_model_available(progress_callback=_on_progress)
        if not model_path:
            _write_status({
                "pct": 0.0, "done": False, "error": "ensure_model_available returned None",
                "msg": "Download fallito", "ts": time.time(),
            })
            print("_download_model_bg: ensure_model_available returned None", flush=True)
            return

        # Update os.environ (so the running Streamlit subprocess sees it) and
        # persist into ~/.spendifai/.env (user-writable on every OS). On the
        # Linux .deb / .rpm bundle ``app_dir`` is ``/opt/spendifai`` which is
        # read-only, so we never write there.
        os.environ["LLAMA_CPP_MODEL_PATH"] = model_path
        env_file = _SPENDIFAI_HOME / ".env"
        try:
            lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
            replaced = False
            for i, line in enumerate(lines):
                if line.strip().startswith("LLAMA_CPP_MODEL_PATH="):
                    lines[i] = f"LLAMA_CPP_MODEL_PATH={model_path}"
                    replaced = True
                    break
            if not replaced:
                lines.append(f"LLAMA_CPP_MODEL_PATH={model_path}")
            env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"_download_model_bg: could not persist LLAMA_CPP_MODEL_PATH to .env ({exc})", flush=True)

        _write_status({
            "pct": 1.0,
            "msg": "Modello AI pronto",
            "elapsed_s": int(time.monotonic() - start),
            "eta_remaining_s": 0,
            "done": True,
            "error": None,
            "model_path": model_path,
            "ts": time.time(),
        })
        print(f"_download_model_bg: complete, model at {model_path}", flush=True)

    except Exception as exc:
        print(f"_download_model_bg: FAILED — {exc!r}", flush=True)
        traceback.print_exc()
        _write_status({
            "pct": 0.0, "done": False, "error": repr(exc),
            "msg": f"Errore download: {exc}", "ts": time.time(),
        })


# ---------------------------------------------------------------------------
# Subprocess management
# ---------------------------------------------------------------------------

_procs: list[subprocess.Popen] = []


def _start_streamlit(port: int, app_dir: Path) -> subprocess.Popen:
    """Launch ``streamlit run app.py`` in a child process.

    The child is started in its own process group (``start_new_session``)
    so we can later kill the entire tree — including uvicorn workers,
    Streamlit script reruns and any pywebview helper — with one signal
    via ``os.killpg``. Without this, closing the pywebview window leaves
    Streamlit running, which looks to the user like the app "reopens on
    its own" the next time they double-click the bundle.
    """
    cmd = [
        sys.executable, "-m", "streamlit", "run",
        str(app_dir / "app.py"),
        "--global.developmentMode=false",
        "--server.port", str(port),
        "--server.headless", "true",
        "--server.fileWatcherType", "none",
        "--browser.gatherUsageStats", "false",
    ]
    env = os.environ.copy()
    env["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"
    env["STREAMLIT_GLOBAL_DEVELOPMENT_MODE"] = "false"

    popen_kwargs = {"env": env, "cwd": str(app_dir)}
    if sys.platform != "win32":
        # Detach into a new session so the child + its descendants form
        # their own process group, killable with one signal.
        popen_kwargs["start_new_session"] = True
    else:
        # On Windows, use CREATE_NEW_PROCESS_GROUP for the same purpose.
        popen_kwargs["creationflags"] = getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )

    proc = subprocess.Popen(cmd, **popen_kwargs)
    _procs.append(proc)
    return proc


def _cleanup() -> None:
    """Terminate every child process tree we spawned.

    Streamlit itself spawns descendants (uvicorn worker, script subprocess,
    pywebview helpers depending on backend). A plain ``proc.terminate()``
    only signals the immediate child; the grandchildren survive and keep
    holding the port + the model file open, which on macOS makes the
    application "reopen" on the next launch because Launch Services thinks
    the previous instance is still running.

    Solution: kill the entire process group of each tracked Popen.
    """
    print("_cleanup: terminating subprocesses…", flush=True)
    for p in _procs:
        if p.poll() is not None:
            continue
        try:
            if sys.platform != "win32":
                # Send SIGTERM to the whole process group; falls back to
                # plain terminate() if for some reason killpg fails.
                try:
                    pgid = os.getpgid(p.pid)
                    os.killpg(pgid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError, OSError):
                    p.terminate()
            else:
                p.terminate()
        except Exception as exc:
            print(f"_cleanup: terminate failed for pid={p.pid}: {exc!r}", flush=True)

    for p in _procs:
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                if sys.platform != "win32":
                    try:
                        os.killpg(os.getpgid(p.pid), signal.SIGKILL)
                    except (ProcessLookupError, PermissionError, OSError):
                        p.kill()
                else:
                    p.kill()
            except Exception as exc:
                print(f"_cleanup: kill failed for pid={p.pid}: {exc!r}", flush=True)
    _clear_instance_lock()
    print("_cleanup: done", flush=True)


atexit.register(_cleanup)

if sys.platform != "win32":
    # Both SIGTERM (kill, Activity Monitor force-quit) and SIGINT (Ctrl+C
    # in dev) trigger a clean exit that goes through atexit → _cleanup.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("main(): checking for stale instance lock...", flush=True)
    _SPENDIFAI_HOME.mkdir(parents=True, exist_ok=True)
    _kill_previous_instance()

    print("main(): resolving app_dir...", flush=True)
    app_dir = _resolve_app_dir()
    print(f"main(): app_dir = {app_dir}", flush=True)

    port = _get_free_port()
    print(f"main(): free port = {port}", flush=True)

    print(f"main(): splash HTML = {_SPLASH_HTML} (exists: {_SPLASH_HTML.exists()})", flush=True)

    # Create the native window showing the splash screen.
    # `maximized=True` makes it fill the available screen area on every OS
    # (minus the menu bar / dock) so even on small laptops the wizard's
    # "Next" button is always visible without scrolling. Streamlit's
    # default page layout is tall — at 900 px the action row was below
    # the fold on a 16" MacBook Pro at native scaling.
    window = webview.create_window(
        title="Spendif.ai",
        url=str(_SPLASH_HTML),
        width=1400,
        height=900,
        min_size=(1024, 700),
        maximized=True,
    )
    print("main(): pywebview window created", flush=True)

    def _on_shown() -> None:
        """Background thread: bootstrap → Streamlit + model download in parallel."""
        try:
            # 1. Synchronous bootstrap — fast (.env, sys.path). Required before
            #    Streamlit starts so app.py can resolve LLM_BACKEND and DB path.
            print("_on_shown: bootstrap_env...", flush=True)
            _bootstrap_env(app_dir)
            print("_on_shown: bootstrap_env done", flush=True)

            # 2. Pre-mark status file: lets the UI banner know a download is
            #    coming, even before the bg thread has computed any progress.
            #    We only set this if the model is NOT already on disk —
            #    otherwise the banner would flash uselessly.
            from core.model_manager import MODELS_DIR  # imported via sys.path

            already_have_a_model = any(MODELS_DIR.glob("*.gguf")) if MODELS_DIR.exists() else False
            if not already_have_a_model:
                _write_status({
                    "pct": 0.0,
                    "msg": "Preparazione download modello AI...",
                    "elapsed_s": 0,
                    "eta_remaining_s": None,
                    "done": False,
                    "error": None,
                    "ts": time.time(),
                })
                print("_on_shown: spawning model download thread", flush=True)
                Thread(target=_download_model_bg, args=(app_dir,), daemon=True).start()
            else:
                print("_on_shown: model already present — skipping download", flush=True)
                # If the model is present we still want LLAMA_CPP_MODEL_PATH in
                # .env (in case it was removed). Reuse the bg routine but mark
                # done immediately so the banner doesn't appear.
                Thread(target=_download_model_bg, args=(app_dir,), daemon=True).start()

            # 3. Start Streamlit immediately — it can render the wizard while
            #    the model downloads in the background. The wizard does not
            #    need the LLM, only the Import page does.
            print("_on_shown: starting Streamlit...", flush=True)
            _streamlit_proc = _start_streamlit(port, app_dir)
            _write_instance_lock(_streamlit_proc)

            print(f"_on_shown: waiting for port {port}...", flush=True)
            if _wait_for_port(port):
                print(f"_on_shown: Streamlit ready, navigating window", flush=True)
                # Hook the post-navigation event ONCE: as soon as Streamlit's
                # initial HTML lands, inject a branded overlay on top of it
                # and poll for the wizard root. This hides Streamlit's own
                # "Please wait, running script" intermediate state so the
                # user never sees the "double splash" jank (splash.html →
                # blank Streamlit shell → wizard).
                _injected = {"done": False}

                def _on_loaded() -> None:
                    if _injected["done"]:
                        return
                    _injected["done"] = True
                    window.evaluate_js("""
                        (function() {
                            if (document.getElementById('__sai_overlay')) return;
                            const o = document.createElement('div');
                            o.id = '__sai_overlay';
                            o.style.cssText = 'position:fixed;inset:0;background:#0e1117;z-index:2147483647;display:flex;flex-direction:column;align-items:center;justify-content:center;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#fafafa;transition:opacity .4s;';
                            o.innerHTML = '<h1 style="margin:0 0 8px;font-size:2.4rem;font-weight:700;">Spendif.ai</h1><p style="margin:0;opacity:.7;">Caricamento dell\\'interfaccia…</p>';
                            document.body.appendChild(o);
                            // Poll for Streamlit's app root to acquire real children
                            // (the first script render). Cap at 15 s as a safety net.
                            let attempts = 0;
                            const t = setInterval(function() {
                                attempts++;
                                const app = document.querySelector('[data-testid="stApp"]') || document.querySelector('.stApp');
                                if (app && app.querySelectorAll('h1, h2, h3, button, [data-testid="stMarkdown"]').length > 0) {
                                    clearInterval(t);
                                    o.style.opacity = '0';
                                    setTimeout(function() { o.remove(); }, 450);
                                } else if (attempts > 75) {
                                    clearInterval(t);
                                    o.remove();
                                }
                            }, 200);
                        })();
                    """)

                try:
                    window.events.loaded += _on_loaded
                except Exception as exc:
                    # Older pywebview versions may not expose `.events`; fall
                    # back gracefully — UX gets the legacy double-flash but
                    # the app still works.
                    print(f"_on_shown: overlay hook unavailable: {exc!r}", flush=True)
                window.load_url(f"http://127.0.0.1:{port}")
            else:
                print("_on_shown: Streamlit did not respond within 30s", flush=True)
                window.load_html(
                    "<html><body style='font-family:sans-serif;padding:2em'>"
                    "<h2>Startup failed</h2>"
                    "<p>Streamlit did not respond within 30 seconds.</p>"
                    f"<p>See <code>{_LOG_FILE}</code> for details.</p>"
                    "</body></html>"
                )
        except Exception:
            print("_on_shown: UNHANDLED EXCEPTION", flush=True)
            traceback.print_exc()
            try:
                window.load_html(
                    "<html><body style='font-family:sans-serif;padding:2em'>"
                    "<h2>Startup error</h2>"
                    "<p>An exception occurred during setup.</p>"
                    f"<p>See <code>{_LOG_FILE}</code> for the full trace.</p>"
                    "</body></html>"
                )
            except Exception:
                pass

    # webview.start() blocks until the window is closed.
    # _on_shown runs in a background thread after the window is visible.
    print("main(): calling webview.start()...", flush=True)
    webview.start(_on_shown, debug=("--debug" in sys.argv))
    print("main(): webview.start() returned (window closed)", flush=True)

    # Window closed → clean up
    _cleanup()
    print("main(): cleanup done, exiting", flush=True)


def _entrypoint() -> None:
    """Top-level entry: catch any uncaught exception and log it."""
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        print("=== TOP-LEVEL UNHANDLED EXCEPTION ===", flush=True)
        traceback.print_exc()
        raise


if __name__ == "__main__":
    _entrypoint()
