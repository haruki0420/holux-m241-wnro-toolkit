"""Load the Download Agent into the M-241's RAM and jump to it (no flash write).

Validates the whole DA handshake on real hardware before any destructive step:

    1. WRITE16 the DA image to 0x00100800   (count in words, byte-swapped pairs)
    2. read it back and compare
    3. JUMP 0x00100800
    4. read the 20-byte DA info report (SYNC 0xC0 + DA version + flash size ...)

Nothing is written to flash.

Usage: python tools/load_da.py COM3 --da path/to/DA_v3.8.4.bin
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
import time
from pathlib import Path

import serial

OPCODE_WRITE16 = 0xA1
OPCODE_READ32 = 0xAF
OPCODE_JUMP = 0xA8

BASE_ADDR_DA = 0x00100800
DA_INFO_FORMAT = ">BHBLHHHHL"
DA_INFO_SIZE = struct.calcsize(DA_INFO_FORMAT)

#: Download agent shipped with the official Holux utility. Point M241_DA at
#: your own copy; there is no default because the file is proprietary.
DEFAULT_DA = Path(os.environ.get("M241_DA", "DA_v3.8.4.bin"))


class BootRom:
    def __init__(self, ser: serial.Serial) -> None:
        self.ser = ser

    def drain(self, count: int, timeout: float = 2.0) -> bytes:
        buf = b""
        deadline = time.time() + timeout
        while len(buf) < count:
            chunk = self.ser.read(count - len(buf))
            if chunk:
                buf += chunk
            elif time.time() > deadline:
                break
        return buf

    def read32(self, address: int, dwords: int) -> bytes:
        frame = bytes([OPCODE_READ32]) + struct.pack(">L", address) + struct.pack(">L", dwords)
        self.ser.reset_input_buffer()
        self.ser.write(frame)
        self.ser.flush()
        time.sleep(0.01)
        echo = self.drain(len(frame))
        if echo != frame:
            raise RuntimeError(f"read echo {echo.hex()}")
        raw = self.drain(dwords * 4, timeout=3.0)
        return b"".join(raw[i : i + 4][::-1] for i in range(0, len(raw), 4))

    def write_words_payload(
        self, address: int, data: bytes, *, byte_swap: bool = True, chunk: int = 0x100
    ) -> None:
        """WRITE16 in chunks: count is in 16-bit words, payload byte-swapped."""
        if len(data) % 2:
            raise ValueError("even length only")
        for offset in range(0, len(data), chunk):
            block = data[offset : offset + chunk]
            if len(block) % 2:
                block += b"\x00"
            payload = block
            if byte_swap:
                payload = b"".join(block[i : i + 2][::-1] for i in range(0, len(block), 2))
            words = len(block) // 2
            frame = (
                bytes([OPCODE_WRITE16])
                + struct.pack(">L", address + offset)
                + struct.pack(">L", words)
            )
            self.ser.reset_input_buffer()
            self.ser.write(frame + payload)
            self.ser.flush()
            expected = len(frame) + len(payload)
            echo = self.drain(expected, timeout=2.0)
            while len(echo) < expected:
                more = self.drain(expected - len(echo), timeout=2.0)
                if not more:
                    break
                echo += more
            if echo[: len(frame)] != frame:
                raise RuntimeError(
                    f"header echo mismatch at 0x{address + offset:08X}: {echo[: len(frame)].hex()}"
                )

    def jump(self, address: int) -> None:
        frame = bytes([OPCODE_JUMP]) + struct.pack(">L", address)
        self.ser.write(frame)
        self.ser.flush()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("port")
    parser.add_argument("--da", type=Path, default=DEFAULT_DA)
    args = parser.parse_args()

    da = args.da.read_bytes()
    trimmed = da.rstrip(b"\x00")
    if len(trimmed) % 4:
        trimmed += b"\x00" * (4 - len(trimmed) % 4)
    print(f"DA: {args.da.name}  {len(da)} bytes (trimmed {len(trimmed)})")

    ser = serial.Serial(args.port, 115200, timeout=0.05)
    try:
        try:
            ser.dtr = True
            ser.rts = True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.1)
        brom = BootRom(ser)

        print(f"1) writing DA to 0x{BASE_ADDR_DA:08X} ...")
        brom.write_words_payload(BASE_ADDR_DA, trimmed)
        print("   done")

        print("2) read-back verification ...")
        readback = brom.read32(BASE_ADDR_DA, len(trimmed) // 4)
        if readback == trimmed:
            print("   identical")
        else:
            diffs = sum(1 for a, b in zip(readback, trimmed, strict=False) if a != b)
            print(f"   MISMATCH ({diffs}/{len(trimmed)} bytes differ) - aborting")
            return 3

        print(f"3) JUMP 0x{BASE_ADDR_DA:08X} ...")
        brom.jump(BASE_ADDR_DA)
        time.sleep(0.5)

        print("4) reading DA info report ...")
        report = brom.drain(64 + DA_INFO_SIZE, timeout=3.0)
        print(f"   raw: {report.hex(' ')}")
        sync_index = report.find(b"\xc0")
        if sync_index < 0:
            print("   [!] no 0xC0 SYNC byte - DA did not start")
            return 4
        info = report[sync_index : sync_index + DA_INFO_SIZE]
        fields = (
            "sync_char",
            "da_version",
            "flash_device_id",
            "flash_size",
            "flash_manufacturer_code",
            "flash_device_code",
            "flash_ext_device_code1",
            "flash_ext_device_code2",
            "ext_sram_size",
        )
        values = dict(zip(fields, struct.unpack(DA_INFO_FORMAT, info), strict=True))
        for key, value in values.items():
            print(
                f"   {key:<24} = {value} (0x{value:X})"
                if isinstance(value, int)
                else f"   {key:<24} = {value}"
            )
        if values["da_version"] != 0x0308:
            print(f"   [!] unexpected DA version 0x{values['da_version']:04X}")
            return 5
        print("\n*** DA is running ***")
        return 0
    finally:
        ser.close()


if __name__ == "__main__":
    raise SystemExit(main())
