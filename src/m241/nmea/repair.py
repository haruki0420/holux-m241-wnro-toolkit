"""GPS week-number-rollover (WNRO) repair for NMEA streams.

Background: the M-241 firmware converts GPS time to Unix time with the epoch
constant ``315964800``.  GPS week numbers rolled over on 2019-04-06 and the
firmware does not know about it, so a broken unit reports every date exactly
``1024`` weeks (``7168`` days, ``619315200`` seconds) in the past.  A device
that should say *2026-05-03* says *2006-09-17* instead.

The fix is to add the shift back to every date-bearing sentence and recompute
its checksum.  :func:`repair_sentence` also handles the reverse situation:
with ``auto=True`` it only shifts dates that look broken, so a stream from a
healthy receiver passes through untouched.

The observed sentence from the original blog post::

    $GPRMC,022127.542,V,8960.0000,N,00000.0000,E,0.00,0.00,170906,,,N*7F
    -> $GPRMC,022127.542,V,8960.0000,N,00000.0000,E,0.00,0.00,030526,,,N*<crc>

Non-NMEA lines (the M-241 prints C++ debug banners such as
``======Setting_ProcessKeys========``) are forwarded verbatim.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import date, datetime, timedelta

from ..errors import NmeaError
from .sentence import (
    checksum,
    parse_rmc,
    parse_zda,
    sentence_checksum,
    split,
    with_checksum,
)

#: One GPS week-number-rollover period is 1024 weeks.
WNRO_WEEKS = 1024
WNRO_DAYS = 7168  # = 1024 * 7
WNRO_SECONDS = 619315200  # = WNRO_DAYS * 86400

#: Dates a WNRO-broken M-241 can report (inclusive).  A healthy device is far
#: outside this range today: the last day is the actual 2019 rollover date.
STALE_WINDOW_START = date(1999, 8, 22)
STALE_WINDOW_END = date(2019, 4, 6)

#: Auto mode also requires the wall clock to be at least this many days ahead
#: (~19.0 years).  The true shift is 19.62 years, so this only rejects dates
#: that are genuinely close to today.
AUTO_MIN_GAP_DAYS = 6939

#: Field index of the ``ddmmyy`` date inside an RMC sentence (after the type).
RMC_DATE_FIELD = 8

#: Field indices of ``dd``, ``mm``, ``yyyy`` inside a ZDA sentence.
ZDA_DAY_FIELD = 1
ZDA_MONTH_FIELD = 2
ZDA_YEAR_FIELD = 3


def _as_date(value: date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise NmeaError(f"expected a date, not {type(value).__name__}")


def shift_days(date_value: date | datetime, days: int = WNRO_DAYS) -> date:
    """Return *date_value* shifted by *days* days."""
    return _as_date(date_value) + timedelta(days=int(days))


#: Alias matching the contract wording in older docs.
shift_date = shift_days


def needs_wnro_fix(
    date_value: date | datetime, *, wall_clock: date | datetime | None = None
) -> bool:
    """Return True when *date_value* looks like it came from a WNRO-broken unit.

    A date qualifies when it falls inside the stale window
    (:data:`STALE_WINDOW_START` .. :data:`STALE_WINDOW_END`) **and** either no
    wall clock was supplied or the wall clock is more than
    :data:`AUTO_MIN_GAP_DAYS` (~19 years) ahead of it.  A modern date, or a
    stale-looking date from a unit whose clock genuinely is in that era, is
    left alone.
    """
    moment = _as_date(date_value)
    if not STALE_WINDOW_START <= moment <= STALE_WINDOW_END:
        return False
    if wall_clock is None:
        return True
    return (_as_date(wall_clock) - moment).days > AUTO_MIN_GAP_DAYS


def _resolve_shift(
    date_value: date,
    *,
    offset_days: int,
    auto: bool,
    wall_clock: date | datetime | None,
) -> int:
    """Return the number of days to add to *date_value* (0 = leave unchanged)."""
    if offset_days:
        return int(offset_days)
    if auto and needs_wnro_fix(date_value, wall_clock=wall_clock):
        return WNRO_DAYS
    return 0


def _split_line_ending(line: str) -> tuple[str, str]:
    text = str(line)
    stripped = text.rstrip("\r\n")
    return stripped, text[len(stripped) :]


def _parse_rmc_date(fields: list[str]) -> date | None:
    if len(fields) <= RMC_DATE_FIELD:
        return None
    text = fields[RMC_DATE_FIELD]
    if len(text) != 6 or not text.isdigit():
        return None
    day, month, year = int(text[0:2]), int(text[2:4]), int(text[4:6])
    year += 2000 if year < 70 else 1900
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _parse_zda_date(fields: list[str]) -> date | None:
    try:
        text_day = fields[ZDA_DAY_FIELD]
        text_month = fields[ZDA_MONTH_FIELD]
        text_year = fields[ZDA_YEAR_FIELD]
    except IndexError:
        return None
    if not (text_day.isdigit() and text_month.isdigit() and text_year.isdigit()):
        return None
    try:
        return date(int(text_year), int(text_month), int(text_day))
    except ValueError:
        return None


def _rebuild(kind: str, fields: list[str], *, had_checksum: bool) -> str:
    body = kind + ("," + ",".join(fields) if fields else "")
    return with_checksum(body) if had_checksum else "$" + body


def repair_sentence(
    line: str,
    *,
    offset_days: int = 0,
    auto: bool = True,
    wall_clock: date | datetime | None = None,
) -> tuple[str, bool]:
    """Repair the date inside one NMEA line.

    Only date-bearing sentences are modified: ``RMC`` (field 9, ``ddmmyy``)
    and ``ZDA`` (day/month/year fields).  Everything else -- GGA/GSA/GSV/VTG,
    proprietary PMTK chatter and the device's plain-text debug output -- is
    returned byte-for-byte unchanged.

    Args:
        line: One line, with or without its line terminator.
        offset_days: Explicit shift in days.  ``0`` (the default) means "decide
            from *auto* / *wall_clock*"; any non-zero value always applies.
        auto: When True and *offset_days* is 0, shift only dates that look
            WNRO-broken (see :func:`needs_wnro_fix`).
        wall_clock: Today's date, used by the auto heuristic.  ``None`` makes
            auto mode shift every date inside the stale window.

    Returns:
        ``(line, changed)``.  The original line terminator is preserved and the
        checksum is recomputed only when a field actually changed.
    """
    text, ending = _split_line_ending(line)
    if not text.startswith("$"):
        return line, False
    try:
        kind, fields = split(text)
    except NmeaError:
        return line, False
    upper = kind.upper()
    if upper.endswith("RMC"):
        old_date = _parse_rmc_date(fields)
        if old_date is None:
            return line, False
        days = _resolve_shift(old_date, offset_days=offset_days, auto=auto, wall_clock=wall_clock)
        if not days:
            return line, False
        new_date = old_date + timedelta(days=days)
        fields = list(fields)
        fields[RMC_DATE_FIELD] = new_date.strftime("%d%m%y")
    elif upper.endswith("ZDA"):
        old_date = _parse_zda_date(fields)
        if old_date is None:
            return line, False
        days = _resolve_shift(old_date, offset_days=offset_days, auto=auto, wall_clock=wall_clock)
        if not days:
            return line, False
        new_date = old_date + timedelta(days=days)
        fields = list(fields)
        fields[ZDA_DAY_FIELD] = f"{new_date.day:02d}"
        fields[ZDA_MONTH_FIELD] = f"{new_date.month:02d}"
        fields[ZDA_YEAR_FIELD] = f"{new_date.year:04d}"
    else:
        return line, False

    had_checksum = sentence_checksum(text) is not None
    return _rebuild(kind, fields, had_checksum=had_checksum) + ending, True


def repair_stream(lines: Iterable[str], **kw) -> Iterator[str]:
    """Yield every line of *lines* with :func:`repair_sentence` applied.

    Accepts ``offset_days``, ``auto`` and ``wall_clock`` keyword arguments.
    Line endings are preserved; no extra blank lines are introduced.
    """
    for line in lines:
        repaired, _changed = repair_sentence(line, **kw)
        yield repaired


def repair_text(text: str, **kw) -> str:
    """Apply :func:`repair_stream` to a whole text buffer."""
    return "".join(repair_stream(str(text).splitlines(keepends=True), **kw))


def sentence_kind(line: str) -> str | None:
    """Return the NMEA sentence type of *line* (``"GPRMC"``), or ``None``."""
    text = str(line).strip("\r\n")
    if not text.startswith("$"):
        return None
    try:
        kind, _fields = split(text)
    except NmeaError:
        return None
    return kind


def date_of(line: str) -> date | None:
    """Return the date carried by an RMC/ZDA line, or ``None``.

    Useful for diagnostics: ``date_of("$GPRMC,...,170906,,,N*7F")`` is
    ``date(2006, 9, 17)``.
    """
    try:
        kind, fields = split(str(line).strip("\r\n"))
    except NmeaError:
        return None
    upper = kind.upper()
    if upper.endswith("RMC"):
        return _parse_rmc_date(fields)
    if upper.endswith("ZDA"):
        return _parse_zda_date(fields)
    return None


def checksum_of(line: str) -> int | None:
    """Return the checksum *line* carries (``None`` when it has none)."""
    text = str(line).strip("\r\n")
    carried = sentence_checksum(text)
    if carried is None:
        return None
    return carried


def recompute_checksum(line: str) -> str:
    """Return *line* with its checksum recomputed from scratch."""
    text, ending = _split_line_ending(line)
    if not text.startswith("$"):
        return line
    kind, fields = split(text)
    return _rebuild(kind, list(fields), had_checksum=True) + ending


__all__ = [
    "AUTO_MIN_GAP_DAYS",
    "RMC_DATE_FIELD",
    "STALE_WINDOW_END",
    "STALE_WINDOW_START",
    "WNRO_DAYS",
    "WNRO_SECONDS",
    "WNRO_WEEKS",
    "ZDA_DAY_FIELD",
    "ZDA_MONTH_FIELD",
    "ZDA_YEAR_FIELD",
    "checksum_of",
    "date_of",
    "needs_wnro_fix",
    "parse_rmc",
    "parse_zda",
    "recompute_checksum",
    "repair_sentence",
    "repair_stream",
    "repair_text",
    "sentence_kind",
    "shift_date",
    "shift_days",
    "checksum",
]
