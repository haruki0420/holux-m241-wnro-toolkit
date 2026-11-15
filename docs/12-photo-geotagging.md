# Geotagging photos with the M-241

The logger records where you were; the camera records when you took each shot.
Matching the two by time writes GPS coordinates into the photo files, after
which any viewer or map application can place them.

This page is the end-to-end workflow. It needs no third-party Python package:
the toolkit reads the track log and the photo EXIF itself, and emits an
**exiftool argument file** that you apply with one command.

## 0. Prerequisites

| Need | Why |
|---|---|
| `m241` installed (`pip install .`) | the whole workflow |
| The logger's firmware patched | otherwise every timestamp is 19.6 years off — see [`00-background.md`](00-background.md) |
| [exiftool](https://exiftool.org/) | applies the coordinates to the photos (optional: you can use the CSV/JSON output instead) |
| Camera clock accurate | a wrong camera clock shifts every photo |

The firmware patch matters more than it looks. Without it the M-241 writes
`2007-04-01` for a 2026 track, and no photo will ever match. Check with
`m241 info` plus a live NMEA read: the `$GPRMC` date must be today.

## 1. Record a track

On the device: `Setting → Log record → By Time` and pick an interval. One
point per second is the default; five seconds is plenty for walking and uses a
fifth of the space.

Keep the logger powered and outdoors until you have a fix. The OLED shows the
satellite count.

## 2. Download the log

```bash
m241 logs download COM3 -o track.bin
```

```
port     : COM3
output   : track.bin
bytes    : 212860
reported : 212860
next     : m241 logs read track.bin --format gpx -o track.gpx
```

`logs download` talks PMTK182 over the normal NMEA port. It pauses logging for
the transfer and resumes it afterwards, so the device keeps recording when the
download finishes.

Options worth knowing:

| Option | Use |
|---|---|
| `--chunk 0x800` | bytes per read (the device accepts at least 2048) |
| `--length N` | read fewer bytes than the device reports |
| `--force` | overwrite an existing output file |
| `--nmea-baud 38400` | if you changed the port speed |

If the device reports an empty log, the command says so and writes a zero-byte
file rather than failing.

## 3. Check what came back

```bash
m241 logs info track.bin
```

```
blocks        : 4
  block 0x000000  count=3244  bitmask=0x8000001D  period=5s  used=65012 bytes
  ...
record length : 16 bytes
records       : 10399
crc errors    : 0
first utc     : 2024-08-01T23:20:01Z
last utc      : 2024-10-23T17:44:55Z
```

Two things to look at:

* **`crc errors` must be 0.** A non-zero count means the transfer was
  interrupted; download again.
* **The date range must be plausible.** If the log was recorded before the
  firmware patch, the dates will be ~7168 days early and you will need
  `--shift-days 7168` in step 4.

### A note on mixed logs

A log that spans the firmware patch contains both correct and stale records.
`m241 logs info` shows the full range, and the transition is visible in the
per-block summary. When in doubt, record a fresh track after patching: the
whole file is then consistent and no shifting is needed.

## 4. Match the photos

```bash
m241 logs geotag track.bin ~/Pictures/holiday \
    --utc-offset +09:00 \
    -o geotag.args
```

```
wrote exiftool to geotag.args
matched  : 214
skipped  : 3
max gap  : 4.2s
next     : exiftool -@ geotag.args
```

| Option | Meaning |
|---|---|
| `--utc-offset +09:00` | **the camera's timezone.** EXIF stores local time with no zone; the track is UTC. Get this wrong and every photo is off by that many hours. |
| `--shift-days 7168` | add 1024 weeks to the track before matching (unpatched logs only) |
| `--max-gap 300` | skip photos further than this from any track point |
| `--format exiftool\|csv\|json` | output shape (default `exiftool`) |
| `--recursive` | descend into subdirectories |

Photos whose nearest track point is too far away are reported and skipped
rather than being given a wrong position:

```
skipped IMG_4471.jpg: nearest track point is 1840s away (limit 300s)
```

That usually means the camera clock drifted, or the logger was off.

### Why a gap limit exists

If you took a photo an hour after the track ended, the "nearest" point is an
hour away and the coordinate is meaningless. The limit turns that into an
explicit skip instead of a silently wrong tag.

## 5. Write the coordinates

```bash
exiftool -@ geotag.args
```

The argument file looks like this — one block per photo:

```
-GPSLatitude=35.6812360
-GPSLatitudeRef=N
-GPSLongitude=139.7671250
-GPSLongitudeRef=E
-GPSAltitude=44.12
-GPSAltitudeRef=0
-DateTimeOriginal<EXIF:DateTimeOriginal
IMG_0001.jpg
-execute
```

`-execute` applies the accumulated tags to the preceding file, which is how
exiftool batches a per-file tag set.

Verify one file:

```bash
exiftool -GPSLatitude -GPSLongitude -GPSAltitude IMG_0001.jpg
```

Undo is easy: keep the originals, or run `exiftool -gps:all= IMG_0001.jpg`.

## 6. Or skip exiftool entirely

`--format csv` and `--format json` give you the mapping without touching the
photos, which is useful for a database, a spreadsheet, or a custom script:

```bash
m241 logs geotag track.bin photos/ --format csv --utc-offset +09:00
```

```
photo,utc,lat,lon,height_m,gap_seconds
"photos/IMG_0001.jpg",2026-11-15T01:20:31+00:00,35.6812360,139.7671250,44.12,0.0
```

## 6a. Free the space afterwards

Once the log is downloaded and the photos are tagged, the device can start a new
track. The log is a circular buffer, so it overwrites itself when full — but if
you want to start clean:

```bash
m241 logs erase COM3 --require-backup track.bin
```

```
port     : COM3
log size : 212860 bytes

This permanently erases the track log on the device.
Type ERASE to continue:
```

`--require-backup` makes the command refuse unless the file you name exists and
is non-empty, so a typo cannot destroy a log you meant to keep. Pass `--yes` to
skip the prompt in a script.

Erasing is optional: `Setting → Log record → Overwrite` on the device makes the
logger reuse the oldest space by itself.

## 7. A GPX track for maps

```bash
m241 logs read track.bin --format gpx -o track.gpx
```

Drop the GPX on any map viewer, or hand it to `exiftool -geotag track.gpx`
instead of using step 4 — that path uses exiftool's own interpolation and is a
good cross-check.

`m241 logs read` also emits `csv`, `nmea`, `kml` and `json`.

## Complete example

```bash
# once, after patching the firmware
m241 logs download COM3 -o track.bin
m241 logs info track.bin                 # crc errors must be 0
m241 logs geotag track.bin ~/Pictures/trip \
    --utc-offset +09:00 --recursive -o geotag.args
exiftool -@ geotag.args
exiftool -GPSLatitude -GPSLongitude ~/Pictures/trip/IMG_0001.jpg
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `the device did not answer $PMTK182,2,8` | logger off, wrong port, or another program holds it | `m241 ports`, close serial monitors |
| `the device refused to stop logging` | firmware does not implement `$PMTK182,5` | check `m241 info`; the device may need a power cycle |
| `no reply for $PMTK182,7,...` | transfer interrupted | retry; keep the logger still and powered |
| every photo skipped | wrong `--utc-offset` | try `+09:00`, `0`, or `--shift-days 7168` |
| dates are 19.6 years early | log recorded before the firmware patch | `--shift-days 7168`, or re-record after patching |
| `crc errors` above zero | incomplete download | download again with `--chunk 0x400` |
| coordinates are all identical | no GPS fix while recording | record outdoors; check the OLED satellite count |
