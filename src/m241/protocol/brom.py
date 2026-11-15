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
#:
#: Measured 2026-11-15 on a V1.13 unit: reading 0x20000000 returns
#: `42 20 02 02` repeatedly (unmapped bus), while 0x00200000 returns the
#: firmware's `18 f0 9f e5` vector table. The M-241 firmware therefore lives at
#: 0x00200000 in the MT3318 BootROM's view.
BASE_ADDR_FIRMWARE = 0x00200000
#: The MT3318's linear (DA mapped) view of the same flash.
BASE_ADDR_FIRMWARE_M3318 = 0x00000000
#: MT333x-style address some MTK boot ROMs use; kept for probing only.
BASE_ADDR_FIRMWARE_MT333X = 0x20000000
#: Measured load address of the download agent.
BASE_ADDR_DA = 0x00100800

#: BootROM sync probe bytes and the replies they trigger, in order.
BOOT_SYNC = b"\xa0\x0a\x50\x05"
BOOT_REPLY = (0x5F, 0xF5, 0xAF, 0xFA)

#: The BootROM switch command: 13 bytes, no arguments.
#: Measured on hardware: this is what actually moves a real M-241 into BootROM.
PMTK180_COMMAND = b"$PMTK180*3B\r\n"
#: The M-241's NMEA baud rate (used for the PMTK180 switch).
DEFAULT_NMEA_BAUD = 38400

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
    to memory order and back.  This is the transformation for ``0xA1`` WRITE16
    and ``0xA2`` READ16 payloads.
    """
    out = bytearray(data)
    for index in range(0, len(out) - 1, 2):
        out[index], out[index + 1] = out[index + 1], out[index]
    return bytes(out)


def reverse_words32(data: bytes) -> bytes:
    """Reverse each 32-bit word: ``01 02 03 04`` -> ``04 03 02 01``.

    Measured on hardware: ``0xAF`` READ32 returns the flash contents as big
    endian 32-bit words, so memory order is the byte-reversed form of the wire.
    A trailing partial word is left alone.
    """
    out = bytearray(data)
    for index in range(0, len(out) - 3, 4):
        out[index : index + 4] = out[index : index + 4][::-1]
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
        nmea_probe_seconds: float = 0.6,
    ) -> None:
        self.port = port
        self.baud = baud
        self.nmea_baud = nmea_baud
        self.serial_factory = serial_factory
        self.timeout = float(timeout)
        self.sync_timeout = float(sync_timeout)
        #: How long :meth:`probe_nmea` listens before deciding the port is
        #: silent. One NMEA sentence per second, so this must exceed 1 s on
        #: hardware; tests shorten it.
        self.nmea_probe_seconds = float(nmea_probe_seconds)
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
        """Enter the BootROM and perform the ``a0 0a 50 05`` sync dance.

        Two entry routes, both verified on 2026-11-15 against a V1.13 unit:

        * ``pmtk180=True`` -- send ``$PMTK180*3B\\r\\n`` at the NMEA baud, wait
          for the NMEA stream to stop, then continue the sync at the BootROM
          baud.  **This is the route that works on a real M-241.**
        * ``pmtk180=False`` -- assert DTR and RTS and hold them, then sync.

        In both cases the three follow-up probes (``0x0A 0x50 0x05``) are sent
        back to back with no pause: the chip drops out of the handshake if the
        sequence is not immediate.
        """
        serial_port = self._ensure_open()
        if pmtk180:
            self._send_pmtk180(serial_port)
        if use_rts_dtr:
            # Never pulse the reset lines at a firmware that is still streaming:
            # that leaves the unit unresponsive until the battery is removed.
            # The probe is tri-state and the uncertain answer refuses.
            running = self.probe_nmea(serial_port, seconds=self.nmea_probe_seconds)
            if running is True:
                raise BootRomError(
                    "the firmware is streaming NMEA, so the reset lines must not "
                    "be pulsed (that leaves the unit unresponsive). Use "
                    "'--pmtk180' to switch it into BootROM mode instead."
                )
            if running is None:
                raise BootRomError(
                    "cannot tell whether the firmware is running, so the reset "
                    "lines will not be pulsed. Use '--pmtk180' to switch it into "
                    "BootROM mode, or '--assume-brom' if it is already silent."
                )
            self._assert_lines(serial_port)
        self._discard_input(serial_port)
        self._sync(serial_port)

    def _assert_lines(self, serial_port: Any) -> None:
        """Assert DTR and RTS and hold them.

        The logger only listens for the sync while these modem-control lines
        are asserted, so they are held for the duration of the handshake rather
        than pulsed and released.
        """
        for line in ("dtr", "rts"):
            with contextlib.suppress(AttributeError, OSError, ValueError):
                setattr(serial_port, line, True)

    def _send_pmtk180(self, serial_port: Any) -> None:
        """Send the BootROM switch command at the NMEA baud, then switch baud.

        Hardware-verified: the firmware accepts the plain ``$PMTK180*3B``
        sentence (no arguments) and only at the NMEA baud rate.  Sending
        ``$PMTK180,<baud>`` -- as an earlier version of this function did -- is
        silently ignored.

        The sentence is only honoured while the chip is still streaming NMEA,
        so this waits for the stream to go quiet afterwards and reports failure
        when it does not: a later sync attempt against a chip that never
        switched can only time out.
        """
        previous = getattr(serial_port, "baudrate", self.baud)
        probe_baud = self.nmea_baud or DEFAULT_NMEA_BAUD
        if hasattr(serial_port, "baudrate"):
            with contextlib.suppress(AttributeError, OSError, ValueError):
                serial_port.baudrate = probe_baud
                time.sleep(0.05)

        heard = b""
        with contextlib.suppress(AttributeError, OSError, ValueError):
            serial_port.reset_input_buffer()
            heard = self._drain_for(serial_port, 0.35)
            serial_port.reset_input_buffer()
            serial_port.write(PMTK180_COMMAND)
            flush = getattr(serial_port, "flush", None)
            if flush is not None:
                flush()
            time.sleep(0.2)
        trailing = b""
        with contextlib.suppress(AttributeError, OSError, ValueError):
            trailing = self._drain_for(serial_port, 0.5)

        if not heard:
            raise BootRomError(
                "no NMEA stream before $PMTK180: the logger is not in normal "
                "mode (already in BootROM, switched off, or needs a power cycle)"
            )
        if trailing:
            raise BootRomError(
                f"the logger kept streaming NMEA after $PMTK180 "
                f"({len(trailing)} bytes); the switch did not take effect"
            )

        # The chip is listening now; continue at the BootROM baud.
        if hasattr(serial_port, "baudrate"):
            with contextlib.suppress(AttributeError, OSError, ValueError):
                serial_port.baudrate = previous
                time.sleep(0.05)

    def probe_nmea(self, serial_port: Any, *, seconds: float = 0.6) -> bool | None:
        """Is the firmware streaming NMEA right now?

        Returns ``True`` when NMEA sentences were heard, ``False`` when the
        port stayed silent for the whole window, and ``None`` when the answer
        cannot be established (the port could not be read, or bytes arrived
        that are neither NMEA nor a clean silence).

        Pulsing DTR/RTS at a *running* firmware leaves the unit unresponsive
        until the battery is removed, so the reset route must not be taken on a
        ``True`` or a ``None`` answer.  This is the check that
        :meth:`start` relies on; it is deliberately conservative.
        """
        previous_baud = getattr(serial_port, "baudrate", None)
        previous_timeout = getattr(serial_port, "timeout", None)
        probe_baud = self.nmea_baud or DEFAULT_NMEA_BAUD
        if seconds <= 0:
            # A zero window means "do not probe" (simulators and tests); skip
            # the baud switch so it costs nothing.
            return False
        heard = b""
        try:
            if hasattr(serial_port, "baudrate"):
                with contextlib.suppress(AttributeError, OSError, ValueError):
                    serial_port.baudrate = probe_baud
                    time.sleep(0.05)
            with contextlib.suppress(AttributeError, OSError, ValueError):
                serial_port.reset_input_buffer()
            heard = self._drain_for(serial_port, seconds)
        except (AttributeError, OSError, ValueError):
            return None
        finally:
            if previous_timeout is not None:
                with contextlib.suppress(AttributeError, OSError, ValueError):
                    serial_port.timeout = previous_timeout
            if previous_baud is not None:
                with contextlib.suppress(AttributeError, OSError, ValueError):
                    serial_port.baudrate = previous_baud
                    time.sleep(0.05)

        if not heard:
            return False
        # NMEA is ASCII and every sentence starts with '$' and ends with a
        # checksum. Anything else means we cannot tell what the port is doing.
        if b"$" in heard and (b"\n" in heard or b"\r" in heard):
            return True
        return None

    def _drain_for(self, serial_port: Any, seconds: float) -> bytes:
        """Read whatever arrives within *seconds*; never raises.

        The deadline is **fixed**: it is not pushed forward when data arrives.
        Re-arming it made this loop unable to exit while a stream was flowing,
        and it is called precisely when NMEA may still be streaming.

        ``read(4096)`` blocks for the port timeout even when a single byte is
        queued, so the timeout is temporarily shortened while draining and
        restored afterwards; otherwise a 0.35 s budget costs a full second.
        """
        previous = getattr(serial_port, "timeout", None)
        deadline = time.monotonic() + seconds
        buffer = bytearray()
        try:
            with contextlib.suppress(AttributeError, OSError, ValueError):
                serial_port.timeout = min(0.05, seconds)
            while time.monotonic() < deadline:
                chunk = serial_port.read(4096)
                if chunk:
                    buffer += chunk
                else:
                    time.sleep(_POLL)
        finally:
            if previous is not None:
                with contextlib.suppress(AttributeError, OSError, ValueError):
                    serial_port.timeout = previous
        return bytes(buffer)

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
        """Send ``probe_byte`` until the chip answers, accepting X or ~X.

        Measured on hardware: the reply may be the probe byte itself or its
        bitwise complement (``0xA0`` answers with ``0x5F``, ``0x0A`` with
        ``0xF5``, and so on), so both are treated as a valid sync.
        """
        probe = bytes([probe_byte])
        while True:
            serial_port.write(probe)
            got = self._read_byte(deadline, f"BootROM sync reply 0x{expected:02X}")
            if got == expected or got == (probe_byte ^ 0xFF):
                return expected
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

    def _frame(self, opcode: int, address: int, count: int) -> bytes:
        """Build an ``opcode + BE32 address + BE32 count`` frame.

        ``count`` is the unit the chip expects for that opcode: 32-bit dwords
        for ``0xAF``, 16-bit words for ``0xA2``/``0xA1``/``0xA4``, and 0 for
        ``0xA8``.  Measured on hardware -- sending a byte count instead is
        rejected or returns the wrong amount of data.
        """
        return (
            bytes([opcode])
            + (address & 0xFFFFFFFF).to_bytes(4, "big")
            + (count & 0xFFFFFFFF).to_bytes(4, "big")
        )

    # -- commands ----------------------------------------------------------

    def _read(self, opcode: int, address: int, length: int, byte_swap: bool) -> bytes:
        """Read ``length`` bytes; the count field is derived from the opcode.

        ``0xAF`` (READ32) counts dwords and returns byte-reversed 32-bit words;
        ``0xA2`` (READ16) counts words and returns 16-bit pair-swapped data.
        Neither returns a checksum trailer -- the earlier implementation
        consumed two extra bytes and lost frame sync.
        """
        if length <= 0:
            return b""
        if opcode == OPCODE_READ32:
            if address % 4 or length % 4:
                raise BootRomError(
                    f"READ32 needs a 4-byte aligned address and length "
                    f"(got 0x{address:08X}, {length} bytes)"
                )
            count = length // 4
        else:
            if address % 2 or length % 2:
                raise BootRomError(
                    f"READ16 needs a 2-byte aligned address and length "
                    f"(got 0x{address:08X}, {length} bytes)"
                )
            count = length // 2
        self._send_echo(self._frame(opcode, address, count), "read header")
        raw = self.read_exact(length, what=f"{length} bytes at 0x{address:08X}")
        memory_order = reverse_words32(raw) if opcode == OPCODE_READ32 else swap_pairs(raw)
        return memory_order if byte_swap else raw

    def read32(self, address: int, length: int, byte_swap: bool = False) -> bytes:
        """Read ``length`` bytes through the 32-bit BootROM read command (0xAF)."""
        return self._read(OPCODE_READ32, address, length, byte_swap)

    def read16(self, address: int, length: int, byte_swap: bool = False) -> bytes:
        """Read ``length`` bytes through the 16-bit BootROM read command (0xA2)."""
        return self._read(OPCODE_READ16, address, length, byte_swap)

    def read(self, address: int, length: int, byte_swap: bool = False) -> bytes:
        """Read through ``0xAF`` when 4-byte aligned, else fall back to ``0xA2``."""
        if address % 4 == 0 and length % 4 == 0:
            return self.read32(address, length, byte_swap=byte_swap)
        return self.read16(address, length, byte_swap=byte_swap)

    def checksum(self, address: int, length: int) -> int:
        """Ask the chip for the XOR-16 checksum of a region (opcode 0xA4).

        Unlike the read commands this one *does* answer with two bytes.
        """
        if length <= 0:
            return 0
        if address % 2 or length % 2:
            raise BootRomError(
                f"CHECKSUM needs a 2-byte aligned address and length "
                f"(got 0x{address:08X}, {length} bytes)"
            )
        self._send_echo(
            self._frame(OPCODE_CHECKSUM, address, length // 2), "checksum header"
        )
        reply = self.read_exact(2, what=f"checksum reply for 0x{address:08X}")
        return int.from_bytes(reply, "big")

    def jump(self, address: int) -> None:
        """Jump to ``address`` (opcode 0xA8).

        Measured on hardware: the chip echoes only part of the frame and then
        hands control over, so the echo cannot be required the way the other
        commands require it.  The frame is therefore written in one go and
        nothing is read here -- whatever the chip sends next (the DA's info
        report, for instance) belongs to the caller.
        """
        serial_port = self._ensure_open()
        serial_port.write(self._frame(OPCODE_JUMP, address, 0))
        flush = getattr(serial_port, "flush", None)
        if flush is not None:
            flush()

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
        """Write ``data`` with the 16-bit BootROM write command (opcode ``0xA1``).

        The frame carries the count in **16-bit words**.  With
        ``byte_swap=True`` the payload is pair-swapped on the wire (the chip
        stores little-endian, so pass bytes as they should appear in memory).

        **Echo handling.** The chip echoes every byte it receives, but the echo
        trails by exactly one byte: after sending N bytes only N-1 echo bytes
        are available, and the echo of the final byte never arrives because
        nothing follows it.  Requiring an exact echo for the last byte (or
        reading one byte at a time) therefore hangs on real hardware.  This
        implementation streams the frame in ten-byte chunks and, after each
        chunk, compares the accumulated echo against everything sent so far
        *minus its last byte*.

        Unless ``skip_cksum`` is set the chip's checksum of the written region
        is compared with the checksum of the bytes that were stored; RAM writes
        (download-agent loading) use ``skip_cksum=True``.
        """
        payload = bytes(data)
        if not payload:
            return
        if len(payload) % 2:
            raise BootRomError(f"WRITE16 needs an even length, got {len(payload)}")
        if address % 2:
            raise BootRomError(f"WRITE16 needs a 2-byte aligned address, got 0x{address:08X}")
        wire = swap_pairs(payload) if byte_swap else payload
        header = self._frame(OPCODE_WRITE16, address, len(payload) // 2)

        serial_port = self._ensure_open()
        self._discard_input(serial_port)

        state = {"sent": b"", "echo": b""}

        def pump(block: bytes, where: int) -> None:
            """Send ``block`` and verify the echo, allowing for the one-byte lag."""
            serial_port.write(block)
            serial_port.flush()
            state["sent"] += block
            if not verify_echo:
                return
            want = len(state["sent"]) - 1
            if want > len(state["echo"]):
                state["echo"] += self.read_exact(
                    want - len(state["echo"]),
                    what=f"write16 echo at 0x{where:08X}",
                )
            if state["echo"] != state["sent"][:-1]:
                raise BootRomError(
                    f"BootROM echo mismatch while writing at 0x{where:08X}: "
                    f"sent {state['sent'].hex()}, chip echoed {state['echo'].hex()}",
                    offset=len(state["sent"]),
                )

        pump(header, address)
        done = 0
        for offset in range(0, len(wire), WRITE_CHUNK):
            chunk = wire[offset : offset + WRITE_CHUNK]
            pump(chunk, address + offset)
            done += len(chunk)
            if progress_cb is not None:
                progress_cb(done, len(wire))

        # The chip may still be flushing; drop the tail so the next command
        # starts on a clean frame boundary.
        self._drain_quiet(serial_port)

        if not skip_cksum:
            stored = swap_pairs(wire)
            expected = checksum_xor16(stored)
            actual = self.checksum(address, len(payload))
            if actual != expected:
                raise BootRomError(
                    f"BootROM write16 checksum mismatch at 0x{address:08X}: "
                    f"expected 0x{expected:04X}, chip reports 0x{actual:04X}"
                )

    def _drain_quiet(self, serial_port: Any, *, quiet: float = 0.06, limit: float = 1.0) -> bytes:
        """Consume bytes until the line has been idle for ``quiet`` seconds."""
        drained = bytearray()
        deadline = time.monotonic() + limit
        quiet_at = time.monotonic() + quiet
        while time.monotonic() < deadline:
            chunk = serial_port.read(256)
            if chunk:
                drained += chunk
                quiet_at = time.monotonic() + quiet
            elif time.monotonic() >= quiet_at:
                break
            else:
                time.sleep(_POLL)
        self._discard_input(serial_port)
        return bytes(drained)


def _decode_firmware_size(raw_header: bytes) -> int:
    """Decode the size field at ``base + 0xF4``.

    The image stores the value little-endian (see
    ``docs/04-firmware-profiles.md``).  ``0xAF`` READ32 returns byte-reversed
    32-bit words, so callers pass the already-corrected bytes; this helper just
    accepts either endianness and keeps the plausible value.
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
    """Dump ``length`` (or header-derived) bytes from the firmware base address.

    ``byte_swap=True`` (the default) returns memory-order bytes: the 32-bit
    words the chip sends back are byte-reversed.
    """
    if length is None:
        raw_header = brom.read32(
            base_addr + FIRMWARE_SIZE_OFFSET, 4, byte_swap=byte_swap
        )
        length = _decode_firmware_size(raw_header)
    if length < 0:
        raise FirmwareError(f"invalid dump length {length}", offset=0)
    if length == 0:
        return b""
    buffer = bytearray()
    while len(buffer) < length:
        chunk_len = min(DUMP_CHUNK, length - len(buffer))
        if chunk_len % 4:
            chunk_len -= chunk_len % 4
            if chunk_len == 0:
                chunk_len = 4
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
    return bytes(buffer[:length])


__all__ = [
    "BASE_ADDR_DA",
    "BASE_ADDR_FIRMWARE",
    "BASE_ADDR_FIRMWARE_M3318",
    "BASE_ADDR_FIRMWARE_MT333X",
    "BOOT_REPLY",
    "BOOT_SYNC",
    "DEFAULT_NMEA_BAUD",
    "DUMP_CHUNK",
    "FIRMWARE_SIZE_OFFSET",
    "MAX_FIRMWARE_SIZE",
    "MIN_FIRMWARE_SIZE",
    "OPCODE_CHECKSUM",
    "OPCODE_JUMP",
    "OPCODE_READ16",
    "OPCODE_READ32",
    "OPCODE_WRITE16",
    "PMTK180_COMMAND",
    "MtkBootRom",
    "WRITE_CHUNK",
    "checksum_xor16",
    "dump_firmware",
    "reverse_words32",
    "swap_pairs",
]
