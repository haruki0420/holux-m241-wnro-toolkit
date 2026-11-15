"""Diagnose why `m241 info` does not see the PMTK705 reply.

Runs the real CLI collector against the device with several timeouts and prints
what it actually received. Read-only.

Usage: python tools/diag_info.py COM3
"""

from __future__ import annotations

import sys

sys.path.insert(0, "src")

from m241.cli import _nmea_collect  # noqa: E402
from m241.nmea import with_checksum  # noqa: E402
from m241.protocol.ports import open_serial  # noqa: E402


def run(port: str, timeout: float) -> list[str]:
    serial_port = open_serial(port, 38400, timeout=0.2)
    try:
        lines = _nmea_collect(
            serial_port,
            [with_checksum("PMTK605")],
            stop_prefix="$PMTK705",
            timeout=timeout,
        )
    finally:
        serial_port.close()
    return lines


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "COM3"
    for timeout in (1.0, 2.0, 3.0, 5.0):
        try:
            lines = run(port, timeout)
        except Exception as exc:  # noqa: BLE001
            print(f"timeout={timeout}: ERROR {exc}")
            continue
        reply = [line for line in lines if line.startswith("$PMTK705")]
        print(f"timeout={timeout}: {len(lines)} line(s), PMTK705 -> {reply}")
        if not reply and lines:
            for line in lines[:8]:
                print(f"    {line!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
