#!/usr/bin/env python3
"""Flash a patched image through the MTK download agent (DESTRUCTIVE).

Equivalent: ``m241 flash PORT --da DA --fw IMG --backup BACKUP``

Example: ``python scripts/flash_firmware.py auto --da vendor/DA_v3.8.4.bin \\
    --fw backup/m241_v114_wnro.bin --backup backup/m241_stock.bin --yes``
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from m241.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["flash", *sys.argv[1:]]))
