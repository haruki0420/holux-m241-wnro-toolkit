"""Definitive MTK BootROM sync: keep streaming 0xA0 *while* generating the reset edge.

Why this exists
---------------
Every earlier attempt pulsed RTS/DTR, slept 0.3-0.4 s, and only then started
poking 0xA0. Many MTK bootloaders only listen for the sync during a short window
right after reset; if the host arrives late the chip has already fallen back to
the application firmware (which does not answer 0xA0).

This tool removes the gap: the 0xA0 stream runs in a thread from before the
reset edge, so the bootloader sees the probe immediately.

It also sweeps the reset polarity (which modem-control line, high or low),
because the CP210x board wiring is unknown.

Nothing is written to flash. If this fails the device needs a physical power
cycle (battery out/in) and another run.

Usage: python tools/hardsync.py COM3
"""

from __future__ import annotations

import contextlib
import itertools
import sys
import threading
import time

import serial

STOP = threading.Event()


def streamer(ser: serial.Serial) -> None:
    """Continuously write 0xA0 until STOP is set."""
    while not STOP.is_set():
        try:
            ser.write(b"\xa0")
            ser.flush()
        except Exception:  # noqa: BLE001 - port may be mid-reconfigure
            return
        time.sleep(0.001)


def attempt(port: str, baud: int, line: str, level: bool, hold: float) -> str:
    """Stream 0xA0, flip *line* to *level* as a reset edge, and watch for 0x5F."""
    STOP.clear()
    try:
        ser = serial.Serial(port, baud, timeout=0.01)
    except Exception as exc:  # noqa: BLE001
        return f"open failed: {exc}"
    try:
        ser.reset_input_buffer()
        # Start the probe stream first, then generate the reset edge.
        worker = threading.Thread(target=streamer, args=(ser,), daemon=True)
        worker.start()
        other = "dtr" if line == "rts" else "rts"
        try:
            setattr(ser, other, False)
            setattr(ser, line, not level)  # assert reset
            time.sleep(0.02)
            setattr(ser, line, level)  # release reset -> chip boots
            time.sleep(hold)
        except Exception:  # noqa: BLE001
            pass

        # Read whatever the chip says while the stream keeps running.
        got = b""
        deadline = time.time() + 1.5
        while time.time() < deadline:
            chunk = ser.read(4096)
            if chunk:
                got += chunk
                if b"\x5f" in got:
                    break
        STOP.set()
        worker.join(timeout=0.5)

        if b"\x5f" not in got:
            return f"no 0x5F ({len(got)}B {got[:12].hex()!r})"

        # Complete the dance now that the chip is listening.
        ser.reset_input_buffer()
        for probe, expect in ((b"\x0a", 0xF5), (b"\x50", 0xAF), (b"\x05", 0xFA)):
            ser.write(probe)
            ser.flush()
            reply = ser.read(1)
            if not reply:
                return "0x5F ok, then silence"
            if reply[0] != expect:
                return f"0x5F ok, {probe.hex()} -> 0x{reply[0]:02X} (want 0x{expect:02X})"
        return "SYNC COMPLETE"
    finally:
        STOP.set()
        with contextlib.suppress(Exception):
            ser.close()


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "COM3"
    combos = list(itertools.product((115200, 38400), ("rts", "dtr"), (True, False)))
    print(f"stream-while-resetting on {port} ({len(combos)} combos)")
    for baud, line, level in combos:
        verdict = attempt(port, baud, line, level, hold=0.05)
        print(f"  baud={baud:>6} {line.upper()}={str(level):<5} -> {verdict}", flush=True)
        if "SYNC COMPLETE" in verdict:
            print(f"\n*** BootROM synced: baud={baud}, {line.upper()}={level} ***")
            print("    (note this combination; the toolkit can be updated to match)")
            return 0
        time.sleep(0.4)
    print("\nno combination synced - the device likely needs a physical power cycle")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
