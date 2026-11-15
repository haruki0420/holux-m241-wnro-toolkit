"""Find the correct inter-probe timing for the M-241 BootROM sync (read-only).

Progress: sending `$PMTK180*3B\\r\\n` at 38400 really does switch the chip into
BootROM, and at 115200 the first three probes answer correctly:

    0xA0 -> 0x5F
    0x0A -> 0xF5
    0x50 -> 0xAF
    0x05 -> (timeout)

Pausing between probes makes this firmware give up: the BootROM goes quiet if the
sequence is not immediate, so the delays are swept here.

This tool sweeps the delay after the third probe (and, as a fallback, the whole
delay profile) to find a profile where all four replies land.

Nothing is written to flash.

Usage: python tools/tune_sync_delays.py COM3
"""

from __future__ import annotations

import sys
import time

import serial

PMTK180 = b"$PMTK180*3B\r\n"
PROBES = (0xA0, 0x0A, 0x50, 0x05)

# Candidate delay profiles in milliseconds, applied after each successful probe.
PROFILES = {
    "long (20/300/1000/700)": (20, 300, 1000, 700),
    "no delay": (0, 0, 0, 0),
    "fast (0/0/0/0)+5ms": (5, 5, 5, 5),
    "short (10/20/50/100)": (10, 20, 50, 100),
    "upstream-ish (50/50/50/50)": (50, 50, 50, 50),
    "head-heavy (20/300/100/50)": (20, 300, 100, 50),
    "tail-short (20/300/50/50)": (20, 300, 50, 50),
    "medium (100/100/100/100)": (100, 100, 100, 100),
}


def enter_bootrom(port: str) -> bool:
    """Send the BootROM switch command and confirm NMEA stops."""
    with serial.Serial(port, 38400, timeout=0.2) as ser:
        time.sleep(0.25)
        ser.reset_input_buffer()
        before = b""
        deadline = time.time() + 1.2
        while time.time() < deadline:
            chunk = ser.read(4096)
            if chunk:
                before += chunk
                deadline = time.time() + 0.2
        if not before:
            print("  [!] no NMEA at 38400 - is the device on?")
            return False
        ser.reset_input_buffer()
        ser.write(PMTK180)
        ser.flush()
        time.sleep(0.2)
        after = ser.read(8192)
    return len(after) == 0


def run_profile(port: str, delays: tuple[int, ...], verbose: bool = False) -> int:
    """Return how many probes were answered before the sequence broke."""
    with serial.Serial(port, 115200, timeout=0.05) as ser:
        try:
            ser.dtr = True
            ser.rts = True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.2)
        ser.reset_input_buffer()
        probe = 0
        attempt = 0
        while probe < len(PROBES) and attempt < 12:
            expected = PROBES[probe]
            ser.write(bytes([expected]))
            ser.flush()
            reply = ser.read(1)
            if not reply:
                attempt += 1
                probe = 0
                continue
            value = reply[0]
            if value == expected or value == (expected ^ 0xFF):
                if verbose:
                    print(f"      0x{expected:02X} -> 0x{value:02X} ok")
                delay = delays[probe]
                probe += 1
                if delay:
                    time.sleep(delay / 1000.0)
            else:
                if verbose:
                    print(f"      0x{expected:02X} -> 0x{value:02X} wrong")
                attempt += 1
                probe = 0
        return probe


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "COM3"

    for name, delays in PROFILES.items():
        print(f"\n=== {name} ===")
        if not enter_bootrom(port):
            print("  could not enter BootROM (no NMEA to begin with)")
            return 1
        reached = run_profile(port, delays, verbose=True)
        print(f"  -> {reached}/4 probes answered")
        if reached == 4:
            print(f"\n*** FULL SYNC with profile {name} = {delays} ***")
            return 0
        # let the chip settle / reset before the next profile
        time.sleep(1.0)
    print("\nno delay profile reached 4/4")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
