"""A pure-Python MT3318 stand-in: fake serial port + fake device.

:class:`FakeDevice` implements the BootROM framing (echo, reads, writes,
checksums, jump) and the download-agent handshake (sync report, memory-range
command, packet writes) so the *real* :class:`m241.protocol.brom.MtkBootRom`
and :class:`m241.protocol.da.DownloadAgent` code can be exercised on a machine
with no hardware attached.

Wire conventions mirrored here:

* Every command byte, and every write16 data byte, is echoed once.
* Reads return the memory bytes with 16-bit pairs swapped, because the host
  side swaps them back when ``byte_swap=True``.
* write16 data arrives with 16-bit pairs swapped and is stored unswapped.
* The checksum command returns the XOR of the big-endian 16-bit words.

Flash is visible both through the BootROM window (``0x20000000``) and through
the DA's linear window (``0x0``); ``0x100000``-``0x1FFFFF`` is RAM.
"""

from __future__ import annotations

import struct
import time

from m241.errors import BootRomError

from .brom import (
    BASE_ADDR_DA,
    BASE_ADDR_FIRMWARE,
    BASE_ADDR_FIRMWARE_MT333X,
    BOOT_REPLY,
    BOOT_SYNC,
    OPCODE_CHECKSUM,
    OPCODE_JUMP,
    OPCODE_READ16,
    OPCODE_READ32,
    OPCODE_WRITE16,
    WRITE_CHUNK,
    checksum_xor16,
    swap_pairs,
)
from .da import (
    ACK,
    CONT,
    DA_INFO_FORMAT,
    DA_INFO_SIZE,
    EXPECTED_DA_VERSION,
    NAK,
    OPCODE_MEM_CMD,
    OPCODE_RESTART,
    OPCODE_SET_BAUD,
    OPCODE_WRITE,
    SYNC,
    WRITE_PACKET_LEN,
)

#: Backing store size of the fake flash address space (8 MiB).
ADDRESS_SPACE = 0x800000
#: Fake RAM window the download agent is loaded into.
RAM_BASE = 0x100000
RAM_SIZE = 0x100000
#: DA report values chosen to look like a real MX29LV320-class 2 MiB chip.
DEFAULT_FLASH_SIZE = 0x200000
DEFAULT_FLASH_DEVICE_ID = 0xBA
DEFAULT_FLASH_MANUFACTURER = 0x00C2
DEFAULT_FLASH_DEVICE_CODE = 0x22BA


class FakeDevice:
    """Emulate enough of the MT3318 to drive the real BootROM/DA code."""

    def __init__(
        self,
        firmware: bytes = b"",
        *,
        flash_size: int = DEFAULT_FLASH_SIZE,
        da_version: int = EXPECTED_DA_VERSION,
        flash_device_id: int = DEFAULT_FLASH_DEVICE_ID,
        flash_manufacturer_code: int = DEFAULT_FLASH_MANUFACTURER,
        flash_device_code: int = DEFAULT_FLASH_DEVICE_CODE,
        ext_sram_size: int = 0,
        silent: bool = False,
        corrupt_echo: bool = False,
        fail_packet: int | None = None,
        nak_set_baud: bool = False,
    ) -> None:
        self.flash_size = flash_size
        self.da_version = da_version
        self.flash_device_id = flash_device_id
        self.flash_manufacturer_code = flash_manufacturer_code
        self.flash_device_code = flash_device_code
        self.ext_sram_size = ext_sram_size
        #: When True the device never answers, so every host read times out.
        self.silent = silent
        #: When True the echo of write16 data is flipped, causing a mismatch.
        self.corrupt_echo = corrupt_echo
        #: Zero-based packet index the fake DA NAKs.
        self.fail_packet = fail_packet
        self.nak_set_baud = nak_set_baud

        self._memory = bytearray(ADDRESS_SPACE)
        self._ram = bytearray(RAM_SIZE)
        self._load_firmware(firmware)
        self._output = bytearray()
        self._incoming = bytearray()
        self._reset_state()

    def _reset_state(self) -> None:
        self._state = "bootrom"
        self._stage = "sync"
        self._sync_index = 0
        self._frame = bytearray()
        self._write_addr = 0
        self._write_count = 0
        self._write_data = bytearray()
        self._write_done = 0
        self._da_start = 0
        self._da_cursor = 0
        self._da_total = 0
        self._da_packet_len = WRITE_PACKET_LEN
        self._da_packet_index = 0

    # -- public state ------------------------------------------------------

    @property
    def memory(self) -> bytearray:
        """The fake flash address space (offset 0 corresponds to ``0x20000000``)."""
        return self._memory

    @property
    def ram(self) -> bytearray:
        """The fake RAM window (offset 0 corresponds to ``0x00100000``)."""
        return self._ram

    def factory(self, port: str, baud: int) -> FakeSerial:
        """Return a ``serial_factory``-compatible callable creating a port handle."""
        return FakeSerial(self, baud=baud)

    def load_firmware(self, image: bytes, *, offset: int = 0) -> None:
        """Store ``image`` into the flash window at ``offset``."""
        self._load_firmware(image, offset=offset)

    def reboot(self) -> None:
        """Return the fake chip to the BootROM sync state."""
        self._output.clear()
        self._incoming.clear()
        self._reset_state()

    def _load_firmware(self, image: bytes, *, offset: int = 0) -> None:
        if offset < 0 or offset + len(image) > len(self._memory):
            raise ValueError(
                f"firmware of {len(image)} bytes at offset 0x{offset:X} does not fit "
                f"the {len(self._memory)} byte fake flash"
            )
        self._memory[offset : offset + len(image)] = image

    # -- serial-facing plumbing -------------------------------------------

    def write(self, data: bytes) -> None:
        if self.silent:
            return
        self._incoming += data
        self._process()

    def read(self, size: int) -> bytes:
        if size <= 0:
            return b""
        chunk = bytes(self._output[:size])
        del self._output[:size]
        return chunk

    @property
    def in_waiting(self) -> int:
        """Number of reply bytes buffered for the host."""
        return len(self._output)

    def emit(self, data: bytes) -> None:
        """Queue ``data`` for the next host read."""
        self._output += data

    # -- byte stream state machine ----------------------------------------

    def _process(self) -> None:
        while self._incoming:
            byte = self._incoming[0]
            del self._incoming[0]
            if self._state == "bootrom":
                self._process_bootrom(byte)
            else:
                self._process_da(byte)

    def _process_bootrom(self, byte: int) -> None:
        if self._stage == "sync":
            if byte != BOOT_SYNC[self._sync_index]:
                return
            self.emit(bytes([BOOT_REPLY[self._sync_index]]))
            self._sync_index += 1
            if self._sync_index == len(BOOT_SYNC):
                self._stage = "command"
            return
        if self._stage == "command":
            if byte == BOOT_SYNC[0] and not self._frame:
                # 0xA0 is not a valid BootROM opcode, so the real chip treats it
                # as the start of another sync dance.  This is what makes a
                # second start() on an already synced chip work.
                self._stage = "sync"
                self._sync_index = 1
                self.emit(bytes([BOOT_REPLY[0]]))
                return
            self.emit(bytes([byte]))  # the chip echoes every command byte
            self._frame.append(byte)
            if len(self._frame) < 9:
                return
            frame = bytes(self._frame)
            self._frame.clear()
            self._dispatch_frame(frame)
            return
        # write16 payload: echo every byte, store 10-byte chunks
        self.emit(bytes([byte ^ 0xFF if self.corrupt_echo else byte]))
        self._write_data.append(byte)
        self._write_done += 1
        if len(self._write_data) == WRITE_CHUNK or self._write_done >= self._write_count:
            chunk_start = self._write_done - len(self._write_data)
            self._write(
                swap_pairs(bytes(self._write_data)), self._write_addr + chunk_start
            )
            self._write_data.clear()
            if self._write_done >= self._write_count:
                self._write_done = 0
                self._stage = "command"

    def _dispatch_frame(self, frame: bytes) -> None:
        opcode = frame[0]
        address = int.from_bytes(frame[1:5], "big")
        count = int.from_bytes(frame[5:9], "big")
        if opcode in (OPCODE_READ32, OPCODE_READ16):
            # The count field is in dwords for 0xAF and in words for 0xA2.
            unit = 4 if opcode == OPCODE_READ32 else 2
            data = self._read(address, count * unit)
            if opcode == OPCODE_READ32:
                # READ32 sends each 32-bit word big-endian on the wire.
                self.emit(b"".join(data[i : i + 4][::-1] for i in range(0, len(data), 4)))
            else:
                self.emit(swap_pairs(data))
        elif opcode == OPCODE_CHECKSUM:
            # CHECKSUM counts words.
            self.emit(checksum_xor16(self._read(address, count * 2)).to_bytes(2, "big"))
        elif opcode == OPCODE_WRITE16:
            # WRITE16 counts words, not bytes.
            if count == 0:
                return
            self._write_addr = address
            self._write_count = count * 2
            self._write_data.clear()
            self._write_done = 0
            self._stage = "write"
        elif opcode == OPCODE_JUMP:
            self._jump(address)
        else:
            raise BootRomError(f"fake device got unknown BootROM opcode 0x{opcode:02X}")

    def _jump(self, address: int) -> None:
        if address != BASE_ADDR_DA:
            self._state = "bootrom"
            self._stage = "sync"
            return
        self._state = "da"
        self._stage = "da_command"
        self._frame.clear()
        self._da_packet_index = 0
        self._da_packet_len = WRITE_PACKET_LEN
        self.emit(self._info_report())

    def _info_report(self) -> bytes:
        return struct.pack(
            DA_INFO_FORMAT,
            SYNC,
            self.da_version & 0xFFFF,
            self.flash_device_id & 0xFF,
            self.flash_size & 0xFFFFFFFF,
            self.flash_manufacturer_code & 0xFFFF,
            self.flash_device_code & 0xFFFF,
            0x22,
            0xBA,
            self.ext_sram_size & 0xFFFFFFFF,
        )

    # -- download-agent state machine -------------------------------------

    def _process_da(self, byte: int) -> None:
        self._frame.append(byte)
        if self._stage == "da_command":
            self._dispatch_da_command()
        elif self._stage == "da_packet":
            if len(self._frame) < self._next_packet_len() + 2:
                return
            raw = bytes(self._frame)
            self._frame.clear()
            self._da_write_packet(raw[:-2], int.from_bytes(raw[-2:], "big"))

    def _next_packet_len(self) -> int:
        """Data length of the next packet: 0x100 except for the final short one."""
        remaining = self._da_total - (self._da_cursor - self._da_start)
        return min(self._da_packet_len, remaining) if remaining > 0 else self._da_packet_len

    def _dispatch_da_command(self) -> None:
        opcode = self._frame[0]
        if opcode == OPCODE_MEM_CMD:
            if len(self._frame) < 10:
                return
            _, _flag, start, end = struct.unpack(">BBLL", bytes(self._frame[:10]))
            self._frame.clear()
            self._da_start = start
            self._da_cursor = start
            self._da_total = end - start + 1
            self._da_packet_index = 0
            self.emit(bytes([ACK]))
            return
        if opcode == OPCODE_WRITE:
            if len(self._frame) < 5:
                return
            _, packet_len = struct.unpack(">BL", bytes(self._frame[:5]))
            self._frame.clear()
            self._da_packet_len = packet_len or WRITE_PACKET_LEN
            self._stage = "da_packet"
            self.emit(bytes([ACK, ACK]))
            return
        if opcode == OPCODE_SET_BAUD:
            if len(self._frame) < 5:
                return
            self._frame.clear()
            self.emit(bytes([NAK if self.nak_set_baud else ACK]))
            return
        if opcode == OPCODE_RESTART:
            self._frame.clear()
            self.reboot()
            return
        self._frame.clear()
        self.emit(bytes([NAK]))

    def _da_write_packet(self, packet: bytes, checksum: int) -> None:
        if self.fail_packet is not None and self._da_packet_index == self.fail_packet:
            self.emit(bytes([NAK]))
            return
        if checksum != sum(packet) & 0xFFFF:
            self.emit(bytes([NAK]))
            return
        if self._da_cursor + len(packet) > len(self._memory):
            raise BootRomError(
                f"fake DA write past the end of the address space at 0x{self._da_cursor:08X}"
            )
        self._memory[self._da_cursor : self._da_cursor + len(packet)] = packet
        self._da_cursor += len(packet)
        self._da_packet_index += 1
        self.emit(bytes([CONT]))
        if self._da_cursor >= self._da_start + self._da_total:
            self.emit(bytes([ACK, ACK]))
            self._stage = "da_command"
            self._da_total = 0

    # -- address decoding --------------------------------------------------

    def _region(self, address: int, length: int) -> tuple[bytearray, int]:
        # The M-241 firmware lives at 0x00200000 in the MT3318's BootROM view
        # (hardware-verified). The MT333x-style 0x20000000 is also mapped so the
        # legacy profile tests and firmware images keep working.
        if BASE_ADDR_FIRMWARE <= address < BASE_ADDR_FIRMWARE + len(self._memory):
            storage, offset = self._memory, address - BASE_ADDR_FIRMWARE
        elif BASE_ADDR_FIRMWARE_MT333X <= address < BASE_ADDR_FIRMWARE_MT333X + len(
            self._memory
        ):
            storage, offset = self._memory, address - BASE_ADDR_FIRMWARE_MT333X
        elif RAM_BASE <= address < RAM_BASE + RAM_SIZE:
            storage, offset = self._ram, address - RAM_BASE
        elif 0 <= address < RAM_BASE:
            storage, offset = self._memory, address
        else:
            raise BootRomError(f"unmapped fake address 0x{address:08X}")
        if offset < 0 or offset + length > len(storage):
            raise BootRomError(
                f"fake access of {length} bytes at 0x{address:08X} is outside the "
                f"{len(storage)} byte region"
            )
        return storage, offset
    def _read(self, address: int, length: int) -> bytes:
        storage, offset = self._region(address, length)
        return bytes(storage[offset : offset + length])

    def _write(self, data: bytes, address: int) -> None:
        storage, offset = self._region(address, len(data))
        storage[offset : offset + len(data)] = data


class FakeSerial:
    """Duck-typed :class:`serial.Serial` backed by a :class:`FakeDevice`."""

    def __init__(self, device: FakeDevice, *, baud: int = 115200) -> None:
        self.device = device
        self.baudrate = baud
        self.timeout: float | None = 1.0
        self.rts = True
        self.dtr = True
        self.is_open = True
        self.port = "FAKE"

    @property
    def in_waiting(self) -> int:
        """Reply bytes currently buffered by the fake device."""
        return self.device.in_waiting

    def write(self, data: bytes) -> int:
        """Hand ``data`` to the fake device; returns the byte count."""
        payload = bytes(data)
        self.device.write(payload)
        return len(payload)

    def read(self, size: int = 1) -> bytes:
        """Read up to ``size`` buffered reply bytes."""
        time.sleep(0)
        return self.device.read(size)

    def flush(self) -> None:
        """No-op: the fake device never blocks."""

    def reset_input_buffer(self) -> None:
        """Drop any bytes the host has not consumed yet."""
        self.device._incoming.clear()  # noqa: SLF001 - simulator internals
        self.device.read(1 << 30)

    def close(self) -> None:
        """Mark this handle closed."""
        self.is_open = False


def roundtrip(self_test_image: bytes) -> bytes:
    """Load an image into a fake device and dump it back through the real BootROM.

    The returned bytes are identical to ``self_test_image``.
    """
    from .brom import MtkBootRom, dump_firmware

    device = FakeDevice(self_test_image)
    brom = MtkBootRom("FAKE", serial_factory=device.factory, timeout=1.0)
    try:
        brom.start()
        return dump_firmware(brom, length=len(self_test_image), byte_swap=True)
    finally:
        brom.close()


__all__ = [
    "ADDRESS_SPACE",
    "DEFAULT_FLASH_DEVICE_CODE",
    "DEFAULT_FLASH_DEVICE_ID",
    "DEFAULT_FLASH_MANUFACTURER",
    "DEFAULT_FLASH_SIZE",
    "DA_INFO_SIZE",
    "FakeDevice",
    "FakeSerial",
    "RAM_BASE",
    "RAM_SIZE",
    "roundtrip",
]
