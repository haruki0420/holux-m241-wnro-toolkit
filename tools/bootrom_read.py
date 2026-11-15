"""Dump memory over the M-241 BootROM with the framing the chip actually uses.

Hardware-confirmed reply shape for `0xAF READ32`:

    host: AF | addr(BE32) | count(BE32 in 16-bit WORDS)
    chip: echo(9 bytes) + data(count*4 bytes)          <- no checksum trailer

Earlier tools assumed a 2-byte checksum trailer and therefore mis-framed every
read. `0xA4 CHECKSUM` *does* answer with a 2-byte value, so the trailer exists
for that opcode only.

This tool reads a window at a given base and prints it, so the firmware's
location can be identified by its `18 f0 9f e5` vector table.

Read-only.

Usage:
    python tools/bootrom_read.py COM3 --base 0x20000000 --words 64
    python tools/bootrom_read.py COM3 --scan
"""

from __future__ import annotations

import argparse
import struct
import sys
import time

import serial

OPCODE_READ32 = 0xAF
MAGIC = bytes.fromhex("18f09fe5")


class Brom:
    def __init__(self, ser: serial.Serial) -> None:
        self.ser = ser

    def _read_exact(self, count: int, timeout: float = 3.0) -> bytes:
        buf = b""
        deadline = time.time() + timeout
        while len(buf) < count:
            chunk = self.ser.read(count - len(buf))
            if chunk:
                buf += chunk
            elif time.time() > deadline:
                raise TimeoutError(f"wanted {count} bytes, got {len(buf)}")
        return buf

    def read_words(self, address: int, words: int) -> bytes:
        frame = bytes([OPCODE_READ32]) + struct.pack(">L", address) + struct.pack(">L", words)
        self.ser.reset_input_buffer()
        self.ser.write(frame)
        self.ser.flush()
        echo = self._read_exact(len(frame), timeout=1.0)
        if echo != frame[: len(echo)]:
            raise RuntimeError(f"echo mismatch: sent {frame.hex()} got {echo.hex()}")
        return self._read_exact(words * 4, timeout=4.0)


def hexdump(data: bytes, base: int) -> None:
    for offset in range(0, len(data), 16):
        chunk = data[offset : offset + 16]
        hexpart = chunk.hex(" ")
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print(f"  {base + offset:08X}  {hexpart:<47}  {asc}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("port")
    parser.add_argument("--base", type=lambda x: int(x, 0), default=0x20000000)
    parser.add_argument("--words", type=int, default=32)
    parser.add_argument("--scan", action="store_true")
    args = parser.parse_args()

    ser = serial.Serial(args.port, 115200, timeout=0.1)
    try:
        try:
            ser.dtr = True
            ser.rts = True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.15)
        brom = Brom(ser)

        if args.scan:
            print("scanning for the 18 f0 9f e5 vector table")
            bases = [0x0, 0x100000, 0x200000, 0x20000000, 0x10000000, 0x08000000]
            for base in bases:
                try:
                    head = brom.read_words(base, 8)
                except Exception as exc:  # noqa: BLE001
                    print(f"  0x{base:08X}: {exc}")
                    continue
                tag = " <== FIRMWARE" if head.startswith(MAGIC) else ""
                print(f"  0x{base:08X}: {head.hex(' ')}{tag}")
            return 0

        data = brom.read_words(args.base, args.words)
        print(f"{args.words} words from 0x{args.base:08X}:")
        hexdump(data, args.base)
        if data.startswith(MAGIC):
            print("\n*** firmware vector table found ***")
    finally:
        ser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
