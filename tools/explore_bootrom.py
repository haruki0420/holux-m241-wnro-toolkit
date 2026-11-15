"""BootROM entry explorer for the real M-241 (read-only).

The standard `m241 dump` path (RTS/DTR pulse -> 0xA0 sync) failed on hardware.
This tool tries the documented and speculative entry routes one at a time and
prints exactly what the device does after each, so we can find the one that
works without guessing.

Nothing here writes to flash. The worst case is that the device ends up in
BootROM mode, which a power cycle fixes.

Usage: python tools/explore_bootrom.py COM3
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


def listen(ser: serial.Serial, seconds: float) -> bytes:
    got = b""
    deadline = time.time() + seconds
    while time.time() < deadline:
        chunk = ser.read(4096)
        if chunk:
            got += chunk
            deadline = time.time() + 0.2
    return got


def sync_dance(ser: serial.Serial, timeout: float = 3.0) -> str:
    """Send 0xA0 until 0x5F; returns a human readable verdict."""
    ser.reset_input_buffer()
    seen = b""
    deadline = time.time() + timeout
    while time.time() < deadline:
        ser.write(b"\xa0")
        ser.flush()
        byte = ser.read(1)
        if byte:
            seen += byte
            if byte == b"\x5f":
                for probe, expect in ((b"\x0a", 0xF5), (b"\x50", 0xAF), (b"\x05", 0xFA)):
                    ser.write(probe)
                    ser.flush()
                    reply = ser.read(1)
                    if not reply:
                        return f"0x5F ok, then NO reply to {probe.hex()}"
                    if reply[0] != expect:
                        return f"0x5F ok, then 0x{reply[0]:02X} != 0x{expect:02X} for {probe.hex()}"
                return "SYNC COMPLETE"
        time.sleep(0.002)
    return f"no 0x5F (saw {seen[:8].hex()!r})"


def route_rts_dtr(port: str) -> str:
    """Pulse DTR/RTS then hammer 0xA0 at 115200."""
    with serial.Serial(port, 115200, timeout=0.02) as ser:
        for line in ("dtr", "rts"):
            setattr(ser, line, False)
            time.sleep(0.05)
            setattr(ser, line, True)
            time.sleep(0.05)
        time.sleep(0.4)
        return sync_dance(ser)


def route_rts_only(port: str) -> str:
    """Some CP210x boards wire modem-control differently; try RTS alone."""
    with serial.Serial(port, 115200, timeout=0.02) as ser:
        ser.dtr = False
        ser.rts = False
        time.sleep(0.1)
        ser.rts = True
        time.sleep(0.1)
        ser.rts = False
        time.sleep(0.3)
        return sync_dance(ser)


def route_pmtk180(port: str) -> str:
    """Send $PMTK180*3B at 38400, then sync at 115200."""
    with serial.Serial(port, 38400, timeout=0.05) as ser:
        time.sleep(0.3)
        ser.reset_input_buffer()
        ser.write(nmea("PMTK180"))
        ser.flush()
        time.sleep(0.5)
        answer = listen(ser, 0.5)
    time.sleep(0.3)
    with serial.Serial(port, 115200, timeout=0.02) as ser:
        verdict = sync_dance(ser, timeout=4.0)
    return f"pmtk180 reply={answer[:60]!r} -> {verdict}"


def route_baud_sweep(port: str) -> str:
    """BootROM may already be listening but at an unexpected baud."""
    results = []
    for baud in (115200, 38400, 57600, 230400, 460800):
        try:
            with serial.Serial(port, baud, timeout=0.02) as ser:
                verdict = sync_dance(ser, timeout=1.5)
            results.append(f"{baud}:{verdict}")
        except Exception as exc:  # noqa: BLE001
            results.append(f"{baud}:ERROR {exc}")
    return " | ".join(results)


ROUTES = (
    ("RTS+DTR pulse @115200", route_rts_dtr),
    ("RTS only @115200", route_rts_only),
    ("$PMTK180*3B @38400", route_pmtk180),
    ("baud sweep 0xA0", route_baud_sweep),
)


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "COM3"
    for label, route in ROUTES:
        print(f"== {label} ==", flush=True)
        try:
            verdict = route(port)
        except Exception as exc:  # noqa: BLE001
            verdict = f"EXCEPTION {exc}"
        print(f"   -> {verdict}", flush=True)
        if "SYNC COMPLETE" in verdict:
            print("\n*** BootROM is reachable via this route ***")
            return 0
        # let the device settle before the next attempt
        time.sleep(1.0)
    print("\nno route reached BootROM")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
