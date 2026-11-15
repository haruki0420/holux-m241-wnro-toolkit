"""Tests for :mod:`m241.firmware.profiles` — the shipped profile database.

The profiles are pure metadata (hashes, sizes, offsets) so these tests do not
need any firmware bytes at all; a couple of them build a minimal synthetic
image to drive :func:`detect_profile` down its fallback (size + version +
epoch offset) path.
"""

from __future__ import annotations

import json
import struct

import pytest

from m241.errors import FirmwareError, FirmwareProfileError
from m241.firmware.image import EPOCH_CONSTANT_OFFSETS, GPS_EPOCH_UNIX, FirmwareImage
from m241.firmware.profiles import (
    OFFICIAL_SHA256,
    PROFILES,
    PROFILES_DATA_FILE,
    detect_profile,
    get_profile,
    list_profiles,
    profile_for_record_size,
    record_bytes_for_version,
)

V112_SIZE = 406312
V113_SIZE = 406620
V112_SHA256 = "4103601a29f256ae5da771bbcbc3ca85a89deb6268ce74865dc84b2583ae98f7"
V113_SHA256 = "32fe52690db9947bdbfa58caa6dba450b3f4222b23f2e045f606252649159435"
V112_PATCHED_SHA256 = "dad2983d95cc3be251b358b2b7d5da9272d9946e70aaa0067162a4c21475c384"
V113_PATCHED_SHA256 = "901555843895054f77716364a446e8ad41c00b799c781b4ff95144352c141c9a"
V112_VERSION_OFFSET = 0x59B14
V113_VERSION_OFFSET = 0x59C28
EFFECTIVE_EPOCH_OFFSET = 0x402F4


def _synthetic_image(size: int, version: str, version_offset: int) -> FirmwareImage:
    data = bytearray(size)
    data[0x90:0x9B] = b"HOLUX_M-241"
    data[0x148:0x14E] = b"B-core"
    data[0x15A] = 1
    data[0x15B] = 20
    struct.pack_into("<I", data, 0xF4, size - 4)
    for offset in EPOCH_CONSTANT_OFFSETS:
        struct.pack_into("<I", data, offset, GPS_EPOCH_UNIX)
    data[version_offset : version_offset + 5] = version.encode("ascii")
    return FirmwareImage.from_bytes(data)


def test_shipped_database_has_the_two_verified_profiles():
    assert list(PROFILES) == ["v1.12", "v1.13"]
    assert [profile.name for profile in list_profiles()] == ["v1.12", "v1.13"]
    assert [profile.version for profile in list_profiles()] == ["V1.12", "V1.13"]


@pytest.mark.parametrize(
    ("name", "size", "version", "version_offset", "log_record_bytes", "sha256", "patched"),
    [
        (
            "v1.12",
            V112_SIZE,
            "V1.12",
            V112_VERSION_OFFSET,
            16,
            V112_SHA256,
            V112_PATCHED_SHA256,
        ),
        (
            "v1.13",
            V113_SIZE,
            "V1.13",
            V113_VERSION_OFFSET,
            20,
            V113_SHA256,
            V113_PATCHED_SHA256,
        ),
    ],
)
def test_profile_encodes_verified_facts(
    name, size, version, version_offset, log_record_bytes, sha256, patched
):
    profile = PROFILES[name]
    assert profile.sizes == (size,)
    assert profile.size == size
    assert profile.version == version
    assert profile.version_bytes == version.encode("ascii")
    assert profile.version_offset == version_offset
    assert profile.epoch_offset == EFFECTIVE_EPOCH_OFFSET
    assert profile.epoch_offset == 0x402F4
    assert profile.epoch_constant == GPS_EPOCH_UNIX
    assert profile.log_record_bytes == log_record_bytes
    assert sha256 in profile.sha256
    assert profile.patched_sha256 == patched
    assert profile.display_name == f"Holux M-241 firmware {version}"
    assert profile.notes
    assert profile.epoch_offset < profile.size
    assert profile.version_offset < profile.size


def test_official_sha256_maps_every_shipped_hash_to_its_profile():
    assert OFFICIAL_SHA256[V112_SHA256] == "v1.12"
    assert OFFICIAL_SHA256[V113_SHA256] == "v1.13"
    assert len(OFFICIAL_SHA256) == sum(len(profile.sha256) for profile in PROFILES.values())


def test_shipped_json_is_importable_and_well_formed():
    # The JSON must live inside the package so the wheel ships it.
    from importlib import resources

    data_file = resources.files("m241").joinpath(PROFILES_DATA_FILE)
    document = json.loads(data_file.read_text(encoding="utf-8"))
    names = [entry["name"] for entry in document["profiles"]]
    assert names == ["v1.12", "v1.13"]
    for entry, profile in zip(document["profiles"], list_profiles(), strict=True):
        assert entry["version"] == profile.version
        assert entry["epoch_offset"] == profile.epoch_offset


def test_detect_profile_recognises_synthetic_images_by_size_version_and_epoch():
    assert detect_profile(_synthetic_image(V112_SIZE, "V1.12", V112_VERSION_OFFSET)).name == "v1.12"
    assert detect_profile(_synthetic_image(V113_SIZE, "V1.13", V113_VERSION_OFFSET)).name == "v1.13"


def test_detect_profile_prefers_sha256_over_header_fields(monkeypatch):
    from m241.firmware import profiles as profiles_module

    image = _synthetic_image(V113_SIZE, "V1.12", V112_VERSION_OFFSET)
    monkeypatch.setitem(profiles_module.OFFICIAL_SHA256, image.sha256(), "v1.13")
    assert detect_profile(image).name == "v1.13"


def test_detect_profile_rejects_garbage_short_image():
    with pytest.raises(FirmwareProfileError) as excinfo:
        detect_profile(FirmwareImage.from_bytes(b"\x00" * 1024))
    message = str(excinfo.value)
    assert "no firmware profile matches" in message
    assert "1024 bytes" in message


def test_detect_profile_rejects_right_size_but_missing_epoch_constant():
    data = bytearray(V112_SIZE)
    data[V112_VERSION_OFFSET : V112_VERSION_OFFSET + 5] = b"V1.12"
    with pytest.raises(FirmwareProfileError):
        detect_profile(FirmwareImage.from_bytes(data))


def test_detect_profile_rejects_known_size_with_unknown_version():
    image = _synthetic_image(V112_SIZE, "V9.99", V112_VERSION_OFFSET)
    with pytest.raises(FirmwareProfileError):
        detect_profile(image)


def test_detect_profile_reports_observed_details():
    image = _synthetic_image(V112_SIZE, "V1.12", V112_VERSION_OFFSET)
    # Remove one epoch constant so nothing matches, then look at the message.
    data = bytearray(image.to_bytes())
    struct.pack_into("<I", data, EFFECTIVE_EPOCH_OFFSET, 0)
    with pytest.raises(FirmwareProfileError) as excinfo:
        detect_profile(FirmwareImage.from_bytes(data))
    assert "V1.12" in str(excinfo.value)
    assert "406312" in str(excinfo.value)


@pytest.mark.parametrize("name", ["v1.12", "V1.12", "1.12", " v1.12 "])
def test_get_profile_accepts_common_spellings(name):
    assert get_profile(name).name == "v1.12"


def test_get_profile_unknown_name_raises_with_known_list():
    with pytest.raises(FirmwareProfileError) as excinfo:
        get_profile("v9.99")
    assert "unknown firmware profile" in str(excinfo.value)
    assert "v1.12" in str(excinfo.value)
    assert "v1.13" in str(excinfo.value)


def test_profile_for_record_size():
    assert [profile.name for profile in profile_for_record_size(16)] == ["v1.12"]
    assert [profile.name for profile in profile_for_record_size(20)] == ["v1.13"]
    assert profile_for_record_size(17) == []


@pytest.mark.parametrize(
    ("version", "expected"),
    [("V1.11", 16), ("V1.12", 16), ("V1.13", 20), ("v1.13", 20), ("1.12", 16)],
)
def test_record_bytes_for_version(version, expected):
    assert record_bytes_for_version(version) == expected


def test_record_bytes_for_version_unknown_raises():
    with pytest.raises(FirmwareProfileError):
        record_bytes_for_version("V2.00")


def test_profile_is_frozen():
    import dataclasses

    profile = get_profile("v1.12")
    with pytest.raises(dataclasses.FrozenInstanceError):
        profile.name = "changed"  # type: ignore[misc]


def test_malformed_database_is_rejected(tmp_path, monkeypatch):
    from m241.firmware import profiles as profiles_module

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"profiles": [{"name": "x"}]}), encoding="utf-8")
    monkeypatch.setattr(profiles_module, "_profiles_path", lambda: bad)
    with pytest.raises(FirmwareError):
        profiles_module.load_profiles()


def test_duplicate_profile_names_are_rejected(tmp_path, monkeypatch):
    from m241.firmware import profiles as profiles_module

    entry = {
        "name": "v1.12",
        "display_name": "dupe",
        "sha256": [V112_SHA256],
        "sizes": [V112_SIZE],
        "version": "V1.12",
        "version_offset": V112_VERSION_OFFSET,
        "epoch_offset": EFFECTIVE_EPOCH_OFFSET,
    }
    bad = tmp_path / "dupes.json"
    bad.write_text(json.dumps({"profiles": [entry, entry]}), encoding="utf-8")
    monkeypatch.setattr(profiles_module, "_profiles_path", lambda: bad)
    with pytest.raises(FirmwareError):
        profiles_module.load_profiles()
