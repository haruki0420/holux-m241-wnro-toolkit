"""Tests for GPS week-number-rollover (WNRO) date repair."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from m241.nmea import (
    STALE_WINDOW_END,
    STALE_WINDOW_START,
    WNRO_DAYS,
    WNRO_SECONDS,
    WNRO_WEEKS,
    checksum_of,
    date_of,
    is_valid,
    needs_wnro_fix,
    parse_rmc,
    recompute_checksum,
    repair_sentence,
    repair_stream,
    repair_text,
    sentence_kind,
    shift_days,
    with_checksum,
)

#: The sentence observed in the original blog post.
SAMPLE = "$GPRMC,022127.542,V,8960.0000,N,00000.0000,E,0.00,0.00,170906,,,N*7F"

#: A modern, already-correct sentence (2026-05-03).
MODERN = with_checksum(
    "GPRMC,022127.542,A,3541.1234,N,13946.5678,E,12.5,270.0,030526,1.5,W,A"
)

WALL_CLOCK = date(2026, 11, 15)  # "today" in the auto-mode heuristic


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
def test_constants() -> None:
    assert WNRO_WEEKS == 1024
    assert WNRO_DAYS == 7168
    assert WNRO_SECONDS == 619315200
    assert WNRO_DAYS * 86400 == WNRO_SECONDS
    assert WNRO_WEEKS * 7 == WNRO_DAYS
    assert date(1999, 8, 22) == STALE_WINDOW_START
    assert date(2019, 4, 6) == STALE_WINDOW_END


# ---------------------------------------------------------------------------
# shift_days
# ---------------------------------------------------------------------------
def test_shift_days() -> None:
    assert shift_days(date(2006, 9, 17)) == date(2026, 5, 3)
    # The stale window's first day shifts to just after the 2019 rollover.
    assert shift_days(date(1999, 8, 22)) == date(2019, 4, 7)
    assert shift_days(date(2026, 5, 3), days=0) == date(2026, 5, 3)
    assert shift_days(date(2026, 5, 3), days=-7168) == date(2006, 9, 17)
    assert shift_days(datetime(2006, 9, 17, 2, 21, tzinfo=timezone.utc)) == date(2026, 5, 3)


def test_shift_days_rejects_non_date() -> None:
    with pytest.raises(Exception):  # noqa: B017 - documented NmeaError
        shift_days("2006-09-17")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# needs_wnro_fix
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "value",
    [
        date(1999, 8, 22),
        date(2006, 9, 17),
        date(2018, 1, 1),
        date(2019, 4, 6),
    ],
)
def test_needs_wnro_fix_inside_window(value: date) -> None:
    assert needs_wnro_fix(value) is True


@pytest.mark.parametrize(
    "value",
    [
        date(1999, 8, 21),  # one day before the window
        date(2019, 4, 7),  # one day after the window
        date(2026, 5, 3),  # modern date
        date(2030, 1, 1),
        date(1980, 1, 6),
    ],
)
def test_needs_wnro_fix_outside_window(value: date) -> None:
    assert needs_wnro_fix(value) is False


def test_needs_wnro_fix_requires_19_year_gap_with_wall_clock() -> None:
    stale = date(2006, 9, 17)
    assert needs_wnro_fix(stale, wall_clock=WALL_CLOCK) is True
    # A wall clock only one year ahead means the unit's date is plausible.
    assert needs_wnro_fix(stale, wall_clock=date(2007, 9, 17)) is False
    # Exactly the threshold gap is not yet "broken".
    assert needs_wnro_fix(stale, wall_clock=stale + timedelta(days=6939)) is False
    assert needs_wnro_fix(stale, wall_clock=stale + timedelta(days=6940)) is True


def test_needs_wnro_fix_accepts_datetime() -> None:
    assert needs_wnro_fix(datetime(2006, 9, 17, 2, 21), wall_clock=datetime(2026, 11, 15))


# ---------------------------------------------------------------------------
# repair_sentence: the blog-post sample
# ---------------------------------------------------------------------------
def test_repair_sample_sentence() -> None:
    repaired, changed = repair_sentence(SAMPLE)
    assert changed is True
    assert repaired != SAMPLE
    assert repaired.endswith("\n") is False

    # Date field: 17/09/06 -> 03/05/26 (+7168 days).
    assert "030526" in repaired
    assert "170906" not in repaired

    # Everything else is untouched and the checksum was recomputed.
    assert repaired.startswith("$GPRMC,022127.542,V,8960.0000,N,00000.0000,E,0.00,0.00,")
    assert repaired.endswith(",,,N*" + repaired.rsplit("*", 1)[1])
    assert is_valid(repaired)

    rmc = parse_rmc(repaired)
    assert rmc.date == date(2026, 5, 3)
    assert rmc.utc is not None
    assert rmc.utc.hour == 2 and rmc.utc.minute == 21


def test_repair_sample_keeps_terminator() -> None:
    repaired, changed = repair_sentence(SAMPLE + "\r\n")
    assert changed is True
    assert repaired.endswith("\r\n")
    assert is_valid(repaired)


def test_repair_forces_offset_when_requested() -> None:
    repaired, changed = repair_sentence(MODERN, offset_days=WNRO_DAYS)
    assert changed is True
    assert date_of(repaired) == date(2026, 5, 3) + timedelta(days=WNRO_DAYS)
    assert is_valid(repaired)


def test_repair_auto_noop_on_modern_date() -> None:
    repaired, changed = repair_sentence(MODERN)
    assert changed is False
    assert repaired == MODERN
    assert is_valid(repaired)


def test_repair_auto_noop_with_wall_clock_for_plausible_date() -> None:
    plausible = with_checksum("GPRMC,022127.542,A,3541.1234,N,13946.5678,E,0,0,170906,,,A")
    repaired, changed = repair_sentence(plausible, wall_clock=date(2007, 1, 1))
    assert changed is False
    assert repaired == plausible
    # ...but the same sentence from today's clock must be shifted.
    repaired2, changed2 = repair_sentence(plausible, wall_clock=WALL_CLOCK)
    assert changed2 is True
    assert date_of(repaired2) == date(2026, 5, 3)


def test_repair_auto_off_leaves_stale_date_alone() -> None:
    repaired, changed = repair_sentence(SAMPLE, auto=False)
    assert changed is False
    assert repaired == SAMPLE


# ---------------------------------------------------------------------------
# repair_sentence: other sentence types
# ---------------------------------------------------------------------------
def test_repair_zda() -> None:
    stale = with_checksum("GPZDA,022127.542,17,09,2006,00,00")
    repaired, changed = repair_sentence(stale, wall_clock=WALL_CLOCK)
    assert changed is True
    assert is_valid(repaired)
    assert "03,05,2026" in repaired

    same, unchanged = repair_sentence(with_checksum("GPZDA,022127.542,03,05,2026,00,00"))
    assert unchanged is False
    assert same


def test_repair_zda_invalid_date_left_alone() -> None:
    line = with_checksum("GPZDA,022127.542,32,13,2006,00,00")
    repaired, changed = repair_sentence(line)
    assert changed is False
    assert repaired == line


@pytest.mark.parametrize(
    "line",
    [
        with_checksum("GPGGA,022127.542,3541.1234,N,13946.5678,E,1,08,0.95,42.5,M,35.0,M,,"),
        with_checksum("GPGSA,A,3,01,02,03,,,,,,,,,,1.5,0.95,1.1"),
        with_checksum("GPGSV,1,1,03,01,10,090,30,02,20,180,40,03,30,270,50"),
        with_checksum("GPVTG,270.0,T,,M,12.5,N,23.2,K,A"),
        with_checksum("PMTK001,605,3"),
    ],
)
def test_repair_leaves_non_date_sentences_alone(line: str) -> None:
    repaired, changed = repair_sentence(line)
    assert changed is False
    assert repaired == line


def test_repair_passes_debug_lines_verbatim() -> None:
    for line in (
        "======Setting_ProcessKeys========\r\n",
        "======Setting_ProcessKeys========",
        "GPS_Run",
        "",
        "\r\n",
        "random garbage 12345",
    ):
        repaired, changed = repair_sentence(line)
        assert changed is False
        assert repaired == line


def test_repair_malformed_nmea_is_returned_verbatim() -> None:
    for line in ("$", "$,", "$GPRMC", "$GPRMC*00"):
        repaired, changed = repair_sentence(line)
        assert changed is False
        assert repaired == line


def test_repair_without_checksum_adds_one() -> None:
    line = "GPRMC,022127.542,V,8960.0000,N,00000.0000,E,0.00,0.00,170906,,,N"
    repaired, changed = repair_sentence(
        "$" + line, wall_clock=WALL_CLOCK
    )
    assert changed is True
    assert repaired.startswith("$GPRMC")
    # The original had no checksum, so none is added back.
    assert "*" not in repaired
    assert "030526" in repaired


# ---------------------------------------------------------------------------
# Stream helpers
# ---------------------------------------------------------------------------
def test_repair_stream_preserves_order_and_types() -> None:
    lines = [
        "======Setting_ProcessKeys========\r\n",
        SAMPLE + "\r\n",
        MODERN + "\r\n",
        with_checksum("GPGGA,022127.542,3541.1234,N,13946.5678,E,1,08,0.95,42.5,M,35.0,M,,")
        + "\r\n",
    ]
    out = list(repair_stream(lines))
    assert len(out) == 4
    assert out[0] == lines[0]
    assert "030526" in out[1]
    assert is_valid(out[1].rstrip("\r\n"))
    assert out[2] == lines[2]
    assert out[3] == lines[3]


def test_repair_text_roundtrip() -> None:
    text = "DEBUG\r\n" + SAMPLE + "\n" + MODERN + "\n"
    out = repair_text(text)
    assert out.count("\n") == text.count("\n")
    assert "030526" in out
    assert MODERN in out


def test_repair_stream_accepts_wall_clock() -> None:
    out = list(repair_stream([SAMPLE + "\n"], wall_clock=date(2007, 1, 1)))
    assert out == [SAMPLE + "\n"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def test_sentence_kind_and_date_of() -> None:
    assert sentence_kind(SAMPLE) == "GPRMC"
    assert sentence_kind("======debug======") is None
    assert date_of(SAMPLE) == date(2006, 9, 17)
    assert date_of(MODERN) == date(2026, 5, 3)
    assert date_of(with_checksum("GPZDA,000000,03,05,2026,00,00")) == date(2026, 5, 3)
    assert date_of("======debug======") is None


def test_checksum_of_and_recompute() -> None:
    assert checksum_of(SAMPLE) == 0x7F
    assert checksum_of("no checksum") is None
    mangled = SAMPLE[:-2] + "00"
    assert is_valid(mangled) is False
    fixed = recompute_checksum(mangled)
    assert is_valid(fixed)
    assert fixed == SAMPLE
    assert recompute_checksum("not nmea") == "not nmea"
