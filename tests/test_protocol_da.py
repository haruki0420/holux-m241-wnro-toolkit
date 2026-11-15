"""Tests for :mod:`m241.protocol.da` (Download Agent handshake and flashing).

Everything runs against :class:`m241.protocol.simulator.FakeDevice`, which
implements the DA side of the wire protocol, so the real host code is exercised
with no hardware attached.
"""

from __future__ import annotations

import struct

import pytest

from m241.errors import DownloadAgentError, FirmwareError
from m241.protocol import brom, da
from m241.protocol.simulator import FakeDevice

DA_BLOB = bytes((index * 31 + 7) & 0xFF for index in range(0x1000))


def _boot(device: FakeDevice) -> brom.MtkBootRom:
    return brom.MtkBootRom("FAKE", serial_factory=device.factory, timeout=0.2, sync_timeout=0.5)


def _started_da(device: FakeDevice, tmp_path: object) -> tuple[brom.MtkBootRom, da.DownloadAgent]:
    from pathlib import Path

    da_path = Path(str(tmp_path)) / "DA_v3.8.4.bin"
    da_path.write_bytes(DA_BLOB)
    boot = _boot(device)
    boot.start()
    agent = da.DownloadAgent(boot)
    agent.start(da_path)
    return boot, agent


def _image(size: int = 0x1000) -> bytes:
    return bytes((index * 37 + 11) & 0xFF for index in range(size))


# -- constants --------------------------------------------------------------


def test_contract_constants() -> None:
    assert da.DownloadAgent.OPCODE_SET_BAUD == 0xD2
    assert da.DownloadAgent.OPCODE_MEM_CMD == 0xD3
    assert da.DownloadAgent.OPCODE_WRITE == 0xD5
    assert da.DownloadAgent.ACK == 0x5A
    assert da.DownloadAgent.NAK == 0xA5
    assert da.DownloadAgent.CONT == 0x69
    assert da.DownloadAgent.SYNC == 0xC0
    assert da.DownloadAgent.WRITE_PACKET_LEN == 0x100
    assert da.DownloadAgent.EXPECTED_DA_VERSION == 0x0308
    assert da.DA_INFO_SIZE == 20
    assert da.DA_INFO_FIELDS == (
        "sync_char",
        "da_version",
        "flash_device_id",
        "flash_size",
        "flash_manufacturer_code",
        "flash_device_code",
        "flash_ext_device_code1",
        "flash_ext_device_code2",
        "ext_sram_size",
    )


def test_info_report_format_is_20_bytes_big_endian() -> None:
    assert struct.calcsize(">BHBLHHHHL") == 20 == da.DA_INFO_SIZE
    report = struct.pack(">BHBLHHHHL", 0xC0, 0x0308, 0xBA, 2048, 0xC2, 0x22BA, 0x22, 0xBA, 0)
    assert len(report) == 20
    assert report[0] == 0xC0


# -- start / info -----------------------------------------------------------


def test_start_loads_da_and_parses_info(tmp_path: object) -> None:
    device = FakeDevice()
    boot, agent = _started_da(device, tmp_path)
    try:
        info = agent.info
        assert info["sync_char"] == da.SYNC
        assert info["da_version"] == da.EXPECTED_DA_VERSION
        assert info["flash_device_id"] == 0xBA
        # Contract: info["flash_size"] is in bytes (the DA report carries KiB).
        assert info["flash_size"] == 2 * 1024 * 1024
        assert agent.flash_size_bytes == 2 * 1024 * 1024
        assert info["flash_manufacturer_code"] == 0x00C2
        assert info["flash_device_code"] == 0x22BA
        # The DA blob itself was written into the fake RAM window (the RAM
        # window starts at 0x100000, so 0x100800 is offset 0x800), byte-swapped
        # on the wire and restored on the chip.
        ram_offset = brom.BASE_ADDR_DA - 0x100000
        assert bytes(device.ram[ram_offset : ram_offset + len(DA_BLOB)]) == DA_BLOB
    finally:
        boot.close()


def test_start_returns_the_info_dict(tmp_path: object) -> None:
    device = FakeDevice()
    boot, agent = _started_da(device, tmp_path)
    try:
        assert set(agent.info) == set(da.DA_INFO_FIELDS)
        # A defensive copy: mutating it must not change the agent's state.
        agent.info["da_version"] = 0
        assert agent.info["da_version"] == da.EXPECTED_DA_VERSION
    finally:
        boot.close()


def test_info_is_empty_before_start() -> None:
    device = FakeDevice()
    boot = _boot(device)
    try:
        assert da.DownloadAgent(boot).info == {}
    finally:
        boot.close()


def test_start_rejects_wrong_da_version(tmp_path: object) -> None:
    from pathlib import Path

    device = FakeDevice(da_version=0x0307)
    da_path = Path(str(tmp_path)) / "DA.bin"
    da_path.write_bytes(DA_BLOB)
    boot = _boot(device)
    try:
        boot.start()
        with pytest.raises(DownloadAgentError, match="version"):
            da.DownloadAgent(boot).start(da_path)
    finally:
        boot.close()


def test_start_rejects_missing_flash_device(tmp_path: object) -> None:
    from pathlib import Path

    device = FakeDevice(flash_device_id=0xFF)
    da_path = Path(str(tmp_path)) / "DA.bin"
    da_path.write_bytes(DA_BLOB)
    boot = _boot(device)
    try:
        boot.start()
        with pytest.raises(DownloadAgentError, match="no flash device"):
            da.DownloadAgent(boot).start(da_path)
    finally:
        boot.close()


def test_start_missing_da_file_raises(tmp_path: object) -> None:
    from pathlib import Path

    device = FakeDevice()
    boot = _boot(device)
    try:
        boot.start()
        with pytest.raises(DownloadAgentError, match="could not read"):
            da.DownloadAgent(boot).start(Path(str(tmp_path)) / "nope.bin")
    finally:
        boot.close()


def test_start_rejects_tiny_da_blob(tmp_path: object) -> None:
    from pathlib import Path

    da_path = Path(str(tmp_path)) / "tiny.bin"
    da_path.write_bytes(b"\x01\x02\x03")
    device = FakeDevice()
    boot = _boot(device)
    try:
        boot.start()
        with pytest.raises(DownloadAgentError, match="not a DA blob"):
            da.DownloadAgent(boot).start(da_path)
    finally:
        boot.close()


def test_start_trims_trailing_nul_padding(tmp_path: object) -> None:
    from pathlib import Path

    blob = DA_BLOB[:100] + b"\x00" * 300
    da_path = Path(str(tmp_path)) / "DA.bin"
    da_path.write_bytes(blob)
    device = FakeDevice()
    boot = _boot(device)
    try:
        boot.start()
        da.DownloadAgent(boot).start(da_path)
        ram_offset = brom.BASE_ADDR_DA - 0x100000
        loaded = bytes(device.ram[ram_offset : ram_offset + 100])
        assert loaded == DA_BLOB[:100]
        # The trimmed padding is not written at all.
        assert bytes(device.ram[ram_offset + 100 : ram_offset + 400]) == b"\x00" * 300
    finally:
        boot.close()


def test_start_requires_sync_byte(tmp_path: object) -> None:
    from pathlib import Path

    class NoSyncDevice(FakeDevice):
        def _jump(self, address: int) -> None:
            super()._jump(address)
            self._output.clear()  # drop the whole info report
            self.emit(b"\x00" * 32)

    device = NoSyncDevice()
    da_path = Path(str(tmp_path)) / "DA.bin"
    da_path.write_bytes(DA_BLOB)
    boot = _boot(device)
    try:
        boot.start()
        with pytest.raises(DownloadAgentError, match="sync byte"):
            da.DownloadAgent(boot).start(da_path)
    finally:
        boot.close()


# -- write_firmware ---------------------------------------------------------


def test_write_firmware_to_flash_and_read_back(tmp_path: object) -> None:
    base_image = bytearray(_image(0x1000))
    base_image[0xF4:0xF8] = struct.pack("<I", 0x1000)
    device = FakeDevice(bytes(base_image))
    boot, agent = _started_da(device, tmp_path)
    try:
        patched = bytes(base_image[:0x200] + b"\xAA" * 0x100 + base_image[0x300:])
        agent.write_firmware(patched, start=0)
        assert bytes(device.memory[: len(patched)]) == patched
        agent.restart()
        boot.start()
        assert brom.dump_firmware(boot, length=len(patched)) == patched
    finally:
        boot.close()


def test_write_firmware_with_nonzero_start(tmp_path: object) -> None:
    device = FakeDevice()
    boot, agent = _started_da(device, tmp_path)
    try:
        payload = b"\x5A" * 300
        agent.write_firmware(payload, start=0x400)
        assert bytes(device.memory[0x400:0x400 + len(payload)]) == payload
        assert bytes(device.memory[:0x400]) == b"\x00" * 0x400
    finally:
        boot.close()


def test_write_firmware_handles_exact_packet_multiple(tmp_path: object) -> None:
    device = FakeDevice()
    boot, agent = _started_da(device, tmp_path)
    try:
        payload = bytes(range(256)) * 4  # exactly 4 x 0x100
        agent.write_firmware(payload)
        assert bytes(device.memory[: len(payload)]) == payload
    finally:
        boot.close()


def test_write_firmware_handles_short_final_packet(tmp_path: object) -> None:
    device = FakeDevice()
    boot, agent = _started_da(device, tmp_path)
    try:
        # Real firmware images are always even length; a final short packet is
        # still padded to an even byte count by every writer in the wild.
        payload = bytes([0x11]) * 0x202  # 2 full packets + 2 bytes
        agent.write_firmware(payload)
        assert bytes(device.memory[: len(payload)]) == payload
    finally:
        boot.close()


def test_write_firmware_progress_callback(tmp_path: object) -> None:
    device = FakeDevice()
    boot, agent = _started_da(device, tmp_path)
    try:
        seen: list[tuple[int, int]] = []
        agent.set_progress(lambda done, total: seen.append((done, total)))
        payload = b"\x00" * 0x300
        agent.write_firmware(payload)
        assert seen == [(0x100, 0x300), (0x200, 0x300), (0x300, 0x300)]
    finally:
        boot.close()


def test_write_firmware_rejects_empty_image(tmp_path: object) -> None:
    device = FakeDevice()
    boot, agent = _started_da(device, tmp_path)
    try:
        with pytest.raises(FirmwareError, match="empty"):
            agent.write_firmware(b"")
    finally:
        boot.close()


def test_write_firmware_requires_start(tmp_path: object) -> None:
    from pathlib import Path

    assert Path(str(tmp_path))
    device = FakeDevice()
    boot = _boot(device)
    try:
        boot.start()
        with pytest.raises(DownloadAgentError, match="not loaded"):
            da.DownloadAgent(boot).write_firmware(b"\x00" * 16)
    finally:
        boot.close()


def test_write_firmware_nak_on_packet_reports_address(tmp_path: object) -> None:
    device = FakeDevice(fail_packet=1)
    boot = _boot(device)
    from pathlib import Path

    da_path = Path(str(tmp_path)) / "DA.bin"
    da_path.write_bytes(DA_BLOB)
    try:
        boot.start()
        agent = da.DownloadAgent(boot)
        agent.start(da_path)
        with pytest.raises(DownloadAgentError) as excinfo:
            agent.write_firmware(b"\x00" * 0x300)
        message = str(excinfo.value)
        assert "0x100" in message
        assert "NAK" in message
    finally:
        boot.close()


# -- baud rate / restart ----------------------------------------------------


def test_set_baud_rate_is_acknowledged(tmp_path: object) -> None:
    device = FakeDevice()
    boot, agent = _started_da(device, tmp_path)
    try:
        agent.set_baud_rate(921600)  # does not raise
    finally:
        boot.close()


def test_set_baud_rate_nak_raises(tmp_path: object) -> None:
    device = FakeDevice(nak_set_baud=True)
    boot, agent = _started_da(device, tmp_path)
    try:
        with pytest.raises(DownloadAgentError, match="NAK"):
            agent.set_baud_rate(921600)
    finally:
        boot.close()


def test_restart_returns_to_bootrom(tmp_path: object) -> None:
    device = FakeDevice()
    boot, agent = _started_da(device, tmp_path)
    try:
        agent.restart()
        assert device._state == "bootrom"  # noqa: SLF001 - test double
        # The device is syncable again, so a fresh BootROM session works.
        boot.start()
        assert len(boot.read32(0x20000000, 16)) == 16
    finally:
        boot.close()

# -- hardware-shaped regressions -------------------------------------------


def test_write_firmware_handles_the_real_image_length(tmp_path: object) -> None:
    """The shipping image is 406,620 bytes: 1,588 full packets + a 92-byte tail.

    This is the exact shape that was written to a real unit, so it is pinned
    here: a short final packet must be accepted by the DA.
    """
    device = FakeDevice(flash_size=0x80000)
    boot, agent = _started_da(device, tmp_path)
    try:
        image = _image(406620)
        agent.write_firmware(image)
        assert bytes(device.memory[: len(image)]) == image
    finally:
        boot.close()


def test_mem_cmd_status_byte_is_consumed(tmp_path: object) -> None:
    """0xD3 is answered by ACK *and* a status byte.

    Leaving the status byte in the buffer shifts every later frame by one byte,
    which is what made the 0xD5 step time out on hardware.  A successful write
    followed by a read proves the stream stayed aligned.
    """
    device = FakeDevice()
    boot, agent = _started_da(device, tmp_path)
    try:
        image = _image(0x300)
        agent.write_firmware(image)
        agent.restart()
        boot.start()
        assert brom.dump_firmware(boot, length=len(image)) == image
    finally:
        boot.close()


def test_mem_cmd_reports_a_bad_status_byte(tmp_path: object) -> None:
    device = FakeDevice(mem_cmd_status=0x05)
    boot, agent = _started_da(device, tmp_path)
    try:
        with pytest.raises(DownloadAgentError, match="status 0x05"):
            agent.write_firmware(_image(0x200))
    finally:
        boot.close()
