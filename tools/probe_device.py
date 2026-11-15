"""Raw serial capture / NMEA+PMTK probe for a connected M-241.

Read-only. Opens the port, prints whatever the device emits, then optionally
sends $PMTK605 and prints the raw reply. Does not touch firmware.

Usage:
    python tools/probe_device.py COM3
    python tools/probe_device.py COM3 --baud 38400 --seconds 4
"""

from __future__ import annotations

import argparse
import time

import serial


def capture(port: str, baud: int, seconds: float, send: str | None) -> None:
    print(f"opening {port} @ {baud} 8N1 ...", flush=True)
    ser = serial.Serial(port, baud, timeout=0.2)
    try:
        time.sleep(0.3)
        ser.reset_input_buffer()
        print(f"--- listening for {seconds:.0f}s ---", flush=True)
        deadline = time.time() + seconds
        total = 0
        while time.time() < deadline:
            chunk = ser.read(4096)
            if chunk:
                total += len(chunk)
                print(chunk.decode("latin1", "replace"), end="", flush=True)
        print(f"\n--- received {total} bytes ---", flush=True)

        if send:
            payload = (send + "\r\n").encode("ascii")
            print(f"\n--- sending {payload!r} ---", flush=True)
            ser.write(payload)
            ser.flush()
            deadline = time.time() + 3.0
            got = b""
            while time.time() < deadline:
                chunk = ser.read(4096)
                if chunk:
                    got += chunk
                    print(chunk.decode("latin1", "replace"), end="", flush=True)
                    deadline = time.time() + 0.7
            print(f"\n--- reply: {got!r} ({len(got)} bytes) ---", flush=True)
    finally:
        ser.close()
        print("port closed", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port")
    parser.add_argument("--baud", type=int, default=38400)
    parser.add_argument("--seconds", type=float, default=3.0)
    parser.add_argument("--send", default="$PMTK605*31")
    args = parser.parse_args()
    capture(args.port, args.baud, args.seconds, args.send)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
