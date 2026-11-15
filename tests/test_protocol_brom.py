"""Tests for :mod:`m241.protocol.brom` (BootROM framing, reads, writes, dump).

The fake device from :mod:`m241.protocol.simulator` stands in for the chip, so
these tests exercise the real host-side framing code with no hardware.
"""

from __future__ import annotations

import struct

import pytest

from m241.errors import BootRomError, FirmwareError
from m241.protocol import brom
from m241.protocol.simulator import FakeDevice


def _brom(device: FakeDevice, **kwargs: object) -> brom.MtkBootRom:
    return brom.MtkBootRom(  # type: ignore[arg-type]
        "FAKE",
        serial_factory=device.factory,
        timeout=0.2,
        sync_timeout=0.5,
        nmea_probe_seconds=0.0,
        **kwargs,
    )


def _image(size: int = 0x400) -> bytes:
    return bytes((index * 37 + 11) & 0xFF for index in range(size))


# -- constants --------------------------------------------------------------


def test_verified_constants() -> None:
    # Measured 2026-11-15 on a real V1.13 unit: the firmware vector table
    # (18 f0 9f e5) reads back from 0x00200000, while 0x20000000 returns
    # `42 20 02 02` (unmapped bus).
    assert brom.BASE_ADDR_FIRMWARE == 0x00200000
    assert brom.BASE_ADDR_FIRMWARE_MT333X == 0x20000000
    assert brom.BASE_ADDR_FIRMWARE_M3318 == 0x00000000
    assert brom.BASE_ADDR_DA == 0x00100800
    assert brom.BOOT_SYNC == b"\xa0\x0a\x50\x05"
    assert brom.BOOT_REPLY == (0x5F, 0xF5, 0xAF, 0xFA)
    assert brom.WRITE_CHUNK == 10
    # The BootROM switch command: no arguments, CRLF terminated.
    assert brom.PMTK180_COMMAND == b"$PMTK180*3B\r\n"
    assert brom.DEFAULT_NMEA_BAUD == 38400


# -- checksum / swap helpers ------------------------------------------------


def test_swap_pairs_swaps_be16_pairs() -> None:
    assert brom.swap_pairs(b"\x01\x02\x03\x04") == b"\x02\x01\x04\x03"
    assert brom.swap_pairs(b"\x01\x02\x03") == b"\x02\x01\x03"
    assert brom.swap_pairs(b"") == b""
    assert brom.swap_pairs(brom.swap_pairs(b"\xde\xad\xbe\xef")) == b"\xde\xad\xbe\xef"


def test_checksum_xor16() -> None:
    assert brom.checksum_xor16(b"") == 0
    assert brom.checksum_xor16(b"\x12\x34") == 0x1234
    assert brom.checksum_xor16(b"\x12\x34\x12\x34") == 0
    assert brom.checksum_xor16(b"\x01\x02\x03\x04") == 0x0102 ^ 0x0304
    # A trailing odd byte acts as the high byte of the final word.
    assert brom.checksum_xor16(b"\xab") == 0xAB00
    assert brom.checksum_xor16(b"\x01\x02\xff") == 0x0102 ^ 0xFF00


# -- lifecycle / sync -------------------------------------------------------


def test_start_performs_sync_handshake() -> None:
    device = FakeDevice(_image())
    with _brom(device) as boot:
        boot.start()
        assert boot.ser is not None
    assert boot.ser is None


def test_start_is_idempotent() -> None:
    device = FakeDevice(_image())
    boot = _brom(device)
    try:
        boot.start()
        boot.start()
        assert boot.read32(0x00200000, 4, byte_swap=True) == _image()[:4]
    finally:
        boot.close()


def test_start_raises_when_device_is_silent() -> None:
    device = FakeDevice()
    device.silent = True
    boot = _brom(device)
    try:
        with pytest.raises(BootRomError, match="sync failed|timeout"):
            boot.start()
    finally:
        boot.close()


def test_start_times_out_on_wrong_sync_reply() -> None:
    class MutatingDevice(FakeDevice):
        def write(self, data: bytes) -> None:  # noqa: D401 - test double
            super().write(data)
            if self._output:
                self._output[0] = 0x00  # break the reply

    boot = _brom(MutatingDevice())
    try:
        with pytest.raises(BootRomError, match="sync"):
            boot.start()
    finally:
        boot.close()


def test_context_manager_closes_port() -> None:
    device = FakeDevice()
    with _brom(device) as boot:
        boot.start()
        serial_port = boot.ser
    assert serial_port is not None
    assert serial_port.is_open is False


def test_using_a_closed_bootrom_is_an_error() -> None:
    device = FakeDevice()
    boot = _brom(device)
    boot.close()
    with pytest.raises(BootRomError, match="closed"):
        boot.read32(0x00200000, 4)


def test_constructing_a_bootrom_does_not_open_the_port() -> None:
    calls: list[tuple[str, int]] = []

    def factory(port: str, baud: int) -> object:
        calls.append((port, baud))
        raise AssertionError("the factory must not be called on construction")

    boot = brom.MtkBootRom("COM7", serial_factory=factory, nmea_probe_seconds=0.0)
    assert calls == []
    assert boot.ser is None
    boot.close()


def test_serial_factory_receives_port_and_baud() -> None:
    calls: list[tuple[str, int]] = []
    device = FakeDevice()

    def factory(port: str, baud: int) -> object:
        calls.append((port, baud))
        return device.factory(port, baud)

    boot = brom.MtkBootRom(
        "COM7", baud=921600, serial_factory=factory, timeout=0.2, nmea_probe_seconds=0.0
    )
    # The port is opened lazily: constructing the object touches no hardware.
    assert calls == []
    try:
        boot.start()
    finally:
        boot.close()
    assert calls == [("COM7", 921600)]


def test_default_serial_factory_is_open_serial(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, int]] = []
    device = FakeDevice()

    def fake_open_serial(port: str, baud: int = 115200, timeout: float = 1.0) -> object:
        calls.append((port, baud))
        return device.factory(port, baud)

    monkeypatch.setattr(brom.ports, "open_serial", fake_open_serial)
    boot = brom.MtkBootRom("COM7", baud=38400, timeout=0.1, nmea_probe_seconds=0.0)
    try:
        boot.start()
    finally:
        boot.close()
    assert calls == [("COM7", 38400)]


# -- reads ------------------------------------------------------------------


def test_read32_and_read16_roundtrip_with_byte_swap() -> None:
    image = _image()
    device = FakeDevice(image)
    with _brom(device) as boot:
        boot.start()
        assert boot.read32(0x00200000, 0x20, byte_swap=True) == image[:0x20]
        assert boot.read16(0x20000010, 0x20, byte_swap=True) == image[0x10:0x30]
        assert boot.read(0x20000020, 0x10, byte_swap=True) == image[0x20:0x30]


def test_read_without_byte_swap_returns_wire_order() -> None:
    image = _image()
    device = FakeDevice(image)
    with _brom(device) as boot:
        boot.start()
        # READ32 sends each 32-bit word big-endian, so memory order is the
        # byte-reversed form of the wire.
        wire = boot.read32(0x00200000, 8, byte_swap=False)
        assert wire == brom.reverse_words32(image[:8])
        assert brom.reverse_words32(wire) == image[:8]


def test_read_zero_length_is_empty_and_sends_nothing() -> None:
    device = FakeDevice()
    with _brom(device) as boot:
        boot.start()
        assert boot.read32(0x00200000, 0) == b""
        assert device.in_waiting == 0


def test_read_timeout_raises_bootrom_error() -> None:
    device = FakeDevice()
    with _brom(device) as boot:
        boot.start()
        device.silent = True
        with pytest.raises(BootRomError, match="timeout"):
            boot.read32(0x00200000, 8)


def test_read_echo_mismatch_is_reported_with_offset() -> None:
    device = FakeDevice()
    with _brom(device) as boot:
        boot.start()
        # A second fake device in silent mode simply never answers; instead
        # corrupt the reply stream by injecting a wrong echo byte.
        boot.ser.write(b"")  # no-op, keeps type checkers happy
        device._output.clear()  # noqa: SLF001 - test double
        device.emit(b"\x00")
        with pytest.raises(BootRomError) as excinfo:
            boot.read32(0x00200000, 4)
        assert excinfo.value.offset == 0


def test_read_has_no_trailing_checksum_word() -> None:
    """``0xAF``/``0xA2`` return data only; the checksum is a separate command.

    Consuming a trailer desynchronises the frame on real hardware, which is
    exactly what the earlier implementation did.  A correct read must leave the
    device's output buffer empty.
    """
    device = FakeDevice(_image(0x100))
    with _brom(device) as boot:
        boot.start()
        assert boot.read32(0x00200000, 0x10, byte_swap=True) == _image(0x100)[:0x10]
        assert device.in_waiting == 0
        assert boot.read16(0x00200010, 0x10, byte_swap=True) == _image(0x100)[0x10:0x20]
        assert device.in_waiting == 0


# -- checksum command -------------------------------------------------------


def test_checksum_matches_helper() -> None:
    image = _image(0x100)
    device = FakeDevice(image)
    with _brom(device) as boot:
        boot.start()
        assert boot.checksum(0x00200000, 0x100) == brom.checksum_xor16(image)
        assert boot.checksum(0x00200000, 0) == 0


# -- write16 ----------------------------------------------------------------


def test_write16_stores_little_endian_with_byte_swap() -> None:
    image = _image()
    device = FakeDevice(image)
    payload = bytes(range(64))
    with _brom(device) as boot:
        boot.start()
        boot.write16(0x20000100, payload, byte_swap=True)
    assert bytes(device.memory[0x100:0x140]) == payload


def test_write16_without_byte_swap_stores_wire_order() -> None:
    device = FakeDevice(_image())
    payload = bytes(range(32))
    with _brom(device) as boot:
        boot.start()
        boot.write16(0x20000100, payload, byte_swap=False)
    # The chip always stores little-endian, i.e. it unswaps whatever arrives on
    # the wire; with byte_swap=False the host sends payload verbatim.
    assert bytes(device.memory[0x100:0x120]) == brom.swap_pairs(payload)


def test_write16_verifies_checksum_by_default() -> None:
    class LyingChecksumDevice(FakeDevice):
        def _dispatch_frame(self, frame: bytes) -> None:
            if frame[0] == brom.OPCODE_CHECKSUM:
                address = int.from_bytes(frame[1:5], "big")
                length = int.from_bytes(frame[5:9], "big")
                self._read(address, length)
                self.emit((0xBEEF).to_bytes(2, "big"))
                return
            super()._dispatch_frame(frame)

    device = LyingChecksumDevice()
    with _brom(device) as boot:
        boot.start()
        with pytest.raises(BootRomError, match="checksum mismatch"):
            boot.write16(0x20000100, b"\x01\x02\x03\x04")


def test_write16_skip_cksum_does_not_ask_for_checksum() -> None:
    class NoChecksumDevice(FakeDevice):
        checksums = 0

        def _dispatch_frame(self, frame: bytes) -> None:
            if frame[0] == brom.OPCODE_CHECKSUM:
                NoChecksumDevice.checksums += 1
            super()._dispatch_frame(frame)

    NoChecksumDevice.checksums = 0
    device = NoChecksumDevice()
    with _brom(device) as boot:
        boot.start()
        boot.write16(0x20000100, b"\x01\x02\x03\x04", skip_cksum=True)
    assert NoChecksumDevice.checksums == 0


def test_write16_echo_mismatch_raises() -> None:
    device = FakeDevice(corrupt_echo=True)
    with _brom(device) as boot:
        boot.start()
        with pytest.raises(BootRomError, match="echo mismatch"):
            boot.write16(0x20000100, bytes(range(32)), skip_cksum=True)


def test_write16_progress_callback() -> None:
    device = FakeDevice()
    seen: list[tuple[int, int]] = []
    with _brom(device) as boot:
        boot.start()
        boot.write16(
            0x20000100,
            bytes(range(50)),
            byte_swap=True,
            progress_cb=lambda done, total: seen.append((done, total)),
        )
    assert seen[0] == (10, 50)
    assert seen[-1] == (50, 50)
    # Chunks are 10 bytes on the wire, so five callbacks for 50 bytes.
    assert len(seen) == 5
    assert all(total == 50 for _, total in seen)


def test_write16_empty_is_a_noop() -> None:
    device = FakeDevice()
    with _brom(device) as boot:
        boot.start()
        boot.write16(0x20000100, b"")
    assert device.in_waiting == 0


# -- jump -------------------------------------------------------------------


def test_jump_to_da_address_enters_da_mode() -> None:
    device = FakeDevice()
    device.ram[0] = 1
    with _brom(device) as boot:
        boot.start()
        boot.jump(brom.BASE_ADDR_DA)
        assert device._state == "da"  # noqa: SLF001 - test double


# -- dump_firmware ----------------------------------------------------------


def test_dump_firmware_uses_header_length() -> None:
    image = bytearray(_image(0x2000))
    header_len = 0x800
    image[0xF4:0xF8] = struct.pack("<I", header_len)
    device = FakeDevice(bytes(image))
    with _brom(device) as boot:
        boot.start()
        dumped = brom.dump_firmware(boot)
    assert dumped == bytes(image[:header_len])


def test_dump_firmware_accepts_explicit_length_and_progress() -> None:
    image = _image(0x100)
    seen: list[tuple[int, int]] = []
    device = FakeDevice(image)
    with _brom(device) as boot:
        boot.start()
        dumped = brom.dump_firmware(
            boot, length=0x100, progress_cb=lambda done, total: seen.append((done, total))
        )
    assert dumped == image
    assert seen == [(0x100, 0x100)]


def test_dump_firmware_rejects_implausible_size() -> None:
    image = bytearray(b"\x00" * 0x200)
    image[0xF4:0xF8] = b"\xff\xff\xff\xff"
    device = FakeDevice(bytes(image))
    with _brom(device) as boot:
        boot.start()
        with pytest.raises(FirmwareError, match="implausible"):
            brom.dump_firmware(boot)


def test_dump_firmware_zero_length_returns_empty() -> None:
    device = FakeDevice(_image())
    with _brom(device) as boot:
        boot.start()
        assert brom.dump_firmware(boot, length=0) == b""


def test_dump_firmware_roundtrips_an_image() -> None:
    image = _image(0x1000)
    device = FakeDevice(image)
    with _brom(device) as boot:
        boot.start()
        assert brom.dump_firmware(boot, length=len(image), byte_swap=True) == image


# -- the NMEA-first safety check --------------------------------------------


class _StreamingPort:
    """A port that keeps emitting NMEA sentences, like a running firmware."""

    def __init__(self) -> None:
        self.baudrate = 38400
        self.timeout = 1.0
        self.dtr = False
        self.rts = False
        self.written = bytearray()

    def read(self, size: int = 1) -> bytes:
        return b"$GPRMC,120000.000,A,3550.00,N,13946.00,E,0.0,0.0,151126,,,A*70\r\n"

    def write(self, data: bytes) -> int:
        self.written += data
        return len(data)

    def reset_input_buffer(self) -> None:
        return None

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None


class _SilentPort(_StreamingPort):
    """A port with nothing on it, like a logger already in BootROM."""

    def read(self, size: int = 1) -> bytes:
        return b""


class _GarbagePort(_StreamingPort):
    """A port emitting bytes that are neither NMEA nor a clean silence."""

    def read(self, size: int = 1) -> bytes:
        return b"\x00\xff\x13\x37"


def _boot_for(port: object) -> brom.MtkBootRom:
    return brom.MtkBootRom(  # type: ignore[arg-type]
        "FAKE",
        serial_factory=lambda _port, _baud: port,
        timeout=0.05,
        sync_timeout=0.05,
        nmea_probe_seconds=0.05,
    )


def test_probe_nmea_detects_a_streaming_firmware() -> None:
    boot = _boot_for(_StreamingPort())
    with boot:
        assert boot.probe_nmea(boot._ensure_open(), seconds=0.05) is True


def test_probe_nmea_reports_silence() -> None:
    boot = _boot_for(_SilentPort())
    with boot:
        assert boot.probe_nmea(boot._ensure_open(), seconds=0.05) is False


def test_probe_nmea_refuses_when_it_cannot_tell() -> None:
    boot = _boot_for(_GarbagePort())
    with boot:
        assert boot.probe_nmea(boot._ensure_open(), seconds=0.05) is None


def test_start_refuses_to_pulse_a_streaming_firmware() -> None:
    """Pulsing DTR/RTS at a running firmware leaves the unit unresponsive."""
    port = _StreamingPort()
    boot = _boot_for(port)
    with boot, pytest.raises(BootRomError, match="streaming NMEA"):
        boot.start()
    assert port.dtr is False and port.rts is False, "the reset lines were pulsed"


def test_start_refuses_when_the_state_is_unknown() -> None:
    boot = _boot_for(_GarbagePort())
    with boot, pytest.raises(BootRomError, match="cannot tell"):
        boot.start()


def test_start_proceeds_on_a_silent_device() -> None:
    """The normal dump path: the firmware is silent, so the reset route is safe."""
    image = _image(0x400)
    device = FakeDevice(image)
    with _brom(device) as boot:
        boot.start()
        assert brom.dump_firmware(boot, length=len(image), byte_swap=True) == image
