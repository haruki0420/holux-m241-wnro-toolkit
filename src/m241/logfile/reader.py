"""Reader for raw Holux M-241 / MTK flash log dumps.

A dump is a flat copy of the logger's serial flash.  Every 64 KiB block starts
with a 0x200-byte header followed by variable-length records and ends with
0xFF padding.  Between records the device may emit 16-byte info blocks (a
``LOG_RST`` marker or a ``HOLUXGR241...`` ASCII tag), which carry configuration
changes or mark the following record as a waypoint.

The parser is deliberately forgiving: truncated final records, bad checksums
and unexpected bytes are reported on the records (:attr:`LogRecord.crc_ok`)
instead of aborting the dump.  ``strict=True`` turns those conditions into
:class:`~m241.errors.LogParseError` exceptions instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from ..errors import LogParseError, M241Error
from .record import (
    BLOCK_HEADER_SIZE,
    BLOCK_SIZE,
    LOG_BIT_SID,
    LOG_HEADER_FIELDS_SIZE,
    MAX_SATELLITES,
    POLICY_LOG_DISABLED,
    POLICY_LOG_ENABLED,
    BlockHeader,
    LogRecord,
    block_header,
    decode_record,
    effective_bitmask,
    field_layout,
    normalize_device,
    record_checksum,
    utc_from_epoch,
)
from .record import (
    record_length as _record_length,
)

#: A 16-byte info marker: 6 x 0xAA, 5 payload/junk bytes, 4 x 0xBB.
LOG_RST_PREFIX = b"\xaa" * 6
LOG_RST_SUFFIX = b"\xbb" * 4
LOG_RST_INFO_SIZE = 16

#: ASCII tags written by the M-241 before/inside the log data.
HOLUX_LOGGER_TAG = b"HOLUXGR241LOGGER"
HOLUX_WAYPOINT_TAG = b"HOLUXGR241WAYPNT"
HOLUX_TAG_PREFIX = b"HOLUX"
HOLUX_TAG_SIZE = 16
HOLUX_TAG_TRAILER = b"    "  # four spaces may follow a tag

#: Info block "kind" byte (offset 7) and what each kind carries.
INFO_KIND_BITMASK = 0x02
INFO_KIND_PERIOD = 0x03
INFO_KIND_DISTANCE = 0x04
INFO_KIND_SPEED = 0x05
INFO_KIND_POLICY_OVERWRITE_STOP = 0x06
INFO_KIND_POLICY_ENABLED = 0x07


@dataclass
class LogDump:
    """A parsed log dump: the raw bytes, its block headers and its records."""

    data: bytes
    blocks: list[BlockHeader] = field(default_factory=list)
    records: list[LogRecord] = field(default_factory=list)
    device: str = "m241"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"LogDump(blocks={len(self.blocks)}, records={len(self.records)}, "
            f"device={self.device!r})"
        )

    @property
    def record_length(self) -> int:
        """Record length implied by the first block's bitmask (0 if unknown)."""
        if not self.blocks:
            return 0
        return _record_length(self.blocks[0].bitmask, device=self.device)

    def used_bytes(self) -> int:
        """Total number of record/tag bytes stored in the dump (headers excluded)."""
        return sum(block.used_bytes for block in self.blocks)

    def available_bytes(self) -> int:
        """Total record area available in the blocks present in the dump."""
        return len(self.blocks) * (BLOCK_SIZE - BLOCK_HEADER_SIZE)

    def fill_ratio(self) -> float:
        """Fraction of the block data area actually used (0.0 - 1.0)."""
        available = self.available_bytes()
        if not available:
            return 0.0
        return self.used_bytes() / available

    @property
    def first_utc(self) -> datetime | None:
        """UTC timestamp of the first record that has one (or ``None``)."""
        for record in self.records:
            if record.utc is not None:
                return record.utc
        return None

    @property
    def last_utc(self) -> datetime | None:
        """UTC timestamp of the last record that has one (or ``None``)."""
        for record in reversed(self.records):
            if record.utc is not None:
                return record.utc
        return None


def _is_log_rst(chunk: bytes) -> bool:
    """True when *chunk* starts with a complete ``LOG_RST`` info marker."""
    return (
        len(chunk) >= LOG_RST_INFO_SIZE
        and chunk[: len(LOG_RST_PREFIX)] == LOG_RST_PREFIX
        and chunk[12:16] == LOG_RST_SUFFIX
    )


def _is_holux_tag(chunk: bytes) -> bool:
    """True when *chunk* starts with a Holux 16-byte ASCII info tag."""
    return len(chunk) >= 5 and chunk[:5].upper() == HOLUX_TAG_PREFIX


def parse_info_block(chunk: bytes) -> dict[str, int]:
    """Decode one 16-byte ``LOG_RST`` info block into a dict of settings.

    Recognised keys are ``bitmask``, ``period_ds``, ``distance_dm``,
    ``speed_dkh`` and ``policy``.  Unknown or malformed blocks return ``{}``
    so callers can simply skip them.
    """
    if not _is_log_rst(chunk):
        return {}
    kind = chunk[7]
    value = int.from_bytes(chunk[8:12], "little")
    if kind == INFO_KIND_BITMASK:
        return {"bitmask": value}
    if kind == INFO_KIND_PERIOD:
        return {"period_ds": value & 0xFFFF}
    if kind == INFO_KIND_DISTANCE:
        return {"distance_dm": value & 0xFFFF}
    if kind == INFO_KIND_SPEED:
        return {"speed_dkh": value & 0xFFFF}
    if kind in (INFO_KIND_POLICY_OVERWRITE_STOP, INFO_KIND_POLICY_ENABLED):
        return {"policy": value & 0xFFFF}
    return {}


def _apply_info(
    header: BlockHeader, bitmask: int, info: dict[str, int], *, device: str
) -> tuple[BlockHeader, int]:
    """Fold an info block into the running header state; return (header, bitmask)."""
    if not info:
        return header, bitmask
    changes: dict[str, int] = dict(info)
    if "bitmask" in changes:
        bitmask = effective_bitmask(changes.pop("bitmask"), device=device)
    if changes:
        header = replace(header, **changes)
    return header, bitmask


def _record_size(
    data: bytes, pos: int, bitmask: int, *, device: str
) -> int:
    """Return the full record size (fields + trailer) at *pos*.

    Records without satellite information have a fixed length for a given
    bitmask, which is by far the common case and much cheaper than building
    the full field layout.
    """
    if not bitmask & (1 << LOG_BIT_SID):
        return _record_length(bitmask, device=device)
    layout = field_layout(data[pos : pos + 64], bitmask, device=device)
    return sum(size for _bit, _off, size in layout) + (2 if device == "mtk" else 1)


def read_log_dump(
    data: bytes, *, device: str = "m241", strict: bool = False
) -> LogDump:
    """Parse a raw log dump.

    Args:
        data: The raw flash image (usually 2 MiB or 4 MiB, but any prefix works).
        device: ``"m241"`` (default), ``"gr245"`` or generic ``"mtk"``.
        strict: Raise :class:`~m241.errors.LogParseError` on a bad checksum or a
            truncated record instead of marking/skipping it.

    Returns:
        A :class:`LogDump` with every successfully decoded record and block.
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise M241Error(f"log dump must be bytes-like, not {type(data).__name__}")
    key = normalize_device(device)
    raw = bytes(data)
    blocks: list[BlockHeader] = []
    records: list[LogRecord] = []

    for base in range(0, len(raw), BLOCK_SIZE):
        chunk = raw[base : base + BLOCK_SIZE]
        if len(chunk) < LOG_HEADER_FIELDS_SIZE:
            if strict:
                raise LogParseError(
                    f"truncated block at 0x{base:X}: only {len(chunk)} bytes", offset=base
                )
            break
        header = replace(block_header(chunk), offset=base)
        bitmask = effective_bitmask(header.bitmask, device=key)
        block_end = min(base + BLOCK_SIZE, len(raw))
        pos = base + BLOCK_HEADER_SIZE
        used = 0
        block_records = 0
        waypoint_pending = False

        if bitmask == 0 or header.count == 0:
            # A block with no enabled fields cannot contain records; treat it
            # as empty rather than guessing a layout.  A count of 0 explicitly
            # says the block holds no records.
            header = replace(header, used_bytes=0)
            blocks.append(header)
            continue

        while pos < block_end:
            rest = raw[pos:block_end]
            if not rest:
                break
            if rest[:4] == b"\xff\xff\xff\xff" or (len(rest) < 4 and rest[0] == 0xFF):
                # 0xFF padding marks the end of the used data area.
                break
            if _is_log_rst(rest):
                info = parse_info_block(rest)
                header, bitmask = _apply_info(header, bitmask, info, device=key)
                pos += LOG_RST_INFO_SIZE
                used += LOG_RST_INFO_SIZE
                continue
            if _is_holux_tag(rest):
                if len(rest) < HOLUX_TAG_SIZE:
                    if strict:
                        raise LogParseError(
                            f"truncated Holux tag at 0x{pos:X}: {len(rest)} bytes",
                            offset=pos,
                        )
                    break
                tag = rest[:HOLUX_TAG_SIZE]
                waypoint_pending = HOLUX_WAYPOINT_TAG in tag
                skip = HOLUX_TAG_SIZE
                if rest[skip : skip + len(HOLUX_TAG_TRAILER)] == HOLUX_TAG_TRAILER:
                    skip += len(HOLUX_TAG_TRAILER)
                pos += skip
                used += skip
                continue

            size = _record_size(raw, pos, bitmask, device=key)
            if size <= 0:
                # A malformed info block can leave the running bitmask at zero
                # (kind 0x02 carrying 0, or 0x80000000 which clears it). Without
                # this guard `pos` never advances and the loop runs forever on a
                # dump the reader is supposed to tolerate.
                if strict:
                    raise LogParseError(
                        f"record size is {size} at 0x{pos:X} (bitmask 0x{bitmask:08X})",
                        offset=pos,
                    )
                break
            crc_bytes = 2 if key == "mtk" else 1
            if len(rest) < size:
                if strict:
                    raise LogParseError(
                        f"truncated record at 0x{pos:X}: {len(rest)} of {size} bytes",
                        offset=pos,
                    )
                break
            body = size - crc_bytes
            crc_ok = record_checksum(rest[:size], body, device=key)
            record = decode_record(
                rest[:size],
                bitmask,
                device=key,
                index=len(records),
                offset=pos,
                crc_ok=crc_ok,
                waypoint=waypoint_pending,
            )
            waypoint_pending = False
            records.append(record)
            block_records += 1
            used += size
            pos += size
            if strict and not crc_ok:
                raise LogParseError(
                    f"bad CRC in record {record.index} at 0x{record.offset:X}",
                    offset=record.offset,
                )
            if header.count is not None and block_records >= header.count:
                break

        header = replace(header, used_bytes=used)
        blocks.append(header)

    return LogDump(raw, blocks, records, device=key)


def read_log_file(path: str | Path, **kw) -> LogDump:
    """Read and parse a log dump from *path*.  Keyword args go to :func:`read_log_dump`."""
    dump_path = Path(path)
    try:
        data = dump_path.read_bytes()
    except OSError as exc:
        raise M241Error(f"cannot read log dump {dump_path}: {exc}") from exc
    return read_log_dump(data, **kw)


__all__ = [
    "BlockHeader",
    "HOLUX_LOGGER_TAG",
    "HOLUX_TAG_PREFIX",
    "HOLUX_WAYPOINT_TAG",
    "LOG_RST_PREFIX",
    "LOG_RST_SUFFIX",
    "LogDump",
    "LogRecord",
    "MAX_SATELLITES",
    "POLICY_LOG_DISABLED",
    "POLICY_LOG_ENABLED",
    "parse_info_block",
    "read_log_dump",
    "read_log_file",
    "utc_from_epoch",
    "LOG_BIT_SID",
]
