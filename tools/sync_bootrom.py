"""Try to sync with a chip that may already be sitting in BootROM mode.

BootROM talks at 115200 regardless of the NMEA baud, so this just hammers 0xA0
at each candidate baud and reports the first one that answers.

Read-only: it never writes to flash.

Usage: python tools/sync_bootrom.py COM3
"""

from __future__ import annotations

import sys
import time

import serial

CANDIDATE_BAUDS = (115200, 38400, 57600, 9600, 460800)


def attempt(port: str, baud: int, timeout: float = 3.0) -> bool:
    try:
        ser = serial.Serial(port, baud, timeout=0.05)
    except Exception as exc:  # noqa: BLE001
        print(f"  {baud:>6}: cannot open ({exc})")
        return False
    try:
        ser.reset_input_buffer()
        deadline = time.time() + timeout
        seen = b""
        while time.time() < deadline:
            ser.write(b"\xa0")
            ser.flush()
            byte = ser.read(1)
            if byte:
                seen += byte
                if byte == b"\x5f":
                    for probe, expect in ((b"\x0a", 0xF5), (b"\x50", 0xAF), (b"\x05", 0xFA)):
                        ser.write(probe)
                        ser.flush()
                        reply = ser.read(1)
                        if not reply or reply[0] != expect:
                            got = reply[0] if reply else None
                            print(
                                f"  {baud:>6}: sync stage failed at {probe!r} "
                                f"(expected 0x{expect:02X}, got {got})"
                            )
                            return False
                    print(f"  {baud:>6}: *** BOOTROM SYNC OK ***")
                    return True
            time.sleep(0.005)
        print(f"  {baud:>6}: no sync (bytes seen: {seen[-8:]!r})")
        return False
    finally:
        ser.close()


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "COM3"
    print(f"trying to sync with {port} (device may already be in BootROM):")
    for baud in CANDIDATE_BAUDS:
        if attempt(port, baud):
            print(f"\nBootROM is reachable at {baud} baud.")
            return 0
    print("\nno BootROM sync on any candidate baud")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
