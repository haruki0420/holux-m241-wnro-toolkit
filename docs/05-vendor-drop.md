# The vendor drop: what is in `holux/` and what the tool uses

The project was built from a folder of official Holux packages (the "drop").
Those files are **proprietary and are not committed**. This page records exactly
what the drop contains, what each file is good for, and how `m241 doctor
--from-drop` consumes it.

Manifest JSON shipped with the package:
[`src/m241/data/vendor_drop.json`](../src/m241/data/vendor_drop.json).

## Inventory

`<sha256>  <bytes>  <path inside the drop>`

```
190d7064cf2320752d8eb65681142730b21cddc90e0d1491d4e8379c3f2e89aa    14896  Holuxlogger Utility_1.1.0.47/Utility_1.1.0.47/DA_v3.8.4.bin
190d7064cf2320752d8eb65681142730b21cddc90e0d1491d4e8379c3f2e89aa    14896  Holuxlogger Utility_1.1.0.48/Utility v1.1.0.48/DA_v3.8.4.bin
a62e692f410d718570f19eb4f45591049fe8a687c1b309a4f19bfe3bcf776ed5   245760  .../brom.dll                (x4, identical)
3741703327fb9cf909380e0e2a7e0f3c9e669d8ca400fbd31f402d00592adbdd      364  .../brom.ini                (x4, identical)
0381cf50831339d03ecb48823572e57298e43d40f5928604d7fa29449222c458       15  .../scat.txt                (x4, identical)
7b7dcb89ce08cb980a467de612205b176a8c10899b7b2902e8ac7981855733fa  1187840  .../HoluxLoggerUtility.exe  (V1.1.0.47 build)
0360fd2eb1e5775256c82eb044c720a551c2acd7d5e76b1d5d02a23899cd4ec8  1187840  .../HoluxLoggerUtility.exe  (V1.1.0.48 build)
4103601a29f256ae5da771bbcbc3ca85a89deb6268ce74865dc84b2583ae98f7   406312  M-241_Firmware_V1.12_update_package/.../*_V1.12_GR-241_HLX.bin
32fe52690db9947bdbfa58caa6dba450b3f4222b23f2e045f606252649159435   406620  M-241_Firmware_V1.13_update_package/.../*_V1.13_GR-241_HLX.bin
da12519e6cc5cdf6641700d168499187704a8a32c37d9854838b3bb57b7e9762    29093  M-241_Firmware_V1.12_update_package/.../Release Note.pdf
782b0a1093d4d7d410897fbf08c1880cb774f8b7f91381938896321392b7ff0b     1550  M-241_Firmware_V1.13_update_package/.../ReleaseNote.txt
```

Plus (not used by the tool): CP210x Windows drivers (`CP210xVCPInstaller_*.exe`,
`slabvcp.inf/.cat/.sys`), `MiniGPS Lite for PC/PPC`. Those are drivers and a
viewer; the toolkit never needs them at runtime, but the CP210x driver is what
makes the USB cable appear as a COM port on Windows. macOS and Linux need no
driver.

## Why each file matters

### `DA_v3.8.4.bin` — the Download Agent (required for flashing)

14,896-byte MTK Download Agent. It is the only file the flash step truly needs
from the drop. Two facts pin it down:

- The M-241 BootROM loads it at **`0x00100800`** (not `0x100000` — that region
  is the BootROM's own stack).
- The vendor utility proves this address: `HoluxLoggerUtility.exe` contains the
  instruction `push 0x100800` (`68 00 08 10 00`) **exactly once**, at file
  offset `0xB7B0`, immediately before a `call DA_SetStartAddr` via the `brom.dll`
  ordinal. That single instruction is the whole "how do I boot the DA" answer.

`m241 doctor --from-drop` extracts it to `vendor/DA_v3.8.4.bin` and verifies
`190d7064…`.

### `brom.dll` — the vendor's BootROM driver (not needed, but explains a lot)

245,760-byte PE32 DLL. It is the reference implementation of the protocol we
reimplemented: `BRom_AutoBoot::StartCmd` (the `a0/0a/50/05` sync),
`DownloadData` (single-shot `0xA1` + whole data stream, *not* per-chunk
transactions), and `DA_SetStartAddr`. Its vtable layout is what made the
Read16/Write16/Jump opcodes recoverable. We do not load or ship it.

### `brom.ini` — flash chip table (useful for decoding the DA report)

```
[Flash Device Name]
70 = [MXIC]MX29LV320AT
71 = [MXIC]MX29LV320AB
...
84 = [MXIC]MX29LV800CB
```

The keys are the DA's `flash_device_id`. An M-241 with a 2 MiB MXIC part reports
`flash_device_id = 0x22BA` in the 20-byte DA info report, which is the
`MX29LV320` family → 2 MiB. `m241.flash` / `m241 logs capacity` use that to pick
the right geometry instead of assuming 2 MiB.

### The two firmware images — patch targets *and* profile sources

| Version | Size | SHA256 |
|---|---|---|
| V1.12 | 406,312 | `4103601a29f256ae5da771bbcbc3ca85a89deb6268ce74865dc84b2583ae98f7` |
| V1.13 | 406,620 | `32fe52690db9947bdbfa58caa6dba450b3f4222b23f2e045f606252649159435` |

These are the official update images. They give us the profile table
(offsets + hashes) and the expected patched hashes, and they are what
`m241 doctor` checks your own dump against. **They are not needed to flash a
device you dumped yourself** — a dump plus the DA is enough — but they are the
ground truth for "did my patch produce a sane image".

### The two `HoluxLoggerUtility.exe` builds — the log-format change

V1.1.0.47 shipped with V1.12, V1.1.0.48 with V1.13. Their release note is the
source for the 16→20-byte log record change:

> `HoluxLoggerUtility_20090814_1000_Ver1.1.0.48`
> `2. log format 16 Byte change to 20 Byte. new Speed format.`

That is the whole reason a V1.13 unit stores ~104k points where a V1.12 unit
stores ~130k, even though both manuals say 130,000. See
[`04-firmware-profiles.md`](04-firmware-profiles.md).

### `ReleaseNote.txt` (V1.13) — other useful lines

```
5. Fixed Photo issue.
6. Add Download FW erase all Log.
7. Add discriminate 2M form 4M by flash ID
```

Line 7 is why the tool asks the DA for `flash_size` and the flash ID rather than
assuming 2 MiB: Holux itself had to cope with both.

## How `m241 doctor --from-drop` uses it

```
m241 doctor --from-drop ../holux --extract-to vendor/
```

1. Recursively hash every file in the drop.
2. Match against `src/m241/data/vendor_drop.json`.
3. Report: which firmware versions are present, whether the DA is present and
   its hash matches, which utility builds you have.
4. With `--extract-to`, copy **only** `DA_v3.8.4.bin` (and, if you ask with
   `--include-firmware`, the stock images) into the target directory, verifying
   the hash after the copy.

It never modifies the drop, never uploads anything, and never copies the vendor
DLLs or executables anywhere.
