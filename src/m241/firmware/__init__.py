"""Firmware image handling, profile detection and WNRO patching.

Nothing in this subpackage imports :mod:`serial`, so ``m241 patch`` and the
firmware-inspection helpers work on a host with no GPS logger attached.

Quick tour::

    from m241.firmware import FirmwareImage, detect_profile, patch_file

    image = FirmwareImage.from_path("m241_dump.bin")
    profile = detect_profile(image)
    plan = patch_file("m241_dump.bin", "m241_v114_wnro.bin")

The only binary rewrite performed is the WNRO epoch fix (``0x402F4``) plus the
optional cosmetic ``V1.xx`` -> ``V1.14`` marker; every offset and hash comes
from the verified profile database shipped in ``m241/data``.
"""

from __future__ import annotations

from .image import (
    EPOCH_CONSTANT_OFFSETS,
    FIRMWARE_FAMILY,
    GPS_EPOCH_UNIX,
    HEADER_SIZE,
    FirmwareHeader,
    FirmwareImage,
)
from .patcher import (
    WNRO_SHIFT_DAYS,
    WNRO_SHIFT_SECONDS,
    Patch,
    PatchPlan,
    apply_patch_plan,
    build_patch_plan,
    patch_file,
    verify_patched,
)
from .profiles import (
    OFFICIAL_SHA256,
    PROFILES,
    FirmwareProfile,
    detect_profile,
    get_profile,
    list_profiles,
    profile_for_record_size,
    record_bytes_for_version,
)

__all__ = [
    "EPOCH_CONSTANT_OFFSETS",
    "FIRMWARE_FAMILY",
    "GPS_EPOCH_UNIX",
    "HEADER_SIZE",
    "OFFICIAL_SHA256",
    "PROFILES",
    "WNRO_SHIFT_DAYS",
    "WNRO_SHIFT_SECONDS",
    "FirmwareHeader",
    "FirmwareImage",
    "FirmwareProfile",
    "Patch",
    "PatchPlan",
    "apply_patch_plan",
    "build_patch_plan",
    "detect_profile",
    "get_profile",
    "list_profiles",
    "patch_file",
    "profile_for_record_size",
    "record_bytes_for_version",
    "verify_patched",
]
