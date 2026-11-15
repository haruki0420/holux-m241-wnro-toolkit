"""Replay of the M-241 BootROM entry sequence (read-only).

Control flow, established on hardware:

    attempt = 0
    probe   = 0                      ; index into the a0 0a 50 05 table
    while probe < 4 and attempt < max_attempts:
        Write(1 byte, table[probe])
        Read(1 byte, &reply)         ; short timeout
        if read failed: -> retry
        expected = table[probe]
        if reply == expected or reply == (expected ^ 0xFF):
            probe += 1               ; advance
        else:
            probe   = 0              ; restart the whole sequence
            attempt += 1

Key details:
  * the reply may be the probe byte itself OR its bitwise complement,
  * DTR and RTS are asserted and HELD (not pulsed then released),
  * a mismatch restarts the sequence from the first probe byte.

Nothing is written to flash.

Usage: python tools/bootrom_sync_vendor.py COM3 [-v]
"""

from __future__ import annotations

import itertools
import sys
import time

import serial

PROBES = (0xA0, 0x0A, 0x50, 0x05)
DELAYS_MS = (20, 300, 1000, 700)  # after each successful probe
MAX_ATTEMPTS = 20


def sync(ser: serial.Serial, verbose: bool = False) -> bool:
    """Run the entry loop. Returns True on a completed 4-byte sequence."""
    probe = 0
    attempt = 0
    while probe < len(PROBES) and attempt < MAX_ATTEMPTS:
        expected = PROBES[probe]
        ser.write(bytes([expected]))
        ser.flush()
        reply = ser.read(1)
        if not reply:
            probe = 0
            attempt += 1
            if verbose:
                print(f"      [{attempt:>2}] probe 0x{expected:02X}: timeout")
            continue
        value = reply[0]
        if value == expected or value == (expected ^ 0xFF):
            if verbose:
                print(
                    f"      [{attempt:>2}] probe 0x{expected:02X} -> "
                    f"0x{value:02X} OK  ({probe + 1}/4)"
                )
            probe += 1
            time.sleep(DELAYS_MS[probe - 1] / 1000.0)
        else:
            if verbose:
                print(
                    f"      [{attempt:>2}] probe 0x{expected:02X} -> 0x{value:02X} WRONG, restart"
                )
            probe = 0
            attempt += 1
    return probe >= len(PROBES)


def try_combo(port: str, baud: int, dtr: bool, rts: bool, verbose: bool) -> bool:
    try:
        ser = serial.Serial(port, baud, timeout=0.05)
    except Exception as exc:  # noqa: BLE001
        print(f"  baud={baud:>6} dtr={dtr!s:<5} rts={rts!s:<5}: open failed ({exc})")
        return False
    try:
        try:
            ser.dtr = dtr
            ser.rts = rts
        except Exception:  # noqa: BLE001
            pass
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        time.sleep(0.35)
        ok = sync(ser, verbose=verbose)
        label = f"baud={baud:>6} dtr={dtr!s:<5} rts={rts!s:<5}"
        if ok:
            print(f"  {label}: *** SYNC COMPLETE ***")
        elif verbose:
            print(f"  {label}: failed")
        return ok
    finally:
        ser.close()


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    verbose = "-v" in sys.argv or "--verbose" in sys.argv
    port = args[0] if args else "COM3"

    print(f"BootROM sync on {port}")
    print(f"  probes {[hex(p) for p in PROBES]}, delays {DELAYS_MS} ms")
    print("  accepting reply == probe OR reply == ~probe, DTR/RTS held\n")

    combos = list(itertools.product((115200, 38400), (True, False), (True, False)))
    for baud, dtr, rts in combos:
        if try_combo(port, baud, dtr, rts, verbose):
            print(f"\nSUCCESS: baud={baud} DTR={dtr} RTS={rts}")
            return 0
        time.sleep(0.4)
    print("\nno combination completed the sync sequence")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
