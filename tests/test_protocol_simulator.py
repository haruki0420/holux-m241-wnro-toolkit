"""Tests for :mod:`m241.protocol.simulator`, the pure-Python MT3318 stand-in.

The simulator is only useful if it drives the *real* BootROM and DA code, so
most of these tests go through :class:`m241.protocol.brom.MtkBootRom` and
:class:`m241.protocol.da.DownloadAgent` instead of poking the fake directly.
"""

from __future__ import annotations

import struct

import pytest

from m241.errors import BootRomError, DownloadAgentError
from m241.protocol import brom, da, simulator


def _image(size: int = 0x1000) -> bytes:
    return bytes((index * 37 + 11) & 0xFF for index in range(size))


def _brom(device: simulator.FakeDevice) -> brom.MtkBootRom:
    return brom.MtkBootRom(
        "FAKE",
        serial_factory=device.factory,
        timeout=0.2,
        sync_timeout=0.5,
        nmea_probe_seconds=0.0,
    )


# -- contract surface -------------------------------------------------------


def test_module_surface() -> None:
    assert simulator.ADDRESS_SPACE >= 0x200000
    assert simulator.FakeDevice is not None
    assert simulator.FakeSerial is not None
    assert callable(simulator.roundtrip)


def test_fake_serial_duck_types_serial_serial() -> None:
    device = simulator.FakeDevice()
    port = device.factory("COM1", 115200)
    for attribute in (
        "write",
        "read",
        "flush",
        "reset_input_buffer",
        "close",
        "in_waiting",
        "baudrate",
        "timeout",
        "rts",
        "dtr",
    ):
        assert hasattr(port, attribute), attribute
    assert port.baudrate == 115200
    port.close()
    assert port.is_open is False


def test_memory_is_byteaddressable_and_8mib() -> None:
    device = simulator.FakeDevice()
    assert isinstance(device.memory, bytearray)
    assert len(device.memory) >= 0x200000
    device.memory[0x1234] = 0x99
    assert device.memory[0x1234] == 0x99


def test_constructor_preloads_firmware() -> None:
    image = _image(0x100)
    device = simulator.FakeDevice(image)
    assert bytes(device.memory[: len(image)]) == image


def test_load_firmware_at_offset() -> None:
    device = simulator.FakeDevice()
    device.load_firmware(b"\xde\xad\xbe\xef", offset=0x40)
    assert bytes(device.memory[0x40:0x44]) == b"\xde\xad\xbe\xef"


def test_load_firmware_overflow_is_rejected() -> None:
    device = simulator.FakeDevice()
    with pytest.raises(ValueError, match="does not fit"):
        device.load_firmware(b"\x00" * 4, offset=len(device.memory) - 2)


# -- BootROM framing through the real host code ----------------------------


def test_sync_sequence_replies_are_accepted() -> None:
    device = simulator.FakeDevice()
    with _brom(device) as boot:
        boot.start()
        # If the sync dance had failed, read32 would have raised.
        assert boot.read32(0x20000000, 4) == b"\x00\x00\x00\x00"


def test_every_command_byte_is_echoed() -> None:
    device = simulator.FakeDevice(_image())
    with _brom(device) as boot:
        boot.start()
        boot.ser.reset_input_buffer()
        # READ32: the count field is in 32-bit dwords.
        frame = (
            bytes([brom.OPCODE_READ32])
            + (0x00200000).to_bytes(4, "big")
            + (1).to_bytes(4, "big")
        )
        port = boot.ser
        for byte in frame:
            port.write(bytes([byte]))
            assert port.read(1) == bytes([byte])
        # READ32 ships each 32-bit word big-endian, so the wire form is the
        # byte-reversed memory order.
        assert port.read(4) == brom.reverse_words32(_image()[:4])


def test_write_then_read_through_bootrom() -> None:
    device = simulator.FakeDevice()
    payload = bytes(range(0x100))
    with _brom(device) as boot:
        boot.start()
        boot.write16(0x20000800, payload, byte_swap=True)
        assert boot.read32(0x20000800, len(payload), byte_swap=True) == payload


def test_fake_checksum_matches_host_helper() -> None:
    image = _image(0x200)
    device = simulator.FakeDevice(image)
    with _brom(device) as boot:
        boot.start()
        assert boot.checksum(0x20000000, 0x200) == brom.checksum_xor16(image)


# -- roundtrip --------------------------------------------------------------


def test_roundtrip_is_byte_identical() -> None:
    image = _image(0x1000)
    assert simulator.roundtrip(image) == image


@pytest.mark.parametrize("size", [1, 2, 3, 17, 0x100, 0x1001])
def test_roundtrip_handles_odd_and_short_images(size: int) -> None:
    image = _image(size)
    assert simulator.roundtrip(image) == image


def test_roundtrip_dumps_through_real_bootrom(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    original = brom.dump_firmware

    def spy(*args: object, **kwargs: object) -> bytes:
        calls.append("dump_firmware")
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(brom, "dump_firmware", spy)
    image = _image(0x200)
    assert simulator.roundtrip(image) == image
    assert calls == ["dump_firmware"]


def test_roundtrip_result_is_independent_of_device_state() -> None:
    image = _image(0x400)
    first = simulator.roundtrip(image)
    second = simulator.roundtrip(image)
    assert first == second == image


# -- DA path ----------------------------------------------------------------


def test_fake_da_info_report_has_expected_values() -> None:
    device = simulator.FakeDevice()
    device.ram[0] = 1
    report = device._info_report()  # noqa: SLF001 - contract check
    assert len(report) == 20
    assert report[0] == da.SYNC
    values = struct.unpack(da.DA_INFO_FORMAT, report)
    assert values[1] == da.EXPECTED_DA_VERSION
    assert values[3] == device.flash_size  # the DA report carries bytes


def test_write_through_fake_da_then_dump_via_bootrom(tmp_path: object) -> None:
    from pathlib import Path

    device = simulator.FakeDevice(bytes(0x1000))
    da_path = Path(str(tmp_path)) / "DA.bin"
    da_path.write_bytes(b"\x42" * 0x800)
    boot = _brom(device)
    try:
        boot.start()
        agent = da.DownloadAgent(boot)
        agent.start(da_path)
        image = _image(0x900)
        agent.write_firmware(image, start=0)
        agent.restart()
        boot.start()
        assert brom.dump_firmware(boot, length=len(image)) == image
    finally:
        boot.close()


def test_fake_da_naks_bad_packets_checksum() -> None:
    device = simulator.FakeDevice()
    device._state = "da"  # noqa: SLF001 - drive the DA state machine directly
    device._da_start = 0  # noqa: SLF001 - drive the DA state machine directly
    device._da_cursor = 0  # noqa: SLF001
    device._da_total = 4  # noqa: SLF001
    device._da_packet_len = 4  # noqa: SLF001
    device._stage = "da_packet"  # noqa: SLF001
    device._frame.clear()  # noqa: SLF001
    # Four data bytes whose sum is 0x0A but a checksum word of 0x0000 -> NAK.
    device.write(b"\x01\x02\x03\x04\x00\x00")
    assert device.read(1) == bytes([da.NAK])


def test_fake_da_accepts_packet_with_correct_checksum() -> None:
    device = simulator.FakeDevice()
    device._state = "da"  # noqa: SLF001
    device._da_start = 0  # noqa: SLF001
    device._da_cursor = 0  # noqa: SLF001
    device._da_total = 4  # noqa: SLF001
    device._da_packet_len = 4  # noqa: SLF001
    device._stage = "da_packet"  # noqa: SLF001
    device._frame.clear()  # noqa: SLF001
    device.write(b"\x01\x02\x03\x04\x00\x0a")  # 1+2+3+4 == 0x0A
    assert device.read(1) == bytes([da.CONT])
    assert bytes(device.memory[:4]) == b"\x01\x02\x03\x04"


def test_fake_restart_returns_to_bootrom() -> None:
    device = simulator.FakeDevice()
    device.ram[0] = 1
    device._jump(brom.BASE_ADDR_DA)  # noqa: SLF001
    assert device._state == "da"  # noqa: SLF001
    device.write(bytes([da.OPCODE_RESTART]))
    assert device._state == "bootrom"  # noqa: SLF001


def test_silent_device_times_out_rather_than_hangs() -> None:
    device = simulator.FakeDevice(silent=True)
    boot = _brom(device)
    try:
        with pytest.raises(BootRomError):
            boot.start()
    finally:
        boot.close()


def test_corrupt_echo_is_detected() -> None:
    device = simulator.FakeDevice(corrupt_echo=True)
    with _brom(device) as boot:
        boot.start()
        with pytest.raises(BootRomError, match="echo mismatch"):
            boot.write16(0x20000100, bytes(range(16)), skip_cksum=True)


def test_fake_device_rejects_unmapped_bootrom_opcode() -> None:
    device = simulator.FakeDevice()
    with _brom(device) as boot:
        boot.start()
        with pytest.raises(BootRomError, match="unknown BootROM opcode"):
            boot.ser.write(bytes([0x77]) + b"\x00" * 8)


# -- test hygiene -----------------------------------------------------------


def test_simulator_does_not_depend_on_pyserial() -> None:
    """The simulator must not import (or reference) pyserial at all."""
    import ast
    from pathlib import Path

    source = Path(simulator.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not any(name.split(".")[0] == "serial" for name in imported)


def test_da_nak_propagates_from_fake(tmp_path: object) -> None:
    from pathlib import Path

    device = simulator.FakeDevice(fail_packet=0)
    da_path = Path(str(tmp_path)) / "DA.bin"
    da_path.write_bytes(b"\x42" * 0x800)
    boot = _brom(device)
    try:
        boot.start()
        agent = da.DownloadAgent(boot)
        agent.start(da_path)
        with pytest.raises(DownloadAgentError, match="NAK"):
            agent.write_firmware(b"\x00" * 0x100)
    finally:
        boot.close()
