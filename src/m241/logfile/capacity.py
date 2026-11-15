"""Flash capacity arithmetic for Holux M-241 log dumps.

The M-241 divides its log flash into 64 KiB blocks.  Each block reserves a
0x200-byte header, so the payload area per block is ``0x10000 - 0x200`` bytes
(65024).  That gives the honest point counts::

    2 MiB -> 32 blocks -> 2,080,768 usable bytes
        16 B/record -> 130,048 points   (marketing: "130,000")
        20 B/record -> 104,038 points   (V1.13 record format)
    4 MiB -> 64 blocks -> 4,161,536 usable bytes
        16 B/record -> 260,096 points
        20 B/record -> 208,076 points
    512 KiB -> 8 blocks -> 520,192 usable bytes
        16 B/record -> 32,512 points
        20 B/record -> 26,009 points

The 512 KiB row exists because the download agent on a retail unit reports
``flash_size = 0x80000`` (MX29LV400CB, ``flash_device_id = 0x49``).  That is the
chip it can program, which is where the firmware image lives; the log area is
evidently separate or larger, because a unit was measured holding 212,860 bytes
of log alongside a 406,620-byte firmware image.  See
``docs/reference/05-features.md``.  Do not treat the 512 KiB row as the log
capacity of a retail unit without measuring it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..errors import M241Error

#: Known flash sizes in bytes, keyed by the shorthand used on the CLI.
FLASH_SIZES: dict[str, int] = {"512K": 0x80000, "2M": 0x200000, "4M": 0x400000}

BLOCK_SIZE = 0x10000
BLOCK_HEADER = 0x200
USABLE_PER_BLOCK = BLOCK_SIZE - BLOCK_HEADER  # 65024

#: DA ``flash_device_id`` (a **byte** in the info report) -> size key.
#: Measured on a retail M-241: ``0x49`` with ``flash_size = 0x80000``.
KNOWN_FLASH_DEVICE_IDS: dict[int, str] = {
    0x49: "512K",  # MXIC MX29LV400CB, 512 KiB (measured on a retail M-241)
}

#: DA ``flash_device_code`` (a **16-bit** field, not the byte id) -> size key.
#: The same retail unit reports ``0x22BA`` for its MX29LV400CB.
KNOWN_FLASH_DEVICE_CODES: dict[int, str] = {
    0x22BA: "512K",  # MXIC MX29LV400CB, 512 KiB (measured on a retail M-241)
}

#: Documented record sizes of the two firmware families.
RECORD_BYTES_V112 = 16
RECORD_BYTES_V113 = 20


@dataclass(frozen=True)
class Capacity:
    """How many points a flash chip of a given geometry can hold.

    The contract allows the duration estimate to be either a callable field
    ``seconds_at`` or a method ``duration(interval_s)``; both are exposed here
    and :attr:`seconds_at` is a property bound to :meth:`duration`, so any
    ``Capacity`` instance (including one built by hand) supports it.
    """

    flash_bytes: int
    blocks: int
    usable_bytes: int
    record_bytes: int
    points: int

    @property
    def flash_name(self) -> str:
        """Shorthand name of the flash size (``"2M"``/``"4M"`` or a byte count)."""
        for name, size in FLASH_SIZES.items():
            if size == self.flash_bytes:
                return name
        return f"{self.flash_bytes}"

    def duration(self, interval_s: float) -> float:
        """Estimated recording duration in seconds at *interval_s* seconds/point."""
        if interval_s <= 0:
            raise M241Error("logging interval must be positive")
        return self.points * float(interval_s)

    def duration_hours(self, interval_s: float) -> float:
        """Estimated recording duration in hours at *interval_s* seconds/point."""
        return self.duration(interval_s) / 3600.0

    def duration_days(self, interval_s: float) -> float:
        """Estimated recording duration in days at *interval_s* seconds/point."""
        return self.duration(interval_s) / 86400.0

    def duration_estimate(self, interval_s: float) -> dict[str, float]:
        """Return a ``{"seconds", "hours", "days"}`` duration estimate dict."""
        seconds = self.duration(interval_s)
        return {"seconds": seconds, "hours": seconds / 3600.0, "days": seconds / 86400.0}

    @property
    def seconds_at(self) -> Callable[[float], float]:
        """Callable form of :meth:`duration` (part of the frozen contract)."""
        return self.duration

    def as_dict(self, interval_s: float | None = None) -> dict[str, object]:
        """Return a JSON-serialisable summary; includes a duration when asked."""
        data: dict[str, object] = {
            "flash": self.flash_name,
            "flash_bytes": self.flash_bytes,
            "blocks": self.blocks,
            "usable_bytes": self.usable_bytes,
            "record_bytes": self.record_bytes,
            "points": self.points,
        }
        if interval_s is not None:
            estimate = self.duration_estimate(interval_s)
            data["interval_s"] = float(interval_s)
            data["duration_s"] = estimate["seconds"]
            data["duration_hours"] = estimate["hours"]
            data["duration_days"] = estimate["days"]
        return data


def resolve_flash(flash: str | int) -> int:
    """Return the flash size in bytes for a ``"2M"``/``"4M"`` shorthand or a number.

    Numerical values (including their hexadecimal-string spellings) are taken
    as a byte count when they are a positive multiple of 64 KiB.  Raises
    :class:`~m241.errors.M241Error` for anything unrecognised.
    """
    if isinstance(flash, bool):
        raise M241Error(f"invalid flash size {flash!r}")
    if isinstance(flash, int):
        size = flash
    else:
        text = str(flash).strip()
        key = text.upper().replace("IB", "M").replace("I", "M")
        key = key.replace("MB", "M").replace("BYTES", "").strip()
        if key in FLASH_SIZES:
            return FLASH_SIZES[key]
        text = text.upper()
        try:
            size = int(text, 16) if text.startswith("0X") else int(text, 10)
        except ValueError as exc:
            known = ", ".join(sorted(FLASH_SIZES))
            raise M241Error(f"unknown flash size {flash!r} (expected {known} or bytes)") from exc
    if size <= 0 or size % BLOCK_SIZE:
        raise M241Error(
            f"flash size {size} is not a positive multiple of {BLOCK_SIZE} bytes"
        )
    return size


def capacity(flash: str | int, record_bytes: int, *, interval_s: float | None = None) -> Capacity:
    """Compute the point capacity of *flash* for records of *record_bytes* bytes.

    Args:
        flash: ``"2M"``, ``"4M"``, an explicit byte count, or a DA device id is
            **not** accepted here -- use :func:`flash_name_from_da` for that.
        record_bytes: Size of one log record in bytes (16 for V1.12, 20 for V1.13).
        interval_s: Optional logging interval used by :meth:`Capacity.duration`.

    Returns:
        A :class:`Capacity` with the exact arithmetic (never the marketing number).
    """
    if not isinstance(record_bytes, int) or isinstance(record_bytes, bool):
        raise M241Error(f"record_bytes must be an int, not {type(record_bytes).__name__}")
    if record_bytes <= 0:
        raise M241Error("record_bytes must be positive")
    flash_bytes = resolve_flash(flash)
    blocks = flash_bytes // BLOCK_SIZE
    usable = blocks * USABLE_PER_BLOCK
    cap = Capacity(
        flash_bytes=flash_bytes,
        blocks=blocks,
        usable_bytes=usable,
        record_bytes=record_bytes,
        points=usable // record_bytes,
    )
    if interval_s is not None:
        # Validate eagerly so a bad interval fails at construction time.
        cap.duration(interval_s)
    return cap


def capacity_table(*, interval_s: float | None = None) -> list[Capacity]:
    """Return the 512K/2M/4M x 16B/20B capacity table in a stable order."""
    rows: list[Capacity] = []
    for flash in ("512K", "2M", "4M"):
        for record_bytes in (RECORD_BYTES_V112, RECORD_BYTES_V113):
            rows.append(capacity(flash, record_bytes, interval_s=interval_s))
    return rows


def record_bytes_for_version(version: str) -> int:
    """Return the record size used by a firmware version string.

    ``"V1.12"`` and older -> 16 bytes, ``"V1.13"`` and later -> 20 bytes.
    A bare ``"1.12"``/``"1.13"`` is accepted as well.
    """
    text = str(version).strip().upper()
    if text.startswith("V"):
        text = text[1:]
    parts = text.split(".")
    try:
        numbers = [int(part) for part in parts[:2]]
    except ValueError as exc:
        raise M241Error(f"cannot parse firmware version {version!r}") from exc
    if not numbers:
        raise M241Error(f"cannot parse firmware version {version!r}")
    major = numbers[0]
    minor = numbers[1] if len(numbers) > 1 else 0
    if (major, minor) < (1, 13):
        return RECORD_BYTES_V112
    return RECORD_BYTES_V113


def estimate_points(flash: str | int, fw_version: str) -> int:
    """Return the number of points *flash* can hold running *fw_version*."""
    return capacity(flash, record_bytes_for_version(fw_version)).points


def flash_name_from_da(flash_device_id: int) -> str | None:
    """Map a DA ``flash_device_id`` to a flash-size shorthand (``"512K"``).

    ``flash_device_id`` is the **byte** field of the info report.  The 16-bit
    ``flash_device_code`` is a different field with its own table; use
    :func:`flash_name_from_code` for it.

    Returns ``None`` for ids that are not in the verified table; callers should
    fall back to the DA ``flash_size`` field instead of guessing.
    """
    if not isinstance(flash_device_id, int) or isinstance(flash_device_id, bool):
        raise M241Error("flash_device_id must be an int")
    return KNOWN_FLASH_DEVICE_IDS.get(flash_device_id)


def flash_name_from_code(flash_device_code: int) -> str | None:
    """Map a DA ``flash_device_code`` (16-bit) to a flash-size shorthand."""
    if not isinstance(flash_device_code, int) or isinstance(flash_device_code, bool):
        raise M241Error("flash_device_code must be an int")
    return KNOWN_FLASH_DEVICE_CODES.get(flash_device_code)


def flash_bytes_from_da(flash_device_id: int) -> int | None:
    """Return the flash size in bytes for a DA ``flash_device_id`` (or ``None``)."""
    name = flash_name_from_da(flash_device_id)
    return FLASH_SIZES[name] if name else None


# Alias used by the DA report decoder name variant in the ARCHITECTURE doc.
def flash_name_from_id(flash_device_id: int) -> str | None:
    """Alias of :func:`flash_name_from_da`."""
    return flash_name_from_da(flash_device_id)


__all__ = [
    "BLOCK_HEADER",
    "BLOCK_SIZE",
    "Capacity",
    "FLASH_SIZES",
    "KNOWN_FLASH_DEVICE_CODES",
    "KNOWN_FLASH_DEVICE_IDS",
    "RECORD_BYTES_V112",
    "RECORD_BYTES_V113",
    "USABLE_PER_BLOCK",
    "capacity",
    "capacity_table",
    "estimate_points",
    "flash_bytes_from_da",
    "flash_name_from_code",
    "flash_name_from_da",
    "flash_name_from_id",
    "record_bytes_for_version",
    "resolve_flash",
]
