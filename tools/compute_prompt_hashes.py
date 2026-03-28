#!/usr/bin/env python3
"""Compute SHA256 hashes of all prompt files for integrity verification (S-01).

Usage:
    python tools/compute_prompt_hashes.py          # Generate/update prompt_hashes.json
    python tools/compute_prompt_hashes.py --verify  # Verify current hashes match (CI/pre-commit)

Exit codes:
    0 — All hashes match (--verify) or hashes written successfully
    1 — Hash mismatch or unauthorized prompt file detected (--verify)
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
HASHES_FILE = PROMPTS_DIR / "prompt_hashes.json"


def _sha256(filepath: Path) -> str:
    """Compute SHA256 hex digest of a file."""
    h = hashlib.sha256()
    h.update(filepath.read_bytes())
    return h.hexdigest()


def _get_prompt_files() -> list[Path]:
    """Return all .json files in prompts/ except prompt_hashes.json."""
    return sorted(
        p for p in PROMPTS_DIR.glob("*.json")
        if p.name != "prompt_hashes.json"
    )


def compute_hashes() -> dict[str, str]:
    """Compute SHA256 for all prompt files."""
    return {p.name: _sha256(p) for p in _get_prompt_files()}


def write_hashes() -> None:
    """Compute and write prompt_hashes.json."""
    hashes = compute_hashes()
    with open(HASHES_FILE, "w", encoding="utf-8") as f:
        json.dump(hashes, f, indent=2, sort_keys=True)
        f.write("\n")
    print(f"Wrote {len(hashes)} hashes to {HASHES_FILE}")
    for name, h in sorted(hashes.items()):
        print(f"  {name}: {h[:16]}...")


def verify_hashes() -> bool:
    """Verify prompt files against stored hashes.

    Returns True if all pass, False if any mismatch.
    Prints detailed errors to stderr.
    """
    if not HASHES_FILE.exists():
        print("SKIP: prompt_hashes.json not found — integrity check disabled", file=sys.stderr)
        return True

    with open(HASHES_FILE, encoding="utf-8") as f:
        stored = json.load(f)

    current = compute_hashes()
    ok = True

    # Check for modified prompts
    for name, stored_hash in sorted(stored.items()):
        current_hash = current.get(name)
        if current_hash is None:
            print(f"MISSING: {name} listed in manifest but file not found", file=sys.stderr)
            ok = False
        elif current_hash != stored_hash:
            print(
                f"MODIFIED: {name}\n"
                f"  expected: {stored_hash}\n"
                f"  actual:   {current_hash}\n"
                f"  → Run: python tools/compute_prompt_hashes.py",
                file=sys.stderr,
            )
            ok = False

    # Check for unauthorized new prompts
    for name in sorted(current):
        if name not in stored:
            print(
                f"UNAUTHORIZED: {name} exists in prompts/ but is not in prompt_hashes.json\n"
                f"  → If intentional, run: python tools/compute_prompt_hashes.py\n"
                f"  → If not, remove the file.",
                file=sys.stderr,
            )
            ok = False

    if ok:
        print(f"OK: {len(stored)} prompt files verified")
    else:
        print(
            "\nFAILED: Prompt integrity check failed.\n"
            "If changes are intentional, regenerate hashes:\n"
            "  python tools/compute_prompt_hashes.py\n"
            "Then commit the updated prompt_hashes.json.",
            file=sys.stderr,
        )

    return ok


if __name__ == "__main__":
    if "--verify" in sys.argv:
        sys.exit(0 if verify_hashes() else 1)
    else:
        write_hashes()
