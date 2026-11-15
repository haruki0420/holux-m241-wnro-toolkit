"""Tests for the raw M-241 log-dump reader.

Every fixture is synthesised in code -- no real firmware or log dump is used.
"""

from __future__ import annotations

import struct
from datetime import datetime, timezone

import pytest

from m241.errors import LogParseError, M241Error
from m241.logfile import (
    LOG_BIT_DISTANCE,
    LOG_BIT_HDOP,
    LOG_BIT_HEIGHT,
    LOG_BIT_LATITUDE,
    LOG_BIT_LONGITUDE,
    LOG_BIT_MILLISECOND,
    LOG_BIT_NSAT,
    LOG_BIT_RCR,
    LOG_BIT_SID,
    LOG_BIT_SNR,
    LOG_BIT_SPEED,
    LOG_BIT_UTC,
    LOG_BIT_VALID,
    block_header,
    field_sizes,
    read_log_dump,
    read_log_file,
    record_length,
)
from m241.logfile.reader import parse_info_block

BLOCK = 0x10000
HEADER = 0x200
DATA = BLOCK - HEADER

MASK_16 = (
    (1 << LOG_BIT_UTC)
    | (1 << LOG_BIT_LATITUDE)
    | (1 << LOG_BIT_LONGITUDE)
    | (1 << LOG_BIT_HEIGHT)
)
MASK_20 = MASK_16 | (1 << LOG_BIT_SPEED)

INFO_BITMASK = 0x02
INFO_PERIOD = 0x03
INFO_DISTANCE = 0x04
INFO_SPEED = 0x05
INFO_POLICY = 0x07


# ---------------------------------------------------------------------------
# Synthetic-dump builders
# ---------------------------------------------------------------------------
def u16(value: int) -> bytes:
    return int(value).to_bytes(2, "little")


def u32(value: int) -> bytes:
    return int(value).to_bytes(4, "little")


def f32(value: float) -> bytes:
    return struct.pack("<f", float(value))


def f64(value: float) -> bytes:
    return struct.pack("<d", float(value))


def height24(value: float) -> bytes:
    """M-241 3-byte height: the top three bytes of a little-endian float32."""
    return f32(value)[1:4]


def xor(data: bytes) -> int:
    value = 0
    for byte in data:
        value ^= byte
    return value


def make_record_16(utc: int, lat: float, lon: float, height: float) -> bytes:
    """Build a 16-byte V1.12-style record (UTC, LAT, LON, HEIGHT + 1-byte CRC)."""
    payload = u32(utc) + f32(lat) + f32(lon) + height24(height)
    return payload + bytes([xor(payload)])


def make_record_20(utc: int, lat: float, lon: float, height: float, speed: float) -> bytes:
    """Build a 20-byte V1.13-style record (adds a float SPEED field)."""
    payload = u32(utc) + f32(lat) + f32(lon) + height24(height) + f32(speed)
    return payload + bytes([xor(payload)])


def make_info_block(kind: int, value: int) -> bytes:
    """Build a 16-byte ``LOG_RST`` info block."""
    return bytes([0xAA] * 6) + b"\x00" + bytes([kind]) + u32(value) + bytes([0xBB] * 4)


def make_block(
    body: bytes = b"",
    *,
    count: int | None = None,
    bitmask: int = MASK_16,
    policy: int = 0x0106,
    period_ds: int = 50,
    distance_dm: int = 0,
    speed_dkh: int = 0,
) -> bytes:
    """Build one 64 KiB block: a 0x200-byte header, *body*, then 0xFF padding."""
    head = struct.pack(
        "<HIHIII",
        0xFFFF if count is None else count,
        bitmask,
        policy,
        period_ds,
        distance_dm,
        speed_dkh,
    )
    assert len(body) <= DATA
    return head.ljust(HEADER, b"\x00") + body + b"\xff" * (DATA - len(body))


def utc_seconds(
    year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0
) -> int:
    return int(
        datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc).timestamp()
    )


# ---------------------------------------------------------------------------
# Record layout
# ---------------------------------------------------------------------------
def test_record_length_16_and_20() -> None:
    assert record_length(MASK_16) == 16
    assert record_length(MASK_20) == 20
    # Sanity: the M-241 field table itself.
    assert field_sizes("m241")[LOG_BIT_LATITUDE] == 4  # float, not double
    assert field_sizes("m241")[LOG_BIT_HEIGHT] == 3
    assert field_sizes("mtk")[LOG_BIT_LATITUDE] == 8


def test_record_length_generic_mtk_differs() -> None:
    # Generic MTK loggers use doubles for lat/lon and a 4-byte height,
    # plus a '*' separator and a 1-byte CRC.
    assert record_length(MASK_16, device="mtk") == 2 + 4 + 8 + 8 + 4
    assert record_length(MASK_20, device="mtk") == 2 + 4 + 8 + 8 + 4 + 4


def test_record_length_unknown_device_raises() -> None:
    with pytest.raises(M241Error):
        record_length(MASK_16, device="nope")


# ---------------------------------------------------------------------------
# 16-byte layout
# ---------------------------------------------------------------------------
def test_read_16_byte_block() -> None:
    t0 = utc_seconds(2026, 5, 3, 2, 21)
    records = [
        make_record_16(t0, 35.6895, 139.6917, 42.5),
        make_record_16(t0 + 5, 35.6900, 139.6920, 43.0),
        make_record_16(t0 + 10, 35.6905, 139.6925, 43.5),
    ]
    dump = read_log_dump(make_block(b"".join(records), count=3))

    assert dump.record_length == 16
    assert len(dump.blocks) == 1
    assert len(dump.records) == 3
    assert dump.blocks[0].count == 3
    assert dump.blocks[0].bitmask == MASK_16
    assert dump.blocks[0].used_bytes == 48

    first = dump.records[0]
    assert first.index == 0
    assert first.offset == HEADER
    assert first.crc_ok is True
    assert first.utc is not None
    assert first.utc.tzinfo is not None
    assert first.utc == datetime(2026, 5, 3, 2, 21, tzinfo=timezone.utc)
    assert first.lat == pytest.approx(35.6895, abs=1e-5)
    assert first.lon == pytest.approx(139.6917, abs=1e-5)
    assert first.height_m == pytest.approx(42.5, abs=1e-5)
    assert first.speed_kmh is None
    assert first.has_fix is True
    assert dump.records[2].offset == HEADER + 32
    assert dump.records[2].utc == datetime(2026, 5, 3, 2, 21, 10, tzinfo=timezone.utc)


def test_height_24bit_roundtrip() -> None:
    raw = height24(1234.5)
    assert len(raw) == 3
    record = make_record_16(utc_seconds(2026, 1, 1), 0.0, 0.0, 1234.5)
    dump = read_log_dump(make_block(record, count=1))
    assert dump.records[0].height_m == pytest.approx(1234.5, abs=1e-3)


# ---------------------------------------------------------------------------
# 20-byte layout (V1.13)
# ---------------------------------------------------------------------------
def test_read_20_byte_block() -> None:
    t0 = utc_seconds(2026, 5, 3, 2, 21)
    records = [
        make_record_20(t0, 35.6895, 139.6917, 42.5, 12.5),
        make_record_20(t0 + 5, 35.6900, 139.6920, 43.0, 0.0),
    ]
    dump = read_log_dump(make_block(b"".join(records), count=2, bitmask=MASK_20))

    assert dump.record_length == 20
    assert len(dump.records) == 2
    assert dump.records[0].speed_kmh == pytest.approx(12.5, abs=1e-4)
    assert dump.records[1].speed_kmh == pytest.approx(0.0)
    assert dump.blocks[0].used_bytes == 40


def test_block_header_parsing() -> None:
    block = make_block(
        count=7,
        bitmask=MASK_20,
        policy=0x0104,
        period_ds=100,
        distance_dm=250,
        speed_dkh=30,
    )
    header = block_header(block)
    assert header.count == 7
    assert header.bitmask == MASK_20
    assert header.policy == 0x0104
    assert not header.log_enabled
    assert header.period_s == pytest.approx(10.0)
    assert header.distance_m == pytest.approx(25.0)
    assert header.speed_kmh == pytest.approx(3.0)
    assert header.data_offset == HEADER


def test_block_header_open_block_and_truncation() -> None:
    header = block_header(make_block(count=None))
    assert header.count is None
    assert header.is_open is True
    with pytest.raises(LogParseError):
        block_header(b"\x00" * 10)


# ---------------------------------------------------------------------------
# Multi-block rollover and padding
# ---------------------------------------------------------------------------
def test_multi_block_rollover() -> None:
    t0 = utc_seconds(2026, 5, 3, 2, 0)
    block1 = make_block(
        b"".join(make_record_16(t0 + i * 5, 35.0 + i, 139.0, 10.0) for i in range(2)),
        count=2,
    )
    block2 = make_block(
        b"".join(make_record_16(t0 + 100 + i * 5, 36.0 + i, 140.0, 20.0) for i in range(3)),
        count=None,
    )
    dump = read_log_dump(block1 + block2)

    assert len(dump.blocks) == 2
    assert dump.blocks[1].offset == BLOCK
    assert dump.blocks[1].count is None
    assert len(dump.records) == 5
    assert dump.records[2].offset == BLOCK + HEADER
    assert dump.records[2].lat == pytest.approx(36.0, abs=1e-5)
    assert [r.index for r in dump.records] == [0, 1, 2, 3, 4]
    assert dump.used_bytes() == 5 * 16
    assert dump.available_bytes() == 2 * DATA
    assert dump.fill_ratio() == pytest.approx(80 / (2 * DATA))


def test_ff_padding_ends_block() -> None:
    t0 = utc_seconds(2026, 5, 3)
    body = make_record_16(t0, 1.0, 2.0, 3.0)
    dump = read_log_dump(make_block(body, count=None))
    assert len(dump.records) == 1
    assert dump.blocks[0].used_bytes == 16


def test_block_without_bitmask_is_skipped() -> None:
    dump = read_log_dump(make_block(b"\x00" * 100, count=None, bitmask=0))
    assert len(dump.blocks) == 1
    assert dump.records == []
    assert dump.record_length == 0


# ---------------------------------------------------------------------------
# Error handling: truncation and CRC
# ---------------------------------------------------------------------------
def test_truncated_final_record_is_dropped_by_default() -> None:
    t0 = utc_seconds(2026, 5, 3)
    good1 = make_record_16(t0, 1.0, 2.0, 3.0)
    good2 = make_record_16(t0 + 5, 1.1, 2.1, 3.1)
    partial = make_record_16(t0 + 10, 1.2, 2.2, 3.2)[:10]
    data = make_block(good1 + good2 + partial)[: HEADER + 16 + 16 + 10]
    dump = read_log_dump(data)
    assert len(dump.records) == 2
    assert all(record.crc_ok for record in dump.records)


def test_truncated_final_record_raises_in_strict_mode() -> None:
    t0 = utc_seconds(2026, 5, 3)
    partial = make_record_16(t0, 1.0, 2.0, 3.0)[:7]
    data = make_block(partial)[: HEADER + 7]
    with pytest.raises(LogParseError) as excinfo:
        read_log_dump(data, strict=True)
    assert excinfo.value.offset == HEADER


def test_bad_crc_marks_record_not_raises() -> None:
    t0 = utc_seconds(2026, 5, 3)
    good = make_record_16(t0, 1.0, 2.0, 3.0)
    broken = bytearray(make_record_16(t0 + 5, 1.1, 2.1, 3.1))
    broken[-1] ^= 0xFF
    dump = read_log_dump(make_block(good + bytes(broken), count=2))
    assert [r.crc_ok for r in dump.records] == [True, False]
    assert len(dump.records) == 2


def test_bad_crc_raises_in_strict_mode() -> None:
    t0 = utc_seconds(2026, 5, 3)
    broken = bytearray(make_record_16(t0, 1.0, 2.0, 3.0))
    broken[-1] ^= 0x5A
    with pytest.raises(LogParseError) as excinfo:
        read_log_dump(make_block(bytes(broken), count=1), strict=True)
    assert excinfo.value.offset == HEADER


def test_truncated_block_header_is_tolerated() -> None:
    dump = read_log_dump(b"\x00" * 8)
    assert dump.blocks == []
    assert dump.records == []
    with pytest.raises(LogParseError):
        read_log_dump(b"\x00" * 8, strict=True)


def test_non_bytes_input_raises() -> None:
    with pytest.raises(M241Error):
        read_log_dump("not bytes")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Holux info blocks and waypoint tags
# ---------------------------------------------------------------------------
def test_waypoint_tag_marks_next_record() -> None:
    t0 = utc_seconds(2026, 5, 3)
    tag = b"HOLUXGR241WAYPNT"
    body = (
        tag
        + make_record_16(t0, 1.0, 2.0, 3.0)
        + tag
        + b"    "
        + make_record_16(t0 + 5, 1.1, 2.1, 3.1)
    )
    dump = read_log_dump(make_block(body, count=2))
    assert len(dump.records) == 2
    assert dump.records[0].waypoint is True
    assert dump.records[1].waypoint is True
    # The tag (and its four trailing spaces) are consumed, not decoded as data.
    assert dump.blocks[0].used_bytes == 16 + 16 + 16 + 4 + 16


def test_logger_tag_is_skipped_and_does_not_set_waypoint() -> None:
    t0 = utc_seconds(2026, 5, 3)
    tag = b"HOLUXGR241LOGGER"
    dump = read_log_dump(make_block(tag + make_record_16(t0, 1.0, 2.0, 3.0), count=1))
    assert len(dump.records) == 1
    assert dump.records[0].waypoint is False


def test_info_block_changes_bitmask_mid_block() -> None:
    t0 = utc_seconds(2026, 5, 3)
    body = (
        make_record_16(t0, 1.0, 2.0, 3.0)
        + make_info_block(INFO_BITMASK, MASK_20)
        + make_record_20(t0 + 5, 1.1, 2.1, 3.1, 10.0)
    )
    dump = read_log_dump(make_block(body, count=2))
    assert len(dump.records) == 2
    assert dump.records[0].speed_kmh is None
    assert dump.records[1].speed_kmh == pytest.approx(10.0, abs=1e-4)


def test_parse_info_block_kinds() -> None:
    assert parse_info_block(make_info_block(INFO_BITMASK, MASK_20)) == {"bitmask": MASK_20}
    assert parse_info_block(make_info_block(INFO_PERIOD, 300)) == {"period_ds": 300}
    assert parse_info_block(make_info_block(INFO_DISTANCE, 150)) == {"distance_dm": 150}
    assert parse_info_block(make_info_block(INFO_SPEED, 50)) == {"speed_dkh": 50}
    assert parse_info_block(make_info_block(INFO_POLICY, 0x0106)) == {"policy": 0x0106}
    assert parse_info_block(bytes(16)) == {}


def test_info_block_changes_period_metadata() -> None:
    t0 = utc_seconds(2026, 5, 3)
    body = (
        make_info_block(INFO_PERIOD, 100)
        + make_info_block(INFO_DISTANCE, 250)
        + make_info_block(INFO_SPEED, 30)
        + make_record_16(t0, 1.0, 2.0, 3.0)
    )
    dump = read_log_dump(make_block(body, count=1, period_ds=50))
    assert dump.blocks[0].period_s == pytest.approx(10.0)
    assert dump.blocks[0].distance_m == pytest.approx(25.0)
    assert dump.blocks[0].speed_kmh == pytest.approx(3.0)
    assert len(dump.records) == 1


# ---------------------------------------------------------------------------
# Variable-length satellite records
# ---------------------------------------------------------------------------
def test_sid_records_are_variable_length() -> None:
    t0 = utc_seconds(2026, 5, 3)
    mask = (
        (1 << LOG_BIT_UTC)
        | (1 << LOG_BIT_LATITUDE)
        | (1 << LOG_BIT_LONGITUDE)
        | (1 << LOG_BIT_NSAT)
        | (1 << LOG_BIT_SID)
    )

    def sid_record(utc: int, used: int, view: int) -> bytes:
        # SID slots: [id, used, count_lo, count_hi] where the count is only
        # meaningful in the first slot (that is what the MTK reference does).
        payload = u32(utc) + f32(1.0) + f32(2.0) + bytes([view, used])
        payload += bytes([1, 1, 2, 0])  # slot 1 + satellite count = 2
        payload += bytes([2, 0, 0, 0])  # slot 2
        return payload + bytes([xor(payload)])

    body = sid_record(t0, 2, 3) + sid_record(t0 + 5, 4, 5)
    dump = read_log_dump(make_block(body, count=2, bitmask=mask))
    assert len(dump.records) == 2
    assert dump.records[0].sats_used == 2
    assert dump.records[1].sats_used == 4
    assert dump.records[1].utc == datetime(2026, 5, 3, 0, 0, 5, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Field decoding: VALID, DOP, RCR, milliseconds, distance
# ---------------------------------------------------------------------------
def test_valid_rcr_ms_hdop_distance_fields() -> None:
    mask = (
        (1 << LOG_BIT_UTC)
        | (1 << LOG_BIT_VALID)
        | (1 << LOG_BIT_LATITUDE)
        | (1 << LOG_BIT_LONGITUDE)
        | (1 << LOG_BIT_HDOP)
        | (1 << LOG_BIT_RCR)
        | (1 << LOG_BIT_MILLISECOND)
        | (1 << LOG_BIT_DISTANCE)
    )
    payload = (
        u32(utc_seconds(2026, 5, 3, 2, 21, 27))
        + u16(0x0001)  # no fix
        + f32(35.0)
        + f32(139.0)
        + u16(125)  # hdop = 1.25
        + u16(0x0008)  # button press
        + u16(542)  # milliseconds
        + f64(1234.5)
    )
    body = payload + bytes([xor(payload)])
    dump = read_log_dump(make_block(body, count=1, bitmask=mask))

    record = dump.records[0]
    assert record.valid == 0x0001
    assert record.has_fix is False
    assert record.hdop == pytest.approx(1.25)
    assert record.rcr == 0x0008
    assert record.ms == 542
    assert record.distance_m == pytest.approx(1234.5)
    assert record.utc is not None
    assert record.utc.microsecond == 542000
    assert record.utc_iso == "2026-05-03T02:21:27.542Z"


# ---------------------------------------------------------------------------
# File helpers and object shape
# ---------------------------------------------------------------------------
def test_read_log_file(tmp_path) -> None:
    t0 = utc_seconds(2026, 5, 3)
    data = make_block(make_record_16(t0, 1.0, 2.0, 3.0), count=1)
    path = tmp_path / "data.bin"
    path.write_bytes(data)
    dump = read_log_file(path)
    assert len(dump.records) == 1
    assert dump.first_utc is not None
    assert dump.last_utc == dump.first_utc


def test_read_log_file_missing_raises(tmp_path) -> None:
    with pytest.raises(M241Error):
        read_log_file(tmp_path / "nope.bin")


def test_dump_repr_and_device() -> None:
    dump = read_log_dump(make_block(), device="m241")
    assert "LogDump" in repr(dump)
    assert dump.device == "m241"
    assert dump.record_length == 16


def test_accepts_bytearray_and_memoryview() -> None:
    t0 = utc_seconds(2026, 5, 3)
    data = make_block(make_record_16(t0, 1.0, 2.0, 3.0), count=1)
    for payload in (bytearray(data), memoryview(data)):
        dump = read_log_dump(payload)
        assert len(dump.records) == 1
        assert dump.records[0].lat == pytest.approx(1.0, abs=1e-6)


def test_zero_count_block_is_empty() -> None:
    dump = read_log_dump(make_block(b"\x00" * 64, count=0))
    assert len(dump.blocks) == 1
    assert dump.blocks[0].count == 0
    assert dump.records == []
    assert dump.blocks[0].used_bytes == 0


def test_erased_trailing_block_is_skipped() -> None:
    t0 = utc_seconds(2026, 5, 3)
    used = make_block(make_record_16(t0, 1.0, 2.0, 3.0), count=1)
    erased = b"\xff" * BLOCK
    dump = read_log_dump(used + erased)
    assert len(dump.blocks) == 2
    assert len(dump.records) == 1
    assert dump.blocks[1].used_bytes == 0
    assert dump.blocks[1].bitmask == 0xFFFFFFFF


def test_strict_mode_accepts_a_clean_dump() -> None:
    t0 = utc_seconds(2026, 5, 3)
    data = make_block(
        make_record_16(t0, 1.0, 2.0, 3.0) + make_record_16(t0 + 5, 1.1, 2.1, 3.1),
        count=2,
    )
    dump = read_log_dump(data, strict=True)
    assert len(dump.records) == 2
    assert all(record.crc_ok for record in dump.records)


# ---------------------------------------------------------------------------
# Other device flavours
# ---------------------------------------------------------------------------
def test_generic_mtk_dump_uses_doubles_and_star_crc() -> None:
    """Generic MTK loggers: 8-byte doubles for lat/lon, 4-byte height, '*'+CRC."""
    mask = (
        (1 << LOG_BIT_UTC)
        | (1 << LOG_BIT_LATITUDE)
        | (1 << LOG_BIT_LONGITUDE)
        | (1 << LOG_BIT_HEIGHT)
    )
    payload = (
        u32(utc_seconds(2026, 5, 3))
        + f64(35.6895)
        + f64(139.6917)
        + f32(42.5)
    )
    body = payload + b"*" + bytes([xor(payload)])
    assert len(body) == record_length(mask, device="mtk") == 2 + 4 + 8 + 8 + 4

    dump = read_log_dump(make_block(body, count=1, bitmask=mask), device="mtk")
    assert len(dump.records) == 1
    record = dump.records[0]
    assert record.lat == pytest.approx(35.6895, abs=1e-9)
    assert record.lon == pytest.approx(139.6917, abs=1e-9)
    assert record.height_m == pytest.approx(42.5)
    assert record.crc_ok is True


def test_generic_mtk_bad_separator_is_crc_failure() -> None:
    mask = (
        (1 << LOG_BIT_UTC)
        | (1 << LOG_BIT_LATITUDE)
        | (1 << LOG_BIT_LONGITUDE)
        | (1 << LOG_BIT_HEIGHT)
    )
    payload = u32(utc_seconds(2026, 5, 3)) + f64(1.0) + f64(2.0) + f32(3.0)
    body = payload + b"!" + bytes([xor(payload)])  # wrong separator
    dump = read_log_dump(make_block(body, count=1, bitmask=mask), device="mtk")
    assert dump.records[0].crc_ok is False
    with pytest.raises(LogParseError):
        read_log_dump(make_block(body, count=1, bitmask=mask), device="mtk", strict=True)


def test_gr245_swaps_height_and_speed_slots() -> None:
    """The GR-245 keeps a 4-byte speed in the HEIGHT slot and vice versa."""
    mask = (
        (1 << LOG_BIT_UTC)
        | (1 << LOG_BIT_LATITUDE)
        | (1 << LOG_BIT_LONGITUDE)
        | (1 << LOG_BIT_HEIGHT)
        | (1 << LOG_BIT_SPEED)
    )
    # HEIGHT slot holds speed in cm/s; SPEED slot holds the 3-byte height.
    payload = (
        u32(utc_seconds(2026, 5, 3))
        + f32(1.0)
        + f32(2.0)
        + u32(1250)
        + height24(42.5)
    )
    body = payload + bytes([xor(payload)])
    assert len(body) == record_length(mask, device="gr245")

    dump = read_log_dump(make_block(body, count=1, bitmask=mask), device="gr245")
    record = dump.records[0]
    assert record.height_m == pytest.approx(42.5, abs=1e-3)
    # 1250 cm/s -> 12.5 m/s -> 45 km/h (the slot holds a cm/s counter).
    assert record.speed_kmh == pytest.approx(45.0, abs=1e-3)


def test_record_length_sid_worst_case() -> None:
    """Satellite info is counted as 32 slots when calculating a record length."""
    mask = (1 << LOG_BIT_UTC) | (1 << LOG_BIT_SID) | (1 << LOG_BIT_SNR)
    # 4 (UTC) + (4 SID + 2 SNR) * 32 + 1 (CRC)
    assert record_length(mask) == 4 + 6 * 32 + 1
