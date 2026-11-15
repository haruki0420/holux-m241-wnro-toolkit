# The M-241 binary track log format (and why V1.13 stores fewer points)

The M-241 does not write NMEA to flash. It writes a compact binary log in the
MTK "logger" format, which `m241.logfile` parses. This page is the format
reference; it is derived from GPSBabel's `mtk_logger.cc` (the de-facto
documentation of this format) plus the M-241-specific quirks confirmed against
the official release notes.

## 1. Physical layout

Flash is split into **64 KiB blocks**. Each block starts with a **0x200-byte
header** followed by records; the tail is `0xFF` padding until the next block.

```
+---------------------------- 0x10000 (64 KiB) ----------------------------+
| 0x200 header | record | record | ...        | 0xFF padding               |
+--------------------------------------------------------------------------+
```

The device only uses the start of a block until it fills up, then rolls to the
next block. So the *usable* capacity is not `blocks × 65536`:

```
usable_per_block = 0x10000 - 0x200 = 65024 bytes
```

## 2. Block header (0x200 bytes, little-endian)

| Offset | Size | Field |
|---|---|---|
| `0x00` | u16 | log count in this block (`0xFFFF` = block not full) |
| `0x02` | u32 | bitmask — which fields each record contains |
| `0x06` | u16 | policy: `0x0104` logging disabled, `0x0106` turned on |
| `0x08` | u32 | log period × 10 (seconds) |
| `0x0C` | u32 | log distance × 10 (metres) |
| `0x10` | u32 | log speed × 10 (km/h) |
| `0x14` | — | rest of the header: bitmaps and UI defaults, not needed to parse |

A partially written block (device powered off mid-write) shows up as
`count == 0xFFFF`; treat the block as "unknown length, parse until 0xFF".

## 3. Record layout

A record is a concatenation of the fields whose bit is set in the bitmask, in
ascending bit order, followed by the checksum.

| Bit | Field | Size (M-241) |
|---|---|---|
| 0 | UTC (Unix seconds, u32) | 4 |
| 1 | VALID (fix quality flags, u16) | 2 |
| 2 | LATITUDE | 4 (**float**, not double) |
| 3 | LONGITUDE | 4 (**float**, not double) |
| 4 | HEIGHT | 3 (**u24**, not float32) |
| 5 | SPEED | 4 (float, km/h) |
| 6 | HEADING | 4 (float) |
| 7 | DSTA | 2 |
| 8 | DAGE | 4 |
| 9 | PDOP | 2 (÷100) |
| 10 | HDOP | 2 (÷100) |
| 11 | VDOP | 2 (÷100) |
| 12 | NSAT | 2 (view, used) |
| 13 | SID | 2 + 32 × (1 + used × (2×elev + 2×azim + 2×snr)) |
| 14 | ELEVATION | inside SID |
| 15 | AZIMUTH | inside SID |
| 16 | SNR | inside SID |
| 17 | RCR | 2 (log reason bitmask) |
| 18 | MILLISECOND | 2 |
| 19 | DISTANCE | 8 (double) |
| — | checksum | **1 byte** (no `'*'` separator) |

M-241 quirks that cost real debugging time:

1. **Lat/lon are 4-byte floats.** Generic MTK loggers use 8-byte doubles. Getting
   this wrong yields ~0.5 m resolution garbage instead of coordinates.
2. **Height is 3 bytes**, little-endian `u24`. Reinterpret as a float with a
   zero high byte: `struct.unpack('<f', b'\x00' + data[3])`. (GPSBabel does
   exactly this — with the comment "M-241" next to it.)
3. **The checksum is one byte and there is no `'*'`.** Generic MTK writes
   `'*'` + checksum (2 bytes).
4. **`RCR & 0x0008` (or a `HOLUXGR241WAYPNT` info record) means a waypoint**
   was pressed. The device restarts the log session on a button press, so a
   waypoint is also duplicated as a trackpoint.
5. **Info records** are 16 bytes and start with `0xAA` (matching the `LOG_RST`
   pattern) or with the ASCII strings `HOLUXGR241LOGGER` / `HOLUXGR241WAYPNT`
   (sometimes followed by 4 spaces). They update the bitmask / period /
   distance / speed mid-stream and must be consumed, not parsed as records.

## 4. The V1.12 → V1.13 record-size change

The official release note is unambiguous:

```
HoluxLoggerUtility_20090814_1000_Ver1.1.0.48
2. log format 16 Byte change to 20 Byte. new Speed format.
```

`V1.13` adds a 4-byte float speed field, so the *default* M-241 bitmask produces:

| Firmware | Default record | Bytes |
|---|---|---|
| V1.11 / V1.12 | UTC, VALID, LAT, LON, HEIGHT, HDOP, NSAT, RCR, … + CRC | **16** |
| V1.13 | the same **+ SPEED (float)** | **20** |

Do not hard-code either number: `m241 logs info` computes the record length from
the bitmask in the block header. The 16/20 figures above are what the *shipped*
bitmask happens to produce; a user who enables more fields gets a longer record.

## 5. Real capacity

| Flash | Blocks | Usable bytes | 16 B records | 20 B records |
|---|---|---|---|---|
| 2 MiB (`MX29LV320`, id `0x22BA`) | 32 | 2,080,768 | **130,048** | **104,038** |
| 4 MiB | 64 | 4,161,536 | 260,096 | 208,076 |

The manual's "130,000 points" is the 16-byte figure rounded down. A V1.13 unit
therefore holds **~104k** points — 20 % fewer, despite selling the same number.
This is exactly the "12 と 13 で記録できる数が違う" trap:

- do not assume 130k;
- do not assume the record is 20 bytes just because the unit says V1.13 (the
  bitmask decides);
- ask the DA for the flash size / use `--flash 4M` when you know the part.

At 1 Hz:

| Record | 2 MiB | 4 MiB |
|---|---|---|
| 16 B | 36.1 h | 72.2 h |
| 20 B | 28.9 h | 57.8 h |

(distance- and speed-triggered logging changes this; `m241 logs capacity
--interval` recomputes it.)

## 6. Recovering a log after the WNRO bug

Every UTC field in the log is 7168 days behind. Two ways to repair:

```bash
# shift while exporting
m241 logs read data.bin --format gpx --utc-offset-days 7168 -o fixed.gpx

# or shift the dump itself first, then export anything
m241 logs read data.bin --format json | jq '.records[0].utc'
```

`--utc-offset-days` also accepts `auto`, which applies 7168 only if the newest
record in the dump looks stale (same rule as `m241 nmea repair`). Because the
offset is exactly 1024 weeks, the time-of-day and the day-of-week are preserved
— you can verify a repair by checking that a Sunday stays a Sunday.

## 7. Why the parser is defensive

Real dumps contain: blocks that were never used (all `0xFF`), blocks that were
half-erased, a final record truncated by a power-off, and stale records from an
earlier overwrite cycle. `m241.logfile.reader` therefore:

- resyncs to the next 64 KiB block on `FFFFFFFF`,
- marks bad checksums `crc_ok=False` instead of raising (`strict=True` raises),
- drops a trailing truncated record silently (reported in the summary),
- never assumes the first block is full.
