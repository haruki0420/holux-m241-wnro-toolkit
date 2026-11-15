# Troubleshooting

`m241 doctor` is the first thing to run. It checks the Python environment, the
`pyserial` version, the serial port list, the vendor drop (only with
`--from-drop`) and the shipped firmware profiles. It only opens the device when
you pass `--port`, and prints the exact next command to try.

```bash
m241 doctor
m241 doctor --json
m241 doctor --simulate        # no hardware: proves the install is healthy
m241 doctor --from-drop <folder> --extract-to vendor/
```

Exit codes: `0` ok, `1` runtime error, `2` usage, `3` hardware not found,
`4` verification failed, `5` safety refusal, `6` flash write failed.

---

## Environment / install

### `ModuleNotFoundError: No module named 'serial'`

`pyserial` is not installed in the interpreter you are running.

```bash
python -m pip install pyserial
# or reinstall the toolkit (it pulls pyserial in):
python -m pip install "git+https://github.com/haruki0420/holux-m241-wnro-toolkit.git"
m241 doctor
```

Note the package name is `pyserial`, but the import name is `serial`.

### `m241: command not found` after installing

The Python `Scripts/` (Windows) or `bin/` (POSIX) directory is not on `PATH`.
Either add it, or run the module directly:

```bash
python -m m241 --help
```

---

## Serial port discovery

### `m241 ports` shows nothing

1. Is the cable plugged in? Try another USB port (direct, not through a hub).
2. Windows: install the CP210x driver; check Device Manager → Ports (COM & LPT).
3. Linux: `dmesg | tail` after plugging in — you should see `cp210x` and
   `ttyUSB0`.
4. macOS: `ls /dev/*usb*` — you should see `/dev/tty.usbserial-*` and
   `/dev/cu.usbserial-*`.
5. Is the M-241 powered on? The bridge chip powers up with USB, but the GPS
   answers only when the device is on.

### `m241 ports` sees the port but `auto` refuses to pick it

`auto` picks a port only if exactly one *plausible* bridge is present
(CP210x/PL2303/FTDI/CH340). If you have several, name it explicitly:

```bash
m241 dump COM5 -o backup/stock.bin
m241 dump /dev/ttyUSB0 -o backup/stock.bin
```

### `PermissionError` / `Access is denied` / `Resource busy`

Something else has the port open. On each platform:

**Windows**
```powershell
Get-Process | Where-Object { $_.ProcessName -match 'Holux|screen|putty|arduino' }
# close vendor utilities, serial monitors, gpsd, IDE serial plotter
```

**Linux**
```bash
sudo lsof /dev/ttyUSB0
sudo fuser -v /dev/ttyUSB0
```

**macOS**
```bash
lsof /dev/tty.usbserial-0001
```

On Linux also check group membership: `groups | grep dialout`.

---

## BootROM sync failures

### `BootROM sync failed: no 0x5F reply within ...`

The chip never answered the `0xA0` sync probe (the message ends with
`check the port, the cable and that the logger is in BootROM mode`). In order of
likelihood:

1. **The device is not in a state where the BootROM listens.** Power-cycle it
   (pull the battery for 2 s), turn it on, retry.
2. **Wrong port** — you picked a different USB-serial device.
3. **The port is held open** by another program.
4. **RTS/DTR reset is not wired** on your cable. Try the legacy entry:
   ```bash
   m241 dump auto --pmtk180 -o backup/stock.bin
   ```
   This sends `$PMTK180*3B` at the NMEA baud first.
5. **NMEA baud mismatch.** The device is normally 38400, but a firmware setting
   can change it. Tell the tool:
   ```bash
   m241 dump auto --nmea-baud 38400 -o backup/stock.bin
   ```
6. **Marginal USB hub.** Plug directly into the machine.

### `BootROM sync failed: expected 0x.., got 0x..`

The `0xA0` sync worked but a later handshake byte came back wrong (the message
names the expected and the received byte). Usually the device reset itself
mid-sequence (brown-out, bad cable) or the port is being probed by the OS
(ModemManager on Linux, a vendor app on Windows). `BootROM echo mismatch in ...`
is the same class of problem: the chip echoed something other than what was
sent.

### Already in BootROM and don't want to reset

```bash
m241 dump auto --assume-brom -o backup/stock.bin
```

This skips the reset pulse and only performs the 4-byte sync. Use it when a
previous run died mid-way and left the chip listening.

---

## Dump problems

### `implausible firmware size at header offset 0xF4: ...`

The read at `base+0xF4` did not decode to a plausible size — the chip answered
but the flash was not mapped where we looked. Almost always a **wrong
`--base-addr`**. The correct default is `0x00200000`:

```bash
m241 dump auto -o backup/stock.bin                        # default: 0x00200000
m241 dump auto --base-addr 0x0 -o backup/stock.bin        # linear flash view
```

`0x20000000` is **not** the firmware on this part: it reads back as an unmapped
bus (`42 20 02 02`). Do not use it. The message also prints the raw bytes and
both endian interpretations; pass `--length` only if you know the size.

or you were not really in BootROM (see above).

### `short read at 0x...: got N of M bytes` / `timeout after ... waiting for data`

A transient read failure — the chip was still busy or the cable glitched.
There is no automatic retry: re-run the dump. If it keeps failing, try
`--no-swap` (MT3318 variants differ in 32-bit byte order), a shorter cable, or
another USB port:

```bash
m241 dump auto --no-swap -o backup/stock.bin
```

There is no `read32 checksum mismatch` error: reads over the BootROM carry no
checksum trailer (the separate `0xA4` checksum command is the only one that
does).

### Two dumps differ

The tool refuses to proceed, because a flaky read is a flaky write. Causes:
unstable cable, USB power saving, or a device that reset during the dump.
Re-run; if it is reproducible, the cable is the prime suspect.

### `no firmware profile matches this image (...)`

Your dump is not a stock V1.12/V1.13 image. Either:

- the dump is corrupt (compare the two passes),
- the image is a variant this project has not catalogued (please open an issue
  with the detected image facts — `m241 patch --dry-run` prints the family,
  device type, version, build number, size and SHA-256),
- or you already patched it (`m241 patch` will say "already patched").

Never force the patch onto an unknown image. Use `--profile` only if you have
verified the offsets by hand.

---

## Patch problems

### `image is already patched: the effective epoch constant ...`

The epoch constant at `0x402F4` already equals `935280000`. You are patching a
patched image. Use the original dump.

### `--dry-run` says the offsets do not match

Your image is a different version than the profile table knows. Do **not** force
it; the two firmware images in the drop are the only verified ones.

### Can I patch the `.bin` straight out of the Holux zip?

Yes — those are stock images and match the profiles exactly (hashes are in
[`docs/reference/03-firmware-offsets.md`](../reference/03-firmware-offsets.md)). The recommended route is still dump → patch, so
you keep a known-good backup of your *own* unit.

---

## Flash problems

### `no 0xC0 sync byte from the download agent ...`

The DA did not boot. Causes:

- The DA was written with `byte_swap=False` (a bug in older tooling; this
  implementation always uses `byte_swap=True` for the DA).
- Wrong load address. It must be `0x00100800`; `0x00100000` is the BootROM stack
  and writing there kills the chip.
- Wrong DA file (not `DA_v3.8.4.bin`).

### `unsupported download agent version 0x....; expected 0x0308`

You are using a global `MTK_AllInOne_DA_*.bin` instead of Holux's DA v3.8. Use
the file from the official logger utility.

### Write stops part-way / `device reports readiness to read but returned no data`

The write is now incomplete. **The device state is undefined.** Re-flash your
original dump with the same command:

```bash
m241 flash auto --da vendor/DA_v3.8.4.bin --fw backup/stock.bin --backup backup/stock.bin
```

Do not power-cycle into the half-written firmware expecting it to boot.

### `--high-baud` makes it worse

Drop it. High baud is an optimisation, not a requirement; marginal cables fail
at 460800/921600.

### The device seems dead after a failed flash

Remove and re-insert the battery, then plug in USB **without turning it on** and
try the dump/flash again with `--assume-brom`. The MT3318 BootROM lives in mask
ROM and survives flash corruption; a power-cycle usually brings it back. If the
port never appears at all, the unit may need physical service — see
`DISCLAIMER.md`.

---

## Log download / parsing

### Record length looks wrong / coordinates are garbage

You forced the wrong firmware profile. Let the parser decide:

```bash
m241 logs info data.bin            # shows the bitmask and the computed record size
```

`--firmware V1.12` / `--firmware V1.13` only change the *capacity* estimate; the
record length always comes from the block header's bitmask.

### Fewer points than the manual claims

Expected. See `docs/reference/02-log-format.md`: 130,048 points only happens with 16-byte
records; a V1.13 unit configured for 20-byte records holds 104,038 on 2 MiB.

### Dates are 19.6 years in the past

That is the bug this project exists for. The offset is always **19.6 years**
(7168 days), so the year you see depends on today's date: an unpatched unit in
late 2026 shows 2007, while earlier in 2026 it showed 2006. Either patch the
firmware, or export with a correction:

```bash
m241 logs read data.bin --format gpx --utc-offset-days 7168 -o fixed.gpx
```

### `strict mode` raises on my dump

A real dump can legitimately contain stale/truncated records. Use the default
(non-strict) mode; inspect `crc_ok` per record in the JSON export.

---

## NMEA repair / proxy

### `m241 nmea repair` changes nothing

Auto mode only shifts dates inside the stale window (1999-08-22…2019-04-06) when
the wall clock says now is much later. If your capture is already correct, that
is the desired no-op. Force it with `--offset-days 7168`.

### Proxy: the mapping app sees no data

- Only one program can read the serial port. The proxy owns the port; point the
  app at the **TCP** endpoint (`127.0.0.1:10110`), not at the COM port.
- Check the port name and the device's baud (default 38400).
- Windows Firewall may prompt on first bind — loopback binding is enough.

### Proxy: dates still wrong

Auto detection refuses to shift fresh data. Use `--offset-days 7168`
explicitly.
