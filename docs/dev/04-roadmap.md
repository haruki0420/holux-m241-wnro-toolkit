# Roadmap

Status of everything in this project, and what is deliberately left for later.
`✅` shipped and tested (on hardware where the note says so) · `🧪` shipped,
logic-tested only · `⚠️` not verified on hardware · `⬜` planned

## Core

| Item | Status | Notes |
|---|---|---|
| MTK BootROM client (read16/32, write16, jump, checksum, sync) | ✅ | tested against the in-repo fake device and exercised on hardware |
| Download Agent driver (DA v3.8, `0xD3`/`0xD5`, CONT/ACK) | ✅ | DA load at `0x00100800`, byte-swapped write16 |
| Firmware dump (two passes, hash compare) | ✅ | hardware-verified 2026-11-15: 406,620 bytes, two identical dumps |
| Firmware profiles (V1.12, V1.13) | ✅ | offsets and hashes measured from the official packages |
| WNRO patch (LE32 at `0x402F4`) | ✅ | output hashes locked in tests; flashed, and `$GPRMC` confirmed the date |
| Version marker patch (`V1.xx` → `V1.14`) | ✅ | works for both V1.12 and V1.13 (upstream's version was V1.13-only) |
| Firmware flash (write back through the DA) | ✅ | hardware-verified 2026-11-15: write completed, DA restarted, unit boots |
| Atomic patched-image write | ✅ | temp file + `os.replace`, failure leaves the destination intact |

## Logs

| Item | Status | Notes |
|---|---|---|
| MTK/Holux binary log parser | ✅ | 16-byte and 20-byte records, block rollover, 0xFF padding |
| Corrupt/truncated dump handling | ✅ | `crc_ok` per record, `strict=True` raises |
| Capacity maths (512K/2M/4M × 16B/20B) | ✅ | 32,512 / 26,009 / 130,048 / 104,038 / 260,096 / 208,076 points |
| Exports: GPX, CSV, NMEA, KML, JSON | ✅ | GPX 1.1 and KML parsed by `xml.etree` in tests |
| Log download over PMTK182 | ✅ | `m241 logs download`; hardware-verified: 212,860 bytes, 10,399 records, 0 CRC errors |
| Erase-log command | 🧪 | `m241 logs erase`; implemented and simulator-tested, never run on hardware |

## NMEA

| Item | Status | Notes |
|---|---|---|
| Sentence parse / checksum / RMC / GGA | ✅ | |
| WNRO repair (file or stream) | ✅ | auto-detection window + explicit `--offset-days` |
| TCP proxy | ✅ | `127.0.0.1:10110` default, loopback only by default |
| PMTK aiding commands (715/716/717) | 🧪 | wired into `m241 info --aiding`; the effect on start time was never measured |

## Cross-platform

| Item | Status | Notes |
|---|---|---|
| Windows | ✅ | CP210x driver instructions, `COM<n>` handling; all hardware work was done here (Windows 11) |
| Linux | ⚠️ | code runs in CI; no device was connected |
| macOS | ⚠️ | code runs in CI; no device was connected |
| CI matrix (Linux 3.10/3.11/3.13, Windows 3.12, macOS 3.12) | ✅ | `.github/workflows/ci.yml`; ruff and the docs check run once in the lint job |
| Wheel/sdist release workflow | ✅ | `.github/workflows/release.yml` (verify → build → publish) on `v*` tags |

## Deliberately out of scope

- **GUI.** The CLI plus the proxy cover every workflow; a GUI would be a
  separate project.
- **Bundling vendor binaries.** Never. See `DISCLAIMER.md` and `LICENSE`.
- **Automatic "is my device bricked" recovery.** There is no JTAG; if the
  BootROM no longer answers, nothing in software can help.
- **Firmware for other MTK loggers (iTrek, Qstarz, …).** The protocol layer is
  reusable and the parser already understands generic MTK records, but each
  device needs its own profile and flash map. Contributions welcome.

## Wanted: hardware reports

The following have not been confirmed on hardware:

1. A dump from a **V1.11** unit (to add a third profile).
2. A dump from a **4 MiB** M-241 or a **GR-245** (to confirm the flash geometry
   and the 3-byte-height/first-field-speed swap).
3. A **Linux** or **macOS** run against a real logger, including whether the
   patched image boots (only Windows 11 was exercised here).
4. A **Bluetooth SPP** session (all hardware work went over USB).
5. A post-flash log download, to confirm the binary log's timestamps are
   corrected (currently inferred, not measured).

Open an issue with `m241 doctor --json` and `m241 patch --dry-run` output —
those contain no proprietary bytes.
