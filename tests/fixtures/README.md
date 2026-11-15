# Test fixtures

The test suite needs realistic inputs, but it must not contain proprietary
firmware bytes or your personal GPS track. So every fixture is **synthesised in
code**.

## What the tests build

| Fixture | Where | How |
|---|---|---|
| Firmware image | `tests/test_firmware_*.py` | `bytearray` of the documented size, header strings / version marker / epoch constant written at the documented offsets |
| Log dump (16-byte records) | `tests/test_logfile_*.py` | block header + records built by `m241.logfile.record.build_record` |
| Log dump (20-byte records) | `tests/test_logfile_*.py` | same builder with the SPEED field enabled |
| NMEA stream | `tests/test_nmea_*.py` | literal sentences, including the one from the source write-up |
| Serial device | `tests/conftest.py` + `m241.protocol.simulator` | pure-Python MT3318 stand-in |

There are no binary fixture files in this repository at all. That is deliberate:
a checked-in firmware image would be a copyright problem, and a checked-in log
would leak someone's movements.

## Optional real-firmware tests

`tests/test_firmware_patcher.py` contains tests marked with `skipif` that read a
real image **only if you point at one** via environment variables:

```bash
# PowerShell
$env:M241_FW_V112 = "C:\path\to\*_V1.12_*.bin"
$env:M241_FW_V113 = "C:\path\to\*_V1.13_*.bin"
pytest tests -q

# bash
export M241_FW_V112=/path/to/..._V1.12_....bin
export M241_FW_V113=/path/to/..._V1.13_....bin
pytest tests -q
```

Without those variables the tests skip, so CI and fresh clones stay clean. With
them, the exact output hashes in `docs/reference/03-firmware-offsets.md` are verified
against the real files.

## If you want to contribute a real log

Don't attach the raw dump. Instead:

1. Parse it and emit JSON:
   ```bash
   m241 logs read data.bin --format json -o log.json
   ```
2. Scrub it: replace `lat`/`lon`/`height_m` with rounded or synthetic values,
   keep the *structure* (bitmask, record length, block boundaries, CRCs,
   timestamps relative to the first record).
3. Re-encode with `m241.logfile.record.build_record` in a test, from the scrubbed
   values.
4. Include only the encoded bytes in the test, never the original.

If the interesting thing is a *malformed* record (a truncation pattern, an odd
block header), describe it precisely in an issue instead — that is usually
enough to write a synthetic reproduction.

## Half-open blocks, truncation, corruption

These are easy to synthesise and the tests already cover them:

- set the block's `count` to `0xFFFF` for "block not full",
- cut the final record mid-way for truncation,
- flip one byte in a record's CRC for `crc_ok=False`,
- fill the tail of a block with `0xFF` for padding,
- start a second block to test rollover.

See `tests/test_logfile_reader.py` for the helpers.
