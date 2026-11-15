#!/usr/bin/env python3
"""Dump the M-241 firmware over the BootROM. Equivalent: ``m241 dump PORT -o FILE``

Example: ``python scripts/dump_firmware.py auto -o backup/m241_stock.bin``
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from m241.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["dump", *sys.argv[1:]]))
