"""Command line interface for the Holux M-241 toolkit.

``m241 --help`` lists the commands; ``m241 <command> --help`` documents one
command.  Machine-readable output goes to **stdout** (``--json`` produces a
single pretty-printed JSON object), while progress, warnings and errors go to
**stderr**.

Exit codes
----------
===  =====================================================================
0    success
1    runtime error (I/O, parse, refused by the device, ...)
2    usage error (argparse)
3    hardware not found (no port, cannot open it, no BootROM sync)
4    verification failed (hash mismatch, two dumps differ, profile mismatch)
5    safety refusal (destructive action without confirmation, missing backup)
6    flash write failed (device state undefined; re-flash your backup)
===  =====================================================================

Nothing here imports :mod:`serial`: the hardware modules are imported lazily
inside the command that needs them, so ``m241 patch``, ``m241 logs ...`` and
``m241 nmea repair`` work on a machine with no pyserial installed at all.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import platform
import struct
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from m241 import __version__
from m241.errors import M241Error

PROG = "m241"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_HARDWARE = 3
EXIT_VERIFY = 4
EXIT_SAFETY = 5
EXIT_FLASH = 6

#: Default BootROM baud rate and the M-241's NMEA baud rate.
DEFAULT_BAUD = 115200
DEFAULT_NMEA_BAUD = 38400

#: Roles `doctor --extract-to` may copy (never drivers/DLLs/EXEs).
_EXTRACT_ROLES = {"download_agent", "firmware"}
_NEVER_COPY_SUFFIXES = {".dll", ".exe", ".sys", ".cat", ".msi"}

#: Files larger than this are not hashed by `doctor --from-drop` (8 MiB).
_MAX_HASH_BYTES = 8 * 1024 * 1024

#: How long to wait for the BootROM sync handshake (tests lower this).
_SYNC_TIMEOUT = 5.0

#: How long to wait for an NMEA reply before giving up (tests lower this).
_NMEA_TIMEOUT = 1.0

#: Injection hook for tests and embedders: ``callable(port, baud) -> serial-like``.
#: When set it replaces :func:`m241.protocol.ports.open_serial` for every
#: hardware command, which is how the test-suite drives `dump`/`flash` through
#: :class:`m241.protocol.simulator.FakeDevice`.
_SERIAL_FACTORY: Callable[[str, int], Any] | None = None

#: Recorded sentence from the original blog post (broken 2006-09-17 date).
_SAMPLE_RMC = "$GPRMC,022127.542,V,8960.0000,N,00000.0000,E,0.00,0.00,170906,,,N*7F"


# ---------------------------------------------------------------------------
# Internal error carrying an exit code
# ---------------------------------------------------------------------------
class _CliError(Exception):
    """A user-facing failure that maps onto a documented exit code."""

    def __init__(self, code: int, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


# ---------------------------------------------------------------------------
# Console helper: machine output -> stdout, progress/warnings -> stderr
# ---------------------------------------------------------------------------
class _Ui:
    """Tiny output helper shared by every command."""

    def __init__(self, *, quiet: bool = False, verbose: bool = False) -> None:
        self.quiet = quiet
        self.verbose = verbose

    def result(self, text: str = "") -> None:
        """Print a line of command result on stdout."""
        print(text)

    def log(self, text: str) -> None:
        """Print a status/progress line on stderr (suppressed by ``--quiet``)."""
        if not self.quiet:
            print(text, file=sys.stderr)

    def warn(self, text: str) -> None:
        """Print a warning on stderr (never suppressed)."""
        print(f"m241: warning: {text}", file=sys.stderr)

    def debug(self, text: str) -> None:
        """Print a diagnostic line on stderr when ``--verbose`` is set."""
        if self.verbose:
            print(f"m241: debug: {text}", file=sys.stderr)

    def emit_json(self, payload: Any) -> None:
        """Print one pretty-printed JSON object on stdout."""
        print(json.dumps(payload, indent=2, ensure_ascii=False))

    def progress(self, done: int, total: int) -> None:
        """Render a ``\\r``-based progress line on stderr (TTY only)."""
        if self.quiet or total <= 0 or not sys.stderr.isatty():
            return
        percent = 100.0 * done / total
        sys.stderr.write(f"\r{done}/{total} bytes ({percent:5.1f}%)")
        if done >= total:
            sys.stderr.write("\n")
        sys.stderr.flush()


def _ui_from_args(args: argparse.Namespace) -> _Ui:
    return _Ui(
        quiet=bool(getattr(args, "quiet", False)),
        verbose=bool(getattr(args, "verbose", False)),
    )


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------
def _parse_int(text: str) -> int:
    """argparse type: integers in decimal or ``0x`` form."""
    try:
        return int(text, 0)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(f"invalid integer: {text!r}") from exc


def _parse_offset_days(text: str) -> str | int:
    """argparse type: ``auto`` or an explicit number of days."""
    value = str(text).strip()
    if value.lower() == "auto":
        return "auto"
    try:
        return int(value, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"expected 'auto' or a number of days, not {text!r}"
        ) from exc


def _iso(moment: datetime | None) -> str | None:
    """RFC 3339 UTC text for *moment* (``None`` stays ``None``)."""
    if moment is None:
        return None
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str | None:
    """SHA256 of *path*; ``None`` when the file is larger than the hash limit."""
    try:
        if path.stat().st_size > _MAX_HASH_BYTES:
            return None
    except OSError:
        return None
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _read_package_data(name: str) -> str | None:
    """Read a shipped file from ``m241/data/`` (package resources first)."""
    from importlib import resources

    try:
        resource = resources.files("m241").joinpath(f"data/{name}")
        if resource.is_file():
            return resource.read_text(encoding="utf-8")
    except (ImportError, AttributeError, OSError):
        pass
    fallback = Path(__file__).resolve().parent / "data" / name
    try:
        return fallback.read_text(encoding="utf-8")
    except OSError:
        return None


def _package_data_ok() -> bool:
    """True when both shipped data files resolve and parse."""
    try:
        profiles = json.loads(_read_package_data("firmware_profiles.json") or "{}")
        drop = json.loads(_read_package_data("vendor_drop.json") or "{}")
    except ValueError:
        return False
    return bool(profiles.get("profiles")) and bool(drop.get("files"))


def _pyserial_version() -> str | None:
    """Return the installed pyserial version, or ``None`` when it is missing."""
    try:
        import serial  # noqa: PLC0415 - deliberately lazy: pyserial is optional here
    except Exception:
        return None
    version = getattr(serial, "VERSION", None)
    if version:
        return str(version)
    try:
        from importlib import metadata

        return metadata.version("pyserial")
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Port / hardware helpers
# ---------------------------------------------------------------------------
def _resolve_port(spec: str | None) -> str:
    """Resolve a user supplied port spec, mapping failures onto exit code 3."""
    from m241.protocol import ports as ports_mod

    try:
        return ports_mod.resolve_port(spec)
    except M241Error as exc:
        raise _CliError(
            EXIT_HARDWARE,
            str(exc),
            "plug the logger in, then run 'm241 ports'; or pass the port explicitly",
        ) from exc


def _serial_factory() -> Callable[[str, int], Any]:
    """Return the serial factory (the test hook when set, else pyserial)."""
    if _SERIAL_FACTORY is not None:
        return _SERIAL_FACTORY
    from m241.protocol import ports as ports_mod

    return ports_mod.open_serial


def _open_serial(port: str, baud: int) -> Any:
    """Open *port* at *baud*, mapping failures onto exit code 3."""
    factory = _serial_factory()
    try:
        return factory(port, baud)
    except M241Error as exc:
        raise _CliError(EXIT_HARDWARE, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - any OS error means "no hardware"
        raise _CliError(
            EXIT_HARDWARE, f"could not open serial port {port!r} at {baud} baud: {exc}"
        ) from exc


def _bootrom(
    port: str,
    *,
    baud: int = DEFAULT_BAUD,
    nmea_baud: int | None = None,
    timeout: float = 1.0,
) -> Any:
    """Create an :class:`m241.protocol.brom.MtkBootRom` (port opened lazily)."""
    from m241.protocol.brom import MtkBootRom

    factory = _SERIAL_FACTORY
    return MtkBootRom(
        port,
        baud=baud,
        nmea_baud=nmea_baud,
        serial_factory=factory,
        timeout=timeout,
        sync_timeout=max(float(timeout), _SYNC_TIMEOUT),
    )


def _start_bootrom(boot: Any, *, assume_brom: bool, pmtk180: bool) -> None:
    """Enter the BootROM, mapping wire failures onto exit code 3."""
    from m241.errors import M241Error

    try:
        boot.start(use_rts_dtr=not assume_brom, pmtk180=pmtk180)
    except M241Error as exc:
        raise _CliError(
            EXIT_HARDWARE,
            str(exc),
            "power-cycle the logger and retry; try '--assume-brom' if it is "
            "already in BootROM, or '--pmtk180' for legacy firmware",
        ) from exc


def _nmea_collect(
    serial_port: Any,
    sentences: list[str],
    *,
    stop_prefix: str | None = None,
    timeout: float = 1.0,
    max_lines: int = 64,
) -> list[str]:
    """Write *sentences* and collect reply lines until *stop_prefix* or timeout."""
    for sentence in sentences:
        serial_port.write(sentence.encode("ascii"))
    lines: list[str] = []
    buffer = b""
    deadline = time.monotonic() + max(0.05, timeout)
    while time.monotonic() < deadline and len(lines) < max_lines:
        chunk = serial_port.read(1)
        if not chunk:
            time.sleep(0.002)
            continue
        buffer += bytes(chunk)
        while b"\n" in buffer:
            raw, buffer = buffer.split(b"\n", 1)
            text = raw.decode("ascii", "replace").strip()
            if not text:
                continue
            lines.append(text)
            if stop_prefix is not None and text.startswith(stop_prefix):
                return lines
    return lines


def _chip_guess(model: str) -> str:
    """Guess the MTK chip family from a PMTK705 product model string."""
    text = (model or "").lower()
    for chip in ("3318", "3329", "3339", "3327"):
        if chip in text:
            return f"MT{chip}"
    return "MT3318"


# ---------------------------------------------------------------------------
# Firmware-image helpers
# ---------------------------------------------------------------------------
def _image_facts(path: Path) -> dict[str, Any]:
    """Best-effort header facts for diagnostics (never raises)."""
    from m241.firmware.image import FirmwareImage

    facts: dict[str, Any] = {"size": None, "sha256": None, "family": None,
                             "device_type": None, "build_number": None,
                             "version": None}
    try:
        image = FirmwareImage.from_path(path)
    except M241Error:
        return facts
    facts["size"] = len(image)
    facts["sha256"] = image.sha256()
    facts["version"] = image.version_string()
    try:
        header = image.header()
    except M241Error:
        return facts
    facts["family"] = header.family
    facts["device_type"] = header.device_type
    facts["build_number"] = header.build_number
    return facts


def _format_facts(facts: dict[str, Any]) -> str:
    """Render :func:`_image_facts` as indented key/value lines for an error."""
    lines = ["detected image:"]
    for key in ("family", "device_type", "version", "build_number", "size", "sha256"):
        lines.append(f"  {key.replace('_', ' ')}: {facts.get(key)}")
    return "\n".join(lines)


def _header_dict(image: Any) -> dict[str, Any] | None:
    """JSON-safe firmware header summary (``None`` when it cannot be parsed)."""
    try:
        header = image.header()
    except M241Error:
        return None
    return {
        "family": header.family,
        "device_type": header.device_type,
        "release_name": header.release_name,
        "release_major": header.release_major,
        "release_minor": header.release_minor,
        "build_number": header.build_number,
        "fw_size": header.fw_size,
        "build_date": header.build_date,
    }


def _firmware_trailer(
    boot: Any, data: bytes, *, base_addr: int, byte_swap: bool
) -> bytes:
    """Read the 4-byte image trailer the ``0xF4`` size field excludes.

    The M-241 stores ``filesize - 4`` at ``base + 0xF4``; a header-derived dump
    therefore stops four bytes short of the patchable image.  Those four bytes
    are read back when the stored size is self-consistent.
    """
    if len(data) < 0x200:
        return b""
    declared = int.from_bytes(data[0xF4:0xF8], "little")
    if declared != len(data):
        return b""
    try:
        trailer = boot.read32(base_addr + len(data), 4, byte_swap=byte_swap)
    except M241Error:
        return b""
    return trailer if len(trailer) == 4 else b""


def _dump_full_image(
    boot: Any,
    *,
    base_addr: int,
    length: int | None,
    byte_swap: bool,
    progress_cb: Callable[[int, int], None] | None,
) -> bytes:
    """Dump the firmware, completing a header-derived image with its trailer."""
    from m241.protocol.brom import dump_firmware

    if length is not None:
        return dump_firmware(
            boot,
            base_addr=base_addr,
            length=length,
            byte_swap=byte_swap,
            progress_cb=progress_cb,
        )
    data = dump_firmware(
        boot, base_addr=base_addr, byte_swap=byte_swap, progress_cb=progress_cb
    )
    return data + _firmware_trailer(boot, data, base_addr=base_addr, byte_swap=byte_swap)


# ---------------------------------------------------------------------------
# `m241 ports`
# ---------------------------------------------------------------------------
def _port_dict(info: Any) -> dict[str, Any]:
    return {
        "device": info.device,
        "description": info.description,
        "hwid": info.hwid,
        "vid": info.vid,
        "pid": info.pid,
        "is_candidate": info.is_candidate,
    }


def _collect_ports() -> dict[str, Any]:
    """Return the ports payload shared by ``ports`` and ``doctor``."""
    from m241.protocol import ports as ports_mod

    try:
        infos = ports_mod.list_serial_ports()
    except M241Error:
        if _pyserial_version() is None:
            return {
                "ports": [],
                "candidates": [],
                "platform_hint": ports_mod.platform_hint(),
                "note": (
                    "pyserial is not installed, so serial ports cannot be "
                    "enumerated; install it with 'pip install pyserial'"
                ),
            }
        raise
    payload: dict[str, Any] = {
        "ports": [_port_dict(info) for info in infos],
        "candidates": [info.device for info in infos if info.is_candidate],
        "platform_hint": ports_mod.platform_hint(),
    }
    if not infos:
        payload["note"] = (
            "No serial ports found. Plug the M-241 in with its USB cable, turn it "
            "on, then run 'm241 ports' again."
        )
    return payload


def _cmd_ports(args: argparse.Namespace, ui: _Ui) -> int:
    try:
        payload = _collect_ports()
    except M241Error as exc:
        raise _CliError(
            EXIT_ERROR, str(exc), "run 'm241 doctor' for environment diagnostics"
        ) from exc

    if args.json:
        ui.emit_json(payload)
        return EXIT_OK

    infos = payload["ports"]
    if not infos:
        ui.result(payload.get("note", "no serial ports found."))
        if not payload.get("note", "").startswith("pyserial"):
            ui.result(payload["platform_hint"])
        return EXIT_OK
    ui.result(f"{'DEVICE':<18}{'CAND':<6}{'DESCRIPTION':<34}HWID")
    for info in infos:
        marker = "*" if info["is_candidate"] else ""
        ui.result(
            f"{info['device']:<18}{marker:<6}{info['description']:<34}{info['hwid']}"
        )
    if payload["candidates"]:
        ui.result("")
        ui.result("candidate port(s): " + ", ".join(payload["candidates"]))
    else:
        ui.result("")
        ui.result("No obvious M-241 cable found.")
        ui.result(payload["platform_hint"])
    return EXIT_OK


# ---------------------------------------------------------------------------
# `m241 doctor`
# ---------------------------------------------------------------------------
def _environment_section() -> dict[str, Any]:
    return {
        "python": platform.python_version(),
        "platform": sys.platform,
        "pyserial": _pyserial_version(),
        "package_data": _package_data_ok(),
    }


def _profiles_section() -> list[dict[str, Any]]:
    from m241.firmware import list_profiles

    rows = []
    for profile in list_profiles():
        rows.append(
            {
                "name": profile.name,
                "version": profile.version,
                "size": profile.size,
                "log_record_bytes": profile.log_record_bytes,
            }
        )
    return rows


def _simulated_da_info() -> dict[str, Any]:
    """Boot the bundled simulator, load a synthetic DA and return its report."""
    from m241.protocol.brom import MtkBootRom
    from m241.protocol.da import DownloadAgent
    from m241.protocol.simulator import FakeDevice

    device = FakeDevice(bytes(0x1000))
    boot = MtkBootRom("SIM", serial_factory=device.factory, timeout=1.0)
    try:
        boot.start()
        agent = DownloadAgent(boot)
        with tempfile.TemporaryDirectory(prefix="m241-sim-") as directory:
            da_path = Path(directory) / "DA_v3.8.4.bin"
            da_path.write_bytes(b"\x42" * 0x800)
            return dict(agent.start(da_path))
    finally:
        boot.close()


def _device_section(args: argparse.Namespace, ui: _Ui) -> tuple[dict[str, Any], int]:
    """Build the ``device`` section; return ``(section, exit_code)``."""
    if args.simulate:
        try:
            info = _simulated_da_info()
        except Exception as exc:  # noqa: BLE001 - report any simulator failure
            ui.warn(f"simulated device failed: {exc}")
            return (
                {"simulated": True, "ok": False, "error": f"{type(exc).__name__}: {exc}"},
                EXIT_HARDWARE,
            )
        return (
            {
                "simulated": True,
                "ok": True,
                "da_version": f"0x{int(info.get('da_version', 0)):04X}",
                "flash_size": int(info.get("flash_size", 0)),
                "flash_device_id": f"0x{int(info.get('flash_device_id', 0)):02X}",
                "flash_device_code": f"0x{int(info.get('flash_device_code', 0)):04X}",
            },
            EXIT_OK,
        )
    if args.port:
        port = _resolve_port(args.port)
        serial_port = _open_serial(port, DEFAULT_NMEA_BAUD)
        with contextlib.suppress(Exception):
            serial_port.close()
        return (
            {"simulated": False, "ok": True, "opened": True, "port": port},
            EXIT_OK,
        )
    return (
        {
            "simulated": False,
            "ok": True,
            "opened": False,
            "next": "m241 info auto   # probe the device once it is plugged in",
        },
        EXIT_OK,
    )


def _scan_drop(root: Path) -> list[dict[str, Any]]:
    """Hash every file in *root* that is small enough for the manifest."""
    found: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > _MAX_HASH_BYTES:
            continue
        digest = _sha256_file(path)
        if digest is None:
            continue
        found.append(
            {
                "path": str(path),
                "basename": path.name,
                "size": size,
                "sha256": digest,
            }
        )
    return found


def _vendor_drop_section(
    drop: Path, *, extract_to: str | None, include_firmware: bool, ui: _Ui
) -> tuple[dict[str, Any], bool]:
    """Match the drop against the shipped manifest; return (section, hash_failed)."""
    manifest_text = _read_package_data("vendor_drop.json")
    if manifest_text is None:
        raise _CliError(
            EXIT_ERROR,
            "the shipped vendor-drop manifest (m241/data/vendor_drop.json) is missing",
            "reinstall the package or run from a full source checkout",
        )
    try:
        manifest = json.loads(manifest_text)
    except ValueError as exc:
        raise _CliError(EXIT_ERROR, f"vendor-drop manifest is not valid JSON: {exc}") from exc
    if not drop.is_dir():
        raise _CliError(
            EXIT_ERROR,
            f"vendor drop directory not found: {drop}",
            "pass the folder that contains the official Holux packages",
        )

    scanned = _scan_drop(drop)
    by_name: dict[str, list[dict[str, Any]]] = {}
    for item in scanned:
        by_name.setdefault(item["basename"].lower(), []).append(item)

    found: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    mismatched: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for entry in manifest.get("files", []):
        basename = str(entry.get("basename", ""))
        bucket = by_name.get(basename.lower(), [])
        identity = (str(entry.get("role", "")), basename, str(entry.get("sha256", "")))
        if identity in seen:
            continue
        seen.add(identity)
        if not bucket:
            missing.append(
                {
                    "role": entry.get("role"),
                    "basename": basename,
                    "expected_sha256": entry.get("sha256"),
                }
            )
            continue
        match = next(
            (
                item
                for item in bucket
                if item["size"] == entry.get("size")
                and item["sha256"] == str(entry.get("sha256", "")).lower()
            ),
            None,
        )
        if match is None:
            actual = bucket[0]
            row = {
                "role": entry.get("role"),
                "basename": basename,
                "path": actual["path"],
                "sha256": actual["sha256"],
                "expected_sha256": entry.get("sha256"),
                "ok": False,
            }
            found.append(row)
            mismatched.append(row)
            ui.warn(
                f"{basename}: hash mismatch (expected {str(entry.get('sha256'))[:16]}..., "
                f"found {actual['sha256'][:16]}...)"
            )
            continue
        found.append(
            {
                "role": entry.get("role"),
                "basename": basename,
                "path": match["path"],
                "sha256": match["sha256"],
                "ok": True,
            }
        )

    hash_failed = bool(mismatched)
    extracted: list[dict[str, Any]] = []
    if extract_to:
        target_root = Path(extract_to)
        for row in found:
            if row["role"] not in _EXTRACT_ROLES or not row["ok"]:
                continue
            if row["role"] == "firmware" and not include_firmware:
                continue
            source = Path(row["path"])
            if source.suffix.lower() in _NEVER_COPY_SUFFIXES:
                ui.warn(f"refusing to copy executable/DLL file {source.name}")
                continue
            destination = target_root / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
            copied_sha = _sha256_file(destination)
            verified = copied_sha == row["sha256"]
            extracted.append(
                {
                    "role": row["role"],
                    "basename": source.name,
                    "path": str(destination),
                    "sha256": copied_sha,
                    "ok": verified,
                }
            )
            if verified:
                ui.log(f"extracted {source.name} -> {destination} (sha256 verified)")
            else:
                hash_failed = True
                ui.warn(f"{destination}: hash changed while copying")

    section = {
        "path": str(drop),
        "found": found,
        "missing": missing,
        "extracted": extracted,
    }
    return section, hash_failed


def _cmd_doctor(args: argparse.Namespace, ui: _Ui) -> int:
    environment = _environment_section()
    ports_payload: dict[str, Any]
    try:
        ports_payload = _collect_ports()
    except M241Error as exc:
        ports_payload = {"ports": [], "candidates": [], "platform_hint": "", "error": str(exc)}

    profiles: list[dict[str, Any]] = []
    profiles_ok = True
    try:
        profiles = _profiles_section()
    except M241Error as exc:
        profiles_ok = False
        ui.warn(f"firmware profiles could not be loaded: {exc}")

    vendor_section: dict[str, Any] | None = None
    hash_failed = False
    if args.from_drop:
        vendor_section, hash_failed = _vendor_drop_section(
            Path(args.from_drop),
            extract_to=args.extract_to,
            include_firmware=args.include_firmware,
            ui=ui,
        )

    device_section, device_code = _device_section(args, ui)

    required_ok = (
        bool(environment["package_data"]) and environment["pyserial"] is not None and profiles_ok
    )
    if hash_failed:
        exit_code = EXIT_VERIFY
    elif device_code != EXIT_OK:
        exit_code = device_code
    elif not required_ok:
        exit_code = EXIT_ERROR
    else:
        exit_code = EXIT_OK

    document: dict[str, Any] = {
        "ok": exit_code == EXIT_OK,
        "environment": environment,
        "ports": ports_payload["ports"],
        "profiles": profiles,
        "device": device_section,
    }
    if vendor_section is not None:
        document["vendor_drop"] = vendor_section
    if exit_code != EXIT_OK:
        document["exit_code"] = exit_code

    if args.json:
        ui.emit_json(document)
        return exit_code

    ui.result("environment")
    ui.result(f"  python       : {environment['python']}")
    ui.result(f"  platform     : {environment['platform']}")
    if environment["pyserial"] is None:
        ui.result("  pyserial     : MISSING (pip install pyserial) - hardware commands will fail")
    else:
        ui.result(f"  pyserial     : {environment['pyserial']}")
    ui.result(f"  package data : {'OK' if environment['package_data'] else 'MISSING'}")

    ui.result("")
    ui.result(f"serial ports ({len(ports_payload['ports'])})")
    for info in ports_payload["ports"]:
        marker = " *" if info["is_candidate"] else ""
        ui.result(f"  {info['device']:<18}{info['description']}{marker}")
    if not ports_payload["ports"]:
        ui.result("  none found")
    if ports_payload.get("error"):
        ui.result(f"  error: {ports_payload['error']}")
    if ports_payload["platform_hint"]:
        ui.result(f"  hint: {ports_payload['platform_hint']}")

    if vendor_section is not None:
        ui.result("")
        ui.result(f"vendor drop: {vendor_section['path']}")
        for row in vendor_section["found"]:
            state = "ok" if row["ok"] else "BAD HASH"
            ui.result(f"  [{state:<8}] {row['role']:<14} {row['basename']}")
        for row in vendor_section["missing"]:
            ui.result(f"  [{'missing':<8}] {row['role']:<14} {row['basename']}")
        for row in vendor_section["extracted"]:
            ui.result(f"  extracted: {row['path']} (sha256 {'verified' if row['ok'] else 'MISMATCH'})")

    ui.result("")
    ui.result("device")
    if device_section.get("simulated"):
        if device_section.get("ok"):
            ui.result(
                "  simulated device OK: DA version "
                f"{device_section['da_version']}, flash size "
                f"{device_section['flash_size']} bytes"
            )
        else:
            ui.result(f"  simulated device FAILED: {device_section.get('error')}")
    elif device_section.get("opened"):
        ui.result(f"  opened {device_section['port']} OK")
    else:
        ui.result("  no device opened (pass --port to probe, or --simulate)")
        ui.result(f"  next: {device_section['next']}")

    ui.result("")
    ui.result(f"firmware profiles ({len(profiles)})")
    for row in profiles:
        ui.result(
            f"  {row['name']:<6} {row['version']:<6} {row['size']:>8} bytes "
            f"{row['log_record_bytes']:>2}-byte records"
        )

    ui.result("")
    if exit_code == EXIT_OK:
        ui.result("doctor: all required checks passed")
    else:
        ui.result(f"doctor: FAILED (exit {exit_code})")
    return exit_code


# ---------------------------------------------------------------------------
# `m241 info`
# ---------------------------------------------------------------------------
def _cmd_info(args: argparse.Namespace, ui: _Ui) -> int:
    from m241.nmea import with_checksum

    port = _resolve_port(args.port)
    serial_port = _open_serial(port, args.nmea_baud)
    try:
        lines = _nmea_collect(
            serial_port,
            [with_checksum("PMTK605")],
            stop_prefix="$PMTK705",
            timeout=_NMEA_TIMEOUT,
        )
    finally:
        with contextlib.suppress(Exception):
            serial_port.close()

    raw = next((line for line in lines if line.startswith("$PMTK705")), None)
    if raw is None:
        raise _CliError(
            EXIT_HARDWARE,
            f"no $PMTK705 reply from {port} (received {len(lines)} line(s))",
            "check 'm241 ports', close other serial programs, and pass "
            "'--nmea-baud 38400' if you changed the baud rate",
        )

    from m241.nmea import split

    try:
        _kind, fields = split(raw)
    except M241Error as exc:
        raise _CliError(EXIT_ERROR, f"could not parse the $PMTK705 reply: {raw}") from exc
    fields = list(fields) + [""] * (4 - len(fields))
    release, build, model, sdk = fields[0], fields[1], fields[2], fields[3]
    document: dict[str, Any] = {
        "port": port,
        "release": release,
        "build": build,
        "model": model,
        "sdk": sdk,
        "raw": raw,
        "chip_guess": _chip_guess(model),
    }

    if args.aiding:
        now = datetime.now(timezone.utc)
        stamp = now.strftime("%H%M%S") + f".{now.microsecond // 1000:03d}"
        sentences = [
            with_checksum(f"PMTK715,{stamp}"),
            with_checksum("PMTK716,0.0000,N,0.0000,E"),
            with_checksum("PMTK717,0,0"),
        ]
        aiding_port = _open_serial(port, args.nmea_baud)
        try:
            acks = _nmea_collect(aiding_port, sentences, timeout=_NMEA_TIMEOUT)
        finally:
            with contextlib.suppress(Exception):
                aiding_port.close()
        document["aiding"] = {
            "sent": sentences,
            "acks": [line for line in acks if line.startswith("$PMTK001")],
        }
        ui.debug(f"aiding replies: {document['aiding']['acks']}")

    if args.json:
        ui.emit_json(document)
        return EXIT_OK

    ui.result(f"port     : {port}")
    ui.result(f"release  : {release}")
    ui.result(f"build    : {build}")
    ui.result(f"model    : {model}")
    ui.result(f"sdk      : {sdk}")
    ui.result(f"chip     : {document['chip_guess']}")
    ui.result(f"raw      : {raw}")
    if "aiding" in document:
        ui.result(f"aiding   : {len(document['aiding']['acks'])} acknowledgement(s)")
    return EXIT_OK


# ---------------------------------------------------------------------------
# `m241 dump`
# ---------------------------------------------------------------------------
def _cmd_dump(args: argparse.Namespace, ui: _Ui) -> int:
    from m241.errors import FirmwareError, FirmwareProfileError, ProtocolError
    from m241.firmware import FirmwareImage, detect_profile

    output = Path(args.output)
    if output.exists() and not args.force:
        raise _CliError(
            EXIT_SAFETY,
            f"refusing to overwrite existing file {output}",
            "pass --force, or choose another -o path",
        )
    if args.length is not None and args.length <= 0:
        raise _CliError(EXIT_USAGE, "--length must be positive")

    port = _resolve_port(args.port)
    byte_swap = not args.no_swap
    data: bytes
    boot = _bootrom(port, baud=args.baud, nmea_baud=args.nmea_baud)
    try:
        _start_bootrom(boot, assume_brom=args.assume_brom, pmtk180=args.pmtk180)
        data = _dump_full_image(
            boot,
            base_addr=args.base_addr,
            length=args.length,
            byte_swap=byte_swap,
            progress_cb=ui.progress,
        )
        verified = False
        if args.verify:
            if sys.stdin.isatty() and not ui.quiet:
                ui.log(
                    "power-cycle the logger now (so the read is from a fresh "
                    "boot), then press Enter to dump again and compare hashes"
                )
                with contextlib.suppress(EOFError):
                    input()
            _start_bootrom(boot, assume_brom=args.assume_brom, pmtk180=args.pmtk180)
            second = _dump_full_image(
                boot,
                base_addr=args.base_addr,
                length=args.length,
                byte_swap=byte_swap,
                progress_cb=ui.progress,
            )
            if second != data:
                raise _CliError(
                    EXIT_VERIFY,
                    "the two dumps differ; the flash contents are not stable "
                    f"(first sha256 {_sha256_bytes(data)}, second "
                    f"{_sha256_bytes(second)})",
                    "do not flash this image; retry with a shorter cable and a "
                    "stable power supply",
                )
            verified = True
    except ProtocolError as exc:
        raise _CliError(
            EXIT_HARDWARE,
            str(exc),
            "power-cycle the logger and retry; check 'm241 ports'",
        ) from exc
    except FirmwareError as exc:
        raise _CliError(
            EXIT_VERIFY,
            str(exc),
            "the firmware size in the header is implausible; pass --length "
            "explicitly or --base-addr 0x0 for the M3318 view",
        ) from exc
    finally:
        boot.close()

    if not data:
        raise _CliError(EXIT_VERIFY, "the dump is empty; check --base-addr and --length")

    image = FirmwareImage.from_bytes(data)
    profile_name: str | None = None
    try:
        profile_name = detect_profile(image).name
    except FirmwareProfileError:
        ui.warn(
            "this image matches no shipped firmware profile; it can still be "
            "dumped and patched with an explicit --profile"
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)
    digest = image.sha256()
    document = {
        "port": port,
        "output": str(output),
        "size": len(data),
        "sha256": digest,
        "profile": profile_name,
        "verified": verified,
        "header": _header_dict(image),
    }
    if args.json:
        ui.emit_json(document)
        return EXIT_OK

    ui.result(f"port      : {port}")
    ui.result(f"base addr : 0x{args.base_addr:08X}")
    ui.result(f"size      : {len(data)} bytes")
    ui.result(f"sha256    : {digest}")
    ui.result(f"profile   : {profile_name or 'unknown'}")
    ui.result(f"verified  : {'yes (two identical dumps)' if verified else 'no'}")
    header = document["header"]
    if header:
        ui.result(
            f"header    : {header['family']} {header['device_type']} "
            f"release {header['release_name']} {header['release_major']}."
            f"{header['release_minor']} build {header['build_number']} "
            f"({header['build_date'] or 'no build date'})"
        )
    ui.result(f"written   : {output}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# `m241 patch`
# ---------------------------------------------------------------------------
def _cmd_patch(args: argparse.Namespace, ui: _Ui) -> int:
    from m241.errors import FirmwareError, FirmwareProfileError
    from m241.firmware import (
        FirmwareImage,
        apply_patch_plan,
        build_patch_plan,
        get_profile,
        verify_patched,
    )

    source = Path(args.source)
    if not source.is_file():
        raise _CliError(
            EXIT_ERROR, f"firmware image not found: {source}", "pass the file you dumped"
        )
    in_place = bool(args.in_place)
    if args.output is None and not in_place:
        raise _CliError(
            EXIT_USAGE,
            "patch needs an output file",
            "pass '-o OUT.bin' (recommended), or '--in-place' to overwrite the input",
        )
    destination = Path(args.output) if args.output is not None else source
    same_file = _same_path(source, destination)
    if same_file and not in_place:
        raise _CliError(
            EXIT_SAFETY,
            "refusing to overwrite the input image in place",
            "write to a different file with '-o', or add '--in-place' to confirm",
        )

    try:
        image = FirmwareImage.from_path(source)
    except FirmwareError as exc:
        raise _CliError(EXIT_ERROR, str(exc)) from exc

    profile = None
    if args.profile and args.profile != "auto":
        try:
            profile = get_profile(args.profile)
        except FirmwareProfileError as exc:
            raise _CliError(EXIT_USAGE, str(exc)) from exc

    try:
        plan = build_patch_plan(
            image,
            profile=profile,
            target_version=args.target_version,
            include_version_marker=not args.no_version_marker,
        )
    except FirmwareProfileError as exc:
        facts = _image_facts(source)
        raise _CliError(
            EXIT_VERIFY,
            f"{exc}\n{_format_facts(facts)}",
            "pass '--profile v1.12' or '--profile v1.13' if you know the image, "
            "or report the facts above (never paste firmware bytes)",
        ) from exc
    except FirmwareError as exc:
        message = str(exc)
        hint = "the image cannot be patched with this plan"
        if "already patched" in message:
            hint = "this image is already fixed; flash it or dump the stock image again"
        raise _CliError(EXIT_VERIFY, message, hint) from exc

    try:
        patched = apply_patch_plan(image, plan)
        verify_patched(patched, plan)
    except FirmwareError as exc:
        raise _CliError(EXIT_VERIFY, str(exc)) from exc

    written = False
    if not args.dry_run:
        try:
            if same_file:
                patched.save(destination)
            else:
                patched.save(destination)
        except FirmwareError as exc:
            raise _CliError(EXIT_ERROR, str(exc)) from exc
        written = True

    document = {
        "input": str(source),
        "output": str(destination),
        "profile": plan.profile.name,
        "target_version": plan.target_version,
        "dry_run": bool(args.dry_run),
        "patches": [
            {
                "offset": patch.offset,
                "old": patch.old.hex(),
                "new": patch.new.hex(),
                "description": patch.description,
                "changed_offsets": patch.changed_bytes,
            }
            for patch in plan.patches
        ],
        "changed_offsets": plan.changed_offsets(),
        "sha256_before": plan.before_sha256,
        "sha256_after": patched.sha256(),
        "written": written,
    }
    if args.json:
        ui.emit_json(document)
        return EXIT_OK

    ui.result(f"input  : {source}")
    ui.result(f"profile: {plan.profile.name} ({plan.profile.display_name})")
    ui.result(f"version: {plan.profile.version} -> {plan.target_version}")
    ui.result("")
    for patch in plan.patches:
        offsets = ", ".join(f"0x{offset:X}" for offset in patch.changed_bytes) or "none"
        ui.result(
            f"  0x{patch.offset:X}  {patch.old.hex()} -> {patch.new.hex()}  "
            f"{patch.description}"
        )
        ui.result(f"            changed: {offsets}")
    ui.result("")
    ui.result(
        "changed offsets: "
        + (", ".join(f"0x{offset:X}" for offset in plan.changed_offsets()) or "none")
    )
    ui.result(f"sha256 before : {plan.before_sha256}")
    ui.result(f"sha256 after  : {patched.sha256()}")
    if args.dry_run:
        ui.result("dry run: nothing was written")
    else:
        ui.result(f"written       : {destination}")
    return EXIT_OK


def _same_path(left: Path, right: Path) -> bool:
    """Best-effort "these two paths name the same file" test."""
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return str(left.absolute()).lower() == str(right.absolute()).lower()


# ---------------------------------------------------------------------------
# `m241 flash`
# ---------------------------------------------------------------------------
_DA_BAUD_CODES = {921600: 1, 912600: 1, 460800: 2, 230400: 3}


def _load_backup(path: Path) -> Any:
    """Load and sanity-check the backup image (safety refusal, exit 5)."""
    from m241.errors import FirmwareError
    from m241.firmware import FirmwareImage

    if not path.is_file():
        raise _CliError(
            EXIT_SAFETY,
            f"backup image not found: {path}",
            "pass the dump you made with 'm241 dump -o' as --backup",
        )
    try:
        image = FirmwareImage.from_path(path)
        header = image.header()
    except FirmwareError as exc:
        raise _CliError(
            EXIT_SAFETY,
            f"--backup is not a usable firmware image: {exc}",
            "dump your unit first: 'm241 dump auto -o backup/stock.bin'",
        ) from exc
    if len(image) < 0x200 or not header.family.upper().startswith("HOLUX"):
        raise _CliError(
            EXIT_SAFETY,
            f"--backup does not look like an M-241 firmware image "
            f"(family={header.family!r}, size={len(image)})",
            "dump your unit first: 'm241 dump auto -o backup/stock.bin'",
        )
    return image


def _allowed_offsets(backup: Any) -> tuple[set[int], str | None]:
    """Offsets a patched copy of *backup* may legitimately differ at."""
    from m241.errors import FirmwareProfileError
    from m241.firmware import PROFILES, detect_profile

    allowed: set[int] = set()
    profile_name: str | None = None
    try:
        profile = detect_profile(backup)
    except FirmwareProfileError:
        for candidate in PROFILES.values():
            allowed.update(range(candidate.epoch_offset, candidate.epoch_offset + 4))
    else:
        profile_name = profile.name
        allowed.update(range(profile.epoch_offset, profile.epoch_offset + 4))
    version_offset = backup.version_offset()
    if version_offset is not None:
        allowed.update(range(version_offset, version_offset + 5))
    return allowed, profile_name


def _check_flash_image(
    backup: Any, fw_bytes: bytes, *, size_mismatch_ok: bool, ui: _Ui
) -> str:
    """Refuse images that are not a known profile or a patched backup (exit 5)."""
    from m241.errors import FirmwareProfileError
    from m241.firmware import FirmwareImage, detect_profile

    if len(fw_bytes) != len(backup):
        if not size_mismatch_ok:
            raise _CliError(
                EXIT_SAFETY,
                f"--fw is {len(fw_bytes)} bytes but --backup is {len(backup)} bytes",
                "flash an image patched from the same dump, or pass --yes to accept "
                "the size difference",
            )
        ui.warn(
            f"--fw ({len(fw_bytes)} bytes) and --backup ({len(backup)} bytes) differ "
            "in size; continuing because --yes was given"
        )

    fw_image = FirmwareImage.from_bytes(fw_bytes)
    try:
        profile = detect_profile(fw_image)
    except FirmwareProfileError:
        allowed, _name = _allowed_offsets(backup)
        before = backup.to_bytes()
        differing = {
            index
            for index, (old, new) in enumerate(zip(before, fw_bytes, strict=False))
            if old != new
        }
        if differing and not differing <= allowed:
            raise _CliError(
                EXIT_SAFETY,
                "refusing to flash an image that is not a patched copy of your backup "
                f"({len(differing - allowed)} unexpected differing byte(s), first at "
                f"0x{min(differing - allowed):X})",
                "patch your own dump with 'm241 patch', then flash that file",
            ) from None
        return "patched copy of the backup"
    return f"known profile {profile.name}"


def _confirm_flash(ui: _Ui) -> None:
    """Require the literal word ``WRITE``; ``--yes`` skips this."""
    if not sys.stdin.isatty():
        raise _CliError(
            EXIT_SAFETY,
            "refusing to flash without confirmation (stdin is not a terminal)",
            "re-run with --yes once you have read DISCLAIMER.md",
        )
    sys.stderr.write("Type WRITE to flash the logger, anything else aborts: ")
    sys.stderr.flush()
    try:
        answer = sys.stdin.readline().strip()
    except KeyboardInterrupt as exc:  # pragma: no cover - interactive only
        raise _CliError(EXIT_SAFETY, "flash cancelled") from exc
    if answer != "WRITE":
        raise _CliError(EXIT_SAFETY, f"flash cancelled (expected 'WRITE', got {answer!r})")


def _cmd_flash(args: argparse.Namespace, ui: _Ui) -> int:
    from m241.errors import DownloadAgentError, FirmwareError, M241Error, ProtocolError
    from m241.protocol.da import DownloadAgent

    backup_path = Path(args.backup)
    fw_path = Path(args.fw)
    da_path = Path(args.da)
    if not fw_path.is_file():
        raise _CliError(EXIT_ERROR, f"--fw image not found: {fw_path}", "pass a patched image")
    if not da_path.is_file():
        raise _CliError(
            EXIT_ERROR,
            f"--da download agent not found: {da_path}",
            "extract it from your official package: "
            "'m241 doctor --from-drop <drop> --extract-to vendor'",
        )
    try:
        fw_bytes = fw_path.read_bytes()
    except OSError as exc:
        raise _CliError(EXIT_ERROR, f"cannot read --fw image: {exc}") from exc
    if not fw_bytes:
        raise _CliError(EXIT_ERROR, f"--fw image is empty: {fw_path}")

    backup = _load_backup(backup_path)
    accepted_as = _check_flash_image(
        backup, fw_bytes, size_mismatch_ok=bool(args.yes), ui=ui
    )
    ui.debug(f"--fw accepted: {accepted_as}")
    if not args.yes:
        _confirm_flash(ui)

    port = _resolve_port(args.port)
    boot = _bootrom(port, baud=args.baud, nmea_baud=args.nmea_baud)
    written = False
    da_info: dict[str, int] = {}
    try:
        _start_bootrom(boot, assume_brom=args.assume_brom, pmtk180=args.pmtk180)
        agent = DownloadAgent(boot)
        try:
            da_info = agent.start(da_path)
        except M241Error as exc:
            raise _CliError(
                EXIT_HARDWARE,
                str(exc),
                "confirm the logger is in BootROM mode and that --da is the "
                "official DA_v3.8.4.bin",
            ) from exc
        flash_size = int(da_info.get("flash_size", 0))
        if flash_size and len(fw_bytes) > flash_size:
            raise _CliError(
                EXIT_VERIFY,
                f"the firmware image ({len(fw_bytes)} bytes) does not fit the "
                f"reported flash size ({flash_size} bytes)",
                "this image belongs to a different flash geometry",
            )
        if args.high_baud:
            code = _DA_BAUD_CODES.get(args.high_baud)
            if code is None:
                raise _CliError(
                    EXIT_USAGE,
                    f"unsupported --high-baud {args.high_baud}",
                    "use one of 230400, 460800 or 921600",
                )
            try:
                agent.set_baud_rate(code)
                boot.ser.baudrate = args.high_baud
            except (DownloadAgentError, ProtocolError, AttributeError) as exc:
                raise _CliError(
                    EXIT_HARDWARE, f"could not switch to {args.high_baud} baud: {exc}"
                ) from exc
        agent.set_progress(ui.progress)
        try:
            agent.write_firmware(fw_bytes, start=args.start_addr)
        except (DownloadAgentError, ProtocolError, FirmwareError) as exc:
            hint = (
                "the logger state is undefined; re-flash your backup: "
                f"m241 flash {args.port} --da {da_path} --fw {backup_path} "
                f"--backup {backup_path} --yes"
            )
            raise _CliError(EXIT_FLASH, f"firmware write failed: {exc}", hint) from exc
        agent.restart()
        written = True
    except ProtocolError as exc:
        raise _CliError(
            EXIT_HARDWARE, str(exc), "power-cycle the logger and retry"
        ) from exc
    finally:
        boot.close()

    document = {
        "port": port,
        "firmware": str(fw_path),
        "backup": str(backup_path),
        "da": str(da_path),
        "start_addr": args.start_addr,
        "size": len(fw_bytes),
        "sha256": _sha256_bytes(fw_bytes),
        "da_info": da_info,
        "written": written,
    }
    if args.json:
        ui.emit_json(document)
        return EXIT_OK

    ui.result(f"port      : {port}")
    ui.result(f"firmware  : {fw_path} ({len(fw_bytes)} bytes)")
    ui.result(f"sha256    : {document['sha256']}")
    if da_info:
        ui.result(
            f"DA        : version 0x{int(da_info.get('da_version', 0)):04X}, "
            f"flash {int(da_info.get('flash_size', 0))} bytes, "
            f"id 0x{int(da_info.get('flash_device_id', 0)):02X}"
        )
    ui.result("flash     : written and the download agent was restarted")
    ui.result("next      : power-cycle the logger and check the OLED shows V1.14")
    return EXIT_OK


# ---------------------------------------------------------------------------
# `m241 logs ...`
# ---------------------------------------------------------------------------
def _capacity_section(dump: Any) -> dict[str, list[dict[str, Any]]]:
    """Per-flash capacity/usage rows for the 16- and 20-byte record formats."""
    from m241.logfile.capacity import capacity

    used = dump.used_bytes()
    section: dict[str, list[dict[str, Any]]] = {}
    for flash in ("2M", "4M"):
        rows = []
        for record_bytes in (16, 20):
            cap = capacity(flash, record_bytes)
            ratio = used / cap.usable_bytes if cap.usable_bytes else 0.0
            rows.append(
                {
                    "flash": flash,
                    "record_bytes": record_bytes,
                    "points": cap.points,
                    "usable_bytes": cap.usable_bytes,
                    "used_bytes": used,
                    "fill_ratio": round(ratio, 6),
                    "hours_at_1s": round(cap.points / 3600.0, 2),
                }
            )
        section[flash] = rows
    return section


def _cmd_logs_info(args: argparse.Namespace, ui: _Ui) -> int:
    from m241.errors import LogParseError
    from m241.logfile import read_log_file

    path = Path(args.file)
    if not path.is_file():
        raise _CliError(EXIT_ERROR, f"log dump not found: {path}", "pass a dumped track log")
    try:
        dump = read_log_file(path)
    except LogParseError as exc:
        raise _CliError(EXIT_ERROR, str(exc), "the file is not an M-241 log dump") from exc
    except M241Error as exc:
        raise _CliError(EXIT_ERROR, str(exc)) from exc

    blocks = [
        {
            "offset": block.offset,
            "count": block.count,
            "bitmask": block.bitmask,
            "policy": block.policy,
            "period_s": block.period_s,
            "distance_m": block.distance_m,
            "speed_kmh": block.speed_kmh,
            "used_bytes": block.used_bytes,
        }
        for block in dump.blocks
    ]
    document = {
        "file": str(path),
        "size": len(dump.data),
        "blocks": blocks,
        "record_length": dump.record_length,
        "records": len(dump.records),
        "crc_errors": sum(1 for record in dump.records if not record.crc_ok),
        "first_utc": _iso(dump.first_utc),
        "last_utc": _iso(dump.last_utc),
        "capacity": _capacity_section(dump),
    }
    if args.json:
        ui.emit_json(document)
        return EXIT_OK

    ui.result(f"file          : {path}")
    ui.result(f"size          : {len(dump.data)} bytes")
    ui.result(f"blocks        : {len(blocks)}")
    for block in blocks:
        ui.result(
            f"  block 0x{block['offset']:06X}  count={block['count']}  "
            f"bitmask=0x{block['bitmask']:08X}  policy=0x{block['policy']:04X}  "
            f"period={block['period_s']:g}s  distance={block['distance_m']:g}m  "
            f"speed={block['speed_kmh']:g}km/h  used={block['used_bytes']} bytes"
        )
    ui.result(f"record length : {document['record_length']} bytes")
    ui.result(f"records       : {document['records']}")
    ui.result(f"crc errors    : {document['crc_errors']}")
    ui.result(f"first utc     : {document['first_utc'] or 'n/a'}")
    ui.result(f"last utc      : {document['last_utc'] or 'n/a'}")
    ui.result("capacity      :")
    for flash in ("2M", "4M"):
        parts = [
            f"{row['record_bytes']} B -> {row['points']:,} points "
            f"({row['used_bytes']:,} of {row['usable_bytes']:,} used, "
            f"{row['fill_ratio'] * 100:.2f}%)"
            for row in document["capacity"][flash]
        ]
        ui.result(f"  {flash}: " + "; ".join(parts))
    return EXIT_OK


def _shift_dump(dump: Any, days: int) -> Any:
    """Return a copy of *dump* with every record timestamp shifted by *days*."""
    shifted = [
        replace(
            record,
            utc=None if record.utc is None else record.utc + timedelta(days=days),
        )
        for record in dump.records
    ]
    return replace(dump, records=shifted)


def _resolve_utc_offset(spec: str | int | None, dump: Any) -> int:
    """Resolve ``--utc-offset-days`` (``auto`` uses the WNRO staleness heuristic)."""
    if spec is None:
        return 0
    if spec != "auto":
        return int(spec)
    from m241.nmea.repair import WNRO_DAYS, needs_wnro_fix

    last = dump.last_utc
    if last is None:
        return 0
    return WNRO_DAYS if needs_wnro_fix(last.date()) else 0


def _cmd_logs_read(args: argparse.Namespace, ui: _Ui) -> int:
    from m241.logfile import export, read_log_file
    from m241.logfile.capacity import capacity

    path = Path(args.file)
    if not path.is_file():
        raise _CliError(EXIT_ERROR, f"log dump not found: {path}", "pass a dumped track log")
    try:
        dump = read_log_file(path, strict=bool(args.strict))
    except M241Error as exc:
        hint = "--strict is on; drop it to tolerate bad records" if args.strict else None
        raise _CliError(EXIT_ERROR, str(exc), hint) from exc

    days = _resolve_utc_offset(args.utc_offset_days, dump)
    if days:
        dump = _shift_dump(dump, days)
        ui.log(f"shifted every record by +{days} days (WNRO fix)")

    if args.flash:
        from m241.logfile.capacity import record_bytes_for_version

        if args.firmware:
            record_bytes = record_bytes_for_version(args.firmware)
        else:
            record_bytes = dump.record_length or 16
        cap = capacity(args.flash, record_bytes)
        used = dump.used_bytes()
        ui.log(
            f"usage on {args.flash} flash: {used:,} of {cap.usable_bytes:,} usable "
            f"bytes ({100.0 * used / cap.usable_bytes:.2f}%); capacity "
            f"{cap.points:,} records at {record_bytes} B"
        )

    try:
        text = export(dump, args.format)
    except M241Error as exc:
        raise _CliError(EXIT_USAGE, str(exc)) from exc

    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        ui.log(f"wrote {args.format} to {output}")
        return EXIT_OK
    sys.stdout.write(text if text.endswith("\n") else text + "\n")
    return EXIT_OK


def _cmd_logs_capacity(args: argparse.Namespace, ui: _Ui) -> int:
    from m241.logfile.capacity import capacity, record_bytes_for_version

    if args.interval <= 0:
        raise _CliError(EXIT_USAGE, "--interval must be positive")
    record_sizes = (16, 20)
    if args.firmware:
        record_sizes = (record_bytes_for_version(args.firmware),)
    flashes = ("2M", "4M") if args.flash == "all" else (args.flash,)

    rows = []
    for flash in flashes:
        for record_bytes in record_sizes:
            cap = capacity(flash, record_bytes)
            rows.append(
                {
                    "flash": flash,
                    "blocks": cap.blocks,
                    "usable_bytes": cap.usable_bytes,
                    "record_bytes": record_bytes,
                    "points": cap.points,
                    "hours_at_interval": round(
                        cap.duration_hours(args.interval), 2
                    ),
                }
            )
    if args.json:
        ui.emit_json({"rows": rows})
        return EXIT_OK

    header = (
        f"{'flash':<7}{'blocks':<8}{'usable bytes':<15}"
        f"{'16-byte records':<21}{'20-byte records'}"
    )
    ui.result(header)
    for flash in flashes:
        row_map = {row["record_bytes"]: row for row in rows if row["flash"] == flash}
        cells = []
        for record_bytes in (16, 20):
            row = row_map.get(record_bytes)
            cells.append(
                f"{row['points']:,} ({row['hours_at_interval']:.1f} h)"
                if row
                else "-"
            )
        first = row_map.get(16) or row_map.get(20)
        assert first is not None
        ui.result(
            f"{flash:<7}{first['blocks']:<8}{first['usable_bytes']:<15,}"
            f"{cells[0]:<21}{cells[1]}"
        )
    return EXIT_OK


# ---------------------------------------------------------------------------
# `m241 nmea ...`
# ---------------------------------------------------------------------------
def _read_text_input(path: str | None) -> str:
    if path in (None, "-"):
        return sys.stdin.read()
    try:
        with Path(path).open("r", encoding="latin-1", newline="") as stream:
            return stream.read()
    except OSError as exc:
        raise _CliError(EXIT_ERROR, f"cannot read {path}: {exc}") from exc


def _write_text_output(path: str | None, text: str) -> None:
    if path in (None, "-"):
        sys.stdout.write(text)
        sys.stdout.flush()
        return
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="latin-1", newline="") as stream:
            stream.write(text)
    except OSError as exc:
        raise _CliError(EXIT_ERROR, f"cannot write {path}: {exc}") from exc


def _cmd_nmea_repair(args: argparse.Namespace, ui: _Ui) -> int:
    from m241.nmea import repair_sentence

    offset_days = 0
    auto = True
    if args.offset_days != "auto":
        offset_days = int(args.offset_days)
        auto = False
    wall_clock = None
    if args.wall_clock:
        try:
            wall_clock = date.fromisoformat(args.wall_clock)
        except ValueError as exc:
            raise _CliError(
                EXIT_USAGE, f"--wall-clock must be YYYY-MM-DD, not {args.wall_clock!r}"
            ) from exc

    text = _read_text_input(args.input)
    out_lines: list[str] = []
    read = 0
    changed = 0
    for line in text.splitlines(keepends=True):
        repaired, was_changed = repair_sentence(
            line, offset_days=offset_days, auto=auto, wall_clock=wall_clock
        )
        read += 1
        if was_changed:
            changed += 1
        out_lines.append(repaired)

    stats = {
        "read": read,
        "changed": changed,
        "offset_days": args.offset_days,
        "wall_clock": _iso(datetime.combine(wall_clock, datetime.min.time()))
        if wall_clock
        else None,
    }
    if args.json or args.stats:
        if args.json:
            ui.emit_json({"ok": True, **stats})
            return EXIT_OK
        ui.log(f"sentences read: {read}; dates repaired: {changed}")
    _write_text_output(args.output, "".join(out_lines))
    return EXIT_OK


def _parse_tcp(text: str) -> tuple[str, int]:
    """Parse ``HOST:PORT`` (or a bare port) into a (host, port) tuple."""
    value = str(text).strip()
    host, _, port_text = value.rpartition(":")
    if not host:
        host, port_text = "127.0.0.1", value
    try:
        port = int(port_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid --tcp value: {text!r}") from exc
    if not 0 < port < 65536:
        raise argparse.ArgumentTypeError(f"invalid TCP port: {port}")
    return host, port


def _is_loopback(host: str) -> bool:
    return host in ("127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1", "")


def _cmd_nmea_proxy(args: argparse.Namespace, ui: _Ui) -> int:
    from m241.nmea.proxy import NmeaProxy

    port = _resolve_port(args.port)
    offset_days = 0
    auto = True
    if args.offset_days != "auto":
        offset_days = int(args.offset_days)
        auto = False
    baud = args.nmea_baud or args.baud
    try:
        proxy = NmeaProxy(
            port,
            offset_days=offset_days,
            auto=auto,
            baud=baud,
            serial_factory=_SERIAL_FACTORY,
        )
    except M241Error as exc:
        raise _CliError(EXIT_HARDWARE, str(exc)) from exc

    duration = args.duration
    if duration is not None and duration <= 0:
        raise _CliError(EXIT_USAGE, "--duration must be positive")

    if duration is not None:
        timer = threading.Timer(duration, proxy.stop)
        timer.daemon = True
    else:
        timer = None

    try:
        if args.stdout:
            if timer is not None:
                timer.start()
            try:
                proxy.serve_stdout()
            except (OSError, M241Error) as exc:
                raise _CliError(EXIT_HARDWARE, f"NMEA proxy failed: {exc}") from exc
            finally:
                if timer is not None:
                    timer.cancel()
        else:
            host, tcp_port = args.tcp or ("127.0.0.1", 10110)
            if not _is_loopback(host):
                ui.warn(
                    f"serving on non-loopback address {host}: there is no "
                    "authentication; anyone reachable can read the stream"
                )
            try:
                thread = proxy.serve_tcp_in_thread(host, tcp_port)
            except M241Error as exc:
                raise _CliError(EXIT_ERROR, str(exc)) from exc
            ui.log(f"NMEA proxy listening on {host}:{tcp_port} (Ctrl-C to stop)")
            try:
                if duration is not None:
                    thread.join(timeout=duration)
                else:
                    thread.join()
            except KeyboardInterrupt:  # pragma: no cover - interactive only
                ui.log("stopping")
            finally:
                proxy.stop()
                thread.join(timeout=5.0)
    finally:
        proxy.close()

    ui.log(
        f"proxy stopped: {proxy.lines_processed} line(s) read, "
        f"{proxy.lines_changed} date(s) repaired"
    )
    return EXIT_OK


# ---------------------------------------------------------------------------
# Synthetic fixtures used by `m241 selftest` (never written to the repo)
# ---------------------------------------------------------------------------
_SYNTHETIC_SIZE = 406620
_SYNTHETIC_VERSION_OFFSET = 0x59C28


def _synthetic_firmware() -> bytes:
    """Build an in-memory image with the documented V1.13 header layout."""
    from m241.firmware.image import EPOCH_CONSTANT_OFFSETS, GPS_EPOCH_UNIX

    data = bytearray(_SYNTHETIC_SIZE)
    data[0x00:0x20] = bytes.fromhex("18f09fe5" * 8)  # thumb vector table
    data[0x90:0x9B] = b"HOLUX_M-241"
    struct.pack_into("<I", data, 0xF4, _SYNTHETIC_SIZE - 4)
    struct.pack_into("<H", data, 0x108, 67)
    data[0x148:0x14E] = b"B-core"
    data[0x15A] = 1
    data[0x15B] = 20
    data[0x190:0x199] = b"SPP Slave"
    data[0x1B0:0x1B9] = b"01017-00F"
    for offset in EPOCH_CONSTANT_OFFSETS:
        struct.pack_into("<I", data, offset, GPS_EPOCH_UNIX)
    data[_SYNTHETIC_VERSION_OFFSET : _SYNTHETIC_VERSION_OFFSET + 5] = b"V1.13"
    return bytes(data)


def _synthetic_log_dump(points: int = 4) -> bytes:
    """Build a one-block dump using the V1.13 20-byte record layout."""
    from m241.logfile.record import (
        BLOCK_HEADER_SIZE,
        BLOCK_SIZE,
        build_block_header,
        build_record,
    )

    mask = (1 << 0) | (1 << 2) | (1 << 3) | (1 << 4) | (1 << 5)
    start = int(datetime(2026, 5, 3, 2, 21, 27, tzinfo=timezone.utc).timestamp())
    body = bytearray()
    for index in range(points):
        payload = (
            struct.pack("<I", start + index * 5)
            + struct.pack("<f", 35.6895 + index * 1e-4)
            + struct.pack("<f", 139.6917)
            + struct.pack("<f", 42.5)[1:4]
            + struct.pack("<f", 12.5)
        )
        body += build_record(payload, bitmask=mask)
    header = build_block_header(
        count=points, bitmask=mask, policy=0x0106, period_ds=10
    ).ljust(BLOCK_HEADER_SIZE, b"\x00")
    return bytes(header + body).ljust(BLOCK_SIZE, b"\xff")


def _stage_firmware_roundtrip() -> str:
    """Dump the synthetic image back through the real BootROM code path."""
    from m241.protocol.brom import MtkBootRom, dump_firmware
    from m241.protocol.simulator import FakeDevice

    firmware = _synthetic_firmware()
    device = FakeDevice(firmware)
    boot = MtkBootRom("SIM", serial_factory=device.factory, timeout=1.0)
    try:
        boot.start()
        dumped = dump_firmware(boot, length=len(firmware), byte_swap=True)
    finally:
        boot.close()
    if dumped != firmware:
        raise _CliError(
            EXIT_VERIFY,
            f"BootROM roundtrip mismatch ({len(dumped)} of {len(firmware)} bytes)",
        )
    return f"{len(firmware)} bytes, sha256 {_sha256_bytes(dumped)[:16]}..."


def _stage_patch() -> str:
    """Patch the synthetic image in memory and verify every rewrite."""
    from m241.firmware import (
        FirmwareImage,
        apply_patch_plan,
        build_patch_plan,
        detect_profile,
        verify_patched,
    )

    image = FirmwareImage.from_bytes(_synthetic_firmware())
    if detect_profile(image).name != "v1.13":
        raise _CliError(EXIT_VERIFY, "the synthetic image was not detected as v1.13")
    plan = build_patch_plan(image)
    patched = apply_patch_plan(image, plan)
    verify_patched(patched, plan)
    offsets = plan.changed_offsets()
    if offsets != [0x402F6, 0x402F7, _SYNTHETIC_VERSION_OFFSET + 4]:
        raise _CliError(EXIT_VERIFY, f"unexpected changed offsets {offsets}")
    if patched.version_string() != "V1.14":
        raise _CliError(EXIT_VERIFY, "the version marker was not rewritten to V1.14")
    return (
        f"v1.13 -> V1.14, {len(offsets)} bytes changed, "
        f"sha256 {patched.sha256()[:16]}..."
    )


def _stage_flash_roundtrip() -> str:
    """Flash the patched image through the simulated DA, then re-read it."""
    from m241.firmware import FirmwareImage, apply_patch_plan, build_patch_plan
    from m241.protocol.brom import MtkBootRom, dump_firmware
    from m241.protocol.da import DownloadAgent
    from m241.protocol.simulator import FakeDevice

    stock = _synthetic_firmware()
    image = FirmwareImage.from_bytes(stock)
    patched = apply_patch_plan(image, build_patch_plan(image))
    expected = patched.to_bytes()
    device = FakeDevice(stock)
    boot = MtkBootRom("SIM", serial_factory=device.factory, timeout=1.0)
    try:
        boot.start()
        agent = DownloadAgent(boot)
        with tempfile.TemporaryDirectory(prefix="m241-selftest-") as directory:
            da_path = Path(directory) / "DA_v3.8.4.bin"
            da_path.write_bytes(b"\x42" * 0x800)
            info = agent.start(da_path)
            if len(expected) > int(info.get("flash_size", 0)):
                raise _CliError(EXIT_VERIFY, "the patched image does not fit the fake flash")
            agent.write_firmware(expected, start=0)
            agent.restart()
        boot.start()
        dumped = dump_firmware(boot, length=len(expected), byte_swap=True)
    finally:
        boot.close()
    if dumped != expected:
        raise _CliError(EXIT_VERIFY, "the re-read image differs from what was flashed")
    return (
        f"DA 0x{int(info.get('da_version', 0)):04X}, flash "
        f"{int(info.get('flash_size', 0))} bytes, re-read sha256 "
        f"{_sha256_bytes(dumped)[:16]}..."
    )


def _stage_log() -> str:
    """Parse the synthetic 20-byte log dump and export it in every format."""
    from m241.logfile import export, read_log_dump

    dump = read_log_dump(_synthetic_log_dump())
    if dump.record_length != 20 or len(dump.records) != 4:
        raise _CliError(
            EXIT_VERIFY,
            f"log parse gave {len(dump.records)} records of "
            f"{dump.record_length} bytes (expected 4 x 20)",
        )
    csv_text = export(dump, "csv")
    gpx_text = export(dump, "gpx")
    nmea_text = export(dump, "nmea")
    json_text = export(dump, "json")
    if "35.6894" not in csv_text or "<trkpt" not in gpx_text:
        raise _CliError(EXIT_VERIFY, "the CSV/GPX export lost the first position")
    if not nmea_text.startswith("$GPRMC") or "030526" not in nmea_text:
        raise _CliError(EXIT_VERIFY, "the NMEA export is not a fixed RMC stream")
    if '"records": 4' not in json_text:
        raise _CliError(EXIT_VERIFY, "the JSON export does not report 4 records")
    return f"4 x 20-byte records, csv/gpx/nmea/json exports OK, first {dump.first_utc}"


def _stage_nmea_repair() -> str:
    """Repair the blog's sample sentence and validate the new checksum."""
    from m241.nmea import is_valid, repair_sentence

    repaired, changed = repair_sentence(_SAMPLE_RMC)
    if not changed:
        raise _CliError(EXIT_VERIFY, "the sample sentence was not repaired")
    if "030526" not in repaired or "170906" in repaired:
        raise _CliError(EXIT_VERIFY, f"unexpected repaired date in {repaired}")
    if not is_valid(repaired):
        raise _CliError(EXIT_VERIFY, f"the repaired checksum is invalid: {repaired}")
    return f"170906 -> 030526, checksum {'*' + repaired.rsplit('*', 1)[1]} valid"


def _stage_nmea_proxy() -> str:
    """Run the proxy over an in-memory stream and confirm the dates change."""
    import io

    from m241.nmea import pump_proxy

    source = io.BytesIO((_SAMPLE_RMC + "\r\n").encode("ascii"))
    sink = io.StringIO()
    count = pump_proxy(source, sink)
    text = sink.getvalue()
    if count != 1 or "030526" not in text:
        raise _CliError(EXIT_VERIFY, f"the proxy produced {count} line(s): {text!r}")
    return "1 line relayed, WNRO date repaired, checksum recomputed"


_SELFTEST_STAGES = (
    ("firmware-bootrom", _stage_firmware_roundtrip),
    ("firmware-patch", _stage_patch),
    ("firmware-flash", _stage_flash_roundtrip),
    ("log-parse-export", _stage_log),
    ("nmea-repair", _stage_nmea_repair),
    ("nmea-proxy", _stage_nmea_proxy),
)


def _cmd_selftest(args: argparse.Namespace, ui: _Ui) -> int:
    stages: list[dict[str, Any]] = []
    for name, stage in _SELFTEST_STAGES:
        try:
            detail = stage()
        except _CliError as exc:
            stages.append({"name": name, "ok": False, "detail": exc.message})
            detail = exc.message
        except Exception as exc:  # noqa: BLE001 - a selftest reports, never raises
            detail = f"{type(exc).__name__}: {exc}"
            stages.append({"name": name, "ok": False, "detail": detail})
        else:
            stages.append({"name": name, "ok": True, "detail": detail})
        if not args.json:
            ui.result(f"{'ok  ' if stages[-1]['ok'] else 'FAIL'}  {name}: {detail}")
        else:
            ui.log(f"{'ok' if stages[-1]['ok'] else 'FAIL'} {name}")

    ok = all(stage["ok"] for stage in stages)
    if args.json:
        ui.emit_json({"ok": ok, "stages": stages})
        return EXIT_OK if ok else EXIT_ERROR
    ui.result("")
    ui.result(f"selftest: {'PASS' if ok else 'FAIL'} ({len(stages)} stages)")
    return EXIT_OK if ok else EXIT_ERROR


# ---------------------------------------------------------------------------
# `m241 version`
# ---------------------------------------------------------------------------
def _cmd_version(_args: argparse.Namespace, ui: _Ui) -> int:
    ui.result(f"m241 {__version__}")
    ui.result(f"Python {platform.python_version()}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------
_DESCRIPTION = """\
Cross-platform toolkit for the Holux M-241 GPS logger.

Dump, patch and re-flash the firmware, repair the GPS week-number-rollover
clock, download and convert track logs, and correct a live NMEA stream.
Everything except the hardware commands runs offline; `m241 selftest` proves
it on the built-in simulator.
"""

_EPILOG = """\
exit codes: 0 ok, 1 runtime error, 2 usage, 3 hardware not found,
            4 verification failed, 5 safety refusal, 6 flash write failed

examples:
  m241 ports
  m241 dump auto -o backup/stock.bin
  m241 patch backup/stock.bin -o backup/v114.bin
  m241 flash auto --da vendor/DA_v3.8.4.bin --fw backup/v114.bin \\
      --backup backup/stock.bin
  m241 logs capacity
  m241 nmea proxy auto --tcp 127.0.0.1:10110
  m241 selftest
"""


def _add_json_option(parser: argparse.ArgumentParser, text: str = "print one JSON object on stdout") -> None:
    parser.add_argument("--json", action="store_true", help=text)


def build_parser() -> argparse.ArgumentParser:
    """Build the full ``m241`` argument parser."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        default=argparse.SUPPRESS,
        help="suppress progress and status messages",
    )
    common.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS,
        help="print extra diagnostics on stderr",
    )

    parser = argparse.ArgumentParser(
        prog=PROG,
        description=_DESCRIPTION,
        epilog=_EPILOG,
        parents=[common],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"m241 {__version__} (Python {platform.python_version()})",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    # -- ports -------------------------------------------------------------
    p = sub.add_parser(
        "ports",
        parents=[common],
        help="list serial ports and flag likely M-241 cables",
        description="List serial ports; a '*' marks a plausible M-241 USB-serial bridge.",
    )
    _add_json_option(p)
    p.set_defaults(func=_cmd_ports)

    # -- doctor ------------------------------------------------------------
    p = sub.add_parser(
        "doctor",
        parents=[common],
        help="diagnose the environment, vendor drop and device",
        description=(
            "Diagnostics: environment, serial ports, vendor drop, simulated "
            "device and the shipped firmware profiles."
        ),
    )
    _add_json_option(p)
    p.add_argument("--simulate", action="store_true", help="run the built-in simulator")
    p.add_argument(
        "--from-drop",
        metavar="PATH",
        help="hash and identify a folder of official Holux packages",
    )
    p.add_argument(
        "--extract-to",
        metavar="DIR",
        help="copy the download agent (never DLLs/EXEs) into DIR",
    )
    p.add_argument(
        "--include-firmware",
        action="store_true",
        help="with --extract-to, also copy the stock firmware images",
    )
    p.add_argument("--port", metavar="PORT", help="try opening this port as a check")
    p.set_defaults(func=_cmd_doctor)

    # -- info --------------------------------------------------------------
    p = sub.add_parser(
        "info",
        parents=[common],
        help="probe the device (PMTK605/705 firmware id)",
        description="Open the port in NMEA mode and ask for the firmware identity.",
    )
    _add_json_option(p)
    p.add_argument("port", nargs="?", default="auto", help="serial port (default: auto)")
    p.add_argument("--nmea-baud", type=_parse_int, default=DEFAULT_NMEA_BAUD,
                   help=f"NMEA baud rate (default {DEFAULT_NMEA_BAUD})")
    p.add_argument("--aiding", action="store_true",
                   help="also send the undocumented PMTK715/716/717 aiding commands")
    p.set_defaults(func=_cmd_info)

    # -- dump --------------------------------------------------------------
    p = sub.add_parser(
        "dump",
        parents=[common],
        help="read firmware over the BootROM (read-only)",
        description=(
            "Dump the firmware over the BootROM. Read-only: nothing is written "
            "to the device. The image is verified by dumping twice."
        ),
    )
    _add_json_option(p)
    p.add_argument("port", nargs="?", default="auto", help="serial port (default: auto)")
    p.add_argument("-o", "--output", required=True, metavar="FILE", help="output image")
    p.add_argument("--base-addr", type=_parse_int, default=0x20000000, metavar="ADDR",
                   help="firmware base address (default 0x20000000; 0x0 for the M3318 view)")
    p.add_argument("--length", type=_parse_int, default=None, metavar="N",
                   help="byte count to read (default: from the header at 0xF4)")
    p.add_argument("--baud", type=_parse_int, default=DEFAULT_BAUD,
                   help=f"BootROM baud rate (default {DEFAULT_BAUD})")
    p.add_argument("--nmea-baud", type=_parse_int, default=DEFAULT_NMEA_BAUD,
                   help=f"NMEA baud rate used by --pmtk180 (default {DEFAULT_NMEA_BAUD})")
    p.add_argument("--no-swap", action="store_true", help="do not byte-swap 16-bit pairs")
    p.add_argument("--assume-brom", action="store_true",
                   help="the chip is already in BootROM; only run the sync")
    p.add_argument("--pmtk180", action="store_true",
                   help="send $PMTK180 first (legacy BootROM entry)")
    p.add_argument("--verify", dest="verify", action="store_true", default=True,
                   help="dump twice and compare (default)")
    p.add_argument("--no-verify", dest="verify", action="store_false",
                   help="skip the second dump")
    p.add_argument("--force", action="store_true", help="overwrite an existing output file")
    p.set_defaults(func=_cmd_dump)

    # -- patch -------------------------------------------------------------
    p = sub.add_parser(
        "patch",
        parents=[common],
        help="apply the WNRO + version-marker patch",
        description=(
            "Apply the verified WNRO fix (epoch constant 0x402F4) and the "
            "cosmetic V1.xx -> V1.14 marker. Writes nothing with --dry-run."
        ),
    )
    _add_json_option(p)
    p.add_argument("source", metavar="SRC", help="firmware image to patch")
    p.add_argument("-o", "--output", metavar="DST", help="patched output image")
    p.add_argument("--in-place", action="store_true",
                   help="allow writing the result over SRC")
    p.add_argument("--profile", default="auto", choices=("auto", "v1.12", "v1.13"),
                   help="force a firmware profile (default: auto-detect)")
    p.add_argument("--target-version", default="V1.14", metavar="V1.xx",
                   help="cosmetic version marker (default V1.14)")
    p.add_argument("--no-version-marker", action="store_true",
                   help="only apply the WNRO fix")
    p.add_argument("--dry-run", action="store_true",
                   help="show the plan and write nothing")
    p.set_defaults(func=_cmd_patch)

    # -- flash -------------------------------------------------------------
    p = sub.add_parser(
        "flash",
        parents=[common],
        help="write firmware over the download agent (DESTRUCTIVE)",
        description=(
            "DESTRUCTIVE: pre-flight checks, then write the image through the "
            "MTK download agent. Read DISCLAIMER.md first."
        ),
    )
    _add_json_option(p)
    p.add_argument("port", nargs="?", default="auto", help="serial port (default: auto)")
    p.add_argument("--da", required=True, metavar="DA", help="DA_v3.8.4.bin")
    p.add_argument("--fw", required=True, metavar="IMG", help="firmware image to write")
    p.add_argument("--backup", required=True, metavar="BACKUP",
                   help="your stock dump (must be the source of --fw)")
    p.add_argument("--start-addr", type=_parse_int, default=0x0, metavar="ADDR",
                   help="linear flash address to start writing at (default 0x0)")
    p.add_argument("--baud", type=_parse_int, default=DEFAULT_BAUD, help="initial baud rate")
    p.add_argument("--nmea-baud", type=_parse_int, default=DEFAULT_NMEA_BAUD,
                   help="NMEA baud rate used by --pmtk180")
    p.add_argument("--high-baud", type=_parse_int, default=None, metavar="N",
                   help="switch to this baud rate after the DA starts (230400/460800/921600)")
    p.add_argument("--assume-brom", action="store_true",
                   help="the chip is already in BootROM; only run the sync")
    p.add_argument("--pmtk180", action="store_true",
                   help="send $PMTK180 first (legacy BootROM entry)")
    p.add_argument("--yes", action="store_true",
                   help="skip the interactive confirmation (read DISCLAIMER.md!)")
    p.set_defaults(func=_cmd_flash)

    # -- logs --------------------------------------------------------------
    logs = sub.add_parser(
        "logs",
        parents=[common],
        help="inspect and convert dumped track logs",
        description="Inspect the binary track log and convert it to GPX/CSV/NMEA/KML/JSON.",
    )
    logs_sub = logs.add_subparsers(dest="logs_command", metavar="SUBCOMMAND", required=True)

    p = logs_sub.add_parser(
        "info",
        parents=[common],
        help="block/record summary of a dumped track log",
        description="Summarise blocks, records, CRCs, date range and flash usage.",
    )
    _add_json_option(p)
    p.add_argument("file", metavar="FILE", help="raw log dump")
    p.set_defaults(func=_cmd_logs_info)

    p = logs_sub.add_parser(
        "read",
        parents=[common],
        help="convert a dumped track log",
        description="Convert a raw log dump to CSV, GPX, NMEA, KML or JSON.",
    )
    p.add_argument("file", metavar="FILE", help="raw log dump")
    p.add_argument("--format", default="csv",
                   choices=("csv", "gpx", "nmea", "kml", "json"),
                   help="output format (default: csv)")
    p.add_argument("-o", "--output", metavar="FILE", help="output file (default: stdout)")
    p.add_argument("--utc-offset-days", type=_parse_offset_days, default="auto",
                   metavar="auto|N",
                   help="shift timestamps (default: auto, i.e. only if they look stale)")
    p.add_argument("--flash", default=None, choices=("2M", "4M"),
                   help="flash geometry for the usage report (never affects the parse)")
    p.add_argument("--firmware", default=None, choices=("V1.12", "V1.13"),
                   help="firmware version for the capacity report")
    p.add_argument("--strict", action="store_true",
                   help="fail on a bad CRC or a truncated record")
    p.set_defaults(func=_cmd_logs_read)

    p = logs_sub.add_parser(
        "capacity",
        parents=[common],
        help="honest point-count table for 2M/4M x 16B/20B",
        description="Print the exact point capacity of 2 MiB and 4 MiB flash.",
    )
    _add_json_option(p)
    p.add_argument("--flash", default="all", choices=("2M", "4M", "all"),
                   help="flash size to report (default: all)")
    p.add_argument("--firmware", default=None, choices=("V1.12", "V1.13"),
                   help="only report the record size of this firmware version")
    p.add_argument("--interval", type=float, default=1.0, metavar="SECONDS",
                   help="logging interval for the duration estimate (default 1.0)")
    p.set_defaults(func=_cmd_logs_capacity)

    # -- nmea --------------------------------------------------------------
    nmea = sub.add_parser(
        "nmea",
        parents=[common],
        help="repair or relay NMEA streams",
        description="Repair WNRO dates in a captured stream, or relay a live one over TCP.",
    )
    nmea_sub = nmea.add_subparsers(dest="nmea_command", metavar="SUBCOMMAND", required=True)

    p = nmea_sub.add_parser(
        "repair",
        parents=[common],
        help="rewrite WNRO dates in a captured NMEA stream",
        description=(
            "Rewrite the date inside RMC/ZDA sentences and recompute the "
            "checksum. Every other line passes through verbatim."
        ),
    )
    p.add_argument("--in", dest="input", default="-", metavar="FILE",
                   help="input file, or '-' for stdin (default)")
    p.add_argument("--out", dest="output", default="-", metavar="FILE",
                   help="output file, or '-' for stdout (default)")
    p.add_argument("--offset-days", type=_parse_offset_days, default="auto",
                   metavar="auto|7168", help="date shift (default: auto)")
    p.add_argument("--wall-clock", metavar="YYYY-MM-DD",
                   help="'today' used by the auto heuristic")
    p.add_argument("--stats", action="store_true", help="report how many dates changed")
    _add_json_option(p, "with --stats, print the counters as JSON instead of the stream")
    p.set_defaults(func=_cmd_nmea_repair)

    p = nmea_sub.add_parser(
        "proxy",
        parents=[common],
        help="relay a live NMEA stream with repaired dates",
        description=(
            "Read NMEA from the logger, repair the dates and serve the result "
            "over TCP (default 127.0.0.1:10110) or stdout."
        ),
    )
    p.add_argument("port", nargs="?", default="auto", help="serial port (default: auto)")
    p.add_argument("--offset-days", type=_parse_offset_days, default="auto",
                   metavar="auto|7168", help="date shift (default: auto)")
    p.add_argument("--tcp", type=_parse_tcp, default=None, metavar="HOST:PORT",
                   help="listen address (default 127.0.0.1:10110)")
    p.add_argument("--stdout", action="store_true", help="write the stream to stdout")
    p.add_argument("--baud", type=_parse_int, default=DEFAULT_NMEA_BAUD,
                   help=f"serial baud rate (default {DEFAULT_NMEA_BAUD})")
    p.add_argument("--nmea-baud", type=_parse_int, default=None,
                   help="alias of --baud for the NMEA stream")
    p.add_argument("--duration", type=float, default=None, metavar="SECONDS",
                   help="stop after N seconds (for tests)")
    p.set_defaults(func=_cmd_nmea_proxy)

    # -- selftest ----------------------------------------------------------
    p = sub.add_parser(
        "selftest",
        parents=[common],
        help="end-to-end run on the built-in simulator (no hardware)",
        description=(
            "Hardware-free end-to-end check: synthetic firmware roundtrip, "
            "patch, simulated flash, log parsing/export and NMEA repair."
        ),
    )
    _add_json_option(p)
    p.set_defaults(func=_cmd_selftest)

    # -- version -----------------------------------------------------------
    p = sub.add_parser("version", parents=[common], help="print the version")
    p.set_defaults(func=_cmd_version)

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def _report_error(exc: _CliError, args: argparse.Namespace) -> None:
    """Render a :class:`_CliError` to stderr (and as JSON when requested)."""
    print(f"m241: error: {exc.message}", file=sys.stderr)
    if exc.hint:
        print(f"hint: {exc.hint}", file=sys.stderr)
    if getattr(args, "json", False):
        print(
            json.dumps(
                {"ok": False, "error": exc.message, "exit_code": exc.code},
                indent=2,
                ensure_ascii=False,
            )
        )


def main(argv: list[str] | None = None) -> int:
    """Run the CLI; returns the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help(sys.stdout)
        return EXIT_OK
    ui = _ui_from_args(args)
    try:
        return int(args.func(args, ui))
    except _CliError as exc:
        _report_error(exc, args)
        return exc.code
    except M241Error as exc:
        _report_error(_CliError(EXIT_ERROR, str(exc)), args)
        return EXIT_ERROR
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        print("m241: interrupted", file=sys.stderr)
        return EXIT_ERROR
    except Exception as exc:  # noqa: BLE001 - never leak a traceback by default
        if ui.verbose:
            raise
        print(f"m241: error: unexpected failure: {type(exc).__name__}: {exc}", file=sys.stderr)
        if getattr(args, "json", False):
            print(
                json.dumps({"ok": False, "error": str(exc), "exit_code": EXIT_ERROR}, indent=2)
            )
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover - exercised via ``python -m m241``
    raise SystemExit(main())
