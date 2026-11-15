"""Probe the DA write commands on hardware without rewriting the firmware.

Steps:

  1. enter BootROM ($PMTK180 + sync),
  2. load and start the DA,
  3. send the 0xD3 range command in the form the reference implementation
     uses (one ten-byte frame) and report the ACK plus its status byte,
  4. optionally send 0xD5 and read the two ACKs.

Step 3 does set the memory range, which makes the DA erase the sectors that
range covers.  To stay non-destructive the range is placed in **unused** flash
space, well past the firmware image, and `--probe-only` stops before 0xD3 if you
just want the DA handshake.

Usage:
    python tools/test_da_protocol.py COM3 --probe-only
    python tools/test_da_protocol.py COM3 --range 0x00700000 0x100
"""

from __future__ import annotations

import argparse
import struct
import sys
import time
from pathlib import Path

import serial

OPCODE_WRITE16 = 0xA1
OPCODE_READ32 = 0xAF
OPCODE_JUMP = 0xA8

OPCODE_MEM_CMD = 0xD3
OPCODE_WRITE = 0xD5
ACK = 0x5A
NAK = 0xA5
CONT = 0x69

BASE_ADDR_DA = 0x00100800
FIRMWARE_BASE = 0x00200000
NMEA_BAUD = 38400
BOOTROM_BAUD = 115200

DEFAULT_DA = Path(
    r"vendor-drop\Holuxlogger_Utility_1.1.0.47"
    r"\Utility_1.1.0.47\DA_v3.8.4.bin"
)


class Link:
    def __init__(self, port: str) -> None:
        self.ser = serial.Serial(port, BOOTROM_BAUD, timeout=0.05)
        try:
            self.ser.dtr = True
            self.ser.rts = True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.1)

    def close(self) -> None:
        if self.ser is not None:
            self.ser.close()

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
        echo = self.drain(len(frame))
        if echo != frame:
            raise RuntimeError(f"read echo {echo.hex()}")
        raw = self.drain(dwords * 4, timeout=3.0)
        return b"".join(raw[i : i + 4][::-1] for i in range(0, len(raw), 4))

    def write16(self, address: int, data: bytes, *, byte_swap: bool = True) -> None:
        if len(data) % 2:
            data += b"\x00"
        payload = data
        if byte_swap:
            payload = b"".join(data[i : i + 2][::-1] for i in range(0, len(data), 2))
        frame = (
            bytes([OPCODE_WRITE16]) + struct.pack(">L", address) + struct.pack(">L", len(data) // 2)
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
            raise RuntimeError(f"write16 header echo mismatch: {echo[: len(frame)].hex()}")

    def jump(self, address: int) -> None:
        self.ser.write(bytes([OPCODE_JUMP]) + struct.pack(">L", address))
        self.ser.flush()

    def read_byte(self, timeout: float = 5.0) -> int | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            chunk = self.ser.read(1)
            if chunk:
                return chunk[0]
        return None


def enter_bootrom(port: str) -> bool:
    with serial.Serial(port, NMEA_BAUD, timeout=0.2) as ser:
        time.sleep(0.25)
        ser.reset_input_buffer()
        heard = b""
        deadline = time.time() + 1.5
        while time.time() < deadline:
            chunk = ser.read(4096)
            if chunk:
                heard += chunk
                deadline = time.time() + 0.25
        if not heard:
            print("[!] no NMEA at 38400: is the logger switched on?")
            return False
        ser.reset_input_buffer()
        ser.write(b"$PMTK180*3B\r\n")
        ser.flush()
        time.sleep(0.15)
        after = ser.read(8192)
    return not after


def sync(link: Link, timeout: float = 8.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        link.ser.write(b"\xa0")
        link.ser.flush()
        reply = link.ser.read(1)
        if reply and reply[0] == 0x5F:
            break
        time.sleep(0.001)
    else:
        print("[!] no 0x5F")
        return False
    for probe, expected in ((0x0A, 0xF5), (0x50, 0xAF), (0x05, 0xFA)):
        link.ser.write(bytes([probe]))
        link.ser.flush()
        reply = link.ser.read(1)
        if not reply or reply[0] != expected:
            got = f"0x{reply[0]:02X}" if reply else "nothing"
            print(f"[!] probe 0x{probe:02X} -> {got}, expected 0x{expected:02X}")
            return False
    return True


def start_da(link: Link, da_path: Path) -> bool:
    blob = da_path.read_bytes().rstrip(b"\x00")
    if len(blob) % 4:
        blob += b"\x00" * (4 - len(blob) % 4)
    print(f"  writing DA ({len(blob)} bytes) to 0x{BASE_ADDR_DA:08X} ...")
    link.write16(BASE_ADDR_DA, blob)
    back = link.read32(BASE_ADDR_DA, len(blob) // 4)
    if back != blob:
        print("  [!] read-back mismatch")
        return False
    link.jump(BASE_ADDR_DA)
    time.sleep(0.4)
    report = link.drain(64, timeout=3.0)
    idx = report.find(b"\xc0")
    if idx < 0:
        print(f"  [!] no DA sync byte: {report[:32].hex(' ')}")
        return False
    info = report[idx : idx + 20]
    values = struct.unpack(">BHBLHHHHL", info)
    print(
        f"  DA running: version 0x{values[1]:04X}, flash {values[3]} bytes, "
        f"device id 0x{values[2]:02X}, mfr 0x{values[4]:04X} dev 0x{values[5]:04X}"
    )
    return True


def probe_mem_cmd(link: Link, start: int, length: int) -> bool:
    """Send 0xD3 as one ten-byte frame and report the ACK plus its status byte."""
    end = start + length - 1
    print(f"  sending 0xD3: range 0x{start:08X}..0x{end:08X}")
    link.ser.write(struct.pack(">BBLL", OPCODE_MEM_CMD, 1, start, end))
    link.ser.flush()

    started = time.time()
    reply = link.read_byte(timeout=60.0)
    elapsed = time.time() - started
    if reply is None:
        print(f"  [!] no ACK after {elapsed:.1f}s")
        return False
    if reply == NAK:
        print(f"  [!] NAK after {elapsed:.1f}s")
        return False
    if reply != ACK:
        print(f"  [!] unexpected 0x{reply:02X} after {elapsed:.1f}s")
        return False
    # The ACK is always followed by a status byte; leaving it unread shifts
    # every later frame by one byte.
    status = link.read_byte(timeout=5.0)
    if status is None:
        print("  [!] ACK but no status byte")
        return False
    print(f"  ACK 0x5A + status 0x{status:02X} after {elapsed:.1f}s")
    return status == 0x00


def probe_write_cmd(link: Link, packet_len: int = 0x100) -> bool:
    print(f"  sending 0xD5 with packet length {packet_len}")
    link.ser.write(struct.pack(">BL", OPCODE_WRITE, packet_len))
    link.ser.flush()
    first = link.read_byte(timeout=5.0)
    second = link.read_byte(timeout=5.0)
    print(f"  replies: {first} {second} (want 0x5A 0x5A)")
    return first == ACK and second == ACK


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("port")
    parser.add_argument("--da", type=Path, default=DEFAULT_DA)
    parser.add_argument("--probe-only", action="store_true", help="do not send 0xD3/0xD5 at all")
    parser.add_argument(
        "--range",
        nargs=2,
        type=lambda x: int(x, 0),
        default=[FIRMWARE_BASE, 0x100],
        metavar=("ADDR", "LEN"),
        help="flash range for the 0xD3 probe (default: 0x100 bytes at the firmware base)",
    )
    args = parser.parse_args()

    print(f"[1/4] entering BootROM on {args.port}")
    if not enter_bootrom(args.port):
        return 2
    print("      NMEA stopped")

    link = Link(args.port)
    try:
        print("[2/4] sync")
        if not sync(link):
            return 3
        print("      sync complete")

        print("[3/4] load and start the DA")
        if not start_da(link, args.da):
            return 4

        if args.probe_only:
            print("[4/4] --probe-only: stopping before 0xD3")
            return 0

        print("[4/4] probing the flash-write commands")
        start, length = args.range
        if not probe_mem_cmd(link, start, length):
            return 5
        if not probe_write_cmd(link):
            return 6
        print("\n*** both write commands were accepted ***")
        print("    NOTE: sending 0xD3 already erased the sectors covering the range.")
        return 0
    finally:
        link.close()


if __name__ == "__main__":
    raise SystemExit(main())
