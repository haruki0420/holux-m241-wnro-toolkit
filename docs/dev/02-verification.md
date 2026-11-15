# Verification & evidence

This page records how every claim in this repository was checked, so a reader
does not have to trust the README. Most of it is reproducible on a machine with
no M-241 attached; section 8 records the hardware session.

## 1. Firmware profiles are measured, not guessed

The official Holux update packages were hashed and analysed byte by byte. The
tool that does it ships in the repo:

```bash
python tools/analysis/analyze_firmware.py vendor/*_V1.12_*.bin vendor/*_V1.13_*.bin
python tools/analysis/analyze_firmware.py --plan vendor/*_V1.13_*.bin
python tools/analysis/analyze_firmware.py --diff vendor/*_V1.12_*.bin vendor/*_V1.13_*.bin
```

What it reports for the two stock images (this is the origin of every offset in
`docs/reference/03-firmware-offsets.md`):

| | V1.12 | V1.13 |
|---|---|---|
| size | 406,312 | 406,620 |
| sha256 | `4103601a…` | `32fe5269…` |
| epoch constant LE32 at | `0x402DC` **and** `0x402F4` | same |
| ASCII version marker | `V1.12` @ `0x59B14` | `V1.13` @ `0x59C28` |
| build number (`0x108`) | 51 | 67 |
| device type (`0x1B0`) | `01017-00E` | `01017-00F` |
| log record (utility default) | 16 B | 20 B |

The record size is a property of the block-header bitmask, not the firmware
version; see [`02-log-format.md`](../reference/02-log-format.md) §4.

## 2. The patch output is deterministic

```bash
python -c "import sys; sys.path.insert(0,'src'); \
from m241.firmware import FirmwareImage, patch_file; \
p = patch_file('vendor/V1.13.bin', '/tmp/out.bin'); \
print(p.after_sha256)"
```

| Input | Output SHA256 | Changed offsets |
|---|---|---|
| V1.12 `4103601a…` | `dad2983d95cc3be251b358b2b7d5da9272d9946e70aaa0067162a4c21475c384` | `0x402F6`, `0x402F7`, `0x59B18` |
| V1.13 `32fe5269…` | `901555843895054f77716364a446e8ad41c00b799c781b4ff95144352c141c9a` | `0x402F6`, `0x402F7`, `0x59C2C` |

Those three-byte diffs are the whole patch: the two high bytes of the LE32 epoch
constant and one digit of the version string.

`tests/test_firmware_patcher.py` asserts exactly this — including that `0x402DC`
is **not** touched: patching both occurrences would double-shift the clock.

## 3. The whole protocol stack is exercised without hardware

`m241.protocol.simulator` implements the MT3318 BootROM and the DA handshake. It
is not a mock: it drives the real `MtkBootRom` and `DownloadAgent` code paths,
including echo verification, the XOR16 checksums, the 16-bit byte-swapped write
format, the 20-byte DA info report, and the `0xD3`/`0xD5`/`CONT` packet protocol.

```bash
m241 selftest
m241 doctor --simulate
pytest tests/test_protocol_simulator.py tests/test_protocol_da.py -q
```

## 4. End-to-end check against a real vendor drop

If you have the official Holux packages:

```bash
python tools/verify_against_drop.py /path/to/holux --json
```

It (a) hashes every file against the shipped manifest, (b) patches both stock
images and compares the output hashes, (c) dumps both images through the
simulator and compares byte for byte, and (d) uploads the real `DA_v3.8.4.bin`
through the simulated BootROM and checks it lands at `0x00100800`.

On the drop this project was built from:

```
18/18 checks passed
```

## 5. Capacity numbers

```bash
m241 logs capacity
```

```
flash  blocks  usable bytes   16-byte records      20-byte records
512K   8       520,192        32,512 (9.0 h)       26,009 (7.2 h)
2M     32      2,080,768      130,048 (36.1 h)     104,038 (28.9 h)
4M     64      4,161,536      260,096 (72.2 h)     208,076 (57.8 h)
```

The arithmetic: `0x10000 - 0x200 = 65024` usable bytes per 64 KiB block, times
the block count, divided by the record size. The 512 KiB row is geometry, not a
measured log capacity: the DA reports a 512 KiB part while a unit held ~212 KB
of log alongside a 406,620-byte firmware image. `tests/test_logfile_capacity.py`
locks the numbers and `tests/test_portability.py` asserts the README advertises
the same ones.

## 6. NMEA repair against the real observed sentence

From the source write-up, on a broken unit in 2026:

```
$GPRMC,022127.542,V,8960.0000,N,00000.0000,E,0.00,0.00,170906,,,N*7F
```

```bash
echo '$GPRMC,022127.542,V,8960.0000,N,00000.0000,E,0.00,0.00,170906,,,N*7F' \
  | m241 nmea repair --offset-days 7168
```

```
$GPRMC,022127.542,V,8960.0000,N,00000.0000,E,0.00,0.00,030526,,,N*74
```

`170906` (2006-09-17) → `030526` (2026-05-03), and the XOR checksum is recomputed
(`7F` → `74`). The device's own C++ debug lines (`======Setting_ProcessKeys====`)
pass through untouched.

## 7. Cross-platform

Every path in the codebase goes through `pathlib`; serial discovery goes through
`m241.protocol.ports`; [`docs/user/02-install.md`](../user/02-install.md)
documents driver/permission setup for all three OSes.
`tests/test_portability.py` enforces all of it:

- no hard-coded `/dev/tty*` or `"COM3"` in logic,
- no top-level `import serial` outside `m241.protocol`,
- the offline commands (`patch`, `logs`, `nmea`, `selftest`) run in a subprocess
  where `import serial` is **blocked**,
- `.gitignore` cannot hide the source tree again (this is how the
  `src/m241/firmware/` regression was caught),
- the README's platform/capacity claims match what the code computes.

CI runs the suite on Linux 3.10 / 3.11 / 3.13, Windows 3.12 and macOS 3.12; ruff
and the docs link check run once, in the Linux lint job.

## 8. What was and was not verified on hardware

This repository dumped, patched and flashed a **retail M-241** itself on
2026-11-15: Windows 11, CP210x USB cable, firmware V1.13 (`B-core_1.20`, build
`0043`, model `01017-00F`).

| Step | Result |
|---|---|
| `m241 dump` | 406,620 bytes, read twice, SHA-256 identical both times |
| `m241 patch` | 3 bytes changed (`0x402F6`, `0x402F7`, `0x59C2C`) |
| `m241 flash` | the write completed and the download agent restarted |
| Boot after flash | the logger boots |
| NMEA date after flash | `$GPRMC` carried `151126` = 2026-11-15 |
| `m241 logs download` | 212,860 bytes, 10,399 records, 0 CRC errors |

The WNRO fix, the `0x402F4` offset, the `0x00200000` firmware base, the DA load
address and the `0xD3` / `0xD5` write framing were therefore all confirmed on
this unit. The upstream write-up
([catea.tech](https://catea.tech/blog/holux-m241-wnro-fix)) is the origin of the
patch; this project reproduced it and flashed its own result.

**Not verified — treat as ⚠️ unverified:**

- **The OLED display after flashing.** Never observed.
- **Linux and macOS with real hardware.** The code runs in CI; no device was
  connected.
- **Bluetooth SPP.** All hardware work went over USB.
- **4 MiB units and GR-245 variants.** The geometry maths and the GR-245 field
  swap are implemented and unit-tested, but no such device was available.
- **Firmware V1.11 and V1.12 on hardware.** No image to profile for V1.11, and
  V1.12 was never flashed. `m241 patch` refuses rather than guesses.
- **Whether the binary log's timestamps are corrected.** Inferred from the
  shared NMEA code path; no post-flash log was downloaded.
- **Any timing figure.** There is no stopwatch data in this repository.

If you can close one of those gaps, see `CONTRIBUTING.md`.
