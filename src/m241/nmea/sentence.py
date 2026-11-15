"""NMEA 0183 sentence helpers: checksums, splitting and RMC/GGA parsing.

Only the small subset needed by the M-241 toolkit is implemented; no sentence
type is rejected merely because it is unknown -- :func:`split` works for every
``$...*XX`` line and the typed parsers are strict only about their own format.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time

from ..errors import NmeaError

#: XOR of every character between ``'$'`` and ``'*'``.
CHECKSUM_HEX_DIGITS = "0123456789ABCDEF"


class TalkerId(str):
    """A talker id such as ``"GP"`` (kept as ``str`` for convenience)."""


def checksum(body: str) -> int:
    """Return the NMEA checksum (XOR) of *body*.

    *body* must be the text between ``'$'`` and ``'*'`` (the delimiters are
    tolerated and ignored if present).
    """
    text = str(body).strip("\r\n")
    if text.startswith("$"):
        text = text[1:]
    star = text.find("*")
    if star >= 0:
        text = text[:star]
    value = 0
    for char in text:
        value ^= ord(char)
    return value


def checksum_hex(body: str) -> str:
    """Return the two-digit uppercase hexadecimal checksum of *body*."""
    return f"{checksum(body):02X}"


def with_checksum(body: str) -> str:
    """Return ``"$<body>*XX"`` with the checksum appended.

    A leading ``'$'`` or a trailing ``'*XX'`` in *body* is tolerated and
    replaced, so ``with_checksum(with_checksum(b))`` is idempotent.
    """
    text = str(body).strip("\r\n")
    if text.startswith("$"):
        text = text[1:]
    star = text.find("*")
    if star >= 0:
        text = text[:star]
    return f"${text}*{checksum_hex(text)}"


def split(sentence: str) -> tuple[str, list[str]]:
    """Split an NMEA sentence into ``(type, fields)``.

    Args:
        sentence: A line such as ``"$GPRMC,022127.542,V,...*7F"`` (a trailing
            newline is ignored).

    Returns:
        ``("GPRMC", ["022127.542", "V", ...])`` -- the type is whatever
        precedes the first comma, without ``'$'``.

    Raises:
        NmeaError: when the line is not an NMEA sentence.
    """
    text = str(sentence).strip("\r\n")
    if not text.startswith("$"):
        raise NmeaError(f"not an NMEA sentence: {text[:40]!r}")
    body = text[1:]
    star = body.find("*")
    if star >= 0:
        body = body[:star]
    parts = body.split(",")
    kind = parts[0].strip()
    if not kind:
        raise NmeaError(f"empty NMEA sentence type in {text[:40]!r}")
    return kind, parts[1:]


def sentence_checksum(sentence: str) -> int | None:
    """Return the checksum carried by *sentence*, or ``None`` when absent."""
    text = str(sentence).strip("\r\n")
    star = text.rfind("*")
    if star < 0 or len(text) < star + 3:
        return None
    try:
        return int(text[star + 1 : star + 3], 16)
    except ValueError:
        return None


def is_valid(sentence: str) -> bool:
    """Return True when *sentence* is NMEA and its checksum matches.

    Non-NMEA lines (the M-241's C++ debug strings, for example) simply return
    ``False`` -- this function never raises.
    """
    text = str(sentence).strip("\r\n")
    if not text.startswith("$"):
        return False
    expected = sentence_checksum(text)
    if expected is None:
        return False
    try:
        kind, _fields = split(text)
    except NmeaError:
        return False
    if not kind:
        return False
    return expected == checksum(text)


def _to_float(text: str) -> float | None:
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _to_int(text: str) -> int | None:
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _parse_time(text: str) -> time | None:
    """Parse ``hhmmss`` or ``hhmmss.sss`` into a :class:`datetime.time`."""
    if len(text) < 6:
        return None
    try:
        hour = int(text[0:2])
        minute = int(text[2:4])
        second = int(text[4:6])
        micro = 0
        if len(text) > 7 and text[6] == ".":
            fraction = (text[7:] + "000000")[:6]
            micro = int(fraction)
        return time(hour, minute, second, micro)
    except ValueError:
        return None


def _parse_date(text: str) -> date | None:
    """Parse ``ddmmyy`` into a :class:`datetime.date` with the 1970 pivot."""
    if len(text) != 6 or not text.isdigit():
        return None
    day, month, year = int(text[0:2]), int(text[2:4]), int(text[4:6])
    year += 2000 if year < 70 else 1900
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _parse_latlon(
    value: str, hemisphere: str, *, lat: bool
) -> float | None:
    """Convert a ``ddmm.mmmm``/``dddmm.mmmm`` pair into signed decimal degrees."""
    if not value:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    degrees = int(number / 100)
    minutes = number - degrees * 100
    result = degrees + minutes / 60.0
    if hemisphere.upper() in ("S", "W"):
        result = -result
    limit = 90.0 if lat else 180.0
    if abs(result) > limit:
        return None
    return result


def _field(fields: list[str], index: int) -> str:
    return fields[index] if index < len(fields) else ""


@dataclass
class Rmc:
    """A parsed ``$--RMC`` (recommended minimum navigation information) sentence."""

    utc: time | None
    status: str
    lat: float | None
    lon: float | None
    speed_kn: float | None
    course: float | None
    date: date | None
    mag_var: float | None = None
    mag_var_dir: str = ""
    mode: str = ""
    talker: str = ""
    raw: str = ""

    @property
    def active(self) -> bool:
        """True when the fix status field is ``'A'`` (valid)."""
        return self.status.upper() == "A"

    @property
    def position(self) -> tuple[float, float] | None:
        """``(lat, lon)`` when both are present."""
        if self.lat is None or self.lon is None:
            return None
        return (self.lat, self.lon)


def parse_rmc(sentence: str) -> Rmc:
    """Parse an RMC sentence.

    Raises:
        NmeaError: when the sentence is not RMC or has too few fields.
    """
    kind, fields = split(sentence)
    if not kind.endswith("RMC"):
        raise NmeaError(f"not an RMC sentence: {kind!r}")
    if len(fields) < 9:
        raise NmeaError(f"RMC sentence has {len(fields)} fields, expected at least 9")
    talker = kind[:-3]
    return Rmc(
        utc=_parse_time(_field(fields, 0)),
        status=_field(fields, 1) or "V",
        lat=_parse_latlon(_field(fields, 2), _field(fields, 3), lat=True),
        lon=_parse_latlon(_field(fields, 4), _field(fields, 5), lat=False),
        speed_kn=_to_float(_field(fields, 6)),
        course=_to_float(_field(fields, 7)),
        date=_parse_date(_field(fields, 8)),
        mag_var=_to_float(_field(fields, 9)),
        mag_var_dir=_field(fields, 10),
        mode=_field(fields, 11),
        talker=talker,
        raw=str(sentence).strip("\r\n"),
    )


def parse_gga(sentence: str) -> dict[str, object]:
    """Parse a GGA sentence into a plain dictionary.

    Keys: ``kind``, ``utc``, ``lat``, ``lon``, ``quality``, ``sats``,
    ``hdop``, ``altitude_m``, ``geoid_m``, ``dgps_age``, ``station_id``.
    Raises :class:`~m241.errors.NmeaError` for non-GGA or truncated input.
    """
    kind, fields = split(sentence)
    if not kind.endswith("GGA"):
        raise NmeaError(f"not a GGA sentence: {kind!r}")
    if len(fields) < 9:
        raise NmeaError(f"GGA sentence has {len(fields)} fields, expected at least 9")
    return {
        "kind": kind,
        "utc": _parse_time(_field(fields, 0)),
        "lat": _parse_latlon(_field(fields, 1), _field(fields, 2), lat=True),
        "lon": _parse_latlon(_field(fields, 3), _field(fields, 4), lat=False),
        "quality": _to_int(_field(fields, 5)),
        "sats": _to_int(_field(fields, 6)),
        "hdop": _to_float(_field(fields, 7)),
        "altitude_m": _to_float(_field(fields, 8)),
        "geoid_m": _to_float(_field(fields, 10)),
        "dgps_age": _to_float(_field(fields, 12)),
        "station_id": _field(fields, 13),
    }


@dataclass
class Gpzda:
    """A parsed ``$--ZDA`` (time and date) sentence."""

    utc: time | None
    date: date | None
    local_zone_hours: int | None = None
    local_zone_minutes: int | None = None
    talker: str = ""
    extra: dict[str, object] = field(default_factory=dict)


def parse_zda(sentence: str) -> Gpzda:
    """Parse a ZDA sentence (time and full date, including local zone)."""
    kind, fields = split(sentence)
    if not kind.endswith("ZDA"):
        raise NmeaError(f"not a ZDA sentence: {kind!r}")
    if len(fields) < 3:
        raise NmeaError(f"ZDA sentence has {len(fields)} fields, expected at least 3")
    day = _to_int(_field(fields, 1))
    month = _to_int(_field(fields, 2))
    year = _to_int(_field(fields, 3))
    parsed_date: date | None = None
    if day and month and year:
        try:
            parsed_date = date(year, month, day)
        except ValueError:
            parsed_date = None
    return Gpzda(
        utc=_parse_time(_field(fields, 0)),
        date=parsed_date,
        local_zone_hours=_to_int(_field(fields, 4)),
        local_zone_minutes=_to_int(_field(fields, 5)),
        talker=kind[:-3],
    )


__all__ = [
    "CHECKSUM_HEX_DIGITS",
    "Gpzda",
    "Rmc",
    "TalkerId",
    "checksum",
    "checksum_hex",
    "is_valid",
    "parse_gga",
    "parse_rmc",
    "parse_zda",
    "sentence_checksum",
    "split",
    "with_checksum",
]
