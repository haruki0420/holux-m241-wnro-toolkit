"""Read the M-241 firmware from a BootROM session that is already synced.

Use this when `tools/dump_via_bootrom.py` cannot re-enter BootROM (for example
right after a previous run left the chip in the BootROM command state).

It assumes the chip already answered the `a0/0a/50/05` dance, so it goes
straight to the read opcodes:

    0xAF READ32  addr(BE32) count(BE32) -> data + XOR16 checksum

Read-only.

Usage: python tools/read_bootrom.py COM3 -o backup/m241_stock.bin
"""

from __future__ import annotations

import argparse
import hashlib
import struct
import sys
import time
from pathlib import Path

import serial

OPCODE_READ32 = 0xAF
OPCODE_CHECKSUM = 0xA4


def checksum_xor16(data: bytes) -> int:
    value = 0
    for index in range(0, len(data) & ~1, 2):
        value ^= (data[index] << 8) | data[index + 1]
    if len(data) % 2:
        value ^= data[-1] << 8
    return value & 0xFFFF


class Brom:
    def __init__(self, ser: serial.Serial, verbose: bool = False) -> None:
        self.ser = ser
        self.verbose = verbose

    def _read_exact(self, count: int, timeout: float = 3.0) -> bytes:
        buf = b""
        deadline = time.time() + timeout
        while len(buf) < count:
            chunk = self.ser.read(count - len(buf))
            if chunk:
                buf += chunk
            elif time.time() > deadline:
                raise TimeoutError(f"timeout: wanted {count} bytes, got {len(buf)}")
        return buf

    def _cmd(self, opcode: int, address: int, count: int) -> None:
        payload = bytes([opcode]) + struct.pack(">L", address) + struct.pack(">L", count)
        self.ser.write(payload)
        self.ser.flush()
        echo = self._read_exact(len(payload), timeout=1.0)
        if echo != payload:
            raise RuntimeError(
                f"command echo mismatch: sent {payload.hex()} got {echo.hex()}"
            )

    def read32(self, address: int, length: int, *, byte_swap: bool = False) -> bytes:
        if address % 4 or length % 4:
            raise ValueError("32-bit aligned only")
        self._cmd(OPCODE_READ32, address, length // 4)
        data = self._read_exact(length, timeout=5.0)
        chip_checksum = struct.unpack(">H", self._read_exact(2, timeout=2.0))[0]
        if byte_swap:
            data = b"".join(data[i : i + 2][::-1] for i in range(0, len(data), 2))
        local = checksum_xor16(data)
        if local != chip_checksum:
            raise RuntimeError(
                f"checksum mismatch at 0x{address:08X}: chip 0x{chip_checksum:04X} "
                f"local 0x{local:04X}"
            )
        return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("port")
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--base", type=lambda x: int(x, 0), default=0x20000000)
    parser.add_argument("--length", type=lambda x: int(x, 0), default=None)
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--swap", action="store_true", help="byte-swap 16-bit pairs")
    args = parser.parse_args()

    ser = serial.Serial(args.port, args.baud, timeout=0.1)
    try:
        try:
            ser.dtr = True
            ser.rts = True
        except Exception:  # noqa: BLE001
            pass
        brom = Brom(ser, verbose=True)

        print(f"probing BootROM at 0x{args.base:08X} ...", flush=True)
        try:
            probe = brom.read32(args.base, 4)
        except Exception as exc:  # noqa: BLE001
            print(f"[!] {exc}", file=sys.stderr)
            print("    the chip is probably not in a synced BootROM session;")
            print("    run tools/dump_via_bootrom.py instead")
            return 2
        print(f"  first 4 bytes: {probe.hex(' ')}")

        length = args.length
        if length is None:
            header = brom.read32(args.base + 0xF4, 4)
            le = struct.unpack("<I", header)[0]
            be = struct.unpack(">I", header)[0]
            print(f"  size header raw={header.hex(' ')}  LE={le}  BE={be}")
            if 0x1000 <= le <= 0x400000:
                length = le + 4
            elif 0x1000 <= be <= 0x400000:
                length = be + 4
            else:
                print("[!] implausible size; pass --length", file=sys.stderr)
                return 3
        print(f"reading {length} bytes ...")

        out = bytearray()
        cursor = args.base
        remaining = length
        started = time.time()
        while remaining:
            take = min(0x100, remaining)
            out.extend(brom.read32(cursor, take, byte_swap=args.swap))
            cursor += take
            remaining -= take
            pct = int(len(out) * 100 / length)
            sys.stdout.write(f"\r  {pct:3d}%  {len(out)}/{length}  "
                             f"({time.time() - started:.1f}s)")
            sys.stdout.flush()
        print()
    finally:
        ser.close()

    image = bytes(out)
    digest = hashlib.sha256(image).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(image)
    print(f"\nsaved  : {args.output}")
    print(f"size   : {len(image)}")
    print(f"sha256 : {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
