# AGENTS.md — working notes for automated contributors

This file is for AI agents and humans who change this repository. It records
what was expensive to learn so it does not have to be rediscovered. Read it
before touching `src/m241/protocol/` or `src/m241/logfile/download.py`.

Sections 3–6 were measured on real hardware. Sections 0, 1, 2, 7, 8, 9, 10, 11,
12 and 13 are process, convention, recovery or open-question notes — not
hardware measurements.

---

## 0. How to work in this repository

These rules exist because they were broken repeatedly. They are not style
preferences.

### 0.1 Report every three minutes

Any task that will take more than a couple of minutes **must** be reported on at
least every three minutes: what is running, what has finished, what is next. Do
not disappear into a long tool call and reappear with a result.

### 0.2 Run long checks in the background

`pytest`, builds and hardware sweeps go through a background job so the turn
returns immediately and progress can be reported. Never block a turn on a
multi-minute command.

### 0.3 One check command

Use `python tools/check_all.py` instead of running `pytest`, `ruff` and
`check_docs.py` separately. It runs all three and prints a one-line summary per
gate.

### 0.4 Never manage files with a script

* Moving or renaming a file: **`git mv` only**. A script that writes files
  leaves no rename history.
* Editing content: the editor tools, one change at a time. **No bulk
  find-and-replace scripts** — a previous bulk replacement corrupted Python
  identifiers (`True` became `hrue`) and had to be reverted from git.

### 0.5 Do not claim what has not been verified

If something has not been run on hardware, say so in the docs with `⚠️ 未検証`.
Writing `✅` for an untested platform or feature is a defect, not optimism.

---

## 1. What this project is

A cross-platform (Windows / Linux / macOS) toolkit for the **Holux M-241** GPS
logger (MediaTek **MT3318**, firmware `B-core_1.20`, model `01017-00F`).

Three things it does:

1. **Repairs the GPS week-number rollover.** The 2006-era firmware hard-codes
   the GPS-epoch Unix offset `315964800` and never accounts for the second
   rollover (2019-04-06), so every timestamp is 7168 days in the past. The
   4-byte date constant at file offset `0x402F4` is replaced
   (`315964800` → `935280000`); **3 bytes actually change**. That fixes the
   NMEA date, and — through the shared conversion code — the OLED clock and the
   binary log; only the NMEA date was confirmed on hardware.
2. **Downloads and converts the track log**, including matching photos to
   track points by capture time.
3. **Repairs or relays a live NMEA stream** for hosts that cannot be patched.

Runtime dependency: **`pyserial` only**, imported lazily inside
`m241.protocol`. Everything else is pure standard library.

---

## 2. Non-negotiable safety rules

These are not style preferences. Each one exists because breaking it destroyed
something during development.

### 2.1 Never pulse DTR/RTS at a logger that is streaming NMEA

`MtkBootRom.start()` defaults to the reset-line route. It is only safe when the
device is **already silent**. Pulsing the reset lines at a running firmware
leaves the unit unresponsive until the battery is removed.

The client therefore checks for NMEA first and returns a tri-state:

* `True` — firmware is running → use `$PMTK180*3B` instead;
* `False` — definitively silent → the reset route is safe;
* `None` — cannot tell → **refuse**, do not pulse.

Do not "simplify" that tri-state away.

### 2.2 `0xD3` erases the range it covers

The DA erases whole sectors when you set a memory range. A range that starts at
`0` therefore erases the start of the firmware image — including the reset
vector — and the unit stops booting.

Rules:

* the range must be a hard-coded constant, not a value derived from user input;
* refuse to run if that constant is anything unexpected;
* verify a known-intact marker before erasing;
* require an explicit confirmation: type `WRITE` at the prompt, or pass `--yes`.

### 2.3 Take a dump before writing anything

`m241 flash` accepts an image that matches a known profile, or one whose
differing bytes all fall inside the allowed patch offsets of your own backup
(an image identical to the backup is accepted too); anything else is refused.
Do not weaken that check. A full-image flash erases the whole chip.

### 2.4 Never commit personal data

See section 9.

---

## 3. Verified hardware facts — do not re-derive

| Fact | Value | How it was established |
|---|---|---|
| Firmware base (BootROM view) | **`0x00200000`** | `0x20000000` returns `42 20 02 02` (unmapped bus); `0x00200000` returns the vector table `18 f0 9f e5 …` |
| DA load address | **`0x00100800`** | `0x100000`–`0x1007FF` is BootROM scratch/stack; writing there kills the chip |
| DA info report | `>BHBLHHHHL`, 20 bytes, starts with `0xC0` | DA v3.8.4 answers `C0 03 08 49 …` |
| Flash on a retail unit | **512 KiB**, MX29LV400CB, mfr `0xC2`, device `0x22BA`, DA id `0x49` | DA info report |
| Firmware image size | **406,620 bytes** (`0x6345C`) | read back and hashed |
| Log record size | determined by the **block header bitmask**, not the firmware version | see section 6 |
| NMEA | 38400 8N1, `$GPGGA/$GPGSA/$GPGSV/$GPRMC/$GPVTG` at 1 Hz | |
| UI languages | **English / French / German** — no Japanese | release notes list TC/SC/DE/FR/EN; the 41-entry string table is byte-identical between V1.12 and V1.13 |

The DA's `flash_size` describes the chip it can program. A unit was measured
holding 212,860 bytes of log *alongside* a 406,620-byte firmware image, so the
log area is not that chip. This is unresolved; see `docs/dev/04-roadmap.md`.

---

## 4. The protocol gotchas that cost the most time

Every one of these produced a plausible-looking failure that pointed somewhere
else.

### 4.1 `0xD3` replies with **two** bytes

`0xD3` is answered by `ACK (0x5A)` **plus a status byte** (`0x00` = success).
Reading only the ACK leaves the status byte in the buffer, which shifts every
later frame by one byte — the next command then times out, and it looks like
the `0xD5` step is broken.

```python
# correct
reply = read(2)
if reply[0] != ACK: fail
status = reply[1]
```

### 4.2 WRITE16's echo **trails by one byte**

After sending N bytes only **N-1** echo bytes are available, and the echo of the
final byte never arrives because nothing follows it. Therefore:

* writing one byte and waiting for its echo **hangs**;
* requiring an exact echo for every chunk **fails on the last chunk**.

The client accumulates the echo and compares it against everything sent so far
*minus its last byte*, then drains the tail:

```python
want = len(sent) - 1
if want > len(echo):
    echo += read_exact(want - len(echo))
if echo != sent[:-1]:
    raise BootRomError(...)
```

### 4.3 Reads carry **no checksum trailer**

`0xA2` / `0xAF` return echo + data and nothing else. The checksum is a separate
`0xA4` command. Consuming a two-byte trailer desynchronises every read — this
was a real bug, now covered by
`tests/test_protocol_brom.py::test_read_has_no_trailing_checksum_word`.

### 4.4 The two widths order bytes differently

| Opcode | Wire order | Conversion |
|---|---|---|
| `0xA2` / `0xA1` | 16-bit pairs swapped | `swap_pairs` |
| `0xAF` | each 32-bit word byte-reversed | `reverse_words32` |

Using the pair swap for `0xAF` yields plausible garbage, not an error.

### 4.5 Count fields are in units, not bytes

`0xAF` takes a **dword** count, `0xA2` / `0xA1` / `0xA4` take a **word** count.

### 4.6 `$PMTK180*3B` needs a live NMEA stream

The sentence is only honoured while the firmware is still streaming. If the
stream has already stopped the sentence is silently ignored, so the client
checks for the stream first and reports a precise error instead of a bare sync
timeout. Send it at the NMEA baud, then switch to 115200.

### 4.7 The three sync probes must be back to back

`0xA0` → `0x5F`, then `0x0A`→`0xF5`, `0x50`→`0xAF`, `0x05`→`0xFA` with **no
pause** between them. Inserting delays drops the chip out of the handshake.

---

## 5. Flash write sequence (proven)

```
0xD3  >BBLL (opcode, 1, start, end)   -> ACK + status (2 bytes)
0xD5  >BL   (opcode, 0x100)           -> ACK, ACK
each packet: 0x100 bytes + >H byte-sum -> 0x69 (CONT)
end                                    -> ACK, ACK
```

* The image is written **as-is**, 406,620 bytes, so the final packet is 92
  bytes. That is the configuration verified on hardware; do not "fix" it by
  padding unless you have a reason.
* The DA accepts a short final packet.
* `0xD9` restarts the chip. After a successful write the firmware boots, so
  verification must re-enter the BootROM first.

---

## 6. Log download (PMTK182)

```
$PMTK182,2,8            -> $PMTK182,3,8,<hex bytes used>
$PMTK182,5              -> logging disabled
$PMTK182,7,<addr>,<len> -> $PMTK182,8,<addr>,<hex data>
$PMTK182,4              -> logging enabled
$PMTK182,6              -> erase the log
```

Two things that are easy to get wrong:

* **Address and length are hexadecimal without a `0x` prefix.** `$PMTK182,7,64,64`
  reads from address `0x64` and returns `0x64` bytes, not 64 and 64.
* **Logging must be paused for the transfer** and resumed in a `finally` block,
  or a failure leaves the device not recording.

Chunks of `0x800` (2048) bytes are accepted. A 200 KB log is about 100 round
trips.

### Record size comes from the block header

Each 64 KiB block starts with a `0x200` header whose bitmask says which fields
each record carries. A V1.13 unit can therefore produce **16-byte** records
(UTC + lat + lon + height) or **20-byte** records (with speed), depending on
what the log session was configured for. Do not assume a size from the firmware
version alone.

Lat/lon are **4-byte floats**, height is a **3-byte u24**, and the per-record
checksum is **one byte with no `'*'` separator**.

---

## 7. Testing

* **470 tests**, all hardware-free. `pytest -q` must stay green.
* `m241.protocol.simulator.FakeDevice` models the chip: sync, reads, writes,
  the DA handshake and the flash write path. When you change a protocol
  detail, **change the simulator too** — that is what makes the regression
  test meaningful.
* The simulator models the `0xD3` status byte (`mem_cmd_status`). If you break
  the two-byte read, `test_mem_cmd_status_byte_is_consumed` fails.
* `tests/test_logfile_download_geotag.py` has a fake PMTK182 device; use it as
  the template for new device-facing tests.
* Hardware-shaped regressions are pinned deliberately: the real image length
  (406,620 bytes, 92-byte tail) and a full write-then-read-back.

Run before every commit:

```bash
pytest -q
ruff check src tests tools
python tools/check_docs.py
```

---

## 8. Code conventions

* Python **3.10+**, `from __future__ import annotations` everywhere.
* `ruff` with `select = ["E","F","W","I","UP","B","SIM"]`, line length 100.
  `pyproject.toml` also ignores `E501` and `SIM108` globally; `tools/**` ignores
  `E402`, `F401`, `SIM113`, and `tests/**` ignores `B011`.
* **No new runtime dependency.** `pyserial` is the only one and it is imported
  lazily, so `m241 patch`, `m241 logs …` and `m241 nmea repair` work on a
  machine without it.
* Hardware modules are imported **inside** the CLI command that needs them.
* Errors: raise the hierarchy in `m241/errors.py`. CLI exit codes are
  0 ok / 1 runtime / 2 usage / 3 hardware / 4 verify / 5 safety / 6 flash.
* Machine-readable output goes to **stdout** (`--json` prints one object);
  progress, warnings and errors go to **stderr**.
* Prefer `pathlib` over string paths; never hard-code a user directory — take
  it from an argument or an environment variable (`M241_DROP`, `M241_DA`).

---

## 9. Personal data policy

This repository is public. Before any commit that touches docs, tests or
examples:

* **No real coordinates.** Use a generic city-centre example
  (`35.6812360, 139.7671250`) or the no-fix placeholder
  (`8960.0000,N,00000.0000,E`). A real track log is personal location data.
* **No local paths.** No Windows user-directory paths, no Unix home
  directories, no macOS user directories.
* **No serial-port instance names from a specific machine** in docs — `COM3`
  in a usage example is fine, a machine-specific enumeration is not.
* **Never commit** `backup/` (firmware dumps, track logs), `vendor/` (the
  proprietary Holux packages) or `.venv/`. All three are gitignored; keep it
  that way and keep the regression test that guards `.gitignore`.
* Commit author identity should be the GitHub noreply address.

If personal data ever lands in history, rewriting is not enough on its own:
GitHub keeps unreachable objects reachable by SHA until the repository is
deleted. The reliable fix is rewrite → force-push → **delete and recreate the
repository** → push the clean history.

---

## 10. Documentation conventions

* `docs/README.md` is the index; add a row for every new page.
* Relative links only, and `python tools/check_docs.py` must report
  "all links resolve".
* `docs/reference/01-protocol.md` is the authoritative protocol reference. If you change
  a protocol detail in code, change that page in the same commit.
* `docs/dev/02-verification.md` records what was verified and what was not. Keep it
  honest — mark unverified things as unverified.
* The wiki is a separate git repository
  (`<repo>.wiki.git`) with its own history. It is **not** part of the main
  repo, so a doc change usually needs two commits.

---

## 11. Commit conventions

Conventional-commit prefixes: `feat:`, `fix:`, `docs:`, `chore:`, `test:`.

A commit message should say **what was measured** and **what it replaced**. For
protocol fixes, name the byte sequence and the symptom it caused. Example:

```
fix(protocol): 0xD3 replies with two bytes, not one

Reading only the ACK left the status byte in the buffer, shifting every later
frame by one byte, so the 0xD5 step always timed out.
```

Do not mention proprietary-binary analysis or third-party tooling internals in
commit messages or docs.

---

## 12. Recovery playbook

The BootROM is mask ROM, so a failed flash is a **soft** brick: the chip still
answers `0xA0` once the bootloader runs.

1. Power-cycle the logger (remove the battery for ~10 s).
2. `m241 info` to see whether it boots. (`m241 logs info` takes a dumped log
   *file*, not the device, so it cannot answer this.)
3. If it is silent, try `m241 dump --assume-brom`; the BootROM may already be
   listening.
4. To restore firmware:
   `m241 flash PORT --da DA --fw <own stock dump> --backup <own stock dump> --yes`
   An image identical to the backup is accepted (zero differing bytes).
5. Power-cycle again and confirm `$GPRMC` carries today's date.

If the device answers neither NMEA nor `0xA0`, it needs a physical power cycle;
there is no software path back from a chip that is not listening.

---

## 13. Known unknowns

Do not fabricate answers for these; they are recorded as open in
`docs/dev/04-roadmap.md`.

* Where the log actually lives, given the DA reports a 512 KiB part while a
  unit held 212 KB of log plus a 406 KB image.
* Whether a Japanese-language firmware SKU exists (none of the V1.12 / V1.13
  images contain Japanese).
* Bluetooth SPP was never exercised — all hardware work went over USB.
* The `0xD3` status byte's meaning beyond `0x00` = success is unknown.
