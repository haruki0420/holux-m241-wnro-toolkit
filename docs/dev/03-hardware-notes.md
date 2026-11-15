# Hardware session notes — M-241 V1.13 on Windows 11

Device: `B-core_1.20`, build `0043`, model `01017-00F` (**V1.13**), MT3318.
Host: Windows 11 Pro, CP210x bridge (VID 10C4 / PID EA60) as a COM port.

Sections 1–7 were measured on the device itself. The destructive flash step is
called out where it appears; everything else used read-only commands. The
SHA-256 values are this unit's — the dump contains per-unit runtime fields, so
your hash will be different.

---

## 1. `m241 info` needed a CRLF terminator (fixed)

```
CRLF     -> $PMTK705,B-core_1.20,0043,01017-00F,1.0*72
no CRLF  -> (no reply)
```

The MTK firmware only parses a PMTK command once it sees a line terminator.
`_nmea_collect()` now appends `\r\n` to every sentence it writes.

## 2. Entering the BootROM

The entry sequence that works on this unit:

```
1. 38400, send $PMTK180*3B\r\n        -> NMEA stops (chip is listening)
2. 115200, assert DTR and RTS (hold them)
3. 0xA0 repeated                      -> 0x5F  (36 probes on this unit)
4. 0x0A -> 0xF5, 0x50 -> 0xAF, 0x05 -> 0xFA
```

Two details matter and are now in `MtkBootRom`:

- **The command is `$PMTK180*3B\r\n` with no arguments.** An earlier version of
  this toolkit sent `$PMTK180,<baud>`, which the firmware silently ignores.
- **The three follow-up probes must be sent back to back.** Any pause makes the
  chip give up on the handshake; this unit is happier with no delay at all.

The reply to each probe is accepted either as the probe byte itself or as its
bitwise complement (`0xA0` → `0x5F`, `0x0A` → `0xF5`, `0x50` → `0xAF`,
`0x05` → `0xFA`).

## 3. Where the firmware lives and how it is read

```
0x00000000: ea ff ff ff e1 0f 00 00 ...   (BootROM code)
0x00200000: e5 9f f0 18 e5 9f f0 18 ...   <- firmware, words byte-reversed
0x20000000: 42 20 02 02 42 20 02 02 ...   <- unmapped bus
```

| Item | Value on this hardware |
|---|---|
| Firmware base | **`0x00200000`** (not `0x20000000`) |
| Read opcode | `0xAF` READ32 |
| Count field | **32-bit dwords** for `0xAF` (16-bit words for `0xA2`/`0xA1`/`0xA4`), not bytes |
| Reply shape | echo(9 bytes) + data — **no checksum trailer** |
| 32-bit word order | **byte-reversed** within each word (`e5 9f f0 18` = `18 f0 9f e5`) |
| `0xA4` CHECKSUM | answers with a 2-byte value (the only command with a trailer) |

`BASE_ADDR_FIRMWARE` defaults to `0x00200000`;
`BASE_ADDR_FIRMWARE_MT333X = 0x20000000` is kept for probing other parts.

## 4. Real dump: 406,620 bytes, reproducible

```
size    : 406620
sha256  : ee52d339205348a53e67fa8990512b594c9a325e7756c2805bf5ce3e020709ae
first 16: 18 f0 9f e5 18 f0 9f e5 18 f0 9f e5 18 f0 9f e5
```

Two independent full reads produced the **same SHA256**, and `18 f0 9f e5`
(`ldr pc, [pc, #-0x18]`) is exactly the vector table the profile table predicts.

### Deltas versus the official V1.13 package: 14 bytes, all known

| Offset | Count | Meaning |
|---|---|---|
| `0x05C` | 4 | programming magic written by the vendor tool (package has `FF`) |
| `0x0F8` | 6 | build/flash counters written at flash time |
| `0x63458` | 4 | image trailer written at flash time |

`14/14 differing bytes are inside documented runtime fields`, with no
unexplained divergence — so the unit is not modified and the read path is
correct.

## 5. The WNRO bug reproduced on this unit

```
$GPRMC,060530.588,V,8960.0000,N,00000.0000,E,0.00,0.00,010407,,,N*..
```

`010407` = **2007-04-01**, while the wall clock was 2026-11-15: the documented
7168-day (1024-week) offset.

## 6. Patch applied to the real dump

```
input  : backup/m241_stock.bin   ee52d339...
profile: v1.13
  0x402F4  80 3d d5 12 -> 80 3d bf 37   (WNRO: +619315200 s = +1024 weeks)
            changed: 0x402F6, 0x402F7
  0x59C28  "V1.13" -> "V1.14"
            changed: 0x59C2C
output : backup/m241_v114_wnro.bin  9f192786c1cbd0a2bef3a53fbbdd0e6e850065de48be739927f2c650124dd4f2
```

Exactly three bytes differ between the stock dump and the patched image, at the
three documented offsets.

## 7. Status

Done and verified:

- [x] device identified (V1.13 / MT3318)
- [x] BootROM entry works
- [x] two identical dumps, correct vector table
- [x] dump explained byte-for-byte against the official package
- [x] profile detected as v1.13
- [x] patched image built; the three changed bytes verified
- [x] stock backup kept at `backup/m241_stock.bin`
- [x] patched image flashed back; the DA accepted every packet and restarted
- [x] the logger boots after the flash
- [x] `$GPRMC` reports 2026-11-15 (`151126`) after the flash

Not done / not observed:

- [ ] the OLED display after flashing (never seen)
- [ ] a post-flash log download, so the binary log's corrected timestamps are
      inferred from the shared NMEA code path rather than measured
- [ ] Linux, macOS, Bluetooth, 4 MiB and V1.11/V1.12 on hardware

The write path is destructive and was exercised exactly once, end to end, on
this unit. `m241 flash` does **not** read back and compare; success means the DA
accepted every packet and was restarted.

## 8. Tooling

The one-off bring-up scripts used during this session were removed in the
repository reorganisation; the knowledge they produced lives in this page and in
`docs/reference/01-protocol.md`. The table below supersedes the earlier lists in
this page's history, which named one-off scripts that no longer exist
(probe_device.py, diag_pmtk.py, bootrom_read.py, dump_firmware_hw.py and
others).

What remains:

| Tool | Purpose |
|---|---|
| `tools/compare_dump.py` | explain the byte differences between a real dump and the stock image |
| `tools/analysis/analyze_firmware.py` | offline firmware analysis; produced the offsets in `docs/reference/` |
| `tools/hardware/recover_bootrom.py` | try to revive a logger that stopped answering |
| `tools/hardware/test_da_protocol.py` | exercise the flash-write commands without writing flash |
| `tools/verify_against_drop.py` | hash-check an official Holux package against the shipped manifest |
| `tools/check_all.py` | run every gate (lint, docs, tests) and print one line each |
| `tools/check_docs.py` | fail if a relative Markdown link or repository path is broken |
| `tools/check_wheel.py` | install the built wheel in isolation and smoke-test the CLI |
