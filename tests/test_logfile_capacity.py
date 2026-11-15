"""Tests for the flash-capacity arithmetic (exact documented numbers)."""

from __future__ import annotations

import pytest

from m241.errors import M241Error
from m241.logfile import (
    BLOCK_HEADER,
    BLOCK_SIZE,
    FLASH_SIZES,
    USABLE_PER_BLOCK,
    capacity,
    capacity_table,
    estimate_points,
    flash_name_from_da,
    record_bytes_for_version,
    resolve_flash,
)

POINTS_512K_16 = 32_512
POINTS_512K_20 = 26_009
POINTS_2M_16 = 130_048
POINTS_2M_20 = 104_038
POINTS_4M_16 = 260_096
POINTS_4M_20 = 208_076


# ---------------------------------------------------------------------------
# Constants and per-block geometry
# ---------------------------------------------------------------------------
def test_geometry_constants() -> None:
    assert BLOCK_SIZE == 0x10000
    assert BLOCK_HEADER == 0x200
    assert USABLE_PER_BLOCK == 65_024
    assert USABLE_PER_BLOCK == BLOCK_SIZE - BLOCK_HEADER
    assert FLASH_SIZES == {"512K": 0x80000, "2M": 0x200000, "4M": 0x400000}


def test_usable_bytes_totals() -> None:
    assert 8 * USABLE_PER_BLOCK == 520_192
    assert 32 * USABLE_PER_BLOCK == 2_080_768
    assert 64 * USABLE_PER_BLOCK == 4_161_536


# ---------------------------------------------------------------------------
# The exact documented point counts
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("flash", "record_bytes", "blocks", "usable", "points"),
    [
        ("512K", 16, 8, 520_192, POINTS_512K_16),
        ("512K", 20, 8, 520_192, POINTS_512K_20),
        ("2M", 16, 32, 2_080_768, POINTS_2M_16),
        ("2M", 20, 32, 2_080_768, POINTS_2M_20),
        ("4M", 16, 64, 4_161_536, POINTS_4M_16),
        ("4M", 20, 64, 4_161_536, POINTS_4M_20),
    ],
)
def test_capacity_numbers(
    flash: str, record_bytes: int, blocks: int, usable: int, points: int
) -> None:
    cap = capacity(flash, record_bytes)
    assert cap.flash_bytes == FLASH_SIZES[flash]
    assert cap.blocks == blocks
    assert cap.usable_bytes == usable
    assert cap.record_bytes == record_bytes
    assert cap.points == points


def test_capacity_table_order_and_values() -> None:
    table = capacity_table()
    assert [(row.flash_name, row.record_bytes) for row in table] == [
        ("512K", 16),
        ("512K", 20),
        ("2M", 16),
        ("2M", 20),
        ("4M", 16),
        ("4M", 20),
    ]
    assert [row.points for row in table] == [
        POINTS_512K_16,
        POINTS_512K_20,
        POINTS_2M_16,
        POINTS_2M_20,
        POINTS_4M_16,
        POINTS_4M_20,
    ]
    # The marketing "130,000 points" is only reachable at 16 B.
    two_mebibyte = [row for row in table if row.flash_name == "2M"]
    assert two_mebibyte[0].points >= 130_000 > two_mebibyte[1].points


def test_capacity_accepts_byte_count_and_hex() -> None:
    assert capacity(0x200000, 16).points == POINTS_2M_16
    assert capacity("0x200000", 16).points == POINTS_2M_16
    assert capacity(2_097_152, 16).points == POINTS_2M_16


def test_capacity_rejects_bad_input() -> None:
    with pytest.raises(M241Error):
        capacity("8M", 16)
    with pytest.raises(M241Error):
        capacity("2M", 0)
    with pytest.raises(M241Error):
        capacity("2M", -4)
    with pytest.raises(M241Error):
        capacity("2M", 16.5)  # type: ignore[arg-type]
    with pytest.raises(M241Error):
        capacity(1234, 16)  # not a multiple of 64 KiB
    with pytest.raises(M241Error):
        resolve_flash(True)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Duration estimates
# ---------------------------------------------------------------------------
def test_duration_estimate_seconds_hours_days() -> None:
    cap = capacity("2M", 16)
    estimate = cap.duration_estimate(1.0)
    assert estimate["seconds"] == pytest.approx(POINTS_2M_16)
    assert estimate["hours"] == pytest.approx(POINTS_2M_16 / 3600)
    assert estimate["days"] == pytest.approx(POINTS_2M_16 / 86400)
    assert cap.duration(5) == pytest.approx(POINTS_2M_16 * 5)
    assert cap.duration_hours(5) == pytest.approx(POINTS_2M_16 * 5 / 3600)
    assert cap.duration_days(5) == pytest.approx(POINTS_2M_16 * 5 / 86400)


def test_seconds_at_callable_alias() -> None:
    cap = capacity("2M", 20)
    assert cap.seconds_at(10.0) == pytest.approx(POINTS_2M_20 * 10.0)


def test_duration_rejects_non_positive_interval() -> None:
    cap = capacity("2M", 16)
    with pytest.raises(M241Error):
        cap.duration(0)
    with pytest.raises(M241Error):
        cap.duration(-1)


def test_capacity_accepts_interval_at_construction() -> None:
    cap = capacity("4M", 20, interval_s=30)
    assert cap.as_dict(30)["duration_s"] == pytest.approx(POINTS_4M_20 * 30)
    with pytest.raises(M241Error):
        capacity("4M", 20, interval_s=0)


def test_as_dict_schema() -> None:
    data = capacity("2M", 16).as_dict()
    assert data == {
        "flash": "2M",
        "flash_bytes": 0x200000,
        "blocks": 32,
        "usable_bytes": 2_080_768,
        "record_bytes": 16,
        "points": POINTS_2M_16,
    }


# ---------------------------------------------------------------------------
# Firmware version -> record size
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("V1.12", 16),
        ("v1.12", 16),
        ("1.12", 16),
        ("V1.11", 16),
        ("V1.13", 20),
        ("v1.13", 20),
        ("1.13", 20),
        ("V1.14", 20),
        ("V2.00", 20),
    ],
)
def test_record_bytes_for_version(version: str, expected: int) -> None:
    assert record_bytes_for_version(version) == expected


def test_record_bytes_for_version_invalid() -> None:
    with pytest.raises(M241Error):
        record_bytes_for_version("nonsense")
    with pytest.raises(M241Error):
        record_bytes_for_version("")


def test_estimate_points() -> None:
    assert estimate_points("2M", "V1.12") == POINTS_2M_16
    assert estimate_points("2M", "V1.13") == POINTS_2M_20
    assert estimate_points("4M", "V1.12") == POINTS_4M_16
    assert estimate_points("4M", "V1.13") == POINTS_4M_20


# ---------------------------------------------------------------------------
# DA flash id
# ---------------------------------------------------------------------------
def test_flash_name_from_da() -> None:
    assert flash_name_from_da(0x22BA) == "2M"
    assert flash_name_from_da(0x22BA) is not None
    assert flash_name_from_da(0x1234) is None


def test_flash_name_from_da_bad_input() -> None:
    with pytest.raises(M241Error):
        flash_name_from_da("22BA")  # type: ignore[arg-type]


def test_capacity_flash_name_property() -> None:
    assert capacity("2M", 16).flash_name == "2M"
    assert capacity("4M", 16).flash_name == "4M"
    assert capacity(0x800000, 16).flash_name == "8388608"
