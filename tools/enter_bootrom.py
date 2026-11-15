"""Robust BootROM entry + sync, in the order that works on hardware.

Observed on the real M-241:
  * the chip must receive `$PMTK180*3B\\r\\n` **while it is still streaming
    NMEA**; if the stream has already stopped it will not answer,
  * after the switch the host must reopen the port at 115200 (reusing the
    38400 handle does not work),
  * the chip can take many seconds to answer the first 0xA0, so the probe
    timeout must be generous (30 s),
  * the three follow-up probes must be sent with no pause at all.

Once the flow is broken the chip stays silent until it is power-cycled, so this
tool refuses to start if it cannot hear NMEA first.

Read-only: it never writes to flash.

Usage: python tools/enter_bootrom.py COM3 [--timeout 30]
"""

from __future__ import annotations

import argparse
import sys
import time

import serial

NMEA_BAUD = 38400
BOOTROM_BAUD = 115200


def hear_nmea(port: str) -> int:
    with serial.Serial(port, NMEA_BAUD, timeout=0.2) as ser:
        time.sleep(0.25)
        ser.reset_input_buffer()
        heard = b""
        deadline = time.time() + 1.5
        while time.time() < deadline:
            chunk = ser.read(4096)
            if chunk:
                heard += chunk
                deadline = time.time() + 0.3
        return len(heard)


def switch(port: str) -> bool:
    """Send the switch command while NMEA is still flowing."""
    with serial.Serial(port, NMEA_BAUD, timeout=0.2) as ser:
        time.sleep(0.25)
        ser.reset_input_buffer()
        heard = b""
        deadline = time.time() + 1.5
        while time.time() < deadline:
            chunk = ser.read(4096)
            if chunk:
                heard += chunk
                deadline = time.time() + 0.3
        if not heard:
            print("  [!] no NMEA: power-cycle the logger first")
            return False
        ser.reset_input_buffer()
        ser.write(b"$PMTK180*3B\r\n")
        ser.flush()
        time.sleep(0.2)
        after = ser.read(8192)
        if after:
            print(f"  [!] NMEA still running ({len(after)} bytes)")
            return False
        print("  NMEA stopped: the chip is listening")
        return True


def sync(port: str, timeout: float) -> bool:
    ser = serial.Serial(port, BOOTROM_BAUD, timeout=0.02)
    try:
        try:
            ser.dtr = True
            ser.rts = True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.15)
        ser.reset_input_buffer()

        started = time.time()
        deadline = started + timeout
        probes = 0
        while time.time() < deadline:
            ser.write(b"\xa0")
            ser.flush()
            probes += 1
            reply = ser.read(1)
            if reply and reply[0] == 0x5F:
                break
            time.sleep(0.002)
        else:
            print(f"  [!] no 0x5F after {probes} probes / {time.time() - started:.0f}s")
            return False
        print(f"  0x5F after {probes} probes / {time.time() - started:.1f}s")

        for probe, expected in ((0x0A, 0xF5), (0x50, 0xAF), (0x05, 0xFA)):
            ser.write(bytes([probe]))
            ser.flush()
            reply = ser.read(1)
            got = f"0x{reply[0]:02X}" if reply else "nothing"
            print(f"  0x{probe:02X} -> {got} (want 0x{expected:02X})")
            if not reply or reply[0] != expected:
                return False
        print("  BootROM sequence complete")
        return True
    finally:
        ser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("port")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    print(f"1) check NMEA on {args.port}")
    heard = hear_nmea(args.port)
    print(f"  {heard} bytes")
    if heard == 0:
        print("\nThe logger is not streaming NMEA. Power-cycle it and try again.")
        return 2

    print("2) send the BootROM switch command")
    if not switch(args.port):
        return 3

    print(f"3) sync at {BOOTROM_BAUD} (up to {args.timeout:.0f}s)")
    if sync(args.port, args.timeout):
        print("\n*** BootROM is reachable ***")
        return 0
    print("\nsync failed; the chip is now silent until it is power-cycled")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
