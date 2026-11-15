"""Tests for :mod:`m241.firmware.image` — header parsing, hashing, saving.

Every fixture here is synthetic: the real Holux images are proprietary and are
never committed, so each test builds a bytearray of the documented size and
places only the documented header strings / constants / version marker at the
documented offsets.  This is enough to exercise the parser and the atomic
writer without shipping a single byte of vendor firmware.

The one test that touches a real image is skipped unless the user's own copy
of the official V1.13 package is present (``M241_FW_V113`` or the default
extracted-drop glob).
"""

from __future__ import annotations

import hashlib
import os
import struct
from pathlib import Path

import pytest

from m241.errors import FirmwareError
from m241.firmware.image import (
    BUILD_NUMBER_OFFSET,
    DEVICE_TYPE_OFFSET,
    EPOCH_CONSTANT_OFFSETS,
    FAMILY_OFFSET,
    FIRMWARE_FAMILY,
    FW_SIZE_OFFSET,
    GPS_EPOCH_UNIX,
    HEADER_SIZE,
    RELEASE_MAJOR_OFFSET,
    RELEASE_MINOR_OFFSET,
    RELEASE_NAME_OFFSET,
    SERIAL_PORT_OFFSET,
    FirmwareHeader,
    FirmwareImage,
)

V112_SIZE = 406312
V113_SIZE = 406620
V112_SHA256 = "4103601a29f256ae5da771bbcbc3ca85a89deb6268ce74865dc84b2583ae98f7"
V113_SHA256 = "32fe52690db9947bdbfa58caa6dba450b3f4222b23f2e045f606252649159435"
V112_VERSION_OFFSET = 0x59B14
V113_VERSION_OFFSET = 0x59C28
EFFECTIVE_EPOCH_OFFSET = 0x402F4
INACTIVE_EPOCH_OFFSET = 0x402DC

DEFAULT_FW_V113_GLOB_ROOT = Path(__file__).resolve().parents[2] / "_extracted"


def _real_firmware_path() -> Path | None:
    """Path of a real V1.13 image supplied by the user, or ``None``.

    Resolution order: the ``M241_FW_V113`` environment variable, then the
    default extracted-drop glob next to the repository.  Both are opt-in, so
    CI never reads a vendor image.
    """
    explicit = os.environ.get("M241_FW_V113")
    if explicit:
        candidate = Path(explicit)
        return candidate if candidate.is_file() else None
    if not DEFAULT_FW_V113_GLOB_ROOT.is_dir():
        return None
    matches = sorted(DEFAULT_FW_V113_GLOB_ROOT.glob("**/*V1.13*.bin"))
    return matches[0] if matches else None


def _synthetic_image(size: int, version: str, version_offset: int) -> bytearray:
    """Build a zero-filled image with only the documented fields populated."""
    data = bytearray(size)
    data[FAMILY_OFFSET : FAMILY_OFFSET + len(FIRMWARE_FAMILY)] = FIRMWARE_FAMILY.encode("ascii")
    struct.pack_into("<I", data, FW_SIZE_OFFSET, size - 4)
    struct.pack_into("<H", data, BUILD_NUMBER_OFFSET, 67)
    data[RELEASE_NAME_OFFSET : RELEASE_NAME_OFFSET + 6] = b"B-core"
    data[RELEASE_MAJOR_OFFSET] = 1
    data[RELEASE_MINOR_OFFSET] = 20
    data[SERIAL_PORT_OFFSET : SERIAL_PORT_OFFSET + 9] = b"SPP Slave"
    data[DEVICE_TYPE_OFFSET : DEVICE_TYPE_OFFSET + 9] = b"01017-00F"
    for offset in EPOCH_CONSTANT_OFFSETS:
        struct.pack_into("<I", data, offset, GPS_EPOCH_UNIX)
    data[version_offset : version_offset + 5] = version.encode("ascii")
    return data


def _v112_image() -> FirmwareImage:
    return FirmwareImage.from_bytes(_synthetic_image(V112_SIZE, "V1.12", V112_VERSION_OFFSET))


def _v113_image() -> FirmwareImage:
    return FirmwareImage.from_bytes(_synthetic_image(V113_SIZE, "V1.13", V113_VERSION_OFFSET))


@pytest.mark.parametrize(
    ("image_factory", "expected_size", "expected_version", "expected_offset"),
    [
        (_v112_image, V112_SIZE, "V1.12", V112_VERSION_OFFSET),
        (_v113_image, V113_SIZE, "V1.13", V113_VERSION_OFFSET),
    ],
)
def test_image_reports_size_version_and_hash(image_factory, expected_size, expected_version, expected_offset):
    image = image_factory()
    assert len(image) == expected_size
    assert image.version_string() == expected_version
    assert image.version_offset() == expected_offset
    assert image.sha256() == hashlib.sha256(bytes(image.data)).hexdigest()
    assert image.sha256() == hashlib.sha256(image.to_bytes()).hexdigest()
    assert image.epoch_offsets() == [INACTIVE_EPOCH_OFFSET, EFFECTIVE_EPOCH_OFFSET]


def test_header_parses_documented_offsets():
    image = _v113_image()
    header = image.header()
    assert isinstance(header, FirmwareHeader)
    assert header.family == "HOLUX_M-241"
    assert header.device_type == "01017-00F"
    assert header.release_name == "B-core"
    assert header.release_major == 1
    assert header.release_minor == 20
    assert header.release == "B-core_1.20"
    assert header.build_number == 67
    assert header.fw_size == V113_SIZE - 4
    assert header.build_date == ""
    assert len(header.raw) == HEADER_SIZE


def test_header_build_date_is_extracted_when_present():
    data = _synthetic_image(V113_SIZE, "V1.13", V113_VERSION_OFFSET)
    stamp = b"Feb  9 2006 10:53:05"
    data[0x30 : 0x30 + len(stamp)] = stamp
    header = FirmwareImage.from_bytes(data).header()
    assert header.build_date == stamp.decode("ascii")


def test_header_rejects_short_image():
    with pytest.raises(FirmwareError) as excinfo:
        FirmwareImage.from_bytes(b"\x00" * 8).header()
    assert excinfo.value.offset == 8


def test_sha256_known_real_digest_of_synthetic_is_stable():
    # The synthetic image is deterministic: same bytes -> same digest.  The
    # value is not the official hash (no vendor bytes here), it just proves the
    # encoding of the documented fields is reproducible.
    first = _v112_image().sha256()
    second = _v112_image().sha256()
    assert first == second
    assert first != V112_SHA256
    assert first != V113_SHA256


def test_estimate_log_record_bytes_uses_detected_profile():
    assert _v112_image().estimate_log_record_bytes() == 16
    assert _v113_image().estimate_log_record_bytes() == 20


def test_estimate_log_record_bytes_none_for_unknown_image():
    assert FirmwareImage.from_bytes(b"\x00" * 4096).estimate_log_record_bytes() is None


def test_from_path_and_save_roundtrip(tmp_path: Path):
    source = tmp_path / "src.bin"
    source.write_bytes(_v112_image().to_bytes())
    loaded = FirmwareImage.from_path(source)
    assert loaded.version_string() == "V1.12"
    assert loaded.sha256() == _v112_image().sha256()

    target = tmp_path / "nested" / "dir" / "out.bin"
    loaded.save(target)
    assert target.read_bytes() == loaded.to_bytes()
    assert not list(target.parent.glob(".*.tmp"))


def test_save_replaces_existing_file_atomically(tmp_path: Path):
    target = tmp_path / "out.bin"
    target.write_bytes(b"stale")
    image = _v113_image()
    image.save(target)
    assert target.read_bytes() == image.to_bytes()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out.bin"]


def test_save_cleans_up_temporary_file_on_failure(tmp_path: Path, monkeypatch):
    target = tmp_path / "out.bin"
    target.write_bytes(b"keep-me")

    def _boom(src, dst):
        raise OSError("boom")

    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(FirmwareError):
        _v112_image().save(target)
    assert target.read_bytes() == b"keep-me"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out.bin"]


def test_from_path_reports_missing_file(tmp_path: Path):
    with pytest.raises(FirmwareError) as excinfo:
        FirmwareImage.from_path(tmp_path / "nope.bin")
    assert "cannot read firmware image" in str(excinfo.value)


def test_from_bytes_copies_input():
    raw = bytearray(_synthetic_image(V112_SIZE, "V1.12", V112_VERSION_OFFSET))
    image = FirmwareImage.from_bytes(raw)
    raw[0] = 0xFF
    assert image.data[0] == 0x00


def test_unknown_version_and_epoch_report_none_or_empty():
    image = FirmwareImage.from_bytes(bytearray(0x200))
    assert image.version_string() is None
    assert image.version_offset() is None
    assert image.epoch_offsets() == []


def test_repr_mentions_size():
    assert repr(_v112_image()) == f"FirmwareImage(size={V112_SIZE})"


@pytest.mark.skipif(_real_firmware_path() is None, reason="real V1.13 firmware not available")
def test_real_v113_image_matches_official_hash():
    path = _real_firmware_path()
    assert path is not None
    image = FirmwareImage.from_path(path)
    assert len(image) == V113_SIZE
    assert image.sha256() == V113_SHA256
    assert image.version_string() == "V1.13"
    assert image.version_offset() == V113_VERSION_OFFSET
    assert image.epoch_offsets() == [INACTIVE_EPOCH_OFFSET, EFFECTIVE_EPOCH_OFFSET]
    header = image.header()
    assert header.family == "HOLUX_M-241"
    assert header.fw_size == V113_SIZE - 4
