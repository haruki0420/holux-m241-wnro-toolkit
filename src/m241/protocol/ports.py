"""Serial-port discovery, candidate heuristics and port opening.

The Holux M-241 exposes a Silicon Labs CP210x USB-serial bridge.  Other
adapters (Prolific PL2303, FTDI, WCH CH340) are common stand-ins, so the
candidate heuristic accepts the known bridge vendors plus a set of textual
hints in the OS-provided description/hardware id.

`pyserial` is imported lazily inside the functions of this module so that
``import m241`` (and every command that does not touch hardware) keeps working
on machines without pyserial installed.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any

from m241.errors import M241Error

SI_LABS_VID = 0x10C4
CP210X_PID = 0xEA60
PL2303 = (0x067B, 0x2303)
FTDI = (0x0403, None)
CH340 = (0x1A86, 0x7523)

#: Vendor ids that mean "USB-serial bridge" regardless of the exact pid.
_BRIDGE_VIDS = (SI_LABS_VID, PL2303[0], FTDI[0], CH340[0])

#: Lower-case substrings that mark a port as a plausible M-241 connection.
_TEXT_HINTS = (
    "cp210",
    "silicon labs",
    "silabs",
    "prolific",
    "pl2303",
    "ftdi",
    "ch340",
    "ch341",
    "usb-serial",
    "usb serial",
    "usb to serial",
    "uart bridge",
    "holux",
    "m-241",
    "m241",
    "gps",
)


@dataclass(frozen=True)
class PortInfo:
    """A serial port as reported by the operating system."""

    device: str
    description: str = ""
    hwid: str = ""
    vid: int | None = None
    pid: int | None = None
    is_candidate: bool = False


def _is_candidate(vid: int | None, pid: int | None, description: str, hwid: str) -> bool:
    """Return True when a port looks like an M-241 (or a generic USB-serial bridge)."""
    if vid is not None:
        if vid == SI_LABS_VID:
            return True
        if vid == PL2303[0] and pid in (None, PL2303[1]):
            return True
        if vid == FTDI[0]:
            return True
        if vid == CH340[0] and pid in (None, CH340[1]):
            return True
    haystack = f"{description} {hwid}".lower()
    return any(hint in haystack for hint in _TEXT_HINTS)


def _comports() -> list[Any]:
    """Return the raw pyserial port entries, raising M241Error on failure."""
    try:
        from serial.tools import list_ports
    except ImportError as exc:  # pragma: no cover - pyserial is a hard dependency
        raise M241Error(
            "pyserial is not installed; install it with 'pip install pyserial'"
        ) from exc
    try:
        return list(list_ports.comports())
    except Exception as exc:  # noqa: BLE001 - OS backends raise platform-specific errors
        raise M241Error(f"could not enumerate serial ports: {exc}") from exc


def list_serial_ports() -> list[PortInfo]:
    """List every serial port on this machine, M-241 candidates first."""
    infos: list[PortInfo] = []
    for entry in _comports():
        device = str(getattr(entry, "device", "") or "")
        if not device:
            continue
        description = str(getattr(entry, "description", "") or "")
        hwid = str(getattr(entry, "hwid", "") or "")
        vid = getattr(entry, "vid", None)
        pid = getattr(entry, "pid", None)
        infos.append(
            PortInfo(
                device=device,
                description=description,
                hwid=hwid,
                vid=vid,
                pid=pid,
                is_candidate=_is_candidate(vid, pid, description, hwid),
            )
        )
    # Candidates first, each group keeping the OS enumeration order (stable).
    infos.sort(key=lambda info: not info.is_candidate)
    return infos


def candidate_ports() -> list[PortInfo]:
    """Return only the ports that look like an M-241 / USB-serial bridge."""
    return [info for info in list_serial_ports() if info.is_candidate]


def resolve_port(spec: str | None) -> str:
    """Resolve a user supplied port specification to a concrete device name.

    ``None``, ``"auto"`` and an empty string pick the single candidate port and
    raise :class:`M241Error` listing the candidates otherwise.  Anything else is
    treated as a raw device path/COM name and returned unchanged.
    """
    if spec is not None:
        text = spec.strip()
        if text and text.lower() not in ("auto", "default"):
            return text

    candidates = candidate_ports()
    if len(candidates) == 1:
        return candidates[0].device
    if not candidates:
        detected = list_serial_ports()
        listed = ", ".join(info.device for info in detected) or "none"
        raise M241Error(
            f"no M-241 serial port found (serial ports detected: {listed}). "
            f"{platform_hint()} Pass the port explicitly, e.g. '--port COM7'."
        )
    listing = "\n".join(
        f"  {info.device}  {info.description}".rstrip() for info in candidates
    )
    raise M241Error(
        "multiple candidate serial ports found; choose one explicitly:\n" + listing
    )


def open_serial(port: str, baud: int = 115200, timeout: float = 1.0) -> Any:
    """Open ``port`` at ``baud`` and return the :class:`serial.Serial` instance.

    Any pyserial failure is re-raised as :class:`M241Error` with an
    OS-appropriate troubleshooting hint appended to the message.
    """
    try:
        import serial
    except ImportError as exc:  # pragma: no cover - pyserial is a hard dependency
        raise M241Error(
            "pyserial is not installed; install it with 'pip install pyserial'"
        ) from exc
    try:
        return serial.Serial(port=port, baudrate=baud, timeout=timeout)
    except (serial.SerialException, ValueError) as exc:
        raise M241Error(
            f"could not open serial port {port!r} at {baud} baud: {exc}. "
            f"{platform_hint()}"
        ) from exc


def platform_hint() -> str:
    """Return an OS-specific hint for troubleshooting serial-port problems."""
    platform = sys.platform
    if platform.startswith("win"):
        return (
            "On Windows: check Device Manager > Ports (COM & LPT), close any other "
            "program using the port, and install the Silicon Labs CP210x driver if no "
            "COM port appears."
        )
    if platform == "darwin":
        return (
            "On macOS: look for /dev/tty.usbserial-* or /dev/tty.SLAB_USBtoUART and "
            "install the CP210x VCP driver if the logger does not appear."
        )
    if platform.startswith("linux"):
        return (
            "On Linux: add your user to the 'dialout' group "
            "(sudo usermod -aG dialout $USER), log in again, and check 'dmesg | tail' "
            "right after plugging the logger in."
        )
    return (
        "Check that the USB-serial adapter is plugged in and that its driver is "
        "installed."
    )


__all__ = [
    "CH340",
    "CP210X_PID",
    "FTDI",
    "PL2303",
    "SI_LABS_VID",
    "PortInfo",
    "candidate_ports",
    "list_serial_ports",
    "open_serial",
    "platform_hint",
    "resolve_port",
]
