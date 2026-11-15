"""Firmware image handling for the Holux M-241 (MT3318) GPS logger.

The M-241 firmware is a Thumb image loaded at ``0x00200000`` by the MTK
BootROM (measured on hardware; ``0x20000000`` is the MT333x window and reads
back as an unmapped bus on this part).  The first ``0x200`` bytes carry an
MTK/holux header block (family string, device type, release name, firmware
size, ...) that is identical in layout across the V1.12 and V1.13 releases, so a
single parser covers both.

No firmware bytes are shipped with this package: every helper here works on a
user-supplied image (the official package ``*.bin`` or a flash dump) and every
test builds its own synthetic image.

Verified facts (``docs/reference/03-firmware-offsets.md``): both V1.12 and V1.13
store the Unix time of the GPS epoch (``315964800``) as a little-endian ``u32``
at ``0x402DC`` (unused) and at ``0x402F4`` (the effective conversion constant),
and carry the ASCII app version (``V1.12`` / ``V1.13``) exactly once.  The
parser therefore derives the version offset from the bytes instead of
hard-coding it.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..errors import FirmwareError, FirmwareProfileError

__all__ = [
    "BUILD_NUMBER_OFFSET",
    "DEVICE_TYPE_OFFSET",
    "DEVICE_TYPE_SIZE",
    "EPOCH_CONSTANT_OFFSETS",
    "FAMILY_OFFSET",
    "FAMILY_SIZE",
    "FIRMWARE_FAMILY",
    "FW_SIZE_OFFSET",
    "GPS_EPOCH_UNIX",
    "HEADER_SIZE",
    "RELEASE_MAJOR_OFFSET",
    "RELEASE_MINOR_OFFSET",
    "RELEASE_NAME_OFFSET",
    "RELEASE_NAME_SIZE",
    "SERIAL_PORT_OFFSET",
    "SERIAL_PORT_SIZE",
    "FirmwareHeader",
    "FirmwareImage",
]

#: Unix seconds at the GPS epoch 1980-01-06 UTC (``0x12D53D80``).
GPS_EPOCH_UNIX = 315964800

#: Little-endian wire form of :data:`GPS_EPOCH_UNIX`.
GPS_EPOCH_BYTES = struct.pack("<I", GPS_EPOCH_UNIX)

#: Where the epoch constant is known to live in the M-241 images.  Only the
#: second entry (``0x402F4``) is effective; patching both double-shifts time.
EPOCH_CONSTANT_OFFSETS = (0x402DC, 0x402F4)

#: The MTK-style header block at the start of the image.
HEADER_SIZE = 0x200

FAMILY_OFFSET = 0x90
FAMILY_SIZE = 0x20

#: Little-endian ``u32`` with the firmware payload length (file size - 4).
FW_SIZE_OFFSET = 0xF4

#: Little-endian ``u16`` build number (e.g. ``51`` / ``67`` in V1.12/V1.13).
BUILD_NUMBER_OFFSET = 0x108

RELEASE_NAME_OFFSET = 0x148
RELEASE_NAME_SIZE = 0x08

RELEASE_MAJOR_OFFSET = 0x15A
RELEASE_MINOR_OFFSET = 0x15B

SERIAL_PORT_OFFSET = 0x190
SERIAL_PORT_SIZE = 0x10

DEVICE_TYPE_OFFSET = 0x1B0
DEVICE_TYPE_SIZE = 0x10

#: Family string every M-241 image carries.
FIRMWARE_FAMILY = "HOLUX_M-241"

_VERSION_RE = re.compile(rb"V1\.[0-9]{2}")

#: ``__DATE__``-style build timestamp, e.g. ``Feb  9 2006 10:53:05``.
_BUILD_DATE_RE = re.compile(
    rb"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    rb"[ ]+[0-9]{1,2}[ ]+[0-9]{4}[ ]+[0-9]{2}:[0-9]{2}:[0-9]{2}"
)


def _ascii_field(data: bytes, offset: int, size: int) -> str:
    """Decode a NUL-padded ASCII header string."""
    chunk = data[offset : offset + size].split(b"\x00", 1)[0]
    return chunk.decode("ascii", "replace")


def _build_date(data: bytes) -> str:
    """Best-effort build timestamp search in the header block."""
    match = _BUILD_DATE_RE.search(data)
    if match is None:
        return ""
    return match.group(0).decode("ascii", "replace")


@dataclass(frozen=True)
class FirmwareHeader:
    """Decoded MTK/Holux firmware header (first :data:`HEADER_SIZE` bytes)."""

    family: str
    device_type: str
    release_name: str
    release_major: int
    release_minor: int
    build_number: int
    fw_size: int
    build_date: str
    raw: bytes = b""

    @property
    def release(self) -> str:
        """``"B-core_1.20"``-style release label."""
        return f"{self.release_name}_{self.release_major}.{self.release_minor}"


class FirmwareImage:
    """A mutable in-memory firmware image.

    The bytes are exposed through :attr:`data` (a :class:`bytearray`) so patch
    helpers can copy and rewrite them.  Use :meth:`from_bytes` /
    :meth:`from_path` to construct and :meth:`save` to write (atomically).
    """

    #: Mutable image bytes (kept as a ``bytearray`` so patching is cheap).
    data: bytearray

    def __init__(self, data: bytes | bytearray = b"") -> None:
        self.data = bytearray(data)

    # -- constructors -----------------------------------------------------
    @classmethod
    def from_bytes(cls, data: bytes | bytearray) -> FirmwareImage:
        """Wrap raw image bytes (a defensive copy is taken)."""
        return cls(data)

    @classmethod
    def from_path(cls, path: str | Path) -> FirmwareImage:
        """Read an image from disk.

        Raises:
            FirmwareError: the file could not be read.
        """
        source = Path(path)
        try:
            raw = source.read_bytes()
        except OSError as exc:
            raise FirmwareError(f"cannot read firmware image {source}: {exc}") from exc
        return cls(raw)

    # -- inspection -------------------------------------------------------
    def sha256(self) -> str:
        """Lower-case hex SHA256 digest of the image bytes."""
        return hashlib.sha256(bytes(self.data)).hexdigest()

    def header(self) -> FirmwareHeader:
        """Decode the firmware header block.

        Raises:
            FirmwareError: the image is shorter than :data:`HEADER_SIZE`.
        """
        if len(self.data) < HEADER_SIZE:
            raise FirmwareError(
                f"firmware image too short ({len(self.data)} bytes) to contain a "
                f"{HEADER_SIZE}-byte header",
                offset=len(self.data),
            )
        raw = bytes(self.data[:HEADER_SIZE])
        return FirmwareHeader(
            family=_ascii_field(raw, FAMILY_OFFSET, FAMILY_SIZE),
            device_type=_ascii_field(raw, DEVICE_TYPE_OFFSET, DEVICE_TYPE_SIZE),
            release_name=_ascii_field(raw, RELEASE_NAME_OFFSET, RELEASE_NAME_SIZE),
            release_major=raw[RELEASE_MAJOR_OFFSET],
            release_minor=raw[RELEASE_MINOR_OFFSET],
            build_number=struct.unpack_from("<H", raw, BUILD_NUMBER_OFFSET)[0],
            fw_size=struct.unpack_from("<I", raw, FW_SIZE_OFFSET)[0],
            build_date=_build_date(raw),
            raw=raw,
        )

    def version_offset(self) -> int | None:
        """Offset of the first ASCII ``V1.xx`` run, or ``None``."""
        match = _VERSION_RE.search(self.data)
        return None if match is None else match.start()

    def version_string(self) -> str | None:
        """The ASCII app version (``"V1.12"``, ``"V1.13"``) or ``None``."""
        offset = self.version_offset()
        if offset is None:
            return None
        return bytes(self.data[offset : offset + 5]).decode("ascii", "replace")

    def epoch_offsets(self) -> list[int]:
        """Every offset where the LE32 GPS epoch constant occurs, ascending."""
        offsets: list[int] = []
        index = self.data.find(GPS_EPOCH_BYTES)
        while index != -1:
            offsets.append(index)
            index = self.data.find(GPS_EPOCH_BYTES, index + 1)
        return offsets

    def estimate_log_record_bytes(self) -> int | None:
        """Log record size implied by the detected profile, else ``None``.

        V1.12 and older write 16-byte records, V1.13 writes 20-byte records.
        Images that match no shipped profile (for example an already patched
        ``V1.14`` image) return ``None``.
        """
        from .profiles import detect_profile

        try:
            return detect_profile(self).log_record_bytes
        except FirmwareProfileError:
            return None

    # -- output -----------------------------------------------------------
    def to_bytes(self) -> bytes:
        """A plain :class:`bytes` copy of the image."""
        return bytes(self.data)

    def save(self, path: str | Path) -> None:
        """Write the image to ``path`` atomically (temp file + ``os.replace``).

        Parent directories are created.  The temporary file lives next to the
        target so the final replace is atomic on the same filesystem; it is
        removed when anything fails, leaving an existing target untouched.

        Raises:
            FirmwareError: the image could not be written.
        """
        target = Path(path)
        tmp_name: str | None = None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            handle, tmp_name = tempfile.mkstemp(
                prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
            )
            with os.fdopen(handle, "wb") as stream:
                stream.write(bytes(self.data))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp_name, target)
            tmp_name = None
        except OSError as exc:
            raise FirmwareError(f"cannot write firmware image {target}: {exc}") from exc
        finally:
            if tmp_name is not None:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_name)

    # -- dunders ----------------------------------------------------------
    def __len__(self) -> int:
        return len(self.data)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FirmwareImage):
            return NotImplemented
        return self.data == other.data

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"FirmwareImage(size={len(self.data)})"
