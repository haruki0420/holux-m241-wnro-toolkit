"""Tests for the log-dump exporters (GPX, CSV, NMEA, KML, JSON)."""

from __future__ import annotations

import csv
import io
import json
import struct
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import pytest

from m241.errors import M241Error
from m241.logfile import (
    CSV_COLUMNS,
    EXPORTERS,
    LOG_BIT_HEIGHT,
    LOG_BIT_LATITUDE,
    LOG_BIT_LONGITUDE,
    LOG_BIT_SPEED,
    LOG_BIT_UTC,
    export,
    read_log_dump,
    to_csv,
    to_gpx,
    to_json,
    to_kml,
    to_nmea,
)
from m241.nmea import is_valid, parse_rmc

HEADER = 0x200
DATA = 0x10000 - HEADER
MASK_16 = (
    (1 << LOG_BIT_UTC)
    | (1 << LOG_BIT_LATITUDE)
    | (1 << LOG_BIT_LONGITUDE)
    | (1 << LOG_BIT_HEIGHT)
)
MASK_20 = MASK_16 | (1 << LOG_BIT_SPEED)

GPX_NS = {"gpx": "http://www.topografix.com/GPX/1/1"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def u32(value: int) -> bytes:
    return int(value).to_bytes(4, "little")


def f32(value: float) -> bytes:
    return struct.pack("<f", float(value))


def height24(value: float) -> bytes:
    return f32(value)[1:4]


def xor(data: bytes) -> int:
    value = 0
    for byte in data:
        value ^= byte
    return value


def utc_seconds(year: int, month: int, day: int, hh: int = 0, mm: int = 0, ss: int = 0) -> int:
    return int(datetime(year, month, day, hh, mm, ss, tzinfo=timezone.utc).timestamp())


def rec16(utc: int, lat: float, lon: float, height: float) -> bytes:
    payload = u32(utc) + f32(lat) + f32(lon) + height24(height)
    return payload + bytes([xor(payload)])


def rec20(utc: int, lat: float, lon: float, height: float, speed: float) -> bytes:
    payload = u32(utc) + f32(lat) + f32(lon) + height24(height) + f32(speed)
    return payload + bytes([xor(payload)])


def make_block(body: bytes, *, count: int | None = None, bitmask: int = MASK_16) -> bytes:
    head = struct.pack("<HIHIII", 0xFFFF if count is None else count, bitmask, 0x0106, 10, 0, 0)
    return head.ljust(HEADER, b"\x00") + body + b"\xff" * (DATA - len(body))


@pytest.fixture()
def dump():
    t0 = utc_seconds(2026, 5, 3, 2, 21, 27)
    body = (
        rec20(t0, 35.6895, 139.6917, 42.5, 12.5)
        + rec20(t0 + 5, 35.6900, 139.6920, 43.0, 0.0)
        + rec20(t0 + 10, 35.6905, 139.6925, 43.5, 3.0)
    )
    return read_log_dump(make_block(body, count=3, bitmask=MASK_20))


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------
def test_csv_header_and_columns(dump) -> None:
    text = to_csv(dump.records)
    rows = list(csv.reader(io.StringIO(text)))
    assert tuple(rows[0]) == CSV_COLUMNS
    assert CSV_COLUMNS == (
        "index",
        "utc",
        "lat",
        "lon",
        "altitude_m",
        "speed_kmh",
        "heading_deg",
        "hdop",
        "sats",
        "crc_ok",
    )
    assert len(rows) == 4  # header + 3 records
    first = rows[1]
    assert first[0] == "0"
    assert first[1] == "2026-05-03T02:21:27Z"
    assert float(first[2]) == pytest.approx(35.6895, abs=1e-4)
    assert float(first[3]) == pytest.approx(139.6917, abs=1e-4)
    assert float(first[4]) == pytest.approx(42.5, abs=1e-3)
    assert float(first[5]) == pytest.approx(12.5, abs=1e-3)
    assert first[9] == "true"
    assert rows[2][9] == "true"


def test_csv_export_via_registry(dump) -> None:
    assert export(dump, "csv") == to_csv(dump.records)
    assert export(dump, "CSV") == to_csv(dump.records)


def test_csv_bad_crc_flag() -> None:
    t0 = utc_seconds(2026, 5, 3)
    good = rec16(t0, 1.0, 2.0, 3.0)
    broken = bytearray(rec16(t0 + 5, 1.1, 2.1, 3.1))
    broken[-1] ^= 0xFF
    dump = read_log_dump(make_block(good + bytes(broken), count=2))
    rows = list(csv.reader(io.StringIO(to_csv(dump.records))))
    assert rows[1][9] == "true"
    assert rows[2][9] == "false"


# ---------------------------------------------------------------------------
# GPX
# ---------------------------------------------------------------------------
def test_gpx_is_valid_xml_and_schema_shaped(dump) -> None:
    text = to_gpx(dump.records, name="Test log")
    root = ET.fromstring(text)
    assert root.tag == "{http://www.topografix.com/GPX/1/1}gpx"
    assert root.attrib["version"] == "1.1"
    assert root.attrib["creator"]

    tracks = root.findall("gpx:trk", GPX_NS)
    assert len(tracks) == 1
    name = tracks[0].find("gpx:name", GPX_NS)
    assert name is not None and name.text == "Test log"
    segments = tracks[0].findall("gpx:trkseg", GPX_NS)
    assert len(segments) == 1
    points = segments[0].findall("gpx:trkpt", GPX_NS)
    assert len(points) == 3
    assert float(points[0].attrib["lat"]) == pytest.approx(35.6895, abs=1e-5)
    assert float(points[0].attrib["lon"]) == pytest.approx(139.6917, abs=1e-5)

    altitude = points[0].find("gpx:ele", GPX_NS)
    assert altitude is not None and float(altitude.text) == pytest.approx(42.5, abs=1e-2)
    when = points[0].find("gpx:time", GPX_NS)
    assert when is not None and when.text == "2026-05-03T02:21:27Z"


def test_gpx_skips_records_without_position() -> None:
    t0 = utc_seconds(2026, 5, 3)
    dump = read_log_dump(make_block(rec16(t0, 1.0, 2.0, 3.0), count=1))
    dump.records[0].lat = None
    root = ET.fromstring(to_gpx(dump.records))
    assert root.findall(".//gpx:trkpt", GPX_NS) == []


def test_gpx_waypoint_flag_emits_wpt(dump) -> None:
    dump.records[1].waypoint = True
    root = ET.fromstring(to_gpx(dump.records))
    waypoints = root.findall("gpx:wpt", GPX_NS)
    assert len(waypoints) == 1
    assert float(waypoints[0].attrib["lat"]) == pytest.approx(35.6900, abs=1e-5)
    assert waypoints[0].find("gpx:name", GPX_NS).text == "WP000001"


def test_gpx_escapes_name() -> None:
    text = to_gpx([], name="A & B <log>")
    root = ET.fromstring(text)
    assert root.find("gpx:metadata/gpx:name", GPX_NS).text == "A & B <log>"


# ---------------------------------------------------------------------------
# NMEA
# ---------------------------------------------------------------------------
def test_nmea_reconstruction(dump) -> None:
    text = to_nmea(dump.records)
    lines = [line for line in text.splitlines() if line]
    assert len(lines) == 3
    for line in lines:
        assert line.startswith("$GPRMC,")
        assert is_valid(line)

    rmc = parse_rmc(lines[0])
    assert rmc.date is not None
    assert rmc.date.strftime("%d%m%y") == "030526"
    assert rmc.utc is not None
    assert (rmc.utc.hour, rmc.utc.minute, rmc.utc.second) == (2, 21, 27)
    assert rmc.status == "A"
    assert rmc.lat == pytest.approx(35.6895, abs=1e-4)
    assert rmc.lon == pytest.approx(139.6917, abs=1e-4)
    assert rmc.speed_kn == pytest.approx(12.5 / 1.852, abs=1e-2)


def test_nmea_roundtrip_after_repair(dump) -> None:
    # A repaired dump (as produced by the WNRO patch) stays valid NMEA.
    from m241.nmea import repair_sentence

    for line in to_nmea(dump.records).splitlines():
        repaired, changed = repair_sentence(line, offset_days=0, auto=True)
        assert changed is False
        assert is_valid(repaired)


# ---------------------------------------------------------------------------
# KML
# ---------------------------------------------------------------------------
def test_kml_is_valid_xml(dump) -> None:
    text = to_kml(dump.records, name="Track")
    root = ET.fromstring(text)
    assert root.tag == "{http://www.opengis.net/kml/2.2}kml"
    coords = root.find(".//{http://www.opengis.net/kml/2.2}coordinates")
    assert coords is not None
    lines = [line.strip() for line in coords.text.splitlines() if line.strip()]
    assert len(lines) == 3
    lon, lat, alt = (float(part) for part in lines[0].split(","))
    assert lat == pytest.approx(35.6895, abs=1e-5)
    assert lon == pytest.approx(139.6917, abs=1e-5)
    assert alt == pytest.approx(42.5, abs=1e-2)


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------
def test_json_schema(dump) -> None:
    payload = to_json(dump)
    assert payload["format"] == "m241-log-dump"
    assert payload["version"] == 1
    assert payload["device"] == "m241"
    assert payload["record_length"] == 20

    summary = payload["summary"]
    assert summary["records"] == 3
    assert summary["blocks"] == 1
    assert summary["record_length"] == 20
    assert summary["used_bytes"] == 60
    assert summary["available_bytes"] == DATA
    assert summary["fill_ratio"] == pytest.approx(60 / DATA)
    assert summary["first_utc"] == "2026-05-03T02:21:27Z"
    assert summary["last_utc"] == "2026-05-03T02:21:37Z"

    assert len(payload["blocks"]) == 1
    block = payload["blocks"][0]
    assert block["offset"] == 0
    assert block["count"] == 3
    assert block["bitmask"] == MASK_20
    assert block["policy"] == 0x0106
    assert block["period_s"] == pytest.approx(1.0)

    record = payload["records"][0]
    assert record["index"] == 0
    assert record["utc"] == "2026-05-03T02:21:27Z"
    assert record["lat"] == pytest.approx(35.6895, abs=1e-4)
    assert record["lon"] == pytest.approx(139.6917, abs=1e-4)
    assert record["speed_kmh"] == pytest.approx(12.5, abs=1e-3)
    assert record["crc_ok"] is True
    assert record["waypoint"] is False


def test_json_export_is_serialisable(dump) -> None:
    text = export(dump, "json")
    data = json.loads(text)
    assert data["summary"]["records"] == 3
    # everything must already be JSON-safe (no datetime objects)
    assert json.dumps(data, indent=2, ensure_ascii=False) == text
    assert json.loads(json.dumps(data)) == data


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
def test_exporter_registry() -> None:
    assert set(EXPORTERS) == {"gpx", "csv", "nmea", "kml", "json"}
    for name, func in EXPORTERS.items():
        assert callable(func), name


def test_export_unknown_format(dump) -> None:
    with pytest.raises(M241Error):
        export(dump, "shapefile")


def test_export_empty_dump() -> None:
    dump = read_log_dump(b"")
    assert dump.records == []
    assert to_csv([]).splitlines() == [",".join(CSV_COLUMNS)]
    assert to_nmea([]) == ""
    ET.fromstring(to_gpx([]))
    ET.fromstring(to_kml([]))
    assert to_json(dump)["summary"]["records"] == 0


def test_exporters_accept_generators(dump) -> None:
    gen = (record for record in dump.records)
    assert to_csv(gen).count("\n") == 4
