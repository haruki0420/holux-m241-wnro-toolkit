# Contributing

Thanks for wanting to help. This project exists to keep a discontinued GPS
logger usable, and the most valuable contributions are **hardware reports** and
**new firmware profiles**.

## Quick start

```bash
git clone https://github.com/haruki0420/holux-m241-wnro-toolkit.git
cd holux-m241-wnro-toolkit
python -m venv .venv
# Windows:      .venv\Scripts\activate
# Linux/macOS:  source .venv/bin/activate
pip install -e ".[dev]"

pytest -q
ruff check src tests tools
```

The whole suite runs with **no hardware**: `m241.protocol.simulator` is a
pure-Python MT3318 stand-in. If a test wants a real serial port, that is a bug.

## What we want

### 1. Firmware profiles

A profile is a set of measured facts about one firmware image:

```python
FirmwareProfile(
    name="v1.11",
    display_name="Holux M-241 firmware V1.11",
    sha256=("...",),
    sizes=(406000,),
    version="V1.11",
    version_offset=0x59B00,
    epoch_offset=0x402F4,
    epoch_constant=315964800,
    log_record_bytes=16,
)
```

plus an entry in `src/m241/data/firmware_profiles.json`. To add one:

```bash
# 1. dump (it reads twice and compares the hashes for you)
m241 dump auto -o backup/mine.bin

# 2. show what the analyzer sees (no proprietary bytes in the output)
python tools/analysis/analyze_firmware.py backup/mine.bin

# 3. show what the patch would change (writes nothing)
python tools/analysis/analyze_firmware.py --plan backup/mine.bin
```

Open an issue with that output — it contains hashes and offsets only, never
firmware bytes. If you are comfortable with a patch, add the profile, add a
sha256-asserting test, and send a PR.

**Never commit a firmware image, a dump, `brom.dll`, `DA_v3.8.4.bin`, or any
vendor executable.** `.gitignore` blocks the common ones; do not fight it.

### 2. Hardware reports

We know the code works against the simulator and against this project's own
retail unit (Windows 11, CP210x, V1.13 — dump, patch, flash and the NMEA date
were all confirmed on 2026-11-15). Confirmations on other combos are gold:

| Report | Status |
|---|---|
| Windows 11, CP210x, V1.13 | **verified by this project** (Windows 10 untested) |
| Linux (any distro), V1.13 | wanted |
| macOS 14/15, Apple Silicon | wanted |
| 4 MiB unit | wanted |
| GR-245 variant | wanted |
| V1.11 or earlier | wanted |
| Bluetooth SPP | wanted (never exercised) |

Paste `m241 doctor --json`, `m241 ports --json`, and (if you dumped)
`m241 patch --dry-run --json` output. Those are safe: no binary content.

### 3. Log samples

A **synthetic** dump is better than a real one for tests. If you want to
contribute a real one, strip the coordinates first — see
`tests/fixtures/README.md` for the recipe. Real logs contain your movements.

### 4. Documentation

The Japanese pages live in `docs/user/` (see
[`docs/user/01-symptoms.ja.md`](docs/user/01-symptoms.ja.md)). Japanese
translations of `docs/reference/01-protocol.md`,
`docs/reference/02-log-format.md` and `docs/user/06-troubleshooting.md` would be
genuinely useful.

## Code style

- **Python 3.10+**, `from __future__ import annotations` everywhere.
- Type hints on public functions; docstrings on modules and public classes.
- `ruff check` must pass (`E,F,W,I,UP,B,SIM`, line length 100).
- Tests live in `tests/test_<module>.py`, use `tmp_path`, never the network,
  never a real device.
- No top-level `import serial` outside `m241/protocol/`. Everything else must
  work on a machine with no pyserial.
- Public API changes: update `docs/dev/01-architecture.md` in the same commit.
- New CLI behaviour: update `docs/reference/04-cli.md` and `tests/test_cli.py`.

## Commit / PR conventions

- One logical change per commit; imperative subject line.
- Reference the issue (`Fixes #12`).
- Add a `CHANGELOG.md` entry under "Unreleased".
- CI must be green on all three operating systems. If a failure is
  platform-specific and unrelated to your change, say so in the PR.

## Safety rules for reviewers

Any PR that touches `m241/protocol/da.py`, `m241/firmware/patcher.py`, or the
`flash` path gets extra scrutiny: those are the code paths that can brick a
device. Requirements:

- the diff must not change the DA load address (`0x00100800`) or the WNRO
  offset (`0x402F4`) without a hardware-verified report,
- the patch plan must remain a single 4-byte store for the epoch fix,
- `tests/test_firmware_patcher.py`'s changed-offset assertions must not be
  relaxed.

## License

Contributions are accepted under the MIT licence (see `LICENSE`). By opening a
PR you confirm you wrote the code or that it is compatibly licensed, and that
you are not contributing proprietary vendor code.
