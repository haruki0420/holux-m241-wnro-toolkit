"""Tests for :mod:`m241.firmware.patcher` — the WNRO + version-marker patch.

The patch offsets, the LE32 values and the changed-byte sets are the verified
facts from ``docs/04-firmware-profiles.md``: the WNRO store touches exactly one
occurrence of the epoch constant (``0x402F4``) and flips the two high bytes at
``0x402F6``/``0x402F7``; the version marker rewrites the whole 5-byte ASCII run
so only the final digit moves (``0x59B18`` for V1.12, ``0x59C2C`` for V1.13).

Real firmware bytes are never committed.  The synthetic images below reproduce
the documented layout, so they pin the offsets and the LE32 semantics exactly.
The expected *full-image* SHA256 of a patched official image is only asserted
when the user's own copy of the official package is present (``M241_FW_V112`` /
``M241_FW_V113`` or the default extracted drop); those tests are skipped
otherwise.
"""

from __future__ import annotations

import os
import struct
from pathlib import Path

import pytest

from m241.errors import FirmwareError, FirmwareProfileError
from m241.firmware.image import EPOCH_CONSTANT_OFFSETS, GPS_EPOCH_UNIX, FirmwareImage
from m241.firmware.patcher import (
    WNRO_SHIFT_DAYS,
    WNRO_SHIFT_SECONDS,
    Patch,
    PatchPlan,
    apply_patch_plan,
    build_patch_plan,
    patch_file,
    verify_patched,
)
from m241.firmware.profiles import get_profile

V112_SIZE = 406312
V113_SIZE = 406620
V112_SHA256 = "4103601a29f256ae5da771bbcbc3ca85a89deb6268ce74865dc84b2583ae98f7"
V113_SHA256 = "32fe52690db9947bdbfa58caa6dba450b3f4222b23f2e045f606252649159435"
V112_PATCHED_SHA256 = "dad2983d95cc3be251b358b2b7d5da9272d9946e70aaa0067162a4c21475c384"
V113_PATCHED_SHA256 = "901555843895054f77716364a446e8ad41c00b799c781b4ff95144352c141c9a"
V112_VERSION_OFFSET = 0x59B14
V113_VERSION_OFFSET = 0x59C28
INACTIVE_EPOCH_OFFSET = 0x402DC
EFFECTIVE_EPOCH_OFFSET = 0x402F4
PATCHED_EPOCH = 935280000  # 0x37BF3D80
EXPECTED_CHANGED = {
    "V1.12": {0x402F6, 0x402F7, 0x59B18},
    "V1.13": {0x402F6, 0x402F7, 0x59C2C},
}
DEFAULT_FW_GLOB_ROOT = Path(__file__).resolve().parents[2] / "_extracted"


def _real_firmware_path(version: str) -> Path | None:
    """Path of a real image for ``version`` (``V1.12``/``V1.13``) if present.

    Resolution order: ``M241_FW_V112``/``M241_FW_V113``, then the default
    extracted-drop glob next to the repository.  Opt-in only: CI never reads a
    vendor image.
    """
    env_name = f"M241_FW_V{version[1:].replace('.', '')}"
    explicit = os.environ.get(env_name)
    if explicit:
        candidate = Path(explicit)
        return candidate if candidate.is_file() else None
    if not DEFAULT_FW_GLOB_ROOT.is_dir():
        return None
    matches = sorted(DEFAULT_FW_GLOB_ROOT.glob(f"**/*{version}*.bin"))
    return matches[0] if matches else None


def _synthetic_image(size: int, version: str, version_offset: int) -> FirmwareImage:
    """Zero-filled image with only the documented header/constant/version bytes."""
    data = bytearray(size)
    data[0x00:0x20] = bytes.fromhex("18f09fe5" * 8)
    data[0x90:0x9B] = b"HOLUX_M-241"
    struct.pack_into("<I", data, 0xF4, size - 4)
    data[0x148:0x14E] = b"B-core"
    data[0x15A] = 1
    data[0x15B] = 20
    data[0x190:0x199] = b"SPP Slave"
    data[0x1B0:0x1B9] = b"01017-00F"
    for offset in EPOCH_CONSTANT_OFFSETS:
        struct.pack_into("<I", data, offset, GPS_EPOCH_UNIX)
    data[version_offset : version_offset + 5] = version.encode("ascii")
    return FirmwareImage.from_bytes(data)


def _v112_image() -> FirmwareImage:
    return _synthetic_image(V112_SIZE, "V1.12", V112_VERSION_OFFSET)


def _v113_image() -> FirmwareImage:
    return _synthetic_image(V113_SIZE, "V1.13", V113_VERSION_OFFSET)


def _changed_offsets(before: FirmwareImage, after: FirmwareImage) -> set[int]:
    return {i for i, (a, b) in enumerate(zip(before.data, after.data, strict=True)) if a != b}


def _u32(image: FirmwareImage, offset: int) -> int:
    return struct.unpack_from("<I", image.data, offset)[0]


def test_wnro_shift_constants():
    assert WNRO_SHIFT_SECONDS == 619315200
    assert WNRO_SHIFT_SECONDS == 1024 * 604800
    assert WNRO_SHIFT_DAYS == 7168
    assert WNRO_SHIFT_SECONDS // 86400 == WNRO_SHIFT_DAYS
    assert GPS_EPOCH_UNIX + WNRO_SHIFT_SECONDS == PATCHED_EPOCH
    assert PATCHED_EPOCH == 0x37BF3D80
    assert GPS_EPOCH_UNIX == 0x12D53D80


@pytest.mark.parametrize("factory", [_v112_image, _v113_image])
def test_plan_patches_only_the_effective_epoch_constant(factory):
    image = factory()
    plan = build_patch_plan(image)
    epoch_patches = [p for p in plan.patches if p.offset == EFFECTIVE_EPOCH_OFFSET]
    assert len(epoch_patches) == 1
    patch = epoch_patches[0]
    assert patch.old == struct.pack("<I", GPS_EPOCH_UNIX)
    assert patch.new == struct.pack("<I", PATCHED_EPOCH)
    assert patch.changed_bytes == [0x402F6, 0x402F7]
    assert "WNRO" in patch.description
    # The inactive copy is untouched and is not part of the plan.
    assert all(p.offset != INACTIVE_EPOCH_OFFSET for p in plan.patches)
    assert INACTIVE_EPOCH_OFFSET not in plan.changed_offsets()


@pytest.mark.parametrize(
    ("factory", "version", "version_offset"),
    [
        (_v112_image, "V1.12", V112_VERSION_OFFSET),
        (_v113_image, "V1.13", V113_VERSION_OFFSET),
    ],
)
def test_apply_plan_changes_exactly_the_documented_offsets(factory, version, version_offset):
    image = factory()
    plan = build_patch_plan(image)
    patched = apply_patch_plan(image, plan)

    assert plan.changed_offsets() == sorted(EXPECTED_CHANGED[version])
    assert _changed_offsets(image, patched) == EXPECTED_CHANGED[version]
    assert plan.changed_offsets() == sorted(_changed_offsets(image, patched))

    # LE32 semantics: effective constant forwarded by exactly +1024 weeks.
    assert _u32(patched, EFFECTIVE_EPOCH_OFFSET) == PATCHED_EPOCH
    assert _u32(patched, EFFECTIVE_EPOCH_OFFSET) - _u32(image, EFFECTIVE_EPOCH_OFFSET) == (
        WNRO_SHIFT_SECONDS
    )
    # The unused alternate copy keeps the original constant (double-shift bug).
    assert _u32(patched, INACTIVE_EPOCH_OFFSET) == GPS_EPOCH_UNIX

    # The whole 5-byte marker run is rewritten, not a single digit.
    assert patched.data[version_offset : version_offset + 5] == b"V1.14"
    assert patched.version_string() == "V1.14"
    assert patched.version_offset() == version_offset

    # Size is preserved and the input object is untouched.
    assert len(patched) == len(image)
    assert image.version_string() == version
    assert _u32(image, EFFECTIVE_EPOCH_OFFSET) == GPS_EPOCH_UNIX


def test_plan_summary_mentions_offsets_and_versions():
    plan = build_patch_plan(_v113_image())
    summary = plan.summary()
    assert "v1.13" in summary
    assert "V1.14" in summary
    assert "0x402F4" in summary
    assert "0x59C28" in summary
    assert "WNRO" in summary


def test_no_version_marker_plan_touches_only_the_epoch_constant():
    image = _v112_image()
    plan = build_patch_plan(image, include_version_marker=False)
    patched = apply_patch_plan(image, plan)
    assert plan.target_version == "V1.14"
    assert len(plan.patches) == 1
    assert _changed_offsets(image, patched) == {0x402F6, 0x402F7}
    assert patched.version_string() == "V1.12"
    assert _u32(patched, EFFECTIVE_EPOCH_OFFSET) == PATCHED_EPOCH


def test_custom_target_version_and_shift():
    image = _v113_image()
    plan = build_patch_plan(image, target_version="V1.20", wnro_shift_seconds=604800)
    patched = apply_patch_plan(image, plan)
    assert patched.version_string() == "V1.20"
    assert _u32(patched, EFFECTIVE_EPOCH_OFFSET) == GPS_EPOCH_UNIX + 604800


def test_already_patched_image_is_refused_when_detecting():
    patched = apply_patch_plan(_v113_image(), build_patch_plan(_v113_image()))
    with pytest.raises(FirmwareError) as excinfo:
        build_patch_plan(patched)
    assert "already patched" in str(excinfo.value)
    assert excinfo.value.offset == EFFECTIVE_EPOCH_OFFSET


def test_already_patched_image_is_refused_with_profile_override():
    profile = get_profile("v1.13")
    patched = apply_patch_plan(_v113_image(), build_patch_plan(_v113_image()))
    with pytest.raises(FirmwareError) as excinfo:
        build_patch_plan(patched, profile=profile)
    assert "already patched" in str(excinfo.value)


def test_already_patched_image_without_marker_is_refused():
    image = _v112_image()
    patched = apply_patch_plan(image, build_patch_plan(image, include_version_marker=False))
    with pytest.raises(FirmwareError) as excinfo:
        build_patch_plan(patched)
    assert "already patched" in str(excinfo.value)


def test_applying_the_same_plan_twice_is_refused():
    image = _v112_image()
    plan = build_patch_plan(image)
    patched = apply_patch_plan(image, plan)
    with pytest.raises(FirmwareError) as excinfo:
        apply_patch_plan(patched, plan)
    assert "already patched" in str(excinfo.value)


def test_plan_is_refused_for_wrong_size():
    image = FirmwareImage.from_bytes(bytearray(V112_SIZE - 4))
    with pytest.raises(FirmwareProfileError):
        build_patch_plan(image)


def test_plan_override_with_profile_but_wrong_size_raises():
    image = FirmwareImage.from_bytes(bytearray(4096))
    with pytest.raises(FirmwareError):
        build_patch_plan(image, profile=get_profile("v1.12"))


def test_unexpected_epoch_constant_raises():
    data = bytearray(_v112_image().to_bytes())
    struct.pack_into("<I", data, EFFECTIVE_EPOCH_OFFSET, 12345)
    image = FirmwareImage.from_bytes(data)
    with pytest.raises(FirmwareError) as excinfo:
        build_patch_plan(image, profile=get_profile("v1.12"))
    assert "unexpected epoch constant" in str(excinfo.value)
    assert excinfo.value.offset == EFFECTIVE_EPOCH_OFFSET


def test_invalid_target_version_raises():
    with pytest.raises(FirmwareError):
        build_patch_plan(_v113_image(), target_version="1.14")
    with pytest.raises(FirmwareError):
        build_patch_plan(_v113_image(), target_version="V1.140")


def test_non_positive_shift_raises():
    with pytest.raises(FirmwareError):
        build_patch_plan(_v113_image(), wnro_shift_seconds=0)
    with pytest.raises(FirmwareError):
        build_patch_plan(_v113_image(), wnro_shift_seconds=-604800)


def test_overflowing_shift_raises():
    with pytest.raises(FirmwareError):
        build_patch_plan(_v113_image(), wnro_shift_seconds=0xFFFFFFFF)


def test_verify_patched_accepts_patched_and_rejects_original():
    image = _v113_image()
    plan = build_patch_plan(image)
    patched = apply_patch_plan(image, plan)
    verify_patched(patched, plan)
    with pytest.raises(FirmwareError):
        verify_patched(image, plan)


def test_verify_patched_detects_a_missing_patch():
    image = _v113_image()
    plan = build_patch_plan(image)
    patched = apply_patch_plan(image, plan)
    broken = FirmwareImage.from_bytes(patched.to_bytes())
    struct.pack_into("<I", broken.data, EFFECTIVE_EPOCH_OFFSET, GPS_EPOCH_UNIX)
    with pytest.raises(FirmwareError) as excinfo:
        verify_patched(broken, plan)
    assert excinfo.value.offset == EFFECTIVE_EPOCH_OFFSET


def test_patch_dataclass_validation():
    with pytest.raises(FirmwareError):
        Patch(offset=-1, old=b"\x00", new=b"\x01", description="bad")
    with pytest.raises(FirmwareError):
        Patch(offset=0, old=b"", new=b"", description="empty")
    with pytest.raises(FirmwareError):
        Patch(offset=0, old=b"\x00", new=b"\x00\x01", description="length mismatch")


# -- patch_file ---------------------------------------------------------------


def test_patch_file_writes_atomic_output_and_reports_hashes(tmp_path: Path):
    source = tmp_path / "orig.bin"
    source.write_bytes(_v113_image().to_bytes())
    destination = tmp_path / "patched" / "out.bin"

    plan = patch_file(source, destination)

    assert isinstance(plan, PatchPlan)
    assert plan.before_sha256 == _v113_image().sha256()
    out = FirmwareImage.from_path(destination)
    assert plan.after_sha256 == out.sha256()
    assert plan.after_sha256 != plan.before_sha256
    assert out.version_string() == "V1.14"
    assert _u32(out, EFFECTIVE_EPOCH_OFFSET) == PATCHED_EPOCH
    # Source untouched, no temp files left behind.
    assert source.read_bytes() == _v113_image().to_bytes()
    assert sorted(p.name for p in destination.parent.iterdir()) == ["out.bin"]


def test_patch_file_refuses_in_place_write(tmp_path: Path):
    source = tmp_path / "orig.bin"
    source.write_bytes(_v112_image().to_bytes())
    with pytest.raises(FirmwareError) as excinfo:
        patch_file(source, source)
    assert "refusing to overwrite the input" in str(excinfo.value)
    assert source.read_bytes() == _v112_image().to_bytes()


def test_patch_file_allows_in_place_write_when_confirmed(tmp_path: Path):
    source = tmp_path / "orig.bin"
    source.write_bytes(_v112_image().to_bytes())
    plan = patch_file(source, source, overwrite_input=True)
    patched = FirmwareImage.from_path(source)
    assert patched.version_string() == "V1.14"
    assert plan.after_sha256 == patched.sha256()
    assert plan.before_sha256 == _v112_image().sha256()


def test_patch_file_no_version_marker(tmp_path: Path):
    source = tmp_path / "orig.bin"
    source.write_bytes(_v112_image().to_bytes())
    destination = tmp_path / "out.bin"
    patch_file(source, destination, include_version_marker=False)
    out = FirmwareImage.from_path(destination)
    assert out.version_string() == "V1.12"
    assert _u32(out, EFFECTIVE_EPOCH_OFFSET) == PATCHED_EPOCH


def test_patch_file_failure_leaves_destination_untouched(tmp_path: Path, monkeypatch):
    source = tmp_path / "orig.bin"
    source.write_bytes(_v112_image().to_bytes())
    destination = tmp_path / "out.bin"
    destination.write_bytes(b"previous")

    def _boom(self, path):
        raise FirmwareError("disk full")

    monkeypatch.setattr(FirmwareImage, "save", _boom)
    with pytest.raises(FirmwareError):
        patch_file(source, destination)
    assert destination.read_bytes() == b"previous"


def test_patch_file_on_already_patched_input_raises(tmp_path: Path):
    patched = apply_patch_plan(_v113_image(), build_patch_plan(_v113_image()))
    source = tmp_path / "already.bin"
    source.write_bytes(patched.to_bytes())
    destination = tmp_path / "out.bin"
    with pytest.raises(FirmwareError) as excinfo:
        patch_file(source, destination)
    assert "already patched" in str(excinfo.value)
    assert not destination.exists()


def test_patch_file_missing_source_raises(tmp_path: Path):
    with pytest.raises(FirmwareError):
        patch_file(tmp_path / "nope.bin", tmp_path / "out.bin")


# -- real-firmware verification (skipped without the vendor drop) -------------


@pytest.mark.skipif(
    _real_firmware_path("V1.12") is None or _real_firmware_path("V1.13") is None,
    reason="official V1.12/V1.13 firmware not available on this machine",
)
def test_real_official_images_patch_to_the_documented_hashes(tmp_path: Path):
    expectations = (
        ("V1.12", V112_SIZE, V112_SHA256, V112_PATCHED_SHA256, EXPECTED_CHANGED["V1.12"]),
        ("V1.13", V113_SIZE, V113_SHA256, V113_PATCHED_SHA256, EXPECTED_CHANGED["V1.13"]),
    )
    for version, size, in_sha, out_sha, changed in expectations:
        path = _real_firmware_path(version)
        assert path is not None
        image = FirmwareImage.from_path(path)
        assert len(image) == size
        assert image.sha256() == in_sha
        plan = build_patch_plan(image)
        patched = apply_patch_plan(image, plan)
        assert _changed_offsets(image, patched) == changed
        assert patched.sha256() == out_sha
        destination = tmp_path / f"patched_{version.replace('.', '')}.bin"
        file_plan = patch_file(path, destination)
        assert file_plan.after_sha256 == out_sha
        assert destination.read_bytes() == patched.to_bytes()
