"""Binary record layout of the Holux M-241 / MTK GPS-logger format.

The M-241 writes a bitmask-driven binary log into 2 MiB or 4 MiB of serial
flash.  Every 64 KiB block starts with a 0x200-byte header followed by the
records.  A record is a concatenation of the fields selected by the current
bitmask, in ascending bit order from :data:`LOG_BIT_UTC` to
:data:`LOG_BIT_DISTANCE`, followed by a checksum trailer.

Holux M-241 differences from the generic MTK format:

* latitude/longitude are 4-byte little-endian floats (generic MTK: doubles),
* height is 3 bytes: the three most significant bytes of a little-endian
  float32 (the implicit least-significant byte is ``0x00``),
* the trailer is a single XOR checksum byte with **no** ``'*'`` separator
  (generic MTK writes ``'*'`` followed by one checksum byte).

Bit 13 (``SID``) is special: when present the record carries a variable number
of satellite slots instead of a single value, so :func:`record_length` reports
the MTK worst case (32 slots) while the reader derives the *actual* length of
each satellite-bearing record from its satellite count.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ..errors import LogParseError, M241Error

# ---------------------------------------------------------------------------
# MTK log bitmask -- bit index for every field
# ---------------------------------------------------------------------------
LOG_BIT_UTC = 0
LOG_BIT_VALID = 1
LOG_BIT_LATITUDE = 2
LOG_BIT_LONGITUDE = 3
LOG_BIT_HEIGHT = 4
LOG_BIT_SPEED = 5
LOG_BIT_HEADING = 6
LOG_BIT_DSTA = 7
LOG_BIT_DAGE = 8
LOG_BIT_PDOP = 9
LOG_BIT_HDOP = 10
LOG_BIT_VDOP = 11
LOG_BIT_NSAT = 12
LOG_BIT_SID = 13
LOG_BIT_ELEVATION = 14
LOG_BIT_AZIMUTH = 15
LOG_BIT_SNR = 16
LOG_BIT_RCR = 17
LOG_BIT_MILLISECOND = 18
LOG_BIT_DISTANCE = 19

LOG_FIELD_NAMES: dict[int, str] = {
    LOG_BIT_UTC: "UTC",
    LOG_BIT_VALID: "VALID",
    LOG_BIT_LATITUDE: "LATITUDE",
    LOG_BIT_LONGITUDE: "LONGITUDE",
    LOG_BIT_HEIGHT: "HEIGHT",
    LOG_BIT_SPEED: "SPEED",
    LOG_BIT_HEADING: "HEADING",
    LOG_BIT_DSTA: "DSTA",
    LOG_BIT_DAGE: "DAGE",
    LOG_BIT_PDOP: "PDOP",
    LOG_BIT_HDOP: "HDOP",
    LOG_BIT_VDOP: "VDOP",
    LOG_BIT_NSAT: "NSAT",
    LOG_BIT_SID: "SID",
    LOG_BIT_ELEVATION: "ELEVATION",
    LOG_BIT_AZIMUTH: "AZIMUTH",
    LOG_BIT_SNR: "SNR",
    LOG_BIT_RCR: "RCR",
    LOG_BIT_MILLISECOND: "MILLISECOND",
    LOG_BIT_DISTANCE: "DISTANCE",
}

MAX_SATELLITES = 32

# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------
BLOCK_SIZE = 0x10000
BLOCK_HEADER_SIZE = 0x200
LOG_HEADER_FIELDS_SIZE = 20  # meaningful bytes of a 0x200-byte block header

POLICY_LOG_DISABLED = 0x0104
POLICY_LOG_ENABLED = 0x0106

#: Bit 31 is a Holux "low precision" marker that is not a data field.
HOLUX_EXTRA_BIT = 0x80000000

#: Worst-case field sizes on the M-241 (bit -> bytes).
M241_FIELD_SIZES: dict[int, int] = {
    LOG_BIT_UTC: 4,
    LOG_BIT_VALID: 2,
    LOG_BIT_LATITUDE: 4,
    LOG_BIT_LONGITUDE: 4,
    LOG_BIT_HEIGHT: 3,
    LOG_BIT_SPEED: 4,
    LOG_BIT_HEADING: 4,
    LOG_BIT_DSTA: 2,
    LOG_BIT_DAGE: 4,
    LOG_BIT_PDOP: 2,
    LOG_BIT_HDOP: 2,
    LOG_BIT_VDOP: 2,
    LOG_BIT_NSAT: 2,
    LOG_BIT_SID: 4,
    LOG_BIT_ELEVATION: 2,
    LOG_BIT_AZIMUTH: 2,
    LOG_BIT_SNR: 2,
    LOG_BIT_RCR: 2,
    LOG_BIT_MILLISECOND: 2,
    LOG_BIT_DISTANCE: 8,
}

#: Generic MTK loggers use doubles for lat/lon and a 4-byte float height.
MTK_FIELD_SIZES: dict[int, int] = {
    **M241_FIELD_SIZES,
    LOG_BIT_LATITUDE: 8,
    LOG_BIT_LONGITUDE: 8,
    LOG_BIT_HEIGHT: 4,
}

#: Holux GR-245 swaps the meaning of the HEIGHT and SPEED slots.
GR245_FIELD_SIZES: dict[int, int] = {
    **M241_FIELD_SIZES,
    LOG_BIT_HEIGHT: 4,
    LOG_BIT_SPEED: 3,
}

DEVICE_FIELD_SIZES: dict[str, dict[int, int]] = {
    "m241": M241_FIELD_SIZES,
    "gr245": GR245_FIELD_SIZES,
    "mtk": MTK_FIELD_SIZES,
}

#: Trailer size in bytes; the generic MTK '"*"' separator counts as one byte.
DEVICE_CRC_BYTES: dict[str, int] = {"m241": 1, "gr245": 1, "mtk": 2}

DEVICE_ALIASES: dict[str, str] = {
    "m241": "m241",
    "holux": "m241",
    "holux m-241": "m241",
    "m-241": "m241",
    "gr241": "m241",
    "gr-241": "m241",
    "gr245": "gr245",
    "gr-245": "gr245",
    "mtk": "mtk",
    "generic": "mtk",
}

SATELLITE_BITS = (LOG_BIT_ELEVATION, LOG_BIT_AZIMUTH, LOG_BIT_SNR)


def normalize_device(device: str) -> str:
    """Return the canonical device key for *device* (``"m241"``/``"gr245"``/``"mtk"``)."""
    key = DEVICE_ALIASES.get(str(device).strip().lower())
    if key is None:
        known = ", ".join(sorted(DEVICE_FIELD_SIZES))
        raise M241Error(f"unknown log device {device!r} (expected one of: {known})")
    return key


def field_sizes(device: str = "m241") -> dict[int, int]:
    """Return the ``bit -> byte size`` table used by *device*."""
    return DEVICE_FIELD_SIZES[normalize_device(device)]


def crc_bytes(device: str = "m241") -> int:
    """Return the number of trailer bytes used by *device*."""
    return DEVICE_CRC_BYTES[normalize_device(device)]


def effective_bitmask(bitmask: int, *, device: str = "m241") -> int:
    """Clear bits that are not data fields for *device* (Holux low-precision bit)."""
    if normalize_device(device) in ("m241", "gr245"):
        return bitmask & ~HOLUX_EXTRA_BIT
    return bitmask


def record_length(bitmask: int, *, device: str = "m241") -> int:
    """Return the length in bytes of one record for *bitmask*.

    The checksum trailer is included.  Satellite information (bit 13) is
    counted at the MTK worst case of 32 satellite slots when enabled, because
    the real record length depends on how many satellites are in view.

    The two M-241 firmware families are::

        record_length(0x1D)  # UTC | LAT | LON | HEIGHT  -> 16 (V1.12)
        record_length(0x3D)  # ... plus float SPEED      -> 20 (V1.13)
    """
    sizes = field_sizes(device)
    bitmask = effective_bitmask(bitmask, device=device)
    if bitmask == 0:
        return 0
    has_sid = bool(bitmask & (1 << LOG_BIT_SID))
    satellite_extra = sum(sizes[bit] for bit in SATELLITE_BITS if bitmask & (1 << bit))
    length = crc_bytes(device)
    for bit in sorted(sizes):
        if not bitmask & (1 << bit):
            continue
        if has_sid and bit in SATELLITE_BITS:
            continue  # counted inside every satellite slot
        if bit == LOG_BIT_SID:
            length += (sizes[bit] + satellite_extra) * MAX_SATELLITES
        else:
            length += sizes[bit]
    return length


def field_layout(
    payload: bytes, bitmask: int, *, device: str = "m241"
) -> list[tuple[int, int, int]]:
    """Return ``(bit, offset, size)`` for every field of one record.

    ``payload`` must contain at least the record body; when the satellite
    count cannot be read, the worst case of 32 slots is assumed.  The
    variable-length ``SID`` field is returned as one entry whose size covers
    all satellite slots.
    """
    sizes = field_sizes(device)
    bitmask = effective_bitmask(bitmask, device=device)
    if bitmask == 0:
        return []
    has_sid = bool(bitmask & (1 << LOG_BIT_SID))
    satellite_extra = sum(sizes[bit] for bit in SATELLITE_BITS if bitmask & (1 << bit))
    layout: list[tuple[int, int, int]] = []
    pos = 0
    for bit in sorted(sizes):
        if not bitmask & (1 << bit):
            continue
        if has_sid and bit in SATELLITE_BITS:
            continue
        if bit == LOG_BIT_SID:
            count = MAX_SATELLITES
            if len(payload) >= pos + 4:
                count = int.from_bytes(payload[pos + 2 : pos + 4], "little")
                count = max(0, min(count, MAX_SATELLITES))
            size = (sizes[bit] + satellite_extra) * count
        else:
            size = sizes[bit]
        layout.append((bit, pos, size))
        pos += size
    return layout


# ---------------------------------------------------------------------------
# Primitive decoders
# ---------------------------------------------------------------------------
def _u16(raw: bytes) -> int:
    return int.from_bytes(raw[:2], "little")


def _u32(raw: bytes) -> int:
    return int.from_bytes(raw[:4], "little")


def _f32(raw: bytes) -> float:
    return struct.unpack("<f", raw[:4])[0]


def _f64(raw: bytes) -> float:
    return struct.unpack("<d", raw[:8])[0]


def _height_from_u24(raw: bytes) -> float:
    """Decode the M-241 3-byte height (high bytes of a little-endian float32)."""
    return struct.unpack("<f", b"\x00" + bytes(raw[:3]))[0]


def utc_from_epoch(seconds: int) -> datetime | None:
    """Convert MTK's Unix seconds to a tz-aware UTC datetime (``None`` if invalid)."""
    if seconds in (0, 0xFFFFFFFF):
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def decode_field(bit: int, raw: bytes, *, device: str = "m241") -> Any:
    """Decode one field of *bit* from its raw bytes.

    ``UTC`` is returned as Unix seconds (see :func:`utc_from_epoch`), ``NSAT``
    as a ``(view, used)`` tuple; the satellite bits return ``None`` because
    they are only meaningful inside a ``SID`` block.  DOP values are divided
    by 100 already.
    """
    key = normalize_device(device)
    if key == "gr245":
        # The GR-245 keeps a 4-byte speed (cm/s) in the HEIGHT slot and the
        # 3-byte height in the SPEED slot.
        if bit == LOG_BIT_HEIGHT:
            return _u32(raw)
        if bit == LOG_BIT_SPEED:
            return _height_from_u24(raw)
    if bit == LOG_BIT_UTC:
        return _u32(raw)
    if bit == LOG_BIT_VALID:
        return _u16(raw)
    if bit in (LOG_BIT_LATITUDE, LOG_BIT_LONGITUDE):
        return _f64(raw) if key == "mtk" else _f32(raw)
    if bit == LOG_BIT_HEIGHT:
        return _f32(raw) if key == "mtk" else _height_from_u24(raw)
    if bit in (LOG_BIT_SPEED, LOG_BIT_HEADING, LOG_BIT_DAGE):
        return _f32(raw)
    if bit in (LOG_BIT_DSTA, LOG_BIT_RCR, LOG_BIT_MILLISECOND):
        return _u16(raw)
    if bit in (LOG_BIT_PDOP, LOG_BIT_HDOP, LOG_BIT_VDOP):
        return _u16(raw) / 100.0
    if bit == LOG_BIT_NSAT:
        return (int(raw[0]), int(raw[1])) if len(raw) >= 2 else None
    if bit == LOG_BIT_DISTANCE:
        return _f64(raw)
    return None


def xor_checksum(data: bytes) -> int:
    """Return the MTK XOR checksum of *data* (one byte)."""
    value = 0
    for byte in data:
        value ^= byte
    return value


def record_checksum(payload: bytes, field_bytes: int, *, device: str = "m241") -> bool:
    """Return True when the trailer of *payload* matches the XOR of its fields."""
    key = normalize_device(device)
    if field_bytes < 0 or len(payload) < field_bytes + DEVICE_CRC_BYTES[key]:
        return False
    expected = xor_checksum(payload[:field_bytes])
    if key == "mtk":
        return payload[field_bytes] == 0x2A and payload[field_bytes + 1] == expected
    return payload[field_bytes] == expected


@dataclass
class LogRecord:
    """One decoded log record (one track point or one waypoint marker)."""

    index: int
    utc: datetime | None
    valid: int | None
    lat: float | None
    lon: float | None
    height_m: float | None
    speed_kmh: float | None
    heading_deg: float | None
    pdop: float | None
    hdop: float | None
    vdop: float | None
    sats_used: int | None
    sats_view: int | None
    crc_ok: bool = True
    offset: int = 0
    # -- extras (kept after the frozen contract fields, all defaulted)
    rcr: int | None = None
    ms: int | None = None
    distance_m: float | None = None
    waypoint: bool = False

    @property
    def has_fix(self) -> bool:
        """True when the record carries a usable position.

        MTK stores ``VALID == 0x0001`` for "no fix"; a missing VALID field is
        treated as a usable position because lat/lon are present.
        """
        if self.lat is None or self.lon is None:
            return False
        if self.valid is None:
            return True
        return not self.valid & 0x0001

    @property
    def utc_iso(self) -> str | None:
        """Timestamp as an RFC 3339 UTC string (``2026-05-03T02:21:27.542Z``)."""
        if self.utc is None:
            return None
        moment = self.utc.astimezone(timezone.utc)
        text = moment.strftime("%Y-%m-%dT%H:%M:%S")
        if moment.microsecond:
            text += f".{moment.microsecond // 1000:03d}"
        return text + "Z"

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation (used by ``to_json``)."""
        return {
            "index": self.index,
            "utc": self.utc_iso,
            "lat": self.lat,
            "lon": self.lon,
            "height_m": self.height_m,
            "speed_kmh": self.speed_kmh,
            "heading_deg": self.heading_deg,
            "pdop": self.pdop,
            "hdop": self.hdop,
            "vdop": self.vdop,
            "sats_used": self.sats_used,
            "sats_view": self.sats_view,
            "crc_ok": self.crc_ok,
            "offset": self.offset,
            "waypoint": self.waypoint,
        }


@dataclass
class BlockHeader:
    """The meaningful part of the 0x200-byte header of one 64 KiB block."""

    offset: int
    count: int | None
    bitmask: int
    policy: int
    period_ds: int
    distance_dm: int
    speed_dkh: int
    # -- filled in by the reader (not part of the on-disk header)
    used_bytes: int = 0

    @property
    def period_s(self) -> float:
        """Logging interval in seconds (the header stores it multiplied by 10)."""
        return self.period_ds / 10.0

    @property
    def distance_m(self) -> float:
        """Distance logging threshold in metres (the header stores it x10)."""
        return self.distance_dm / 10.0

    @property
    def speed_kmh(self) -> float:
        """Speed logging threshold in km/h (the header stores it x10)."""
        return self.speed_dkh / 10.0

    @property
    def data_offset(self) -> int:
        """Absolute offset of the first record of this block."""
        return self.offset + BLOCK_HEADER_SIZE

    @property
    def is_open(self) -> bool:
        """True when the block is not full (count field is ``0xFFFF``)."""
        return self.count is None

    @property
    def log_enabled(self) -> bool:
        """True when the block records that logging was turned on."""
        return self.policy == POLICY_LOG_ENABLED


def block_header(block: bytes) -> BlockHeader:
    """Parse the 0x200-byte MTK block header.

    The header layout (little endian) is::

        u16 log count in this block (0xFFFF -> block not full)
        u32 log bitmask
        u16 log policy (0x0104 disabled / 0x0106 enabled)
        u32 log period * 10
        u32 log distance * 10
        u32 log speed * 10

    Only the first 20 bytes matter; the rest of the header is padding.
    Raises :class:`~m241.errors.LogParseError` when fewer than 20 bytes are
    supplied.
    """
    raw = bytes(block)[:LOG_HEADER_FIELDS_SIZE]
    if len(raw) < LOG_HEADER_FIELDS_SIZE:
        raise LogParseError(
            f"truncated block header: {len(raw)} of {LOG_HEADER_FIELDS_SIZE} bytes"
        )
    count_raw, bitmask, policy, period_ds, distance_dm, speed_dkh = struct.unpack(
        "<HIHIII", raw
    )
    return BlockHeader(
        offset=0,
        count=None if count_raw == 0xFFFF else count_raw,
        bitmask=bitmask,
        policy=policy,
        period_ds=period_ds,
        distance_dm=distance_dm,
        speed_dkh=speed_dkh,
    )


def build_block_header(
    *,
    count: int | None,
    bitmask: int,
    policy: int = POLICY_LOG_ENABLED,
    period_ds: int = 10,
    distance_dm: int = 0,
    speed_dkh: int = 0,
) -> bytes:
    """Build the first 20 bytes of a block header (helper for tests/tools)."""
    count_raw = 0xFFFF if count is None else count
    return struct.pack(
        "<HIHIII", count_raw, bitmask, policy, period_ds, distance_dm, speed_dkh
    )


def build_record(
    payload: bytes,
    *,
    bitmask: int,
    device: str = "m241",
    broken_crc: bool = False,
) -> bytes:
    """Append the correct checksum trailer to *payload* and return the record."""
    key = normalize_device(device)
    crc = xor_checksum(payload)
    if broken_crc:
        crc ^= 0xFF
    if key == "mtk":
        return payload + b"*" + bytes([crc])
    return payload + bytes([crc])


def decode_record(
    payload: bytes,
    bitmask: int,
    *,
    device: str = "m241",
    index: int = 0,
    offset: int = 0,
    crc_ok: bool = True,
    waypoint: bool = False,
) -> LogRecord:
    """Decode one complete record (fields **and** trailer) into a :class:`LogRecord`."""
    key = normalize_device(device)
    layout = field_layout(payload, bitmask, device=key)
    values: dict[int, Any] = {}
    for bit, pos, size in layout:
        if bit in (LOG_BIT_SID, *SATELLITE_BITS):
            continue
        values[bit] = decode_field(bit, payload[pos : pos + size], device=key)

    utc = utc_from_epoch(values.get(LOG_BIT_UTC, 0))
    ms = values.get(LOG_BIT_MILLISECOND)
    if utc is not None and ms:
        utc = utc + timedelta(milliseconds=ms)
    nsat = values.get(LOG_BIT_NSAT)
    sats_view, sats_used = (nsat if isinstance(nsat, tuple) else (None, None))
    valid = values.get(LOG_BIT_VALID)
    height_m = values.get(LOG_BIT_HEIGHT)
    speed_kmh = values.get(LOG_BIT_SPEED)
    if key == "gr245":
        # The GR-245 firmware swaps the two slots: the HEIGHT slot carries the
        # speed as an unsigned 32-bit cm/s counter, the SPEED slot the height.
        speed_raw = values.get(LOG_BIT_HEIGHT)
        speed_kmh = None if speed_raw is None else speed_raw / 100.0 * 3.6
        height_m = values.get(LOG_BIT_SPEED)
    return LogRecord(
        index=index,
        utc=utc,
        valid=valid,
        lat=values.get(LOG_BIT_LATITUDE),
        lon=values.get(LOG_BIT_LONGITUDE),
        height_m=height_m,
        speed_kmh=speed_kmh,
        heading_deg=values.get(LOG_BIT_HEADING),
        pdop=values.get(LOG_BIT_PDOP),
        hdop=values.get(LOG_BIT_HDOP),
        vdop=values.get(LOG_BIT_VDOP),
        sats_used=sats_used,
        sats_view=sats_view,
        crc_ok=crc_ok,
        offset=offset,
        rcr=values.get(LOG_BIT_RCR),
        ms=ms,
        distance_m=values.get(LOG_BIT_DISTANCE),
        waypoint=waypoint,
    )
