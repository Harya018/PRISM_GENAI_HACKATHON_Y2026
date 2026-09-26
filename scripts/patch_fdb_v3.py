#!/usr/bin/env python3
"""Applies this session's harness reliability patches (fdb-agent/patches/*.diff) to a fresh
Full-Duplex-Bench checkout, idempotently, so scripts/run_fdb_v3.sh reproduces on any machine
without depending on a manually-patched local clone.

Every patch here is harness reliability only — no scoring/benchmark logic changed. See
patches/README.md for what each one does and why. Applying the real diffs directly (rather than
re-implementing each fix as a separate hardcoded string replacement, as an earlier version of
this script did) keeps this in sync with the actual tested changes automatically — one source of
truth, not two copies that can drift apart.

Usage: python patch_fdb_v3.py /path/to/Full-Duplex-Bench   (the repo ROOT, not v3/ — the diffs
                                                             are rooted at v3/<file>)
"""
import subprocess
import sys
from pathlib import Path

PATCHES_DIR = Path(__file__).resolve().parent.parent / "patches"


def already_applied(repo_root: Path, diff_path: Path) -> bool:
    """git apply --check --reverse succeeding means this diff is already applied."""
    result = subprocess.run(
        ["git", "apply", "--check", "--reverse", str(diff_path)],
        cwd=str(repo_root), capture_output=True, text=True,
    )
    return result.returncode == 0


def main():
    repo_root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    diffs = sorted(PATCHES_DIR.glob("*.diff"))
    if not diffs:
        print(f"No .diff files found in {PATCHES_DIR}")
        return

    applied, skipped, failed = [], [], []
    for diff_path in diffs:
        if already_applied(repo_root, diff_path):
            skipped.append(diff_path.name)
            continue
        result = subprocess.run(
            ["git", "apply", str(diff_path)],
            cwd=str(repo_root), capture_output=True, text=True,
        )
        if result.returncode == 0:
            applied.append(diff_path.name)
        else:
            failed.append(diff_path.name)
            print(f"  [FAILED] {diff_path.name}: {result.stderr.strip()}")

    if applied:
        print("Applied: " + ", ".join(applied))
    if skipped:
        print("Already applied (skipped): " + ", ".join(skipped))
    if failed:
        print("FAILED to apply (check manually against patches/README.md): "
             + ", ".join(failed))
    if not applied and not skipped and not failed:
        print("Nothing to do.")


if __name__ == "__main__":
    main()
