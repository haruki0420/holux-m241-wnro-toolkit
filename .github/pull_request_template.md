# Pull request

## What this changes

<!-- One or two sentences. -->

Fixes #

## Type

- [ ] Bug fix
- [ ] New firmware profile
- [ ] New feature
- [ ] Documentation
- [ ] CI / tooling
- [ ] Refactor (no behaviour change)

## Safety checklist

Only tick what applies.

- [ ] This does **not** touch `m241/protocol/da.py`, `m241/firmware/patcher.py`,
      or the `flash` path.
- [ ] OR: this touches a destructive path, and I have **hardware-verified** it,
      or it is covered by the simulated DA and I have not changed the DA load
      address (`0x00100800`) or the WNRO offset (`0x402F4`).
- [ ] No firmware image, dump, `brom.dll`, `DA_*.bin`, or vendor executable is
      included in the diff.
- [ ] No real GPS track / coordinates are included in tests or fixtures.

## Verification

```bash
pytest -q
ruff check src tests tools
m241 selftest
```

Output:

```
# paste
```

## Docs

- [ ] `docs/dev/01-architecture.md` updated (public API changed)
- [ ] `docs/reference/04-cli.md` + `tests/test_cli.py` updated (CLI changed)
- [ ] `CHANGELOG.md` updated under "Unreleased"
- [ ] `README.md`（日本語） / `README.en.md`（English） updated if the user-facing workflow changed

## Notes for reviewers

<!-- Anything subtle, any platform you could not test, any follow-up needed. -->
