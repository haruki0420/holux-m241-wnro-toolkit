"""Firmware patching: the WNRO fix plus a cosmetic version marker.

The second GPS week-number rollover (2019-04-06) makes 2nd-generation M-241
units report dates 1024 weeks in the past.  The fix verified on hardware is a
single little-endian ``u32`` store at ``0x402F4``::

    315964800 (0x12D53D80)  ->  935280000 (0x37BF3D80)

``935280000 - 315964800 == 619315200 == 1024 * 604800`` == +1024 weeks
(= +7168 days).  The first occurrence of the same constant at ``0x402DC`` is
an unused alternate copy; patching it as well double-shifts the clock (verified
on hardware by the original author), so exactly one occurrence is touched.

The optional version-marker patch rewrites the whole 5-byte ASCII run
(``V1.12``/``V1.13`` -> ``V1.14``) so a modified unit is identifiable from its
OLED display.  It is enabled by default and can be turned off.
"""

from __future__ import annotations

import os
import re
import struct
from dataclasses import dataclass
from pathlib import Path

from ..errors import FirmwareError, FirmwareProfileError
from .image import FirmwareImage
from .profiles import PROFILES, FirmwareProfile, detect_profile

__all__ = [
    "WNRO_SHIFT_DAYS",
    "WNRO_SHIFT_SECONDS",
    "Patch",
    "PatchPlan",
    "apply_patch_plan",
    "build_patch_plan",
    "patch_file",
    "verify_patched",
]

#: +1024 weeks in seconds: the WNRO shift applied to the epoch constant.
WNRO_SHIFT_SECONDS = 1024 * 604800  # 619315200

#: ``+1024 weeks`` in days (what the NMEA repair path uses).
WNRO_SHIFT_DAYS = 7168

_VERSION_RE = re.compile(rb"V1\.[0-9]{2}")


@dataclass(frozen=True)
class Patch:
    """One byte-range rewrite inside a firmware image."""

    offset: int
    old: bytes
    new: bytes
    description: str

    def __post_init__(self) -> None:
        if self.offset < 0:
            raise FirmwareError(f"negative patch offset {self.offset}", offset=self.offset)
        if not self.old or len(self.old) != len(self.new):
            raise FirmwareError(
                "patch old/new byte runs must be non-empty and the same length",
                offset=self.offset,
            )

    @property
    def size(self) -> int:
        """Number of bytes the patch spans."""
        return len(self.new)

    @property
    def changed_bytes(self) -> list[int]:
        """Absolute offsets whose byte value actually changes."""
        return [
            self.offset + index
            for index, (before, after) in enumerate(zip(self.old, self.new, strict=True))
            if before != after
        ]


@dataclass
class PatchPlan:
    """An ordered set of patches plus the profile they were built against."""

    profile: FirmwareProfile
    patches: list[Patch]
    target_version: str
    #: SHA256 of the source image (filled in by :func:`patch_file`).
    before_sha256: str = ""
    #: SHA256 of the patched image (filled in by :func:`patch_file`).
    after_sha256: str = ""

    def changed_offsets(self) -> list[int]:
        """Every absolute offset the plan modifies, ascending and unique."""
        offsets = {offset for patch in self.patches for offset in patch.changed_bytes}
        return sorted(offsets)

    def summary(self) -> str:
        """Human-readable multi-line summary of the plan."""
        lines = [
            f"profile: {self.profile.name} ({self.profile.display_name})",
            f"target version: {self.target_version}",
            f"patches: {len(self.patches)}",
        ]
        for patch in self.patches:
            offsets = patch.changed_bytes
            if offsets:
                where = ", ".join(f"0x{offset:X}" for offset in offsets)
            else:
                where = "no bytes change"
            lines.append(
                f"  - 0x{patch.offset:X} [{patch.size} bytes] {patch.description}: {where}"
            )
        return "\n".join(lines)


def _read_u32(image: FirmwareImage, offset: int) -> int:
    """Read a little-endian ``u32`` with a bounds check."""
    if offset < 0 or offset + 4 > len(image):
        raise FirmwareError(
            f"offset 0x{offset:X} is outside the {len(image)}-byte image", offset=offset
        )
    return struct.unpack_from("<I", image.data, offset)[0]


def _raise_if_already_patched(image: FirmwareImage, wnro_shift_seconds: int) -> None:
    """Turn "no profile matches" into a precise "already patched" error when true.

    A patched image no longer matches any shipped profile (its hash, size and
    version marker all changed), so :func:`detect_profile` raises before the
    epoch constant can be inspected.  This helper recognises the already
    forwarded clock and reports the real cause.
    """
    for profile in PROFILES.values():
        if len(image) not in profile.sizes or profile.epoch_offset + 4 > len(image):
            continue
        value = _read_u32(image, profile.epoch_offset)
        if value == profile.epoch_constant + wnro_shift_seconds:
            raise FirmwareError(
                "image is already patched: the effective epoch constant at "
                f"0x{profile.epoch_offset:X} already equals 0x{value:08X}",
                offset=profile.epoch_offset,
            )


def _version_run(image: FirmwareImage) -> tuple[int, bytes] | None:
    """Locate the 5-byte ASCII version run, if the image has one."""
    match = _VERSION_RE.search(image.data)
    if match is None:
        return None
    return match.start(), bytes(image.data[match.start() : match.start() + 5])


def _validate_target_version(target_version: str) -> bytes:
    """Validate a requested marker value and return its ASCII bytes."""
    value = target_version.encode("ascii", "ignore") if isinstance(target_version, str) else b""
    if len(value) != 5 or _VERSION_RE.fullmatch(value) is None:
        raise FirmwareError(
            f"invalid target version {target_version!r}: expected a 5-byte 'V1.xx' marker"
        )
    return value


def build_patch_plan(
    image: FirmwareImage,
    *,
    profile: FirmwareProfile | None = None,
    target_version: str = "V1.14",
    include_version_marker: bool = True,
    wnro_shift_seconds: int = WNRO_SHIFT_SECONDS,
) -> PatchPlan:
    """Build the patch plan for ``image`` without touching it.

    The plan always contains the WNRO epoch fix; the version marker is added
    when ``include_version_marker`` is true and the image does not already
    carry ``target_version``.

    Args:
        image: The firmware image to patch (not modified).
        profile: Profile override (the CLI's ``--profile``); detected when
            omitted.
        target_version: Marker value written over the ASCII version run.
        include_version_marker: Add the cosmetic 5-byte marker patch.
        wnro_shift_seconds: Epoch shift; defaults to +1024 weeks.

    Raises:
        FirmwareProfileError: no profile matches the image.
        FirmwareError: the image is already patched, has an unexpected epoch
            constant, or is structurally incompatible with the profile.
    """
    if wnro_shift_seconds <= 0:
        raise FirmwareError(f"WNRO shift must be positive, got {wnro_shift_seconds}")

    if profile is not None:
        resolved = profile
    else:
        try:
            resolved = detect_profile(image)
        except FirmwareProfileError:
            _raise_if_already_patched(image, wnro_shift_seconds)
            raise

    if len(image) not in resolved.sizes:
        raise FirmwareError(
            f"image size {len(image)} bytes is not accepted by profile "
            f"{resolved.name!r} (expected one of {', '.join(str(s) for s in resolved.sizes)})",
            offset=0,
        )

    epoch_offset = resolved.epoch_offset
    current_epoch = _read_u32(image, epoch_offset)
    target_epoch = resolved.epoch_constant + wnro_shift_seconds
    if target_epoch > 0xFFFFFFFF:
        raise FirmwareError(
            f"WNRO shift {wnro_shift_seconds} overflows the 32-bit epoch constant"
        )

    if current_epoch == target_epoch:
        raise FirmwareError(
            "image is already patched: the effective epoch constant at "
            f"0x{epoch_offset:X} already equals 0x{target_epoch:08X}",
            offset=epoch_offset,
        )
    if current_epoch != resolved.epoch_constant:
        raise FirmwareError(
            f"unexpected epoch constant 0x{current_epoch:08X} at 0x{epoch_offset:X}; "
            f"expected 0x{resolved.epoch_constant:08X}",
            offset=epoch_offset,
        )

    patches: list[Patch] = [
        Patch(
            offset=epoch_offset,
            old=struct.pack("<I", resolved.epoch_constant),
            new=struct.pack("<I", target_epoch),
            description=(
                "WNRO fix: shift the Unix epoch base forward by "
                f"+{wnro_shift_seconds} s (+{wnro_shift_seconds // 604800} weeks)"
            ),
        )
    ]

    if include_version_marker:
        marker = _validate_target_version(target_version)
        found = _version_run(image)
        if found is None:
            raise FirmwareError(
                "image has no ASCII 'V1.xx' version marker to rewrite",
                offset=resolved.version_offset,
            )
        version_offset, current_version = found
        if current_version != marker:
            patches.append(
                Patch(
                    offset=version_offset,
                    old=current_version,
                    new=marker,
                    description=(
                        "version marker: "
                        f"{current_version.decode('ascii', 'replace')} -> "
                        f"{marker.decode('ascii', 'replace')}"
                    ),
                )
            )

    return PatchPlan(
        profile=resolved,
        patches=patches,
        target_version=target_version,
        before_sha256=image.sha256(),
    )


def apply_patch_plan(image: FirmwareImage, plan: PatchPlan) -> FirmwareImage:
    """Return a patched copy of ``image`` (the input is never modified).

    Raises:
        FirmwareError: the plan does not fit the image, the expected original
            bytes are absent, or the image is already patched.
    """
    if len(image) not in plan.profile.sizes:
        raise FirmwareError(
            f"image size {len(image)} bytes does not match plan profile {plan.profile.name!r}",
            offset=0,
        )

    patched = FirmwareImage.from_bytes(image.to_bytes())
    for patch in plan.patches:
        end = patch.offset + patch.size
        if patch.offset < 0 or end > len(patched):
            raise FirmwareError(
                f"patch at 0x{patch.offset:X} does not fit the {len(patched)}-byte image",
                offset=patch.offset,
            )
        current = bytes(patched.data[patch.offset : end])
        if current == patch.new:
            raise FirmwareError(
                f"image is already patched at 0x{patch.offset:X}",
                offset=patch.offset,
            )
        if current != patch.old:
            raise FirmwareError(
                f"unexpected bytes at 0x{patch.offset:X}: found {current.hex()}, "
                f"expected {patch.old.hex()}",
                offset=patch.offset,
            )
        patched.data[patch.offset : end] = patch.new
    return patched


def verify_patched(patched: FirmwareImage, plan: PatchPlan) -> None:
    """Verify that ``patched`` carries every rewrite the plan promises.

    Raises:
        FirmwareError: a patch is missing, a target offset holds unexpected
            bytes, or the size no longer matches the profile.
    """
    for patch in plan.patches:
        end = patch.offset + patch.size
        if end > len(patched):
            raise FirmwareError(
                f"patched image is too short for the patch at 0x{patch.offset:X}",
                offset=patch.offset,
            )
        current = bytes(patched.data[patch.offset : end])
        if current != patch.new:
            raise FirmwareError(
                f"patch at 0x{patch.offset:X} was not applied: found {current.hex()}, "
                f"expected {patch.new.hex()}",
                offset=patch.offset,
            )
    if len(patched) not in plan.profile.sizes:
        raise FirmwareError(
            f"patched image size {len(patched)} bytes does not match profile "
            f"{plan.profile.name!r}",
            offset=0,
        )


def patch_file(
    src: str | Path,
    dst: str | Path,
    **kwargs: object,
) -> PatchPlan:
    """Patch ``src`` into ``dst`` and return the applied :class:`PatchPlan`.

    The output is written atomically (temp file in the destination directory +
    ``os.replace``), so a failure never leaves a truncated firmware image
    behind.  The returned plan carries the before/after SHA256 digests in
    :attr:`PatchPlan.before_sha256` / :attr:`PatchPlan.after_sha256`.

    Args:
        src: Source image path.
        dst: Destination path.
        **kwargs: Forwarded to :func:`build_patch_plan` (``profile``,
            ``target_version``, ``include_version_marker``,
            ``wnro_shift_seconds``).  The extra keyword ``overwrite_input``
            (default ``False``) allows ``dst`` to resolve to the same file as
            ``src``; without it an in-place overwrite is refused because a
            bad firmware write would brick the logger.

    Raises:
        FirmwareError: the source cannot be read, is already patched, or the
            destination is the source without ``overwrite_input``.
    """
    overwrite_input = bool(kwargs.pop("overwrite_input", False))
    source = Path(src)
    destination = Path(dst)
    if not overwrite_input and _same_path(source, destination):
        raise FirmwareError(
            f"refusing to overwrite the input firmware image in place ({source}); "
            "pass overwrite_input=True (or write to a different file) to confirm"
        )

    image = FirmwareImage.from_path(source)
    plan = build_patch_plan(image, **kwargs)  # type: ignore[arg-type]
    patched = apply_patch_plan(image, plan)
    verify_patched(patched, plan)
    patched.save(destination)
    plan.after_sha256 = patched.sha256()
    return plan


def _same_path(left: Path, right: Path) -> bool:
    """Best-effort "these two paths name the same file" test."""
    try:
        return left.resolve() == right.resolve()
    except OSError:  # pragma: no cover - defensive (unresolvable parents)
        return os.path.normcase(os.path.abspath(left)) == os.path.normcase(
            os.path.abspath(right)
        )
