#!/usr/bin/env python3
"""Run the m241 toolkit from a source checkout. Equivalent: ``m241 <args>``

Example: ``python scripts/m241.py selftest``
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from m241.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
