---
name: Firmware profile contribution
about: Add support for another M-241 firmware version (V1.11, a variant, ...)
title: "[PROFILE] M-241 firmware Vx.yy"
labels: ["firmware-profile"]
---

<!--
A firmware *profile* is a set of measured facts: hashes, sizes, offsets.
No firmware bytes are needed, and none may be committed.
-->

## Which firmware

- Version shown on the OLED:
- Where you got it (official package name / own dump):
- SHA256 of the image: `python tools/analysis/analyze_firmware.py <your_image.bin>`
  prints it. (`m241 patch --dry-run` only prints a hash once it recognises the
  image, which is the case this form exists for.)

## Analyzer output

```bash
$ python tools/analysis/analyze_firmware.py <your_image.bin>
```

```
# paste here - it contains offsets and hashes only
```

## Patch plan output

```bash
$ python tools/analysis/analyze_firmware.py --plan <your_image.bin>
```

```
# paste here
```

## Findings

- Unix epoch constant `315964800` offsets found:
- Which offset appears to feed the NMEA/log timestamp:
- ASCII `V1.xx` marker offset and value:
- Log record size (from a dump via `m241 logs info`, if you have one):

## Hardware check (optional but very useful)

- [ ] Applied the patch on a real unit and confirmed `$GPRMC` shows today's date
- [ ] OLED shows `V1.14`
- Result notes:

## Confirmations

- [ ] I understand firmware images and vendor binaries must never be committed.
- [ ] The output above contains no proprietary firmware bytes.
