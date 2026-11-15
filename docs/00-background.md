# Background: GPS week-number rollover and the M-241

This page explains *why* an M-241 reports 2006 in 2026, what the fix actually
changes, and which alternatives exist. Read it if you want to understand the
patch before running it.

## 1. GPS time is a week number plus a time-of-week

A GPS receiver does not broadcast "2026-05-03". It broadcasts:

- **WN** — the GPS week number, a 10-bit field (0…1023), incremented on
  Saturday night/Sunday morning;
- **TOW** — time of week, in seconds, from Saturday 00:00 UTC;
- plus clock correction terms.

Because WN is 10 bits, it wraps every 1024 weeks = 7168 days = 19.6 years.

| Rollover | Date (UTC) |
|---|---|
| 1st | 1999-08-22 |
| 2nd | 2019-04-06 |
| 3rd | 2038-11-21 |

Firmware written before 1999 handles the first rollover (or simply ignores the
problem); firmware written in 2006 generally assumes WN ≥ 1024 means the first
rollover happened, so it adds 1024 weeks and calls it a day. That assumption
breaks on 2019-04-06.

## 2. What the M-241 does wrong

The M-241's main firmware (`HOLUX_M-241`, `B-core_1.20`, build date
`Feb 9 2006`) converts GPS time to Unix time using a hard-coded constant:

```
unix_seconds = gps_seconds_since_1980 + 315964800
                                         ^^^^^^^^^
                                         1980-01-06T00:00:00Z in Unix time
```

It derives `gps_seconds_since_1980` from the 10-bit WN assuming at most one
rollover. After 2019-04-06, every timestamp it computes is exactly
`1024 * 604800 = 619,315,200 s` (7168 days) in the past.

Observed on a real unit (2026-05-03):

```
$GPRMC,022127.542,V,8960.0000,N,00000.0000,E,0.00,0.00,170906,,,N*7F
                                                   ^^^^^^
                                                   == 2006-09-17
```

- The time-of-day is correct.
- Only the **date** is 7168 days behind.
- The OLED clock, the NMEA output, and the binary track log all inherit the
  same offset, because they all read the same converted timestamp.

## 3. The 4-byte fix

There are two copies of the epoch constant in the image. Only the second one
(`0x402F4`) feeds the conversion path that produces NMEA/log timestamps:

```
0x402F4: 80 3D D5 12   = 315964800 (0x12D53D80)
     ->  80 3D BF 37   = 935280000 (0x37BF3D80)
```

`935280000 - 315964800 = 619315200`, so the constant now means
"Unix time at the GPS epoch **plus one rollover**" — exactly the correction the
firmware needs to keep working until the *third* rollover in 2038.

Patching **both** copies was tried and double-shifts the clock into 2045, so the
tool patches exactly one. `tests/test_firmware_patcher.py` asserts the byte set.

### Why not patch the `+1024` week arithmetic?

The firmware contains 27 instances of the `movs rN,#1; lsls rN,rN,#10`
pattern (i.e. `+1024`). Four of them look like they belong to the
week→seconds path; all four were patched in turn on real hardware and **none of
them changed the RMC date**. (They are flag handling, range comparisons, and a
"store week − 1024" repair path.) The epoch constant is the deterministic,
single-site fix.

## 4. The cosmetic version marker

Each firmware image contains exactly one ASCII `V1.xx` string, shown in the
device's Setting menu and on the boot screen. The tool rewrites it to `V1.14`
so you can tell a patched unit from a stock one. This is cosmetic — it is on by
default and can be disabled with `--no-version-marker`.

The original write-up flipped a single byte (`'3'`→`'4'`), which only ever
worked for V1.13. This tool rewrites the whole 5-byte run and therefore works
identically for V1.12 and V1.13.

## 5. Alternatives that don't touch firmware

| Method | Fixes logs | Fixes live NMEA | Fixes the OLED |
|---|---|---|---|
| Post-process with `+7168 days` (GPSBabel, spreadsheet, `m241 logs read --utc-offset-days 7168`) | ✅ | ❌ | ❌ |
| `m241 nmea proxy` (rewrite the stream on the host) | ✅ | ✅ | ❌ |
| This firmware patch | ✅ | ✅ | ✅ |

The patch is the only option that fixes the device itself — which matters if
you want to read the date off the screen while walking around.

## 6. Where the numbers in this project come from

Every offset, hash and size used by the tool is measured from the official
Holux update packages and recorded in
[`04-firmware-profiles.md`](04-firmware-profiles.md). The tool never guesses an
offset: if your image does not match a known profile, `m241 patch` refuses to
run unless you force it.
