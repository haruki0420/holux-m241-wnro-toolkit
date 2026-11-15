# M-241 feature matrix — what still works in 2026

The M-241 shipped around 2007 and Holux left the GPS business in 2019. This page
separates the features the manual advertises from the ones that are still usable
today, and says which of them this toolkit can drive.

Manual: `M-241_JP_User Manual.pdf` (43 pages, Japanese). Values below quoted from
it are marked *(manual)*. **works** means the feature was exercised on a retail
unit or is implemented and covered by this toolkit's tests; ⚠️ unverified means
it was not measured on hardware. The hardware session is recorded in
[the verification page](../dev/02-verification.md).

## 1. Hardware and positioning

| Feature | Manual | Status | Notes |
|---|---|---|---|
| GPS chipset | MTK MT3318, 32 channels *(manual)* | **works** | `$PMTK705` returns release/build/model/sdk; `m241 info` derives the chip from the model string (falling back to MT3318) |
| Sensitivity | -159 dBm *(manual)* | ⚠️ unverified | no sensitivity measurement in this repository |
| Update rate | 1 Hz *(manual)* | **works** | NMEA arrived at 1 Hz |
| WAAS / EGNOS / MSAS | built-in *(manual)* | ⚠️ unverified | no host-side support needed, but it was never checked |
| Cold / warm / hot start | 36 / 33 / 1 s *(manual)* | ⚠️ unverified | no start-time measurement |
| Undocumented aiding | — | ⚠️ unverified | `$PMTK715/716/717` are sent by `m241 info --aiding`; the effect on start time was never measured |
| Position accuracy | 3.0 m CEP *(manual)* | ⚠️ unverified | no accuracy measurement |
| Altitude / speed limits | 18,000 m / 515 m/s *(manual)* | ⚠️ unverified | not tested |

## 2. Logging

| Feature | Manual | Status | Notes |
|---|---|---|---|
| Track logging | 130,000 points *(manual)* | **works, capacity differs** | see the note below |
| Time-based interval | 1/5/10/15/30/60/120 s *(manual)* | **works** | `$PMTK182,2,1` / `,2,2` report the logging state and mode word |
| Distance-based interval | 50/100/150/200/300/500/1000 m *(manual)* | ⚠️ unverified | never exercised on hardware; the toolkit does not set it |
| Distance by point / route | *(manual)* | ⚠️ unverified | never exercised on hardware; the toolkit does not set it |
| Full-stop vs overwrite | *(manual)* | ⚠️ unverified | never exercised on hardware; the toolkit does not set it |
| Log enable / disable | — | **works** | `$PMTK182,4` / `,5` — both exercised by a hardware log download |
| Log erase | — | ⚠️ unverified | `$PMTK182,6` is implemented and simulator-tested but **never run on hardware**; see `docs/dev/04-roadmap.md` |
| Read log over USB | ezTour *(manual)* | **works** | `m241 logs download` reads the device; `m241 logs read` converts the saved file |
| Export to GPX / CSV / KML / NMEA | ezTour *(manual)* | **works** | `m241 logs read --format …` |

### Capacity: the manual's 130,000 does not apply to every unit

The manual advertises **130,000 points**. That figure is 2 MiB of log area with
16-byte records. Two things change it:

- **Record size.** The record size comes from the block-header bitmask, not the
  firmware version: a V1.13 unit can write 16-byte records or 20-byte records
  (the 20-byte default adds a float speed field). A unit configured for 20-byte
  records stores 20 % fewer points in the same space.
- **Flash fitted.** The DA on this unit reports `flash_size = 0x80000`
  (512 KiB, MX29LV400CB, DA id `0x49`), not 2 MiB.

Measured on this unit:

```
$PMTK182,2,8  ->  $PMTK182,3,8,00033F0C
```

`0x00033F0C` is 212,748 bytes of log written at the moment of the query. The
`m241 logs download` that followed returned 212,860 bytes — 112 more than the
status reply, from a different command; the two figures were not reconciled.
Either way that is more than the space left if the firmware image (406,620
bytes) shared a 512 KiB chip with it. The log area is therefore **not** the
firmware chip, or the DA's `flash_size` describes only the part it can program.
This is unresolved and recorded in [the roadmap](../dev/04-roadmap.md);
`m241 logs capacity` reports the arithmetic for 512 KiB, 2 MiB and 4 MiB rather
than guessing.

## 3. Interfaces

| Interface | Manual | Status | Notes |
|---|---|---|---|
| USB (mini USB, CMOS level) | *(manual)* | **works** | CP210x bridge; `m241 ports` finds it |
| NMEA 0183 V3.01 output | 38400 8N1 *(manual)* | **works** | `$GPGGA/$GPGSA/$GPGSV/$GPRMC/$GPVTG` at 1 Hz |
| NMEA proxy / repair | — | **works** | `m241 nmea proxy` rewrites the date on the fly |
| **Bluetooth SPP** | V1.2, class 2, 10 m *(manual)* | ⚠️ unverified | never exercised; see below |
| Bluetooth without SPP | "connect to devices without Bluetooth" *(manual)* | **not usable** | needs the Holux cable accessory, discontinued |

### Bluetooth

The radio is a plain **Bluetooth V1.2 SPP** serial bridge: pair with
`HOLUX_M-241`, then use the assigned virtual COM port exactly like the USB one.
Nothing in this toolkit is Bluetooth-specific — once the OS exposes a COM port,
`m241` works with it.

Practical limits in 2026:

- **Windows 11** still supports SPP, but the "incoming/outgoing COM port"
  pairing UI moved; you may need to add the port manually in Bluetooth settings.
- **macOS** dropped the Bluetooth serial (SPP) profile UI in recent releases;
  third-party tools are needed to create the `/dev/cu.*` device.
- **Linux** works with `rfcomm bind`.
- The device is **class 2, V1.2**, so expect ~10 m and low throughput; NMEA at
  38400 baud fits comfortably.

Bluetooth SPP was **never exercised** — all hardware work went over USB — so it
is marked ⚠️ unverified. The description above is the manual's, not a
measurement.

## 3a. UI language: no Japanese in any image we have

The Japanese manual lists Japanese in the language menu, but **no firmware image
available for this unit contains Japanese**. Three independent checks agree.

### 1. The official release notes name the supported languages

`M-241 new FW_V1.12 and utility Release Note.pdf` (dated 2008-10-01) documents
the Bluetooth on/off feature and states which languages it was added for:

> 5. Add Bluetooth turn on/ turn off function (Including **TC, SC, DE, FR, EN**
>    String)
>   English : Bluetooth, on, off
>   German : Bluetooth, EIN, AUS
>   French : Bluetooth, allumez, éteinez

**TC = Traditional Chinese, SC = Simplified Chinese, DE = German, FR = French,
EN = English. There is no JP.**

### 2. The firmware's language table matches the release notes

The string table holds 41 entries and is **byte-identical between V1.12 and
V1.13** (zero additions, zero removals). It contains `English`, `French`,
`German`, `Anglais`, `Français`, `Allemand`, `Englisch`, `Deutsch`, plus the
French strings the release notes quote verbatim (`allumez`, `éteinez`).

### 3. No Japanese encoding or font anywhere

| Check | V1.12 | V1.13 | retail dump |
|---|---|---|---|
| ISO-2022-JP escape sequences | 0 | 0 | 0 |
| NUL-delimited Shift-JIS strings | 0 | 0 | 0 |
| Japanese font bitmap | none | none | none |
| Language table entries | 41 | 41 | 41 |

Every "Shift-JIS hit" a naive scan reports is byte-identical between the two
builds, which proves it is code rather than text.

### What this means

The UI offers **English / French / German** in the images we have. The manual's
six-language claim (Japanese, English, Traditional Chinese, Simplified Chinese,
German, French) does not match these builds: the release notes list five
languages without Japanese, and the images ship three.

If a Japanese SKU exists, it is a separate image that is not part of the V1.12
or V1.13 update packages. Do not expect the manual's language menu on this
hardware.

## 4. Software that no longer exists

| Item | Manual | Status |
|---|---|---|
| ezTour (log viewer / exporter) | bundled *(manual)* | **unavailable** — the Holux download site is gone |
| Mini GPS Viewer | troubleshooting *(manual)* | **unavailable** |
| Official firmware updates | — | **unavailable** — Holux left GPS in 2019 |
| Warranty / support | 1 year *(manual)* | **expired** |
| Windows driver download | — | **works** via the OS-provided CP210x driver |

This toolkit replaces the useful parts of ezTour: dump, patch, flash, log
export, NMEA proxy.

## 5. What this toolkit adds

| Capability | Command | Why it matters in 2026 |
|---|---|---|
| Device identity | `m241 info` | confirms the unit is alive and which firmware it runs |
| Firmware dump | `m241 dump` | mandatory backup before any write |
| WNRO patch | `m241 patch` | fixes the 19.6-year date error |
| Firmware flash | `m241 flash` | applies the patch; also the recovery path |
| Log export | `m241 logs read` | replaces ezTour |
| Log download | `m241 logs download` | reads the log off the device over USB |
| Log capacity | `m241 logs capacity` | honest point counts |
| NMEA proxy | `m241 nmea proxy` | correct dates for software that cannot be patched |
| Aiding | `m241 info --aiding` | sends the undocumented aiding sentences (effect ⚠️ unverified) |
| Self test | `m241 selftest` | verifies the install without hardware |

## 6. Summary

**Still fully usable:** positioning, track logging, USB serial, NMEA output,
LCD, log modes, and — with this toolkit — the date itself.

**⚠️ Unverified:** Bluetooth SPP (never exercised; OS-dependent pairing).

**Gone:** ezTour, Mini GPS Viewer, official firmware updates, warranty.

**Not present at all:** a Japanese UI. The official release notes list the
supported languages as TC / SC / DE / FR / EN -- no JP -- and the images we
have ship English / French / German with no Japanese strings or font.

**Fixed by this project:** the GPS week-number rollover, which otherwise makes
every 2026 fix record as 2007.
