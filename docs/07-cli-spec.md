# CLI specification (`m241`)

This is the authoritative behaviour spec for `src/m241/cli.py`. It extends
[`ARCHITECTURE.md`](ARCHITECTURE.md) with the details that matter for user-facing
behaviour. Tests in `tests/test_cli.py` enforce it.

`main(argv: list[str] | None = None) -> int` — argv defaults to `sys.argv[1:]`.
`-h` / `--help` exits 0. Any unknown subcommand or bad option exits **2** with
usage on **stderr**. Everything machine-readable goes to **stdout**; progress,
warnings and logs go to **stderr**.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | runtime error (I/O, parse, patch refused, DA refused, …) |
| 2 | usage error (argparse) |
| 3 | hardware not found (no port, can't open it, no BootROM sync) |
| 4 | verification failed (hash mismatch, two dumps differ, profile mismatch) |
| 5 | safety refusal (destructive action without confirmation / missing backup) |

## Global behaviour

- No command may import `serial` at module import time.
- `--json` is available on `ports`, `doctor`, `patch`, `logs info`,
  `logs capacity`, `nmea repair --stats`, `info`. Output is a single JSON object
  on stdout, pretty-printed with `indent=2`, ASCII-safe (`ensure_ascii=False` is
  allowed but keys are ASCII).
- Paths are created with `parents=True, exist_ok=True` when writing.
- Progress reporting is `\r`-based on stderr with a percentage; it must be
  silent when stderr is not a TTY **and** `--quiet` is set.
- Every command accepts `--quiet` / `-q` (suppress progress) and `--verbose`/`-v`
  (debug detail on stderr). Unknown combos must not crash.

## `m241 ports [-q] [--json]`

Lists serial ports. Table columns: device, description, hwid, candidate marker
(`*` for a plausible CP210x/PL2303/FTDI/CH340 bridge).

`--json`: `{"ports": [{"device", "description", "hwid", "vid", "pid",
"is_candidate"}], "candidates": ["COM5"], "platform_hint": "..."}`.

Exit 0 even when empty (an empty list is not an error); `"note"` explains what to
do next.

## `m241 doctor [--json] [--simulate] [--from-drop PATH] [--extract-to DIR] [--include-firmware]`

Diagnostics. Sections:

1. **environment** — Python version, platform, `pyserial` version (import and
   report; a missing pyserial is a distinct, clearly-labelled failure, not a
   traceback), whether the package data files resolve.
2. **ports** — same data as `m241 ports`.
3. **vendor drop** — only with `--from-drop`: walk the directory, hash every
   file up to 8 MiB, match against `src/m241/data/vendor_drop.json`
   (basename + size + sha256), and report per role: found / missing / wrong hash.
   With `--extract-to DIR`, copy **only** the `download_agent` role (and the two
   `firmware` roles when `--include-firmware` is given) into DIR, verifying the
   hash after the copy. Never copy DLLs/EXEs. Never modify the drop.
4. **device** — with `--simulate`, run against
   `m241.protocol.simulator` and report "simulated device OK". Without it, list
   ports and say which command to run next; do **not** open a port unless
   `--port` is given.
5. **firmware profiles** — the shipped profiles with name, version, size,
   record bytes.

Exit 0 if every *required* check passes; 3 if a device was requested and not
found; 4 if a hash verification failed.

`--json` shape:

```json
{
  "ok": true,
  "environment": {"python": "3.12.14", "platform": "win32", "pyserial": "3.5", "package_data": true},
  "ports": [...],
  "vendor_drop": {"path": "...", "found": [{"role": "...", "basename": "...", "path": "...", "sha256": "...", "ok": true}], "missing": [...], "extracted": [...]},
  "device": {"simulated": true, "ok": true, "da_version": "0x0308", "flash_size": 2097152},
  "profiles": [{"name": "v1.12", "version": "V1.12", "size": 406312, "log_record_bytes": 16}]
}
```

## `m241 info [PORT] [--json] [--nmea-baud N] [--aiding]`

Opens the port in NMEA mode at 38400 (override with `--nmea-baud`), sends
`$PMTK605*31`, prints the parsed `$PMTK705` reply: release name, build number,
product model, SDK version, and a decoded summary. With `--aiding`, additionally
sends `$PMTK715`/`716`/`717` with the current UTC time / zero position and
reports the ACKs.

`--json`: `{"port", "release", "build", "model", "sdk", "raw", "chip_guess"}`.
Exit 3 if the port cannot be opened or no reply arrives.

## `m241 dump [PORT] -o FILE [--base-addr 0x20000000] [--length N] [--baud N] [--nmea-baud N] [--no-swap] [--assume-brom] [--pmtk180] [--verify/--no-verify] [--force] [--json]`

BootROM dump. `PORT` may be `auto`. Default: read the size from the header, dump,
then **prompt the user to power-cycle** and dump again, comparing SHA256
(`--no-verify` skips the second pass; `--verify` is the default for interactive
use and forced when `--yes` is given? No: `--no-verify` honours the flag, and
`--force` allows overwriting an existing output file).

Writes `FILE` with `parents=True`. Prints the header summary and the SHA256.
Detects the firmware profile and prints it (or a warning that it is unknown).

Exit: 0 ok; 3 hardware; 4 dump mismatch / length implausible; 5 refusing to
overwrite without `--force`.

`--json`: `{"port", "output", "size", "sha256", "profile", "verified", "header"}`
(no firmware bytes).

## `m241 patch SRC -o DST [--profile auto|v1.12|v1.13] [--target-version V1.14] [--no-version-marker] [--dry-run] [--json]`

Applies the patch plan. Prints the detected profile, the plan (offset, old → new,
description), the changed byte offsets, and before/after SHA256. `--dry-run`
prints the plan and **writes nothing**. Refuses (`exit 5`) to write onto `SRC`
without `--in-place`; refuses an already-patched image (`exit 4`); refuses an
unknown profile unless `--profile` was given explicitly.

`--json`: `{"input", "output", "profile", "target_version", "dry_run",
"patches": [{"offset", "old", "new", "description", "changed_offsets"}],
"changed_offsets": [...], "sha256_before", "sha256_after", "written": bool}`.

## `m241 flash [PORT] --da DA --fw IMG --backup BACKUP [--start-addr 0] [--baud N] [--nmea-baud N] [--high-baud N] [--assume-brom] [--pmtk180] [--yes] [--json]`

Destructive. Pre-flight, in order, each with its own refusal message:

1. `--backup` must exist and be a plausible firmware image; if its size differs
   from `--fw`, warn (allow with `--yes`).
2. `--fw` must parse as a known profile **or** differ from `--backup` only inside
   the known patch offsets. Otherwise exit 5 ("refusing to flash an image that is
   not a patched copy of your backup").
3. Interactive confirmation: type `WRITE` (uppercase). `--yes` skips it.
4. Enter BootROM, report DA version/flash size/flash id, check `len(fw) <=
   flash_size`, optional `--high-baud`, write with a progress bar, `restart()`.

Exit: 0 ok; 3 hardware; 4 verification/size failure; 5 safety refusal; 6 write
failed (device state undefined — print the recovery command).

`--json`: `{"port", "firmware", "backup", "da", "start_addr", "size",
"sha256", "da_info", "written": true}` (on failure: `{"ok": false, "error":
"..."}` and the corresponding exit code).

## `m241 logs info FILE [--json]`

Reads a dumped track log and prints: block count, block headers (offset, count,
bitmask, policy, period/distance/speed), computed record length, record count,
per-record CRC failures, first/last UTC, and the capacity/usage percentage for
2 MiB and 4 MiB flash.

`--json`: `{"file", "size", "blocks": [...], "record_length", "records",
"crc_errors", "first_utc", "last_utc", "capacity": {"2M": [...], "4M": [...]}}`.

## `m241 logs read FILE [--format csv|gpx|nmea|kml|json] [-o FILE] [--utc-offset-days auto|N] [--flash 2M|4M] [--firmware V1.12|V1.13] [--strict]`

Converts a dump. Default `--format csv`, default output stdout. `--utc-offset-days
auto` applies the WNRO shift only if the newest record looks stale. `--flash`
only affects the capacity/usage report, never the parse.

## `m241 logs capacity [--flash 2M|4M|all] [--firmware V1.12|V1.13] [--interval SECONDS] [--json]`

Prints the honest table:

```
flash  blocks  usable bytes   16-byte records      20-byte records
2M     32      2,080,768      130,048 (36.1 h)     104,038 (28.9 h)
4M     64      4,161,536      260,096 (72.2 h)     208,076 (57.8 h)
```

`--json`: `{"rows": [{"flash", "blocks", "usable_bytes", "record_bytes",
"points", "hours_at_interval"}]}`.

## `m241 nmea repair [--in FILE|-] [--out FILE|-] [--offset-days auto|7168] [--wall-clock YYYY-MM-DD] [--stats] [--json]`

Rewrites dates in a captured NMEA stream. Non-NMEA lines pass through verbatim.
`--stats` prints how many sentences were read/changed. `--json` (with `--stats`)
reports the counters instead of the stream.

## `m241 nmea proxy [PORT] [--offset-days auto|7168] [--tcp HOST:PORT|--stdout] [--baud N] [--nmea-baud N] [--duration SECONDS]`

Live NMEA relay with date correction. Default: TCP `127.0.0.1:10110`. Binds
loopback unless an explicit non-loopback host is given, in which case a warning
is printed (no authentication exists). `--duration` stops after N seconds (used
by tests and by `--selftest`).

## `m241 selftest [--json]`

End-to-end, hardware-free: build a synthetic firmware image, dump it through the
simulator's BootROM, patch it, write it back through the simulated DA, read it
again, assert every hash; build a synthetic log dump with the 20-byte layout and
parse + export it; repair the blog's sample `$GPRMC` sentence and verify the
checksum. Prints one line per stage. Exit 0 only if every stage passes.

`--json`: `{"ok": true, "stages": [{"name", "ok", "detail"}]}`.

## `m241 version` / `--version`

Prints `m241 <version>` and the Python version. Exit 0.

## Error formatting

```
m241: error: <message>
hint: <suggested next command or fix>
```

`m241: error:` lines go to stderr. Unknown-profile errors must print the detected
header fields (family, device type, build number, size, sha256) so a user can
file a useful issue without pasting firmware bytes.

## Script shims

`scripts/dump_firmware.py`, `scripts/patch_firmware.py`,
`scripts/flash_firmware.py`, `scripts/m241.py` — each ≤ 25 lines:

```python
#!/usr/bin/env python3
"""Equivalent: m241 dump ..."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from m241.cli import main  # noqa: E402
raise SystemExit(main(["dump", *sys.argv[1:]]))
```

They must keep the original three-step blog workflow greppable:
`scripts/dump_firmware.py PORT -o fw.bin` → `scripts/patch_firmware.py fw.bin -o
fw_patched.bin` → `scripts/flash_firmware.py PORT --da DA --fw fw_patched.bin
--backup fw.bin --yes`.
