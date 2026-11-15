"""Prove the BootROM WRITE16 path on real hardware before touching flash.

Non-destructive: writes a pattern into RAM (0x00102000), reads it back, and
reports whether the framing, count unit and byte order are right.

The BootROM frame is:
    0xA1  WRITE16   addr(BE32)  count(BE32, in 16-bit words)  data...
    every sent byte is echoed by the chip

Then:
    0xAF  READ32    addr(BE32)  count(BE32, in 32-bit dwords) -> data

Usage: python tools/test_ram_write.py COM3
"""

from __future__ import annotations

import struct
import sys
import time

import serial

OPCODE_WRITE16 = 0xA1
OPCODE_READ32 = 0xAF

TEST_ADDR = 0x00102000  # scratch RAM, far from the DA area at 0x00100800
PATTERN = bytes.fromhex("deadbeefcafebabe1122334455667788")


class Brom:
    def __init__(self, ser: serial.Serial) -> None:
        self.ser = ser

    def _read_exact(self, count: int, timeout: float = 2.0) -> bytes:
        buf = b""
        deadline = time.time() + timeout
        while len(buf) < count:
            chunk = self.ser.read(count - len(buf))
            if chunk:
                buf += chunk
            elif time.time() > deadline:
                raise TimeoutError(f"wanted {count} bytes, got {len(buf)}")
        return buf

    def write16(self, address: int, data: bytes, *, byte_swap: bool) -> None:
        if len(data) % 2:
            raise ValueError("data must be even length")
        words = len(data) // 2
        payload = data
        if byte_swap:
            payload = b"".join(data[i : i + 2][::-1] for i in range(0, len(data), 2))
        frame = bytes([OPCODE_WRITE16]) + struct.pack(">L", address) + struct.pack(">L", words)
        self.ser.reset_input_buffer()
        self.ser.write(frame)
        self.ser.flush()
        echo = self._read_exact(len(frame), timeout=1.0)
        if echo != frame:
            raise RuntimeError(f"header echo mismatch: sent {frame.hex()} got {echo.hex()}")
        # stream the payload, checking the echo
        for index, byte in enumerate(payload):
            self.ser.write(bytes([byte]))
            self.ser.flush()
            reply = self._read_exact(1, timeout=1.0)
            if reply[0] != byte:
                raise RuntimeError(
                    f"payload echo mismatch at byte {index}: sent {byte:02x} got {reply[0]:02x}"
                )

    def read32(self, address: int, dwords: int) -> bytes:
        frame = bytes([OPCODE_READ32]) + struct.pack(">L", address) + struct.pack(">L", dwords)
        self.ser.reset_input_buffer()
        self.ser.write(frame)
        self.ser.flush()
        echo = self._read_exact(len(frame), timeout=1.0)
        if echo != frame:
            raise RuntimeError(f"read header echo mismatch: {echo.hex()}")
        raw = self._read_exact(dwords * 4, timeout=3.0)
        return b"".join(raw[i : i + 4][::-1] for i in range(0, len(raw), 4))


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "COM3"
    ser = serial.Serial(port, 115200, timeout=0.1)
    try:
        try:
            ser.dtr = True
            ser.rts = True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.1)
        brom = Brom(ser)

        print(f"RAM write test at 0x{TEST_ADDR:08X}, pattern {PATTERN.hex(' ')}")
        before = brom.read32(TEST_ADDR, len(PATTERN) // 4)
        print(f"  before: {before.hex(' ')}")

        for byte_swap in (False, True):
            label = "byte_swap=True " if byte_swap else "byte_swap=False"
            try:
                brom.write16(TEST_ADDR, PATTERN, byte_swap=byte_swap)
            except Exception as exc:  # noqa: BLE001
                print(f"  {label}: WRITE FAILED: {exc}")
                continue
            time.sleep(0.05)
            got = brom.read32(TEST_ADDR, len(PATTERN) // 4)
            match = got == PATTERN
            print(f"  {label}: read back {got.hex(' ')}  match={match}")
            if match:
                print("\n*** RAM write works with this byte order ***")
                return 0
    finally:
        ser.close()
    print("\nno byte order round-tripped the pattern")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
