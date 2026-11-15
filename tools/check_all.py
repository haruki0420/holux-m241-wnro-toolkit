"""Run every repository gate and print one line per gate.

Replaces running `pytest`, `ruff` and `check_docs.py` separately. Each gate is
run as a subprocess so a failure in one does not hide the others, and the whole
thing is fast enough to run before every commit.

Usage:
    python tools/check_all.py
    python tools/check_all.py --quiet     # only the summary

Exit code is 0 only when every gate passes.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def run(name: str, command: list[str], *, quiet: bool) -> tuple[str, bool, float, str]:
    """Run one gate; return (name, ok, seconds, first line of failure)."""
    started = time.monotonic()
    result = subprocess.run(
        command,
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    elapsed = time.monotonic() - started
    ok = result.returncode == 0
    detail = ""
    if not ok:
        combined = (result.stdout or "") + (result.stderr or "")
        lines = [line for line in combined.splitlines() if line.strip()]
        detail = lines[-1][:120] if lines else "(no output)"
    elif not quiet:
        combined = (result.stdout or "") + (result.stderr or "")
        lines = [line for line in combined.splitlines() if line.strip()]
        detail = lines[-1][:120] if lines else ""
    return name, ok, elapsed, detail


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true", help="summary only")
    args = parser.parse_args()

    gates = [
        (
            "lint",
            [sys.executable, "-m", "ruff", "check", "src", "tests", "tools"],
        ),
        (
            "docs",
            [sys.executable, "tools/check_docs.py"],
        ),
        (
            "tests",
            [sys.executable, "-m", "pytest", "-q", "--no-header"],
        ),
    ]

    print(f"check_all: {len(gates)} gates in {REPO}")
    failed = 0
    total = 0.0
    for name, command in gates:
        gate_name, ok, elapsed, detail = run(name, command, quiet=args.quiet)
        total += elapsed
        mark = "PASS" if ok else "FAIL"
        line = f"  [{mark}] {gate_name:<6} {elapsed:6.1f}s"
        if detail:
            line += f"  {detail}"
        print(line, flush=True)
        if not ok:
            failed += 1

    print(f"check_all: {len(gates) - failed}/{len(gates)} passed in {total:.1f}s")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
