# Adapting the toolkit to the real CP210x + M-241 (bootROM entry findings)

This note captures what changed after the first real-hardware session with a
**Holux M-241, firmware V1.13 (`01017-00F`, build `0043`, MT3318)** on
**Windows 11, CP210x (VID 10C4 / PID EA60) as COM3**.

Everything here is read-only exploration; **no firmware was written**.

## 1. `m241 info` needed a CRLF terminator (fixed)

Real-hardware transcript (`tools/diag_pmtk.py`):

```
CRLF                   reply=[b'$PMTK705,B-core_1.20,0043,01017-00F,1.0*72']
no CRLF                reply=[]
```

Sending `$PMTK605*31` without `\r\n` is **silently ignored** by the MTK
firmware. `m241.nmea.with_checksum()` returns the sentence *without* a line
terminator by design (it is also used for parsing/round-trips), so the CLI's
`_nmea_collect()` now appends `\r\n` to every sentence it writes and calls
`flush()` afterwards.

`FakeNmeaSerial` in `tests/test_cli.py` gained a `flush()` method so the test
double keeps matching `serial.Serial`.

## 2. Device identity confirmed on hardware

```
$PMTK705,B-core_1.20,0043,01017-00F,1.0*72
```

| Field | Value | Meaning |
|---|---|---|
| release | `B-core_1.20` | MTK firmware release name |
| build | `0043` | internal build number |
| model | `01017-00F` | Holux product model → **matches the V1.13 profile** |
| SDK | `1.0` | |

This matches `docs/04-firmware-profiles.md`: the V1.13 stock image has
`device_type = 01017-00F` at `0x1B0` and `build_number = 67`... note the
*reported* build (`0043` = decimal 43) is the GPS firmware's own build counter,
not the header's `0x108` field, so it is informational only.

Observed NMEA while broken (2026-11-15):

```
$GPRMC,054525.536,V,8960.0000,N,00000.0000,E,0.00,0.00,010407,,,N*70
```

`010407` = **2007-04-01**. That is the WNRO bug live on hardware (no satellite
fix yet, so the date comes from the last known / default time).

## 3. BootROM entry did not work on this unit (open issue)

`m241 dump` fails with:

```
m241: error: timeout waiting for BootROM sync reply 0x5F
```

Attempts made (all read-only, all silent — zero bytes ever came back):

| Route | Tool | Result |
|---|---|---|
| RTS+DTR pulse then `0xA0` @115200 | `m241 dump` | no `0x5F` |
| `$PMTK180*3B` @38400 then `0xA0` | `--pmtk180` | no `0x5F` |
| `$PMTK180,115200*10` @38400 | `tools/probe_bootrom.py` | no `0x5F` |
| baud sweep 9600…921600, per-byte `0xA0` | `tools/sweep_sync.py` | silent on all |
| continuous `0xA0` stream per baud | `tools/recover_bootrom.py` | silent on all |
| stream `0xA0` *while* generating reset edge, RTS/DTR × high/low | `tools/hardsync.py` | all 8 combos silent |

**After the first `m241 dump` attempt the device stopped emitting NMEA
entirely** (port still opens, `in_waiting == 0` at every baud). A physical power
cycle was required to bring NMEA back; after that the device was healthy again
and `m241 info` worked, but `m241 dump` still could not sync.

Implications, in order of likelihood:

1. **This unit may not expose the BootROM over the USB UART.** The original
   write-up used a **CP210x USB-UART wired to the GPS module**, which is not
   necessarily the same electrical path as the M-241's own USB port. (The
   `brom.dll` flow drives the chip through the *logger utility's* serial
   channel, which on this hardware is the CP210x bridge, but the M-241 may gate
   that behind its display MCU.)
2. **The reset edge is not wired** in a way `pyserial` can drive (many CP210x
   boards leave DTR/RTS unconnected or tied).
3. **A vendor-side handshake precedes BootROM entry** that we have not reversed
   (the `HoluxLoggerUtility.exe` firmware-update path may send a proprietary
   sequence first).

## 4. What is needed to make progress

The remaining unknown is **how the vendor utility makes the chip listen**. The
concrete next steps, in increasing effort:

1. **USB sniff of a real vendor update.** Run `HoluxLoggerUtility.exe`'s
   firmware-update flow against a throwaway unit with a USB capture (USBPcap /
   Wireshark) and record the bytes that precede the `0xA0` `0x0A` `0x50` `0x05`
   sync. This is exactly how the upstream author recovered the protocol, and it
   is the only reliable way to close this gap.
2. **Try `PowerFlash.exe`** (the GlobalTop flashing tool referenced in the
   write-up) with the DA binary, to confirm the chip *can* enter BootROM on this
   unit at all.
3. **Check the hardware path**: whether the M-241's USB port exposes the GPS
   UART directly or goes through the display MCU. A continuity check of the
   CP210x TX/RX against the MT3318 pins settles it.

Until then: **`m241 dump` / `m241 flash` are unverified on this specific unit**.
`m241 info`, `m241 nmea repair`, `m241 nmea proxy`, `m241 logs ...` and
`m241 patch` all work and are unaffected (they need no BootROM).

## 5. Tooling added during this session

All read-only; none writes to flash.

| Tool | Purpose |
|---|---|
| `tools/probe_device.py` | raw serial capture + optional PMTK probe |
| `tools/diag_info.py` | run the CLI collector with several timeouts |
| `tools/diag_pmtk.py` | compare PMTK605 framing variants (found the CRLF bug) |
| `tools/probe_bootrom.py` | try the three documented entry routes |
| `tools/sync_bootrom.py` | hammer `0xA0` at candidate bauds |
| `tools/sweep_sync.py` | exhaustive baud sweep with continuous streaming |
| `tools/recover_bootrom.py` | recover a device silenced by a reset pulse |
| `tools/hardsync.py` | stream `0xA0` while generating the reset edge |
| `tools/explore_bootrom.py` | try all entry routes in one run and report |
