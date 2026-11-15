# tools/

Development and verification scripts. **Nothing here is installed by
`pip install`, and nothing here is needed to use the `m241` command.**

If you only want to fix your logger, stop reading — the CLI is all you need:

```bash
pip install "git+https://github.com/haruki0420/holux-m241-wnro-toolkit.git"
m241 --help
```

## What each script is for

| Script | Hardware needed | Purpose |
|---|---|---|
| `check_all.py` | no | Run every gate (lint, docs, tests) and print one line each |
| `check_docs.py` | no | Fail if a Markdown link or a backticked repository path is broken |
| `check_wheel.py` | no | Install the built wheel in isolation and smoke-test the CLI |
| `verify_against_drop.py` | no | Hash-check an official Holux package against the shipped manifest |
| `compare_dump.py` | no | Explain the byte differences between a real dump and the stock image |
| `analysis/analyze_firmware.py` | no | Offline firmware analysis; produced the offsets in `docs/reference/` |
| `hardware/test_da_protocol.py` | **yes** | Check the flash-write commands without writing flash |
| `hardware/recover_bootrom.py` | **yes** | Try to revive a logger that stopped answering |

## Rules for this directory

* Scripts here are **not** part of the package and are not imported by `src/`.
* They must not be required by the CLI — if the CLI needs the behaviour, it
  belongs in `src/m241/`.
* Anything that touches hardware must be read-only by default and must say so
  in its docstring.
