---
name: Bug report
about: Something in the toolkit behaves incorrectly
title: "[BUG] <short description>"
labels: ["bug"]
---

## What happened

<!-- One or two sentences. -->

## What you expected

## Steps to reproduce

```bash
# exact commands, from a clean venv
```

## Output

```
# paste stderr/stdout. Remove coordinates from any NMEA/log output.
```

## Environment

| | |
|---|---|
| OS + version | |
| Python version | |
| Install method | `pip install -e .` / `pip install git+…` / other |
| `m241 --version` | |
| Hardware involved | real M-241 / simulator only |

## Does it happen with the simulator?

```bash
m241 selftest
```

- [ ] `m241 selftest` passes
- [ ] `m241 selftest` fails (paste the stage output above)

## Anything else

<!-- Guesses, timing, whether it is reproducible. -->
