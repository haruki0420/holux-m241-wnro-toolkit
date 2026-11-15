"""NMEA 0183 sentence handling and GPS week-number-rollover (WNRO) repair.

Public API::

    from m241.nmea import checksum, with_checksum, parse_rmc, parse_gga, is_valid
    from m241.nmea import repair_sentence, repair_stream, needs_wnro_fix
    from m241.nmea import NmeaProxy, make_proxy

The M-241 firmware converts GPS time to Unix time with the pre-rollover epoch
constant, so a unit that was never patched reports dates 1024 weeks (7168 days,
619315200 seconds) in the past.  :func:`repair_sentence` adds that shift back
and recomputes the checksum; :class:`NmeaProxy` does it live on a serial stream
and re-serves it over TCP or stdout.
"""

from __future__ import annotations

from .proxy import (
    DEFAULT_BAUD,
    DEFAULT_TCP_HOST,
    DEFAULT_TCP_PORT,
    NmeaProxy,
    SerialLike,
    make_proxy,
    pump_proxy,
    rewrite_lines,
)
from .repair import (
    AUTO_MIN_GAP_DAYS,
    STALE_WINDOW_END,
    STALE_WINDOW_START,
    WNRO_DAYS,
    WNRO_SECONDS,
    WNRO_WEEKS,
    checksum_of,
    date_of,
    needs_wnro_fix,
    recompute_checksum,
    repair_sentence,
    repair_stream,
    repair_text,
    sentence_kind,
    shift_date,
    shift_days,
)
from .sentence import (
    Gpzda,
    Rmc,
    checksum,
    checksum_hex,
    is_valid,
    parse_gga,
    parse_rmc,
    parse_zda,
    sentence_checksum,
    split,
    with_checksum,
)

__all__ = [
    "AUTO_MIN_GAP_DAYS",
    "DEFAULT_BAUD",
    "DEFAULT_TCP_HOST",
    "DEFAULT_TCP_PORT",
    "STALE_WINDOW_END",
    "STALE_WINDOW_START",
    "WNRO_DAYS",
    "WNRO_SECONDS",
    "WNRO_WEEKS",
    "Gpzda",
    "NmeaProxy",
    "Rmc",
    "SerialLike",
    "checksum",
    "checksum_hex",
    "checksum_of",
    "date_of",
    "is_valid",
    "make_proxy",
    "needs_wnro_fix",
    "parse_gga",
    "parse_rmc",
    "parse_zda",
    "pump_proxy",
    "recompute_checksum",
    "repair_sentence",
    "repair_stream",
    "repair_text",
    "rewrite_lines",
    "sentence_checksum",
    "sentence_kind",
    "shift_date",
    "shift_days",
    "split",
    "with_checksum",
]
