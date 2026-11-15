# Firmware profiles: verified facts for V1.12 and V1.13

Everything in this file was derived from the official Holux update packages in
the `holux/` drop (not committed to the repository) and cross-checked byte for
byte. It is the single source of truth for `m241/data/firmware_profiles.json`
and for the patch logic. **Do not invent offsets.**

## Files analysed

| Version | Member name inside the official zip | Size | SHA256 |
|---|---|---|---|
| V1.12 | `01017-01B MBGPS_2.02B1.20,38400, GGA,GSA,GSV,VTG,RMC_V1.12_GR-241_HLX.bin` | 406312 (`0x63328`) | `4103601a29f256ae5da771bbcbc3ca85a89deb6268ce74865dc84b2583ae98f7` |
| V1.13 | `01017-01C MBGPS_2.02B1.20,38400, GGA,GSA,GSV,VTG,RMC_V1.13_GR-241_HLX.bin` | 406620 (`0x6345c`) | `32fe52690db9947bdbfa58caa6dba450b3f4222b23f2e045f606252649159435` |

`DA_v3.8.4.bin` (all four copies in the drop, and in both logger utilities):
size 14896, SHA256 `190d7064cf2320752d8eb65681142730b21cddc90e0d1491d4e8379c3f2e89aa`.

## Header fields (same offsets in both images)

| Field | Offset | Value |
|---|---|---|
| firmware family string | `0x90` | `HOLUX_M-241` |
| firmware size (LE u32) | `0xF4` | 406308 (V1.12) / 406616 (V1.13) (= file size − 4) |
| build number (LE u16) | `0x108` | 51 (V1.12) / 67 (V1.13) |
| release name | `0x148` | `B-core` |
| release major/minor | `0x15A`/`0x15B` | 1 / 20 → `B-core_1.20` |
| serial port string | `0x190` | `SPP Slave` |
| device type string | `0x1B0` | `01017-00E` (V1.12) / `01017-00F` (V1.13) |

Note the two versions differ in **build number and device type string**, which
is a second, independent way to tell them apart when the hash is unknown (e.g.
because the image is a locally-built variant). On the success path
`m241 patch --dry-run` prints the input, the detected profile, the version, the
patch lines, the changed offsets and the two SHA-256 values; the header fields
(family, device type, build number) appear only in the error path when no
profile can be detected.

The first 32 bytes are a vector table (`18 f0 9f e5` repeated = `ldr pc, [pc, #-0x18]`),
i.e. a thumb image loaded at **`0x00200000`** by the MTK BootROM, **not** at
`0x0`. `0x20000000` is the MT333x-style probe address: on this part it reads
back `42 20 02 02` (unmapped bus). That is why `m241 dump` defaults to
`--base-addr 0x00200000`; the original blog post used `--start-addr 0x0` with a
patched argument and that only works because the DA maps the flash linearly.
Keep both options, document both.

## The Unix-epoch constant

`315964800` = `0x12D53D80` = Unix seconds at the GPS epoch 1980-01-06 UTC.
It appears **twice**, little-endian, in both firmwares:

| Occurrence | V1.12 | V1.13 | Meaning |
|---|---|---|---|
| first  | `0x402DC` | `0x402DC` | unused/alternate copy — patching it too double-shifts the clock |
| **effective** | **`0x402F4`** | **`0x402F4`** | the GPS→Unix conversion actually used by the NMEA/log path |

So the WNRO patch is a single LE32 store:

```
offset 0x402F4 : 0x12D53D80 -> 0x37BF3D80   (315964800 -> 935280000)
```

`935280000 - 315964800 = 619315200 = 1024 * 604800` = +1024 weeks = +7168 days.

Because the source constant lives at the *same* offset in both versions, a
single offset works for V1.12 and V1.13 — but the profiles still carry it
separately so a future V1.11/V1.14 dump can be added without code changes.

## The version string

ASCII `V1.xx` appears exactly once in each image:

| Version | Offset | Bytes |
|---|---|---|
| V1.12 | `0x59B14` | `56 31 2E 31 32` |
| V1.13 | `0x59C28` | `56 31 2E 31 33` |

(Note: the upstream project's `0x59C2C` is the *last* byte of the string; it
flipped `'3'`→`'4'` for V1.13 only and silently did nothing for V1.12. Our
implementation rewrites the whole 5-byte run so it is version-agnostic.)

Cosmetic marker patch: rewrite `V1.12`/`V1.13` → `V1.14` (5 bytes). This is
what makes a modified unit identifiable; it is on by default and can be
disabled with `--no-version-marker`.

## Verified patch results (computed, reproducible)

| Input | Patch set | Output SHA256 |
|---|---|---|
| V1.12 (`4103601a…`) | epoch@`0x402F4`, version→V1.14 | `dad2983d95cc3be251b358b2b7d5da9272d9946e70aaa0067162a4c21475c384` |
| V1.13 (`32fe5269…`) | epoch@`0x402F4`, version→V1.14 | `901555843895054f77716364a446e8ad41c00b799c781b4ff95144352c141c9a` |

Changed byte offsets:

- V1.12: `0x402F6`, `0x402F7`, `0x59B18`  (the two high bytes of the LE32 and the `2`→`4` of the version)
- V1.13: `0x402F6`, `0x402F7`, `0x59C2C`  (the two high bytes of the LE32 and the `3`→`4` of the version)

These are asserted by `tests/test_firmware_patcher.py` against synthetic images
and by `tools/verify_against_drop.py` against the real drop when it is present.

## Log record size (the "12 vs 13" difference)

The release note shipped with V1.13 says:

> `HoluxLoggerUtility_20090814_1000_Ver1.1.0.48`
> 2. log format 16 Byte change to 20 Byte. new Speed format.

That is a change to the default log configuration, not a property of the
firmware: **the record size is determined by the bitmask in the block header**.
A V1.13 unit can write 16-byte records (UTC + lat + lon + height) or 20-byte
records (with the 4-byte float speed field), depending on what the log session
was configured for; V1.12 writes whatever its bitmask says too. The release
note tells you which default the utility used, not what any given block holds.

Both families nevertheless advertise *"130,000 points"* in the manual. That
number is only reachable on a 2 MiB log area with the 16-byte format:

```
usable per 64 KiB block = 0x10000 - 0x200 (header) = 65024
2 MiB   -> 32 blocks -> 2,080,768 usable bytes
   16 B/record -> 130,048 points   <- marketing "130,000"
   20 B/record -> 104,038 points
512 KiB -> 8 blocks  -> 520,192 usable bytes
   16 B/record -> 32,512 points
   20 B/record -> 26,009 points
```

The retail unit this project was verified on reports a **512 KiB** flash, so
**104,038 applies only to a hypothetical 2 MiB log area**; on the measured
geometry the figures are 32,512 / 26,009. `m241 logs capacity` prints the honest
table for 512 KiB, 2 MiB and 4 MiB flash, both formats, and `m241 logs info`
reports the *actual* record length found in the dump's bitmask. This is the
"firm 12/13 で記録できる数が違う" issue: it must be derived from the image,
never hard-coded.

## Flash geometry observed

- Flash chip reported by the DA on a V1.13 retail unit: **512 KiB**, MX29LV400CB,
  manufacturer `0xC2`, 16-bit device code `0x22BA`, DA `flash_device_id = 0x49`.
  (`0x22BA` is the device-code field, not the byte-wide id.)
- BootROM memory map on this chip: firmware visible at **`0x00200000`**;
  `0x20000000` reads back `42 20 02 02` (unmapped bus). RAM at
  `0x100000`–`0x1FFFFF`, DA must be loaded at **`0x00100800`**
  (`0x100000`–`0x1007FF` is BootROM stack/scratch and is lethal to write).
- `BOOT_SYNC = a0 0a 50 05` → replies `5F F5 AF FA`.
- `WRITE16` expects the data stream **byte-swapped in 16-bit pairs** on the
  wire; the chip stores it little-endian.
- The verified DA load address is confirmed by the vendor utility:
  `HoluxLoggerUtility.exe` contains `68 00 08 10 00` (`push 0x100800`) exactly
  once, at file offset `0xB7B0`.

## What is deliberately *not* in this repository

`brom.dll`, `DA_v3.8.4.bin`, the firmware `.bin` images, `HoluxLoggerUtility.exe`
and the vendor zips are proprietary. They are `.gitignore`d. The repo ships
**profiles** (hashes, offsets, sizes) and a tool that extracts what it needs
from the user's own copy of the official package, plus a pure-Python DA/BootROM
implementation so no vendor code is required at runtime.
