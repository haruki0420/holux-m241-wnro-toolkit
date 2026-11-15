"""MTK/Holux binary log-dump support: records, reader, capacity and exporters.

Public API::

    from m241.logfile import read_log_dump, read_log_file, LogDump, LogRecord
    from m241.logfile import capacity, capacity_table, record_bytes_for_version
    from m241.logfile import export, EXPORTERS
    from m241.logfile import LogDownloader, download_log
    from m241.logfile import match_photos, read_photo_time

Typical use::

    dump = read_log_file("data.bin")
    print(dump.record_length, len(dump.records), dump.fill_ratio())
    open("track.gpx", "w", encoding="utf-8").write(export(dump, "gpx"))
"""

from __future__ import annotations

from .capacity import (
    BLOCK_HEADER,
    BLOCK_SIZE,
    FLASH_SIZES,
    USABLE_PER_BLOCK,
    Capacity,
    capacity,
    capacity_table,
    estimate_points,
    flash_name_from_da,
    record_bytes_for_version,
    resolve_flash,
)
from .download import LogDownloader, download_log
from .export import (
    CSV_COLUMNS,
    EXPORT_FORMATS,
    EXPORTERS,
    export,
    to_csv,
    to_gpx,
    to_json,
    to_json_text,
    to_kml,
    to_nmea,
)
from .geotag import match_photos, read_photo_time
from .reader import (
    LogDump,
    parse_info_block,
    read_log_dump,
    read_log_file,
)
from .record import (
    BLOCK_HEADER_SIZE,
    LOG_BIT_AZIMUTH,
    LOG_BIT_DAGE,
    LOG_BIT_DISTANCE,
    LOG_BIT_DSTA,
    LOG_BIT_ELEVATION,
    LOG_BIT_HDOP,
    LOG_BIT_HEADING,
    LOG_BIT_HEIGHT,
    LOG_BIT_LATITUDE,
    LOG_BIT_LONGITUDE,
    LOG_BIT_MILLISECOND,
    LOG_BIT_NSAT,
    LOG_BIT_PDOP,
    LOG_BIT_RCR,
    LOG_BIT_SID,
    LOG_BIT_SNR,
    LOG_BIT_SPEED,
    LOG_BIT_UTC,
    LOG_BIT_VALID,
    LOG_BIT_VDOP,
    LOG_FIELD_NAMES,
    M241_FIELD_SIZES,
    BlockHeader,
    LogRecord,
    block_header,
    field_sizes,
    record_length,
)

__all__ = [
    "BLOCK_HEADER",
    "BLOCK_HEADER_SIZE",
    "BLOCK_SIZE",
    "CSV_COLUMNS",
    "EXPORTERS",
    "EXPORT_FORMATS",
    "FLASH_SIZES",
    "LOG_BIT_AZIMUTH",
    "LOG_BIT_DAGE",
    "LOG_BIT_DISTANCE",
    "LOG_BIT_DSTA",
    "LOG_BIT_ELEVATION",
    "LOG_BIT_HDOP",
    "LOG_BIT_HEADING",
    "LOG_BIT_HEIGHT",
    "LOG_BIT_LATITUDE",
    "LOG_BIT_LONGITUDE",
    "LOG_BIT_MILLISECOND",
    "LOG_BIT_NSAT",
    "LOG_BIT_PDOP",
    "LOG_BIT_RCR",
    "LOG_BIT_SID",
    "LOG_BIT_SNR",
    "LOG_BIT_SPEED",
    "LOG_BIT_UTC",
    "LOG_BIT_VALID",
    "LOG_BIT_VDOP",
    "LOG_FIELD_NAMES",
    "M241_FIELD_SIZES",
    "USABLE_PER_BLOCK",
    "BlockHeader",
    "Capacity",
    "LogDownloader",
    "LogDump",
    "LogRecord",
    "block_header",
    "capacity",
    "capacity_table",
    "download_log",
    "estimate_points",
    "export",
    "field_sizes",
    "flash_name_from_da",
    "match_photos",
    "parse_info_block",
    "read_log_dump",
    "read_log_file",
    "record_bytes_for_version",
    "record_length",
    "read_photo_time",
    "resolve_flash",
    "to_csv",
    "to_gpx",
    "to_json",
    "to_json_text",
    "to_kml",
    "to_nmea",
]
