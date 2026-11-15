---
name: Hardware report
about: Report results on a real M-241 (works, partially works, or fails)
title: "[HW] <OS> / <firmware version> / works|fails at <step>"
labels: ["hardware-report"]
---

<!--
Thank you! Hardware reports are the most valuable contribution to this project.
Paste command output as text where possible, and REMOVE YOUR COORDINATES from
any log output you include.
-->

## Environment

| | |
|---|---|
| OS + version | e.g. Windows 11 24H2 / Ubuntu 24.04 / macOS 15.1 |
| Architecture | x64 / arm64 |
| Python version | e.g. 3.12.4 |
| `m241 --version` | |
| USB bridge | CP210x / PL2303 / FTDI / CH340 / unknown |
| Cable | original Holux / third-party |

## Device

| | |
|---|---|
| Product model (from `m241 info`) | e.g. `01017-00F` |
| Firmware version shown on the OLED | e.g. V1.13 |
| Flash size (if known) | 512 KiB / 2 MiB / 4 MiB / unknown |
| Symptoms | date 19.6 years behind / other |

## What you ran

```bash
# paste the exact commands
```

## Result

- [ ] `m241 ports` sees the device
- [ ] `m241 doctor` passes
- [ ] `m241 info` reads the PMTK705 release
- [ ] `m241 dump` produced two identical dumps
- [ ] `m241 patch` detected the correct profile
- [ ] `m241 flash` completed
- [ ] OLED shows `V1.14` after power-cycle
- [ ] `$GPRMC` shows the correct date

## Diagnostic output (safe to paste)

```
$ m241 doctor --json
...

$ m241 ports --json
...

$ m241 patch --dry-run --json
...
```

<!-- Those commands contain hashes/offsets only: no firmware bytes, no
coordinates. Do NOT paste a firmware dump or a raw track log. -->

## Anything else

<!-- Timing, retries, cable quirks, whether it worked on a second attempt... -->
