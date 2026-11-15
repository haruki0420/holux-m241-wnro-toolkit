#!/usr/bin/env python3
"""Patch a dumped M-241 image (WNRO + version marker).

Equivalent: ``m241 patch SRC -o DST``

Example: ``python scripts/patch_firmware.py backup/m241_stock.bin \\
    -o backup/m241_v114_wnro.bin``
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from m241.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["patch", *sys.argv[1:]]))
