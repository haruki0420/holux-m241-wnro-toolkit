"""Exhaustive 0xA0 BootROM sync sweep (read-only).

When the device stops emitting NMEA after a reset pulse, it is usually sitting
in BootROM waiting for the host.  The only unknown left is the baud rate, so
this tool tries every plausible one with a continuous 0xA0 stream instead of
one-byte-at-a-time poking (the real bootloader times out if the probe is too
slow).

Nothing is written to flash.

Usage: python tools/sweep_sync.py COM3
"""

from __future__ import annotations

import sys
import time

import serial

BAUDS = (
    9600,
    19200,
    38400,
    57600,
    115200,
    230400,
    460800,
    921600,
)


def try_baud(port: str, baud: int, *, stream_seconds: float = 2.5) -> str:
    try:
        ser = serial.Serial(port, baud, timeout=0.01)
    except Exception as exc:  # noqa: BLE001
        return f"cannot open: {exc}"
    try:
        ser.reset_input_buffer()
        # Stream 0xA0 for a while, then read everything that came back.
        deadline = time.time() + stream_seconds
        while time.time() < deadline:
            ser.write(b"\xa0" * 16)
            ser.flush()
            time.sleep(0.004)
        time.sleep(0.2)
        got = b""
        deadline = time.time() + 0.8
        while time.time() < deadline:
            chunk = ser.read(4096)
            if chunk:
                got += chunk
                deadline = time.time() + 0.15
        if not got:
            return "silent"
        if b"\x5f" in got:
            # complete the dance
            ser.reset_input_buffer()
            for probe, expect in ((b"\x0a", 0xF5), (b"\x50", 0xAF), (b"\x05", 0xFA)):
                ser.write(probe)
                ser.flush()
                reply = ser.read(1)
                if not reply:
                    return f"0x5F seen but no reply to {probe.hex()}"
                if reply[0] != expect:
                    return f"0x5F seen, {probe.hex()} -> 0x{reply[0]:02X} (want 0x{expect:02X})"
            return "SYNC COMPLETE"
        return f"{len(got)} bytes but no 0x5F: {got[:24].hex()}"
    finally:
        ser.close()


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "COM3"
    print(f"sweeping 0xA0 sync on {port}")
    for baud in BAUDS:
        verdict = try_baud(port, baud)
        print(f"  {baud:>7}: {verdict}", flush=True)
        if verdict == "SYNC COMPLETE":
            print(f"\n*** BootROM reachable at {baud} baud ***")
            return 0
    print("\nno baud reached BootROM")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
