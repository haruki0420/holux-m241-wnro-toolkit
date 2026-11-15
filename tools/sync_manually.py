"""Patient BootROM sync: mimics the sequence that worked, with retries.

The CLI's `dump --pmtk180` sends `$PMTK180*3B\\r\\n` and then tries to sync over
the *same* open port object. On hardware the chip appears to need a fresh port
open at the BootROM baud, so this tool does the sequence the manual way and
reports exactly what happens:

    1. open at 38400, send the switch command, close
    2. reopen at 115200, assert DTR and RTS
    3. send 0xA0 patiently (up to 10 s) until 0x5F
    4. 0x0A/0x50/0x05 immediately

Read-only: it never writes to flash.

Usage: python tools/sync_manually.py COM3
"""

from __future__ import annotations

import sys
import time

import serial

NMEA_BAUD = 38400
BOOTROM_BAUD = 115200


def send_switch(port: str) -> bool:
    with serial.Serial(port, NMEA_BAUD, timeout=0.2) as ser:
        time.sleep(0.25)
        ser.reset_input_buffer()
        heard = b""
        deadline = time.time() + 1.2
        while time.time() < deadline:
            chunk = ser.read(4096)
            if chunk:
                heard += chunk
                deadline = time.time() + 0.25
        print(f"  NMEA before switch: {len(heard)} bytes")
        ser.reset_input_buffer()
        ser.write(b"$PMTK180*3B\r\n")
        ser.flush()
        time.sleep(0.2)
        after = ser.read(8192)
        print(f"  bytes after switch: {len(after)}")
        if heard and not after:
            print("  -> chip switched to BootROM")
            return True
        if not heard:
            print("  -> no NMEA to begin with (already in BootROM, or off)")
            return True
        return False


def sync(port: str, timeout: float = 10.0) -> bool:
    ser = serial.Serial(port, BOOTROM_BAUD, timeout=0.02)
    try:
        try:
            ser.dtr = True
            ser.rts = True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.15)
        ser.reset_input_buffer()

        deadline = time.time() + timeout
        probes = 0
        while time.time() < deadline:
            ser.write(b"\xa0")
            ser.flush()
            probes += 1
            reply = ser.read(1)
            if reply:
                print(f"  byte 0x{reply[0]:02X} at probe {probes}")
                if reply[0] == 0x5F:
                    break
            time.sleep(0.001)
        else:
            print(f"  no 0x5F after {probes} probes in {timeout:.0f}s")
            return False
        print(f"  0x5F after {probes} probes")

        for probe, expected in ((0x0A, 0xF5), (0x50, 0xAF), (0x05, 0xFA)):
            ser.write(bytes([probe]))
            ser.flush()
            reply = ser.read(1)
            got = f"0x{reply[0]:02X}" if reply else "nothing"
            print(f"  0x{probe:02X} -> {got} (want 0x{expected:02X})")
            if not reply or reply[0] != expected:
                return False
        return True
    finally:
        ser.close()


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "COM3"
    print(f"1) switch to BootROM via $PMTK180 on {port}")
    send_switch(port)
    time.sleep(0.2)

    print(f"2) sync at {BOOTROM_BAUD}")
    if sync(port):
        print("\n*** BootROM session is up ***")
        return 0
    print("\nsync failed")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
