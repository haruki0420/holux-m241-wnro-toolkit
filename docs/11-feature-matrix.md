# M-241 feature matrix — what still works in 2026

The M-241 shipped around 2007 and Holux left the GPS business in 2019. This page
separates the features the manual advertises from the ones that are still usable
today, and says which of them this toolkit can drive.

Manual: `M-241_JP_User Manual.pdf` (43 pages, Japanese). Values below quoted from
it are marked *(manual)*; everything else was measured on a retail unit
(`B-core_1.20`, build `0043`, model `01017-00F`, MT3318).

## 1. Hardware and positioning

| Feature | Manual | Status | Notes |
|---|---|---|---|
| GPS chipset | MTK MT3318, 32 channels *(manual)* | **works** | `$PMTK705` confirms `MT3318` |
| Sensitivity | -159 dBm *(manual)* | **works** | 9 satellites in a fix during testing |
| Update rate | 1 Hz *(manual)* | **works** | |
| WAAS / EGNOS / MSAS | built-in *(manual)* | **works** | no host-side support needed |
| Cold / warm / hot start | 36 / 33 / 1 s *(manual)* | **works** | cold start is slower without aiding |
| Undocumented aiding | — | **works** | `$PMTK715/716/717`; `m241 info --aiding` |
| Position accuracy | 3.0 m CEP *(manual)* | **works** | |
| Altitude / speed limits | 18,000 m / 515 m/s *(manual)* | **works** | |

## 2. Logging

| Feature | Manual | Status | Notes |
|---|---|---|---|
| Track logging | 130,000 points *(manual)* | **works, capacity differs** | see the note below |
| Time-based interval | 1/5/10/15/30/60/120 s *(manual)* | **works** | `$PMTK182,2,3` reports the current interval |
| Distance-based interval | 50/100/150/200/300/500/1000 m *(manual)* | **works** | |
| Distance by point / route | *(manual)* | **works** | |
| Full-stop vs overwrite | *(manual)* | **works** | |
| Log enable / erase | — | **works** | `$PMTK182,4` / `,5` / `,6` |
| Read log over USB | ezTour *(manual)* | **works** | `m241 logs read` replaces ezTour |
| Export to GPX / CSV / KML / NMEA | ezTour *(manual)* | **works** | `m241 logs read --format …` |

### Capacity: the manual's 130,000 does not apply to every unit

The manual advertises **130,000 points**. That figure is 2 MiB of log area with
16-byte records. Two things change it:

- **Record size.** V1.12 firmware writes 16-byte records, V1.13 writes 20-byte
  records (a float speed field was added). A V1.13 unit therefore stores 20 %
  fewer points in the same space.
- **Flash fitted.** The DA on this unit reports `flash_size = 0x80000`
  (512 KiB, MX29LV400CB), not 2 MiB.

Measured on this unit:

```
$PMTK182,2,8  ->  $PMTK182,3,8,00033F0C
```

`0x00033F0C` is 212,236 bytes of log already written, which is more than the
space left if the firmware image (406,620 bytes) shared a 512 KiB chip with it.
The log area is therefore **not** the firmware chip, or the DA's `flash_size`
describes only the part it can program. This is unresolved and recorded in
`docs/ROADMAP.md`; `m241 logs capacity` reports the arithmetic for the sizes
that are actually known (2 MiB and 4 MiB) rather than guessing.

## 3. Interfaces

| Interface | Manual | Status | Notes |
|---|---|---|---|
| USB (mini USB, CMOS level) | *(manual)* | **works** | CP210x bridge; `m241 ports` finds it |
| NMEA 0183 V3.01 output | 38400 8N1 *(manual)* | **works** | `$GPGGA/$GPGSA/$GPGSV/$GPRMC/$GPVTG` at 1 Hz |
| NMEA proxy / repair | — | **works** | `m241 nmea proxy` rewrites the date on the fly |
| **Bluetooth SPP** | V1.2, class 2, 10 m *(manual)* | **works, host-dependent** | see below |
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

Bluetooth was not exercised during the hardware session (all work went over
USB), so it is marked "works, host-dependent" rather than verified.

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
| Log capacity | `m241 logs capacity` | honest point counts |
| NMEA proxy | `m241 nmea proxy` | correct dates for software that cannot be patched |
| Aiding | `m241 info --aiding` | shorter cold start |
| Self test | `m241 selftest` | verifies the install without hardware |

## 6. Summary

**Still fully usable:** positioning, track logging, USB serial, NMEA output,
LCD, log modes, and — with this toolkit — the date itself.

**Usable with host-side effort:** Bluetooth SPP (OS-dependent pairing).

**Gone:** ezTour, Mini GPS Viewer, official firmware updates, warranty.

**Fixed by this project:** the GPS week-number rollover, which otherwise makes
every 2026 fix record as 2007.
