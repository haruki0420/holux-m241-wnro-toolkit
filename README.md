# Holux M-241 WNRO toolkit — cross-platform repair for the GPS week rollover bug

[![CI](https://github.com/haruki0420/holux-m241-wnro-toolkit/actions/workflows/ci.yml/badge.svg)](https://github.com/haruki0420/holux-m241-wnro-toolkit/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](pyproject.toml)
[![Platforms](https://img.shields.io/badge/platforms-Windows%20%7C%20Linux%20%7C%20macOS-informational)](#supported-platforms)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

A pure-Python, **Windows / Linux / macOS** toolkit for the abandoned **Holux
M-241** GPS logger:

- **repairs the GPS week-number rollover (WNRO) bug** with a verified 4-byte
  firmware patch (the device thinks it is 2006; it is 2026),
- **talks to the MT3318 BootROM and Download Agent directly** — no
  `PowerFlash.exe`, no vendor DLLs, no Windows-only tooling,
- **dumps / patches / re-flashes** firmware with verified SHA256 at every step,
- **downloads and converts the binary track log** (GPX, CSV, NMEA, KML, JSON)
  with correct capacity maths for **firmware V1.12 (16-byte records)** and
  **V1.13 (20-byte records)**,
- **repairs a live NMEA stream** (`m241 nmea proxy`) if you don't want to flash,
- ships a **hardware-free simulator** so every code path is testable on CI.

> 日本語の説明は [`README.ja.md`](README.ja.md) を参照してください。
> Background and the physics of the bug: [`docs/00-background.md`](docs/00-background.md).

---

## The problem in one paragraph

GPS broadcasts the week number in a 10-bit field, so it wraps every 1024 weeks
(7168 days ≈ 19.6 years). The last rollover was **2019-04-06**. The M-241's
firmware was built in 2006 and hard-codes the Unix epoch offset
`315964800 s` (1980-01-06) without accounting for a second rollover, so today
it reports every timestamp **exactly 7168 days in the past** — the OLED clock,
the NMEA `$GPRMC` date, and the binary track log. Holux left the GPS business in
2019, so no fix is coming. This project provides one.

```
$GPRMC,022127.542,V,8960.0000,N,00000.0000,E,0.00,0.00,170906,,,N*7F
                                                    ^^^^^^
                                                    2006-09-17  (wrong)
                                                    2026-05-03  (after patch)
```

## Supported platforms

| | Status |
|---|---|
| **Windows 10/11** | ✅ first-class — `COM3` etc., CP210x driver from the vendor package |
| **Linux** | ✅ `/dev/ttyUSB0`, needs your user in the `dialout` group |
| **macOS (Intel + Apple Silicon)** | ✅ `/dev/tty.usbserial-*`, no driver needed on modern macOS |
| Python | 3.10, 3.11, 3.12, 3.13 |

Everything is pure Python + `pyserial`. There is **nothing to compile**, no
`ctypes`, no platform-specific binaries, and no GUI.

## Install

```bash
git clone https://github.com/haruki0420/holux-m241-wnro-toolkit.git
cd holux-m241-wnro-toolkit
python -m venv .venv
# Windows:  .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
m241 --help
```

Or, without cloning:

```bash
pip install "git+https://github.com/haruki0420/holux-m241-wnro-toolkit.git"
```

## Quick start

The three-step workflow is deliberately the same as the original write-up:
**dump → patch → flash**. Each step prints SHA256 hashes so you can prove what
you did.

```bash
# 0) find your cable (plug the M-241 in, turn it on)
m241 ports

# 1) DUMP your own firmware twice; the tool verifies both hashes match.
#    This is read-only. Nothing is written to the device.
m241 dump auto -o backup/m241_stock.bin

# 2) PATCH the dump. Auto-detects V1.12 vs V1.13 from the image.
m241 patch backup/m241_stock.bin -o backup/m241_v114_wnro.bin

# 3) FLASH it back. Destructive — read DISCLAIMER.md first.
m241 flash auto \
    --da vendor/DA_v3.8.4.bin \
    --fw backup/m241_v114_wnro.bin \
    --backup backup/m241_stock.bin
```

Then power-cycle the device and check the OLED: it should show **V1.14**, and
`$GPRMC` should carry today's date.

### Where do `DA_v3.8.4.bin` and the firmware come from?

They are **proprietary and are not distributed here**. You supply them from a
copy of the official Holux update package you already have:

```bash
# extract everything the tool needs from the official zips in ../holux/
m241 doctor --from-drop ../holux --extract-to vendor/
```

That command verifies the SHA256 of every file it extracts, tells you which
firmware version you have, and writes a `vendor/` directory with
`DA_v3.8.4.bin`. (You can also just point `--da` at the file inside the official
installer yourself.)

If you don't own the M-241's firmware package, you can still dump your own
device — the dump *is* the firmware — and you only need the DA binary, which is
inside `HoluxLoggerUtility.exe`'s directory.

## All commands

```
m241 ports                       list serial ports, flag likely M-241 cables
m241 doctor                      environment + vendor-drop + device health checks
m241 info <port>                 probe the device (PMTK605/705 firmware id)
m241 dump <port> -o FILE         read firmware over the BootROM (read-only, safe)
m241 patch SRC -o DST            apply the WNRO + version-marker patch
m241 flash <port> --da --fw      write firmware over the DA (DESTRUCTIVE)
m241 logs info FILE              block/record summary of a dumped track log
m241 logs read FILE --format gpx download-format conversion
m241 logs capacity               honest point-count table for 2M/4M × 16B/20B
m241 nmea repair                 fix dates in a captured NMEA file
m241 nmea proxy <port>           live-correct the NMEA stream over TCP
m241 selftest                    end-to-end run against the built-in simulator
```

Every command supports `-h`. Hardware commands support `--json` where a
machine-readable result makes sense. Exit codes: `0` ok, `1` runtime error,
`2` usage, `3` hardware not found, `4` verification failed, `5` safety refusal.

## Firmware V1.12 vs V1.13 — why the point counts differ

Both versions advertise *"130,000 points"*, but that number is only achievable
with the older 16-byte log record. The official V1.13 release note says:

> `2. log format 16 Byte change to 20 Byte. new Speed format.`

The extra 4 bytes are a little-endian float speed field. So:

| Flash | Blocks | Usable bytes | 16-byte records (V1.11/V1.12) | 20-byte records (V1.13) |
|---|---|---|---|---|
| 2 MiB | 32 × 64 KiB | 2,080,768 | **130,048** points | **104,038** points |
| 4 MiB | 64 × 64 KiB | 4,161,536 | 260,096 points | 208,076 points |

(Each 64 KiB block spends its first `0x200` bytes on a header, hence
`65024` usable bytes per block.)

`m241 logs capacity` prints this table; `m241 logs info` reads the *actual*
bitmask out of your dump and reports the real record length, so you never have
to guess which format a given unit wrote.

At the default 1 Hz logging interval:

| Format | 2 MiB | 4 MiB |
|---|---|---|
| 16 B | ≈ 36.1 h | ≈ 72.2 h |
| 20 B | ≈ 28.9 h | ≈ 57.8 h |

## How the patch works

```
offset 0x402F4 : 0x12D53D80 -> 0x37BF3D80
                 315964800     935280000        (+619315200 s = +1024 weeks)
offset 0x59B14 : "V1.12"  ->  "V1.14"           (cosmetic marker)
offset 0x59C28 : "V1.13"  ->  "V1.14"           (cosmetic marker)
```

The image contains the epoch constant **twice**; only `0x402F4` feeds the
timestamp path. Patching both double-shifts the clock to 2045 (verified on
hardware by the original reverse-engineer), so the tool patches exactly one and
`tests/test_firmware_patcher.py` locks that behaviour down.

Verified output hashes:

| Input | Output |
|---|---|
| V1.12 `4103601a…` | `dad2983d95cc3be251b358b2b7d5da9272d9946e70aaa0067162a4c21475c384` |
| V1.13 `32fe5269…` | `901555843895054f77716364a446e8ad41c00b799c781b4ff95144352c141c9a` |

Full derivation: [`docs/04-firmware-profiles.md`](docs/04-firmware-profiles.md).

## If you don't want to flash

Two non-destructive alternatives are built in:

```bash
# fix a track log downloaded from the device
m241 logs read data.bin --format gpx --utc-offset-days 7168 -o fixed.gpx

# fix the live NMEA stream: point your mapping app at 127.0.0.1:10110
m241 nmea proxy auto --offset-days 7168 --tcp 127.0.0.1:10110
```

The OLED clock stays wrong, but every application sees the right date.

## Safety

This project writes to NOR flash on a device with **no JTAG header**. A bad
flash can brick it. Read [`DISCLAIMER.md`](DISCLAIMER.md) before step 3. Short
version:

1. Dump twice, compare SHA256, keep the backup.
2. Only flash an image **you built from your own dump**.
3. Don't let the machine sleep, and don't unplug the cable mid-write.

## Repository layout

```
src/m241/
  protocol/     MTK BootROM + Download Agent, serial port discovery, simulator
  firmware/     image parsing, version profiles, patch engine
  logfile/      MTK/Holux binary log parser, capacity maths, exporters
  nmea/         sentence parsing, WNRO repair, TCP proxy
  data/         shipped firmware profile JSON (hashes + offsets, no binaries)
  cli.py        the `m241` command
scripts/        compatibility shims for the original dump/patch/flash workflow
docs/           background, protocol, firmware profiles, architecture, Japanese docs
tests/          the whole suite runs against synthetic images and a fake device
```

## Contributing

Contributions are welcome, especially:

- firmware profiles for V1.11 or other M-241 revisions (see
  [`docs/04-firmware-profiles.md`](docs/04-firmware-profiles.md) for what a
  profile needs),
- validation on 4 MiB units and on GR-245 variants,
- Windows/macOS user reports in the issue tracker.

Run `pytest` and `ruff check .` before opening a PR; CI runs both on all three
operating systems.

## Credits

- The original reverse engineering, BootROM/DA protocol decode, and the
  discovery that `DA_SetStartAddr(handle, 0x100800)` is the magic number:
  [猫葉Tech / Catea Tech](https://catea.tech/blog/holux-m241-wnro-fix) and the
  upstream project
  [NekobaTechnology/holux-m241-wnro-fix](https://github.com/NekobaTechnology/holux-m241-wnro-fix) (MIT).
  This project is an independent, cross-platform, feature-complete
  re-implementation with a tested simulator; the research lineage is theirs.
- [`dimhoff/mt333x-fw-utils`](https://github.com/dimhoff/mt333x-fw-utils) for
  the original Python MTK protocol reference.
- [GPSBabel](https://www.gpsbabel.org/) (`mtk_logger.cc`) for the documented
  MTK/Holux log format, including the M-241 quirks (4-byte float lat/lon,
  3-byte height, single-byte CRC).
- Everyone who keeps abandonware running.

## License

[MIT](LICENSE) for the code and documentation. Proprietary binaries and firmware
images remain the property of their owners and are not distributed here.
