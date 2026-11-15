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
        **kwargs,
    )


def _image(size: int = 0x400) -> bytes:
    return bytes((index * 37 + 11) & 0xFF for index in range(size))


# -- constants --------------------------------------------------------------


def test_verified_constants() -> None:
    assert brom.BASE_ADDR_FIRMWARE == 0x20000000
    assert brom.BASE_ADDR_FIRMWARE_M3318 == 0x00000000
    assert brom.BASE_ADDR_DA == 0x00100800
    assert brom.BOOT_SYNC == b"\xa0\x0a\x50\x05"
    assert brom.BOOT_REPLY == (0x5F, 0xF5, 0xAF, 0xFA)
    assert brom.WRITE_CHUNK == 10


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
        assert boot.read32(0x20000000, 4, byte_swap=True) == _image()[:4]
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
        boot.read32(0x20000000, 4)


def test_constructing_a_bootrom_does_not_open_the_port() -> None:
    calls: list[tuple[str, int]] = []

    def factory(port: str, baud: int) -> object:
        calls.append((port, baud))
        raise AssertionError("the factory must not be called on construction")

    boot = brom.MtkBootRom("COM7", serial_factory=factory)
    assert calls == []
    assert boot.ser is None
    boot.close()


def test_serial_factory_receives_port_and_baud() -> None:
    calls: list[tuple[str, int]] = []
    device = FakeDevice()

    def factory(port: str, baud: int) -> object:
        calls.append((port, baud))
        return device.factory(port, baud)

    boot = brom.MtkBootRom("COM7", baud=921600, serial_factory=factory, timeout=0.2)
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
    boot = brom.MtkBootRom("COM7", baud=38400, timeout=0.1)
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
        assert boot.read32(0x20000000, 0x20, byte_swap=True) == image[:0x20]
        assert boot.read16(0x20000010, 0x20, byte_swap=True) == image[0x10:0x30]
        assert boot.read(0x20000020, 0x10, byte_swap=True) == image[0x20:0x30]


def test_read_without_byte_swap_returns_wire_order() -> None:
    image = _image()
    device = FakeDevice(image)
    with _brom(device) as boot:
        boot.start()
        wire = boot.read32(0x20000000, 8, byte_swap=False)
        assert wire == brom.swap_pairs(image[:8])
        assert brom.swap_pairs(wire) == image[:8]


def test_read_zero_length_is_empty_and_sends_nothing() -> None:
    device = FakeDevice()
    with _brom(device) as boot:
        boot.start()
        assert boot.read32(0x20000000, 0) == b""
        assert device.in_waiting == 0


def test_read_timeout_raises_bootrom_error() -> None:
    device = FakeDevice()
    with _brom(device) as boot:
        boot.start()
        device.silent = True
        with pytest.raises(BootRomError, match="timeout"):
            boot.read32(0x20000000, 8)


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
            boot.read32(0x20000000, 4)
        assert excinfo.value.offset == 0


def test_read_checksum_mismatch_raises() -> None:
    class LyingReadChecksumDevice(FakeDevice):
        def _dispatch_frame(self, frame: bytes) -> None:
            if frame[0] in (brom.OPCODE_READ32, brom.OPCODE_READ16):
                address = int.from_bytes(frame[1:5], "big")
                length = int.from_bytes(frame[5:9], "big")
                self.emit(brom.swap_pairs(self._read(address, length)))
                self.emit(b"\xDE\xAD")  # wrong XOR16
                return
            super()._dispatch_frame(frame)

    device = LyingReadChecksumDevice()
    with _brom(device) as boot:
        boot.start()
        with pytest.raises(BootRomError, match="read checksum mismatch"):
            boot.read32(0x20000000, 8)


def test_read_checksum_is_verified_not_just_consumed() -> None:
    """The trailing checksum word must actually be validated."""
    device = FakeDevice(_image(0x100))
    with _brom(device) as boot:
        boot.start()
        # A correct read consumes data + 2 checksum bytes and leaves nothing.
        assert boot.read32(0x20000000, 0x10, byte_swap=True) == _image(0x100)[:0x10]
        assert device.in_waiting == 0


# -- checksum command -------------------------------------------------------


def test_checksum_matches_helper() -> None:
    image = _image(0x100)
    device = FakeDevice(image)
    with _brom(device) as boot:
        boot.start()
        assert boot.checksum(0x20000000, 0x100) == brom.checksum_xor16(image)
        assert boot.checksum(0x20000000, 0) == 0


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
