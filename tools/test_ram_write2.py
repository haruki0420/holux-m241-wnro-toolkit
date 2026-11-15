"""Find the WRITE16 framing that the real M-241 BootROM accepts (RAM only).

Non-destructive: only writes to RAM at 0x00102000, never to flash.

Earlier attempt failed because the payload was fed byte-by-byte with a flush and
a 1-byte read after each byte; the chip's echo arrives as a stream, so the reads
drifted out of sync. This version sends the whole frame + payload in one write
and then drains every echo byte, trying both count units.

Usage: python tools/test_ram_write2.py COM3
"""

from __future__ import annotations

import struct
import sys
import time

import serial

OPCODE_WRITE16 = 0xA1
OPCODE_READ32 = 0xAF
OPCODE_CHECKSUM = 0xA4

TEST_ADDR = 0x00102000
PATTERN = bytes.fromhex("deadbeefcafebabe1122334455667788")


def drain(ser: serial.Serial, count: int, timeout: float = 2.0) -> bytes:
    buf = b""
    deadline = time.time() + timeout
    while len(buf) < count:
        chunk = ser.read(count - len(buf))
        if chunk:
            buf += chunk
        elif time.time() > deadline:
            break
    return buf


def read32(ser: serial.Serial, address: int, dwords: int) -> bytes:
    frame = bytes([OPCODE_READ32]) + struct.pack(">L", address) + struct.pack(">L", dwords)
    ser.reset_input_buffer()
    ser.write(frame)
    ser.flush()
    time.sleep(0.02)
    echo = drain(ser, len(frame))
    if echo != frame:
        raise RuntimeError(f"read echo {echo.hex()}")
    raw = drain(ser, dwords * 4)
    return b"".join(raw[i : i + 4][::-1] for i in range(0, len(raw), 4))


def write_pattern(ser: serial.Serial, address: int, data: bytes, count_unit: str) -> str:
    """Send 0xA1 frame + payload in one shot, then drain the echo."""
    if len(data) % 2:
        raise ValueError("even length only")
    count = len(data) // 2 if count_unit == "words" else len(data)
    frame = bytes([OPCODE_WRITE16]) + struct.pack(">L", address) + struct.pack(">L", count)
    ser.reset_input_buffer()
    ser.write(frame + data)
    ser.flush()
    expected = len(frame) + len(data)
    echo = drain(ser, expected, timeout=1.5)
    if echo[: len(frame)] != frame:
        return f"header echo mismatch: sent {frame.hex()} got {echo[: len(frame)].hex()}"
    if len(echo) < expected:
        return f"short echo: {len(echo)}/{expected} bytes"
    return "ok"


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "COM3"
    ser = serial.Serial(port, 115200, timeout=0.05)
    try:
        try:
            ser.dtr = True
            ser.rts = True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.1)

        print(f"target RAM 0x{TEST_ADDR:08X}, pattern {PATTERN.hex(' ')}")
        print(f"  before: {read32(ser, TEST_ADDR, 4).hex(' ')}\n")

        for count_unit in ("words", "bytes"):
            for swap in (False, True):
                payload = PATTERN
                if swap:
                    payload = b"".join(PATTERN[i : i + 2][::-1] for i in range(0, len(PATTERN), 2))
                label = f"count={count_unit:<5} swap={swap!s:<5}"
                try:
                    status = write_pattern(ser, TEST_ADDR, payload, count_unit)
                except Exception as exc:  # noqa: BLE001
                    print(f"  {label}: exception {exc}")
                    continue
                if status != "ok":
                    print(f"  {label}: {status}")
                    continue
                time.sleep(0.05)
                got = read32(ser, TEST_ADDR, 4)
                print(
                    f"  {label}: ok, read-back {got.hex(' ')}  "
                    f"match={'YES' if got == PATTERN else 'no'}"
                )
                if got == PATTERN:
                    print(f"\n*** working combination: count={count_unit}, byte_swap={swap} ***")
                    return 0
        print("\nno combination round-tripped")
        return 1
    finally:
        ser.close()


if __name__ == "__main__":
    raise SystemExit(main())
