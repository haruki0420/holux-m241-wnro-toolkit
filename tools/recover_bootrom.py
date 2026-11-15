"""Recover a device that went silent after a reset pulse, and get into BootROM.

The M-241 stopped emitting NMEA after the first BootROM attempt. Two plausible
causes:

  a) it is sitting in BootROM but at a baud/sync shape we have not tried, or
  b) the USB-UART link lost the device's UART (needs a true device power cycle).

This tool does the non-destructive things that can dislodge (a):
  * long continuous 0xA0 stream at 115200 (no per-byte pacing),
  * a 0xA0 stream preceded by a long "quiet" period,
  * classic MTK pattern: send 0xA0, then 0x0A/0x50/0x05 as a burst,
  * a full baud sweep with continuous streaming.

It also reports whether NMEA comes back, which distinguishes (b).

Usage: python tools/recover_bootrom.py COM3
"""

from __future__ import annotations

import sys
import time

import serial

DOLLAR = chr(36)


def nmea(body: str) -> bytes:
    value = 0
    for char in body:
        value ^= ord(char)
    return f"{DOLLAR}{body}*{value:02X}\r\n".encode()


def read_available(ser: serial.Serial, seconds: float) -> bytes:
    got = b""
    deadline = time.time() + seconds
    while time.time() < deadline:
        chunk = ser.read(4096)
        if chunk:
            got += chunk
            deadline = time.time() + 0.15
    return got


def nmea_alive(port: str) -> bool:
    try:
        with serial.Serial(port, 38400, timeout=0.2) as ser:
            time.sleep(0.2)
            ser.reset_input_buffer()
            return len(read_available(ser, 1.5)) > 0
    except Exception:  # noqa: BLE001
        return False


def long_stream(port: str, baud: int, seconds: float) -> str:
    with serial.Serial(port, baud, timeout=0.01) as ser:
        ser.reset_input_buffer()
        deadline = time.time() + seconds
        while time.time() < deadline:
            ser.write(b"\xa0" * 32)
            ser.flush()
            time.sleep(0.003)
        got = read_available(ser, 0.6)
    if b"\x5f" in got:
        return "0x5F SEEN"
    return f"silent ({len(got)}B)" if got else "silent"


def burst_sync(port: str) -> str:
    """Send the whole 4-byte dance as one burst, then read."""
    with serial.Serial(port, 115200, timeout=0.01) as ser:
        ser.reset_input_buffer()
        for _ in range(40):
            ser.write(b"\xa0")
            ser.flush()
            time.sleep(0.001)
        ser.write(b"\x0a\x50\x05")
        ser.flush()
        got = read_available(ser, 0.5)
    return f"reply={got[:16].hex()!r}" if got else "silent"


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "COM3"

    print("baseline: is the device emitting NMEA?", flush=True)
    alive = nmea_alive(port)
    print(f"  NMEA alive: {alive}", flush=True)

    print("\n1) per-baud continuous 0xA0 stream", flush=True)
    for baud in (115200, 38400, 57600, 230400, 460800, 921600):
        verdict = long_stream(port, baud, 2.0)
        print(f"   {baud:>7}: {verdict}", flush=True)
        if "0x5F" in verdict:
            print(f"\n*** BootROM answered at {baud} ***")
            return 0

    print("\n2) full dance as one burst @115200", flush=True)
    print(f"   {burst_sync(port)}", flush=True)

    print("\n3) NMEA check after attempts", flush=True)
    alive_after = nmea_alive(port)
    print(f"  NMEA alive: {alive_after}", flush=True)
    if not alive and not alive_after:
        print("\nThe device never came back: this looks like a power/link issue,")
        print("not a protocol issue. Power-cycle the M-241 (battery out/in, or the")
        print("slide switch OFF then ON) and re-run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
