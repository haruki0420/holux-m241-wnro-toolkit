"""MTK BootROM (BROM) transport for the MT3318 GPS chip used by the M-241.

Every BootROM command is a nine byte frame::

    opcode (u8) | address (u32 big-endian) | length (u32 big-endian)

The chip echoes every received byte exactly once before it answers, so each
frame byte is verified; a mismatch means the line is dirty or the chip
rebooted.  Reads are byte-swapped in 16-bit pairs on the wire (that is why
``byte_swap`` exists), an odd trailing byte is passed through unchanged.

``pyserial`` is only reached through :func:`m241.protocol.ports.open_serial`
and only when a port is actually opened.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from typing import Any

from m241.errors import BootRomError, FirmwareError

from . import ports

#: Flash is visible at this address through the BootROM on the M-241.
BASE_ADDR_FIRMWARE = 0x20000000
#: The MT3318's linear (DA mapped) view of the same flash.
BASE_ADDR_FIRMWARE_M3318 = 0x00000000
#: Verified load address of the download agent (HoluxLoggerUtility.exe).
BASE_ADDR_DA = 0x00100800

#: BootROM sync probe bytes and the replies they trigger, in order.
BOOT_SYNC = b"\xa0\x0a\x50\x05"
BOOT_REPLY = (0x5F, 0xF5, 0xAF, 0xFA)

OPCODE_WRITE16 = 0xA1
OPCODE_READ16 = 0xA2
OPCODE_CHECKSUM = 0xA4
OPCODE_JUMP = 0xA8
OPCODE_READ32 = 0xAF

#: Firmware size header field offset relative to the firmware base address.
FIRMWARE_SIZE_OFFSET = 0xF4

MIN_FIRMWARE_SIZE = 0x10
MAX_FIRMWARE_SIZE = 8 * 1024 * 1024

#: Data bytes per write16 serial chunk (the reference tooling uses 10).
WRITE_CHUNK = 10
#: Bytes per read command while dumping.
DUMP_CHUNK = 0x1000
#: Poll interval while waiting for serial data.
_POLL = 0.002

ProgressCallback = Callable[[int, int], None]


def swap_pairs(data: bytes) -> bytes:
    """Swap every 16-bit pair: ``ab cd`` -> ``ba dc`` (odd tail unchanged).

    Applying it twice is the identity, so the same helper converts wire order
    to memory order and back.
    """
    out = bytearray(data)
    for index in range(0, len(out) - 1, 2):
        out[index], out[index + 1] = out[index + 1], out[index]
    return bytes(out)


def checksum_xor16(data: bytes) -> int:
    """XOR of the big-endian 16-bit words of ``data``.

    A trailing odd byte is treated as the high byte of a word, matching how the
    chip's checksum command walks a region two bytes at a time.
    """
    value = 0
    for index in range(0, len(data) - 1, 2):
        value ^= (data[index] << 8) | data[index + 1]
    if len(data) % 2:
        value ^= data[-1] << 8
    return value & 0xFFFF


class MtkBootRom:
    """Talk to the MTK BootROM over a serial port.

    ``serial_factory(port, baud)`` is the injection point for tests; it
    defaults to :func:`m241.protocol.ports.open_serial`.  The port is opened
    lazily on the first I/O operation (normally :meth:`start`), so constructing
    an instance never touches hardware -- which keeps ``m241 ports`` style
    commands and signature-only tests hardware-free.
    """

    def __init__(
        self,
        port: str,
        *,
        baud: int = 115200,
        nmea_baud: int | None = None,
        serial_factory: Callable[[str, int], Any] | None = None,
        timeout: float = 1.0,
        sync_timeout: float = 5.0,
    ) -> None:
        self.port = port
        self.baud = baud
        self.nmea_baud = nmea_baud
        self.serial_factory = serial_factory
        self.timeout = float(timeout)
        self.sync_timeout = float(sync_timeout)
        self.ser: Any = None
        self._closed = False

    # -- lifecycle ---------------------------------------------------------

    def _ensure_open(self) -> Any:
        if self.ser is None:
            if self._closed:
                raise BootRomError(
                    "the serial port of this MtkBootRom was closed; create a new instance"
                )
            factory = self.serial_factory or ports.open_serial
            serial_port = factory(self.port, self.baud)
            with contextlib.suppress(AttributeError, ValueError):
                serial_port.timeout = self.timeout
            self.ser = serial_port
        return self.ser

    def close(self) -> None:
        """Close the serial port.  Safe to call more than once."""
        serial_port, self.ser = self.ser, None
        self._closed = True
        if serial_port is None:
            return
        with contextlib.suppress(Exception):  # closing must never raise
            serial_port.close()

    def __enter__(self) -> MtkBootRom:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- handshake ---------------------------------------------------------

    def start(self, *, use_rts_dtr: bool = True, pmtk180: bool = False) -> None:
        """Pulse the control lines and perform the ``a0 0a 50 05`` sync dance."""
        serial_port = self._ensure_open()
        if use_rts_dtr:
            self._pulse_lines(serial_port)
        if pmtk180:
            self._send_pmtk180(serial_port)
        self._discard_input(serial_port)
        self._sync(serial_port)

    def _pulse_lines(self, serial_port: Any) -> None:
        for line in ("dtr", "rts"):
            with contextlib.suppress(AttributeError, OSError, ValueError):
                setattr(serial_port, line, False)
                time.sleep(0.01)
                setattr(serial_port, line, True)
                time.sleep(0.01)

    def _send_pmtk180(self, serial_port: Any) -> None:
        """Best-effort ``PMTK180`` probe before the BootROM sync.

        Some M-241 firmwares only answer on their configured NMEA baud rate;
        this sends the vendor style probe (with a computed NMEA checksum) and
        restores the previous baud rate afterwards.  Failures are ignored: the
        sync dance decides whether the connection works.
        """
        body = f"PMTK180,{self.baud}"
        checksum = 0
        for char in body:
            checksum ^= ord(char)
        sentence = f"${body}*{checksum:02X}\r\n".encode("ascii")
        previous = getattr(serial_port, "baudrate", self.baud)
        probe_baud = self.nmea_baud or previous
        with contextlib.suppress(AttributeError, OSError, ValueError):
            serial_port.baudrate = probe_baud
            serial_port.write(sentence)
            time.sleep(0.05)
        with contextlib.suppress(AttributeError, OSError, ValueError):
            serial_port.baudrate = previous

    def _discard_input(self, serial_port: Any) -> None:
        with contextlib.suppress(AttributeError, OSError):
            serial_port.reset_input_buffer()

    def _sync(self, serial_port: Any) -> None:
        deadline = time.monotonic() + self.sync_timeout
        for probe_byte, expected in zip(BOOT_SYNC, BOOT_REPLY, strict=True):
            reply = self._sync_stage(serial_port, probe_byte, expected, deadline)
            if reply != expected:  # pragma: no cover - _sync_stage raises instead
                raise BootRomError(
                    f"BootROM sync failed: expected 0x{expected:02X}, got 0x{reply:02X}"
                )

    def _sync_stage(
        self, serial_port: Any, probe_byte: int, expected: int, deadline: float
    ) -> int:
        probe = bytes([probe_byte])
        while True:
            serial_port.write(probe)
            got = self._read_byte(deadline, f"BootROM sync reply 0x{expected:02X}")
            if got == expected:
                return got
            if time.monotonic() >= deadline:
                raise BootRomError(
                    f"BootROM sync failed: no 0x{expected:02X} reply within "
                    f"{self.sync_timeout:.2f}s (last byte 0x{got:02X}); check the port, "
                    "the cable and that the logger is in BootROM mode"
                )

    # -- low level I/O -----------------------------------------------------

    def _read_byte(self, deadline: float, what: str) -> int:
        serial_port = self._ensure_open()
        while True:
            chunk = serial_port.read(1)
            if chunk:
                return chunk[0]
            if time.monotonic() >= deadline:
                raise BootRomError(f"timeout waiting for {what}")
            time.sleep(_POLL)

    def read_exact(self, count: int, *, what: str = "data") -> bytes:
        """Read exactly ``count`` bytes, raising :class:`BootRomError` on timeout."""
        if count <= 0:
            return b""
        serial_port = self._ensure_open()
        deadline = time.monotonic() + self.timeout
        buffer = bytearray()
        while len(buffer) < count:
            chunk = serial_port.read(count - len(buffer))
            if chunk:
                buffer += chunk
                continue
            if time.monotonic() >= deadline:
                raise BootRomError(
                    f"timeout after {self.timeout:.2f}s waiting for {what}: "
                    f"received {len(buffer)} of {count} bytes",
                    offset=len(buffer),
                )
            time.sleep(_POLL)
        return bytes(buffer)

    def _send_echo(self, frame: bytes, what: str, *, verify: bool = True) -> None:
        """Write ``frame`` byte by byte and consume the chip's echo."""
        serial_port = self._ensure_open()
        for offset, byte in enumerate(frame):
            serial_port.write(bytes([byte]))
            echoed = self.read_exact(1, what=f"echo of {what} byte {offset}")
            if verify and echoed[0] != byte:
                raise BootRomError(
                    f"BootROM echo mismatch in {what}: sent 0x{byte:02X}, "
                    f"chip echoed 0x{echoed[0]:02X}",
                    offset=offset,
                )

    def _frame(self, opcode: int, address: int, length: int) -> bytes:
        return (
            bytes([opcode])
            + (address & 0xFFFFFFFF).to_bytes(4, "big")
            + (length & 0xFFFFFFFF).to_bytes(4, "big")
        )

    # -- commands ----------------------------------------------------------

    def _read(self, opcode: int, address: int, length: int, byte_swap: bool) -> bytes:
        if length <= 0:
            return b""
        self._send_echo(self._frame(opcode, address, length), "read header")
        raw = self.read_exact(length, what=f"{length} bytes at 0x{address:08X}")
        reply = self.read_exact(2, what=f"read checksum for 0x{address:08X}")
        # The wire always carries 16-bit pairs swapped relative to memory; the
        # chip's XOR16 checksum is computed over the memory-order bytes.
        memory_order = swap_pairs(raw)
        expected = checksum_xor16(memory_order)
        actual = int.from_bytes(reply, "big")
        if actual != expected:
            raise BootRomError(
                f"read checksum mismatch at 0x{address:08X} (0x{length:X} bytes): "
                f"expected 0x{expected:04X}, chip sent 0x{actual:04X}; "
                "the chip may still be busy, retry the read",
                offset=0,
            )
        return memory_order if byte_swap else raw

    def read32(self, address: int, length: int, byte_swap: bool = False) -> bytes:
        """Read ``length`` bytes through the 32-bit BootROM read command (0xAF)."""
        return self._read(OPCODE_READ32, address, length, byte_swap)

    def read16(self, address: int, length: int, byte_swap: bool = False) -> bytes:
        """Read ``length`` bytes through the 16-bit BootROM read command (0xA2)."""
        return self._read(OPCODE_READ16, address, length, byte_swap)

    def read(self, address: int, length: int, byte_swap: bool = False) -> bytes:
        """Generic read; identical to :meth:`read32`."""
        return self.read32(address, length, byte_swap=byte_swap)

    def checksum(self, address: int, length: int) -> int:
        """Ask the chip for the XOR-16 checksum of a region (opcode 0xA4)."""
        if length <= 0:
            return 0
        self._send_echo(self._frame(OPCODE_CHECKSUM, address, length), "checksum header")
        reply = self.read_exact(2, what=f"checksum reply for 0x{address:08X}")
        return int.from_bytes(reply, "big")

    def jump(self, address: int) -> None:
        """Jump to ``address`` (opcode 0xA8); the chip does not answer."""
        self._send_echo(self._frame(OPCODE_JUMP, address, 0), "jump header")

    def write16(
        self,
        address: int,
        data: bytes,
        *,
        byte_swap: bool = False,
        verify_echo: bool = True,
        skip_cksum: bool = False,
        progress_cb: ProgressCallback | None = None,
    ) -> None:
        """Write ``data`` with the 16-bit BootROM write command (opcode 0xA1).

        The frame (opcode/address/count) is sent once, then the whole payload as
        ten byte chunks; every byte is echoed.  With ``byte_swap=True`` the data
        stream is byte-swapped in 16-bit pairs on the wire (the chip stores
        little-endian, so pass the bytes as they should end up in memory).

        Unless ``skip_cksum`` is set the chip's checksum of the written region
        is compared with the checksum of the bytes that were stored; RAM writes
        (download-agent loading) use ``skip_cksum=True``.
        """
        payload = bytes(data)
        if not payload:
            return
        wire = swap_pairs(payload) if byte_swap else payload
        self._send_echo(
            self._frame(OPCODE_WRITE16, address, len(payload)), "write16 header"
        )
        serial_port = self._ensure_open()
        done = 0
        for offset in range(0, len(wire), WRITE_CHUNK):
            chunk = wire[offset : offset + WRITE_CHUNK]
            serial_port.write(chunk)
            echoed = self.read_exact(len(chunk), what=f"write16 echo at 0x{address + offset:08X}")
            if verify_echo and echoed != chunk:
                raise BootRomError(
                    f"BootROM echo mismatch while writing at 0x{address + offset:08X}: "
                    f"sent {chunk.hex()}, chip echoed {echoed.hex()}",
                    offset=offset,
                )
            done += len(chunk)
            if progress_cb is not None:
                progress_cb(done, len(wire))
        if not skip_cksum:
            stored = swap_pairs(wire)
            expected = checksum_xor16(stored)
            actual = self.checksum(address, len(payload))
            if actual != expected:
                raise BootRomError(
                    f"BootROM write16 checksum mismatch at 0x{address:08X}: "
                    f"expected 0x{expected:04X}, chip reports 0x{actual:04X}"
                )


def _decode_firmware_size(raw_header: bytes) -> int:
    """Decode the size field at ``base + 0xF4``.

    The image stores the value little-endian (see
    ``docs/04-firmware-profiles.md``), while the frozen contract names it
    big-endian, so both orders are accepted and the smaller plausible value
    wins: byte-reversing a realistic firmware size always yields a larger
    number.
    """
    big_endian = int.from_bytes(raw_header, "big")
    little_endian = int.from_bytes(raw_header, "little")
    plausible = [
        value
        for value in (big_endian, little_endian)
        if MIN_FIRMWARE_SIZE <= value <= MAX_FIRMWARE_SIZE
    ]
    if not plausible:
        raise FirmwareError(
            "implausible firmware size at header offset 0xF4: "
            f"bytes {raw_header.hex()} decode to {big_endian} (big-endian) or "
            f"{little_endian} (little-endian); pass an explicit length",
            offset=FIRMWARE_SIZE_OFFSET,
        )
    return min(plausible)


def dump_firmware(
    brom: MtkBootRom,
    *,
    base_addr: int = BASE_ADDR_FIRMWARE,
    length: int | None = None,
    byte_swap: bool = True,
    progress_cb: ProgressCallback | None = None,
) -> bytes:
    """Dump ``length`` (or header-derived) bytes from the firmware base address."""
    if length is None:
        raw_header = brom.read32(
            base_addr + FIRMWARE_SIZE_OFFSET, 4, byte_swap=byte_swap
        )
        header = raw_header if byte_swap else swap_pairs(raw_header)
        length = _decode_firmware_size(header)
    if length < 0:
        raise FirmwareError(f"invalid dump length {length}", offset=0)
    if length == 0:
        return b""
    buffer = bytearray()
    while len(buffer) < length:
        chunk_len = min(DUMP_CHUNK, length - len(buffer))
        chunk = brom.read32(base_addr + len(buffer), chunk_len, byte_swap=byte_swap)
        if len(chunk) != chunk_len:
            raise BootRomError(
                f"short read at 0x{base_addr + len(buffer):08X}: "
                f"got {len(chunk)} of {chunk_len} bytes",
                offset=len(buffer),
            )
        buffer += chunk
        if progress_cb is not None:
            progress_cb(len(buffer), length)
    return bytes(buffer)


__all__ = [
    "BASE_ADDR_DA",
    "BASE_ADDR_FIRMWARE",
    "BASE_ADDR_FIRMWARE_M3318",
    "BOOT_REPLY",
    "BOOT_SYNC",
    "DUMP_CHUNK",
    "FIRMWARE_SIZE_OFFSET",
    "MAX_FIRMWARE_SIZE",
    "MIN_FIRMWARE_SIZE",
    "OPCODE_CHECKSUM",
    "OPCODE_JUMP",
    "OPCODE_READ16",
    "OPCODE_READ32",
    "OPCODE_WRITE16",
    "MtkBootRom",
    "WRITE_CHUNK",
    "checksum_xor16",
    "dump_firmware",
    "swap_pairs",
]
