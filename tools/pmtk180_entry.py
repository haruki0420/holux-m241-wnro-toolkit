"""Test the PMTK180 -> BootROM transition properly (read-only).

The device accepts exactly one form of the switch command: the plain 13-byte
`$PMTK180*3B\\r\\n` (no argument). Our toolkit's `_send_pmtk180()` used to send
`$PMTK180,<baud>` and never switched the host baud afterwards -- so even a
successful switch would have been invisible.

This tool does it the way the device expects:

    1. open at the NMEA baud (38400)
    2. write `$PMTK180*3B\\r\\n`
    3. confirm NMEA stops (proof the switch happened)
    4. switch the host to 115200 and run the `a0 0a 50 05` sync

Nothing is written to flash.

Usage: python tools/pmtk180_entry.py COM3
"""

from __future__ import annotations

import sys
import time

import serial

PMTK180 = b"$PMTK180*3B\r\n"
PROBES = (0xA0, 0x0A, 0x50, 0x05)
DELAYS_MS = (20, 300, 1000, 700)


def listen(ser: serial.Serial, seconds: float) -> bytes:
    got = b""
    deadline = time.time() + seconds
    while time.time() < deadline:
        chunk = ser.read(4096)
        if chunk:
            got += chunk
            deadline = time.time() + 0.2
    return got


def sync(ser: serial.Serial, verbose: bool = True) -> bool:
    probe = 0
    attempt = 0
    while probe < len(PROBES) and attempt < 15:
        expected = PROBES[probe]
        ser.write(bytes([expected]))
        ser.flush()
        reply = ser.read(1)
        if not reply:
            attempt += 1
            probe = 0
            if verbose:
                print(f"    [{attempt:>2}] 0x{expected:02X}: timeout")
            continue
        value = reply[0]
        if value == expected or value == (expected ^ 0xFF):
            probe += 1
            if verbose:
                print(f"    [ OK ] 0x{expected:02X} -> 0x{value:02X} ({probe}/4)")
            time.sleep(DELAYS_MS[probe - 1] / 1000.0)
        else:
            attempt += 1
            probe = 0
            if verbose:
                print(f"    [{attempt:>2}] 0x{expected:02X} -> 0x{value:02X} wrong")
    return probe >= len(PROBES)


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "COM3"

    print(f"1) open {port} at 38400 and confirm NMEA is flowing")
    with serial.Serial(port, 38400, timeout=0.2) as ser:
        time.sleep(0.3)
        ser.reset_input_buffer()
        before = listen(ser, 1.5)
        print(f"   NMEA before: {len(before)} bytes")

        print(f"2) send the vendor's exact switch command {PMTK180!r}")
        ser.reset_input_buffer()
        ser.write(PMTK180)
        ser.flush()

        print("3) confirm NMEA stops (that is the proof the switch happened)")
        after = listen(ser, 1.5)
        print(f"   bytes after PMTK180: {len(after)}")
        if after:
            print(f"   first line: {after.split(b'chr(13)'.encode())[0][:60]!r}")

    switched = len(after) == 0 and len(before) > 0
    print(f"   -> NMEA stopped: {switched}")

    print("\n4) switch host to 115200 and run the sync")
    with serial.Serial(port, 115200, timeout=0.05) as ser:
        try:
            ser.dtr = True
            ser.rts = True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.2)
        ser.reset_input_buffer()
        ok = sync(ser)
        if ok:
            print("\n*** BOOTROM SYNC COMPLETE ***")
            ser.write(b"\xa4")
            ser.flush()
            print(f"    post-sync 0xA4 echo = {ser.read(1)!r}")
            return 0
    print("\nsync failed")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
