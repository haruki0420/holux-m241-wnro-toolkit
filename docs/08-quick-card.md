# The 2026 troubleshooting quick card

Print this. Tape it to the cable.

```
BEFORE ANYTHING
  m241 doctor                 # environment, ports, profiles, simulator
  m241 selftest               # proves the install works without hardware

PORT NOT FOUND
  Windows  → Device Manager > Ports (COM & LPT); install the CP210x driver
  Linux    → dmesg | tail ; ls -l /dev/ttyUSB* ; add yourself to dialout
  macOS    → ls /dev/*usbserial* ; no driver needed on modern macOS
  Still nothing → different USB port (not a hub), different cable, device ON

PORT FOUND BUT SYNC FAILS  (timeout @ BOOT_ROM_START_CMD1)
  1. unplug/replug, pull the battery 2 s, power on, retry
  2. close everything else that might hold the port
  3. m241 dump auto --pmtk180 ...          # legacy BootROM entry
  4. m241 dump auto --assume-brom ...      # already in BootROM
  5. m241 dump auto --nmea-baud 38400 ...  # baud mismatch

DUMP SAYS "size from header is invalid"
  wrong base address:    --base-addr 0x20000000  (BootROM view, default)
                         --base-addr 0x0         (linear flash view)
  or --no-swap if the image looks byte-shuffled on an MT3318 variant

TWO DUMPS DIFFER
  Flaky read = flaky write. Do NOT flash. Retry; suspect the cable.

PATCH SAYS "was not applied" / "unexpected epoch constant"
  Wrong profile for this image. m241 patch --dry-run --json, read the header
  fields, and open a profile issue. Never force it.

PATCH SAYS "already patched"
  Good news. You are patching a patched image. Use the original dump.

FLASH SAYS "DA SYNC byte 0xc0 not found"
  the DA was written with the wrong byte order, or at the wrong address
  → use DA_v3.8.4.bin from the official utility; address is 0x00100800

FLASH DIED MID-WRITE
  State is undefined. Do NOT boot it. Re-flash your ORIGINAL dump:
  m241 flash auto --da vendor/DA_v3.8.4.bin \
      --fw backup/stock.bin --backup backup/stock.bin --yes
  No port at all after a power cycle → hard brick (no JTAG). See DISCLAIMER.md

DEVICE SEEMS DEAD
  battery out/in, plug USB, do NOT power on, then:
  m241 dump auto --assume-brom -o probe.bin
  BootROM lives in mask ROM; flash corruption alone usually does not kill it.

OLED SHOWS V1.14 BUT THE DATE IS STILL WRONG
  you patched the image but did not flash it, or flashed the wrong image.
  m241 dump and compare the SHA256 against your patched file.

DATE RIGHT ON OLED BUT WRONG IN YOUR APP
  your app cached the old date, or you are reading the wrong port.
  m241 nmea proxy auto --offset-days 7168 --tcp 127.0.0.1:10110

DATES IN AN OLD LOG ARE 19.6 YEARS BEHIND
  expected: the log was written before the fix.
  m241 logs read data.bin --format gpx --utc-offset-days 7168 -o fixed.gpx

FEWER POINTS THAN THE MANUAL'S 130,000
  expected on V1.13: 20-byte records → 104,038 on 2 MiB.
  m241 logs capacity

PARSER OUTPUTS GARBAGE COORDINATES
  wrong record layout guess. m241 logs info data.bin reads the real bitmask
  and prints the computed record length. Do not force --firmware for parsing.

EVERYTHING IS SLOW
  flashing: try --high-baud 460800 (drop it again if it fails)
  dumping: ~60 s for 400 KiB at 115200 is normal

TESTS FAIL ON ONE OS ONLY
  a path bug, not a protocol bug. Report the test name; do not skip it.
```
