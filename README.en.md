# Holux M-241 WNRO toolkit — cross-platform repair for the GPS week rollover bug

[![CI](https://github.com/haruki0420/holux-m241-wnro-toolkit/actions/workflows/ci.yml/badge.svg)](https://github.com/haruki0420/holux-m241-wnro-toolkit/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](pyproject.toml)
[![Platforms](https://img.shields.io/badge/platforms-Windows%20%7C%20Linux%20%7C%20macOS-informational)](#supported-platforms)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

A pure-Python, **Windows / Linux / macOS** toolkit for the abandoned **Holux
M-241** GPS logger. Hardware-verified on **Windows 11**; Linux and macOS are
⚠️ **unverified** (the code runs in CI, but no device was connected):

- **repairs the GPS week-number rollover (WNRO) bug** by replacing a verified
  4-byte date constant (only 3 bytes actually change; the device is 19.6 years
  behind),
- **talks to the MT3318 BootROM and Download Agent directly** — no
  `PowerFlash.exe`, no vendor DLLs, no Windows-only tooling,
- **dumps / patches / re-flashes** firmware, with SHA-256 verification of the
  dump (read twice) and of the patched image (the flash step does not read the
  device back),
- **downloads and converts the binary track log** (GPX, CSV, NMEA, KML, JSON)
  with correct capacity maths for **16-byte and 20-byte records** (the block
  header bitmask decides which a given unit writes),
- **repairs a live NMEA stream** (`m241 nmea proxy`) if you don't want to flash,
- ships a **hardware-free simulator** so every code path is testable on CI.

> 日本語の README は [`README.md`](README.md) です（こちらが既定）。
> Background and the physics of the bug: [`docs/user/01-symptoms.md`](docs/user/01-symptoms.md).
> Which advertised features still work in 2026: [`docs/reference/05-features.md`](docs/reference/05-features.md).
> Geotagging photos from a track log: [`docs/user/05-photos.md`](docs/user/05-photos.md).
> Changing the code? Read [`AGENTS.md`](AGENTS.md) first.

---

## What this repository actually does

**In one sentence:** it lets you diagnose, dump, patch, re-flash and convert logs
for a Holux M-241 whose dates drifted 19.6 years — from Windows, which is
hardware-verified, or from Linux / macOS (⚠️ unverified), without Holux's
Windows-only tooling.

```
  ┌─────────────┐   1. inspect / dump    ┌──────────────┐
  │  Holux      │ ─────────────────────▶ │  your PC     │
  │  M-241      │   (USB serial)         │  Windows /   │
  │  (device)   │ ◀───────────────────── │  Linux /     │
  └─────────────┘   3. flash it back     │  macOS       │
                          ▲              └──────────────┘
                          │        2. 4-byte patch
                          └──────────────────────
```

### By task and command

| I want to… | Command | Needs the device? |
|---|---|---|
| find which USB port is the M-241 | `m241 ports` | plugged in only |
| check my environment (device only with `--port`, vendor drop only with `--from-drop`) | `m241 doctor` | optional |
| identify the firmware and model | `m241 info` | yes |
| **dump the firmware (read-only, safe)** | `m241 dump` | yes |
| **apply the WNRO fix (4-byte constant; 3 bytes change)** | `m241 patch` | no (file only) |
| **write the fixed image back** | `m241 flash` | yes (destructive) |
| inspect a dumped track log | `m241 logs info` | no |
| convert a log to GPX / CSV / NMEA / KML / JSON | `m241 logs read` | no |
| work out how many points it holds | `m241 logs capacity` | no |
| fix dates in a captured NMEA file | `m241 nmea repair` | no |
| **correct live NMEA without flashing** | `m241 nmea proxy` | yes |
| self-test everything without hardware | `m241 selftest` | no |

### Problems it solves

1. **"My M-241 thinks it is 2006"** → `m241 patch` + `m241 flash` fixes the device
2. **"I want the OLED clock right"** → same; only a firmware fix can do this
   (⚠️ unverified: the OLED was never observed after flashing)
3. **"Flashing scares me, I just want correct logs"** → `m241 logs read --utc-offset-days 7168`
4. **"I only want my app to see the right date"** → `m241 nmea proxy`
5. **"The vendor tool is Windows-only and I use a Mac/Linux"** → pure Python; runs on all three OSes (Windows hardware-verified, Linux/macOS ⚠️ unverified)
6. **"How many points does V1.12 vs V1.13 actually hold?"** → `m241 logs capacity`
7. **"My dumped log won't parse"** → `m241 logs info` derives the real record length from the bitmask
8. **"Which file is the right DA binary?"** → `m241 doctor --from-drop` hash-verifies and extracts it

### What it does *not* do

- ❌ other logger models (the protocol layer is reusable, but each needs a profile)
- ❌ software recovery of a hard-bricked unit (no JTAG; a wrong image or a power
  loss at the wrong moment can leave the unit permanently unusable)
- ❌ redistributing proprietary binaries (bring your own DA + dump)
- ❌ GUI (CLI only, by design)

### What you need

| | |
|---|---|
| Hardware | Holux M-241 with the WNRO bug + its USB cable (CP210x) |
| PC | Windows 11 (verified), Linux or macOS (unverified), Python 3.10+ |
| Files | `DA_v3.8.4.bin` (proprietary, from the old official HoluxLoggerUtility package) + your own dump |

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

## Does flashing the firmware actually fix WNRO?

**Yes — the date offset goes away, but it is not a permanent fix.** Precisely:

### What it fixes

| Item | Result |
|---|---|
| OLED date | ⚠️ unverified — never observed after flashing (the *time* was always correct; only the date was shifted) |
| NMEA `$GPRMC` date | ✅ correct — verified on hardware after flashing (`$GPRMC` carried `151126`) |
| Timestamps in *future* binary logs | ⚠️ unverified — inferred from the shared NMEA code path; no post-flash log was downloaded |
| Time of day (`hhmmss`) | was already correct |

### What it does not fix

| Item | Why |
|---|---|
| **Logs recorded before the patch** | stored timestamps are not rewritten. Post-process with `m241 logs read --utc-offset-days 7168` |
| **After 2038-11-21 (3rd rollover)** | this patch shifts by exactly one rollover (+1024 weeks). Past the 3rd rollover the device drifts again (late 2038 / early 2039) |
| Anything else wrong with the unit | battery, buttons, Bluetooth, … are unrelated |

### Why shifting by one rollover is enough

The firmware hard-codes the Unix time of the GPS epoch (1980-01-06). Replacing
it with that value **plus 1024 weeks** makes the firmware's own arithmetic
equivalent to "one rollover has already happened":

```
315964800  ->  935280000      (+619,315,200 s = +1024 weeks = +7168 days)
```

That absorbs the 2nd rollover (2019) and stays correct until the 3rd
(2038-11-21). In principle you could repeat the same trick for another +1024
weeks later, but this toolkit's profiles only carry the verified single shift.

### How well is "it works" verified?

- The `0x402F4` offset and the patch content come from the upstream
  [catea.tech write-up](https://catea.tech/blog/holux-m241-wnro-fix).
- This repository reproduces that result **deterministically against the stock
  firmware images**, and locks the output SHA256 in tests.
- The full **dump → patch → flash round trip was completed on a retail unit**,
  and the NMEA date was verified afterwards.

### What real hardware confirmed, as of 2026-11

Tested against an actual M-241 (V1.13 / `01017-00F` / MT3318, Windows 11 +
CP210x on COM3):

| Step | Result |
|---|---|
| Port detection / driver | ✅ recognised as COM3 with the CP210x driver |
| `m241 info` firmware identification | ✅ `B-core_1.20, 0043, 01017-00F` |
| WNRO bug reproduced on hardware | ✅ observed `$GPRMC,...,010407` (2007-04-01) |
| **`m241 dump` (BootROM sync)** | ✅ **succeeded** — 406,620 bytes, read twice, SHA-256 identical both times |
| **`m241 patch`** | ✅ **succeeded** — 3 bytes changed |
| **`m241 flash`** | ✅ **succeeded** — the write completed and the download agent restarted |
| Boot after flash | ✅ **succeeded** — the logger boots |
| NMEA date after flash | ✅ **succeeded** — `$GPRMC` carried `151126` = 2026-11-15 |
| Log download | ✅ **succeeded** — 212,860 bytes, 10,399 records, 0 CRC errors |

**Not verified on hardware:** the OLED display after flashing (never observed),
Bluetooth SPP (all hardware work was over USB), 4 MiB and GR-245 units, firmware
V1.11 / V1.12, and whether the binary log's timestamps are corrected (inferred
from the shared NMEA code path; no post-flash log was downloaded). The session
record is in [`docs/dev/03-hardware-notes.md`](docs/dev/03-hardware-notes.md).

### If you would rather not flash

| Method | Logs | Live NMEA | OLED |
|---|---|---|---|
| Post-process with `+7168` days | ✅ | ❌ | ❌ |
| `m241 nmea proxy` (corrects on the host) | ✅ | ✅ | ❌ |
| The firmware patch in this repo | ✅ | ✅ | ⚠️ |

Only the firmware patch fixes the device itself, which is what you need if you
want to read the date off the screen while walking around (⚠️ the OLED display
after flashing was never observed — unverified).

## Supported platforms

| | Status |
|---|---|
| **Windows 11** | ✅ hardware-verified — `COM3` etc., CP210x driver from the vendor package. Windows 10 was never tested |
| **Linux** | ⚠️ **unverified** — code runs in CI, but no device was connected; `/dev/ttyUSB0`, needs your user in the `dialout` group |
| **macOS (Intel + Apple Silicon)** | ⚠️ **unverified** — code runs in CI, but no device was connected; `/dev/tty.usbserial-*`, no driver needed on modern macOS |
| Python | 3.10, 3.11, 3.12, 3.13 |

Everything is pure Python + `pyserial`. There is **nothing to compile**, no
`ctypes`, no platform-specific binaries, and no GUI.

## Install

```bash
pip install "git+https://github.com/haruki0420/holux-m241-wnro-toolkit.git"
m241 --help
```

That is the whole install: one package, no daemon, no GUI. Uninstall with
`pip uninstall m241-wnro-toolkit`.

### Developing from a clone

```bash
git clone https://github.com/haruki0420/holux-m241-wnro-toolkit.git
cd holux-m241-wnro-toolkit
python -m venv .venv
# Windows:  .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
m241 --help
```

## Quick start

The three-step workflow is deliberately the same as the original write-up:
**dump → patch → flash**. Each step prints SHA256 hashes so you can record what
you did; the dump is verified by reading twice, but the flash step does not read
the device back.

**What the three steps actually do:**

```
1. dump  : copy the firmware currently on the device to your PC (read-only, safe)
2. patch : replace the 4-byte date constant in that copy — only 3 bytes
           actually change (the device is not touched)
3. flash : write the modified copy back (this is the only destructive step)
```

So a failure at step 2 leaves the device untouched, while a failure at step 3 is
serious — which is why the backup from step 1 is your lifeline.

```bash
# 0) find your cable (plug the M-241 in, turn it on)
m241 ports

# 1) DUMP your own firmware twice; the tool prompts you to power-cycle the
#    logger between passes and verifies both hashes match. Read-only.
m241 dump auto -o backup/m241_stock.bin

# 2) PATCH the dump. Auto-detects V1.12 vs V1.13 from the image.
#    --dry-run first is recommended: it shows what would change.
m241 patch backup/m241_stock.bin -o backup/m241_v114_wnro.bin --dry-run
m241 patch backup/m241_stock.bin -o backup/m241_v114_wnro.bin

# 3) FLASH it back. Destructive — read DISCLAIMER.md first.
m241 flash auto --da vendor/DA_v3.8.4.bin --fw backup/m241_v114_wnro.bin --backup backup/m241_stock.bin
```

Then power-cycle the device and check `$GPRMC` (for example with
`m241 nmea proxy auto --offset-days 0 --stdout`): it should carry today's date.
The OLED should show **V1.14**, but ⚠️ that display was never observed after
flashing.

### If something goes wrong, how to roll back

If `m241 flash` fails part-way, re-run the **same command with `--fw` pointing at
your original dump**:

```bash
m241 flash auto --da vendor/DA_v3.8.4.bin --fw backup/m241_stock.bin --backup backup/m241_stock.bin --yes
```

That is exactly why "dump twice and compare the hashes" is mandatory.

### Where does `DA_v3.8.4.bin` come from?

`DA_v3.8.4.bin` is a **proprietary MediaTek download agent**. It is **not in this
repository** and cannot be redistributed here. It ships inside the old official
**HoluxLoggerUtility** Windows package — the same package that contains the
vendor's firmware updater.

**Without that file, `m241 flash` cannot run: the firmware cannot be written.**
Holux left the GPS business in 2019, so you must obtain the old package
yourself; this repository cannot link to a download.

The firmware image is not the problem: `m241 dump` reads your own device, and the
dump *is* the firmware. The DA is the only proprietary file you need.

Once you have the package, point the tool at the folder:

```bash
# extract everything the tool needs from the official package folder
m241 doctor --from-drop <folder> --extract-to vendor/
```

That command verifies the SHA256 of every file it extracts, tells you which
firmware version you have, and writes a `vendor/` directory with
`DA_v3.8.4.bin`. (You can also just point `--da` at the file inside the official
installer yourself.)

## Which command for which job

### A. Fix the device itself (NMEA date verified; OLED ⚠️ unverified)

```bash
m241 ports                                          # find the port
m241 info auto                                      # identify the firmware
m241 dump auto -o backup/stock.bin                  # 1. dump
m241 patch backup/stock.bin -o backup/v114.bin      # 2. patch (3 bytes change)
m241 flash auto --da vendor/DA_v3.8.4.bin --fw backup/v114.bin --backup backup/stock.bin  # 3. flash back
```

Timings are not measured; allow a few minutes for the dump and for the flash.

### B. Leave the device alone, just fix log dates

```bash
m241 logs info data.bin                                        # what is in there?
m241 logs read data.bin --format gpx --utc-offset-days 7168 -o fixed.gpx   # GPX out
m241 logs read data.bin --format csv --utc-offset-days 7168 -o fixed.csv
```

Use `--utc-offset-days auto` to shift only when the timestamps look stale.

### C. Only fix what a mapping app sees

```bash
m241 nmea proxy auto --offset-days 7168 --tcp 127.0.0.1:10110
# point your app's GPS input at 127.0.0.1:10110
```

The OLED stays wrong; the applications see the right date.

### D. Verify the install without touching anything

```bash
m241 selftest        # full dump -> patch -> flash -> read on the built-in simulator
m241 doctor          # environment and vendor-package checks (device only with --port)
```

### E. Find out how many points a unit can hold

```bash
m241 logs capacity                  # 512K/2M/4M x 16B/20B table
m241 logs info mydump.bin           # measure the real record length
```

## All commands

```
m241 ports                       list serial ports, flag likely M-241 cables
m241 doctor                      environment + vendor-drop checks (device only with --port)
m241 info <port>                 probe the device (PMTK605/705 firmware id)
m241 dump <port> -o FILE         read firmware over the BootROM (read-only, safe)
m241 patch SRC -o DST            apply the WNRO + version-marker patch
m241 flash <port> --da --fw      write firmware over the DA (DESTRUCTIVE)
m241 logs info FILE              block/record summary of a dumped track log
m241 logs read FILE --format gpx convert a dumped track log
m241 logs download [PORT] -o FILE  download the track log over PMTK182
m241 logs erase [PORT]           erase the track log on the device (DESTRUCTIVE)
m241 logs geotag TRACK PHOTO...  match photos to track points by capture time
m241 logs capacity               honest point-count table for 512K/2M/4M × 16B/20B
m241 nmea repair                 fix dates in a captured NMEA file
m241 nmea proxy <port>           live-correct the NMEA stream over TCP
m241 selftest                    end-to-end run against the built-in simulator
m241 version                     print the toolkit version
```

Every command supports `-h`. Hardware commands support `--json` where a
machine-readable result makes sense. Exit codes: `0` ok, `1` runtime error,
`2` usage, `3` hardware not found, `4` verification failed, `5` safety refusal,
`6` flash write failed.

## Firmware V1.12 vs V1.13 — why the point counts differ

Both versions advertise *"130,000 points"*, but that number is only achievable
with the older 16-byte log record. The official V1.13 release note says:

> `2. log format 16 Byte change to 20 Byte. new Speed format.`

The extra 4 bytes are a little-endian float speed field.

**The version does not decide the record size.** Each 64 KiB block starts with a
`0x200`-byte header whose bitmask says which fields every record in that block
carries, and that is what the reader uses. The V1.13 *utility default* writes
20-byte records, but a V1.13 unit can also be configured to write 16-byte ones.
The table below is therefore "if every block uses this record size":

| Flash | Blocks | Usable bytes | 16-byte records | 20-byte records |
|---|---|---|---|---|
| 512 KiB | 8 × 64 KiB | 520,192 | 32,512 points | 26,009 points |
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

Only **3 bytes actually change**; on the retail V1.13 unit they are `0x402F6`,
`0x402F7` and `0x59C2C`.

The image contains the epoch constant **twice**; only `0x402F4` feeds the
timestamp path. Patching both double-shifts the clock to 2045 (reported by the
upstream author), so the tool patches exactly one and
`tests/test_firmware_patcher.py` locks that behaviour down.

Verified output hashes:

| Input | Output |
|---|---|
| V1.12 `4103601a…` | `dad2983d95cc3be251b358b2b7d5da9272d9946e70aaa0067162a4c21475c384` |
| V1.13 `32fe5269…` | `901555843895054f77716364a446e8ad41c00b799c781b4ff95144352c141c9a` |

These are the official stock images; **your own dump will hash differently**,
because it contains per-unit runtime fields. Never use a hash as a pass/fail
criterion.

Full derivation: [`docs/reference/03-firmware-offsets.md`](docs/reference/03-firmware-offsets.md).

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

This project writes to NOR flash on a device with **no JTAG header**. A failed
write is usually recoverable by power-cycling and re-flashing your own backup,
but a wrong image or a power loss at the wrong moment can leave the unit
permanently unusable. Read [`DISCLAIMER.md`](DISCLAIMER.md) before step 3. Short
version:

1. Dump twice, compare SHA256, keep the backup.
2. Only flash an image **you built from your own dump**.
3. Don't let the machine sleep, and don't unplug the cable mid-write.

## Repository layout

```
src/m241/       the program; this is what `pip install` puts on your machine
  protocol/     MTK BootROM + Download Agent, serial port discovery, simulator
  firmware/     image parsing, version profiles, patch engine
  logfile/      MTK/Holux binary log parser, capacity maths, exporters
  nmea/         sentence parsing, WNRO repair, TCP proxy
  data/         shipped firmware profile JSON (hashes + offsets, no binaries)
  cli.py        the `m241` command
docs/
  user/         symptoms, install, photos, troubleshooting, quick card
  reference/    protocol, log format, firmware offsets, CLI, features
  dev/          architecture, verification, hardware notes, roadmap
tests/          the whole suite runs against synthetic images and a fake device
tools/          development and verification scripts (not installed)
```

## Contributing

Contributions are welcome, especially:

- firmware profiles for V1.11 or other M-241 revisions (see
  [`docs/reference/03-firmware-offsets.md`](docs/reference/03-firmware-offsets.md) for
  what a profile needs),
- validation on 4 MiB units and on GR-245 variants,
- Windows/macOS user reports in the issue tracker.

Run `pytest` and `ruff check .` before opening a PR; CI runs the tests on
Linux 3.10 / 3.11 / 3.13, Windows 3.12 and macOS 3.12, and runs ruff and the
docs link check once in the Linux lint job.

## Credits

- The original protocol analysis and the discovery that the DA load address is
  `0x100800`:
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
