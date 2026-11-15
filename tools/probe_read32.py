"""Work out the BootROM read32 count encoding on the real chip (read-only).

The chip echoed `af 20000000 00000000` when we sent count=1, i.e. it echoed 0
instead of 1. That means our count field is not accepted as-is. Two candidates:

  a) count is in bytes (send 4 for a 4-byte read),
  b) count is in words but must be >= some minimum, or is 16-bit.

This tool sends `0xAF` with several count values, prints the echo and any data,
and reports which encoding produces a sane response. It never writes.

Usage: python tools/probe_read32.py COM3
"""

from __future__ import annotations

import struct
import sys
import time

import serial


def attempt(ser: serial.Serial, address: int, count_bytes: int, label: str) -> None:
    frame = bytes([0xAF]) + struct.pack(">L", address) + struct.pack(">L", count_bytes)
    ser.reset_input_buffer()
    ser.write(frame)
    ser.flush()
    time.sleep(0.05)
    reply = ser.read(64)
    print(f"  {label:<28} sent={frame.hex()}  reply={reply.hex(' ') if reply else '(nothing)'}")


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "COM3"
    ser = serial.Serial(port, 115200, timeout=0.3)
    try:
        try:
            ser.dtr = True
            ser.rts = True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.2)

        print("A) count as WORD count")
        for words in (1, 2, 64):
            attempt(ser, 0x20000000, words, f"count={words} word(s)")

        print("\nB) count as BYTE count")
        for byte_count in (4, 8, 256):
            attempt(ser, 0x20000000, byte_count, f"count={byte_count} byte(s)")

        print("\nC) checksum command (0xA4), same two encodings")
        for value in (1, 4):
            frame = bytes([0xA4]) + struct.pack(">L", 0x20000000) + struct.pack(">L", value)
            ser.reset_input_buffer()
            ser.write(frame)
            ser.flush()
            time.sleep(0.05)
            reply = ser.read(16)
            print(f"  0xA4 count={value}: echo={reply.hex(' ') if reply else '(nothing)'}")
    finally:
        ser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
