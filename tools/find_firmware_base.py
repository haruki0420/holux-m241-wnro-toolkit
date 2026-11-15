"""Locate the M-241 firmware image inside the MT3318 BootROM address space.

The BootROM read protocol is now confirmed on hardware:
    0xAF READ32   addr(BE32) count(BE32)   where count is in 16-bit WORDS
    reply: echo(9 bytes) + data(count*4 bytes) + XOR16 checksum(2 bytes)

Reading 0x20000000 returned `42 20 02 02` repeated, which is not a firmware
image. The firmware starts with a vector table of `18 f0 9f e5` (= ldr pc,
[pc, #-0x18]), so this tool scans candidate bases for that magic and reports
what it finds.

Read-only.

Usage: python tools/find_firmware_base.py COM3
"""

from __future__ import annotations

import struct
import sys
import time

import serial

OPCODE_READ32 = 0xAF
MAGIC = bytes.fromhex("18f09fe5")

# Candidate windows for the flash mapping on MT3318-class parts.
CANDIDATES = [
    0x00000000,  # flat / linear view
    0x20000000,  # typical MT333x SPI flash window
    0x08000000,
    0x10000000,
    0x30000000,
]


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
                raise TimeoutError(f"wanted {count}, got {len(buf)}")
        return buf

    def read_words(self, address: int, words: int) -> bytes:
        frame = bytes([OPCODE_READ32]) + struct.pack(">L", address) + struct.pack(">L", words)
        self.ser.reset_input_buffer()
        self.ser.write(frame)
        self.ser.flush()
        echo = self._read_exact(len(frame), timeout=1.0)
        if echo != frame:
            raise RuntimeError(f"echo mismatch: sent {frame.hex()} got {echo.hex()}")
        data = self._read_exact(words * 4, timeout=3.0)
        self._read_exact(2, timeout=1.0)  # checksum
        return data


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "COM3"
    ser = serial.Serial(port, 115200, timeout=0.1)
    try:
        try:
            ser.dtr = True
            ser.rts = True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.15)
        brom = Brom(ser)

        print("scanning candidate bases for the 18 f0 9f e5 vector table\n")
        for base in CANDIDATES:
            try:
                head = brom.read_words(base, 8)  # 32 bytes
            except Exception as exc:  # noqa: BLE001
                print(f"  0x{base:08X}: error {exc}")
                continue
            marker = "  <== FIRMWARE MAGIC" if head.startswith(MAGIC) else ""
            print(f"  0x{base:08X}: {head.hex(' ')}{marker}")

        print("\nscanning first 64 KiB of each base in 64-word steps for the magic")
        for base in CANDIDATES:
            found = []
            for offset in range(0, 0x10000, 0x100):
                try:
                    chunk = brom.read_words(base + offset, 64)
                except Exception:  # noqa: BLE001
                    break
                index = chunk.find(MAGIC)
                if index >= 0 and index % 4 == 0:
                    found.append(base + offset + index)
            if found:
                print(f"  0x{base:08X}: magic at {[hex(f) for f in found[:6]]}")
    finally:
        ser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
