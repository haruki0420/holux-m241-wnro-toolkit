# Documentation

Everything we learned while building this toolkit, written down so the next
person does not have to rediscover it.

Read in this order if you are new:

| # | Document | What it answers |
|---|---|---|
| — | [`../README.md`](../README.md) | 日本語のメイン README（何ができるか・使い方） |
| — | [`../README.en.md`](../README.en.md) | English README (what it does, how to use it) |
| 00 | [`00-background.md`](00-background.md) | **why** the M-241 reports 2006, and what the 4-byte patch does |
| 01 | [`01-protocol.md`](01-protocol.md) | the MTK BootROM + Download Agent protocol we implement |
| 02 | [`02-log-format.md`](02-log-format.md) | the binary track-log format, and why V1.13 stores fewer points |
| 03 | [`03-platforms.md`](03-platforms.md) | Windows / Linux / macOS setup, drivers, permissions |
| 04 | [`04-firmware-profiles.md`](04-firmware-profiles.md) | every verified offset, hash and size in this repo |
| 05 | [`05-vendor-drop.md`](05-vendor-drop.md) | what is in the official Holux packages and what we use |
| 06 | [`06-troubleshooting.md`](06-troubleshooting.md) | symptom → cause → fix |
| 07 | [`07-cli-spec.md`](07-cli-spec.md) | the authoritative CLI contract (exit codes, JSON, prompts) |
| 08 | [`08-quick-card.md`](08-quick-card.md) | one-page cheat sheet to print |
| 09 | [`09-verification.md`](09-verification.md) | how each claim in this repo was checked (and what was not) |
| 10 | [`10-hardware-session-notes.md`](10-hardware-session-notes.md) | the chronological log of the hardware bring-up |
| 11 | [`11-feature-matrix.md`](11-feature-matrix.md) | which advertised features still work in 2026 (Bluetooth, ezTour, capacity) |
| 12 | [`12-photo-geotagging.md`](12-photo-geotagging.md) | download a track and geotag photos by capture time |

Reference material for contributors:

| Document | Purpose |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | frozen module/interface contract between the packages |
| [`protocol-notes.md`](protocol-notes.md) | places where the implementation interprets the protocol |
| [`ROADMAP.md`](ROADMAP.md) | what is done, what needs hardware confirmation, what is out of scope |
| [`ja/00-background.ja.md`](ja/00-background.ja.md) | 日本語の背景解説 |

Other top-level files:

- [`../DISCLAIMER.md`](../DISCLAIMER.md) — **read before flashing anything**
- [`../CONTRIBUTING.md`](../CONTRIBUTING.md) — how to add a firmware profile or report hardware
- [`../SECURITY.md`](../SECURITY.md) — how to report a vulnerability
- [`../CHANGELOG.md`](../CHANGELOG.md) — what changed in each release
- [`../tests/fixtures/README.md`](../tests/fixtures/README.md) — why there are no binary fixtures

## The one-paragraph version

GPS broadcasts the week number in 10 bits, so it wraps every 1024 weeks
(19.6 years). The last wrap was 2019-04-06. The M-241's 2006 firmware hard-codes
the GPS-epoch Unix offset `315964800` and never accounts for a second wrap, so
every timestamp it produces is 7168 days in the past — NMEA date, track log, and
the OLED clock alike. Holux left the GPS business in 2019, so the only fix is our
own: a single 4-byte change to the epoch constant at file offset `0x402F4`
(`315964800` → `935280000`), written back through the MTK Download Agent from
any OS that runs Python.
