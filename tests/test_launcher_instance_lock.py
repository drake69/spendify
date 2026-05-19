"""Tests for the launcher's single-instance lock — orphan-sweep at startup.

Covers:
  - _pid_alive: liveness probe semantics
  - _kill_previous_instance: stale-PID file → cleaned, current-PID-only file
    → safely ignored, malformed JSON → file removed, no-file → no-op
  - _write_instance_lock / _clear_instance_lock: round-trip + idempotent clear
  - real-subprocess kill: spawn a child, record it, sweep it, verify dead

The launcher imports ``webview`` at module top, which is not available in the
CI test image. We stub it out before importing the launcher module.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

# Stub pywebview so `import desktop.launcher` works without the optional
# desktop extra installed in the test venv.
sys.modules.setdefault("webview", types.SimpleNamespace(
    create_window=lambda *a, **kw: None,
    start=lambda *a, **kw: None,
))

from desktop import launcher  # noqa: E402


@pytest.fixture
def lock_path(tmp_path, monkeypatch):
    """Redirect _INSTANCE_LOCK_FILE to a tmp path so tests can't clobber the
    real ~/.spendifai/launcher.lock of a running app."""
    p = tmp_path / "launcher.lock"
    monkeypatch.setattr(launcher, "_INSTANCE_LOCK_FILE", p)
    return p


def test_pid_alive_current_process():
    assert launcher._pid_alive(os.getpid()) is True


def test_pid_alive_zero_and_one_are_false():
    assert launcher._pid_alive(0) is False
    assert launcher._pid_alive(1) is False  # init/launchd — outside our scope


def test_pid_alive_nonexistent():
    # 2**31 - 1: nothing this high will exist as a real PID on any OS.
    assert launcher._pid_alive(2_147_483_647) is False


def test_kill_previous_instance_no_file_is_noop(lock_path):
    assert not lock_path.exists()
    launcher._kill_previous_instance()  # must not raise


def test_kill_previous_instance_malformed_json_removes_file(lock_path):
    lock_path.write_text("not-json{", encoding="utf-8")
    launcher._kill_previous_instance()
    assert not lock_path.exists()


def test_kill_previous_instance_with_dead_pids_removes_file(lock_path):
    lock_path.write_text(json.dumps({
        "launcher_pid": 2_147_483_646,  # nonexistent
        "child_pid": 2_147_483_647,     # nonexistent
    }), encoding="utf-8")
    launcher._kill_previous_instance()
    assert not lock_path.exists()


def test_kill_previous_instance_skips_self_pid(lock_path):
    """If the lock file somehow contains our own PID (paranoid case) we must
    not commit suicide. The file should still be removed."""
    lock_path.write_text(json.dumps({
        "launcher_pid": os.getpid(),
        "child_pid": os.getpid(),
    }), encoding="utf-8")
    launcher._kill_previous_instance()
    assert not lock_path.exists()


def test_write_then_clear_lock_roundtrip(lock_path):
    fake_proc = types.SimpleNamespace(pid=12345)
    launcher._write_instance_lock(fake_proc)
    assert lock_path.exists()
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    assert payload["launcher_pid"] == os.getpid()
    assert payload["child_pid"] == 12345
    assert "started_at" in payload

    launcher._clear_instance_lock()
    assert not lock_path.exists()
    # Second clear must be idempotent.
    launcher._clear_instance_lock()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only process-group semantics")
def test_kill_previous_instance_actually_kills_subprocess(lock_path):
    """End-to-end: spawn a real sleep subprocess, record its PID in the lock
    file, run _kill_previous_instance, verify the subprocess died and the
    lock file is gone."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        start_new_session=True,
    )
    try:
        # Sanity: the child is alive.
        assert launcher._pid_alive(proc.pid)
        lock_path.write_text(json.dumps({
            "launcher_pid": 2_147_483_646,
            "child_pid": proc.pid,
        }), encoding="utf-8")

        launcher._kill_previous_instance()

        # The subprocess.Popen handle holds the child as a zombie until
        # someone waits on it. _pid_alive(pid) would still return True for
        # the zombie even though the process is logically dead — in the real
        # bundle scenario the child is an orphan reparented to launchd which
        # reaps it. Here we wait() ourselves to reap, and verify wait()
        # returns promptly (would block on a still-running process).
        rc = proc.wait(timeout=5)
        assert rc is not None
        # SIGKILL exit code is -9 on Unix; SIGTERM is -15. Either proves we
        # actually signalled the process.
        assert rc in (-signal.SIGTERM, -signal.SIGKILL), f"unexpected rc={rc}"
        assert not lock_path.exists()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
