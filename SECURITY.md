# Security policy

## Scope

This is an offline, local-only toolkit that talks to a USB serial device. It has
**no server component, no network listener by default, no telemetry, and no
auto-update**. The only network capability is `m241 nmea proxy`, which binds to
`127.0.0.1` unless you explicitly pass a non-loopback `--tcp` host.

That said, there are two classes of security-relevant issues worth reporting.

### 1. Software vulnerabilities

Examples: a crafted log dump or NMEA file causing unbounded memory use, a crash
in the parser, an unsafe temporary-file pattern, a path traversal in an output
path, or CI supply-chain issues.

Report privately via GitHub Security Advisories
("Security" → "Report a vulnerability"), or email the maintainer if you cannot
use GitHub. Please **do not** open a public issue for these.

### 2. Destructive-device bugs

A bug that could cause an unnecessary flash write, write to the wrong offset, or
skip a safety confirmation is treated as a **high severity** issue, because the
consequence is a destroyed device. Report it the same way.

## What we will do

- Acknowledge within a few days.
- Confirm reproduction against the simulator where possible.
- Ship a fix with a test that locks the behaviour.
- Credit you in the changelog unless you prefer otherwise.

## Safe harbour

Good-faith research on this codebase is welcome. We will not pursue action
against researchers who stay within the repo's own test suite and simulated
device. **Do not** test destructive ideas against hardware that isn't yours.

## What is *not* a vulnerability

- The device's own firmware bugs (the WNRO bug this project fixes) — see
  `docs/user/01-symptoms.md`.
- The absence of authentication on the NMEA proxy when you bind it to a public
  interface yourself. It defaults to loopback; if you change that, you own it.
- Needing physical USB access. If an attacker has your USB port and your device,
  this toolkit is not the interesting part of the threat model.
- Proprietary vendor binaries. We do not distribute them and cannot patch them.

## Supported versions

| Version | Supported |
|---|---|
| 1.x | ✅ |

## Dependencies

The only runtime dependency is `pyserial`, imported lazily. Dev/CI dependencies
are `pytest`, `ruff`, and `build`. We keep the dependency set deliberately tiny
so the supply-chain surface stays small.
