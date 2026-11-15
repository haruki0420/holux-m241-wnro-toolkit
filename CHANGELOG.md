# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

Everything below was found while flashing a real M-241 end to end. The previous
release could dump and patch, but its **write path did not work on hardware**.

- **`0xD3` replies with two bytes, not one.** The DA sends ACK *and* a status
  byte. Reading only the ACK left the status byte in the buffer, shifting every
  later frame by one byte, so the `0xD5` step always timed out. The client now
  reads both and reports a non-zero status as an error.
- **WRITE16 echo trails by one byte.** After sending N bytes only N-1 echo bytes
  exist, and the final byte's echo never arrives. Requiring an exact echo (or
  writing one byte and waiting for its echo) hangs on hardware. `write16` now
  accumulates the echo and compares it against everything sent minus its last
  byte, then drains the tail.
- **`0xD3` is sent as one ten-byte frame.** Splitting it into header / begin /
  end writes was an unverified guess; the single frame is what works.
- **Reads carry no checksum trailer.** `0xA2`/`0xAF` return echo + data only;
  the checksum is a separate `0xA4` command. Consuming a trailer desynchronised
  every read.
- **Firmware base is `0x00200000`.** `0x20000000` returns `42 20 02 02`
  (unmapped bus) on this part.
- **`0xAF` returns byte-reversed 32-bit words**, which is not the same
  transformation as the 16-bit pair swap used by `0xA2`/`0xA1`.
- **`$PMTK180*3B` needs a live NMEA stream.** The client now checks for one and
  reports a precise error instead of a bare sync timeout, and it refuses to
  pulse the reset lines when it cannot tell whether the firmware is running
  (doing so leaves a running logger silent).

### Added

- **Hardware-shaped regression tests**: a full 406,620-byte image write
  (1,588 packets + a 92-byte tail), `0xD3` status-byte consumption, and a
  non-zero status report.
- `docs/01-protocol.md` rewritten with the measured values, including the
  one-byte echo lag, the two-byte `0xD3` reply, the real 512 KiB flash size,
  and a recovery procedure for an interrupted flash.

### Changed

- `tools/re/` renamed to `tools/analysis/`.
- Removed the ad-hoc scripts used while bringing the write path up; the proven
  logic lives in `m241.protocol` and is exercised by the test suite.

## [1.0.0] — 2026-11-15

First public release. Cross-platform (Windows / Linux / macOS) re-implementation
of the Holux M-241 GPS week-number-rollover repair.

### Added

- **MTK BootROM client** (`m241.protocol.brom`): sync sequence, `READ16`,
  `READ32`, `CHECKSUM`, `JUMP`, `WRITE16` with echo verification and the
  byte-swapped wire format, pluggable serial factory.
- **Download Agent driver** (`m241.protocol.da`): DA v3.8 handshake, 20-byte
  info report parsing, `0xD3`/`0xD5` write protocol with per-packet `CONT`,
  optional high-baud switch, restart. DA load address `0x00100800` (the value
  recovered from `push 0x100800` in `HoluxLoggerUtility.exe`).
- **Serial port discovery** (`m241.protocol.ports`): CP210x / PL2303 / FTDI /
  CH340 heuristics, `auto` resolution, OS-specific error hints.
- **Hardware-free simulator** (`m241.protocol.simulator`): a complete MT3318
  stand-in so the whole stack, including the DA write path, is testable on CI.
- **Firmware profiles** for **V1.12** (`0x63328`, 16-byte log records) and
  **V1.13** (`0x6345C`, 20-byte log records), including official SHA256s and
  the expected patched SHA256s. Detection is hash-first, then
  size + version + epoch-offset.
- **WNRO patch**: single LE32 store at `0x402F4`
  (`315964800` → `935280000`, i.e. +1024 weeks / +7168 days). The unused
  duplicate constant at `0x402DC` is deliberately left alone.
- **Version marker patch**: rewrites the whole 5-byte ASCII `V1.xx` run to
  `V1.14`, so it works on both V1.12 (upstream's single-byte patch only worked
  on V1.13).
- **Atomic patch output**: temp file + `os.replace`; a failure never leaves a
  truncated firmware image behind. In-place overwrite requires an explicit flag.
- **Binary log parser** (`m241.logfile`): MTK/Holux format, M-241 quirks
  (4-byte float lat/lon, 3-byte height, single-byte CRC with no `'*'`), 64 KiB
  block rollover, 0xFF padding, info blocks, waypoint flags, per-record CRC
  status, strict mode.
- **Capacity maths**: honest point counts for 2 MiB / 4 MiB flash × 16-byte /
  20-byte records — **130,048 / 104,038 / 260,096 / 208,076** — plus duration
  estimates. This is the "V1.12 holds more than V1.13" difference the manuals
  hide.
- **Exporters**: GPX 1.1, CSV, NMEA (RMC reconstruction), KML, JSON.
- **NMEA repair**: file/stream/`stdin` rewriting with automatic stale-date
  detection, checksum recomputation, and verbatim pass-through of the device's
  C++ debug lines.
- **NMEA proxy**: live date correction over TCP (`127.0.0.1:10110` default) so
  mapping software sees the right date without flashing.
- **CLI** (`m241`): `ports`, `doctor`, `info`, `dump`, `patch`, `flash`,
  `logs info|read|capacity`, `nmea repair|proxy`, `selftest`, with documented
  exit codes and `--json` output.
- **Compatibility shims** in `scripts/` so the original three-step
  dump → patch → flash workflow from the source write-up still reads naturally.
- **Vendor-drop tooling**: `m241 doctor --from-drop` and
  `tools/verify_against_drop.py` identify and verify the proprietary files
  (hashes only; nothing is redistributed), and
  `tools/analysis/analyze_firmware.py` reproduces every offset in this repo.
- **Documentation**: background, protocol, log format, platform notes, firmware
  profiles, vendor-drop inventory, troubleshooting, CLI spec, roadmap,
  Japanese background, and a `DISCLAIMER.md` that says plainly what can brick.
- **CI**: Ubuntu / Windows / macOS × Python 3.10–3.13, plus a build job that
  asserts the wheel ships the profile JSON.

### Verified

- V1.12 `4103601a…` → `dad2983d95cc3be251b358b2b7d5da9272d9946e70aaa0067162a4c21475c384`
- V1.13 `32fe5269…` → `901555843895054f77716364a446e8ad41c00b799c781b4ff95144352c141c9a`
- Both stock images dump byte-identically through the simulator and the DA
  binary loads correctly at `0x00100800` (`tools/verify_against_drop.py`
  reports 18/18 checks).

### Notes

- The WNRO fix and the `0x402F4` offset were originally established on real
  hardware by [猫葉Tech / Catea Tech](https://catea.tech/blog/holux-m241-wnro-fix)
  and [NekobaTechnology/holux-m241-wnro-fix](https://github.com/NekobaTechnology/holux-m241-wnro-fix).
  This release is an independent, tested, cross-platform implementation.
- No proprietary binary is distributed. `.gitignore` blocks firmware images,
  vendor DLLs and executables.
