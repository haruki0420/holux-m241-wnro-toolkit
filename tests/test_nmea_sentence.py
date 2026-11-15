"""Tests for NMEA sentence helpers: checksums, splitting and RMC/GGA parsing."""

from __future__ import annotations

from datetime import date, time

import pytest

from m241.errors import NmeaError
from m241.nmea import (
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
from m241.nmea.sentence import _parse_date, _parse_latlon, _parse_time

SAMPLE_RMC = "$GPRMC,022127.542,V,8960.0000,N,00000.0000,E,0.00,0.00,170906,,,N*7F"

GGA = "$GPGGA,022127.542,3541.1234,N,13946.5678,E,1,08,0.95,42.5,M,35.0,M,,*5E"
ZDA = "$GPZDA,022127.542,03,05,2026,00,00*63"


# ---------------------------------------------------------------------------
# Checksums
# ---------------------------------------------------------------------------
def test_checksum_of_sample_sentence() -> None:
    body = "GPRMC,022127.542,V,8960.0000,N,00000.0000,E,0.00,0.00,170906,,,N"
    assert checksum(body) == 0x7F
    assert checksum_hex(body) == "7F"
    # The delimiters are ignored when accidentally included.
    assert checksum(f"${body}*7F") == 0x7F


def test_with_checksum_roundtrip() -> None:
    body = "GPRMC,022127.542,V,8960.0000,N,00000.0000,E,0.00,0.00,170906,,,N"
    sentence = with_checksum(body)
    assert sentence == SAMPLE_RMC
    assert is_valid(sentence)
    # Idempotent: re-adding the checksum does not double it.
    assert with_checksum(sentence) == sentence


def test_with_checksum_replaces_bad_checksum() -> None:
    assert with_checksum("$GPRMC,1,2,3*00") == with_checksum("GPRMC,1,2,3")


def test_checksum_empty_body() -> None:
    assert checksum("") == 0
    assert with_checksum("") == "$*00"


def test_is_valid() -> None:
    assert is_valid(SAMPLE_RMC) is True
    assert is_valid("$GPRMC,1,2,3*00") is False  # checksum mismatch
    assert is_valid("======Setting_ProcessKeys========") is False
    assert is_valid("$GPRMC,no checksum here") is False
    assert is_valid("") is False
    assert is_valid("$*00") is False  # empty type


def test_sentence_checksum() -> None:
    assert sentence_checksum(SAMPLE_RMC) == 0x7F
    assert sentence_checksum("$GPRMC,1,2,3") is None
    assert sentence_checksum("$GPRMC,1,2,3*ZZ") is None
    assert sentence_checksum("$GPRMC,1,2,3*7") is None


# ---------------------------------------------------------------------------
# Split
# ---------------------------------------------------------------------------
def test_split() -> None:
    kind, fields = split(SAMPLE_RMC)
    assert kind == "GPRMC"
    assert fields[0] == "022127.542"
    assert fields[1] == "V"
    assert fields[8] == "170906"
    assert fields[-1] == "N"
    assert len(fields) == 12


def test_split_tolerates_newlines_and_no_checksum() -> None:
    assert split("$GPGGA,1,2,3\r\n") == ("GPGGA", ["1", "2", "3"])
    assert split("$PMTK001,605,3")[0] == "PMTK001"


def test_split_errors() -> None:
    with pytest.raises(NmeaError):
        split("not nmea")
    with pytest.raises(NmeaError):
        split("$,1,2,3")


# ---------------------------------------------------------------------------
# RMC
# ---------------------------------------------------------------------------
def test_parse_rmc_sample() -> None:
    rmc = parse_rmc(SAMPLE_RMC)
    assert rmc.utc == time(2, 21, 27, 542000)
    assert rmc.status == "V"
    assert rmc.date == date(2006, 9, 17)
    assert rmc.lat == pytest.approx(90.0)  # 8960.0000 N == 89 deg 60.0000 min
    assert rmc.lon == pytest.approx(0.0)
    assert rmc.speed_kn == pytest.approx(0.0)
    assert rmc.course == pytest.approx(0.0)
    assert rmc.mode == "N"
    assert rmc.talker == "GP"
    assert rmc.active is False
    assert rmc.raw == SAMPLE_RMC


def test_parse_rmc_real_fix() -> None:
    sentence = with_checksum(
        "GPRMC,022127.542,A,3541.1234,N,13946.5678,E,12.5,270.0,030526,1.5,W,A"
    )
    rmc = parse_rmc(sentence)
    assert rmc.active is True
    assert rmc.date == date(2026, 5, 3)
    assert rmc.lat == pytest.approx(35 + 41.1234 / 60, abs=1e-9)
    assert rmc.lon == pytest.approx(139 + 46.5678 / 60, abs=1e-9)
    assert rmc.position == (rmc.lat, rmc.lon)
    assert rmc.speed_kn == pytest.approx(12.5)
    assert rmc.course == pytest.approx(270.0)
    assert rmc.mag_var == pytest.approx(1.5)
    assert rmc.mag_var_dir == "W"
    assert rmc.mode == "A"


def test_parse_rmc_southern_western() -> None:
    sentence = with_checksum("GPRMC,000000,A,3345.0000,S,07030.0000,W,0,0,010120,,,A")
    rmc = parse_rmc(sentence)
    assert rmc.lat == pytest.approx(-(33 + 45 / 60))
    assert rmc.lon == pytest.approx(-(70 + 30 / 60))


def test_parse_rmc_missing_fields_yields_none() -> None:
    rmc = parse_rmc(with_checksum("GPRMC,022127.542,V,,,,,,,170906"))
    assert rmc.utc is not None
    assert rmc.lat is None
    assert rmc.lon is None
    assert rmc.position is None
    assert rmc.speed_kn is None


def test_parse_rmc_errors() -> None:
    with pytest.raises(NmeaError):
        parse_rmc(with_checksum("GPGGA,1,2,3"))
    with pytest.raises(NmeaError):
        parse_rmc("$GPRMC,1,2,3")


def test_parse_time_and_date_helpers() -> None:
    assert _parse_time("022127.542") == time(2, 21, 27, 542000)
    assert _parse_time("022127") == time(2, 21, 27)
    assert _parse_time("bad") is None
    assert _parse_date("030526") == date(2026, 5, 3)
    assert _parse_date("311299") == date(1999, 12, 31)
    assert _parse_date("010170") == date(1970, 1, 1)
    assert _parse_date("999999") is None
    assert _parse_date("0305") is None
    assert _parse_latlon("3541.1234", "N", lat=True) == pytest.approx(35 + 41.1234 / 60)
    assert _parse_latlon("9541.1234", "N", lat=True) is None  # out of range


# ---------------------------------------------------------------------------
# GGA
# ---------------------------------------------------------------------------
def test_parse_gga() -> None:
    gga = parse_gga(GGA)
    assert gga["kind"] == "GPGGA"
    assert gga["utc"] == time(2, 21, 27, 542000)
    assert gga["lat"] == pytest.approx(35 + 41.1234 / 60, abs=1e-9)
    assert gga["lon"] == pytest.approx(139 + 46.5678 / 60, abs=1e-9)
    assert gga["quality"] == 1
    assert gga["sats"] == 8
    assert gga["hdop"] == pytest.approx(0.95)
    assert gga["altitude_m"] == pytest.approx(42.5)
    assert gga["geoid_m"] == pytest.approx(35.0)


def test_parse_gga_errors() -> None:
    with pytest.raises(NmeaError):
        parse_gga(SAMPLE_RMC)
    with pytest.raises(NmeaError):
        parse_gga("$GPGGA,1,2")


# ---------------------------------------------------------------------------
# ZDA
# ---------------------------------------------------------------------------
def test_parse_zda() -> None:
    zda = parse_zda(ZDA)
    assert zda.utc == time(2, 21, 27, 542000)
    assert zda.date == date(2026, 5, 3)
    assert zda.local_zone_hours == 0
    assert zda.local_zone_minutes == 0
    assert zda.talker == "GP"


def test_parse_zda_invalid_date() -> None:
    zda = parse_zda("$GPZDA,000000,32,13,2026,00,00")
    assert zda.date is None


def test_parse_zda_errors() -> None:
    with pytest.raises(NmeaError):
        parse_zda(SAMPLE_RMC)
