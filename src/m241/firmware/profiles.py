"""Firmware profiles: verified hash/offset metadata for the M-241 images.

The shipped data file :file:`m241/data/firmware_profiles.json` is the single
source of truth for "which image is this and where do its binary patches go".
It is loaded through :mod:`importlib.resources` so an installed wheel and a
plain ``src/`` checkout both work.

Nothing in this module invents offsets: every value comes from
``docs/reference/03-firmware-offsets.md``, which was cross-checked byte for byte
against the official Holux update packages.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..errors import FirmwareError, FirmwareProfileError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .image import FirmwareImage

__all__ = [
    "OFFICIAL_SHA256",
    "PROFILES",
    "PROFILES_DATA_FILE",
    "FirmwareProfile",
    "detect_profile",
    "get_profile",
    "list_profiles",
    "load_profiles",
    "profile_for_record_size",
    "record_bytes_for_version",
]

#: Package-relative location of the shipped profile database.
PROFILES_DATA_FILE = "data/firmware_profiles.json"

#: Log record sizes documented for each firmware generation (independent of
#: the JSON so the helper still answers for versions whose image is absent).
_KNOWN_RECORD_BYTES: dict[str, int] = {
    "V1.10": 16,
    "V1.11": 16,
    "V1.12": 16,
    "V1.13": 20,
}


@dataclass(frozen=True)
class FirmwareProfile:
    """One supported firmware release and everything needed to patch it."""

    name: str
    display_name: str
    sha256: tuple[str, ...]
    sizes: tuple[int, ...]
    version: str
    version_offset: int
    epoch_offset: int
    epoch_constant: int = 315964800
    log_record_bytes: int = 16
    notes: str = ""
    #: Expected SHA256 of the image after the default WNRO + V1.14 patch set
    #: (documented and reproducible; empty when unknown).
    patched_sha256: str = ""

    @property
    def size(self) -> int:
        """The canonical image size (first accepted length)."""
        return self.sizes[0]

    @property
    def version_bytes(self) -> bytes:
        """ASCII form of :attr:`version` (the 5-byte marker run)."""
        return self.version.encode("ascii")


def _profiles_path() -> Any:
    """Locate the shipped JSON, preferring package resources."""
    try:
        candidate = resources.files("m241").joinpath(PROFILES_DATA_FILE)
        if candidate.is_file():
            return candidate
    except (ImportError, AttributeError, OSError):  # pragma: no cover - defensive
        pass
    # Source-tree fallback (``src/m241/data/firmware_profiles.json``).
    fallback = Path(__file__).resolve().parent.parent / "data" / "firmware_profiles.json"
    if fallback.is_file():
        return fallback
    raise FirmwareError(f"firmware profile database not found ({PROFILES_DATA_FILE})")


def _parse_profiles(document: Any) -> dict[str, FirmwareProfile]:
    """Validate and convert the JSON document into profile objects."""
    if not isinstance(document, dict) or "profiles" not in document:
        raise FirmwareError("firmware profile database must be an object with a 'profiles' list")
    entries = document["profiles"]
    if not isinstance(entries, list) or not entries:
        raise FirmwareError("firmware profile database contains no profiles")

    profiles: dict[str, FirmwareProfile] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise FirmwareError("firmware profile entry must be an object")
        try:
            profile = FirmwareProfile(
                name=str(entry["name"]),
                display_name=str(entry["display_name"]),
                sha256=tuple(str(value).lower() for value in entry["sha256"]),
                sizes=tuple(int(value) for value in entry["sizes"]),
                version=str(entry["version"]),
                version_offset=int(entry["version_offset"]),
                epoch_offset=int(entry["epoch_offset"]),
                epoch_constant=int(entry.get("epoch_constant", 315964800)),
                log_record_bytes=int(entry.get("log_record_bytes", 16)),
                notes=str(entry.get("notes", "")),
                patched_sha256=str(entry.get("patched_sha256", "")).lower(),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise FirmwareError(f"invalid firmware profile entry {entry!r}: {exc}") from exc

        if not profile.sha256 or any(len(value) != 64 for value in profile.sha256):
            raise FirmwareError(f"profile {profile.name!r} has a malformed sha256")
        if not profile.sizes or any(size <= 0 for size in profile.sizes):
            raise FirmwareError(f"profile {profile.name!r} has malformed sizes")
        if profile.version_offset >= profile.size or profile.epoch_offset >= profile.size:
            raise FirmwareError(f"profile {profile.name!r} has an offset outside the image")
        if profile.name in profiles:
            raise FirmwareError(f"duplicate firmware profile name {profile.name!r}")
        profiles[profile.name] = profile
    return profiles


def load_profiles() -> dict[str, FirmwareProfile]:
    """Read and validate the shipped profile database."""
    source = _profiles_path()
    try:
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise FirmwareError(f"cannot read firmware profile database: {exc}") from exc
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FirmwareError(f"firmware profile database is not valid JSON: {exc}") from exc
    return _parse_profiles(document)


#: Supported profiles keyed by short name (``"v1.12"``, ``"v1.13"``).
PROFILES: dict[str, FirmwareProfile] = load_profiles()

#: Known official image digest -> profile name.
OFFICIAL_SHA256: dict[str, str] = {
    digest: profile.name for profile in PROFILES.values() for digest in profile.sha256
}


def list_profiles() -> list[FirmwareProfile]:
    """All shipped profiles as a list (ordered as in the JSON database)."""
    return list(PROFILES.values())


def _normalise_name(name: str) -> str:
    return name.strip().lower().lstrip("v")


def get_profile(name: str) -> FirmwareProfile:
    """Look up a profile by name (``v1.12``, ``V1.12`` or ``1.12``).

    Raises:
        FirmwareProfileError: the name is unknown.
    """
    wanted = _normalise_name(name)
    for profile in PROFILES.values():
        if _normalise_name(profile.name) == wanted or _normalise_name(profile.version) == wanted:
            return profile
    known = ", ".join(profile.name for profile in PROFILES.values())
    raise FirmwareProfileError(f"unknown firmware profile {name!r} (known: {known})")


def detect_profile(image: FirmwareImage) -> FirmwareProfile:
    """Identify the profile an image belongs to.

    Matching order: SHA256 first, then (image size, advertised version string,
    effective epoch offset).  This mirrors the frozen contract so a user-built
    dump of the official release and a hand-modified image with a known header
    are both recognised.

    Raises:
        FirmwareProfileError: no shipped profile matches, or the candidate
            profile's epoch constant is absent from the image.
    """
    digest = image.sha256()
    if digest in OFFICIAL_SHA256:
        return PROFILES[OFFICIAL_SHA256[digest]]

    version = image.version_string()
    size = len(image)
    offsets = image.epoch_offsets()

    for profile in PROFILES.values():
        if size not in profile.sizes:
            continue
        if version is None or version != profile.version:
            continue
        if profile.epoch_offset not in offsets:
            continue
        return profile

    detail = f"size {size} bytes"
    if version is not None:
        detail += f", version {version}"
    detail += f", sha256 {digest}"
    known = "; ".join(
        f"{p.name}: {', '.join(str(s) for s in p.sizes)} bytes, {p.version}" for p in PROFILES.values()
    )
    raise FirmwareProfileError(
        f"no firmware profile matches this image ({detail}); known profiles: {known}"
    )


def profile_for_record_size(n: int) -> list[FirmwareProfile]:
    """Profiles whose log records are ``n`` bytes (empty when unknown)."""
    return [profile for profile in PROFILES.values() if profile.log_record_bytes == n]


def record_bytes_for_version(version: str) -> int:
    """Log record size for a firmware version string.

    ``V1.11``/``V1.12`` and older write 16-byte records; ``V1.13`` writes
    20-byte records (it adds a float speed field).

    Raises:
        FirmwareProfileError: the version is not a known M-241 release.
    """
    cleaned = version.strip().upper()
    if not cleaned.startswith("V"):
        cleaned = f"V{cleaned}"
    for profile in PROFILES.values():
        if profile.version.upper() == cleaned:
            return profile.log_record_bytes
    if cleaned in _KNOWN_RECORD_BYTES:
        return _KNOWN_RECORD_BYTES[cleaned]
    known = ", ".join(sorted({*_KNOWN_RECORD_BYTES, *(p.version for p in PROFILES.values())}))
    raise FirmwareProfileError(f"unknown firmware version {version!r} (known: {known})")
