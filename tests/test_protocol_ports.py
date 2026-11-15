"""Tests for :mod:`m241.protocol.ports` (port discovery and opening).

No hardware is touched: pyserial's enumeration backend and ``serial.Serial``
are monkeypatched.  One test inspects the shipped ``m241`` sources to enforce
the "pyserial only inside m241.protocol, lazily" rule.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from m241.errors import M241Error
from m241.protocol import ports


def _entry(
    device: str,
    description: str = "",
    hwid: str = "",
    vid: int | None = None,
    pid: int | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        device=device, description=description, hwid=hwid, vid=vid, pid=pid
    )


def _install_comports(monkeypatch: pytest.MonkeyPatch, entries: list[object]) -> None:
    import serial.tools.list_ports as list_ports

    monkeypatch.setattr(list_ports, "comports", lambda: list(entries))


# -- PortInfo / enumeration -------------------------------------------------


def test_port_info_is_frozen_dataclass() -> None:
    info = ports.PortInfo(device="COM7", description="CP210x", is_candidate=True)
    assert info.device == "COM7"
    assert info.vid is None and info.pid is None
    with pytest.raises(AttributeError):
        info.device = "COM8"  # type: ignore[misc]


def test_list_serial_ports_marks_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_comports(
        monkeypatch,
        [
            _entry("COM3", "Communications Port", "ACPI\\PNP0501"),
            _entry(
                "COM7",
                "Silicon Labs CP210x USB to UART Bridge",
                "USB VID:PID=10C4:EA60",
                0x10C4,
                0xEA60,
            ),
            _entry("COM9", "USB Serial Port", "USB VID:PID=0403:6001", 0x0403, 0x6001),
            _entry("COM11", "USB-SERIAL CH340", "USB VID:PID=1A86:7523", 0x1A86, 0x7523),
        ],
    )
    infos = ports.list_serial_ports()
    assert [info.device for info in infos] == ["COM7", "COM9", "COM11", "COM3"]
    by_device = {info.device: info for info in infos}
    assert by_device["COM7"].is_candidate
    assert by_device["COM7"].vid == 0x10C4
    assert by_device["COM7"].pid == 0xEA60
    assert by_device["COM9"].is_candidate
    assert by_device["COM11"].is_candidate
    assert not by_device["COM3"].is_candidate


def test_candidate_ports_uses_vid_and_text_hints(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_comports(
        monkeypatch,
        [
            _entry("COM1", "Prolific USB-to-Serial Comm Port", "", 0x067B, 0x2303),
            _entry("COM2", "Some GPS receiver", "USB VID:PID=1234:5678", 0x1234, 0x5678),
            _entry("COM3", "Bluetooth link", "BTHENUM\\{0000}", None, None),
        ],
    )
    assert [info.device for info in ports.candidate_ports()] == ["COM1", "COM2"]


def test_list_serial_ports_skips_entries_without_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_comports(monkeypatch, [_entry(""), _entry("COM5")])
    assert [info.device for info in ports.list_serial_ports()] == ["COM5"]


# -- resolve_port -----------------------------------------------------------


def test_resolve_port_returns_explicit_device_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_comports(monkeypatch, [])
    assert ports.resolve_port("COM42") == "COM42"
    assert ports.resolve_port("/dev/ttyUSB3") == "/dev/ttyUSB3"
    assert ports.resolve_port("  COM42  ") == "COM42"


def test_resolve_port_auto_picks_the_single_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_comports(
        monkeypatch,
        [_entry("COM3", "Communications Port"), _entry("COM7", "CP210x", vid=0x10C4, pid=0xEA60)],
    )
    for spec in (None, "auto", "AUTO", "default", ""):
        assert ports.resolve_port(spec) == "COM7"


def test_resolve_port_multiple_candidates_lists_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_comports(
        monkeypatch,
        [
            _entry("COM7", "CP210x", vid=0x10C4, pid=0xEA60),
            _entry("COM9", "FTDI", vid=0x0403, pid=0x6001),
        ],
    )
    with pytest.raises(M241Error) as excinfo:
        ports.resolve_port(None)
    message = str(excinfo.value)
    assert "COM7" in message and "COM9" in message
    assert "multiple candidate" in message


def test_resolve_port_no_candidates_reports_detected_ports_and_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_comports(monkeypatch, [_entry("COM3", "Communications Port")])
    with pytest.raises(M241Error) as excinfo:
        ports.resolve_port(None)
    message = str(excinfo.value)
    assert "no M-241 serial port found" in message
    assert "COM3" in message
    assert ports.platform_hint().split(":")[0] in message


def test_resolve_port_no_ports_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_comports(monkeypatch, [])
    with pytest.raises(M241Error, match="none"):
        ports.resolve_port("auto")


# -- open_serial ------------------------------------------------------------


def test_open_serial_returns_serial_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    import serial

    calls: list[dict[str, object]] = []

    def fake_serial(**kwargs: object) -> object:
        calls.append(kwargs)
        return object()

    monkeypatch.setattr(serial, "Serial", fake_serial)
    handle = ports.open_serial("COM7", baud=38400, timeout=0.5)
    assert handle is not None
    assert calls == [{"port": "COM7", "baudrate": 38400, "timeout": 0.5}]


def test_open_serial_wraps_serial_exception_with_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import serial

    def fail(**kwargs: object) -> object:
        raise serial.SerialException("Access is denied")

    monkeypatch.setattr(serial, "Serial", fail)
    with pytest.raises(M241Error) as excinfo:
        ports.open_serial("COM7")
    message = str(excinfo.value)
    assert "COM7" in message
    assert "Access is denied" in message
    assert "hint" not in message.lower()  # the hint text itself is appended
    assert ports.platform_hint() in message


# -- platform_hint ----------------------------------------------------------


@pytest.mark.parametrize(
    ("platform", "needle"),
    [
        ("win32", "Device Manager"),
        ("linux", "dialout"),
        ("darwin", "usbserial"),
    ],
)
def test_platform_hint_is_os_specific(
    monkeypatch: pytest.MonkeyPatch, platform: str, needle: str
) -> None:
    monkeypatch.setattr(ports.sys, "platform", platform)
    hint = ports.platform_hint()
    assert needle in hint
    assert hint.endswith(".")


def test_platform_hint_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ports.sys, "platform", "freebsd14")
    assert "driver" in ports.platform_hint()


# -- lazy pyserial rules ----------------------------------------------------


def test_importing_m241_protocol_does_not_import_pyserial() -> None:
    """``import m241.protocol`` must not need/import pyserial."""
    repo_root = Path(__file__).resolve().parents[1]
    code = (
        "import sys; "
        f"sys.path.insert(0, {str(repo_root / 'src')!r}); "
        "import m241.protocol; "
        "print('serial' in sys.modules or 'serial.tools' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"


def test_no_top_level_serial_import_outside_protocol() -> None:
    """Every module outside ``m241.protocol`` must import pyserial lazily."""
    package = Path(__file__).resolve().parents[1] / "src" / "m241"
    protocol_dir = package / "protocol"
    offenders: list[str] = []
    for path in sorted(package.rglob("*.py")):
        if protocol_dir in path.parents:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            if any(module.split(".")[0] in ("serial", "pyserial") for module in modules):
                offenders.append(str(path.relative_to(package)))
    assert offenders == []
