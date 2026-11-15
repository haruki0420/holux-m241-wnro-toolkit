"""Dump the M-241 firmware from the MT3318 BootROM (read-only).

Hardware-confirmed, 2026-11-15 (Windows 11, COM3, M-241 V1.13 / MT3318):

  Firmware base      : 0x00200000   (NOT 0x20000000)
  Read opcode        : 0xAF READ32, count field is in 16-bit WORDS
  Reply shape        : echo(9 bytes) + data(words*4) ; NO checksum trailer
  Data byte order    : each 32-bit word arrives byte-reversed
                       (0x00200000 reads `e5 9f f0 18` for `18 f0 9f e5`)

Entering the BootROM (this is the part that took the longest to find):

  1. open at 38400, send `$PMTK180*3B\\r\\n`
  2. NMEA stops: the chip is listening
  3. reopen at 115200, assert DTR and RTS and hold them
  4. send 0xA0 until 0x5F (up to a few seconds; 36 probes on this unit)
  5. send 0x0A/0x50/0x05 **back to back, no delay** -> 0xF5/0xAF/0xFA

Step 5 is the discovery: pausing between the probes makes the handshake time out,
because the chip drops out if the sequence is not immediate.

Read-only. Nothing is ever written to flash.

Usage:
    python tools/dump_firmware_hw.py COM3 -o backup/m241_stock.bin
    python tools/dump_firmware_hw.py COM3 -o out.bin --length 0x6345C
"""

from __future__ import annotations

import argparse
import hashlib
import struct
import sys
import time
from pathlib import Path

import serial

FIRMWARE_BASE = 0x00200000
NMEA_BAUD = 38400
BOOTROM_BAUD = 115200
PROBE = 0xA0
PROBE_REPLIES = (0x5F, 0xF5, 0xAF, 0xFA)
OPCODE_READ32 = 0xAF
CHUNK_WORDS = 0x40  # 256 bytes per read


def open_at(port: str, baud: int, *, assert_lines: bool) -> serial.Serial:
    ser = serial.Serial(port, baud, timeout=0.05)
    if assert_lines:
        try:
            ser.dtr = True
            ser.rts = True
        except Exception:  # noqa: BLE001
            pass
    return ser


def switch_to_bootrom(port: str) -> bool:
    """Send $PMTK180 at 38400 and confirm the NMEA stream stops."""
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
            print("[!] no NMEA at 38400: is the logger switched on?", file=sys.stderr)
            return False
        ser.reset_input_buffer()
        ser.write(b"$PMTK180*3B\r\n")
        ser.flush()
        time.sleep(0.15)
        after = ser.read(8192)
    if after:
        print(f"[!] NMEA still running after PMTK180 ({len(after)} bytes)", file=sys.stderr)
        return False
    return True


def handshake(ser: serial.Serial, timeout: float = 8.0) -> bool:
    """0xA0 until 0x5F, then 0x0A/0x50/0x05 immediately."""
    deadline = time.time() + timeout
    probes = 0
    while time.time() < deadline:
        ser.write(bytes([PROBE]))
        ser.flush()
        probes += 1
        reply = ser.read(1)
        if reply and reply[0] == PROBE_REPLIES[0]:
            break
        time.sleep(0.001)
    else:
        print(f"[!] no 0x5F after {probes} probes", file=sys.stderr)
        return False

    for index, probe in enumerate((0x0A, 0x50, 0x05), start=1):
        ser.write(bytes([probe]))
        ser.flush()
        reply = ser.read(1)
        expected = PROBE_REPLIES[index]
        if not reply or reply[0] != expected:
            got = f"0x{reply[0]:02X}" if reply else "nothing"
            print(f"[!] step {index + 1}: sent 0x{probe:02X}, got {got} "
                  f"(expected 0x{expected:02X})", file=sys.stderr)
            return False
    return True


class BootRomReader:
    def __init__(self, ser: serial.Serial) -> None:
        self.ser = ser

    def _read_exact(self, count: int, timeout: float = 4.0) -> bytes:
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
        if echo != frame:
            raise RuntimeError(
                f"echo mismatch at 0x{address:08X}: sent {frame.hex()} got {echo.hex()}"
            )
        data = self._read_exact(words * 4, timeout=5.0)
        # Each 32-bit word arrives byte-reversed.
        return b"".join(data[i : i + 4][::-1] for i in range(0, len(data), 4))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("port")
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--base", type=lambda x: int(x, 0), default=FIRMWARE_BASE)
    parser.add_argument("--length", type=lambda x: int(x, 0), default=None)
    parser.add_argument("--already-synced", action="store_true",
                        help="skip the PMTK180 + sync dance")
    args = parser.parse_args()

    if not args.already_synced:
        print(f"[1/3] switching {args.port} to BootROM via $PMTK180 ...", flush=True)
        if not switch_to_bootrom(args.port):
            return 2
        print("      NMEA stopped -> chip is listening", flush=True)
    else:
        print("[1/3] assuming the chip is already synced", flush=True)

    ser = open_at(args.port, BOOTROM_BAUD, assert_lines=True)
    try:
        ser.reset_input_buffer()
        if not args.already_synced:
            print(f"[2/3] handshake at {BOOTROM_BAUD} ...", flush=True)
            if not handshake(ser):
                return 3
            print("      sync complete: 0x5F 0xF5 0xAF 0xFA", flush=True)
        else:
            print("[2/3] skipping handshake", flush=True)

        reader = BootRomReader(ser)

        length = args.length
        if length is None:
            header = reader.read_words(args.base + 0xF4, 1)
            declared = struct.unpack("<I", header)[0]
            print(f"      size header at 0x{args.base + 0xF4:X} = {declared}")
            if not 0x1000 <= declared <= 0x400000:
                print("[!] implausible size; pass --length", file=sys.stderr)
                return 4
            length = declared + 4  # the header stores size - 4

        print(f"[3/3] reading {length} bytes from 0x{args.base:08X}", flush=True)
        image = bytearray()
        cursor = args.base
        remaining = length
        started = time.time()
        while remaining:
            words = min(CHUNK_WORDS, (remaining + 3) // 4)
            chunk = reader.read_words(cursor, words)
            take = min(len(chunk), remaining)
            image.extend(chunk[:take])
            cursor += take
            remaining -= take
            pct = int(len(image) * 100 / length)
            sys.stdout.write(f"\r      {pct:3d}%  {len(image)}/{length}  "
                             f"({time.time() - started:.1f}s)")
            sys.stdout.flush()
        print()
    finally:
        ser.close()

    data = bytes(image)
    digest = hashlib.sha256(data).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)

    print(f"\nsaved   : {args.output}")
    print(f"size    : {len(data)}")
    print(f"sha256  : {digest}")
    print(f"first 16: {data[:16].hex(' ')}")

    # Recognise the two stock images this project ships profiles for.
    known = {
        "4103601a29f256ae5da771bbcbc3ca85a89deb6268ce74865dc84b2583ae98f7":
            "stock V1.12",
        "32fe52690db9947bdbfa58caa6dba450b3f4222b23f2e045f606252649159435":
            "stock V1.13",
    }
    if digest in known:
        print(f"\n*** this is a KNOWN image: {known[digest]} ***")
        print("    your dump is byte-identical to the official package,")
        print("    which means the read path is proven correct.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
