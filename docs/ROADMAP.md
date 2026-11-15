# Roadmap

Status of everything in this project, and what is deliberately left for later.
`✅` shipped and tested · `🧪` shipped, needs real-hardware confirmation · `⬜` planned

## Core

| Item | Status | Notes |
|---|---|---|
| MTK BootROM client (read16/32, write16, jump, checksum, sync) | ✅ | tested against the in-repo fake device |
| Download Agent driver (DA v3.8, `0xD3`/`0xD5`, CONT/ACK) | ✅ | DA load at `0x00100800`, byte-swapped write16 |
| Firmware dump (two passes, hash compare) | 🧪 | logic tested; verified on hardware by the upstream project |
| Firmware profiles (V1.12, V1.13) | ✅ | offsets and hashes measured from the official packages |
| WNRO patch (LE32 at `0x402F4`) | ✅ | output hashes locked in tests, verified on real hardware upstream |
| Version marker patch (`V1.xx` → `V1.14`) | ✅ | works for both V1.12 and V1.13 (upstream's version was V1.13-only) |
| Firmware flash (write back through the DA) | 🧪 | logic tested; the destructive path is documented in `DISCLAIMER.md` |
| Atomic patched-image write | ✅ | temp file + `os.replace`, failure leaves the destination intact |

## Logs

| Item | Status | Notes |
|---|---|---|
| MTK/Holux binary log parser | ✅ | 16-byte and 20-byte records, block rollover, 0xFF padding |
| Corrupt/truncated dump handling | ✅ | `crc_ok` per record, `strict=True` raises |
| Capacity maths (2M/4M × 16B/20B) | ✅ | 130,048 / 104,038 / 260,096 / 208,076 points |
| Exports: GPX, CSV, NMEA, KML, JSON | ✅ | GPX 1.1 and KML parsed by `xml.etree` in tests |
| Log download over PMTK182 | ⬜ | the parser and the proxy cover most needs; `PMTK182` download is the next big win |
| Erase-log command | ⬜ | deliberately not implemented yet (too easy to run by accident) |

## NMEA

| Item | Status | Notes |
|---|---|---|
| Sentence parse / checksum / RMC / GGA | ✅ | |
| WNRO repair (file or stream) | ✅ | auto-detection window + explicit `--offset-days` |
| TCP proxy | ✅ | `127.0.0.1:10110` default, loopback only by default |
| PMTK aiding commands (715/716/717) | ⬜ | documented in `docs/01-protocol.md`; not wired into the CLI yet |

## Cross-platform

| Item | Status | Notes |
|---|---|---|
| Windows | ✅ | CP210x driver instructions, `COM<n>` handling |
| Linux | ✅ | `dialout`, udev rule, ModemManager notes |
| macOS | ✅ | `/dev/tty.usbserial-*` and `/dev/cu.*`, no driver needed |
| CI on all three × Python 3.10–3.13 | ✅ | `.github/workflows/ci.yml` |
| Wheel/sdist release workflow | ✅ | `.github/workflows/release.yml` on `v*` tags |

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

The following would turn 🧪 into ✅ with your help:

1. A dump from a **V1.11** unit (to add a third profile).
2. A dump from a **4 MiB** M-241 or a **GR-245** (to confirm the flash geometry
   and the 3-byte-height/first-field-speed swap).
3. Confirmation that the patched image boots on **macOS + Apple Silicon** and on
   **Linux** (the upstream project verified macOS only).

Open an issue with `m241 doctor --json` and `m241 patch --dry-run` output —
those contain no proprietary bytes.
