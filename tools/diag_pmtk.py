"""Compare PMTK605 framing variants against the real device.

Checks whether the trailing CRLF matters, and whether a baud settle delay is
enough. Read-only.

Usage: python tools/diag_pmtk.py COM3
"""

from __future__ import annotations

import sys
import time

import serial

DOLLAR = chr(36)  # '$'


def probe(payload: bytes, label: str, *, settle: float = 0.3, wait: float = 1.5) -> None:
    with serial.Serial("COM3" if len(sys.argv) < 2 else sys.argv[1], 38400, timeout=0.2) as ser:
        time.sleep(settle)
        ser.reset_input_buffer()
        ser.write(payload)
        ser.flush()
        got = b""
        deadline = time.time() + wait
        while time.time() < deadline:
            chunk = ser.read(4096)
            if chunk:
                got += chunk
                deadline = time.time() + 0.3
    reply = [line for line in got.split(b"\r\n") if line.startswith(f"{DOLLAR}PMTK705".encode())]
    print(f"{label:<22} reply={reply}")
    if not reply:
        print(f"{'':<22} raw head={got[:100]!r}")


def main() -> int:
    print("PMTK605 framing variants:")
    probe(f"{DOLLAR}PMTK605*31\r\n".encode(), "CRLF")
    time.sleep(0.5)
    probe(f"{DOLLAR}PMTK605*31".encode(), "no CRLF")
    time.sleep(0.5)
    probe(f"{DOLLAR}PMTK000*32\r\n".encode(), "PMTK000 liveness")
    time.sleep(0.5)
    probe(f"{DOLLAR}PMTK605*31\r\n".encode(), "CRLF again", settle=1.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
