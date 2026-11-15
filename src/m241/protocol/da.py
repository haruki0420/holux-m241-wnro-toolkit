"""MTK Download Agent (DA) driver for the HDX/Holux M-241.

Protocol (verified against the vendor utility and the DA v3.8.4 blob):

1. The BootROM writes the DA blob into RAM at ``0x00100800`` with the 16-bit
   write command, byte-swapped, without a checksum round trip.
2. The chip is told to jump there.
3. The DA prints a 20 byte report starting with a ``0xC0`` sync byte::

       >B  sync_char
       >H  da_version
       >B  flash_device_id
       >L  flash_size (bytes)
       >H  flash_manufacturer_code
       >H  flash_device_code
       >H  flash_ext_device_code1
       >H  flash_ext_device_code2
       >L  ext_sram_size (bytes)

4. Firmware writes use ``0xD3`` (mem-cmd: address range), then ``0xD5``
   (write), then 0x100 byte packets each followed by a big-endian sum-of-bytes
   checksum and answered with ``0x69`` (continue), and finally two ``0x5A``
   ACKs.  ``0xD9`` reboots and ``0xD2`` changes the baud rate.
"""

from __future__ import annotations

import struct
import time
from pathlib import Path
from typing import Any

from m241.errors import DownloadAgentError, FirmwareError

from .brom import BASE_ADDR_DA, MtkBootRom

OPCODE_SET_BAUD = 0xD2
OPCODE_MEM_CMD = 0xD3
OPCODE_WRITE = 0xD5
OPCODE_RESTART = 0xD9

ACK = 0x5A
NAK = 0xA5
CONT = 0x69
SYNC = 0xC0

WRITE_PACKET_LEN = 0x100
EXPECTED_DA_VERSION = 0x0308

#: The 20 byte DA info report, packed big-endian.
DA_INFO_FORMAT = ">BHBLHHHHL"
DA_INFO_SIZE = struct.calcsize(DA_INFO_FORMAT)
DA_INFO_FIELDS = (
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
#: Minimum DA blob size that can plausibly be a real download agent.
_MIN_DA_SIZE = 0x100

ProgressCallback = Any


def _sum16(data: bytes) -> int:
    """Sum of bytes truncated to 16 bits (the DA's packet checksum)."""
    return sum(data) & 0xFFFF


class DownloadAgent:
    """Load and drive the MTK Download Agent on an already-synced BootROM."""

    OPCODE_SET_BAUD = OPCODE_SET_BAUD
    OPCODE_MEM_CMD = OPCODE_MEM_CMD
    OPCODE_WRITE = OPCODE_WRITE
    ACK = ACK
    NAK = NAK
    CONT = CONT
    SYNC = SYNC
    WRITE_PACKET_LEN = WRITE_PACKET_LEN
    EXPECTED_DA_VERSION = EXPECTED_DA_VERSION

    def __init__(self, brom: MtkBootRom) -> None:
        self.brom = brom
        self._info: dict[str, int] = {}
        self._progress_cb: ProgressCallback | None = None
        self._loaded = False

    # -- helpers -----------------------------------------------------------

    @property
    def info(self) -> dict[str, int]:
        """The parsed DA info report (empty before :meth:`start`)."""
        return dict(self._info)

    def set_progress(self, cb: ProgressCallback | None) -> None:
        """Install ``cb(done, total)``, called after every written packet."""
        self._progress_cb = cb

    @property
    def flash_size_bytes(self) -> int:
        """Flash size in bytes (``info["flash_size"]`` already holds bytes)."""
        return int(self._info.get("flash_size", 0))

    def _expect(self, expected: int, what: str) -> int:
        """Read one reply byte and require it to be ``expected``."""
        reply = self.brom.read_exact(1, what=what)[0]
        if reply == NAK:
            raise DownloadAgentError(f"the download agent rejected {what} (NAK 0xA5)")
        if reply != expected:
            raise DownloadAgentError(
                f"unexpected reply for {what}: expected 0x{expected:02X}, "
                f"got 0x{reply:02X}"
            )
        return reply

    def _await_ack(self, what: str) -> None:
        self._expect(ACK, what)

    def _require_serial(self) -> Any:
        serial_port = self.brom.ser
        if serial_port is None:
            raise DownloadAgentError(
                "the BootROM serial port is not open; call MtkBootRom.start() first"
            )
        return serial_port

    def _send(self, payload: bytes) -> None:
        self._require_serial().write(payload)

    @staticmethod
    def _parse_info(report: bytes) -> dict[str, int]:
        if len(report) != DA_INFO_SIZE:  # pragma: no cover - guarded by caller
            raise DownloadAgentError(
                f"short download-agent report: {len(report)} of {DA_INFO_SIZE} bytes"
            )
        values = struct.unpack(DA_INFO_FORMAT, report)
        return dict(zip(DA_INFO_FIELDS, (int(value) for value in values), strict=True))

    def _wait_for_sync(self) -> int:
        """Consume bytes until the ``0xC0`` DA sync byte, returning bytes skipped."""
        serial_port = self._require_serial()
        deadline = time.monotonic() + self.brom.timeout
        skipped = 0
        last = -1
        while time.monotonic() < deadline:
            raw = serial_port.read(1)
            if not raw:
                time.sleep(0.002)
                continue
            last = raw[0]
            if last == SYNC:
                return skipped
            skipped += 1
            if skipped > 0x4000:
                raise DownloadAgentError(
                    f"no 0x{SYNC:02X} sync byte from the download agent after "
                    f"{skipped} bytes (last byte 0x{last:02X})"
                )
        raise DownloadAgentError(
            f"timeout waiting for the download-agent sync byte 0x{SYNC:02X} "
            f"(received {skipped} other bytes, last 0x{last:02X})"
        )

    # -- public API --------------------------------------------------------

    def start(self, da_path: str | Path) -> dict[str, int]:
        """Load the DA blob, jump to it and return the parsed info report."""
        path = Path(da_path)
        try:
            blob = path.read_bytes()
        except OSError as exc:
            raise DownloadAgentError(f"could not read download agent {path}: {exc}") from exc
        if len(blob) < _MIN_DA_SIZE:
            raise DownloadAgentError(
                f"download agent {path} is only {len(blob)} bytes; that is not a DA blob"
            )
        blob = blob.rstrip(b"\x00")
        if len(blob) % 4:
            blob += b"\x00" * (4 - len(blob) % 4)
        try:
            self.brom.write16(
                BASE_ADDR_DA, blob, byte_swap=True, skip_cksum=True
            )
            self.brom.jump(BASE_ADDR_DA)
        except Exception as exc:
            if isinstance(exc, DownloadAgentError):
                raise
            raise DownloadAgentError(
                f"could not upload the download agent to 0x{BASE_ADDR_DA:08X}: {exc}"
            ) from exc
        self._loaded = True
        self._info = self._read_info()
        if self._info["sync_char"] != SYNC:  # pragma: no cover - _read_info enforces
            raise DownloadAgentError(
                f"bad download-agent sync byte 0x{self._info['sync_char']:02X}"
            )
        if self._info["da_version"] != EXPECTED_DA_VERSION:
            raise DownloadAgentError(
                "unsupported download agent version "
                f"0x{self._info['da_version']:04X}; expected "
                f"0x{EXPECTED_DA_VERSION:04X}"
            )
        if self._info["flash_device_id"] == 0xFF:
            raise DownloadAgentError(
                "download agent reports no flash device (id 0xFF); "
                "the logger may not be connected properly"
            )
        return dict(self._info)

    def _read_info(self) -> dict[str, int]:
        skipped = self._wait_for_sync()
        report = bytes([SYNC]) + self.brom.read_exact(
            DA_INFO_SIZE - 1, what="download-agent info report"
        )
        if len(report) != DA_INFO_SIZE:  # pragma: no cover - read_exact enforces
            raise DownloadAgentError(
                f"truncated download-agent info report after {len(report)} bytes"
                f" (skipped {skipped} bytes before the sync byte)",
                offset=skipped,
            )
        return self._parse_info(report)

    def write_firmware(self, fw: bytes, *, start: int = 0) -> None:
        """Write ``fw`` to the DA mapped flash, starting at ``start``."""
        data = bytes(fw)
        if not data:
            raise FirmwareError("refusing to write an empty firmware image", offset=start)
        if not self._loaded:
            raise DownloadAgentError(
                "download agent is not loaded; call start() before write_firmware()"
            )
        end = start + len(data) - 1
        try:
            self._send(struct.pack(">BBLL", OPCODE_MEM_CMD, 1, start, end))
            self._await_ack("the memory-range command (0xD3)")
            self._send(struct.pack(">BL", OPCODE_WRITE, WRITE_PACKET_LEN))
            self._await_ack("the write command (0xD5), first acknowledgement")
            self._await_ack("the write command (0xD5), second acknowledgement")
        except DownloadAgentError:
            raise
        except Exception as exc:
            raise DownloadAgentError(f"download agent refused the write setup: {exc}") from exc

        total = len(data)
        for offset in range(0, total, WRITE_PACKET_LEN):
            packet = data[offset : offset + WRITE_PACKET_LEN]
            self._send(packet)
            self._send(struct.pack(">H", _sum16(packet)))
            try:
                self._expect(CONT, f"packet {offset // WRITE_PACKET_LEN} (0x{start + offset:08X})")
            except DownloadAgentError as exc:
                raise DownloadAgentError(
                    f"download agent rejected the packet at 0x{start + offset:08X}: {exc}",
                    offset=offset,
                ) from exc
            if self._progress_cb is not None:
                self._progress_cb(min(offset + WRITE_PACKET_LEN, total), total)

        self._await_ack("the final write acknowledgement, first")
        self._await_ack("the final write acknowledgement, second")

    def restart(self) -> None:
        """Send the DA restart command (0xD9)."""
        self._send(bytes([OPCODE_RESTART]))

    def set_baud_rate(self, baudrate: int) -> None:
        """Ask the DA to switch its serial baud rate (0xD2 command)."""
        self._send(struct.pack(">BL", OPCODE_SET_BAUD, baudrate))
        self._await_ack(f"the baud-rate command ({baudrate} baud)")


__all__ = [
    "ACK",
    "CONT",
    "DA_INFO_FIELDS",
    "DA_INFO_FORMAT",
    "DA_INFO_SIZE",
    "EXPECTED_DA_VERSION",
    "NAK",
    "OPCODE_MEM_CMD",
    "OPCODE_RESTART",
    "OPCODE_SET_BAUD",
    "OPCODE_WRITE",
    "SYNC",
    "WRITE_PACKET_LEN",
    "DownloadAgent",
]
