"""Manual BootROM entry probe for a connected M-241 (read-only exploration).

Tries, in order:
  1. RTS/DTR pulse + 0xA0 sync
  2. $PMTK180*3B at 38400 then 0xA0 sync
  3. $PMTK180,38400*xx (what the current code sends) then 0xA0 sync

Never writes to flash. Prints exactly what comes back.

Usage: python tools/probe_bootrom.py COM3
"""

from __future__ import annotations

import sys
import time

import serial


def nmea_checksum(body: str) -> int:
    value = 0
    for char in body:
        value ^= ord(char)
    return value


def with_checksum(body: str) -> bytes:
    return f"${body}*{nmea_checksum(body):02X}\r\n".encode("ascii")


def drain(ser: serial.Serial, seconds: float = 0.4) -> bytes:
    out = b""
    deadline = time.time() + seconds
    while time.time() < deadline:
        chunk = ser.read(4096)
        if chunk:
            out += chunk
            deadline = time.time() + 0.1
    return out


def try_sync(ser: serial.Serial, label: str, timeout: float = 4.0) -> bool:
    """Send 0xA0 repeatedly, report what comes back."""
    ser.reset_input_buffer()
    deadline = time.time() + timeout
    received = b""
    while time.time() < deadline:
        ser.write(b"\xa0")
        ser.flush()
        byte = ser.read(1)
        if byte:
            received += byte
            if byte == b"\x5f":
                print(f"  [{label}] got 0x5F sync reply!")
                # continue the dance
                for probe, expect in ((b"\x0a", 0xF5), (b"\x50", 0xAF), (b"\x05", 0xFA)):
                    ser.write(probe)
                    ser.flush()
                    reply = ser.read(1)
                    if not reply:
                        print(f"  [{label}] no reply to {probe!r}")
                        return False
                    if reply[0] != expect:
                        print(f"  [{label}] expected 0x{expect:02X}, got 0x{reply[0]:02X}")
                        return False
                print(f"  [{label}] BootROM SYNC COMPLETE")
                return True
        time.sleep(0.01)
    print(f"  [{label}] no sync. bytes seen: {received[-16:]!r}")
    return False


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "COM3"

    print("== test 1: RTS/DTR pulse + 0xA0 ==")
    ser = serial.Serial(port, 115200, timeout=0.05)
    try:
        ser.dtr = False
        ser.rts = False
        time.sleep(0.05)
        ser.dtr = True
        ser.rts = True
        time.sleep(0.3)
        drained = drain(ser, 0.3)
        if drained:
            print(f"  bytes after reset: {drained[:80]!r}")
        if try_sync(ser, "rts-dtr"):
            ser.write(b"\x00")
            print("  (device left in BootROM)")
            return 0
    finally:
        ser.close()

    time.sleep(0.5)

    print("== test 2: $PMTK180*3B at 38400 + 0xA0 ==")
    ser = serial.Serial(port, 38400, timeout=0.05)
    try:
        ser.reset_input_buffer()
        cmd = with_checksum("PMTK180")
        print(f"  sending {cmd!r}")
        ser.write(cmd)
        ser.flush()
        time.sleep(0.3)
        reply = drain(ser, 0.3)
        print(f"  reply: {reply[:120]!r}")
        if try_sync(ser, "pmtk180"):
            print("  (device left in BootROM)")
            return 0
    finally:
        ser.close()

    time.sleep(0.5)

    print("== test 3: $PMTK180,115200*xx at 38400 + 0xA0 (current code shape) ==")
    ser = serial.Serial(port, 38400, timeout=0.05)
    try:
        ser.reset_input_buffer()
        cmd = with_checksum("PMTK180,115200")
        print(f"  sending {cmd!r}")
        ser.write(cmd)
        ser.flush()
        time.sleep(0.3)
        reply = drain(ser, 0.3)
        print(f"  reply: {reply[:120]!r}")
        if try_sync(ser, "pmtk180,baud"):
            print("  (device left in BootROM)")
            return 0
    finally:
        ser.close()

    print("\nall entry methods failed")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
