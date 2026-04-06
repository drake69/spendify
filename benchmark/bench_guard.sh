#!/usr/bin/env bash
# bench_guard.sh — Version gate for benchmark sessions.
#
# Rules:
#   • git repo detected  → regenerate benchmark/.version = YYYYMMDDHHMMSS-<sha7>
#                          (dev machine: always fresh, each run gets a unique version)
#   • no git             → benchmark/.version must already exist
#                          (remote bench machine: version was written by bench_push_usb/ssh)
#   • no git + no file   → fatal error with actionable hint
#
# Usage (from repo root):
#   SW_VERSION=$(bash benchmark/bench_guard.sh) || exit 1
#
# Output: the version string on stdout (e.g. "20260406001942-c24bb62")
# Stderr: diagnostic messages

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VERSION_FILE="$SCRIPT_DIR/.version"

if git -C "$SCRIPT_DIR" rev-parse --short HEAD &>/dev/null; then
    # ── dev machine: git available → regenerate ────────────────────────────
    _SHA=$(git -C "$SCRIPT_DIR" rev-parse --short HEAD)
    _TS=$(date '+%Y%m%d%H%M%S')
    _VERSION="${_TS}-${_SHA}"
    echo "${_VERSION}" > "$VERSION_FILE"
    echo "[bench_guard] git detected → version regenerated: ${_VERSION}" >&2
    echo "${_VERSION}"
elif [ -f "$VERSION_FILE" ]; then
    # ── remote machine: no git, but .version exists (written by bench_push) ─
    _VERSION=$(cat "$VERSION_FILE" | tr -d '[:space:]')
    echo "[bench_guard] no git → using existing version: ${_VERSION}" >&2
    echo "${_VERSION}"
else
    # ── fatal: no git AND no .version ─────────────────────────────────────
    echo "" >&2
    echo "╔══════════════════════════════════════════════════════════════════╗" >&2
    echo "║  bench_guard ERROR: cannot determine benchmark version           ║" >&2
    echo "╠══════════════════════════════════════════════════════════════════╣" >&2
    echo "║  • No git repository found in $SCRIPT_DIR" >&2
    echo "║  • No benchmark/.version file found                             ║" >&2
    echo "╠══════════════════════════════════════════════════════════════════╣" >&2
    echo "║  On a remote bench machine, deploy first with one of:           ║" >&2
    echo "║    bash benchmark/bench_push_usb.sh --dest /Volumes/BENCH_USB   ║" >&2
    echo "║    bash benchmark/bench_push_ssh.sh --to user@host:/path        ║" >&2
    echo "║  These scripts write benchmark/.version automatically.          ║" >&2
    echo "╚══════════════════════════════════════════════════════════════════╝" >&2
    exit 1
fi
