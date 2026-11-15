"""Tests for the log download (PMTK182) and photo geotagging modules.

Both run without hardware: the downloader is driven by a fake serial port that
speaks the PMTK182 protocol, and the geotagger uses synthetic JPEGs with a real
EXIF APP1 segment.
"""

from __future__ import annotations

import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from m241.errors import M241Error
from m241.logfile.download import (
    LogDownloader,
    LogDownloadError,
    download_log,
    nmea_checksum,
    with_checksum,
)
from m241.logfile.geotag import (
    GeotagError,
    PhotoTime,
    collect_photos,
    format_csv,
    format_exiftool_args,
    format_json,
    match_photos,
    read_photo_time,
)

# ---------------------------------------------------------------------------
# a fake device that speaks PMTK182
# ---------------------------------------------------------------------------


class FakeLogDevice:
    """Minimal PMTK182 responder backed by a byte buffer."""

    def __init__(self, payload: bytes, *, refuse_disable: bool = False) -> None:
        self.payload = payload
        self.refuse_disable = refuse_disable
        self.enabled = True
        self.output = bytearray()
        self.incoming = bytearray()
        self.reads: list[tuple[int, int]] = []
        self.commands: list[str] = []

    # -- serial API ------------------------------------------------------
    def write(self, data: bytes) -> int:
        self.incoming += data
        while b"\n" in self.incoming:
            raw, _, rest = bytes(self.incoming).partition(b"\n")
            self.incoming = bytearray(rest)
            self._handle(raw.decode("ascii", "replace").strip())
        return len(data)

    def flush(self) -> None:
        return None

    def read(self, count: int = 1) -> bytes:
        if not self.output:
            return b""
        take = self.output[:count]
        del self.output[:count]
        return bytes(take)

    def reset_input_buffer(self) -> None:
        self.output.clear()

    def close(self) -> None:
        return None

    # -- protocol --------------------------------------------------------
    def _emit(self, sentence: str) -> None:
        body = sentence[1:]
        checksum = 0
        for char in body:
            checksum ^= ord(char)
        self.output += f"{sentence}*{checksum:02X}\r\n".encode("ascii")

    def _handle(self, line: str) -> None:
        if not line.startswith("$"):
            return
        body = line[1:].split("*")[0]
        self.commands.append(body)
        fields = body.split(",")
        if fields[0] != "PMTK182":
            return
        sub = fields[1]
        if sub == "2" and len(fields) > 2:
            which = fields[2]
            if which == "8":
                self._emit(f"$PMTK182,3,8,{len(self.payload):08X}")
            elif which == "1":
                self._emit(f"$PMTK182,3,1,{1 if self.enabled else 0}")
            elif which == "2":
                self._emit("$PMTK182,3,2,05000000")
            return
        if sub == "5":
            if self.refuse_disable:
                self._emit("$PMTK001,182,5,0")
            else:
                self.enabled = False
                self._emit("$PMTK001,182,5,3")
            return
        if sub == "4":
            self.enabled = True
            self._emit("$PMTK001,182,4,3")
            return
        if sub == "6":
            self.payload = b""
            self._emit("$PMTK001,182,6,3")
            return
        if sub == "7" and len(fields) >= 4:
            address = int(fields[2], 16)
            length = int(fields[3], 16)
            self.reads.append((address, length))
            chunk = self.payload[address : address + length]
            self._emit(f"$PMTK182,8,{address:08X},{chunk.hex().upper()}")
            return


# ---------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------


def test_checksum_matches_hardware() -> None:
    # Measured on a retail M-241: $PMTK182,2,8*33
    assert nmea_checksum("$PMTK182,2,8") == 0x33
    assert with_checksum("$PMTK182,2,8") == "$PMTK182,2,8*33"


def test_status_reports_usage_and_state() -> None:
    device = FakeLogDevice(b"\x00" * 0x1234)
    status = LogDownloader(device).status()
    assert status.used_bytes == 0x1234
    assert status.enabled is True


def test_download_reads_everything_in_chunks() -> None:
    payload = bytes((index * 7 + 3) & 0xFF for index in range(0x1000))
    device = FakeLogDevice(payload)
    data = download_log(device, chunk=0x100)
    assert data == payload
    assert len(device.reads) == 0x10
    assert all(length == 0x100 for _address, length in device.reads)


def test_download_disables_and_reenables_logging() -> None:
    device = FakeLogDevice(b"\xAA" * 0x200)
    download_log(device)
    assert any(command.startswith("PMTK182,5") for command in device.commands)
    assert any(command.startswith("PMTK182,4") for command in device.commands)
    assert device.enabled is True


def test_download_reenables_logging_after_a_failure() -> None:
    device = FakeLogDevice(b"\xAA" * 0x200, refuse_disable=True)
    with pytest.raises(LogDownloadError):
        download_log(device)
    # the disable was refused, so logging must still be on
    assert device.enabled is True


def test_download_reports_progress() -> None:
    device = FakeLogDevice(b"\x55" * 0x400)
    seen: list[tuple[int, int]] = []
    download_log(device, chunk=0x100, progress_cb=lambda done, total: seen.append((done, total)))
    assert seen[-1] == (0x400, 0x400)
    assert [done for done, _total in seen] == [0x100, 0x200, 0x300, 0x400]


def test_download_empty_log_returns_nothing() -> None:
    device = FakeLogDevice(b"")
    assert download_log(device) == b""
    assert device.reads == []


def test_download_rejects_a_silent_device() -> None:
    class Silent(FakeLogDevice):
        def _handle(self, line: str) -> None:
            return None

    with pytest.raises(LogDownloadError, match="did not answer"):
        download_log(Silent(b"\x00" * 16))


# ---------------------------------------------------------------------------
# geotag: EXIF reading
# ---------------------------------------------------------------------------


def _make_jpeg_with_exif(path: Path, stamp: str) -> None:
    """Write a tiny JPEG whose APP1 carries DateTimeOriginal=*stamp*."""
    # TIFF header (little endian) + IFD0 with one entry pointing at ExifIFD
    # + ExifIFD with DateTimeOriginal.
    exif_value = stamp.encode("ascii") + b"\x00"  # 20 bytes
    tiff = bytearray()
    tiff += b"II" + struct.pack("<H", 42) + struct.pack("<I", 8)
    # IFD0: 1 entry (ExifIFD pointer)
    ifd0_offset = 8
    exif_ifd_offset = ifd0_offset + 2 + 12 + 4
    tiff += struct.pack("<H", 1)
    tiff += struct.pack("<HHI", 0x8769, 4, 1) + struct.pack("<I", exif_ifd_offset)
    tiff += struct.pack("<I", 0)  # next IFD
    # ExifIFD: 1 entry, value stored after the IFD
    value_offset = exif_ifd_offset + 2 + 12 + 4
    tiff += struct.pack("<H", 1)
    tiff += struct.pack("<HHI", 0x9003, 2, len(exif_value))
    tiff += struct.pack("<I", value_offset)
    tiff += struct.pack("<I", 0)
    tiff += exif_value

    app1 = b"Exif\x00\x00" + bytes(tiff)
    segment = b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1
    path.write_bytes(b"\xff\xd8" + segment + b"\xff\xd9")


def test_read_photo_time_from_exif(tmp_path: Path) -> None:
    photo = tmp_path / "shot.jpg"
    _make_jpeg_with_exif(photo, "2026:11:15 10:30:00")
    result = read_photo_time(photo)
    assert result.local_time == datetime(2026, 11, 15, 10, 30, 0)
    assert result.source == "DateTimeOriginal"
    assert result.ok


def test_read_photo_time_falls_back_to_mtime(tmp_path: Path) -> None:
    photo = tmp_path / "plain.jpg"
    photo.write_bytes(b"\xff\xd8\xff\xd9")
    result = read_photo_time(photo)
    assert result.local_time is not None
    assert result.source == "file mtime"


def test_collect_photos_expands_directories(tmp_path: Path) -> None:
    (tmp_path / "a.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    (tmp_path / "b.JPG").write_bytes(b"\xff\xd8\xff\xd9")
    (tmp_path / "notes.txt").write_text("ignore me")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    assert len(collect_photos([tmp_path])) == 2
    assert len(collect_photos([tmp_path], recursive=True)) == 3


# ---------------------------------------------------------------------------
# geotag: matching
# ---------------------------------------------------------------------------


def _track() -> list[tuple[datetime, float, float, float | None]]:
    base = datetime(2026, 11, 15, 1, 0, 0, tzinfo=timezone.utc)
    return [
        (base + timedelta(seconds=offset), 36.0 + offset * 1e-5, 137.0 + offset * 1e-5, 10.0)
        for offset in (0, 60, 120, 180)
    ]


def test_match_picks_the_nearest_point() -> None:
    track = _track()
    photos = [
        PhotoTime(Path("a.jpg"), datetime(2026, 11, 15, 1, 1, 5), "DateTimeOriginal"),
        PhotoTime(Path("b.jpg"), datetime(2026, 11, 15, 1, 2, 55), "DateTimeOriginal"),
    ]
    matches, skipped = match_photos(photos, track)
    assert skipped == []
    assert [m.photo.name for m in matches] == ["a.jpg", "b.jpg"]
    assert matches[0].gap_seconds == pytest.approx(5.0)
    assert matches[1].gap_seconds == pytest.approx(5.0)


def test_match_applies_the_camera_utc_offset() -> None:
    track = _track()
    # camera clock reads JST (+09:00) for 01:00 UTC
    photos = [PhotoTime(Path("a.jpg"), datetime(2026, 11, 15, 10, 0, 10), "DateTimeOriginal")]
    matches, _skipped = match_photos(photos, track, utc_offset=timedelta(hours=9))
    assert len(matches) == 1
    assert matches[0].gap_seconds == pytest.approx(10.0)


def test_match_skips_photos_beyond_the_gap() -> None:
    track = _track()
    photos = [PhotoTime(Path("late.jpg"), datetime(2026, 11, 15, 5, 0, 0), "DateTimeOriginal")]
    matches, skipped = match_photos(photos, track, max_gap_seconds=60)
    assert matches == []
    assert skipped[0][0].name == "late.jpg"
    assert "away" in skipped[0][1]


def test_match_skips_photos_without_a_time() -> None:
    photos = [PhotoTime(Path("x.jpg"), None, "unreadable")]
    matches, skipped = match_photos(photos, _track())
    assert matches == []
    assert skipped[0][1] == "no capture time"


def test_match_rejects_an_empty_track() -> None:
    with pytest.raises(GeotagError):
        match_photos([], [])


# ---------------------------------------------------------------------------
# geotag: output formats
# ---------------------------------------------------------------------------


def _matches():
    track = _track()
    photos = [PhotoTime(Path("a.jpg"), datetime(2026, 11, 15, 1, 0, 5), "DateTimeOriginal")]
    matches, _skipped = match_photos(photos, track)
    return matches


def test_exiftool_args_contain_gps_tags() -> None:
    text = format_exiftool_args(_matches())
    assert "-GPSLatitude=36.0000000" in text
    assert "-GPSLatitudeRef=N" in text
    assert "-GPSLongitudeRef=E" in text
    assert "-GPSAltitude=10.00" in text
    assert "a.jpg" in text
    assert text.rstrip().endswith("-execute")


def test_csv_and_json_formats() -> None:
    matches = _matches()
    csv_text = format_csv(matches)
    assert csv_text.splitlines()[0] == "photo,utc,lat,lon,height_m,gap_seconds"
    assert "a.jpg" in csv_text

    json_text = format_json(matches)
    assert '"photo": "a.jpg"' in json_text
    assert '"lat":' in json_text


def test_geotag_error_is_an_m241_error() -> None:
    assert issubclass(GeotagError, M241Error)

# ---------------------------------------------------------------------------
# erase
# ---------------------------------------------------------------------------


def test_erase_stops_logging_erases_and_restarts() -> None:
    from m241.logfile.download import erase_log

    device = FakeLogDevice(b"\xAA" * 0x400)
    freed = erase_log(device)
    assert freed == 0x400
    assert any(command.startswith("PMTK182,6") for command in device.commands)
    assert device.enabled is True


def test_erase_reports_the_freed_size() -> None:
    from m241.logfile.download import erase_log

    device = FakeLogDevice(b"\x00" * 0x1234)
    assert erase_log(device) == 0x1234


def test_erase_refuses_when_the_device_does_not_clear() -> None:
    from m241.logfile.download import LogEraseError, erase_log

    class Stubborn(FakeLogDevice):
        def _handle(self, line: str) -> None:
            body = line[1:].split("*")[0]
            if body.startswith("PMTK182,6"):
                self.commands.append(body)
                self._emit("$PMTK001,182,6,3")
                return
            super()._handle(line)

    with pytest.raises(LogEraseError, match="still reports"):
        erase_log(Stubborn(b"\xAA" * 0x100))


def test_erase_refuses_when_the_device_rejects_it() -> None:
    from m241.logfile.download import LogEraseError, erase_log

    class Refusing(FakeLogDevice):
        def _handle(self, line: str) -> None:
            body = line[1:].split("*")[0]
            if body.startswith("PMTK182,6"):
                self.commands.append(body)
                self._emit("$PMTK001,182,6,0")
                return
            super()._handle(line)

    with pytest.raises(LogEraseError, match="refused"):
        erase_log(Refusing(b"\xAA" * 0x100))
