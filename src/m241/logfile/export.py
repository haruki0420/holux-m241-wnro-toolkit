"""Exporters for parsed M-241 log dumps.

Supported formats (see :data:`EXPORTERS`):

``gpx``   GPX 1.1 track (schema-shaped, opens in any mapping application)
``csv``   the column set documented in ``docs/ARCHITECTURE.md``
``nmea``  reconstructed ``$GPRMC`` sentences (one per record, with checksums)
``kml``   KML 2.2 document with a single LineString placemark
``json``  JSON document (serialised by :func:`export`)

All timestamps are emitted in UTC with a trailing ``Z``.
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from xml.sax.saxutils import escape

from ..errors import M241Error
from .record import LogRecord

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .reader import LogDump

GPX_NAME = "M-241 log"

#: XML namespace for the toolkit's GPX/KML extensions.
EXT_NAMESPACE = "https://github.com/haruki0420/holux-m241-wnro-toolkit"

#: CSV header, exactly the documented column set.
CSV_COLUMNS = (
    "index",
    "utc",
    "lat",
    "lon",
    "altitude_m",
    "speed_kmh",
    "heading_deg",
    "hdop",
    "sats",
    "crc_ok",
)

#: Formats accepted by :func:`export`.
EXPORT_FORMATS = ("gpx", "csv", "nmea", "kml", "json")


def _records(records: Iterable[LogRecord]) -> list[LogRecord]:
    return [record for record in records if record is not None]


def _utc_text(record: LogRecord | datetime | None) -> str:
    """RFC 3339 UTC text for *record* (or a bare datetime), ``""`` when missing."""
    if record is None:
        return ""
    if isinstance(record, datetime):
        moment = record.astimezone(timezone.utc)
        text = moment.strftime("%Y-%m-%dT%H:%M:%S")
        if moment.microsecond:
            text += f".{moment.microsecond // 1000:03d}"
        return text + "Z"
    return record.utc_iso or ""


def _xml_text(value: object) -> str:
    return escape(str(value), {'"': "&quot;", "'": "&apos;"})


def _num(value: float | None, fmt: str = "{:.6f}") -> str:
    return "" if value is None else fmt.format(value)


# ---------------------------------------------------------------------------
# GPX
# ---------------------------------------------------------------------------
def to_gpx(records: Iterable[LogRecord], *, name: str = GPX_NAME) -> str:
    """Render *records* as a GPX 1.1 document with a single ``<trk>``.

    Points without a position (lat or lon missing) are skipped because GPX
    requires both.  A record's optional ``waypoint`` flag becomes a separate
    ``<wpt>`` so that button presses survive the conversion.
    """
    items = _records(records)
    out = io.StringIO()
    out.write('<?xml version="1.0" encoding="UTF-8"?>\n')
    out.write(
        '<gpx version="1.1" creator="m241-wnro-toolkit" '
        'xmlns="http://www.topografix.com/GPX/1/1" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xsi:schemaLocation="http://www.topografix.com/GPX/1/1 '
        'http://www.topografix.com/GPX/1/1/gpx.xsd">\n'
    )
    out.write(f"  <metadata>\n    <name>{_xml_text(name)}</name>\n  </metadata>\n")
    for record in items:
        if not record.waypoint or record.lat is None or record.lon is None:
            continue
        out.write(
            f'  <wpt lat="{record.lat:.8f}" lon="{record.lon:.8f}">'
            f"<name>WP{record.index:06d}</name>"
        )
        if record.utc is not None:
            out.write(f"<time>{_utc_text(record)}</time>")
        out.write("</wpt>\n")
    out.write(f"  <trk>\n    <name>{_xml_text(name)}</name>\n    <trkseg>\n")
    for record in items:
        if record.lat is None or record.lon is None:
            continue
        out.write(f'      <trkpt lat="{record.lat:.8f}" lon="{record.lon:.8f}">\n')
        if record.height_m is not None:
            out.write(f"        <ele>{record.height_m:.2f}</ele>\n")
        if record.utc is not None:
            out.write(f"        <time>{_utc_text(record)}</time>\n")
        if record.speed_kmh is not None or record.heading_deg is not None:
            out.write("        <extensions>\n")
            out.write(f'          <m241:track xmlns:m241="{EXT_NAMESPACE}">\n')
            if record.speed_kmh is not None:
                out.write(f"            <m241:speed_kmh>{record.speed_kmh:.3f}</m241:speed_kmh>\n")
            if record.heading_deg is not None:
                out.write(
                    f"            <m241:heading_deg>{record.heading_deg:.3f}"
                    "</m241:heading_deg>\n"
                )
            if record.hdop is not None:
                out.write(f"            <m241:hdop>{record.hdop:.2f}</m241:hdop>\n")
            out.write("          </m241:track>\n        </extensions>\n")
        out.write("      </trkpt>\n")
    out.write("    </trkseg>\n  </trk>\n</gpx>\n")
    return out.getvalue()


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------
def to_csv(records: Iterable[LogRecord]) -> str:
    """Render *records* as CSV with the documented :data:`CSV_COLUMNS` header."""
    out = io.StringIO()
    out.write(",".join(CSV_COLUMNS) + "\n")
    for record in _records(records):
        sats = "" if record.sats_used is None else str(record.sats_used)
        row = [
            str(record.index),
            _utc_text(record),
            _num(record.lat),
            _num(record.lon),
            _num(record.height_m, "{:.2f}"),
            _num(record.speed_kmh, "{:.3f}"),
            _num(record.heading_deg, "{:.3f}"),
            _num(record.hdop, "{:.2f}"),
            sats,
            "true" if record.crc_ok else "false",
        ]
        out.write(",".join(row) + "\n")
    return out.getvalue()


# ---------------------------------------------------------------------------
# NMEA
# ---------------------------------------------------------------------------
def _dm(value: float, positive: str, negative: str) -> tuple[str, str]:
    """Convert decimal degrees to NMEA ``ddmm.mmmm`` plus hemisphere letter."""
    hemisphere = positive if value >= 0 else negative
    absolute = abs(value)
    degrees = int(absolute)
    minutes = (absolute - degrees) * 60.0
    return f"{degrees:02d}{minutes:07.4f}", hemisphere


def _rmc_sentence(record: LogRecord) -> str | None:
    """Build one ``$GPRMC`` sentence from *record* (``None`` if not possible)."""
    if record.utc is None or record.lat is None or record.lon is None:
        return None
    moment = record.utc.astimezone(timezone.utc)
    status = "A" if record.has_fix else "V"
    lat_text, ns = _dm(record.lat, "N", "S")
    lon_text, ew = _dm(record.lon, "E", "W")
    speed_kn = "" if record.speed_kmh is None else f"{record.speed_kmh / 1.852:.2f}"
    course = "" if record.heading_deg is None else f"{record.heading_deg:.2f}"
    body = ",".join(
        [
            "GPRMC",
            moment.strftime("%H%M%S") + f".{moment.microsecond // 1000:03d}",
            status,
            lat_text,
            ns,
            lon_text,
            ew,
            speed_kn,
            course,
            moment.strftime("%d%m%y"),
            "",
            "",
            "A" if status == "A" else "N",
        ]
    )
    from ..nmea.sentence import with_checksum  # local import avoids a cycle

    return with_checksum(body)


def to_nmea(records: Iterable[LogRecord]) -> str:
    """Render *records* as reconstructed ``$GPRMC`` sentences.

    Records without a timestamp or position are skipped: RMC cannot express
    them.  Every line ends with ``\\n`` and carries a valid checksum.
    """
    lines = []
    for record in _records(records):
        sentence = _rmc_sentence(record)
        if sentence is not None:
            lines.append(sentence)
    return "".join(line + "\n" for line in lines)


# ---------------------------------------------------------------------------
# KML
# ---------------------------------------------------------------------------
def to_kml(records: Iterable[LogRecord], *, name: str = GPX_NAME) -> str:
    """Render *records* as a KML 2.2 document with one LineString placemark."""
    items = [r for r in _records(records) if r.lat is not None and r.lon is not None]
    out = io.StringIO()
    out.write('<?xml version="1.0" encoding="UTF-8"?>\n')
    out.write('<kml xmlns="http://www.opengis.net/kml/2.2">\n  <Document>\n')
    out.write(f"    <name>{_xml_text(name)}</name>\n")
    out.write("    <Placemark>\n")
    out.write(f"      <name>{_xml_text(name)}</name>\n")
    out.write("      <LineString>\n        <tessellate>1</tessellate>\n")
    out.write("        <coordinates>\n")
    for record in items:
        height = 0.0 if record.height_m is None else record.height_m
        out.write(f"          {record.lon:.8f},{record.lat:.8f},{height:.2f}\n")
    out.write("        </coordinates>\n      </LineString>\n    </Placemark>\n")
    out.write("  </Document>\n</kml>\n")
    return out.getvalue()


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------
def to_json(dump: LogDump) -> dict[str, Any]:
    """Convert a :class:`~m241.logfile.reader.LogDump` into a JSON-safe dict.

    Schema::

        {
          "format": "m241-log-dump",
          "version": 1,
          "device": "m241",
          "record_length": 16,
          "blocks": [{"offset", "count", "bitmask", "policy", "period_s", ...}],
          "summary": {"records", "blocks", "used_bytes", "available_bytes",
                      "fill_ratio", "first_utc", "last_utc"},
          "records": [...]
        }
    """
    blocks: list[dict[str, Any]] = []
    for block in dump.blocks:
        blocks.append(
            {
                "offset": block.offset,
                "count": block.count,
                "bitmask": block.bitmask,
                "policy": block.policy,
                "period_s": block.period_s,
                "distance_m": block.distance_m,
                "speed_kmh": block.speed_kmh,
                "used_bytes": block.used_bytes,
            }
        )
    first = dump.first_utc
    last = dump.last_utc
    summary = {
        "records": len(dump.records),
        "blocks": len(dump.blocks),
        "record_length": dump.record_length,
        "used_bytes": dump.used_bytes(),
        "available_bytes": dump.available_bytes(),
        "fill_ratio": dump.fill_ratio(),
        "first_utc": None if first is None else _utc_text(first),
        "last_utc": None if last is None else _utc_text(last),
    }
    return {
        "format": "m241-log-dump",
        "version": 1,
        "device": dump.device,
        "record_length": dump.record_length,
        "summary": summary,
        "blocks": blocks,
        "records": [record.as_dict() for record in dump.records],
    }


def to_json_text(dump: LogDump, *, indent: int = 2) -> str:
    """Serialise :func:`to_json` to a JSON string."""
    return json.dumps(to_json(dump), indent=indent, ensure_ascii=False)


#: Exporter registry.  ``json`` needs the dump (not just the records).
EXPORTERS: dict[str, Callable[..., str]] = {
    "gpx": to_gpx,
    "csv": to_csv,
    "nmea": to_nmea,
    "kml": to_kml,
    "json": to_json_text,
}


def export(dump: LogDump, fmt: str) -> str:
    """Export *dump* in *fmt* (one of :data:`EXPORTERS`).

    Raises :class:`~m241.errors.M241Error` for an unknown format.
    """
    key = str(fmt).strip().lower()
    exporter = EXPORTERS.get(key)
    if exporter is None:
        known = ", ".join(sorted(EXPORTERS))
        raise M241Error(f"unknown export format {fmt!r} (expected one of: {known})")
    if key == "json":
        return to_json_text(dump)
    return exporter(dump.records)


__all__ = [
    "CSV_COLUMNS",
    "EXPORTERS",
    "EXPORT_FORMATS",
    "GPX_NAME",
    "export",
    "to_csv",
    "to_gpx",
    "to_json",
    "to_json_text",
    "to_kml",
    "to_nmea",
]
