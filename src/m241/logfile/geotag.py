"""Match photographs to track-log points by capture time.

The M-241 records UTC. Cameras store *local* time in EXIF with no timezone, so
the two only line up once a UTC offset is applied. This module reads the
capture time out of a JPEG's EXIF block, finds the nearest track point, and
produces either a mapping (JSON/CSV) or an **exiftool argument file** that can
be applied with a single command::

    exiftool -@ geotag.args

Nothing here needs a third-party library: the EXIF reader walks the JPEG APP1
segment and the TIFF IFD chain directly, and the track side reuses
:mod:`m241.logfile`.
"""

from __future__ import annotations

import struct
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..errors import M241Error

#: EXIF tags this module reads.
_TAG_DATETIME = 0x0132
_TAG_EXIF_IFD = 0x8769
_TAG_DATETIME_ORIGINAL = 0x9003
_TAG_DATETIME_DIGITIZED = 0x9004
_TAG_GPS_IFD = 0x8825

#: Default maximum gap between a photo and the nearest track point.
DEFAULT_MAX_GAP_SECONDS = 300.0


class GeotagError(M241Error):
    """A photo or track could not be used for geotagging."""


@dataclass(frozen=True)
class PhotoTime:
    """A photograph and the moment it was taken (naive local time + UTC)."""

    path: Path
    local_time: datetime | None
    source: str

    @property
    def ok(self) -> bool:
        return self.local_time is not None


@dataclass(frozen=True)
class Match:
    """One photo matched to a track point."""

    photo: Path
    taken_utc: datetime
    lat: float
    lon: float
    height_m: float | None
    track_utc: datetime
    gap_seconds: float

    @property
    def gap_ok(self) -> bool:
        return True


# ---------------------------------------------------------------------------
# Minimal EXIF reader
# ---------------------------------------------------------------------------


def _read_ifd(data: bytes, tiff: int, offset: int, little: bool) -> dict[int, tuple[int, int, int]]:
    """Return ``{tag: (type, count, value_offset)}`` for one IFD."""
    entries: dict[int, tuple[int, int, int]] = {}
    fmt = "<" if little else ">"
    if offset + 2 > len(data) - tiff:
        return entries
    (count,) = struct.unpack_from(fmt + "H", data, tiff + offset)
    base = tiff + offset + 2
    for index in range(count):
        entry = base + index * 12
        if entry + 12 > len(data):
            break
        tag, kind, number = struct.unpack_from(fmt + "HHI", data, entry)
        value = struct.unpack_from(fmt + "I", data, entry + 8)[0]
        entries[tag] = (kind, number, value)
    return entries


def _ascii_value(data: bytes, tiff: int, entry: tuple[int, int, int], little: bool) -> str | None:
    kind, count, value = entry
    if kind != 2 or count == 0:
        return None
    if count <= 4:
        raw = struct.pack("<I" if little else ">I", value)[:count]
    else:
        start = tiff + value
        if start + count > len(data):
            return None
        raw = data[start : start + count]
    return raw.split(b"\x00")[0].decode("ascii", "replace").strip()


def _parse_exif_datetime(text: str) -> datetime | None:
    text = text.strip()
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def read_photo_time(path: Path) -> PhotoTime:
    """Return the capture time of *path*, preferring ``DateTimeOriginal``.

    Falls back to the file modification time when no EXIF timestamp exists, so
    a mixed folder still produces a usable mapping.
    """
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise GeotagError(f"cannot read {path}: {exc}") from exc

    if data[:2] == b"\xff\xd8":
        index = 2
        while index + 4 <= len(data):
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                index += 2
                continue
            if index + 4 > len(data):
                break
            (length,) = struct.unpack_from(">H", data, index + 2)
            segment = data[index + 4 : index + 2 + length]
            if marker == 0xE1 and segment[:6] == b"Exif\x00\x00":
                tiff = index + 4 + 6
                if tiff + 8 > len(data):
                    break
                order = data[tiff : tiff + 2]
                if order == b"II":
                    little = True
                elif order == b"MM":
                    little = False
                else:
                    break
                ifd0 = _read_ifd(data, tiff, struct.unpack_from("<I" if little else ">I", data, tiff + 4)[0], little)
                exif_ifd = ifd0.get(_TAG_EXIF_IFD)
                candidates: list[tuple[str, str]] = []
                if exif_ifd is not None:
                    sub = _read_ifd(data, tiff, exif_ifd[2], little)
                    for tag, label in (
                        (_TAG_DATETIME_ORIGINAL, "DateTimeOriginal"),
                        (_TAG_DATETIME_DIGITIZED, "DateTimeDigitized"),
                    ):
                        if tag in sub:
                            text = _ascii_value(data, tiff, sub[tag], little)
                            if text:
                                candidates.append((label, text))
                if _TAG_DATETIME in ifd0:
                    text = _ascii_value(data, tiff, ifd0[_TAG_DATETIME], little)
                    if text:
                        candidates.append(("DateTime", text))
                for label, text in candidates:
                    parsed = _parse_exif_datetime(text)
                    if parsed is not None:
                        return PhotoTime(path=path, local_time=parsed, source=label)
                break
            index += 2 + length

    try:
        stamp = path.stat().st_mtime
    except OSError:
        return PhotoTime(path=path, local_time=None, source="unreadable")
    return PhotoTime(
        path=path,
        local_time=datetime.fromtimestamp(stamp),
        source="file mtime",
    )


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def collect_photos(paths: Iterable[Path], *, recursive: bool = False) -> list[Path]:
    """Expand directories into a sorted list of image files."""
    suffixes = {".jpg", ".jpeg", ".jpe", ".png", ".tif", ".tiff"}
    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            iterator = path.rglob("*") if recursive else path.glob("*")
            found.extend(
                p for p in iterator if p.is_file() and p.suffix.lower() in suffixes
            )
        elif path.is_file():
            found.append(path)
        else:
            raise GeotagError(f"no such file or directory: {path}")
    return sorted(set(found))


def match_photos(
    photos: Sequence[PhotoTime],
    track: Sequence[tuple[datetime, float, float, float | None]],
    *,
    utc_offset: timedelta = timedelta(0),
    max_gap_seconds: float = DEFAULT_MAX_GAP_SECONDS,
) -> tuple[list[Match], list[tuple[Path, str]]]:
    """Pair each photo with the nearest track point.

    Returns ``(matches, skipped)`` where *skipped* is ``(path, reason)``.
    """
    if not track:
        raise GeotagError("the track log contains no points with a position")

    ordered = sorted(track, key=lambda item: item[0])
    times = [item[0] for item in ordered]
    matches: list[Match] = []
    skipped: list[tuple[Path, str]] = []

    for photo in photos:
        if photo.local_time is None:
            skipped.append((photo.path, "no capture time"))
            continue
        taken_utc = (photo.local_time - utc_offset).replace(tzinfo=timezone.utc)

        # binary search for the closest point in time
        lo, hi = 0, len(times) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if times[mid] < taken_utc:
                lo = mid + 1
            else:
                hi = mid
        best = lo
        if lo > 0:
            before = times[lo - 1]
            after = times[lo]
            if abs((before - taken_utc).total_seconds()) <= abs(
                (after - taken_utc).total_seconds()
            ):
                best = lo - 1
        track_utc, lat, lon, height = ordered[best]
        gap = abs((track_utc - taken_utc).total_seconds())
        if gap > max_gap_seconds:
            skipped.append(
                (
                    photo.path,
                    f"nearest track point is {gap:.0f}s away "
                    f"(limit {max_gap_seconds:.0f}s)",
                )
            )
            continue
        matches.append(
            Match(
                photo=photo.path,
                taken_utc=taken_utc,
                lat=lat,
                lon=lon,
                height_m=height,
                track_utc=track_utc,
                gap_seconds=gap,
            )
        )
    return matches, skipped


# ---------------------------------------------------------------------------
# Output formats
# ---------------------------------------------------------------------------


def _decimal_to_dms(value: float) -> tuple[float, float, float]:
    absolute = abs(value)
    degrees = int(absolute)
    minutes_float = (absolute - degrees) * 60
    minutes = int(minutes_float)
    seconds = (minutes_float - minutes) * 60
    return degrees, minutes, seconds


def format_exiftool_args(matches: Sequence[Match]) -> str:
    """Return an exiftool argument file (``exiftool -@ file``).

    Each photo gets its GPS tags written.  Values are given as decimal degrees
    with the reference letter, which exiftool accepts directly.
    """
    lines: list[str] = []
    for match in matches:
        lat_ref = "N" if match.lat >= 0 else "S"
        lon_ref = "E" if match.lon >= 0 else "W"
        lines.append(f"-GPSLatitude={abs(match.lat):.7f}")
        lines.append(f"-GPSLatitudeRef={lat_ref}")
        lines.append(f"-GPSLongitude={abs(match.lon):.7f}")
        lines.append(f"-GPSLongitudeRef={lon_ref}")
        if match.height_m is not None:
            lines.append(f"-GPSAltitude={match.height_m:.2f}")
            lines.append("-GPSAltitudeRef=0")
        lines.append("-DateTimeOriginal<EXIF:DateTimeOriginal")
        lines.append(str(match.photo))
        lines.append("-execute")
    return "\n".join(lines) + ("\n" if lines else "")


def format_csv(matches: Sequence[Match]) -> str:
    """Return a CSV mapping photo path to coordinates."""
    lines = ["photo,utc,lat,lon,height_m,gap_seconds"]
    for match in matches:
        height = "" if match.height_m is None else f"{match.height_m:.2f}"
        lines.append(
            f'"{match.photo}",{match.taken_utc.isoformat()},'
            f"{match.lat:.7f},{match.lon:.7f},{height},{match.gap_seconds:.1f}"
        )
    return "\n".join(lines) + "\n"


def format_json(matches: Sequence[Match]) -> str:
    """Return a JSON array of matches."""
    import json

    payload = [
        {
            "photo": str(match.photo),
            "utc": match.taken_utc.isoformat(),
            "lat": round(match.lat, 7),
            "lon": round(match.lon, 7),
            "height_m": None if match.height_m is None else round(match.height_m, 2),
            "track_utc": match.track_utc.isoformat(),
            "gap_seconds": round(match.gap_seconds, 1),
        }
        for match in matches
    ]
    return json.dumps(payload, indent=2) + "\n"
