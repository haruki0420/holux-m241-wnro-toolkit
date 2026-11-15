"""Dump the M-241 firmware over the BootROM, using the sequence that works.

Confirmed on real hardware (2026-11-15, M-241 V1.13 / MT3318 / Windows 11 COM3):

    1. at 38400, send `$PMTK180*3B\\r\\n`
       -> NMEA stops: the chip is now in BootROM
    2. switch the host to 115200
    3. assert DTR and RTS and hold them
    4. send 0xA0 repeatedly until 0x5F arrives (36 probes on this unit)
    5. **immediately**, with NO delay, send 0x0A / 0x50 / 0x05
       -> 0xF5 / 0xAF / 0xFA
    6. now the BootROM command set works: 0xAF read32, 0xA2 read16, ...

The "no delay" part is the discovery that unblocked this: pausing between the
probes made step 5 time out, so the chip dropped out of the handshake.

Read-only: this tool only reads, it never writes to flash.

Usage:
    python tools/dump_via_bootrom.py COM3 -o backup/m241_stock.bin
    python tools/dump_via_bootrom.py COM3 -o out.bin --length 0x6345C --base 0x20000000
"""

from __future__ import annotations

import argparse
import hashlib
import struct
import sys
import time
from pathlib import Path

import serial

PROBES = (0xA0, 0x0A, 0x50, 0x05)
PROBE_REPLIES = (0x5F, 0xF5, 0xAF, 0xFA)

OPCODE_READ32 = 0xAF
OPCODE_READ16 = 0xA2
OPCODE_CHECKSUM = 0xA4

NMEA_BAUD = 38400
BOOTROM_BAUD = 115200


def enter_bootrom(port: str) -> bool:
    """Send ``$PMTK180*3B\\r\\n`` at 38400 and confirm NMEA stops."""
    with serial.Serial(port, NMEA_BAUD, timeout=0.2) as ser:
        time.sleep(0.25)
        ser.reset_input_buffer()
        got = b""
        deadline = time.time() + 1.5
        while time.time() < deadline:
            chunk = ser.read(4096)
            if chunk:
                got += chunk
                deadline = time.time() + 0.25
        if not got:
            print("  [!] no NMEA at 38400: is the logger powered on?", file=sys.stderr)
            return False
        ser.reset_input_buffer()
        ser.write(b"$PMTK180*3B\r\n")
        ser.flush()
        time.sleep(0.15)
        after = ser.read(8192)
    return len(after) == 0


def handshake(ser: serial.Serial, timeout: float = 8.0) -> bool:
    """0xA0 until 0x5F, then 0x0A/0x50/0x05 back to back with no pause."""
    deadline = time.time() + timeout
    sent = 0
    while time.time() < deadline:
        ser.write(b"\xa0")
        ser.flush()
        sent += 1
        reply = ser.read(1)
        if reply and reply[0] == 0x5F:
            break
        time.sleep(0.002)
    else:
        print(f"  [!] no 0x5F after {sent} probes", file=sys.stderr)
        return False
    for probe in PROBES[1:]:
        ser.write(bytes([probe]))
        ser.flush()
        reply = ser.read(1)
        if not reply or reply[0] != (probe ^ 0xFF):
            got = reply[0] if reply else None
            print(f"  [!] probe 0x{probe:02X} -> {got} (expected 0x{probe ^ 0xFF:02X})",
                  file=sys.stderr)
            return False
    return True


class Brom:
    """Minimal BootROM command client (read-only paths only)."""

    def __init__(self, ser: serial.Serial) -> None:
        self.ser = ser

    def _cmd(self, opcode: int, address: int, count: int) -> None:
        payload = bytes([opcode]) + struct.pack(">L", address) + struct.pack(">L", count)
        self.ser.write(payload)
        self.ser.flush()
        echo = self._read_exact(len(payload))
        if echo != payload:
            raise RuntimeError(f"echo mismatch: sent {payload.hex()} got {echo.hex()}")

    def _read_exact(self, count: int) -> bytes:
        buf = b""
        deadline = time.time() + 3.0
        while len(buf) < count:
            chunk = self.ser.read(count - len(buf))
            if chunk:
                buf += chunk
            elif time.time() > deadline:
                raise TimeoutError(f"timeout reading {count} bytes (got {len(buf)})")
        return buf

    def read32(self, address: int, length: int) -> bytes:
        if length % 4:
            raise ValueError("length must be a multiple of 4")
        self._cmd(OPCODE_READ32, address, length // 4)
        data = self._read_exact(length)
        self._read_exact(2)  # trailing checksum, not validated here
        return data

    def read(self, address: int, length: int, chunk: int = 0x100) -> bytes:
        out = bytearray()
        remaining = length
        cursor = address
        while remaining:
            take = min(chunk, remaining)
            if take % 4:
                take -= take % 4
                if take == 0:
                    take = remaining
            out.extend(self.read32(cursor, take))
            cursor += take
            remaining -= take
        return bytes(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("port")
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--base", type=lambda x: int(x, 0), default=0x20000000)
    parser.add_argument("--length", type=lambda x: int(x, 0), default=None)
    args = parser.parse_args()

    print(f"1) entering BootROM via $PMTK180 on {args.port} @ {NMEA_BAUD}")
    if not enter_bootrom(args.port):
        return 2
    print("   NMEA stopped -> chip switched")

    print(f"2) handshake at {BOOTROM_BAUD}")
    ser = serial.Serial(args.port, BOOTROM_BAUD, timeout=0.05)
    try:
        ser.dtr = True
        ser.rts = True
    except Exception:  # noqa: BLE001
        pass
    time.sleep(0.1)
    ser.reset_input_buffer()
    if not handshake(ser):
        ser.close()
        return 3
    print("   sync complete (0x5F/0xF5/0xAF/0xFA)")

    brom = Brom(ser)
    try:
        length = args.length
        if length is None:
            header = brom.read32(args.base + 0xF4, 4)
            declared = struct.unpack("<I", header)[0]
            print(f"3) header at 0x{args.base + 0xF4:X}: raw={header.hex(' ')} "
                  f"LE={declared} BE={struct.unpack('>I', header)[0]}")
            if 0x1000 <= declared <= 0x400000:
                length = declared + 4  # header stores size-4
            else:
                print("   [!] implausible size, pass --length", file=sys.stderr)
                return 4
        print(f"4) dumping {length} bytes from 0x{args.base:08X}")

        data = bytearray()
        cursor = args.base
        remaining = length
        started = time.time()
        while remaining:
            take = min(0x100, remaining)
            data.extend(brom.read32(cursor, take))
            cursor += take
            remaining -= take
            pct = int(len(data) * 100 / length)
            sys.stdout.write(f"\r   {pct:3d}%  {len(data)}/{length}  "
                             f"({time.time() - started:.1f}s)")
            sys.stdout.flush()
        print()
    finally:
        ser.close()

    image = bytes(data)
    digest = hashlib.sha256(image).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(image)
    print(f"\nsaved  : {args.output}")
    print(f"size   : {len(image)} bytes")
    print(f"sha256 : {digest}")
    print("\nKEEP THIS FILE: it is your only rollback if the flash ever fails.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
